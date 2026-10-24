"""Managed Syncthing service with GUI-owned sharing configuration."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import copy
import grp
import json
import os
import pwd
import re
import shlex
import shutil
import stat
import tempfile
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request

from common.storage_steps import STORAGE_MARKER, assert_declared_storage_mount
from lib.atomic_io import remove_file_durable, write_bytes_atomic, write_json_atomic, write_text_atomic
from lib.config import DEFAULT_SYNCTHING_ROOT, SetupConfig
from lib.credentials import get_runtime_credential
from lib.local_http import open_loopback
from lib.machine_state import can_manage_system_services
from lib.operation_state import OperationStateError, OperationStateStore
from lib.remote_utils import is_dry_run, is_package_installed, run
from lib.setup_directories import directory_fd
from lib.unit_transaction import inspect_unit_state, snapshot_unit_file
from lib.validation import _validate_syncthing_root, validate_filesystem_path
from lib.vm_storage import storage_mounts


SYNCTHING_HOME = "/var/lib/basaltwater/syncthing"
SYNCTHING_SERVICE_NAME = "basaltwater-syncthing"
SYNCTHING_SERVICE_FILE = f"/etc/systemd/system/{SYNCTHING_SERVICE_NAME}.service"
SYNCTHING_GUI_ADDRESS = "127.0.0.1:8384"
SYNCTHING_API_URL = f"http://{SYNCTHING_GUI_ADDRESS}/rest/config"
_DEVICE_ID_PATTERN = re.compile(r"^[A-Z2-7]{7}(?:-[A-Z2-7]{7}){7}$")


def _account(username: str) -> tuple[int, int, str]:
    """Return the target user's UID, GID, and primary group name."""

    try:
        account = pwd.getpwnam(username)
        group_name = grp.getgrgid(account.pw_gid).gr_name
    except KeyError as exc:
        raise RuntimeError(f"Syncthing setup user does not exist: {username}") from exc
    return account.pw_uid, account.pw_gid, group_name


def _systemd_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return f'"{escaped}"'


def _share_root(config: SetupConfig) -> str:
    return config.syncthing_root or DEFAULT_SYNCTHING_ROOT


def _render_service(config: SetupConfig, group_name: str) -> str:
    share_root = _share_root(config)
    writable_lines = "\n".join(
        f"ReadWritePaths={_systemd_quote(path)}"
        for path in (SYNCTHING_HOME, share_root)
    )
    return f"""[Unit]
Description=Managed Syncthing file synchronization
Documentation=https://docs.syncthing.net/
Wants=network-online.target
After=network-online.target
RequiresMountsFor={_systemd_quote(share_root)}

[Service]
Type=simple
User={config.username}
Group={group_name}
UMask=0007
ExecStart=/usr/bin/syncthing serve --home={SYNCTHING_HOME} --gui-address=http://{SYNCTHING_GUI_ADDRESS} --no-browser --no-restart --no-upgrade
Restart=on-failure
RestartSec=5s
SuccessExitStatus=3 4
NoNewPrivileges=true
PrivateDevices=true
PrivateTmp=true
ReadOnlyPaths=/tmp /var/tmp /dev/shm
ProtectClock=true
ProtectControlGroups=true
ProtectHome=true
ProtectHostname=true
ProtectKernelLogs=true
ProtectKernelModules=true
ProtectKernelTunables=true
ProtectSystem=strict
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
RestrictRealtime=true
RestrictSUIDSGID=true
LockPersonality=true
CapabilityBoundingSet=
{writable_lines}

[Install]
WantedBy=multi-user.target
"""


def _run_as_user(
    username: str,
    syncthing_args: list[str],
    *,
    capture_output: bool = False,
    check: bool = True,
    input_data: str | None = None,
) -> Any:
    return run(
        ["runuser", "-u", username, "--", "/usr/bin/syncthing", *syncthing_args],
        capture_output=capture_output,
        check=check,
        input_data=input_data,
    )


def _cli(
    username: str,
    arguments: list[str],
    *,
    capture_output: bool = False,
    check: bool = True,
) -> Any:
    return _run_as_user(
        username,
        ["cli", f"--home={SYNCTHING_HOME}", *arguments],
        capture_output=capture_output,
        check=check,
    )


