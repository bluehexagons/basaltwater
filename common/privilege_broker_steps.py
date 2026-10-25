"""Install the opt-in broker and its independent HTTPS approval service."""

from __future__ import annotations

import grp
from contextlib import contextmanager
import json
import os
import pwd
import re
import secrets
import shutil
import socket
import ssl
import stat
import tempfile
import time
from urllib.parse import urlsplit

from lib.atomic_io import remove_file_durable, write_json_atomic, write_text_atomic
from lib.machine_state import can_manage_system_services, is_vm
from lib.operation_state import OperationStateStore
from lib.privilege_auth import validate_auth
from lib.privilege_policy import (
    CONFIG_DIR, DEFAULT_REQUEST_TTL_SECONDS, POLICY_PATH, WEB_USER, load_policy, protected_path, validate_policy,
)
from lib.privilege_setup import privilege_broker_origin, validate_broker_settings
from lib.remote_utils import is_dry_run, is_service_active, run
from lib.setup_directories import directory_fd
from lib.unit_transaction import inspect_unit_state, snapshot_unit_file
from lib.validators import validate_username

MARKER = "# Managed by basaltwater privilege broker"
BROKER = "basaltwater-privilege-broker"
WEB = "basaltwater-privilege-approval"
AUTH_PATH = CONFIG_DIR + "/auth.json"
POLKIT_PATH = "/etc/polkit-1/rules.d/00-basaltwater-privilege-broker.rules"
UNIT_ROOT = "/etc/systemd/system"


def _marker(path: str) -> str:
    return "// Managed by basaltwater privilege broker" if path == POLKIT_PATH else MARKER


def _managed_write(path: str, content: str) -> None:
    if os.path.lexists(path):
        protected_path(path)
        with open(path, encoding="utf-8") as source:
            if not source.read().startswith(_marker(path) + "\n"):
                raise ValueError(f"Refusing to replace unmanaged file: {path}")
    else:
        protected_path(os.path.dirname(path), directory=True)
    write_text_atomic(path, content, mode=0o644)


def _approval_account():
    if not validate_username(WEB_USER):
        raise ValueError("Invalid approval service username")
    try:
        account = pwd.getpwnam(WEB_USER)
    except KeyError:
        run(["useradd", "--system", "--user-group", "--home-dir", "/nonexistent",
             "--no-create-home", "--shell", "/usr/sbin/nologin", WEB_USER])
        account = pwd.getpwnam(WEB_USER)
    group = grp.getgrgid(account.pw_gid)
    if (not 0 < account.pw_uid < 1000 or account.pw_gid == 0 or account.pw_dir != "/nonexistent"
        or account.pw_shell not in {"/usr/sbin/nologin", "/sbin/nologin"}
        or group.gr_name != WEB_USER or group.gr_mem
        or os.getgrouplist(WEB_USER, account.pw_gid) != [account.pw_gid]
        or any(p.pw_uid == account.pw_uid and p.pw_name != WEB_USER for p in pwd.getpwall())
        or any(p.pw_gid == account.pw_gid and p.pw_name != WEB_USER for p in pwd.getpwall())):
        raise ValueError("Approval service requires a dedicated locked identity and private group")
    run(["usermod", "--lock", WEB_USER])
    return account


def render_units() -> dict[str, str]:
    common = """NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
UMask=0077
MemoryMax=192M
TasksMax=32
Restart=on-failure
RestartSec=5
"""
    return {
        BROKER: f"""{MARKER}
[Unit]
Description=basaltwater agent privilege broker
After=network.target
[Service]
User=root
RuntimeDirectory={BROKER}
RuntimeDirectoryMode=0755
StateDirectory={BROKER}
StateDirectoryMode=0700
ExecStart=/usr/bin/python3 -I /opt/basaltwater/common/service_tools/privilege_broker.py
RestrictAddressFamilies=AF_UNIX
{common}
[Install]
WantedBy=multi-user.target
""",
        WEB: f"""{MARKER}
[Unit]
Description=basaltwater independent privilege approval page
Requires={BROKER}.service
After={BROKER}.service network.target
[Service]
User={WEB_USER}
Group={WEB_USER}
LoadCredential=auth:{AUTH_PATH}
LoadCredential=cert:{CONFIG_DIR}/server.crt
LoadCredential=key:{CONFIG_DIR}/server.key
ExecStart=/usr/bin/python3 -I /opt/basaltwater/common/service_tools/privilege_approval.py
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
CapabilityBoundingSet=
{common}
[Install]
WantedBy=multi-user.target
""",
    }


