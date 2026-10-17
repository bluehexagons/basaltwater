"""Install the optional supervisor without granting an AI root execution."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import copy
import fcntl
import grp
import os
import pwd
import stat
from pathlib import Path
from typing import Any

from lib.atomic_io import write_json_atomic
from lib.config import SetupConfig
from lib.machine_state import can_manage_system_services
from lib.maintenance_agent import _load_baseline, bound_report_history, load_policy, load_reports
from lib.maintenance_agent_config import CONFIG_FILE, CONTROL_DIR, DATA_DIR, SERVICE_NAME, setup_policy
from lib.remote_utils import is_dry_run, run
from lib.setup_directories import check_directory_path, directory_fd
from lib.unit_transaction import replace_units
from lib.validation import validate_filesystem_path
from lib.validators import validate_username


_ACCOUNT = "basaltwater-maintenance"
_HOME = "/var/lib/basaltwater-maintenance"


def maintenance_context(config: SetupConfig) -> dict[str, Any]:
    """Export only workload declarations, never serialized setup credentials."""
    units = ["sshd.service" if config.system_type == "agent_cachyos" else "ssh.service"]
    for enabled, unit in (
        (config.include_web_server or config.web_panel_port is not None or bool(config.homebox or config.gogs), "nginx.service"),
        (config.enable_samba, "smbd.service"), (config.enable_rdp, "xrdp.service"),
        (bool(config.homebox), "homebox.service"),
        (bool(config.gogs), "gogs.service"), (config.enable_syncthing, "basaltwater-syncthing.service"),
    ):
        if enabled:
            units.append(unit)
    for name in ("cleanup-maintenance", "user-cache-maintenance", "auto-update-apt", "security-monitor", "storage-ops"):
        if Path(f"/etc/systemd/system/{name}.timer").is_file():
            units.append(name + ".timer")
    return {"system_type": config.system_type, "machine_type": config.machine_type,
            "units": list(dict.fromkeys(units)), "backups": bool(config.backup_specs),
            "sync": bool(config.sync_specs), "scrub": bool(config.scrub_specs),
            "shared_storage": bool(config.smb_mounts or config.enable_samba),
            "agents": config.selected_agent_tools(), "web_interfaces": config.web_interfaces or []}


def _identity(config: SetupConfig) -> pwd.struct_passwd:
    if config.username != "root":
        return pwd.getpwnam(config.username)
    try:
        account = pwd.getpwnam(_ACCOUNT)
    except KeyError:
        run(["useradd", "--system", "--user-group", "--create-home", "--home-dir", _HOME,
             "--shell", "/usr/sbin/nologin", _ACCOUNT])
        account = pwd.getpwnam(_ACCOUNT)
    if (account.pw_uid == 0 or account.pw_gid == 0 or account.pw_dir != _HOME
            or account.pw_shell not in {"/usr/sbin/nologin", "/sbin/nologin"}
            or account.pw_name != _ACCOUNT
            or grp.getgrgid(account.pw_gid).gr_name != _ACCOUNT
            or set(os.getgrouplist(account.pw_name, account.pw_gid)) != {account.pw_gid}):
        raise RuntimeError("Refusing an incompatible maintenance service account")
    return account


def _validate_directory(descriptor: int, path: str, allowed_uids: set[int]) -> None:
    metadata = os.fstat(descriptor)
    if metadata.st_uid not in allowed_uids or metadata.st_mode & 0o022:
        raise RuntimeError(f"Unsafe maintenance directory: {path}")


def _directory(path: str, uid: int, gid: int, mode: int, *, prior_uids: set[int] | None = None) -> None:
    validate_filesystem_path(path)
    # Only the control directory may migrate from a recognized controller.
    # Descriptor mutations cannot follow a replaced path or symlink ancestor.
    with directory_fd(path, create=True, mode=mode) as descriptor:
        _validate_directory(descriptor, path, {0, uid, *(prior_uids or ())})
        os.fchown(descriptor, uid, gid)
        os.fchmod(descriptor, mode)


@contextmanager
def _policy_lock(uid: int, prior_uids: set[int]) -> Iterator[int]:
    """Acquire the existing policy lock before changing controller ownership."""
    with directory_fd(DATA_DIR, create=True, mode=0o750) as data:
        _validate_directory(data, DATA_DIR, {0})
        with directory_fd(CONTROL_DIR, create=True, mode=0o700) as directory:
            _validate_directory(directory, CONTROL_DIR, {0, uid, *prior_uids})
            try:
                descriptor = os.open(
                    "settings.lock",
                    os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                    0o600,
                    dir_fd=directory,
                )
            except OSError as exc:
                raise RuntimeError("Cannot open maintenance settings lock") from exc
            try:
                metadata = os.fstat(descriptor)
                if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                        or metadata.st_uid not in {0, uid, *prior_uids}
                        or metadata.st_mode & 0o077):
                    raise RuntimeError("Unsafe maintenance settings lock")
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise RuntimeError("Maintenance settings are being updated; retry setup") from exc
                yield descriptor
            finally:
                os.close(descriptor)


def configure_maintenance_agent(config: SetupConfig) -> None:
    """Panel setups install a disabled service so enabling needs no root shell."""
    if not can_manage_system_services(config.machine_type):
        if config.maintenance_agent:
            raise ValueError("The maintenance agent requires a target with system services")
        return
    if is_dry_run():
        print("  [DRY-RUN] Would configure tailored maintenance supervision and panel controls")
        return
    config_parent = str(Path(CONFIG_FILE).parent)
    for path in (DATA_DIR, CONTROL_DIR, config_parent):
        check_directory_path(path)
    if os.path.lexists(CONFIG_FILE):
        _load_baseline(CONFIG_FILE)
    owner = _identity(config)
    if not validate_username(owner.pw_name) or owner.pw_uid == 0:
        raise ValueError("Maintenance AI requires a non-root account")
    validate_filesystem_path(owner.pw_dir, must_exist=True)
    with directory_fd(owner.pw_dir) as descriptor:
        if os.fstat(descriptor).st_uid != owner.pw_uid:
            raise RuntimeError("Maintenance account must own its home")
    controller = owner
    if config.web_panel_port is not None:
        controller = pwd.getpwnam("basaltwater-web-panel" if config.username == "root" else config.username)
    prior_uids: set[int] = set()
    if config.username == "root":
        for name in (_ACCOUNT, "basaltwater-web-panel"):
            try:
                prior = pwd.getpwnam(name)
            except KeyError:
                continue
            if prior.pw_name == name and prior.pw_uid != 0:
                prior_uids.add(prior.pw_uid)
    with directory_fd(config_parent, create=True) as descriptor:
        metadata = os.fstat(descriptor)
        if metadata.st_uid != 0 or metadata.st_mode & 0o022:
            raise RuntimeError("Unsafe maintenance configuration directory")
    policy_path = os.path.join(CONTROL_DIR, "settings.json")
    with _policy_lock(controller.pw_uid, prior_uids) as lock:
        _directory(DATA_DIR, 0, controller.pw_gid, 0o750)
        _directory(CONTROL_DIR, controller.pw_uid, controller.pw_gid, 0o700, prior_uids=prior_uids)
        os.fchown(lock, controller.pw_uid, controller.pw_gid)
        os.fchmod(lock, 0o600)
        policy = setup_policy(config)
        if os.path.lexists(policy_path):
            existing = load_policy(policy_path)  # Corrupt/unsupported state is never overwritten.
            overrides = {key: value for key, value in policy.items()
                         if getattr(config, "maintenance_agent" if key == "enabled" else "maintenance_agent_" + key, None) is not None}
            policy = {**existing, **overrides}
        write_json_atomic(policy_path, policy, uid=controller.pw_uid, gid=controller.pw_gid)
    baseline = {"version": 1, "username": owner.pw_name, "report_gid": controller.pw_gid, "context": maintenance_context(config)}
    write_json_atomic(CONFIG_FILE, baseline, mode=0o644)
    reports_path = os.path.join(DATA_DIR, "reports.json")
    if os.path.lexists(reports_path):
        reports = load_reports(reports_path)
    else:
        reports = {"version": 1, "reports": []}
    reports.update(username=owner.pw_name, context=baseline["context"])
    bound_report_history(reports)
    # Updating the control owner must make retained reports readable immediately,
    # including when the policy is disabled and no check is requested.
    write_json_atomic(reports_path, reports, mode=0o640, uid=0, gid=controller.pw_gid)
    if policy["enabled"] and policy["daily_calls"] and config.system_type != "agent_cachyos":
        from common.agent_steps import install_codex

        owner_config = copy.copy(config)
        owner_config.username = owner.pw_name
        install_codex(owner_config)
    # Fixed root supervisor; the provider process is separately launched as owner.
    service = f"""[Unit]
