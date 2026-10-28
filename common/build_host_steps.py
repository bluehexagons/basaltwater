"""Provision the Debian build-host service and its forced-command accounts."""

from __future__ import annotations

import base64
from collections.abc import Mapping
import hashlib
import os
from pathlib import Path
import pwd
import grp
import re
import stat
from typing import TYPE_CHECKING, Any

from common.build_ssh_admission import admission_content
from lib.build_host_policy import MAX_POLICY_BYTES, parse_build_host_policy
from lib.build_protocol import decode_document, encode_document
from lib.maintenance_defaults import APT_LOCK_OPTIONS
from lib.remote_utils import is_dry_run, is_package_installed, run
from lib.setup_directories import directory_fd
from lib.validation import validate_filesystem_path, validate_package_name
from lib.validators import validate_username
from lib.project_build_tools import BUILD_NODE_HOME

if TYPE_CHECKING:
    from lib.config import SetupConfig


ENROLLMENT_SCHEMA = 1
MAX_ENROLLMENT_BYTES = MAX_POLICY_BYTES + 256 * 1024
BUILD_ACCOUNT = "basaltwater-build"
SERVICE_NAME = "basaltwater-build-host.service"
SERVICE_UNIT = "/etc/systemd/system/" + SERVICE_NAME
ETC_DIR = "/etc/basaltwater/build-host"
MARKER_PATH = ETC_DIR + "/.managed.json"
CONFIG_PATH = ETC_DIR + "/config.json"
SSH_ADMISSION_PATH = ETC_DIR + "/ssh-admission"
DATA_ROOT = "/var/lib/basaltwater/build-host"
SSH_ROOT = "/var/lib/basaltwater-build-host-ssh"
_LEGACY_SSH_ROOT = "/var/lib/basaltwater/build-host-ssh"
SSH_ROOT_MODE = 0o711
SSH_HOME_MODE = 0o555
SSH_DIRECTORY_MODE = 0o755
AUTHORIZED_KEYS_MODE = 0o644
SOURCE_ROOT = "/opt/basaltwater"
ENTRYPOINT_PATH = SOURCE_ROOT + "/common/service_tools/build_host.py"
SUDOERS_DIR = "/etc/sudoers.d"
ROOTS = {
    "state": DATA_ROOT + "/state",
    "inputs": DATA_ROOT + "/inputs",
    "scratch": DATA_ROOT + "/scratch",
    "artifacts": DATA_ROOT + "/artifacts",
    "logs": DATA_ROOT + "/logs",
}

_SLUG = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_MARKER_SCHEMA = 2
_LEGACY_LAYOUT_GUIDANCE = (
    "Legacy build-host transport layout requires operator reconciliation or a clean reinstall; "
    "preserve accounts, markers and retained data, revoke old authority under operator control, "
    "and do not widen /var/lib/basaltwater or move homes/edit markers to bypass this check"
)
_MARKER_LIMIT = 1024 * 1024
_FIXED_PACKAGES = ("git", "e2fsprogs", "openssh-server", "sudo")


def _plain_mapping(value: object, where: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{where} must be a mapping")
    return {key: _plain_value(item, f"{where}.{key}") for key, item in value.items()}


def _plain_value(value: object, where: str) -> Any:
    if isinstance(value, Mapping):
        return _plain_mapping(value, where)
    if isinstance(value, list):
        return [_plain_value(item, where) for item in value]
    if isinstance(value, tuple):
        return [_plain_value(item, where) for item in value]
    return value


def _record(value: object, fields: set[str], where: str) -> dict[str, Any]:
    data = _plain_mapping(value, where)
    if set(data) != fields:
        raise ValueError(f"{where} has missing or unsupported fields")
    return data


def _positive_integer(value: object, minimum: int, maximum: int, label: str) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{label} is outside supported limits")
    return value


def _public_ed25519_key(value: object) -> tuple[str, bytes]:
    if not isinstance(value, str) or "\n" in value or "\r" in value:
        raise ValueError("Enrollment keys must be one canonical ssh-ed25519 public-key line")
    fields = value.split(" ")
    if len(fields) != 2 or fields[0] != "ssh-ed25519" or not fields[1]:
        raise ValueError("Enrollment accepts only an ssh-ed25519 key without options or comments")
    try:
        blob = base64.b64decode(fields[1], validate=True)
    except (ValueError, base64.binascii.Error, UnicodeError):
        raise ValueError("Enrollment contains an invalid ssh-ed25519 wire blob") from None
    if base64.b64encode(blob).decode("ascii") != fields[1]:
        raise ValueError("Enrollment key encoding must be canonical base64")

    def take_string(raw: bytes, offset: int) -> tuple[bytes, int]:
        if offset + 4 > len(raw):
            raise ValueError("Enrollment contains an invalid ssh-ed25519 wire blob")
        length = int.from_bytes(raw[offset:offset + 4], "big")
        start, end = offset + 4, offset + 4 + length
        if end > len(raw):
            raise ValueError("Enrollment contains an invalid ssh-ed25519 wire blob")
        return raw[start:end], end

    key_type, offset = take_string(blob, 0)
    key_bytes, offset = take_string(blob, offset)
    if key_type != b"ssh-ed25519" or len(key_bytes) != 32 or offset != len(blob):
        raise ValueError("Enrollment contains an invalid ssh-ed25519 wire blob")
    return value, blob


def parse_build_host_enrollment(value: object) -> dict[str, Any]:
    """Validate and detach a strict initial build-host enrollment document.

    Accepts an already-decoded mapping or bounded UTF-8 JSON bytes. Principal
    labels are initial-enrollment slugs: lowercase ASCII letters, digits, and
    interior hyphens, up to 32 characters.
    """
    try:
        if isinstance(value, bytes):
            value = decode_document(value, maximum=MAX_ENROLLMENT_BYTES)
        data = _record(value, {"schema", "policy", "keys", "max_records", "max_bytes"},
                       "build-host enrollment")
    except RecursionError as exc:
        raise ValueError("Build-host enrollment nesting is too deep") from exc
    if type(data["schema"]) is not int or data["schema"] != ENROLLMENT_SCHEMA:
        raise ValueError("Unsupported build-host enrollment schema")

    policy = parse_build_host_policy(data["policy"])
    principals = set(policy["principals"])
    if not principals or any(_SLUG.fullmatch(principal) is None for principal in principals):
        raise ValueError("Initial build-host principals must be lowercase slugs of at most 32 characters")
    transport_names = {_transport_username(principal) for principal in principals}
    if len(transport_names) != len(principals):
        raise ValueError("Build-host principals collide on a transport account name")

    raw_keys = _plain_mapping(data["keys"], "build-host enrollment.keys")
    if set(raw_keys) != principals:
        raise ValueError("Enrollment keys must match the policy principal set exactly")
    keys: dict[str, str] = {}
    blobs: set[bytes] = set()
    for principal in sorted(principals):
        key, blob = _public_ed25519_key(raw_keys[principal])
        if blob in blobs:
            raise ValueError("An ssh-ed25519 public key cannot be enrolled for multiple principals")
        blobs.add(blob)
        keys[principal] = key

    max_records = _positive_integer(data["max_records"], 1, 4096, "Build-host record limit")
    max_bytes = _positive_integer(data["max_bytes"], 256 * 1024**2, 8 * 1024**4,
                                  "Build-host storage limit")
    result = {
        "schema": ENROLLMENT_SCHEMA,
        "policy": policy,
        "keys": keys,
        "max_records": max_records,
        "max_bytes": max_bytes,
    }
    encode_document(result, maximum=MAX_ENROLLMENT_BYTES)
    return result


def _trusted_path(path: str, *, final_directory: bool | None = None,
                  allow_missing: bool = False) -> os.stat_result | None:
    """Reject symlinks, non-root ancestors, and writable trusted-path components."""
    validate_filesystem_path(path)
    if not os.path.isabs(path) or os.path.normpath(path) != path:
        raise ValueError("Build-host paths must be canonical absolute paths")
    current = Path("/")
    components = list(Path(path).parts[1:])
    result = None
    for index, component in enumerate(components):
        current = current / component
        try:
            info = current.lstat()
        except FileNotFoundError:
            if allow_missing:
                return None
            raise
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0:
            raise ValueError(f"Build-host path is not controlled by root: {current}")
        is_final = index == len(components) - 1
        if not is_final and not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"Build-host path ancestor is not a directory: {current}")
        if info.st_mode & 0o022 and not (
                stat.S_ISDIR(info.st_mode) and info.st_mode & stat.S_ISVTX):
            raise ValueError(f"Build-host path has a writable trusted ancestor: {current}")
        if is_final and final_directory is not None:
            if final_directory != stat.S_ISDIR(info.st_mode):
                raise ValueError(f"Build-host path has an unexpected file type: {current}")
        result = info
    return result