def _stop() -> None:
    # A missing fragment does not mean the manager unloaded the running unit
    # (including its LoadCredential copies). Inspect both names regardless.
    _check_managed_fragments()
    states = {name: inspect_unit_state(name + ".service") for name in (WEB, BROKER)}
    for name in (WEB, BROKER):
        path = f"{UNIT_ROOT}/{name}.service"
        state = states[name]
        if state["LoadState"] != "not-found" or state["ActiveState"] != "inactive":
            run(["systemctl", "stop", name + ".service"])
        if os.path.lexists(path) or state["UnitFileState"] in {"enabled", "enabled-runtime"}:
            flags = ["--runtime"] if state["UnitFileState"] == "enabled-runtime" else []
            run(["systemctl", "disable", *flags, name + ".service"])
        actual = inspect_unit_state(name + ".service")
        if actual["ActiveState"] != "inactive" or actual["UnitFileState"] in {"enabled", "enabled-runtime"}:
            raise RuntimeError("Privilege approval service did not stop and disable: " + name)


def _check_managed_fragments() -> None:
    for path in (f"{UNIT_ROOT}/{WEB}.service", f"{UNIT_ROOT}/{BROKER}.service", POLKIT_PATH):
        if os.path.lexists(path):
            protected_path(path)
            snapshot = snapshot_unit_file(path)
            if snapshot is None or not snapshot["content"].startswith(_marker(path) + "\n"):
                raise ValueError(f"Refusing to change unmanaged file: {path}")


class BrokerRecoveryError(RuntimeError):
    """Private snapshots and the operation guard must survive uncertain recovery."""


def _configuration_directories() -> tuple[str, ...]:
    return (os.path.dirname(CONFIG_DIR), CONFIG_DIR,
            os.path.dirname(os.path.dirname(POLKIT_PATH)), os.path.dirname(POLKIT_PATH))


