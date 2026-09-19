#!/usr/bin/env python3
"""Privileged target-side implementation for ``basaltw user rename``.

The controller stages a manifest and starts this module from a root-owned
systemd unit.  It is intentionally not a normal local CLI workflow: changing
the login name requires ending the user's SSH session and all of its other
processes first.
"""

from __future__ import annotations

import argparse
import fcntl
import grp
import json
import os
import pwd
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
from typing import Any, Iterable, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.atomic_io import write_json_atomic, write_text_atomic
from lib.machine_state import STATE_FILE, SETUP_CONFIG_FILE
from lib.validation import validate_filesystem_path
from lib.validators import validate_username


RENAME_ROOT = "/var/lib/basaltwater/user-renames"
SYSTEMD_DIR = "/etc/systemd/system"
LINGER_DIR = "/var/lib/systemd/linger"
CRON_DIR = "/var/spool/cron/crontabs"
MAIL_DIRS = ("/var/mail", "/var/spool/mail")
SMB_CREDENTIAL_DIR = "/root/.smb"
STATUS_MODE = 0o644
ROOT_MODE = 0o700
_OPERATION_ID_RE = re.compile(r"^[0-9a-f]{16,64}$")

MANAGED_UNIT_NAMES = {
    "auto-update-node",
    "auto-update-uv",
    "codex-auth-maintenance",
    "user-cache-maintenance",
    "basaltwater-t3code",
    "basaltwater-t3code-connect",
    "basaltwater-device-pairing",
    "basaltwater-syncthing",
}

MANAGED_UNIT_PREFIXES = (
    "auto-update-node",
    "auto-update-uv",
    "user-cache-maintenance",
    "basaltwater-t3code",
    "basaltwater-device-pairing",
)

class RenameError(RuntimeError):
    """Raised when a target rename cannot safely proceed."""


def _acquire_target_lock() -> Any:
    """Serialize rename jobs even when controllers are on different hosts."""

    try:
        os.makedirs(RENAME_ROOT, mode=ROOT_MODE, exist_ok=True)
        os.chmod(RENAME_ROOT, ROOT_MODE)
        lock_file = open(os.path.join(RENAME_ROOT, ".lock"), "a+", encoding="utf-8")
    except OSError as exc:
        raise RenameError(f"Could not create target rename lock: {exc}") from exc
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError) as exc:
        lock_file.close()
        if isinstance(exc, BlockingIOError) or getattr(exc, "errno", None) in {11, 35}:
            raise RenameError("Another target user rename is already running") from exc
        raise RenameError(f"Could not acquire target rename lock: {exc}") from exc
    return lock_file


def _release_target_lock(lock_file: Any) -> None:
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    finally:
        lock_file.close()


def _require_root() -> None:
    if os.geteuid() != 0:
        raise RenameError("target user rename must run as root")


def _validate_operation_id(operation_id: str) -> None:
    if not _OPERATION_ID_RE.fullmatch(operation_id):
        raise RenameError(f"Invalid operation ID: {operation_id!r}")


def _load_manifest(path: str) -> dict[str, Any]:
    if not os.path.isabs(path) or os.path.islink(path):
        raise RenameError("Rename manifest must be an absolute regular file")
    operation_directory = os.path.dirname(path)
    if os.path.islink(operation_directory):
        raise RenameError("Rename operation directory must not be a symlink")
    try:
        metadata = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise RenameError(f"Could not stat rename manifest: {exc}") from exc
    if metadata.st_uid != 0 or metadata.st_mode & 0o022:
        raise RenameError("Rename manifest must be owned by root and not group/world writable")
    try:
        with open(path, encoding="utf-8") as file_obj:
            manifest = json.load(file_obj)
    except (OSError, json.JSONDecodeError) as exc:
        raise RenameError(f"Could not read rename manifest: {exc}") from exc
    if not isinstance(manifest, dict):
        raise RenameError("Rename manifest must be a JSON object")
    required = ("operation_id", "old_username", "new_username")
    missing = [key for key in required if key not in manifest]
    if missing:
        raise RenameError(f"Rename manifest missing: {', '.join(missing)}")
    if not isinstance(manifest["operation_id"], str):
        raise RenameError("Invalid operation ID")
    _validate_operation_id(manifest["operation_id"])
    expected_directory = os.path.join(os.path.abspath(RENAME_ROOT), manifest["operation_id"])
    if os.path.abspath(os.path.dirname(path)) != expected_directory:
        raise RenameError("Rename manifest is not in its operation directory")
    for key in ("old_username", "new_username"):
        value = manifest[key]
        if not isinstance(value, str) or not validate_username(value):
            raise RenameError(f"Invalid {key.replace('_', ' ')}")
    if manifest["old_username"] == manifest["new_username"]:
        raise RenameError("Old and new usernames must differ")
    return manifest


def _operation_dir(operation_id: str) -> str:
    _validate_operation_id(operation_id)
    return os.path.join(RENAME_ROOT, operation_id)


def _status_path(operation_id: str) -> str:
    return os.path.join(_operation_dir(operation_id), "status.json")


def _marker_path(operation_id: str) -> str:
    return os.path.join(_operation_dir(operation_id), "operation.json")


def _unit_name(operation_id: str) -> str:
    return f"basaltwater-user-rename-{operation_id}"


def _unit_path(operation_id: str) -> str:
    return os.path.join(SYSTEMD_DIR, f"{_unit_name(operation_id)}.service")


def _write_status(
    operation_id: str,
    phase: str,
    status: str,
    *,
    error: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
) -> None:
    directory = _operation_dir(operation_id)
    if os.path.islink(directory):
        raise RenameError("Rename operation directory must not be a symlink")
    os.makedirs(directory, mode=ROOT_MODE, exist_ok=True)
    try:
        os.chmod(directory, ROOT_MODE)
    except OSError:
        pass
    payload: dict[str, Any] = {
        "operation_id": operation_id,
        "phase": phase,
        "status": status,
        "updated_at": time.time(),
    }
    if error:
        payload["error"] = error[:1000]
    if details:
        payload["details"] = details
    write_json_atomic(_status_path(operation_id), payload, mode=STATUS_MODE, sort_keys=True)