def _trusted_source_tree() -> None:
    from common.service_tools import build_host as entrypoint

    root = Path(SOURCE_ROOT)
    helper = Path(ENTRYPOINT_PATH)
    validate_filesystem_path(str(root), must_exist=True)
    resolved = root.resolve(strict=True)
    if (str(resolved) != SOURCE_ROOT or resolved == Path("/")
            or resolved.is_relative_to(Path("/home"))):
        raise ValueError("Build-host code must be installed at the fixed root-controlled target tree")
    if str(helper) != ENTRYPOINT_PATH or not helper.is_absolute():
        raise ValueError("Build-host entrypoint path is not a safe literal")
    if any(char.isspace() or char in "%:$\n\r" for char in ENTRYPOINT_PATH):
        raise ValueError("Build-host entrypoint path contains unsupported systemd characters")
    entrypoint._trusted_code(helper)
    entrypoint._trusted_libraries(resolved)


def _read_file(path: str, *, mode: int, maximum: int) -> bytes | None:
    """Read one root-owned regular file through a no-follow parent descriptor."""
    parent, name = os.path.dirname(path), os.path.basename(path)
    _trusted_path(parent, final_directory=True)
    with directory_fd(parent) as parent_fd:
        try:
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
        except FileNotFoundError:
            return None
        try:
            before = os.fstat(descriptor)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != 0 or before.st_gid != 0
                    or before.st_nlink != 1
                    or stat.S_IMODE(before.st_mode) != mode or before.st_size > maximum):
                raise ValueError(f"Managed build-host file is unsafe: {path}")
            with os.fdopen(os.dup(descriptor), "rb") as stream:
                content = stream.read(maximum + 1)
            after = os.fstat(descriptor)
            identity = lambda info: (info.st_dev, info.st_ino, info.st_size,
                                     info.st_mtime_ns, info.st_ctime_ns)
            named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if (len(content) != before.st_size or len(content) > maximum
                    or identity(before) != identity(after)
                    or (named.st_dev, named.st_ino) != (before.st_dev, before.st_ino)):
                raise ValueError(f"Managed build-host file changed while reading: {path}")
            return content
        finally:
            os.close(descriptor)


def _write_file(path: str, content: bytes, *, mode: int, exclusive: bool = False) -> None:
    """Atomically publish bytes via a trusted directory descriptor."""
    parent, name = os.path.dirname(path), os.path.basename(path)
    _trusted_path(parent, final_directory=True)
    with directory_fd(parent) as parent_fd:
        parent_info = os.fstat(parent_fd)
        if (not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_uid != 0
                or parent_info.st_gid != 0
                or parent_info.st_mode & 0o022):
            raise ValueError(f"Build-host file parent is not root-controlled: {parent}")
        temporary = "." + name + "." + os.urandom(12).hex() + ".tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(temporary, flags, mode, dir_fd=parent_fd)
        try:
            os.fchown(descriptor, 0, 0)
            os.fchmod(descriptor, mode)
            offset = 0
            while offset < len(content):
                written = os.write(descriptor, content[offset:])
                if written <= 0:
                    raise OSError("Short write while publishing a build-host file")
                offset += written
            os.fsync(descriptor)
            if exclusive:
                try:
                    os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise FileExistsError(path)
                os.link(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd,
                        follow_symlinks=False)
                os.unlink(temporary, dir_fd=parent_fd)
            else:
                os.replace(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            os.fsync(parent_fd)
        finally:
            os.close(descriptor)
            try:
                os.unlink(temporary, dir_fd=parent_fd)
            except FileNotFoundError:
                pass


def _remove_file(path: str, *, mode: int, maximum: int) -> bytes | None:
    """Durably unlink a verified root-owned regular file and return its bytes."""
    content = _read_file(path, mode=mode, maximum=maximum)
    if content is None:
        return None
    parent, name = os.path.dirname(path), os.path.basename(path)
    with directory_fd(parent) as parent_fd:
        os.unlink(name, dir_fd=parent_fd)
        os.fsync(parent_fd)
    return content


def _ensure_parent_chain(path: str) -> None:
    parent = os.path.dirname(path)
    if parent == "/":
        return
    _ensure_parent_chain(parent)
    try:
        info = os.lstat(parent)
    except FileNotFoundError:
        grandparent, name = os.path.dirname(parent), os.path.basename(parent)
        with directory_fd(grandparent) as descriptor:
            os.mkdir(name, 0o755, dir_fd=descriptor)
            with directory_fd(parent) as child:
                os.fchown(child, 0, 0)
                os.fchmod(child, 0o755)
                os.fsync(child)
            os.fsync(descriptor)
        info = os.lstat(parent)
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_uid != 0 or info.st_mode & 0o022):
        raise ValueError(f"Build-host directory ancestor is not root-controlled: {parent}")


def _ensure_directory(path: str, *, mode: int) -> None:
    _ensure_parent_chain(path)
    parent, name = os.path.dirname(path), os.path.basename(path)
    with directory_fd(parent) as parent_fd:
        created = False
        try:
            descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        except FileNotFoundError:
            os.mkdir(name, mode, dir_fd=parent_fd)
            created = True
            os.fsync(parent_fd)
            descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_gid != 0:
                raise ValueError(f"Build-host directory is not root-owned: {path}")
            if not created and stat.S_IMODE(info.st_mode) != mode:
                raise ValueError(f"Refusing an unmanaged build-host directory: {path}")
            if stat.S_IMODE(info.st_mode) != mode:
                os.fchown(descriptor, 0, 0)
                os.fchmod(descriptor, mode)
                os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _empty_marker() -> dict[str, Any]:
    return {"schema": _MARKER_SCHEMA, "worker": None, "disabled": False, "principals": {},
            "files": {"config": [], "unit": [], "ssh": []}, "pending": None,
            "ssh_pending": None}