Description=Basaltwater tailored maintenance supervisor
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=root
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
ExecStart=/usr/bin/python3 /opt/basaltwater/common/service_tools/maintenance_agent.py
TimeoutStartSec=65min
KillMode=control-group
"""
    timer = f"""[Unit]
Description=Poll Basaltwater maintenance policy

[Timer]
OnCalendar=*:0/15
Persistent=true
RandomizedDelaySec=30s
Unit={SERVICE_NAME}.service

[Install]
WantedBy=timers.target
"""
    path_unit = f"""[Unit]
Description=Wake the Basaltwater maintenance supervisor

[Path]
PathExists={CONTROL_DIR}/wake.json
PathChanged={CONTROL_DIR}/settings.json
Unit={SERVICE_NAME}.service

[Install]
WantedBy=multi-user.target
"""
    replace_units({SERVICE_NAME + ".service": service, SERVICE_NAME + ".timer": timer, SERVICE_NAME + ".path": path_unit},
                  activate=(SERVICE_NAME + ".timer", SERVICE_NAME + ".path"))
    print(f"  ✓ Maintenance agent {'enabled' if policy['enabled'] else 'ready to enable in the panel'}; AI identity: {owner.pw_name}")
    if policy["enabled"]:
        print(f"  Sign in to Codex as {owner.pw_name} before AI reviews; deterministic checks work without credentials")
