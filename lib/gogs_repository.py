"""Configure GitHub repositories to use a Gogs mirror and LFS endpoint."""

from __future__ import annotations

import os
import re
import shlex
import stat
import subprocess
from urllib.parse import urlsplit

from lib.git_credentials import normalize_git_https_origin
from lib.types import JSONDict, NestedStrList, StrList
from lib.validation import validate_filesystem_path, validate_no_control_characters


_REMOTE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_REPOSITORY_PATH_PATTERN = re.compile(r"^/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
_REMOTE_FILESYSTEMS = {
    "9p",
    "afs",
    "ceph",
    "cifs",
    "fuse.rclone",
    "fuse.sshfs",
    "nfs",
    "nfs4",
    "smb2",
    "smb3",
    "sshfs",
}


def normalize_repository_url(value: str, *, label: str, github: bool = False) -> str:
    """Validate and normalize one credential-free HTTPS repository URL."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{label} must be a non-empty HTTPS repository URL")
    validate_no_control_characters(value, label)
    parsed = urlsplit(value)
    if parsed.scheme.lower() != "https":
        raise ValueError(f"{label} must use https://")
    if (
        not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError(f"{label} must not contain embedded credentials")
    if parsed.query or parsed.fragment:
        raise ValueError(f"{label} must not contain a query or fragment")

    origin = normalize_git_https_origin(f"https://{parsed.netloc}")
    if github and origin != "https://github.com":
        raise ValueError("GitHub repository URL must use https://github.com")

    path = parsed.path.rstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    if not _REPOSITORY_PATH_PATTERN.fullmatch(path):
        raise ValueError(
            f"{label} path must contain an owner and repository using safe characters"
        )
    if any(part in {".", ".."} for part in path.split("/")):
        raise ValueError(f"{label} path must not contain dot segments")
    return f"{origin}{path}.git"


def _run(
    command: StrList,
    *,
    timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "command failed"
        raise RuntimeError(f"{shlex.join(command)}: {detail}")
    return result


def _git(repository: str, arguments: StrList) -> subprocess.CompletedProcess[str]:
    return _run(["git", "-C", repository, *arguments])


def _validate_config_target(path: str) -> None:
    if not os.path.lexists(path):
        return
    mode = os.lstat(path).st_mode
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise ValueError(
            f"Repository configuration target must be a regular file: {path}"
        )


def _validate_tracking_patterns(patterns: StrList) -> StrList:
    normalized: StrList = []
    for pattern in patterns:
        if not isinstance(pattern, str) or not pattern or pattern != pattern.strip():
            raise ValueError("Git LFS tracking patterns must be non-empty")
        validate_no_control_characters(pattern, "Git LFS tracking pattern")
        if pattern.startswith("-"):
            raise ValueError("Git LFS tracking patterns must not start with '-'")
        if pattern not in normalized:
            normalized.append(pattern)
    return normalized


def configure_github_gogs_repository(
    repository: str,
    github_url: str,
    gogs_url: str,
    *,
    mirror_remote: str = "gogs",
    track_patterns: StrList | None = None,
    combined_push: bool = True,
    dry_run: bool = False,
) -> JSONDict:
    """Configure one clean local repository for GitHub, Gogs, and Gogs LFS."""
    root = os.path.realpath(os.path.abspath(os.path.expanduser(repository)))
    validate_filesystem_path(root, must_exist=True, check_writable=True)
    if not os.path.isdir(root):
        raise ValueError(f"Repository path must be a directory: {root}")
    github = normalize_repository_url(
        github_url,
        label="GitHub repository URL",
        github=True,
    )
    gogs = normalize_repository_url(gogs_url, label="Gogs repository URL")
    if github == gogs:
        raise ValueError("GitHub and Gogs repository URLs must be different")
    if not _REMOTE_NAME_PATTERN.fullmatch(mirror_remote) or mirror_remote == "origin":
        raise ValueError("Gogs mirror remote must be a safe name other than 'origin'")
    patterns = _validate_tracking_patterns(track_patterns or [])

    discovered_root = _git(root, ["rev-parse", "--show-toplevel"]).stdout.strip()
    if os.path.realpath(discovered_root) != root:
        raise ValueError("Repository path must be the root of a Git worktree")
    if _git(root, ["status", "--porcelain", "--untracked-files=all"]).stdout.strip():
        raise ValueError("Repository worktree must be clean before configuration")

    filesystem = _run(
        ["findmnt", "-n", "-o", "FSTYPE", "--target", root]
    ).stdout.strip().lower()
    if not filesystem:
        raise RuntimeError(f"Could not determine the filesystem for {root}")
    if filesystem in _REMOTE_FILESYSTEMS or filesystem.startswith("nfs"):
        raise ValueError(f"Repository worktree must use local storage, not {filesystem}")

    _git(root, ["lfs", "version"])
    _git(root, ["lfs", "env"])
    for target_name in (".lfsconfig", ".gitattributes"):
        _validate_config_target(os.path.join(root, target_name))

    lfs_url = f"{gogs}/info/lfs"
    remotes = set(_git(root, ["remote"]).stdout.splitlines())
    actions: NestedStrList = [["lfs", "install", "--local"]]
    actions.append(
        ["remote", "set-url", "origin", github]
        if "origin" in remotes
        else ["remote", "add", "origin", github]
    )
    actions.append(
        ["remote", "set-url", mirror_remote, gogs]
        if mirror_remote in remotes
        else ["remote", "add", mirror_remote, gogs]
    )
    actions.append(["config", "--replace-all", "remote.origin.pushurl", github])
    if combined_push:
        actions.append(["config", "--add", "remote.origin.pushurl", gogs])
    actions.append(
        ["config", "--file", os.path.join(root, ".lfsconfig"), "lfs.url", lfs_url]
    )
    actions.extend(["lfs", "track", pattern] for pattern in patterns)

    if not dry_run:
        for arguments in actions:
            _git(root, arguments)

    return {
        "repository": root,
        "filesystem": filesystem,
        "github_url": github,
        "gogs_url": gogs,
        "lfs_url": lfs_url,
        "mirror_remote": mirror_remote,
        "combined_push": combined_push,
        "track_patterns": patterns,
        "dry_run": dry_run,
        "actions": [shlex.join(["git", "-C", root, *arguments]) for arguments in actions],
    }