@contextmanager
def _broker_transaction():
    # Unit replacement alone cannot cover credential publication or the HTTPS
    # readiness gate. Share its bounded snapshots/state probes, but own one
    # operation through application readiness and rollback of all broker files.
    protected_path(UNIT_ROOT, directory=True)
    store = OperationStateStore(UNIT_ROOT + "/.basaltwater-privilege-operation.json")
    backup_dir = ""
    resolved = False
    modified = False
    try:
        record = store.begin("privilege-broker", CONFIG_DIR, "staging")
        try:
            _check_managed_fragments()
            paths = (POLICY_PATH, AUTH_PATH, CONFIG_DIR + "/server.crt", CONFIG_DIR + "/server.key",
                     POLKIT_PATH, f"{UNIT_ROOT}/{BROKER}.service", f"{UNIT_ROOT}/{WEB}.service")
            files = {}
            for path in paths:
                if os.path.lexists(path):
                    protected_path(path)
                files[path] = snapshot_unit_file(path)
            directories = {}
            for path in dict.fromkeys(_configuration_directories()):
                if os.path.lexists(path):
                    protected_path(path, directory=True)
                    with directory_fd(path) as descriptor:
                        info = os.fstat(descriptor)
                    if info.st_uid != 0 or info.st_mode & 0o022:
                        raise ValueError(f"Unsafe privilege configuration directory: {path}")
                    directories[path] = dict(mode=stat.S_IMODE(info.st_mode), uid=info.st_uid, gid=info.st_gid)
                else:
                    directories[path] = None
            states = {name: inspect_unit_state(name + ".service") for name in (BROKER, WEB)}
            backup_dir = tempfile.mkdtemp(prefix=".basaltwater-privilege-", dir=UNIT_ROOT)
            store.transition(record.operation_id, "snapshotting", context={"backup_dir": backup_dir})
            write_json_atomic(backup_dir + "/previous.json", dict(files=files, directories=directories, states=states), mode=0o600)

            def begin_changes(units=None):
                nonlocal modified
                if units:
                    candidates = []
                    for name, content in units.items():
                        path = backup_dir + "/" + name + ".service"
                        write_text_atomic(path, content, mode=0o644)
                        candidates.append(path)
                    run(["systemd-analyze", "verify", *candidates])
                store.transition(record.operation_id, "replacing")
                modified = True

            yield begin_changes
            store.complete(record.operation_id)
            resolved = True
        except BaseException:
            errors = []

            def attempt(action):
                try:
                    action()
                    return True
                except BaseException as exc:
                    errors.append(type(exc).__name__)
                    return False

            if modified:
                attempt(lambda: store.transition(record.operation_id, "rolling-back"))
                # Never publish credentials or restart the old group unless
                # every new process is observed quiescent first.
                if attempt(_stop):
                    for path, previous in files.items():
                        if previous is None:
                            attempt(lambda path=path: remove_file_durable(path))
                        else:
                            attempt(lambda path=path, previous=previous: write_text_atomic(path, **previous))
                    for path, previous in reversed(tuple(directories.items())):
                        def restore_directory(path=path, previous=previous):
                            if previous is None:
                                if os.path.lexists(path):
                                    os.rmdir(path)
                                    with directory_fd(os.path.dirname(path)) as descriptor:
                                        os.fsync(descriptor)
                            else:
                                with directory_fd(path) as descriptor:
                                    os.fchown(descriptor, previous["uid"], previous["gid"])
                                    os.fchmod(descriptor, previous["mode"])
                                    os.fsync(descriptor)
                        attempt(restore_directory)
                    attempt(lambda: run(["systemctl", "daemon-reload"]))
                    if not errors:
                        for name, previous in states.items():
                            if previous["UnitFileState"] in {"enabled", "enabled-runtime"}:
                                flags = ["--runtime"] if previous["UnitFileState"] == "enabled-runtime" else []
                                attempt(lambda name=name, flags=flags: run(["systemctl", "enable", *flags, name + ".service"]))
                            if previous["ActiveState"] == "active":
                                attempt(lambda name=name: run(["systemctl", "restart", name + ".service"]))
                        for name, previous in states.items():
                            def verify(name=name, previous=previous):
                                actual = inspect_unit_state(name + ".service")
                                expected_active = "active" if previous["ActiveState"] == "active" else "inactive"
                                if actual["ActiveState"] != expected_active or actual["UnitFileState"] != previous["UnitFileState"]:
                                    raise RuntimeError("Restored privilege service state did not match")
                            attempt(verify)
                        if states[WEB]["ActiveState"] == "active":
                            def ready():
                                previous_policy = files[POLICY_PATH]
                                if previous_policy is None:
                                    raise RuntimeError("Previous approval policy is unavailable")
                                _wait_ready(json.loads(previous_policy["content"])["origin"])
                            attempt(ready)
            if errors:
                store.transition(record.operation_id, "rollback-failed", status="recovery_required",
                                 context={"backup_dir": backup_dir, "errors": errors})
                raise BrokerRecoveryError(f"Privilege broker recovery required; inspect {store.path} and {backup_dir}") from None
            store.complete(record.operation_id, outcome="rolled_back" if modified else "failed")
            resolved = True
            raise
    finally:
        store.close()
        if backup_dir and resolved:
            shutil.rmtree(backup_dir)


