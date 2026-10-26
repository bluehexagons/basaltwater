"""Exact primary-group SSH admission owned by the build role installers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import fcntl
import hashlib
import os
import re
import stat

from lib.build_target_maintenance import read_protected, trusted_path
from lib.setup_directories import directory_fd
from lib.validators import validate_username


@contextmanager
def ssh_admission_lock(parent: str) -> Iterator[None]:
    """Serialize shared daemon policy edits; retain the protected lock inode."""
    trusted_path(parent, directory=True)
    with directory_fd(parent) as parent_fd:
        descriptor = os.open(".basaltwater-ssh-admission.lock",
                             os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                             0o600, dir_fd=parent_fd)
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != 0
                    or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
                raise ValueError("Unsafe shared SSH admission lock")
            os.fsync(parent_fd)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("Managed SSH admission update is active; retry later") from exc
            yield
        finally:
            os.close(descriptor)


def admission_content(role: str, usernames: list[str]) -> bytes:
    """Encode literal transport groups; never accept OpenSSH pattern syntax."""
    prefixes = {"build-host": "bw-build-", "build-target": "bw-target-"}
    if role not in prefixes:
        raise ValueError("Unknown managed SSH role")
    for username in usernames:
        if (not validate_username(username)
                or re.fullmatch(prefixes[role] + r"[0-9a-f]{12}", username) is None):
            raise ValueError("Invalid managed SSH admission group")
    return ("".join(name + "\n" for name in sorted(set(usernames)))).encode("ascii")


def managed_transport_groups(*, exclude_role: str | None = None,
                             require_admission: bool = True) -> list[str]:
    """Read completed, marker-owned grants without migrating an installation.

    Installers exclude their own in-progress role while holding its existing
    maintenance ownership, and supply its freshly validated identities directly.
    Hardening only consumes admission already published by those installers.
    A refresh may leave another pre-migration role unadmitted, so the roles can
    be migrated sequentially without implicitly granting the other's access.
    """
    from common import build_host_steps as host, build_target_steps as target

    groups = []
    for role, owner in (("build-host", host), ("build-target", target)):
        if role == exclude_role:
            continue
        raw = read_protected(owner.MARKER_PATH, mode=0o600, maximum=owner._MARKER_LIMIT)
        if raw is None:
            continue
        marker = owner._parse_marker(raw)
        if role == "build-host":
            if marker["pending"] is not None:
                raise RuntimeError("Incomplete build-host SSH admission requires reconciliation")
            users = [] if marker["disabled"] else [
                record["username"] for record in marker["principals"].values()
                if record["key"] is not None
            ]
            hashes = marker["files"].get("ssh", [])
        else:
            users = [] if marker["state"] == "disabled" else [
                target._username(principal) for principal in marker["principals"]
                if target._file_paths(principal)[1] in marker["files"]
            ]
            digest = marker["files"].get(owner.SSH_ADMISSION_PATH)
            hashes = [] if digest is None else [digest]
        content = read_protected(owner.SSH_ADMISSION_PATH, mode=0o600, maximum=owner._MARKER_LIMIT)
        if users and not hashes:
            if require_admission:
                raise RuntimeError(f"Rerun {role} enrollment under maintenance to establish SSH admission")
            if content is None:
                continue
        if ((content is None) != (not hashes)
                or content is not None and (
                    hashlib.sha256(content).hexdigest() not in hashes
                    or content != admission_content(role, users))):
            raise ValueError(f"Unowned, missing or modified {role} SSH admission")
        for principal, identity in marker["principals"].items():
            username = host._transport_username(principal) if role == "build-host" else target._username(principal)
            if username not in users:
                continue
            if role == "build-host":
                if not identity["managed"] or identity["uid"] is None or identity["gid"] is None:
                    raise ValueError("Incomplete managed SSH transport identity")
                host._require_exclusive_transport_account(
                    username, host.SSH_ROOT + "/" + username.removeprefix("bw-build-"),
                    identity["uid"], identity["gid"],
                )
            else:
                target._identity(principal, identity)
        groups.extend(users)
    return sorted(groups)