def _write_marker(manifest: dict[str, Any], phase: str) -> None:
    operation_id = manifest["operation_id"]
    updated = dict(manifest)
    updated["phase"] = phase
    write_json_atomic(_marker_path(operation_id), updated, mode=0o600, sort_keys=True)


def _run(
    argv: list[str],
    *,
    check: bool = True,
    capture_output: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            argv,
            check=False,
            capture_output=capture_output,
            text=True,
            timeout=600,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RenameError(f"Command failed to run: {' '.join(argv)}: {exc}") from exc
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RenameError(
            f"Command failed ({result.returncode}): {' '.join(argv)}"
            + (f": {detail[:400]}" if detail else "")
        )
    return result


def _read_json(path: str) -> Optional[dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as file_obj:
            payload = json.load(file_obj)
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _account(username: str) -> pwd.struct_passwd:
    try:
        return pwd.getpwnam(username)
    except KeyError as exc:
        raise RenameError(f"Target account does not exist: {username}") from exc


def _is_local_account(username: str, account: pwd.struct_passwd) -> bool:
    try:
        with open("/etc/passwd", encoding="utf-8") as file_obj:
            return any(line.split(":", 1)[0] == username for line in file_obj)
    except OSError as exc:
        raise RenameError(f"Could not inspect /etc/passwd: {exc}") from exc


def _home_for_manifest(manifest: dict[str, Any], account: pwd.struct_passwd) -> tuple[str, str]:
    old_home = manifest.get("old_home") or account.pw_dir
    if not isinstance(old_home, str):
        raise RenameError("Old home must be an absolute path")
    try:
        validate_filesystem_path(old_home, must_exist=True)
    except ValueError as exc:
        raise RenameError(f"Invalid old home: {exc}") from exc
    if not old_home.startswith("/"):
        raise RenameError("Old home must be an absolute path")
    if old_home != account.pw_dir:
        raise RenameError(
            f"Manifest home {old_home!r} does not match account home {account.pw_dir!r}"
        )
    if os.path.islink(old_home):
        raise RenameError("Account home must not be a symlink")
    keep_home = bool(manifest.get("keep_home", False))
    requested_home = manifest.get("new_home")
    if requested_home is not None:
        if not isinstance(requested_home, str):
            raise RenameError("New home must be an absolute path")
        try:
            validate_filesystem_path(requested_home, must_exist=False)
        except ValueError as exc:
            raise RenameError(f"Invalid new home: {exc}") from exc
        if not requested_home.startswith("/"):
            raise RenameError("New home must be an absolute path")
        if os.path.normpath(requested_home) != requested_home:
            raise RenameError("New home must be normalized without '.' or '..' components")
        new_home = requested_home
    elif keep_home:
        new_home = old_home
    else:
        new_home = os.path.join(os.path.dirname(old_home), manifest["new_username"])
    return old_home, new_home


def _unit_kind(path: str) -> str:
    return os.path.basename(path).rsplit(".", 1)[-1]


def _unit_base(path: str) -> str:
    return os.path.basename(path).rsplit(".", 1)[0]


def _is_managed_unit(path: str, content: str, old_username: str, old_home: str) -> bool:
    base = _unit_base(path)
    if base in MANAGED_UNIT_NAMES or base.startswith(MANAGED_UNIT_PREFIXES):
        return True
    if _unit_kind(path) == "mount" and (
        f"uid={old_username}" in content or f"gid={old_username}" in content
    ) and "credentials=/root/.smb/credentials-" in content:
        return True
    return False


def _managed_unit_files(old_username: str, old_home: str) -> tuple[list[str], list[str]]:
    managed: list[str] = []
    unmanaged: list[str] = []
    try:
        names = os.listdir(SYSTEMD_DIR)
    except OSError:
        return managed, unmanaged
    managed_bases: set[str] = set()
    for name in names:
        if not name.endswith((".service", ".timer", ".path", ".mount")):
            continue
        path = os.path.join(SYSTEMD_DIR, name)
        try:
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as file_obj:
                content = file_obj.read()
        except (OSError, UnicodeDecodeError):
            continue
        if old_username not in content and old_home not in content:
            continue
        if _is_managed_unit(path, content, old_username, old_home):
            managed.append(path)
            managed_bases.add(_unit_base(path))
        else:
            unmanaged.append(path)

    # Timer and path companions normally contain no username themselves, but
    # they must be stopped with a user-scoped service during the identity
    # cutover. Include only companions of a positively identified managed
    # unit, preserving the allowlist boundary above.
    managed_paths = set(managed)
    for name in names:
        path = os.path.join(SYSTEMD_DIR, name)
        if (
            _unit_base(path) not in managed_bases
            or _unit_kind(path) not in {"timer", "path"}
            or path in managed_paths
            or os.path.islink(path)
            or not os.path.isfile(path)
        ):
            continue
        managed.append(path)
        managed_paths.add(path)
    return sorted(managed), sorted(unmanaged)


def _unmanaged_sudoers_references(old_username: str) -> list[str]:
    """Find sudoers files that mention the account outside our managed entry."""

    paths: list[str] = []
    if os.path.isfile("/etc/sudoers"):
        paths.append("/etc/sudoers")
    try:
        paths.extend(
            os.path.join("/etc/sudoers.d", name)
            for name in os.listdir("/etc/sudoers.d")
            if name != f"basaltwater-{old_username}"
        )
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise RenameError(f"Could not inspect sudoers.d: {exc}") from exc
    pattern = re.compile(rf"(?<![A-Za-z0-9_.-]){re.escape(old_username)}(?![A-Za-z0-9_.-])")
    references: list[str] = []
    for path in paths:
        if os.path.islink(path) or not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as file_obj:
                content = file_obj.read()
        except (OSError, UnicodeDecodeError) as exc:
            raise RenameError(f"Could not inspect sudoers file {path}: {exc}") from exc
        if pattern.search(content):
            references.append(path)
    return sorted(references)


def _unmanaged_ssh_references(old_username: str) -> list[str]:
    """Find SSH daemon configuration that names the old login explicitly."""

    paths: list[str] = []
    if os.path.isfile("/etc/ssh/sshd_config"):
        paths.append("/etc/ssh/sshd_config")
    try:
        paths.extend(
            os.path.join("/etc/ssh/sshd_config.d", name)
            for name in os.listdir("/etc/ssh/sshd_config.d")
        )
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise RenameError(f"Could not inspect sshd_config.d: {exc}") from exc
    pattern = re.compile(rf"(?<![A-Za-z0-9_.-]){re.escape(old_username)}(?![A-Za-z0-9_.-])")
    references: list[str] = []
    for path in paths:
        if os.path.islink(path) or not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as file_obj:
                content = file_obj.read()
        except (OSError, UnicodeDecodeError) as exc:
            raise RenameError(f"Could not inspect SSH configuration {path}: {exc}") from exc
        if pattern.search(content):
            references.append(path)
    return sorted(references)


def _reject_concurrent_operation(operation_id: str) -> None:
    """Refuse a second unfinished rename on the same target."""

    try:
        operation_ids = os.listdir(RENAME_ROOT)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise RenameError(f"Could not inspect rename operations: {exc}") from exc
    for candidate in operation_ids:
        if candidate == operation_id or not _OPERATION_ID_RE.fullmatch(candidate):
            continue
        status = _read_json(os.path.join(RENAME_ROOT, candidate, "status.json"))
        if status and status.get("status") in {"in_progress", "failed"}:
            raise RenameError(
                f"Another user rename operation is unfinished: {candidate}"
            )


def _preflight(manifest: dict[str, Any]) -> dict[str, Any]:
    _require_root()
    old_username = manifest["old_username"]
    new_username = manifest["new_username"]
    _reject_concurrent_operation(manifest["operation_id"])
    old_account = _account(old_username)
    if old_account.pw_uid == 0:
        raise RenameError("Renaming root is not supported")
    if not _is_local_account(old_username, old_account):
        raise RenameError("Only local /etc/passwd accounts can be renamed")
    try:
        pwd.getpwnam(new_username)
    except KeyError:
        pass
    else:
        raise RenameError(f"Destination account already exists: {new_username}")
    try:
        grp.getgrnam(new_username)
    except KeyError:
        pass
    else:
        raise RenameError(f"Destination group already exists: {new_username}")

    old_home, new_home = _home_for_manifest(manifest, old_account)
    if old_home == "/":
        raise RenameError("Renaming an account whose home is / is not supported")
    if old_home != new_home and (
        new_home.startswith(old_home + "/") or old_home.startswith(new_home + "/")
    ):
        raise RenameError("Destination home cannot be nested within the existing home")
    if old_home != new_home and os.path.lexists(new_home):
        raise RenameError(f"Destination home already exists: {new_home}")
    if old_home != new_home and not os.path.isdir(os.path.dirname(new_home)):
        raise RenameError(f"Destination home parent is not a directory: {os.path.dirname(new_home)}")
    if not os.path.isdir(old_home):
        raise RenameError(f"Account home is not a directory: {old_home}")
    if os.stat(old_home, follow_symlinks=False).st_uid != old_account.pw_uid:
        raise RenameError("Account home is not owned by the requested account")
    if os.path.ismount(old_home):
        raise RenameError("Moving a mounted home directory is not supported")
    if not os.path.exists("/run/systemd/system") or shutil.which("systemctl") is None:
        raise RenameError("Target user rename requires a running systemd target")

    try:
        primary_group = grp.getgrgid(old_account.pw_gid).gr_name
    except KeyError:
        primary_group = ""
    managed_units, unmanaged_units = _managed_unit_files(old_username, old_home)
    if unmanaged_units:
        raise RenameError(
            "Unmanaged systemd references to the old account: "
            + ", ".join(unmanaged_units)
        )

    state = _read_json(STATE_FILE)
    setup = _read_json(SETUP_CONFIG_FILE)
    setup_operation_path = os.path.join(os.path.dirname(STATE_FILE), "setup-operation.json")
    setup_operation = _read_json(setup_operation_path)
    if os.path.exists(setup_operation_path) and setup_operation is None:
        raise RenameError("Target setup operation marker is invalid; recover it first")
    if setup_operation and setup_operation.get("status") in {"in_progress", "recovery_required"}:
        raise RenameError("Target setup has an unfinished operation; recover it first")
    if os.path.exists(STATE_FILE) and state is None:
        raise RenameError("machine.json is not a valid JSON object")
    if os.path.exists(SETUP_CONFIG_FILE) and setup is None:
        raise RenameError("setup.json is not a valid JSON object")
    if state is not None and state.get("username") != old_username:
        raise RenameError("machine.json username does not match the requested account")
    if setup is not None and setup.get("username") != old_username:
        raise RenameError("setup.json username does not match the requested account")
    existing_group_memberships = _group_member_references(new_username)
    if existing_group_memberships:
        raise RenameError(
            "Destination username already appears in group databases: "
            + ", ".join(existing_group_memberships)
        )
    old_sudoers = os.path.join("/etc/sudoers.d", f"basaltwater-{old_username}")
    new_sudoers = os.path.join("/etc/sudoers.d", f"basaltwater-{new_username}")
    if os.path.islink(old_sudoers) or os.path.islink(new_sudoers):
        raise RenameError("basaltwater sudoers entries must be regular files")
    if os.path.isfile(old_sudoers) and os.path.lexists(new_sudoers):
        raise RenameError(f"Destination sudoers entry already exists: {new_sudoers}")
    unmanaged_sudoers = _unmanaged_sudoers_references(old_username)
    if unmanaged_sudoers:
        raise RenameError(
            "Unmanaged sudoers references to the old account: "
            + ", ".join(unmanaged_sudoers)
        )
    unmanaged_ssh = _unmanaged_ssh_references(old_username)
    if unmanaged_ssh:
        raise RenameError(
            "Unmanaged SSH configuration references to the old account: "
            + ", ".join(unmanaged_ssh)
        )
    for subordinate_path in ("/etc/subuid", "/etc/subgid"):
        if os.path.islink(subordinate_path):
            raise RenameError(f"Unsafe subordinate ID file: {subordinate_path}")
        try:
            with open(subordinate_path, encoding="utf-8") as file_obj:
                if any(line.startswith(new_username + ":") for line in file_obj):
                    raise RenameError(
                        f"Destination subordinate ID entry already exists: {subordinate_path}"
                    )
        except FileNotFoundError:
            continue

    unit_state: list[dict[str, Any]] = []
    for path in managed_units:
        base = os.path.basename(path)
        kind = _unit_kind(path)
        enabled = _run(["systemctl", "is-enabled", base], check=False).returncode == 0
        active = _run(["systemctl", "is-active", base], check=False).returncode == 0
        unit_state.append({"path": path, "unit": base, "kind": kind, "enabled": enabled, "active": active})

    linger_path = os.path.join(LINGER_DIR, old_username)
    if os.path.islink(linger_path):
        raise RenameError("User linger marker must be a regular file")
    if os.path.lexists(os.path.join(LINGER_DIR, new_username)):
        raise RenameError("Destination user already has a linger marker")
    _preflight_cron_and_mail(old_username, new_username)
    _validate_managed_credentials(
        {
            "old_username": old_username,
            "new_username": new_username,
            "managed_units": unit_state,
        },
        allow_completed=False,
    )

    return {
        "old_uid": old_account.pw_uid,
        "old_gid": old_account.pw_gid,
        "old_shell": old_account.pw_shell,
        "old_home": old_home,
        "new_home": new_home,
        "primary_group": primary_group,
        "rename_primary_group": primary_group == old_username,
        "managed_units": unit_state,
        "machine_state_present": state is not None,
        "setup_config_present": setup is not None,
        "linger_enabled": os.path.isfile(linger_path),
    }


def _processes_for_uid(uid: int) -> list[int]:
    result: list[int] = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return result
    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            with open(os.path.join("/proc", entry, "status"), encoding="utf-8") as file_obj:
                uid_line = next((line for line in file_obj if line.startswith("Uid:")), "")
            real_uid = int(uid_line.split()[1]) if uid_line else -1
        except (OSError, ValueError, IndexError):
            continue
        if real_uid == uid:
            result.append(pid)
    return result


def _stop_managed_units(unit_state: Iterable[dict[str, Any]]) -> None:
    seen: set[str] = set()
    for item in unit_state:
        unit = item["unit"]
        if unit in seen:
            continue
        seen.add(unit)
        _run(["systemctl", "stop", unit])


def _restore_managed_units(unit_state: Iterable[dict[str, Any]]) -> None:
    for item in unit_state:
        unit = item["unit"]
        if item.get("enabled"):
            _run(["systemctl", "enable", unit])
        if item.get("active"):
            _run(["systemctl", "start", unit])


def _restore_t3_user_service(username: str, uid: int, home: str) -> None:
    """Restart an upstream T3 user service after its user manager was stopped."""

    service = os.path.join(home, ".config", "systemd", "user", "t3code.service")
    if not os.path.lexists(service):
        return
    if os.path.islink(service) or not os.path.isfile(service):
        raise RenameError(f"T3 Code user service is not a regular file: {service}")
    environment = [
        "env",
        f"XDG_RUNTIME_DIR=/run/user/{uid}",
        f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus",
    ]
    _run(["systemctl", "start", f"user@{uid}.service"])
    prefix = ["runuser", "-u", username, "--", *environment, "systemctl", "--user"]
    _run([*prefix, "daemon-reload"])
    _run([*prefix, "enable", "t3code.service"])
    _run([*prefix, "restart", "t3code.service"])
    if _run([*prefix, "is-active", "--quiet", "t3code.service"], check=False).returncode:
        raise RenameError("T3 Code user service did not restart after the rename")


def _terminate_user(account: pwd.struct_passwd) -> None:
    _run(["usermod", "-s", "/usr/sbin/nologin", account.pw_name])
    _run(["loginctl", "terminate-user", account.pw_name], check=False)
    _run(["systemctl", "stop", f"user@{account.pw_uid}.service"], check=False)
    deadline = time.monotonic() + 30
    while True:
        processes = [pid for pid in _processes_for_uid(account.pw_uid) if pid != os.getpid()]
        if not processes:
            return
        for pid in processes:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        if time.monotonic() >= deadline:
            for pid in _processes_for_uid(account.pw_uid):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            time.sleep(0.5)
            remaining = [pid for pid in _processes_for_uid(account.pw_uid) if pid != os.getpid()]
            if remaining:
                raise RenameError(f"Processes remain for UID {account.pw_uid}: {remaining}")
            return
        time.sleep(0.5)


def _rewrite_value(value: Any, old_home: str, new_home: str) -> Any:
    if isinstance(value, str):
        if old_home and (value == old_home or value.startswith(old_home + "/")):
            return new_home + value[len(old_home):]
        return value
    if isinstance(value, list):
        return [_rewrite_value(item, old_home, new_home) for item in value]
    if isinstance(value, dict):
        return {key: _rewrite_value(item, old_home, new_home) for key, item in value.items()}
    return value


def _rewrite_setup_path_values(config: dict[str, Any], old_home: str, new_home: str) -> dict[str, Any]:
    """Rewrite only the setup schema fields that contain filesystem paths."""

    updated = dict(config)
    if "agent_workspace" in updated:
        updated["agent_workspace"] = _rewrite_value(
            updated["agent_workspace"], old_home, new_home
        )
    gogs = updated.get("gogs")
    if isinstance(gogs, list) and len(gogs) > 1:
        gogs = list(gogs)
        gogs[1] = _rewrite_value(gogs[1], old_home, new_home)
        updated["gogs"] = gogs
    path_indexes = {
        "samba_shares": (2,),
        "smb_mounts": (0,),
        "sync_specs": (0, 1),
        "backup_specs": (0, 1),
        "scrub_specs": (0, 1),
        "deploy_specs": (0,),
        "storage_mounts": (1,),
    }
    for field, indexes in path_indexes.items():
        records = updated.get(field)
        if not isinstance(records, list):
            continue
        rewritten_records = []
        for record in records:
            if not isinstance(record, list):
                rewritten_records.append(record)
                continue
            rewritten = list(record)
            for index in indexes:
                if index < len(rewritten):
                    rewritten[index] = _rewrite_value(rewritten[index], old_home, new_home)
            rewritten_records.append(rewritten)
        updated[field] = rewritten_records
    return updated


def _rewrite_setup_config(config: dict[str, Any], old_username: str, new_username: str, old_home: str, new_home: str) -> dict[str, Any]:
    updated = _rewrite_setup_path_values(config, old_home, new_home)
    if not isinstance(updated, dict):
        raise RenameError("Saved setup configuration is not an object")
    if updated.get("username") == old_username:
        updated["username"] = new_username
    return updated


def _rewrite_state_files(manifest: dict[str, Any], old_home: str, new_home: str) -> None:
    old_username = manifest["old_username"]
    new_username = manifest["new_username"]
    state = _read_json(STATE_FILE)
    if state is not None:
        state["username"] = new_username
        write_json_atomic(STATE_FILE, state, mode=0o600, sort_keys=True)
    setup = _read_json(SETUP_CONFIG_FILE)
    if setup is not None:
        updated = _rewrite_setup_config(setup, old_username, new_username, old_home, new_home)
        updated.pop("password", None)
        write_json_atomic(SETUP_CONFIG_FILE, updated, mode=0o600, sort_keys=True)


def _rewrite_group_memberships(old_username: str, new_username: str) -> None:
    """Preserve supplementary group membership under the new login name."""

    for path, member_fields in (("/etc/group", (3,)), ("/etc/gshadow", (2, 3))):
        if os.path.islink(path):
            raise RenameError(f"Group database must not be a symlink: {path}")
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as file_obj:
                lines = file_obj.readlines()
        except OSError as exc:
            raise RenameError(f"Could not read group database {path}: {exc}") from exc
        updated_lines: list[str] = []
        changed = False
        for line in lines:
            newline = "\n" if line.endswith("\n") else ""
            fields = line[:-1].split(":") if newline else line.split(":")
            for index in member_fields:
                if index >= len(fields):
                    continue
                members = fields[index].split(",") if fields[index] else []
                replaced = [new_username if member == old_username else member for member in members]
                if replaced != members:
                    fields[index] = ",".join(replaced)
                    changed = True
            updated_lines.append(":".join(fields) + newline)
        if changed:
            metadata = os.stat(path, follow_symlinks=False)
            mode = stat.S_IMODE(metadata.st_mode)
            write_text_atomic(path, "".join(updated_lines), mode=mode)
            try:
                os.chown(path, metadata.st_uid, metadata.st_gid)
            except OSError as exc:
                raise RenameError(f"Could not preserve group database ownership {path}: {exc}") from exc


def _verify_group_memberships(old_username: str) -> None:
    references = _group_member_references(old_username)
    if references:
        raise RenameError(
            "Group databases still reference the old account: " + ", ".join(references)
        )


def _group_member_references(username: str) -> list[str]:
    references: list[str] = []
    for path, member_fields in (("/etc/group", (3,)), ("/etc/gshadow", (2, 3))):
        if os.path.islink(path):
            raise RenameError(f"Group database must not be a symlink: {path}")
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as file_obj:
                lines = file_obj.readlines()
        except OSError as exc:
            raise RenameError(f"Could not verify group database {path}: {exc}") from exc
        for line in lines:
            fields = line.rstrip("\n").split(":")
            for index in member_fields:
                if index < len(fields) and username in fields[index].split(","):
                    references.append(path)
                    break
    return references


def _rewrite_subordinate_id_files(old_username: str, new_username: str) -> None:
    """Move rootless-container subordinate ID ownership to the new name."""

    for path in ("/etc/subuid", "/etc/subgid"):
        try:
            with open(path, encoding="utf-8") as file_obj:
                lines = file_obj.readlines()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise RenameError(f"Could not read subordinate ID file {path}: {exc}") from exc
        updated = [
            f"{new_username}{line[len(old_username):]}"
            if line.startswith(old_username + ":")
            else line
            for line in lines
        ]
        if updated != lines:
            if not _replace_in_file(path, [(old_username + ":", new_username + ":")]):
                raise RenameError(f"Could not rewrite subordinate ID file: {path}")


def _verify_subordinate_id_files(old_username: str) -> None:
    for path in ("/etc/subuid", "/etc/subgid"):
        try:
            with open(path, encoding="utf-8") as file_obj:
                if any(line.startswith(old_username + ":") for line in file_obj):
                    raise RenameError(f"{path} still references the old account")
        except FileNotFoundError:
            continue


def _replace_in_file(path: str, replacements: list[tuple[str, str]]) -> bool:
    try:
        if os.path.islink(path) or not os.path.isfile(path):
            return False
        with open(path, "rb") as file_obj:
            raw = file_obj.read()
        if b"\0" in raw:
            return False
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    replacements_by_old = {old: new for old, new in replacements if old}
    if not replacements_by_old:
        return False
    pattern = re.compile(
        "|".join(re.escape(old) for old in sorted(replacements_by_old, key=len, reverse=True))
    )
    updated = pattern.sub(lambda match: replacements_by_old[match.group(0)], text)
    if updated == text:
        return False
    metadata = os.stat(path, follow_symlinks=False)
    mode = stat.S_IMODE(metadata.st_mode)
    write_text_atomic(path, updated, mode=mode)
    try:
        os.chown(path, metadata.st_uid, metadata.st_gid)
    except OSError:
        pass
    return True


def _rewrite_managed_units(manifest: dict[str, Any], old_home: str, new_home: str) -> list[str]:
    # Match the destination home as a whole before the username replacement so
    # a resumed migration does not rewrite ``olduser`` inside ``/srv/olduser-data``.
    replacements = [
        (old_home, new_home),
        (new_home, new_home),
        (manifest["old_username"], manifest["new_username"]),
    ]
    changed: list[str] = []
    for item in manifest.get("managed_units", []):
        path = item.get("path")
        if isinstance(path, str) and _replace_in_file(path, replacements):
            changed.append(path)
    return changed


def _managed_credential_paths(manifest: dict[str, Any]) -> list[tuple[str, str]]:
    """Return SMB credential renames referenced by managed mount units."""

    old_username = manifest["old_username"]
    new_username = manifest["new_username"]
    paths: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in manifest.get("managed_units", []):
        path = item.get("path")
        if not isinstance(path, str):
            continue
        try:
            with open(path, encoding="utf-8") as file_obj:
                content = file_obj.read()
        except (OSError, UnicodeDecodeError) as exc:
            raise RenameError(
                f"Could not inspect managed unit credentials in {path}: {exc}"
            ) from exc
        credential_pattern = rf"credentials={re.escape(SMB_CREDENTIAL_DIR)}/([^\s,]+)"
        for match in re.finditer(credential_pattern, content):
            name = match.group(1)
            if old_username not in name:
                continue
            if "/" in name or name in {".", ".."}:
                raise RenameError("Unsafe SMB credential path in managed mount unit")
            old_path = os.path.join(SMB_CREDENTIAL_DIR, name)
            new_path = os.path.join(
                SMB_CREDENTIAL_DIR,
                name.replace(old_username, new_username),
            )
            rename = (old_path, new_path)
            if rename not in seen:
                seen.add(rename)
                paths.append(rename)
    return paths


def _validate_managed_credentials(
    manifest: dict[str, Any],
    *,
    allow_completed: bool,
) -> list[tuple[str, str]]:
    """Validate SMB credential renames before or during an idempotent run."""

    paths = _managed_credential_paths(manifest)
    for old_path, new_path in paths:
        if os.path.islink(old_path) or os.path.islink(new_path):
            raise RenameError("SMB credential references must be regular files")
        old_exists = os.path.lexists(old_path)
        new_exists = os.path.lexists(new_path)
        if old_exists and not os.path.isfile(old_path):
            raise RenameError(
                f"Referenced SMB credentials are not a regular file: {old_path}"
            )
        if new_exists and not os.path.isfile(new_path):
            raise RenameError(
                f"Destination SMB credentials are not a regular file: {new_path}"
            )
        if old_exists and new_exists:
            raise RenameError(f"Destination SMB credentials already exist: {new_path}")
        if not old_exists and not (allow_completed and new_exists):
            raise RenameError(f"Referenced SMB credentials are missing: {old_path}")
        if new_exists and not allow_completed:
            raise RenameError(f"Destination SMB credentials already exist: {new_path}")
    return paths


def _rename_managed_credentials(manifest: dict[str, Any]) -> None:
    """Rename SMB credential files referenced by managed mount units."""

    for old_path, new_path in _validate_managed_credentials(
        manifest,
        allow_completed=True,
    ):
        if os.path.isfile(old_path):
            os.replace(old_path, new_path)


def _rewrite_managed_home_files(old_home: str, new_home: str) -> list[str]:
    changed: list[str] = []
    for relative in (".local/bin", ".local/share/basaltwater", ".local/share/t3code"):
        root = os.path.join(new_home, relative)
        if not os.path.isdir(root):
            continue
        for directory, _, filenames in os.walk(root):
            for filename in filenames:
                path = os.path.join(directory, filename)
                if _replace_in_file(path, [(old_home, new_home)]):
                    changed.append(path)
    for path in (
        os.path.join(new_home, ".config", "systemd", "user", "t3code.service"),
        os.path.join(
            new_home,
            ".config",
            "systemd",
            "user",
            "t3code.service.d",
            "basaltwater.conf",
        ),
    ):
        if _replace_in_file(path, [(old_home, new_home)]):
            changed.append(path)
    return changed


def _verify_managed_rewrites(manifest: dict[str, Any], old_home: str) -> None:
    """Ensure managed unit files no longer contain stale identity references."""

    old_username = manifest["old_username"]
    for item in manifest.get("managed_units", []):
        path = item.get("path")
        if not isinstance(path, str):
            continue
        try:
            with open(path, encoding="utf-8") as file_obj:
                content = file_obj.read()
        except (OSError, UnicodeDecodeError) as exc:
            raise RenameError(f"Could not verify managed unit {path}: {exc}") from exc
        remaining = content.replace(manifest["new_home"], "")
        if old_home in remaining or old_username in remaining:
            raise RenameError(f"Managed unit still references the old account: {path}")
    new_home = manifest["new_home"]
    for path in (
        os.path.join(new_home, ".config", "systemd", "user", "t3code.service"),
        os.path.join(
            new_home,
            ".config",
            "systemd",
            "user",
            "t3code.service.d",
            "basaltwater.conf",
        ),
    ):
        if not os.path.isfile(path) or os.path.islink(path):
            continue
        with open(path, encoding="utf-8") as file_obj:
            content = file_obj.read()
        if old_home != new_home and old_home in content:
            raise RenameError(f"T3 Code user service still references {old_home}: {path}")


def _rewrite_sudoers(old_username: str, new_username: str) -> list[str]:
    changed: list[str] = []
    old_path = os.path.join("/etc/sudoers.d", f"basaltwater-{old_username}")
    new_path = os.path.join("/etc/sudoers.d", f"basaltwater-{new_username}")
    if os.path.isfile(old_path) and not os.path.lexists(new_path):
        os.replace(old_path, new_path)
        changed.append(new_path)
    if os.path.isfile(new_path):
        _replace_in_file(new_path, [(old_username, new_username)])
        result = _run(["visudo", "-cf", new_path], check=False)
        if result.returncode != 0:
            raise RenameError(f"Generated sudoers file failed validation: {new_path}")
    return changed


def _cron_and_mail_paths(
    old_username: str,
    new_username: str,
) -> list[tuple[str, str, str]]:
    paths = [
        (
            "crontab",
            os.path.join(CRON_DIR, old_username),
            os.path.join(CRON_DIR, new_username),
        )
    ]
    seen_directories: set[str] = set()
    for directory in MAIL_DIRS:
        canonical_directory = os.path.realpath(directory)
        if canonical_directory in seen_directories:
            continue
        seen_directories.add(canonical_directory)
        paths.append(
            (
                "mailbox",
                os.path.join(directory, old_username),
                os.path.join(directory, new_username),
            )
        )
    return paths


def _validate_cron_and_mail_paths(
    old_username: str,
    new_username: str,
    *,
    allow_completed: bool,
) -> list[tuple[str, str, str]]:
    paths = _cron_and_mail_paths(old_username, new_username)
    for label, old_path, new_path in paths:
        if os.path.islink(old_path) or os.path.islink(new_path):
            raise RenameError(f"{label.capitalize()} entries must be regular files")
        old_exists = os.path.lexists(old_path)
        new_exists = os.path.lexists(new_path)
        if old_exists and not os.path.isfile(old_path):
            raise RenameError(f"Existing {label} is not a regular file: {old_path}")
        if new_exists and not os.path.isfile(new_path):
            raise RenameError(f"Destination {label} is not a regular file: {new_path}")
        if old_exists and new_exists:
            raise RenameError(f"Destination {label} already exists: {new_path}")
        if new_exists and not allow_completed:
            raise RenameError(f"Destination {label} already exists: {new_path}")
    return paths


def _preflight_cron_and_mail(old_username: str, new_username: str) -> None:
    _validate_cron_and_mail_paths(
        old_username,
        new_username,
        allow_completed=False,
    )


def _rename_cron_and_mail(old_username: str, new_username: str, uid: int) -> None:
    for label, old_path, new_path in _validate_cron_and_mail_paths(
        old_username,
        new_username,
        allow_completed=True,
    ):
        if not os.path.isfile(old_path):
            continue
        os.replace(old_path, new_path)
        if label == "crontab":
            os.chown(new_path, uid, os.stat(new_path, follow_symlinks=False).st_gid)


def _rename_linger(old_username: str, new_username: str, enabled: bool) -> None:
    """Preserve systemd user-manager linger across the login rename."""

    if not enabled:
        return
    old_path = os.path.join(LINGER_DIR, old_username)
    new_path = os.path.join(LINGER_DIR, new_username)
    if os.path.islink(new_path):
        raise RenameError("Destination linger marker must be a regular file")
    if os.path.isfile(new_path) and not os.path.lexists(old_path):
        return
    if os.path.lexists(new_path):
        raise RenameError(f"Destination linger marker already exists: {new_path}")
    if not os.path.isfile(old_path):
        raise RenameError("Recorded linger marker disappeared before identity cutover")
    os.replace(old_path, new_path)


def _backup_files(operation_id: str) -> None:
    backup_dir = os.path.join(_operation_dir(operation_id), "backup")
    os.makedirs(backup_dir, mode=ROOT_MODE, exist_ok=True)
    for source in ("/etc/passwd", "/etc/group", "/etc/shadow", "/etc/gshadow", "/etc/subuid", "/etc/subgid"):
        if os.path.isfile(source):
            shutil.copy2(source, os.path.join(backup_dir, os.path.basename(source)))
    for path in (STATE_FILE, SETUP_CONFIG_FILE):
        if os.path.isfile(path):
            shutil.copy2(path, os.path.join(backup_dir, os.path.basename(path)))


def _cleanup_operation(operation_id: str) -> None:
    unit = _unit_name(operation_id)
    _run(["systemctl", "disable", unit], check=False)
    try:
        os.unlink(_unit_path(operation_id))
    except FileNotFoundError:
        pass
    _run(["systemctl", "daemon-reload"], check=False)
    marker = _marker_path(operation_id)
    try:
        os.unlink(marker)
    except FileNotFoundError:
        pass
    backup_dir = os.path.join(_operation_dir(operation_id), "backup")
    if os.path.isdir(backup_dir):
        shutil.rmtree(backup_dir)
    manifest_path = os.path.join(_operation_dir(operation_id), "manifest.json")
    try:
        os.unlink(manifest_path)
    except FileNotFoundError:
        pass


def _run_migration(manifest: dict[str, Any]) -> int:
    _require_root()
    try:
        lock_file = _acquire_target_lock()
    except RenameError as exc:
        try:
            _write_status(manifest["operation_id"], "starting", "failed", error=str(exc))
        except OSError:
            pass
        return 1
    try:
        return _run_migration_locked(manifest)
    finally:
        _release_target_lock(lock_file)


def _restore_login_shell_after_failure(manifest: dict[str, Any]) -> None:
    """Best-effort restoration of whichever account name owns the old UID."""

    old_shell = manifest.get("old_shell")
    old_uid = manifest.get("old_uid")
    if not isinstance(old_shell, str) or not isinstance(old_uid, int):
        return
    for key in ("old_username", "new_username"):
        username = manifest.get(key)
        if not isinstance(username, str):
            continue
        try:
            account = _account(username)
        except RenameError:
            continue
        if account.pw_uid != old_uid:
            continue
        _run(["usermod", "-s", old_shell, username], check=False)
        return


def _run_migration_locked(manifest: dict[str, Any]) -> int:
    _require_root()
    operation_id = manifest["operation_id"]
    _write_status(operation_id, "starting", "in_progress")
    try:
        saved_manifest = _read_json(_marker_path(operation_id))
        if saved_manifest:
            manifest = {**manifest, **saved_manifest}
        phase = manifest.get("phase")
        if phase not in {"identity-renamed", "configuration-updated", "services-reconciled", "verified"}:
            try:
                _account(manifest["old_username"])
            except RenameError:
                try:
                    renamed_account = _account(manifest["new_username"])
                except RenameError:
                    raise
                if renamed_account.pw_uid != int(manifest.get("old_uid", -1)):
                    raise RenameError("Existing destination account does not match the recorded UID")
                phase = "identity-renamed"
                manifest["phase"] = phase
                _write_marker(manifest, phase)
        if phase in {"identity-renamed", "configuration-updated", "services-reconciled", "verified"}:
            details = manifest
        else:
            details = _preflight(manifest)
            manifest.update(details)
        os.makedirs(_operation_dir(operation_id), mode=ROOT_MODE, exist_ok=True)
        if not saved_manifest:
            _backup_files(operation_id)
            _write_marker(manifest, "prepared")
        _write_status(operation_id, "prepared", "in_progress", details=details)

        old_username = manifest["old_username"]
        new_username = manifest["new_username"]
        old_home = manifest["old_home"]
        new_home = manifest["new_home"]
        old_account: Optional[pwd.struct_passwd] = None
        if phase not in {"identity-renamed", "configuration-updated", "services-reconciled", "verified"}:
            old_account = _account(old_username)

        if phase not in {"identity-renamed", "configuration-updated", "services-reconciled", "verified"}:
            _write_marker(manifest, "quiescing")
            _write_status(operation_id, "quiescing", "in_progress")
            _stop_managed_units(manifest.get("managed_units", []))
            if old_account is None:
                raise RenameError("Cannot quiesce without the original account")
            _terminate_user(old_account)

        if phase not in {"configuration-updated", "services-reconciled", "verified"}:
            if phase != "identity-renamed":
                _write_marker(manifest, "identity-starting")
                _run(["usermod", "-l", new_username, old_username])
                phase = "identity-renamed"
                _write_marker(manifest, phase)
                _write_status(operation_id, phase, "in_progress")
            if manifest.get("rename_primary_group"):
                try:
                    grp.getgrnam(old_username)
                except KeyError:
                    pass
                else:
                    _run(["groupmod", "-n", new_username, old_username])
            if old_home != new_home:
                current_account = _account(new_username)
                if current_account.pw_dir != new_home:
                    _run(["usermod", "-d", new_home, "-m", new_username])
            _run(["usermod", "-s", manifest["old_shell"], new_username])
            _rename_cron_and_mail(old_username, new_username, int(manifest["old_uid"]))
            _rename_linger(
                old_username,
                new_username,
                bool(manifest.get("linger_enabled", False)),
            )
            _rewrite_group_memberships(old_username, new_username)

        if phase not in {"configuration-updated", "services-reconciled", "verified"}:
            _write_status(operation_id, "configuration-starting", "in_progress")
            _rewrite_state_files(manifest, old_home, new_home)
            _rewrite_subordinate_id_files(old_username, new_username)
            _rewrite_sudoers(old_username, new_username)
            _rename_managed_credentials(manifest)
            _rewrite_managed_units(manifest, old_home, new_home)
            _rewrite_managed_home_files(old_home, new_home)
            _write_marker(manifest, "configuration-updated")
            _write_status(operation_id, "configuration-updated", "in_progress")

        if phase not in {"services-reconciled", "verified"}:
            _write_status(operation_id, "services-starting", "in_progress")
            _run(["systemctl", "daemon-reload"])
            _restore_managed_units(manifest.get("managed_units", []))
            _restore_t3_user_service(
                new_username,
                int(manifest["old_uid"]),
                new_home,
            )
            _write_marker(manifest, "services-reconciled")
            _write_status(operation_id, "services-reconciled", "in_progress")

        _write_status(operation_id, "verified", "in_progress")
        final_account = _account(new_username)
        if final_account.pw_uid != int(manifest["old_uid"]):
            raise RenameError("Renamed account UID changed unexpectedly")
        if final_account.pw_gid != int(manifest["old_gid"]):
            raise RenameError("Renamed account primary GID changed unexpectedly")
        if final_account.pw_dir != new_home:
            raise RenameError("Renamed account home does not match requested path")
        if os.path.exists(STATE_FILE):
            state = _read_json(STATE_FILE)
            if state is None or state.get("username") != new_username:
                raise RenameError("machine.json was not updated")
        if os.path.exists(SETUP_CONFIG_FILE):
            setup = _read_json(SETUP_CONFIG_FILE)
            if setup is None or setup.get("username") != new_username:
                raise RenameError("setup.json was not updated")
        try:
            _account(old_username)
        except RenameError:
            pass
        else:
            raise RenameError("Old account name still exists after rename")
        if not os.path.isdir(new_home):
            raise RenameError("Renamed account home is not a directory")
        if os.stat(new_home, follow_symlinks=False).st_uid != final_account.pw_uid:
            raise RenameError("Renamed account home is not owned by the account")
        _verify_managed_rewrites(manifest, old_home)
        _verify_group_memberships(old_username)
        _verify_subordinate_id_files(old_username)
        linger_path = os.path.join(LINGER_DIR, new_username)
        if bool(manifest.get("linger_enabled", False)) != os.path.isfile(linger_path):
            raise RenameError("User linger state was not preserved")
        _write_marker(manifest, "verified")

        completion_details = {
            "old_home": old_home,
            "old_username": old_username,
            "username": new_username,
            "home": new_home,
        }
        _write_status(
            operation_id,
            "complete",
            "success",
            details=completion_details,
        )
        try:
            _cleanup_operation(operation_id)
        except Exception as cleanup_error:
            _write_status(
                operation_id,
                "complete",
                "success",
                error=f"Cleanup incomplete: {cleanup_error}",
                details=completion_details,
            )
        return 0
    except Exception as exc:
        phase = "recovery_required"
        status = _read_json(_status_path(operation_id)) or {}
        if isinstance(status.get("phase"), str):
            phase = status["phase"]
        try:
            _restore_login_shell_after_failure(manifest)
        except Exception:
            pass
        _write_status(operation_id, phase, "failed", error=str(exc))
        return 1


def _json_preflight(manifest: dict[str, Any]) -> int:
    details = _preflight(manifest)
    payload = {"ok": True, "manifest": {**manifest, **details}}
    print(json.dumps(payload, sort_keys=True))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Internal target user migration helper")
    parser.add_argument("--manifest", required=True, help="Root-owned migration manifest")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--json", action="store_true", help="Emit preflight JSON")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        manifest = _load_manifest(args.manifest)
        if args.preflight:
            return _json_preflight(manifest)
        if args.run:
            return _run_migration(manifest)
        raise RenameError("Specify --preflight or --run")
    except Exception as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