def _wait_ready(origin: str) -> None:
    for _ in range(10):
        if is_service_active(BROKER) and is_service_active(WEB) and _https_ready(origin, CONFIG_DIR + "/server.crt"):
            return
        time.sleep(0.2)
    raise RuntimeError("Privilege approval services did not become active")


def _https_ready(origin: str, certificate: str) -> bool:
    """Verify the installed leaf certificate and the authentication challenge."""
    parsed = urlsplit(origin)
    context = ssl.create_default_context(cafile=certificate)
    context.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    try:
        with socket.create_connection(("127.0.0.1", parsed.port), timeout=1) as connection:
            with context.wrap_socket(connection, server_hostname=parsed.hostname) as tls:
                tls.sendall(f"GET / HTTP/1.0\r\nHost: {parsed.netloc}\r\n\r\n".encode())
                return tls.recv(1024).startswith(b"HTTP/1.0 401 ")
    except OSError:
        return False


def _requester_has_sudo_grants(username: str) -> bool:
    """Return whether sudo explicitly reports a requester grant."""
    sudo = run(["sudo", "-n", "-l", "-U", username], capture_output=True, check=False)
    output = "\n".join(part for part in (sudo.stdout, sudo.stderr) if part)
    denial = rf"User\s+{re.escape(username)}\s+is not allowed to run sudo(?:\s|$)"
    if re.search(denial, output):
        return False
    if sudo.returncode == 0:
        return True
    raise RuntimeError("Could not determine whether the brokered account has sudoers grants")


def configure_privilege_broker(config) -> None:
    validate_broker_settings(config)
    if config.privilege_broker_port is None and not config.disable_privilege_broker:
        return
    if is_dry_run():
        print("  [DRY-RUN] Would reconcile independent HTTPS privilege approvals")
        return
    if not can_manage_system_services(config.machine_type):
        raise ValueError("Privilege approvals require a system service manager")
    with _broker_transaction() as begin_changes:
        _configure_privilege_broker(config, begin_changes)
    if config.disable_privilege_broker:
        print("  ✓ Privilege approvals disabled; policy and audit history retained")
    else:
        print("  ✓ Privilege approvals: " + privilege_broker_origin(config) + "/")