def _parse_marker(raw: bytes) -> dict[str, Any]:
    value = _plain_mapping(decode_document(raw, maximum=_MARKER_LIMIT), "build-host ownership marker")
    value.setdefault("ssh_pending", None)
    marker = _record(value, {"schema", "worker", "disabled", "principals", "files", "pending", "ssh_pending"},
                     "build-host ownership marker")
    if type(marker["schema"]) is int and marker["schema"] == 1:
        raise ValueError(_LEGACY_LAYOUT_GUIDANCE)
    if type(marker["schema"]) is not int or marker["schema"] != _MARKER_SCHEMA:
        raise ValueError("Unsupported build-host ownership marker")
    if type(marker["disabled"]) is not bool:
        raise ValueError("Invalid build-host enabled state marker")
    worker = marker["worker"]
    if worker is not None:
        worker = _record(worker, {"uid", "gid"}, "build-host worker identity")
        for key in ("uid", "gid"):
            if worker[key] is not None and (type(worker[key]) is not int or worker[key] <= 0):
                raise ValueError("Invalid managed build-host worker identity")
    principals = _plain_mapping(marker["principals"], "managed build-host principals")
    transport_names: set[str] = set()
    for principal, record in principals.items():
        if _SLUG.fullmatch(principal) is None:
            raise ValueError("Invalid principal in build-host ownership marker")
        record = _record(record, {"username", "uid", "gid", "key", "managed",
                                  "auth_managed", "sudo_managed", "sudo_hashes"},
                         f"managed principal {principal!r}")
        expected_user = _transport_username(principal)
        if record["username"] != expected_user or not validate_username(expected_user):
            raise ValueError("Invalid transport identity in build-host ownership marker")
        if expected_user in transport_names:
            raise ValueError("Build-host ownership marker has colliding transport identities")
        transport_names.add(expected_user)
        for key in ("uid", "gid"):
            if record[key] is not None and (type(record[key]) is not int or record[key] <= 0):
                raise ValueError("Invalid transport identity in build-host ownership marker")
        if record["key"] is not None:
            record["key"], _blob = _public_ed25519_key(record["key"])
        if any(type(record[field]) is not bool for field in ("managed", "auth_managed", "sudo_managed")):
            raise ValueError("Invalid managed transport identity marker")
        if (not isinstance(record["sudo_hashes"], list) or len(record["sudo_hashes"]) > 2
                or any(not isinstance(item, str) or _HASH.fullmatch(item) is None
                       for item in record["sudo_hashes"])
                or len(set(record["sudo_hashes"])) != len(record["sudo_hashes"])):
            raise ValueError("Invalid managed sudoers hash list")
        principals[principal] = record
    # Schema-2 installs predating SSH admission migrate only in guarded setup.
    raw_files = _plain_mapping(marker["files"], "managed build-host files")
    raw_files.setdefault("ssh", [])
    files = _record(raw_files, {"config", "unit", "ssh"}, "managed build-host files")
    for name, hashes in files.items():
        if (not isinstance(hashes, list) or len(hashes) > 2
                or any(not isinstance(item, str) or _HASH.fullmatch(item) is None for item in hashes)
                or len(set(hashes)) != len(hashes)):
            raise ValueError(f"Invalid managed build-host {name} hash list")
    pending = marker["pending"]
    if pending is not None:
        pending = _record(pending, {"enrollment_sha256", "keys", "config_sha256", "unit_sha256"},
                          "pending build-host reconciliation")
        if any(not isinstance(pending[key], str) or _HASH.fullmatch(pending[key]) is None
               for key in ("enrollment_sha256", "config_sha256", "unit_sha256")):
            raise ValueError("Invalid pending build-host reconciliation")
        pending["keys"] = _plain_mapping(pending["keys"], "pending build-host keys")
        for principal, key in pending["keys"].items():
            if _SLUG.fullmatch(principal) is None:
                raise ValueError("Invalid pending build-host principal")
            _public_ed25519_key(key)
    ssh_pending = marker["ssh_pending"]
    if ssh_pending is not None:
        ssh_pending = _record(ssh_pending, {"action", "sha256"}, "pending build-host SSH admission")
        digest = ssh_pending["sha256"]
        if (not isinstance(digest, str) or _HASH.fullmatch(digest) is None
                or files["ssh"] != [digest]):
            raise ValueError("Invalid pending build-host SSH admission hash")
        if ssh_pending["action"] == "create":
            if pending is None or digest != _hash(admission_content(
                    "build-host", [_transport_username(name) for name in pending["keys"]])):
                raise ValueError("Invalid pending build-host SSH admission creation")
        elif ssh_pending["action"] != "remove" or not marker["disabled"]:
            raise ValueError("Invalid pending build-host SSH admission removal")
    return {"schema": _MARKER_SCHEMA, "worker": worker, "disabled": marker["disabled"],
            "principals": principals,
            "files": files, "pending": pending, "ssh_pending": ssh_pending}


def _write_marker(marker: dict[str, Any], *, exclusive: bool = False) -> None:
    _write_file(MARKER_PATH, encode_document(marker, maximum=_MARKER_LIMIT) + b"\n",
                mode=0o600, exclusive=exclusive)


def _transport_username(principal: str) -> str:
    suffix = hashlib.sha256(principal.encode("ascii")).hexdigest()[:12]
    username = "bw-build-" + suffix
    if not validate_username(username):
        raise ValueError("Derived build-host account name is invalid")
    return username


def _get_account(username: str):
    try:
        return pwd.getpwnam(username)
    except KeyError:
        return None


def _get_group(groupname: str):
    try:
        return grp.getgrnam(groupname)
    except KeyError:
        return None


def _require_exclusive_transport_account(username: str, home: str, uid: int | None,
                                         gid: int | None) -> tuple[int, int]:
    account = _get_account(username)
    group = _get_group(username)
    if account is None or group is None:
        raise RuntimeError(f"Managed build-host transport account is incomplete: {username}")
    if (account.pw_name != username or account.pw_dir != home or account.pw_shell != "/bin/sh"
            or account.pw_uid <= 0 or account.pw_gid <= 0 or group.gr_name != username
            or group.gr_gid != account.pw_gid or group.gr_mem):
        raise ValueError(f"Managed build-host transport account has unexpected attributes: {username}")
    if uid is not None and account.pw_uid != uid or gid is not None and account.pw_gid != gid:
        raise ValueError(f"Managed build-host transport account identity changed: {username}")
    if any(other.pw_name != username and
           (other.pw_uid == account.pw_uid or other.pw_gid == account.pw_gid)
           for other in pwd.getpwall()):
        raise ValueError(f"Build-host transport UID/GID is shared: {username}")
    if any(other.gr_name != username and other.gr_gid == account.pw_gid for other in grp.getgrall()):
        raise ValueError(f"Build-host transport GID has another group alias: {username}")
    try:
        memberships = os.getgrouplist(username, account.pw_gid)
    except (AttributeError, OSError) as exc:
        raise RuntimeError(f"Could not verify build-host supplementary groups: {username}") from exc
    if set(memberships) != {account.pw_gid}:
        raise ValueError(f"Build-host transport account has supplementary groups: {username}")
    status = run(["passwd", "-S", username], capture_output=True, text=True).stdout.split()
    if len(status) < 2 or status[0] != username or status[1] != "L":
        raise ValueError(f"Build-host transport account password is not locked: {username}")
    return account.pw_uid, account.pw_gid


def _ensure_worker_account(marker: dict[str, Any]):
    from common.service_tools import build_host as entrypoint

    identity = marker["worker"]
    group = _get_group(BUILD_ACCOUNT)
    account = _get_account(BUILD_ACCOUNT)
    if identity is None:
        if group is not None or account is not None:
            raise ValueError("Refusing an unmanaged basaltwater-build account or group")
        identity = {"uid": None, "gid": None}
        marker["worker"] = identity
        _write_marker(marker)
    if group is None:
        run(["groupadd", "--system", BUILD_ACCOUNT])
        group = _get_group(BUILD_ACCOUNT)
    if group is None:
        raise RuntimeError("Could not create the managed build-host worker group")
    if account is None:
        run(["useradd", "--system", "--gid", BUILD_ACCOUNT, "--home-dir", "/nonexistent",
             "--no-create-home", "--shell", "/usr/sbin/nologin", "--password", "!", BUILD_ACCOUNT])
        account = _get_account(BUILD_ACCOUNT)
    if account is None:
        raise RuntimeError("Could not create the managed build-host worker account")
    actual = {"uid": account.pw_uid, "gid": account.pw_gid}
    if identity["uid"] is not None and identity != actual:
        raise ValueError("Managed build-host worker identity changed")
    entrypoint.require_build_account(actual)
    marker["worker"] = actual
    _write_marker(marker)
    return actual


def _ensure_transport_account(principal: str, record: dict[str, Any],
                              marker: dict[str, Any]) -> None:
    username = record["username"]
    home = SSH_ROOT + "/" + username.removeprefix("bw-build-")
    validate_filesystem_path(home)
    group = _get_group(username)
    account = _get_account(username)
    if group is None:
        run(["groupadd", "--system", username])
        group = _get_group(username)
    if group is None:
        raise RuntimeError(f"Could not create build-host transport group: {username}")
    if account is None:
        run(["useradd", "--system", "--gid", username, "--home-dir", home,
             "--no-create-home", "--shell", "/bin/sh", "--password", "!", username])
    uid, gid = _require_exclusive_transport_account(username, home, record["uid"], record["gid"])
    record.update(uid=uid, gid=gid, managed=True)
    _write_marker(marker)


