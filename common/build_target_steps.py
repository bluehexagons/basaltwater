"""Install explicit target deployment authority without target build tools."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import os
from pathlib import Path
import pwd
import grp
import re
import stat
from typing import Any

from common.build_host_steps import _public_ed25519_key
from common.service_tools import build_target as entrypoint
from lib.atomic_io import remove_file_durable, write_bytes_atomic
from lib.build_protocol import decode_document, encode_document
from lib.build_target_maintenance import read_protected, set_serving, target_lease, trusted_path
from lib.build_target_policy import MAX_POLICY_BYTES, parse_target_policy
from lib.maintenance_defaults import APT_LOCK_OPTIONS
from lib.remote_utils import is_dry_run, is_package_installed, run
from lib.setup_directories import directory_fd
from lib.validation import validate_filesystem_path, validate_package_name
from lib.validators import validate_username


ETC_DIR = "/etc/basaltwater/build-target"
MARKER_PATH = ETC_DIR + "/.managed.json"
CONFIG_PATH = ETC_DIR + "/config.json"
DATA_ROOT = "/var/lib/basaltwater/build-target"
SSH_ROOT = "/var/lib/basaltwater-build-target-ssh"
_LEGACY_SSH_ROOT = "/var/lib/basaltwater/build-target-ssh"
SOURCE_ROOT = "/opt/basaltwater"
ENTRYPOINT_PATH = SOURCE_ROOT + "/common/service_tools/build_target.py"
SUDOERS_DIR = "/etc/sudoers.d"
ROOTS = {name: DATA_ROOT + "/" + name for name in ("admission", "artifacts", "releases", "verify")}
MAX_ENROLLMENT_BYTES = MAX_POLICY_BYTES + 256 * 1024
_MARKER_LIMIT = 1024 * 1024
_MARKER_SCHEMA = 2
_LEGACY_LAYOUT_GUIDANCE = (
    "Legacy build-target transport layout requires operator reconciliation or a clean reinstall; "
    "preserve accounts, markers, the stable lock and retained data, revoke old authority under "
    "operator control, and do not widen /var/lib/basaltwater or move homes/edit markers to bypass this check"
)
_PREFIX = "bw-target-"
_SLUG = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?")
_HASH = re.compile(r"[0-9a-f]{64}")
_PACKAGES = ("openssh-server", "sudo", "nginx")


def _record(value: object, fields: set[str], label: str) -> dict:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError(label + " has missing or unsupported fields")
    return dict(value)


def _username(principal: str) -> str:
    username = _PREFIX + hashlib.sha256(principal.encode("ascii")).hexdigest()[:12]
    if not validate_username(username):
        raise ValueError("Invalid target transport username")
    return username


def _home(principal: str) -> str:
    return SSH_ROOT + "/" + _username(principal).removeprefix(_PREFIX)


def parse_build_target_enrollment(value: object) -> dict[str, Any]:
    """Return a strict detached schema-1 mapping; raw bytes reject duplicate keys."""
    if isinstance(value, bytes):
        value = decode_document(value, maximum=MAX_ENROLLMENT_BYTES)
    data = _record(value, {"schema", "policy", "keys", "max_records", "max_bytes"},
                   "Target enrollment")
    if type(data["schema"]) is not int or data["schema"] != 1:
        raise ValueError("Unsupported target enrollment schema")
    policy = parse_target_policy(data["policy"])
    principals = {principal for app in policy["applications"].values() for principal in app["principals"]}
    if any(_SLUG.fullmatch(principal) is None for principal in principals):
        raise ValueError("Target enrollment principals must be lowercase slugs of at most 32 characters")
    if len({_username(principal) for principal in principals}) != len(principals):
        raise ValueError("Target principals collide on a transport account name")
    if not isinstance(data["keys"], Mapping) or set(data["keys"]) != principals:
        raise ValueError("Target keys must match the policy principal union exactly")
    keys = {}
    blobs = set()
    for principal in sorted(principals):
        key, blob = _public_ed25519_key(data["keys"][principal])
        if blob in blobs:
            raise ValueError("A target public key cannot authorize multiple principals")
        blobs.add(blob)
        keys[principal] = key
    # Reuse the serving boundary's quotas and private/deployment overlap checks.
    runtime = entrypoint.parse_config({
        "schema": 1, "roots": dict(ROOTS), "policy": policy,
        "max_records": data["max_records"], "max_bytes": data["max_bytes"],
    })
    for app in policy["applications"].values():
        if any(entrypoint._overlap(app["deployment_root"], protected)
               for protected in (SOURCE_ROOT, ETC_DIR, DATA_ROOT, SSH_ROOT, SUDOERS_DIR)):
            raise ValueError("Target deployment root overlaps protected enrollment resources")
    result = {"schema": 1, "policy": policy, "keys": keys,
              "max_records": runtime["max_records"], "max_bytes": runtime["max_bytes"]}
    encode_document(result, maximum=MAX_ENROLLMENT_BYTES)
    return result


def _hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _write(path: str, content: bytes, mode: int) -> None:
    trusted_path(os.path.dirname(path), directory=True)
    write_bytes_atomic(path, content, mode=mode, uid=0, gid=0)


def _save(marker: dict) -> None:
    _write(MARKER_PATH, encode_document(marker, maximum=_MARKER_LIMIT) + b"\n", 0o600)


def _directory(path: str, mode: int, *, create: bool = False) -> None:
    """Create through checked parents; existing role directories need exact modes."""
    validate_filesystem_path(path)
    if not os.path.lexists(path) and create:
        parent, name = os.path.split(path)
        if not os.path.lexists(parent):
            _directory(parent, 0o755, create=True)
        trusted_path(parent, directory=True)
        with directory_fd(parent) as parent_fd:
            os.mkdir(name, mode, dir_fd=parent_fd)
            with directory_fd(path) as descriptor:
                os.fchown(descriptor, 0, 0)
                os.fchmod(descriptor, mode)
                os.fsync(descriptor)
            os.fsync(parent_fd)
    trusted_path(path, directory=True)
    info = os.lstat(path)
    if info.st_gid != 0 or stat.S_IMODE(info.st_mode) != mode:
        raise ValueError("Unexpected managed target directory permissions: " + path)


def _missing_parent_directories(path: str) -> list[str]:
    """Check the complete deployment-parent chain without adopting unsafe paths."""
    validate_filesystem_path(path)
    missing = []
    for component in (*reversed(Path(path).parents), Path(path)):
        try:
            info = component.lstat()
        except FileNotFoundError:
            missing.append(str(component))
            continue
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or info.st_uid != 0 or info.st_mode & 0o022):
            raise ValueError("Unsafe target deployment parent ancestor; requires root-owned "
                             "nonwritable directories without symlinks: " + str(component))
    return missing


def _deployment_parent_plan(enrollment: dict | None) -> list[str]:
    if enrollment is None:
        return []
    parents = {os.path.dirname(app["deployment_root"])
               for app in enrollment["policy"]["applications"].values()}
    missing = {path for parent in sorted(parents) for path in _missing_parent_directories(parent)}
    return sorted(missing, key=lambda path: (len(Path(path).parts), path))


def _create_deployment_parents(paths: list[str]) -> None:
    """Create only the recorded missing parents; preserve safe existing modes."""
    for path in paths:
        missing = _missing_parent_directories(path)
        if not missing:
            continue
        if missing != [path]:
            raise ValueError("Target deployment parent creation plan changed: " + path)
        parent, name = os.path.split(path)
        with directory_fd(parent) as parent_fd:
            info = os.fstat(parent_fd)
            if info.st_uid != 0 or info.st_mode & 0o022:
                raise ValueError("Unsafe target deployment parent: " + parent)
            os.mkdir(name, 0o755, dir_fd=parent_fd)
            descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                 dir_fd=parent_fd)
            try:
                os.fchown(descriptor, 0, 0)
                os.fchmod(descriptor, 0o755)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.fsync(parent_fd)


def _account(username: str):
    try:
        return pwd.getpwnam(username)
    except KeyError:
        return None


def _group(username: str):
    try:
        return grp.getgrnam(username)
    except KeyError:
        return None


def _identity(principal: str, expected: dict | None = None) -> dict:
    username = _username(principal)
    account, group = _account(username), _group(username)
    if (account is None or group is None or account.pw_name != username
            or account.pw_uid <= 0 or account.pw_gid <= 0 or group.gr_name != username
            or group.gr_gid != account.pw_gid or group.gr_mem
            or account.pw_dir != _home(principal) or account.pw_shell != "/bin/sh"):
        raise ValueError("Invalid managed target transport identity: " + username)
    actual = {"uid": account.pw_uid, "gid": account.pw_gid}
    if expected is not None and actual != expected:
        raise ValueError("Managed target transport identity changed: " + username)
    if any(other.pw_name != username and
           (other.pw_uid == account.pw_uid or other.pw_gid == account.pw_gid)
           for other in pwd.getpwall()):
        raise ValueError("Target transport UID/GID is shared")
    if any(other.gr_name != username and other.gr_gid == account.pw_gid for other in grp.getgrall()):
        raise ValueError("Target transport GID has an alias")
    if set(os.getgrouplist(username, account.pw_gid)) != {account.pw_gid}:
        raise ValueError("Target transport has supplementary groups")
    status = run(["passwd", "-S", username], capture_output=True, text=True).stdout.split()
    if len(status) < 2 or status[:2] != [username, "L"]:
        raise ValueError("Target transport password is not locked")
    return actual


def _sudoers(principal: str) -> bytes:
    username = _username(principal)
    return (f'Defaults:{username} env_keep += "SSH_ORIGINAL_COMMAND"\n'
            f"{username} ALL=(root) NOPASSWD: /usr/bin/python3 -I {ENTRYPOINT_PATH} "
            f"--serve --principal {principal}\n").encode("ascii")


def _authorized_key(principal: str, key: str) -> bytes:
    command = (f"/usr/bin/sudo -n /usr/bin/python3 -I {ENTRYPOINT_PATH} "
               f"--serve --principal {principal}")
    return f'restrict,command="{command}" {key}\n'.encode("ascii")


def _file_paths(principal: str) -> tuple[str, str]:
    return SUDOERS_DIR + "/" + _username(principal), _home(principal) + "/.ssh/authorized_keys"


def _file_mode(path: str) -> int:
    if path.startswith(SUDOERS_DIR + "/"):
        return 0o440
    return 0o600 if path == CONFIG_PATH else 0o644


def _parse_marker(raw: bytes) -> dict:
    marker = _record(decode_document(raw, maximum=_MARKER_LIMIT),
                     {"schema", "state", "principals", "files", "pending"}, "Target ownership marker")
    if type(marker["schema"]) is int and marker["schema"] == 1:
        raise ValueError(_LEGACY_LAYOUT_GUIDANCE)
    if type(marker["schema"]) is not int or marker["schema"] != _MARKER_SCHEMA:
        raise ValueError("Unsupported target ownership marker")
    if marker["state"] not in {"enabled", "disabled", "updating"}:
        raise ValueError("Invalid target enrollment state")
    if marker["state"] == "updating" or marker["pending"] is not None:
        raise RuntimeError("Incomplete target enrollment requires operator reconciliation")
    if not isinstance(marker["principals"], dict) or not isinstance(marker["files"], dict):
        raise ValueError("Invalid target ownership inventory")
    allowed = {CONFIG_PATH}
    names = set()
    for principal, identity in marker["principals"].items():
        if not isinstance(principal, str) or _SLUG.fullmatch(principal) is None:
            raise ValueError("Invalid managed target principal")
        record = _record(identity, {"uid", "gid"}, "Target transport identity")
        if any(type(item) is not int or item <= 0 for item in record.values()):
            raise ValueError("Invalid managed target UID/GID")
        if _username(principal) in names:
            raise ValueError("Colliding managed target identities")
        names.add(_username(principal))
        allowed.update(_file_paths(principal))
    if not set(marker["files"]) <= allowed or any(
        not isinstance(digest, str) or _HASH.fullmatch(digest) is None for digest in marker["files"].values()
    ):
        raise ValueError("Invalid target managed file inventory")
    if marker["state"] == "disabled" and marker["files"]:
        raise ValueError("Disabled target marker retains authority")
    if marker["state"] == "enabled" and CONFIG_PATH not in marker["files"]:
        raise ValueError("Enabled target marker is missing configuration ownership")
    return marker


def _namespace(principals: set[str]) -> None:
    names = {_username(principal) for principal in principals}
    if (any(item.pw_name.startswith(_PREFIX) and item.pw_name not in names for item in pwd.getpwall())
            or any(item.gr_name.startswith(_PREFIX) and item.gr_name not in names for item in grp.getgrall())):
        raise ValueError("Refusing unmarked target accounts or groups")
    if trusted_path(SUDOERS_DIR, directory=True, missing=True):
        if any(name.startswith(_PREFIX) and name not in names for name in os.listdir(SUDOERS_DIR)):
            raise ValueError("Refusing unmarked target sudoers")
    if trusted_path(SSH_ROOT, directory=True, missing=True):
        homes = {name.removeprefix(_PREFIX) for name in names}
        if not set(os.listdir(SSH_ROOT)) <= homes:
            raise ValueError("Refusing unmarked target SSH homes")


def _require_ssh_traversal() -> None:
    """Refuse shared parents that hide public keys from transport identities."""
    trusted_path(SSH_ROOT, directory=True, missing=True)
    for ancestor in reversed(Path(SSH_ROOT).parents):
        try:
            info = ancestor.lstat()
        except FileNotFoundError:
            # Missing parents are created root:root 0755 by _directory().
            break
        # Transport identities never have UID/GID zero. Require other search
        # access, and group search too if a non-root group could match theirs.
        if (not info.st_mode & stat.S_IXOTH
                or info.st_gid != 0 and not info.st_mode & stat.S_IXGRP):
            raise ValueError("Target SSH ancestor is not traversable by transport identities: " + str(ancestor))


def _preflight(marker: dict | None, desired: set[str]) -> None:
    """Validate all retained identities/files before any installation mutation."""
    if desired:
        _require_ssh_traversal()
    principals = set(marker["principals"]) if marker else set()
    _namespace(principals)
    if marker is None:
        for path in (ETC_DIR, DATA_ROOT, SSH_ROOT):
            trusted_path(path, directory=True, missing=True)
            if os.path.lexists(path):
                raise ValueError("Refusing unmarked target directory: " + path)
    else:
        for path, mode in ((ETC_DIR, 0o700), (DATA_ROOT, 0o700), (SSH_ROOT, 0o711),
                           *((root, 0o700) for root in ROOTS.values())):
            _directory(path, mode)
        if not set(os.listdir(ETC_DIR)) <= {".managed.json", ".maintenance.lock", "config.json"}:
            raise ValueError("Refusing unmarked target configuration files")
    for principal in principals | desired:
        if principal not in principals:
            if (_account(_username(principal)) is not None or _group(_username(principal)) is not None
                    or os.path.lexists(_home(principal))
                    or any(os.path.lexists(path) for path in _file_paths(principal))):
                raise ValueError("Refusing unmarked target principal resources")
            continue
        _identity(principal, marker["principals"][principal])
        _directory(_home(principal), 0o555)
        _directory(_home(principal) + "/.ssh", 0o755)
        if (set(os.listdir(_home(principal))) != {".ssh"}
                or not set(os.listdir(_home(principal) + "/.ssh")) <= {"authorized_keys"}):
            raise ValueError("Refusing unmarked files in target SSH home")
    paths = {CONFIG_PATH}
    for principal in principals:
        paths.update(_file_paths(principal))
    inventory = marker["files"] if marker else {}
    for path in paths:
        raw = read_protected(path, mode=_file_mode(path), maximum=MAX_ENROLLMENT_BYTES)
        if (raw is None) != (path not in inventory) or raw is not None and _hash(raw) != inventory[path]:
            raise ValueError("Refusing unmarked, missing or modified target file: " + path)


def _trusted_source_tree() -> None:
    trusted_path(SOURCE_ROOT, directory=True)
    entrypoint._trusted_code(Path(ENTRYPOINT_PATH))
    entrypoint._trusted_libraries(Path(SOURCE_ROOT))


def _install_packages() -> None:
    packages = [validate_package_name(package) for package in _PACKAGES]
    missing = [package for package in packages if not is_package_installed(package)]
    if missing:
        environment = os.environ.copy()
        environment["DEBIAN_FRONTEND"] = "noninteractive"
        run(["apt-get", "install", "-y", "-qq", *APT_LOCK_OPTIONS, *missing], env=environment)
        if any(not is_package_installed(package) for package in missing):
            raise RuntimeError("Target prerequisite installation was not verified")


def _validate_sudoers(content: bytes) -> None:
    # This candidate is within the private marker-scoped directory; an interrupted
    # validation deliberately leaves evidence and the durable serving hold.
    path = ETC_DIR + "/.sudoers-candidate"
    if os.path.lexists(path):
        raise ValueError("Unreconciled target sudoers candidate")
    _write(path, content, 0o600)
    run(["visudo", "-cf", path])
    remove_file_durable(path)


def _reconcile(marker: dict, enrollment: dict | None, descriptor: int) -> None:
    parents_to_create = _deployment_parent_plan(enrollment)
    desired = {} if enrollment is None else enrollment["keys"]
    files = {}
    if enrollment is not None:
        runtime = {key: enrollment[key] for key in ("schema", "policy", "max_records", "max_bytes")}
        runtime["roots"] = dict(ROOTS)
        files[CONFIG_PATH] = encode_document(runtime, maximum=MAX_ENROLLMENT_BYTES) + b"\n"
        for principal, key in desired.items():
            sudo, auth = _file_paths(principal)
            files[sudo], files[auth] = _sudoers(principal), _authorized_key(principal, key)
    set_serving(descriptor, False)
    marker["state"] = "updating"
    marker["pending"] = {"files": {path: _hash(raw) for path, raw in files.items()},
                         "deployment_parents_to_create": parents_to_create,
                         "enrollment_sha256": None if enrollment is None else
                         _hash(encode_document(enrollment, maximum=MAX_ENROLLMENT_BYTES))}
    _save(marker)
    if enrollment is not None:
        _create_deployment_parents(parents_to_create)
        _install_packages()
        for path, mode in ((DATA_ROOT, 0o700), (SSH_ROOT, 0o711),
                           *((root, 0o700) for root in ROOTS.values())):
            _directory(path, mode, create=True)
        if not os.path.lexists(SUDOERS_DIR):
            _directory(SUDOERS_DIR, 0o755, create=True)
        for principal in desired:
            if principal not in marker["principals"]:
                marker["principals"][principal] = {"uid": None, "gid": None}
                _save(marker)
                username = _username(principal)
                run(["groupadd", "--system", username])
                run(["useradd", "--system", "--gid", username, "--home-dir", _home(principal),
                     "--no-create-home", "--shell", "/bin/sh", "--password", "!", username])
                marker["principals"][principal] = _identity(principal)
                _save(marker)
                _directory(_home(principal), 0o555, create=True)
                _directory(_home(principal) + "/.ssh", 0o755, create=True)
        for principal in desired:
            _validate_sudoers(_sudoers(principal))
    # Revoke old grants before keys. Serving remains held across every write.
    for path in sorted(set(marker["files"]) - set(files), key=lambda item: item.endswith("authorized_keys")):
        remove_file_durable(path)
    for path, raw in files.items():
        if read_protected(path, mode=_file_mode(path), maximum=MAX_ENROLLMENT_BYTES) != raw:
            _write(path, raw, _file_mode(path))
    marker.update(state="enabled" if enrollment is not None else "disabled",
                  files={path: _hash(raw) for path, raw in files.items()}, pending=None)
    _save(marker)
    if enrollment is not None:
        set_serving(descriptor, True)


def configure_build_target(config: object) -> None:
    """Apply explicit enrollment/disable; ordinary omission retains authority."""
    disable = getattr(config, "disable_build_target", False)
    if type(disable) is not bool:
        raise ValueError("Target disable selection must be boolean")
    raw = getattr(config, "build_target_enrollment", None)
    if raw is None and not disable:
        return
    if not disable and not isinstance(raw, Mapping):
        raise ValueError("Target enrollment must already be a decoded mapping")
    enrollment = None if disable else parse_build_target_enrollment(raw)
    from lib.machine_state import can_manage_system_services

    machine_type = getattr(config, "machine_type", None)
    if not can_manage_system_services(machine_type) or machine_type not in {"vm", "hardware"}:
        raise RuntimeError("Target enrollment requires VM or physical system services; containers are unsupported")
    if bool(getattr(config, "dry_run", False)) or is_dry_run():
        print("  [DRY-RUN] Would configure managed target enrollment" if not disable
              else "  [DRY-RUN] Would disable managed target enrollment")
        return
    if os.geteuid() != 0:
        raise PermissionError("Target enrollment requires root")
    entrypoint.require_platform()
    # Trust installed code before installing packages, accounts or authority.
    _trusted_source_tree()
    if os.path.lexists(_LEGACY_SSH_ROOT):
        raise ValueError(_LEGACY_LAYOUT_GUIDANCE + ": " + _LEGACY_SSH_ROOT)
    # Check all policy-derived parents before even the first-install bootstrap.
    # Reconcile rechecks and records the plan under the exclusive lease.
    _deployment_parent_plan(enrollment)
    initial = not os.path.lexists(ETC_DIR)
    if initial:
        _preflight(None, set() if enrollment is None else set(enrollment["keys"]))
        if disable:
            return
        # The only pre-lease mutations bootstrap the private marker and stable
        # lock. Exclusive mkdir prevents concurrent first installers adopting it.
        parent, name = os.path.split(ETC_DIR)
        if not os.path.lexists(parent):
            _directory(parent, 0o755, create=True)
        trusted_path(parent, directory=True)
        with directory_fd(parent) as parent_fd:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
            os.fsync(parent_fd)
        _directory(ETC_DIR, 0o700)
        marker = {"schema": _MARKER_SCHEMA, "state": "updating", "principals": {}, "files": {}, "pending": None}
        _save(marker)
    with target_lease(installer=True, create=initial) as descriptor:
        if not initial:
            _directory(ETC_DIR, 0o700)
            raw_marker = read_protected(MARKER_PATH, mode=0o600, maximum=_MARKER_LIMIT)
            if raw_marker is None:
                raise ValueError("Refusing target resources without their ownership marker")
            marker = _parse_marker(raw_marker)
            _preflight(marker, set() if enrollment is None else set(enrollment["keys"]))
        _reconcile(marker, enrollment, descriptor)


__all__ = ["parse_build_target_enrollment", "configure_build_target"]