def _wait_for_api(username: str, attempts: int = 30) -> None:
    for _attempt in range(attempts):
        result = _cli(username, ["show", "system"], capture_output=True, check=False)
        if result.returncode == 0:
            return
        time.sleep(0.5)
    raise RuntimeError("Syncthing API did not become ready on 127.0.0.1:8384")


def _load_current_config(username: str) -> dict[str, Any]:
    result = _cli(username, ["config", "dump-json"], capture_output=True)
    try:
        loaded = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Syncthing returned an invalid configuration") from exc
    if not isinstance(loaded, dict):
        raise RuntimeError("Syncthing returned a non-object configuration")
    return loaded


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(f"Syncthing configuration is missing {name}")
    return copy.deepcopy(value)


def _staggered_versioning(default: object) -> dict[str, Any]:
    versioning = _mapping(default, "folder versioning defaults")
    versioning.update(
        {
            "type": "staggered",
            "params": {"cleanInterval": "3600", "maxAge": "31536000"},
            "cleanupIntervalS": 3600,
            "fsPath": "",
            "fsType": "basic",
        }
    )
    return versioning


def _validate_folder_paths(current: dict[str, Any], share_root: str) -> None:
    """Reject GUI-managed folders that escape the writable storage root."""

    folders = current.get("folders")
    if not isinstance(folders, list):
        raise RuntimeError("Syncthing configuration is missing folders")
    resolved_root = os.path.realpath(share_root)
    for folder in folders:
        path = folder.get("path") if isinstance(folder, dict) else None
        try:
            contained = (
                isinstance(path, str)
                and os.path.isabs(path)
                and os.path.commonpath((resolved_root, os.path.realpath(path)))
                == resolved_root
            )
        except ValueError:
            contained = False
        if not contained:
            raise RuntimeError(
                f"Syncthing folder path is outside the configured storage root: {path}"
            )


def build_syncthing_policy_config(
    current: dict[str, Any],
    share_root: str,
) -> dict[str, Any]:
    """Apply service policy without replacing GUI-managed devices or folders."""

    desired = copy.deepcopy(current)
    _validate_folder_paths(desired, share_root)
    defaults = _mapping(desired.get("defaults"), "defaults")
    default_folder = _mapping(defaults.get("folder"), "folder defaults")
    default_folder["path"] = share_root
    default_folder["versioning"] = _staggered_versioning(
        default_folder.get("versioning")
    )
    defaults["folder"] = default_folder
    desired["defaults"] = defaults

    gui = _mapping(desired.get("gui"), "GUI settings")
    if not isinstance(gui.get("user"), str) or not gui["user"]:
        raise RuntimeError("Syncthing GUI administrator was not configured")
    if not isinstance(gui.get("password"), str) or not gui["password"]:
        raise RuntimeError("Syncthing GUI password was not configured")
    gui.update(
        {
            "enabled": True,
            "address": SYNCTHING_GUI_ADDRESS,
            "useTLS": False,
            "debugging": False,
            "insecureAdminAccess": False,
            "insecureSkipHostcheck": False,
            "insecureAllowFrameLoading": False,
        }
    )
    desired["gui"] = gui

    options = _mapping(desired.get("options"), "options")
    options.update(
        {
            "defaultFolderPath": share_root,
            "startBrowser": False,
            "globalAnnounceEnabled": True,
            "localAnnounceEnabled": True,
            "relaysEnabled": True,
            "natEnabled": False,
            "urAccepted": -1,
            "crashReportingEnabled": False,
        }
    )
    desired["options"] = options
    return desired


def _put_config(desired: dict[str, Any]) -> None:
    gui = desired.get("gui")
    api_key = gui.get("apiKey") if isinstance(gui, dict) else None
    if not isinstance(api_key, str) or not api_key:
        raise RuntimeError("Syncthing configuration does not contain an API key")
    request = Request(
        SYNCTHING_API_URL,
        data=json.dumps(desired, separators=(",", ":")).encode("utf-8"),
        method="PUT",
        headers={"Content-Type": "application/json", "X-API-Key": api_key},
    )
    try:
        with open_loopback(request, timeout=20) as response:
            if response.status not in {200, 204}:
                raise RuntimeError(
                    f"Syncthing rejected its managed config (HTTP {response.status})"
                )
    except HTTPError as exc:
        detail = exc.read(500).decode("utf-8", errors="replace").strip()
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(
            f"Syncthing rejected its managed config (HTTP {exc.code}){suffix}"
        ) from exc
    except URLError as exc:
        raise RuntimeError("Could not reach the loopback Syncthing API") from exc