def _sudoers_content(username: str, principal: str) -> bytes:
    entrypoint = ENTRYPOINT_PATH
    command = (f"/usr/bin/python3 -I {entrypoint} --serve --principal {principal}")
    content = (f'Defaults:{username} env_keep += "SSH_ORIGINAL_COMMAND"\n'
               f"{username} ALL=(root) NOPASSWD: {command}\n")
    return content.encode("ascii")


def _authorized_key_line(principal: str, key: str) -> bytes:
    command = (f"/usr/bin/sudo -n /usr/bin/python3 -I {ENTRYPOINT_PATH} "
               f"--serve --principal {principal}")
    return f'restrict,command="{command}" {key}\n'.encode("ascii")


def _matches_managed_public_key(line: bytes, key: str) -> bool:
    """Match only the enrolled key payload, even if root edited its options/comment."""
    encoded = key.split(" ", 1)[1].encode("ascii")
    needle = b"ssh-ed25519 " + encoded
    candidate = line.rstrip(b"\r\n")
    start = candidate.find(needle)
    while start >= 0:
        before_ok = start == 0 or candidate[start - 1:start] in {b" ", b"\t"}
        end = start + len(needle)
        after_ok = end == len(candidate) or candidate[end:end + 1] in {b" ", b"\t"}
        if before_ok and after_ok:
            return True
        start = candidate.find(needle, start + 1)
    return False


