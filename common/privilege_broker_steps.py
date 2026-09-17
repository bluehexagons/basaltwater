"""Install the opt-in broker and its independent HTTPS approval service."""

from __future__ import annotations

import grp
import json
import os
import pwd
import re
import secrets
import socket
import ssl
import time
from urllib.parse import urlsplit

from lib.atomic_io import write_json_atomic, write_text_atomic
from lib.machine_state import can_manage_system_services, is_vm
from lib.privilege_auth import validate_auth
from lib.privilege_policy import CONFIG_DIR, POLICY_PATH, WEB_USER, load_policy, protected_path, validate_policy
from lib.privilege_setup import privilege_broker_origin, validate_broker_settings
from lib.remote_utils import is_dry_run, is_service_active, run
from lib.validators import validate_username

MARKER = "# Managed by infra-tools privilege broker"
BROKER = "infra-tools-privilege-broker"
WEB = "infra-tools-privilege-approval"
AUTH_PATH = CONFIG_DIR + "/auth.json"
POLKIT_PATH = "/etc/polkit-1/rules.d/00-infra-tools-privilege-broker.rules"
UNIT_ROOT = "/etc/systemd/system"


def _marker(path: str) -> str:
    return "// Managed by infra-tools privilege broker" if path == POLKIT_PATH else MARKER


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
Description=infra-tools agent privilege broker
After=network.target
[Service]
User=root
RuntimeDirectory={BROKER}
RuntimeDirectoryMode=0755
StateDirectory={BROKER}
StateDirectoryMode=0700
ExecStart=/usr/bin/python3 -I /opt/infra_tools/common/service_tools/privilege_broker.py
RestrictAddressFamilies=AF_UNIX
{common}
[Install]
WantedBy=multi-user.target
""",
        WEB: f"""{MARKER}
[Unit]
Description=infra-tools independent privilege approval page
Requires={BROKER}.service
After={BROKER}.service network.target
[Service]
User={WEB_USER}
Group={WEB_USER}
LoadCredential=auth:{AUTH_PATH}
LoadCredential=cert:{CONFIG_DIR}/server.crt
LoadCredential=key:{CONFIG_DIR}/server.key
ExecStart=/usr/bin/python3 -I /opt/infra_tools/common/service_tools/privilege_approval.py
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
CapabilityBoundingSet=
{common}
[Install]
WantedBy=multi-user.target
""",
    }


def _stop() -> None:
    for name in (WEB, BROKER):
        path = f"{UNIT_ROOT}/{name}.service"
        if os.path.lexists(path):
            protected_path(path)
            with open(path, encoding="utf-8") as source:
                if not source.read().startswith(MARKER + "\n"):
                    raise ValueError("Refusing to stop an unmanaged approval service")
            run(["systemctl", "disable", "--now", name + ".service"])


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
    if config.disable_privilege_broker:
        _stop()
        for path in (f"{UNIT_ROOT}/{WEB}.service", f"{UNIT_ROOT}/{BROKER}.service", POLKIT_PATH):
            if os.path.lexists(path):
                protected_path(path)
                with open(path, encoding="utf-8") as source:
                    if not source.read().startswith(_marker(path) + "\n"):
                        raise ValueError(f"Refusing to remove unmanaged file: {path}")
                os.unlink(path)
        if os.path.lexists(AUTH_PATH):
            protected_path(AUTH_PATH)
            os.unlink(AUTH_PATH)
        run(["systemctl", "daemon-reload"])
        print("  ✓ Privilege approvals disabled; policy and audit history retained")
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
    for directory in (os.path.dirname(CONFIG_DIR), CONFIG_DIR,
                      os.path.dirname(os.path.dirname(POLKIT_PATH)), os.path.dirname(POLKIT_PATH)):
        if not os.path.lexists(directory):
            protected_path(os.path.dirname(directory), directory=True)
            os.mkdir(directory, 0o755)
        protected_path(directory, directory=True)
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
                  "requester_uid": account.pw_uid, "ttl_seconds": 300, "services": {}, "reboot": "approve", "commands": "approve"}
    validate_policy(policy)
    # Every installed Python dependency is part of the privileged boundary.
    for directory in ("/opt/infra_tools/lib", "/opt/infra_tools/common", "/opt/infra_tools/plugins"):
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
    _stop()
    try:
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
        for name, unit in render_units().items():
            _managed_write(f"{UNIT_ROOT}/{name}.service", unit)
        run(["systemctl", "daemon-reload"])
        run(["systemctl", "enable", "--now", BROKER + ".service", WEB + ".service"])
        for _ in range(10):
            approval_origin = privilege_broker_origin(config)
            if is_service_active(BROKER) and is_service_active(WEB) and _https_ready(approval_origin, CONFIG_DIR + "/server.crt"):
                print("  ✓ Privilege approvals: " + approval_origin + "/")
                return
            time.sleep(0.2)
        raise RuntimeError("Privilege approval services did not become active")
    except Exception:
        _stop()
        raise