def _configure_syncthing_https(config: SetupConfig) -> str | None:
    if os.geteuid() != 0 or not os.path.isfile(
        "/opt/basaltwater/common/service_tools/basaltwater_web.py"
    ):
        return None
    from common.godot_web_steps import configure_internal_web_host, identities_for_config

    configure_internal_web_host(
        identities_for_config(config.host, config.system_hostname),
        [config.username],
        config.effective_access_sources(),
        configure_static_site=True,
        install_utility=True,
    )
    result = run(
        "SUDO_USER=" + shlex.quote(config.username)
        + " /usr/local/bin/basaltwater-web forward add syncthing"
        + " --listen auto --to 127.0.0.1:8384 --profile syncthing --wait 30 --json",
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "HTTPS forward failed").strip()
        raise RuntimeError(f"Could not configure HTTPS Syncthing endpoint: {detail}")
    try:
        payload = json.loads(result.stdout)
        if not isinstance(payload, dict):
            raise ValueError
        url = payload.get("url")
        listen = payload.get("listen")
    except (TypeError, ValueError):
        url, listen = None, None
    if (
        not isinstance(url, str)
        or not url.startswith("https://")
        or not isinstance(listen, int)
        or not 1024 <= listen <= 65535
    ):
        raise RuntimeError("HTTPS gateway returned an invalid Syncthing endpoint")
    return url