def _validate_sudoers(content: bytes) -> None:
    _trusted_path(ETC_DIR, final_directory=True)
    with directory_fd(ETC_DIR) as parent_fd:
        name = ".visudo-" + os.urandom(12).hex() + ".tmp"
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
                             dir_fd=parent_fd)
        try:
            os.fchown(descriptor, 0, 0)
            os.fchmod(descriptor, 0o600)
            offset = 0
            while offset < len(content):
                offset += os.write(descriptor, content[offset:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            run(["visudo", "-cf", ETC_DIR + "/" + name])
        finally:
            os.unlink(name, dir_fd=parent_fd)
            os.fsync(parent_fd)


def _read_root_file(path: str, *, mode: int, maximum: int) -> bytes | None:
    _trusted_path(os.path.dirname(path), final_directory=True)
    return _read_file(path, mode=mode, maximum=maximum)


def _reconcile_authorized_key(principal: str, record: dict[str, Any], desired: str | None,
                              marker: dict[str, Any], *, extra_keys: tuple[str, ...] = ()) -> None:
    path = SSH_ROOT + "/" + record["username"].removeprefix("bw-build-") + "/.ssh/authorized_keys"
    if not os.path.lexists(os.path.dirname(path)):
        if desired is None:
            return
        raise ValueError(f"Managed build-host SSH directory is missing: {os.path.dirname(path)}")
    old_key = record["key"]
    existing = _read_root_file(path, mode=AUTHORIZED_KEYS_MODE, maximum=64 * 1024)
    if existing is not None and not record["auth_managed"]:
        raise ValueError(f"Refusing an unmanaged build-host authorized_keys file: {path}")
    if existing is None:
        lines: list[bytes] = []
    else:
        lines = existing.splitlines(keepends=True)
    managed_keys = tuple(key for key in (old_key, desired, *extra_keys) if key is not None)
    kept = [line for line in lines
            if not any(_matches_managed_public_key(line, key) for key in managed_keys)]
    if desired is not None:
        kept.append(_authorized_key_line(principal, desired))
    updated = b"".join(kept)
    if updated == existing:
        return
    record["auth_managed"] = True
    _write_marker(marker)
    if updated:
        _write_file(path, updated, mode=AUTHORIZED_KEYS_MODE)
    elif existing is not None:
        _remove_file(path, mode=AUTHORIZED_KEYS_MODE, maximum=64 * 1024)


def _reconcile_sudoers(principal: str, record: dict[str, Any], desired: str | None,
                       marker: dict[str, Any]) -> None:
    username = record["username"]
    path = SUDOERS_DIR + "/" + username
    if not os.path.lexists(SUDOERS_DIR):
        if desired is None:
            return
        raise ValueError("The sudoers include directory is missing")
    existing = _read_root_file(path, mode=0o440, maximum=64 * 1024)
    if existing is not None and not record["sudo_managed"]:
        raise ValueError(f"Refusing an unmanaged build-host sudoers file: {path}")
    if existing is not None and _hash(existing) not in record["sudo_hashes"]:
        raise ValueError(f"Refusing a modified managed build-host sudoers file: {path}")
    if desired is not None:
        candidate = _sudoers_content(username, principal)
        final = candidate
        _validate_sudoers(final)
        if final != existing:
            record["sudo_managed"] = True
            record["sudo_hashes"] = sorted(set(record["sudo_hashes"] + [_hash(final)]))[-2:]
            _write_marker(marker)
            _write_file(path, final, mode=0o440)
            record["sudo_hashes"] = [_hash(final)]
            _write_marker(marker)
    else:
        if existing is not None:
            _remove_file(path, mode=0o440, maximum=64 * 1024)
        if record["sudo_hashes"]:
            record["sudo_hashes"] = []
            _write_marker(marker)


def _ensure_runtime_directories() -> None:
    _ensure_directory(DATA_ROOT, mode=0o700)
    for root in ROOTS.values():
        _ensure_directory(root, mode=0o700)
    _ensure_directory(SSH_ROOT, mode=SSH_ROOT_MODE)
    _trusted_path(SUDOERS_DIR, final_directory=True, allow_missing=True)


def _initial_install_conflicts(marker_present: bool, principals: set[str]) -> None:
    if marker_present:
        return
    for path in (DATA_ROOT, SSH_ROOT, CONFIG_PATH, SERVICE_UNIT):
        if os.path.lexists(path):
            raise ValueError(f"Refusing unmanaged build-host path: {path}")
    if _get_account(BUILD_ACCOUNT) is not None or _get_group(BUILD_ACCOUNT) is not None:
        raise ValueError("Refusing an unmanaged basaltwater-build account or group")
    for principal in principals:
        username = _transport_username(principal)
        if _get_account(username) is not None or _get_group(username) is not None:
            raise ValueError(f"Refusing an unmanaged build-host transport identity: {username}")
        if os.path.lexists(SUDOERS_DIR + "/" + username):
            raise ValueError(f"Refusing an unmanaged build-host sudoers file: {username}")


def _runtime_config(enrollment: dict[str, Any], identity: dict[str, int]) -> dict[str, Any]:
    return {
        "schema": 1,
        "uid": identity["uid"],
        "gid": identity["gid"],
        "roots": dict(ROOTS),
        "policy": enrollment["policy"],
        "max_records": enrollment["max_records"],
        "max_bytes": enrollment["max_bytes"],
    }


def _service_content() -> str:
    return (
        "[Unit]\n"
        "Description=Basaltwater on-demand build host worker\n"
        "After=local-fs.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart=/usr/bin/python3 -I {ENTRYPOINT_PATH} --worker\n"
        "User=root\n"
        "Group=root\n"
        "WorkingDirectory=/\n"
        "UMask=0077\n"
        "KillMode=control-group\n"
        "Restart=no\n"
        "StandardOutput=journal\n"
        "StandardError=journal\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def _validate_transport_namespace(principals: set[str]) -> None:
    """Reject build-host-shaped resources not named by this ownership marker."""
    expected_users = {_transport_username(principal) for principal in principals}
    unexpected_accounts = [account.pw_name for account in pwd.getpwall()
                           if account.pw_name.startswith("bw-build-")
                           and account.pw_name not in expected_users]
    unexpected_groups = [group.gr_name for group in grp.getgrall()
                         if group.gr_name.startswith("bw-build-")
                         and group.gr_name not in expected_users]
    if unexpected_accounts or unexpected_groups:
        raise ValueError("Refusing unmarked build-host transport accounts or groups")

    if os.path.lexists(SUDOERS_DIR):
        _trusted_path(SUDOERS_DIR, final_directory=True)
        with os.scandir(SUDOERS_DIR) as entries:
            unexpected = [entry.name for entry in entries
                          if entry.name.startswith("bw-build-") and entry.name not in expected_users]
        if unexpected:
            raise ValueError("Refusing unmarked build-host sudoers files")

    if not os.path.lexists(SSH_ROOT):
        return
    _trusted_path(SSH_ROOT, final_directory=True)
    root_info = Path(SSH_ROOT).lstat()
    if (root_info.st_uid != 0 or root_info.st_gid != 0
            or stat.S_IMODE(root_info.st_mode) != SSH_ROOT_MODE):
        raise ValueError("Build-host SSH root has an unsupported mode or owner")
    expected_homes = {username.removeprefix("bw-build-") for username in expected_users}
    with os.scandir(SSH_ROOT) as entries:
        for entry in entries:
            if entry.name not in expected_homes:
                raise ValueError(f"Refusing an unmarked build-host SSH home: {entry.name}")
            home_info = entry.stat(follow_symlinks=False)
            if (not stat.S_ISDIR(home_info.st_mode) or home_info.st_uid != 0
                    or home_info.st_gid != 0 or stat.S_IMODE(home_info.st_mode) != SSH_HOME_MODE):
                raise ValueError(f"Managed build-host SSH home is unsafe: {entry.name}")
            ssh_path = os.path.join(SSH_ROOT, entry.name, ".ssh")
            if not os.path.lexists(ssh_path):
                continue
            ssh_info = _trusted_path(ssh_path, final_directory=True)
            if (ssh_info is None or ssh_info.st_uid != 0 or ssh_info.st_gid != 0
                    or stat.S_IMODE(ssh_info.st_mode) != SSH_DIRECTORY_MODE):
                raise ValueError(f"Managed build-host .ssh directory is unsafe: {entry.name}")
            with os.scandir(ssh_path) as ssh_entries:
                for child in ssh_entries:
                    if child.name != "authorized_keys":
                        raise ValueError(f"Refusing unmarked file in build-host .ssh directory: {child.name}")


def _install_packages(config: SetupConfig) -> None:
    packages = list(_FIXED_PACKAGES)
    packages = [validate_package_name(package) for package in packages]
    missing = [package for package in packages if not is_package_installed(package)]
    if not missing:
        return
    environment = os.environ.copy()
    environment["DEBIAN_FRONTEND"] = "noninteractive"
    run(["apt-get", "install", "-y", "-qq", *APT_LOCK_OPTIONS, *missing], env=environment)
    remaining = [package for package in missing if not is_package_installed(package)]
    if remaining:
        raise RuntimeError("Build-host package installation was not verified: " + ", ".join(remaining))


def _install_node_toolchain(config: SetupConfig) -> None:
    """Explicit setup only: reuse managed installation, then freeze the inventory."""
    if not config.install_node:
        return
    from common.common_steps import install_node_for_user

    _ensure_directory(BUILD_NODE_HOME, mode=0o755)
    # Setup holds the drained worker lease. No project source is present here.
    run(["chown", BUILD_ACCOUNT + ":" + BUILD_ACCOUNT, "--", BUILD_NODE_HOME])
    try:
        install_node_for_user(BUILD_ACCOUNT, BUILD_NODE_HOME,
                              refresh=config.refresh_packages)
    finally:
        run(["chown", "-hR", "root:root", "--", BUILD_NODE_HOME])
        run(["chmod", "-R", "go-w", "--", BUILD_NODE_HOME])
        run(["chmod", "0755", "--", BUILD_NODE_HOME])


def _store_snapshot(store) -> tuple[bool, bool]:
    with store._locked() as (state, _parent_fd):
        return state["draining"], any(job["state"] == "running" for job in state["jobs"].values())


def _verify_unit_source() -> None:
    _trusted_path(SERVICE_UNIT, final_directory=False, allow_missing=True)
    from lib.remote_utils import run as run_command

    result = run_command(["systemctl", "show", SERVICE_NAME, "--property=FragmentPath", "--value",
                          "--no-pager"], check=False, capture_output=True, text=True)
    fragment = (result.stdout or "").strip()
    if fragment and fragment != SERVICE_UNIT:
        raise ValueError(f"Refusing an unmanaged systemd unit: {SERVICE_NAME}")


def _preflight_managed_state(marker: dict[str, Any], enrollment: dict[str, Any],
                             enrollment_digest: str) -> None:
    """Reject foreign accounts and files before unit, package, or policy changes."""
    _require_ssh_traversal()
    _preflight_ssh_admission(marker)
    if marker["ssh_pending"] is not None and marker["ssh_pending"]["action"] == "remove":
        raise RuntimeError("Build-host SSH admission removal is incomplete; retry disable first")
    _validate_transport_namespace(set(marker["principals"]) | set(enrollment["keys"]))
    pending = marker["pending"]
    if pending is not None and pending["enrollment_sha256"] != enrollment_digest:
        raise RuntimeError("A previous build-host reconciliation is incomplete; retry its enrollment first")

    worker = marker["worker"]
    worker_user, worker_group = _get_account(BUILD_ACCOUNT), _get_group(BUILD_ACCOUNT)
    if worker is None:
        if worker_user is not None or worker_group is not None:
            raise ValueError("Refusing an unmanaged basaltwater-build account or group")
    elif worker["uid"] is not None:
        if worker_user is None or worker_group is None:
            raise ValueError("Managed build-host worker account or group is missing")
        from common.service_tools import build_host as entrypoint
        entrypoint.require_build_account(worker)
    elif worker_user is not None:
        if worker_group is None:
            raise ValueError("Managed build-host worker identity is incomplete")
        from common.service_tools import build_host as entrypoint
        entrypoint.require_build_account({"uid": worker_user.pw_uid, "gid": worker_user.pw_gid})

    for principal in set(enrollment["keys"]) | set(marker["principals"]):
        username = _transport_username(principal)
        record = marker["principals"].get(principal)
        user, group = _get_account(username), _get_group(username)
        sudo_path = SUDOERS_DIR + "/" + username
        key_path = SSH_ROOT + "/" + username.removeprefix("bw-build-") + "/.ssh/authorized_keys"
        if record is None:
            home_path = SSH_ROOT + "/" + username.removeprefix("bw-build-")
            if (user is not None or group is not None or os.path.lexists(sudo_path)
                    or os.path.lexists(home_path) or os.path.lexists(key_path)):
                raise ValueError(f"Refusing unmanaged build-host principal resources: {principal}")
            continue
        if record["managed"]:
            _require_exclusive_transport_account(
                username, SSH_ROOT + "/" + username.removeprefix("bw-build-"),
                record["uid"], record["gid"],
            )
        elif pending is not None and user is not None and group is not None:
            _require_exclusive_transport_account(
                username, SSH_ROOT + "/" + username.removeprefix("bw-build-"),
                record["uid"], record["gid"],
            )
        elif pending is None and (user is not None or group is not None):
            raise ValueError(f"Refusing an unmanaged build-host transport identity: {username}")
        if os.path.lexists(sudo_path):
            sudo_content = _read_file(sudo_path, mode=0o440, maximum=64 * 1024)
            if not record["sudo_managed"]:
                raise ValueError(f"Refusing an unmanaged build-host sudoers file: {username}")
            if _hash(sudo_content or b"") not in record["sudo_hashes"]:
                raise ValueError(f"Refusing a modified managed build-host sudoers file: {username}")
        if os.path.lexists(key_path):
            _trusted_path(os.path.dirname(key_path), final_directory=True)
            _read_file(key_path, mode=AUTHORIZED_KEYS_MODE, maximum=64 * 1024)
            if not record["auth_managed"]:
                raise ValueError(f"Refusing an unmanaged build-host authorized_keys file: {username}")

    current_config = (_read_file(CONFIG_PATH, mode=0o600, maximum=MAX_ENROLLMENT_BYTES)
                      if os.path.lexists(ETC_DIR) else None)
    if current_config is not None and _hash(current_config) not in marker["files"]["config"]:
        raise ValueError("Refusing an unmanaged or modified build-host configuration file")
    current_unit = _read_file(SERVICE_UNIT, mode=0o644, maximum=1024 * 1024)
    if current_unit is not None and _hash(current_unit) not in marker["files"]["unit"]:
        raise ValueError(f"Refusing an unmanaged or modified systemd unit: {SERVICE_NAME}")


def _prepare_enrollment(config: SetupConfig, enrollment: dict[str, Any], marker: dict[str, Any],
                        enrollment_digest: str, unit_content: bytes) -> bytes:
    _preflight_ssh_admission(marker)
    if (marker["pending"] is not None
            and marker["pending"]["enrollment_sha256"] != enrollment_digest):
        raise RuntimeError("A previous build-host reconciliation is incomplete; retry its enrollment first")
    worker_identity = _ensure_worker_account(marker)
    config_content = encode_document(_runtime_config(enrollment, worker_identity),
                                     maximum=MAX_ENROLLMENT_BYTES) + b"\n"
    if marker["pending"] is not None:
        if (marker["pending"]["config_sha256"] != _hash(config_content)
                or marker["pending"]["unit_sha256"] != _hash(unit_content)):
            raise RuntimeError("Pending build-host files differ from the interrupted reconciliation")
    marker["pending"] = {
        "enrollment_sha256": enrollment_digest,
        "keys": dict(enrollment["keys"]),
        "config_sha256": _hash(config_content),
        "unit_sha256": _hash(unit_content),
    }
    marker["files"]["config"] = sorted(set(marker["files"]["config"] + [_hash(config_content)]))[-2:]
    marker["files"]["unit"] = sorted(set(marker["files"]["unit"] + [_hash(unit_content)]))[-2:]
    _write_marker(marker)

    _install_packages(config)
    _install_node_toolchain(config)

    desired_principals = set(enrollment["keys"])
    for principal in sorted(desired_principals):
        if principal not in marker["principals"]:
            username = _transport_username(principal)
            if _get_account(username) is not None or _get_group(username) is not None:
                raise ValueError(f"Refusing an unmanaged build-host transport identity: {username}")
            if os.path.lexists(SUDOERS_DIR + "/" + username):
                raise ValueError(f"Refusing an unmanaged build-host sudoers file: {username}")
            marker["principals"][principal] = {
                "username": username, "uid": None, "gid": None,
                "key": None, "managed": False, "auth_managed": False,
                "sudo_managed": False, "sudo_hashes": [],
            }
    _write_marker(marker)

    for principal, record in sorted(marker["principals"].items()):
        if principal in desired_principals:
            _ensure_transport_account(principal, record, marker)
        else:
            # Retired accounts remain locked and identity-stable; only this
            # setup's key and sudo authority are revoked.
            if record["managed"]:
                _require_exclusive_transport_account(
                    record["username"],
                    SSH_ROOT + "/" + record["username"].removeprefix("bw-build-"),
                    record["uid"], record["gid"],
                )
        if record["managed"]:
            home = SSH_ROOT + "/" + record["username"].removeprefix("bw-build-")
            _ensure_directory(home, mode=SSH_HOME_MODE)
            _ensure_directory(home + "/.ssh", mode=SSH_DIRECTORY_MODE)

    # Remove sudo authority before removing a revoked key. New identities gain
    # the fixed sudo command before their forced-command key is published.
    for principal, record in sorted(marker["principals"].items()):
        desired = enrollment["keys"].get(principal)
        if desired is None:
            _reconcile_sudoers(principal, record, None, marker)
            _reconcile_authorized_key(principal, record, None, marker)
        else:
            _reconcile_sudoers(principal, record, desired, marker)
            _reconcile_authorized_key(principal, record, desired, marker)
        _write_marker(marker)

    existing_config = _read_file(CONFIG_PATH, mode=0o600, maximum=MAX_ENROLLMENT_BYTES)
    if existing_config is not None and _hash(existing_config) not in marker["files"]["config"]:
        raise ValueError("Refusing an unmanaged or modified build-host configuration file")
    _write_file(CONFIG_PATH, config_content, mode=0o600)
    marker["files"]["config"] = [_hash(config_content)]
    _write_marker(marker)
    # The worker lease is still held, the store drained and the unit inactive.
    from security.security_steps import refresh_transport_ssh_admission

    users = [_transport_username(principal) for principal in enrollment["keys"]]
    content = admission_content("build-host", users)
    # Creation can fail before publication (or after rename but before fsync).
    # Record that specific absent/present transition before publishing its hash.
    if not marker["files"]["ssh"] or marker["ssh_pending"] is not None:
        marker["ssh_pending"] = {"action": "create", "sha256": _hash(content)}
    marker["files"]["ssh"] = sorted(set(marker["files"]["ssh"] + [_hash(content)]))[-2:]
    _write_marker(marker)
    _write_file(SSH_ADMISSION_PATH, content, mode=0o600)
    refresh_transport_ssh_admission("build-host", users)
    marker["files"]["ssh"] = [_hash(content)]
    marker["ssh_pending"] = None
    _write_marker(marker)
    return config_content


def _systemd_properties() -> dict[str, str]:
    result = run(["systemctl", "show", SERVICE_NAME,
                  "--property=FragmentPath,LoadState,ActiveState,UnitFileState", "--no-pager"],
                 check=False, capture_output=True, text=True)
    if result.returncode not in {0, 3, 4}:
        raise RuntimeError("Could not inspect the managed build-host service")
    return dict(line.split("=", 1) for line in (result.stdout or "").splitlines() if "=" in line)


def _quiesce_missing_managed_unit() -> None:
    """Stop a cached managed service when its marker-owned unit file is absent."""
    state = _systemd_properties()
    fragment = state.get("FragmentPath", "")
    if fragment and fragment != SERVICE_UNIT:
        raise ValueError(f"Refusing an unmanaged systemd unit: {SERVICE_NAME}")
    active = state.get("ActiveState")
    file_state = state.get("UnitFileState")
    if active not in {"active", "activating", "inactive", "failed", ""}:
        raise RuntimeError("Build-host service state is not safe to disable")
    if file_state not in {"", "enabled", "enabled-runtime", "disabled", "static", "indirect", "not-found"}:
        raise RuntimeError("Build-host service enablement state is not safe to disable")
    if active in {"active", "activating"}:
        if fragment != SERVICE_UNIT:
            raise ValueError("Refusing to stop a build-host service without its managed unit file")
        run(["systemctl", "stop", SERVICE_NAME])
    if file_state in {"enabled", "enabled-runtime"}:
        run(["systemctl", "disable", SERVICE_NAME])
    after = _systemd_properties()
    if (after.get("ActiveState") not in {"inactive", "failed", ""}
            or after.get("UnitFileState") in {"enabled", "enabled-runtime"}):
        raise RuntimeError("Managed build-host service did not stop and disable")


def _preflight_disable(marker: dict[str, Any]) -> None:
    """Verify ownership records and persistent roots before draining the worker."""
    _preflight_ssh_admission(marker)
    pending = {} if marker["pending"] is None else marker["pending"]["keys"]
    _validate_transport_namespace(set(marker["principals"]) | set(pending))
    _trusted_path(ETC_DIR, final_directory=True)
    info = Path(ETC_DIR).lstat()
    if (info.st_uid != 0 or info.st_gid != 0 or stat.S_IMODE(info.st_mode) != 0o700):
        raise ValueError("Managed build-host configuration directory must be root-owned 0700")

    for root in (DATA_ROOT, *ROOTS.values(), SSH_ROOT):
        _trusted_path(root, final_directory=True)
        root_info = Path(root).lstat()
        expected_mode = SSH_ROOT_MODE if root == SSH_ROOT else 0o700
        if (root_info.st_uid != 0 or root_info.st_gid != 0
                or stat.S_IMODE(root_info.st_mode) != expected_mode):
            raise ValueError(f"Managed build-host directory has an unsupported owner or mode: {root}")

    worker = marker["worker"]
    account, group = _get_account(BUILD_ACCOUNT), _get_group(BUILD_ACCOUNT)
    if worker is None:
        if account is not None or group is not None:
            raise ValueError("Refusing an unmanaged basaltwater-build account or group")
    elif account is not None and group is not None:
        from common.service_tools import build_host as entrypoint
        actual = {"uid": account.pw_uid, "gid": account.pw_gid}
        if worker["uid"] is not None and worker != actual:
            raise ValueError("Managed build-host worker identity changed")
        entrypoint.require_build_account(actual)
        marker["worker"] = actual
    # Missing or partially created managed worker identities are retained as
    # evidence. Teardown does not recreate or delete either account resource.

    pending_keys = {} if marker["pending"] is None else marker["pending"]["keys"]
    principal_names = set(marker["principals"]) | set(pending_keys)
    for principal in sorted(principal_names):
        username = _transport_username(principal)
        record = marker["principals"].get(principal)
        if record is None:
            if (_get_account(username) is not None or _get_group(username) is not None
                    or os.path.lexists(SUDOERS_DIR + "/" + username)
                    or os.path.lexists(SSH_ROOT + "/" + username.removeprefix("bw-build-"))):
                raise ValueError(f"Refusing unmanaged build-host principal resources: {principal}")
            continue

        account, group = _get_account(username), _get_group(username)
        home = SSH_ROOT + "/" + username.removeprefix("bw-build-")
        pending_intent = principal in pending_keys
        if record["managed"]:
            if account is not None and group is not None:
                _require_exclusive_transport_account(username, home, record["uid"], record["gid"])
        elif account is not None and group is not None and pending_intent:
            uid, gid = _require_exclusive_transport_account(username, home, record["uid"], record["gid"])
            record.update(uid=uid, gid=gid, managed=True)
        elif account is not None or (group is not None and not pending_intent):
            raise ValueError(f"Refusing an unmanaged build-host transport identity: {username}")

        sudo_path = SUDOERS_DIR + "/" + username
        if os.path.lexists(sudo_path):
            sudo_content = _read_file(sudo_path, mode=0o440, maximum=64 * 1024)
            if not record["sudo_managed"]:
                raise ValueError(f"Refusing an unmanaged build-host sudoers file: {username}")
            if _hash(sudo_content or b"") not in record["sudo_hashes"]:
                raise ValueError(f"Refusing a modified managed build-host sudoers file: {username}")
        key_path = home + "/.ssh/authorized_keys"
        if os.path.lexists(key_path):
            _trusted_path(os.path.dirname(key_path), final_directory=True)
            _read_file(key_path, mode=AUTHORIZED_KEYS_MODE, maximum=64 * 1024)
            if not record["auth_managed"]:
                raise ValueError(f"Refusing an unmanaged build-host authorized_keys file: {username}")

    config_content = (_read_file(CONFIG_PATH, mode=0o600, maximum=MAX_ENROLLMENT_BYTES)
                      if os.path.lexists(ETC_DIR) else None)
    if config_content is not None and _hash(config_content) not in marker["files"]["config"]:
        raise ValueError("Refusing an unmanaged or modified build-host configuration file")
    unit_content = _read_file(SERVICE_UNIT, mode=0o644, maximum=1024 * 1024)
    if unit_content is not None and _hash(unit_content) not in marker["files"]["unit"]:
        raise ValueError(f"Refusing an unmanaged or modified systemd unit: {SERVICE_NAME}")


def _preflight_ssh_admission(marker: dict[str, Any]) -> None:
    content = (_read_file(SSH_ADMISSION_PATH, mode=0o600, maximum=_MARKER_LIMIT)
               if os.path.lexists(ETC_DIR) else None)
    hashes = marker["files"]["ssh"]
    missing_in_transition = content is None and marker["ssh_pending"] is not None
    if ((content is None) != (not hashes) and not missing_in_transition
            or content is not None and _hash(content) not in hashes):
        raise ValueError("Unowned, missing or modified build-host SSH admission")


def _revoke_managed_access(marker: dict[str, Any]) -> None:
    from security.security_steps import refresh_transport_ssh_admission

    _preflight_ssh_admission(marker)
    refresh_transport_ssh_admission("build-host", [])
    if marker["files"]["ssh"]:
        content = _read_file(SSH_ADMISSION_PATH, mode=0o600, maximum=_MARKER_LIMIT)
        digest = _hash(content) if content is not None else marker["ssh_pending"]["sha256"]
        if digest not in marker["files"]["ssh"]:
            raise ValueError("Refusing to remove modified build-host SSH admission")
        marker["files"]["ssh"] = [digest]
        marker["ssh_pending"] = {"action": "remove", "sha256": digest}
        _write_marker(marker)
        _remove_file(SSH_ADMISSION_PATH, mode=0o600, maximum=_MARKER_LIMIT)
        marker["files"]["ssh"] = []
        marker["ssh_pending"] = None
        _write_marker(marker)
    pending_keys = {} if marker["pending"] is None else marker["pending"]["keys"]
    for principal, record in sorted(marker["principals"].items()):
        if not record["managed"] and not (record["auth_managed"] or record["sudo_managed"]):
            continue
        _reconcile_sudoers(principal, record, None, marker)
        pending_key = pending_keys.get(principal)
        extra = () if pending_key is None else (pending_key,)
        _reconcile_authorized_key(principal, record, None, marker, extra_keys=extra)
        record["key"] = None
        record["sudo_hashes"] = []
        _write_marker(marker)

    content = _read_file(CONFIG_PATH, mode=0o600, maximum=MAX_ENROLLMENT_BYTES)
    if content is not None:
        if _hash(content) not in marker["files"]["config"]:
            raise ValueError("Refusing to remove an unmanaged build-host configuration file")
        _remove_file(CONFIG_PATH, mode=0o600, maximum=MAX_ENROLLMENT_BYTES)
    marker["files"]["config"] = []
    marker["pending"] = None
    marker["disabled"] = True
    _write_marker(marker)


def _has_unmarked_build_resources() -> bool:
    if any(os.path.lexists(path) for path in (SERVICE_UNIT, DATA_ROOT, SSH_ROOT)):
        return True
    if _get_account(BUILD_ACCOUNT) is not None or _get_group(BUILD_ACCOUNT) is not None:
        return True
    if (any(account.pw_name.startswith("bw-build-") for account in pwd.getpwall())
            or any(group.gr_name.startswith("bw-build-") for group in grp.getgrall())):
        return True
    if os.path.lexists(SUDOERS_DIR):
        _trusted_path(SUDOERS_DIR, final_directory=True)
        with os.scandir(SUDOERS_DIR) as entries:
            if any(entry.name.startswith("bw-build-") for entry in entries):
                return True
    return False


def _reject_legacy_layout() -> None:
    if os.path.lexists(_LEGACY_SSH_ROOT):
        raise ValueError(_LEGACY_LAYOUT_GUIDANCE + ": " + _LEGACY_SSH_ROOT)


def _require_ssh_traversal() -> None:
    """Validate public transport ancestors without widening private state."""
    _trusted_path(SSH_ROOT, final_directory=True, allow_missing=True)
    for ancestor in reversed(Path(SSH_ROOT).parents):
        try:
            info = ancestor.lstat()
        except FileNotFoundError:
            # Newly created shared parents get exact root:root 0755 modes.
            break
        if (not info.st_mode & stat.S_IXOTH
                or info.st_gid != 0 and not info.st_mode & stat.S_IXGRP):
            raise ValueError("Build-host SSH ancestor is not traversable by transport identities: " + str(ancestor))


def _disable_build_host(config: SetupConfig) -> None:
    machine_type = getattr(config, "machine_type", None)
    from lib.machine_state import can_manage_system_services
    if not can_manage_system_services(machine_type):
        raise RuntimeError("Cannot disable the build-host role where system services cannot be managed")
    if bool(getattr(config, "dry_run", False)) or is_dry_run():
        print("  [DRY-RUN] Would drain and disable the managed build-host service")
        return
    if machine_type not in {"vm", "hardware"}:
        raise RuntimeError(
            "Build-host role teardown is supported only on VM or physical hosts; "
            "unprivileged and privileged LXC are unsupported"
        )

    _reject_legacy_layout()
    if not os.path.lexists(ETC_DIR):
        if _has_unmarked_build_resources():
            raise ValueError("Refusing build-host resources without their ownership marker")
        print("  ✓ No managed build-host service is installed")
        return
    raw_marker = _read_file(MARKER_PATH, mode=0o600, maximum=_MARKER_LIMIT)
    if raw_marker is None:
        raise ValueError("Refusing an unmanaged build-host configuration directory")
    marker = _parse_marker(raw_marker)
    _preflight_disable(marker)
    _verify_unit_source()

    from lib.build_jobs import BuildJobStore
    from lib.unit_transaction import remove_units

    store = BuildJobStore(os.path.join(ROOTS["state"], "jobs.json"))
    _store_snapshot(store)
    store.set_draining(True)
    lease_context = store.worker()
    lease_held = False
    try:
        try:
            lease_context.__enter__()
            lease_held = True
        except RuntimeError as exc:
            raise RuntimeError(
                "Build-host worker is active; drain is retained and teardown will not stop it"
            ) from exc
        _draining, running = _store_snapshot(store)
        if running:
            raise RuntimeError(
                "Build-host setup refuses a retained running claim; drain is retained and worker was not stopped"
            )

        marker["disabled"] = True
        _write_marker(marker)
        if not os.path.lexists(SERVICE_UNIT):
            _quiesce_missing_managed_unit()

        cleanup_done = False

        def prepare() -> None:
            nonlocal cleanup_done
            _revoke_managed_access(marker)
            cleanup_done = True

        remove_units((SERVICE_NAME,), prepare=prepare)
        if not cleanup_done:
            # remove_units skips prepare when an already-removed managed unit
            # has no file. The service is quiescent and the worker lease is
            # still held, so finish the same ownership-scoped cleanup here.
            _revoke_managed_access(marker)
        marker["files"]["unit"] = []
        marker["pending"] = None
        marker["disabled"] = True
        _write_marker(marker)
        # Disabling intentionally leaves queued work held for an explicit
        # later re-enrollment; jobs, artifacts, and evidence are untouched.
        store.set_draining(True)
    except BaseException:
        # A failed de-enrollment stays drained. This prevents queued work from
        # entering a partially revoked host before the same operation is retried.
        raise
    finally:
        if lease_held:
            lease_context.__exit__(None, None, None)


def configure_build_host(config: SetupConfig) -> None:
    """Enable or reconcile the independently managed Debian build-host service."""
    disable = getattr(config, "disable_build_host", False)
    if type(disable) is not bool:
        raise ValueError("Build-host disable selection must be boolean")
    if disable:
        _disable_build_host(config)
        return
    raw = getattr(config, "build_host_enrollment", None)
    if raw is None:
        return
    if not isinstance(raw, Mapping):
        raise ValueError("Build-host enrollment must already be a decoded mapping")
    enrollment = parse_build_host_enrollment(raw)

    machine_type = getattr(config, "machine_type", None)
    from lib.machine_state import can_manage_system_services
    if not can_manage_system_services(machine_type):
        print("  ✓ Skipping build-host service (system services unavailable)")
        return
    if bool(getattr(config, "dry_run", False)) or is_dry_run():
        print("  [DRY-RUN] Would configure the Debian build-host service and enrollment")
        return
    if machine_type not in {"vm", "hardware"}:
        raise RuntimeError(
            "On-demand builds are qualified only on VM or physical Debian 13 x86-64 hosts; "
            "unprivileged and privileged LXC are unsupported"
        )
    if type(getattr(config, "install_node", False)) is not bool:
        raise ValueError("Build-host Node installation selection must be boolean")
    if type(getattr(config, "install_go", False)) is not bool or type(
            getattr(config, "install_python", False)) is not bool:
        raise ValueError("Build-host tool installation selections must be boolean")

    from common.service_tools import build_host as entrypoint
    entrypoint.require_platform()
    _trusted_source_tree()
    _reject_legacy_layout()

    config_dir_exists = os.path.lexists(ETC_DIR)
    initial = _read_file(MARKER_PATH, mode=0o600, maximum=_MARKER_LIMIT) if config_dir_exists else None
    marker_present = initial is not None
    if config_dir_exists and not marker_present:
        raise ValueError("Refusing an unmanaged build-host configuration directory")
    if marker_present:
        marker = _parse_marker(initial)
    else:
        marker = _empty_marker()
    _initial_install_conflicts(marker_present, set(enrollment["keys"]))
    digest = _hash(encode_document(enrollment, maximum=MAX_ENROLLMENT_BYTES))
    _preflight_managed_state(marker, enrollment, digest)
    _verify_unit_source()
    _trusted_path(SUDOERS_DIR, final_directory=True, allow_missing=True)

    # A managed marker makes partial first-install directory creation
    # recoverable; all persistent build data remains in its original place.
    if not marker_present:
        _ensure_directory(ETC_DIR, mode=0o700)
        _write_marker(marker, exclusive=True)
    else:
        config_dir_info = Path(ETC_DIR).lstat()
        if (config_dir_info.st_uid != 0 or config_dir_info.st_gid != 0
                or stat.S_IMODE(config_dir_info.st_mode) != 0o700):
            raise ValueError("Managed build-host configuration directory must be root-owned 0700")
    _ensure_runtime_directories()

    job_store_type = __import__("lib.build_jobs", fromlist=["BuildJobStore"]).BuildJobStore
    store = job_store_type(os.path.join(ROOTS["state"], "jobs.json"))
    prior_draining, _running = _store_snapshot(store)
    was_disabled = marker["disabled"]
    has_pending_reconciliation = marker["pending"] is not None
    store.set_draining(True)

    lease_context = store.worker()
    lease_held = False
    changes_started = has_pending_reconciliation
    try:
        try:
            lease_context.__enter__()
            lease_held = True
        except RuntimeError as exc:
            raise RuntimeError("Build-host worker is active; setup left it running and made no changes") from exc
        _draining, running = _store_snapshot(store)
        if running:
            changes_started = True
            raise RuntimeError("Build-host setup refuses a retained running claim; worker was not stopped")

        unit_text = _service_content()
        unit_content = unit_text.encode("utf-8")

        def prepare() -> None:
            nonlocal lease_held, changes_started
            changes_started = True
            try:
                _prepare_enrollment(config, enrollment, marker, digest, unit_content)
            finally:
                if lease_held:
                    lease_context.__exit__(None, None, None)
                    lease_held = False

        from lib.unit_transaction import replace_units
        replace_units({SERVICE_NAME: unit_text}, activate=(SERVICE_NAME,),
                      deactivate=(SERVICE_NAME,), prepare=prepare)

        marker["files"]["unit"] = [_hash(unit_content)]
        for principal, record in marker["principals"].items():
            record["key"] = enrollment["keys"].get(principal)
            record["managed"] = True
        marker["pending"] = None
        marker["disabled"] = False
        _write_marker(marker)
        store.set_draining(False if was_disabled else prior_draining)
    except BaseException:
        if lease_held:
            lease_context.__exit__(None, None, None)
            lease_held = False
        if not changes_started:
            try:
                store.set_draining(prior_draining)
            except Exception:
                pass
        raise


__all__ = ["configure_build_host", "parse_build_host_enrollment"]