def _configure_privilege_broker(config, begin_changes) -> None:
    if config.disable_privilege_broker:
        begin_changes()
        _stop()
        for path in (f"{UNIT_ROOT}/{WEB}.service", f"{UNIT_ROOT}/{BROKER}.service", POLKIT_PATH):
            if os.path.lexists(path):
                protected_path(path)
                with open(path, encoding="utf-8") as source:
                    if not source.read().startswith(_marker(path) + "\n"):
                        raise ValueError(f"Refusing to remove unmanaged file: {path}")
                remove_file_durable(path)
        if os.path.lexists(AUTH_PATH):
            protected_path(AUTH_PATH)
            remove_file_durable(AUTH_PATH)
        run(["systemctl", "daemon-reload"])
        _stop()
        return
    if not is_vm():
        raise ValueError("Privilege approvals are supported on VMs only")
    account = pwd.getpwnam(config.username)
    if account.pw_uid < 1000:
        raise ValueError("Privilege requester must be a non-system account")
    from common.agent_security_steps import _AGENT_DENIED_GROUPS, _account_groups

    if _AGENT_DENIED_GROUPS & _account_groups(account.pw_name, account.pw_gid):
        raise ValueError("Remove requester privileged groups before installing the broker")
    # Named-user sudoers entries can bypass the group-based posture.
    if _requester_has_sudo_grants(account.pw_name):
        raise ValueError("Brokered account must have no sudoers grants; remove per-user rules through the administrator channel")
    _approval_account()
    for path in (POLICY_PATH, AUTH_PATH, CONFIG_DIR + "/server.crt", CONFIG_DIR + "/server.key"):
        if os.path.lexists(path):
            protected_path(path)
    if config.privilege_broker_auth:
        auth = validate_auth(json.loads(config.privilege_broker_auth))
    elif os.path.exists(AUTH_PATH):
        with open(AUTH_PATH, encoding="utf-8") as source:
            auth = validate_auth(json.load(source))
    else:
        raise ValueError("First installation requires --privilege-broker-password")
    if auth["username"] != config.username:
        raise ValueError("Supply --privilege-broker-password to update the approval login after a user rename")
    if os.path.exists(POLICY_PATH):
        policy = load_policy()
        if policy["requester_uid"] != account.pw_uid:
            raise ValueError("Existing broker policy belongs to a different UID; review it as administrator")
        policy["origin"] = privilege_broker_origin(config)
    else:
        policy = {"version": 1, "machine": secrets.token_hex(16), "origin": privilege_broker_origin(config),
                  "requester_uid": account.pw_uid, "ttl_seconds": DEFAULT_REQUEST_TTL_SECONDS,
                  "services": {}, "reboot": "approve", "commands": "approve"}
    validate_policy(policy)
    # Every installed Python dependency is part of the privileged boundary.
    for directory in ("/opt/basaltwater/lib", "/opt/basaltwater/common", "/opt/basaltwater/plugins"):
        protected_path(directory, directory=True)
        for root, directories, files in os.walk(directory):
            protected_path(root, directory=True)
            for name in directories:
                protected_path(os.path.join(root, name), directory=True)
            for name in files:
                protected_path(os.path.join(root, name))
    from common.godot_web_steps import configure_internal_web_host, identities_for_config

    origin = urlsplit(privilege_broker_origin(config))
    identities = identities_for_config(origin.hostname, config.system_hostname)
    _url, _ca, cert, key, _changed = configure_internal_web_host(
        identities, [config.username], config.effective_access_sources(),
        configure_static_site=True, install_utility=True,
    )
    units = render_units()
    begin_changes(units)
    _stop()
    for directory in _configuration_directories():
        if not os.path.lexists(directory):
            protected_path(os.path.dirname(directory), directory=True)
            with directory_fd(directory, create=True, mode=0o755):
                pass
        protected_path(directory, directory=True)
        if directory in (os.path.dirname(CONFIG_DIR), CONFIG_DIR):
            with directory_fd(directory) as descriptor:
                metadata = os.fstat(descriptor)
                if metadata.st_uid != 0 or metadata.st_mode & 0o022:
                    raise ValueError(f"Unsafe privilege configuration directory: {directory}")
                # Reconcile traversal, retaining private credential file modes.
                os.fchown(descriptor, 0, 0)
                os.fchmod(descriptor, 0o755)
                os.fsync(descriptor)
    write_json_atomic(POLICY_PATH, policy, mode=0o644, sort_keys=True)
    write_json_atomic(AUTH_PATH, auth, mode=0o600)
    for source, destination in ((cert, "server.crt"), (key, "server.key")):
        protected_path(os.path.realpath(source))
        with open(source, encoding="utf-8") as file_obj:
            write_text_atomic(CONFIG_DIR + "/" + destination, file_obj.read(), mode=0o600)
    _managed_write(POLKIT_PATH, _marker(POLKIT_PATH) + "\n" +
                   'polkit.addRule(function(action, subject) {\n'
                   f'    if (subject.user === "{config.username}") return polkit.Result.NO;\n'
                   '});\n')
    for name, unit in units.items():
        _managed_write(f"{UNIT_ROOT}/{name}.service", unit)
    run(["systemctl", "daemon-reload"])
    # Explicit restarts load new credentials and expire old broker requests.
    for name in (BROKER, WEB):
        run(["systemctl", "enable", name + ".service"])
        run(["systemctl", "restart", name + ".service"])
        state = inspect_unit_state(name + ".service")
        if state["ActiveState"] != "active" or state["UnitFileState"] != "enabled":
            raise RuntimeError("Privilege approval service activation did not verify: " + name)
    approval_origin = privilege_broker_origin(config)
    _wait_ready(approval_origin)