def _remove_syncthing_https(config: SetupConfig) -> None:
    if not os.path.isfile("/usr/local/bin/basaltwater-web"):
        return
    result = run(
        "SUDO_USER=" + shlex.quote(config.username)
        + " /usr/local/bin/basaltwater-web forward remove syncthing --json",
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = (
            result.stderr or result.stdout or "HTTPS forward removal failed"
        ).strip()
        if "HTTPS forward does not exist: syncthing" not in detail:
            raise RuntimeError(
                f"Could not remove HTTPS Syncthing endpoint: {detail}"
            )


def _prepare_share_root(config: SetupConfig, group_name: str) -> None:
    share_root = _share_root(config)
    _validate_syncthing_root(share_root)
    if os.path.lexists(share_root) and (
        os.path.islink(share_root)
        or not os.path.isdir(share_root)
    ):
        raise RuntimeError(f"Refusing unsafe Syncthing share root: {share_root}")
    if os.path.realpath(share_root) != share_root:
        raise RuntimeError(
            f"Refusing Syncthing share root with symbolic-link traversal: {share_root}"
        )
    assert_declared_storage_mount(config, share_root)
    uid, gid, _group = _account(config.username)
    declared_mount = any(mount.path == share_root for mount in storage_mounts(config))
    try:
        # Parents belong to the operator or mount provisioning, never to this
        # service. Require them to exist instead of creating or changing them.
        with directory_fd(os.path.dirname(share_root)) as parent:
            try:
                os.mkdir(os.path.basename(share_root), mode=0o770, dir_fd=parent)
                created = True
            except FileExistsError:
                created = False
        with directory_fd(share_root) as descriptor:
            observed = os.fstat(descriptor)
            mode = stat.S_IMODE(observed.st_mode)
            prepared = (
                observed.st_uid == uid
                and observed.st_gid == gid
                and mode & 0o700 == 0o700
                and mode & 0o007 == 0
            )
            entries = set(os.listdir(descriptor))
            fresh_mount = declared_mount and entries <= {STORAGE_MARKER, "lost+found"}
            if fresh_mount and "lost+found" in entries:
                # A recovery directory containing recovered files is data,
                # not evidence of a fresh filesystem. Do not follow a link.
                flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
                recovery = os.open("lost+found", flags, dir_fd=descriptor)
                try:
                    fresh_mount = not os.listdir(recovery)
                finally:
                    os.close(recovery)
            if entries and not prepared and not fresh_mount:
                raise RuntimeError(
                    f"Refusing to adopt nonempty Syncthing root: {share_root}. "
                    "Choose a dedicated directory; after reviewing its contents, "
                    f"explicitly prepare only that directory with chown "
                    f"{config.username}:{group_name} and chmod 0770, then rerun. "
                    "Do not change ownership recursively or use live service data."
                )
            if created or not prepared:
                os.fchown(descriptor, uid, gid)
                os.fchmod(descriptor, 0o770)
    except OSError as exc:
        raise RuntimeError(
            f"Could not prepare Syncthing root {share_root}; prepare its parent "
            "directory or mount explicitly, then rerun"
        ) from exc

    for permission in ("-r", "-x", "-w"):
        access = run(
            ["runuser", "-u", config.username, "--", "/usr/bin/test", permission, share_root],
            check=False,
            capture_output=True,
        )
        if access.returncode != 0:
            raise RuntimeError(
                f"Syncthing user {config.username} cannot read, traverse or write "
                f"{share_root}. Explicitly grant traversal on its ancestors and "
                "access to the dedicated root using permissions or an ACL, then "
                "rerun; setup does not change parent permissions."
            )


def _disable_before_removal(unit_exists: bool) -> None:
    """Require confirmed stopped and disabled states before deleting the unit."""
    unit = f"{SYNCTHING_SERVICE_NAME}.service"
    disabled = run(["systemctl", "disable", "--now", unit], check=False)
    active = run(["systemctl", "is-active", unit], check=False, capture_output=True)
    enabled = run(["systemctl", "is-enabled", unit], check=False, capture_output=True)
    stopped = active.returncode == 3 and str(active.stdout or "").strip() in {"inactive", "failed"}
    unenabled = enabled.returncode == 1 and str(enabled.stdout or "").strip() == "disabled"
    if disabled.returncode == 0 and stopped and unenabled:
        return
    if (
        not unit_exists
        and (stopped or (active.returncode == 4 and str(active.stdout or "").strip() == "unknown"))
        and enabled.returncode == 4 and str(enabled.stdout or "").strip() == "not-found"
    ):
        loaded = run(["systemctl", "show", unit, "--property=LoadState", "--value"], check=False, capture_output=True)
        if loaded.returncode == 0 and str(loaded.stdout or "").strip() == "not-found":
            return
    raise RuntimeError(
        "Could not confirm managed Syncthing service stopped and disabled; "
        "unit preserved. Check systemctl status and is-enabled, then retry."
    )


def _preflight_existing_folders(username: str, share_root: str) -> None:
    """Validate a running endpoint before changing its writable root."""

    active = run(
        ["systemctl", "is-active", "--quiet", f"{SYNCTHING_SERVICE_NAME}.service"],
        check=False,
    )
    if active.returncode != 0:
        return
    _wait_for_api(username)
    _validate_folder_paths(_load_current_config(username), share_root)


def _validate_state_paths() -> None:
    """Refuse redirected persistent state before privileged ownership changes."""
    for path in (SYNCTHING_HOME, SYNCTHING_SERVICE_FILE):
        validate_filesystem_path(path)
        if os.path.realpath(path) != path:
            raise RuntimeError(f"Refusing symlinked Syncthing state or service path: {path}")
    if os.path.lexists(SYNCTHING_HOME) and not os.path.isdir(SYNCTHING_HOME):
        raise RuntimeError(f"Syncthing state must be a directory: {SYNCTHING_HOME}")
    if os.path.lexists(SYNCTHING_SERVICE_FILE) and not os.path.isfile(SYNCTHING_SERVICE_FILE):
        raise RuntimeError(f"Syncthing service must be a regular file: {SYNCTHING_SERVICE_FILE}")
    # Syncthing reads these when generating/updating an existing identity.
    for name in ("config.xml", "cert.pem", "key.pem"):
        path = os.path.join(SYNCTHING_HOME, name)
        if os.path.lexists(path) and (os.path.islink(path) or not os.path.isfile(path) or os.stat(path).st_nlink != 1):
            raise RuntimeError(f"Refusing unsafe Syncthing identity file: {path}")
    existing = {name for name in ("config.xml", "cert.pem", "key.pem")
                if os.path.exists(os.path.join(SYNCTHING_HOME, name))}
    has_state = os.path.isdir(SYNCTHING_HOME) and bool(os.listdir(SYNCTHING_HOME))
    if has_state and not {"cert.pem", "key.pem"} <= existing:
        raise RuntimeError(
            "Incomplete Syncthing identity; preserve existing state and recover "
            "the device certificate and key before rerunning setup"
        )


def _stop_before_configuration() -> None:
    """Permit a missing first-install unit, but never configure a live daemon."""
    unit = f"{SYNCTHING_SERVICE_NAME}.service"
    stopped = run(["systemctl", "stop", unit], check=False)
    state = run(["systemctl", "is-active", unit], check=False, capture_output=True)
    if state.returncode == 0 or str(state.stdout or "").strip() not in {"inactive", "failed", "unknown"}:
        raise RuntimeError("Managed Syncthing service did not stop; configuration was not changed")
    if stopped.returncode != 0:
        loaded = run(["systemctl", "show", unit, "--property=LoadState", "--value"], check=False, capture_output=True)
        if loaded.returncode != 0 or str(loaded.stdout or "").strip() != "not-found":
            raise RuntimeError("Could not stop managed Syncthing service")


def _prepare_state_home(username: str, uid: int, gid: int) -> None:
    """Restore account access after staging locks down the shared state parent."""
    parent = os.path.dirname(SYNCTHING_HOME)
    os.makedirs(parent, mode=0o700, exist_ok=True)
    if not is_package_installed("acl"):
        run(["apt-get", "-o", "DPkg::Lock::Timeout=60", "install", "-y", "-qq", "acl"])
    # Keep the shared directory private: this account can traverse, not list
    # or write it. Reapply after every runtime staging chmod (which masks ACLs).
    run(["setfacl", "-m", f"u:{uid}:--x", "--", parent])
    os.makedirs(SYNCTHING_HOME, mode=0o700, exist_ok=True)
    os.chmod(SYNCTHING_HOME, 0o700)
    os.chown(SYNCTHING_HOME, uid, gid)
    for permission in ("-x", "-w"):
        run(["runuser", "-u", username, "--", "/usr/bin/test", permission, SYNCTHING_HOME])


def _snapshot_config(backup_dir: str) -> dict[str, int] | None:
    """Save only offline configuration, privately, with restoration metadata."""
    path = os.path.join(SYNCTHING_HOME, "config.xml")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise RuntimeError(f"Refusing unsafe Syncthing configuration: {path}")
        content = stream.read(16 * 1024 * 1024 + 1)
    if len(content) > 16 * 1024 * 1024:
        raise RuntimeError("Syncthing configuration exceeds 16 MiB")
    write_bytes_atomic(os.path.join(backup_dir, "config.xml"), content, mode=0o600)
    return {"mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid, "gid": info.st_gid}


def _assert_no_syncthing_recovery() -> None:
    """Refuse setup/removal before preflight mutates an unresolved endpoint."""
    store = OperationStateStore(os.path.join(os.path.dirname(SYNCTHING_HOME), ".syncthing-operation.json"))
    previous = store.load()
    if previous is not None:
        raise OperationStateError(
            f"Unfinished {previous.operation_type} operation is recorded in {store.path}; "
            "preserve private evidence and verify recovery before retrying"
        )


def _restore_syncthing(
    username: str, backup_dir: str, previous_unit: dict | None,
    previous_state: dict[str, str], previous_config: dict[str, int] | None,
    first_install: bool,
) -> None:
    """Quiesce the candidate before restoring login, unit and activation state."""
    unit = f"{SYNCTHING_SERVICE_NAME}.service"
    _stop_before_configuration()
    state = inspect_unit_state(unit)
    if state["LoadState"] != "not-found":
        flags = ["--runtime"] if state["UnitFileState"] == "enabled-runtime" else []
        run(["systemctl", "disable", *flags, unit])
        state = inspect_unit_state(unit)
    if state["ActiveState"] in {"active", "activating"} or state["UnitFileState"] in {"enabled", "enabled-runtime"}:
        raise RuntimeError("Could not quiesce replacement Syncthing service")
    if previous_config is not None:
        with open(os.path.join(backup_dir, "config.xml"), "rb") as stream:
            write_bytes_atomic(
                os.path.join(SYNCTHING_HOME, "config.xml"), stream.read(), **previous_config,
            )
    # With no old config (first install), leave generated identity/config in
    # place for explicit recovery, but never start it during rollback.
    if previous_unit is None:
        remove_file_durable(SYNCTHING_SERVICE_FILE)
    else:
        write_text_atomic(SYNCTHING_SERVICE_FILE, **previous_unit)
    run(["systemctl", "daemon-reload"])
    if previous_unit is not None and not first_install:
        if previous_state["ActiveState"] == "active":
            _validate_state_paths()
            if not os.path.isfile(os.path.join(SYNCTHING_HOME, "config.xml")):
                raise RuntimeError("Prior Syncthing configuration is unavailable; refusing to regenerate it")
        if previous_state["UnitFileState"] in {"enabled", "enabled-runtime"}:
            flags = ["--runtime"] if previous_state["UnitFileState"] == "enabled-runtime" else []
            run(["systemctl", "enable", *flags, unit])
        if previous_state["ActiveState"] == "active":
            run(["systemctl", "start", unit])
            _wait_for_api(username)
    actual = inspect_unit_state(unit)
    if first_install:
        if actual["ActiveState"] in {"active", "activating"} or actual["UnitFileState"] in {"enabled", "enabled-runtime"}:
            raise RuntimeError("Unverified first Syncthing install is still active or enabled")
    elif (
        (actual["ActiveState"] == "active") != (previous_state["ActiveState"] == "active")
        or actual["UnitFileState"] != previous_state["UnitFileState"]
    ):
        raise RuntimeError("Restored Syncthing activation state did not match")


@contextmanager
def _syncthing_transaction(username: str) -> Iterator[None]:
    """Extend unit rollback across offline credentials and API policy changes.

    Unit replacement alone cannot restore application side effects. Keep one
    operation open until policy and API health have also passed. Never snapshot
    or roll back synchronized data, the database, or the device certificate/key.
    """
    parent = os.path.dirname(SYNCTHING_HOME)
    store = OperationStateStore(os.path.join(parent, ".syncthing-operation.json"))
    backup_dir = ""
    resolved = False
    record = None
    modified = False
    previous_config = None
    try:
        record = store.begin("syncthing-reconciliation", SYNCTHING_HOME, "snapshotting")
        previous_unit = snapshot_unit_file(SYNCTHING_SERVICE_FILE)
        previous_state = inspect_unit_state(f"{SYNCTHING_SERVICE_NAME}.service")
        first_install = not os.path.isfile(os.path.join(SYNCTHING_HOME, "config.xml"))
        if previous_state["ActiveState"] == "active" and (
            previous_unit is None or first_install
        ):
            raise RuntimeError("Active Syncthing endpoint lacks a recoverable managed unit/config")
        backup_dir = tempfile.mkdtemp(prefix=".syncthing-recovery-", dir=parent)
        store.transition(record.operation_id, "snapshotting", context={"backup_dir": backup_dir})
        write_json_atomic(os.path.join(backup_dir, "previous.json"), {
            "unit": previous_unit, "state": previous_state, "first_install": first_install,
        })
        store.transition(record.operation_id, "stopping", context={"backup_dir": backup_dir})
        modified = True  # A failed stop can already have stopped the old daemon.
        _stop_before_configuration()
        previous_config = _snapshot_config(backup_dir)
        write_json_atomic(os.path.join(backup_dir, "config-metadata.json"), previous_config)
        store.transition(record.operation_id, "configuring")
        yield
        store.complete(record.operation_id)
        resolved = True
    except BaseException:
        if record is None:
            raise
        try:
            if modified:
                marker_error = None
                try:
                    store.transition(record.operation_id, "rolling-back")
                except Exception as exc:
                    marker_error = exc
                _restore_syncthing(username, backup_dir, previous_unit, previous_state, previous_config, first_install)
                if marker_error is not None:
                    raise marker_error
            store.complete(record.operation_id, outcome="rolled_back" if modified else "failed")
            resolved = True
        except BaseException as recovery_error:
            try:
                store.transition(record.operation_id, "rollback-failed", status="recovery_required",
                                 context={"backup_dir": backup_dir, "error": type(recovery_error).__name__})
            except Exception:
                # The existing marker and private snapshots still identify the
                # interrupted operation even if updating the marker also fails.
                pass
            raise RuntimeError(
                f"Syncthing rollback needs recovery; inspect {store.path} and {backup_dir}; "
                "preserve private evidence and verify recovery before retrying"
            ) from recovery_error
        raise
    finally:
        store.close()
        if resolved and backup_dir:
            try:
                shutil.rmtree(backup_dir)
            except OSError as exc:
                print(f"  ⚠ Syncthing backup cleanup failed at {backup_dir}: {exc}")


def setup_syncthing(config: SetupConfig, **_kwargs: Any) -> None:
    """Install and reconcile one unprivileged, relay-capable Syncthing endpoint."""
    if not config.enable_syncthing and not config.disable_syncthing:
        return
    if not can_manage_system_services(config.machine_type):
        print("  ✓ Skipping Syncthing (persistent services unavailable)")
        return
    if is_dry_run():
        print("  [DRY-RUN] Skipping Syncthing service configuration")
        return
    _assert_no_syncthing_recovery()
    if config.disable_syncthing:
        _remove_syncthing_https(config)
        unit_exists = os.path.lexists(SYNCTHING_SERVICE_FILE)
        if unit_exists and (
            os.path.islink(SYNCTHING_SERVICE_FILE)
            or not os.path.isfile(SYNCTHING_SERVICE_FILE)
        ):
            raise RuntimeError(
                f"Refusing to remove unsafe Syncthing unit path: "
                f"{SYNCTHING_SERVICE_FILE}"
            )
        _disable_before_removal(unit_exists)
        if unit_exists:
            os.unlink(SYNCTHING_SERVICE_FILE)
        run(["systemctl", "daemon-reload"])
        print("  ✓ Managed Syncthing service and HTTPS endpoint removed")
        print(f"  ℹ Preserved Syncthing identity and database in {SYNCTHING_HOME}")
        retained_root = config.syncthing_root or DEFAULT_SYNCTHING_ROOT
        print(f"  ℹ Preserved synchronized files in {retained_root}")
        return

    admin_username = config.syncthing_admin
    if not admin_username:
        raise RuntimeError("Syncthing administrator username was not configured")
    admin_password = get_runtime_credential(config, admin_username)
    if admin_password is None:
        raise RuntimeError(
            f"Syncthing administrator credential is missing: {admin_username}"
        )
    if "\n" in admin_password or "\r" in admin_password:
        raise RuntimeError("Syncthing administrator password cannot contain line breaks")

    _validate_state_paths()
    if not is_package_installed("syncthing"):
        os.environ["DEBIAN_FRONTEND"] = "noninteractive"
        run(
            [
                "apt-get",
                "-o",
                "DPkg::Lock::Timeout=60",
                "install",
                "-y",
                "-qq",
                "syncthing",
            ]
        )
        if not is_package_installed("syncthing"):
            raise RuntimeError("Syncthing package installation did not verify")

    share_root = _share_root(config)
    uid, gid, group_name = _account(config.username)
    _prepare_state_home(config.username, uid, gid)
    _preflight_existing_folders(config.username, share_root)
    _prepare_share_root(config, group_name)

    with _syncthing_transaction(config.username):
        _run_as_user(
            config.username,
            [
                "generate",
                f"--home={SYNCTHING_HOME}",
                "--skip-port-probing",
                "--no-default-folder",
                f"--gui-user={admin_username}",
                "--gui-password=-",
            ],
            input_data=admin_password + "\n",
        )

        write_text_atomic(
            SYNCTHING_SERVICE_FILE,
            _render_service(config, group_name),
            mode=0o644,
        )
        run(["systemd-analyze", "verify", SYNCTHING_SERVICE_FILE])
        run(["systemctl", "daemon-reload"])
        run(["systemctl", "enable", "--now", f"{SYNCTHING_SERVICE_NAME}.service"])
        _wait_for_api(config.username)
        device_id_result = _run_as_user(
            config.username,
            ["serve", "--device-id", f"--home={SYNCTHING_HOME}"],
            capture_output=True,
        )
        local_device_id = str(device_id_result.stdout or "").strip()
        if not _DEVICE_ID_PATTERN.fullmatch(local_device_id):
            raise RuntimeError("Syncthing returned an invalid local device ID")
        desired = build_syncthing_policy_config(
            _load_current_config(config.username), share_root
        )
        _put_config(desired)
        run(["systemctl", "restart", f"{SYNCTHING_SERVICE_NAME}.service"])
        _wait_for_api(config.username)
        active = inspect_unit_state(f"{SYNCTHING_SERVICE_NAME}.service")
        if active["ActiveState"] != "active" or active["UnitFileState"] != "enabled":
            raise RuntimeError("Managed Syncthing service is not active and enabled")

    https_url = _configure_syncthing_https(config)
    print(f"  ✓ Syncthing device ID: {local_device_id}")
    if https_url:
        print(f"  ✓ Syncthing HTTPS admin: {https_url}")
    print(f"  ✓ Syncthing GUI folders are confined to {share_root}")
    print("  ℹ Device and folder membership is managed in the Syncthing web GUI")
    print("  ℹ No router mapping was added; relay fallback remains enabled")
