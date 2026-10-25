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


def _read_git_config_values(
    repository: str,
    key: str,
    *,
    config_file: str | None = None,
    all_values: bool = False,
) -> list[str]:
    """Read config without confusing an absent key with a failed query."""
    arguments = ["config"]
    if config_file is not None:
        arguments.extend(["--file", config_file])
    arguments.extend(["--null", "--get-all" if all_values else "--get", key])
    result = subprocess.run(
        ["git", "-C", repository, *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode == 1:
        return []
    if result.returncode != 0:
        raise RuntimeError(
            "Could not inspect Git LFS endpoint configuration; "
            "the git config query failed"
        )

    values = result.stdout.split("\0") if result.stdout else []
    if values and values[-1] == "":
        values.pop()
    if not values or (not all_values and len(values) != 1):
        raise RuntimeError(
            "Could not inspect Git LFS endpoint configuration; "
            "the git config query returned invalid or ambiguous data"
        )
    return values


def _git_config_target(repository: str) -> str:
    """Return and validate the repository config file local writes update."""
    result = _git(repository, ["rev-parse", "--git-path", "config"])
    output = result.stdout
    if not output.endswith("\n") or output.count("\n") != 1:
        raise RuntimeError("Could not determine the repository Git config target")
    path = output[:-1]
    if not path:
        raise RuntimeError("Could not determine the repository Git config target")
    if not os.path.isabs(path):
        path = os.path.join(repository, path)
    path = os.path.abspath(path)
    if any(ord(character) < 32 or ord(character) == 127 for character in path):
        raise ValueError("Repository Git config target contains control characters")
    _validate_config_target(path)
    return path


def _read_git_config_origins(
    repository: str,
    key: str,
) -> list[tuple[str, str]]:
    """Read each effective config value with its source, without leaking errors."""
    result = subprocess.run(
        [
            "git",
            "-C",
            repository,
            "config",
            "--null",
            "--show-origin",
            "--get-all",
            key,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode == 1:
        return []
    if result.returncode != 0:
        raise RuntimeError(
            "Could not inspect Git push URL configuration; "
            "the git config query failed"
        )

    fields = result.stdout.split("\0") if result.stdout else []
    if fields and fields[-1] == "":
        fields.pop()
    if not fields or len(fields) % 2:
        raise RuntimeError(
            "Could not inspect Git push URL configuration; "
            "the git config query returned invalid data"
        )
    entries = list(zip(fields[::2], fields[1::2]))
    if any(not origin for origin, _value in entries):
        raise RuntimeError(
            "Could not inspect Git push URL configuration; "
            "the git config query returned invalid data"
        )
    return entries


def _config_origin_path(origin: str, repository: str) -> str | None:
    """Resolve a file origin for comparison with the writable local config."""
    if not origin.startswith("file:"):
        return None
    path = origin[len("file:") :]
    if not path or any(
        ord(character) < 32 or ord(character) == 127 for character in path
    ):
        raise RuntimeError(
            "Could not inspect Git push URL configuration; "
            "the git config query returned invalid data"
        )
    if not os.path.isabs(path):
        path = os.path.join(repository, path)
    return os.path.normcase(os.path.abspath(path))


def _validate_push_url_configuration(
    repository: str,
    config_target: str,
    mirror_remote: str,
    github_url: str,
    gogs_url: str,
    *,
    combined_push: bool,
) -> None:
    """Reject inherited push destinations that local replacement cannot remove."""
    writable_config = os.path.normcase(os.path.abspath(config_target))
    expected_by_key = {
        "remote.origin.pushurl": (
            {github_url, gogs_url} if combined_push else {github_url}
        ),
        f"remote.{mirror_remote}.pushurl": {gogs_url},
    }
    for key, expected_destinations in expected_by_key.items():
        for origin, value in _read_git_config_origins(repository, key):
            origin_path = _config_origin_path(origin, repository)
            if origin_path == writable_config or value in expected_destinations:
                continue
            raise ValueError(
                f"Git push URL setting '{key}' has an inherited destination "
                "outside the intended destinations; its value is not shown"
            )


def _normalize_lfs_endpoint(value: str) -> str | None:
    """Canonicalize a credential-free HTTPS LFS endpoint for comparison."""
    if not value or value != value.strip():
        return None
    try:
        validate_no_control_characters(value, "Git LFS endpoint")
        parsed = urlsplit(value)
        if (
            parsed.scheme.lower() != "https"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            return None
        origin = normalize_git_https_origin(f"https://{parsed.netloc}")
    except (ValueError, UnicodeError):
        return None
    return f"{origin}{parsed.path.rstrip('/')}"


def _validate_lfs_endpoint_values(
    key: str,
    values: list[str],
    expected_endpoint: str,
) -> None:
    expected = _normalize_lfs_endpoint(expected_endpoint)
    for value in values:
        endpoint = _normalize_lfs_endpoint(value)
        if endpoint is None:
            raise ValueError(
                f"Git LFS endpoint setting '{key}' is invalid; its value is not shown"
            )
        if endpoint != expected:
            raise ValueError(
                f"Git LFS endpoint setting '{key}' conflicts with the intended "
                "Gogs endpoint; its value is not shown"
            )


def _validate_lfs_endpoint_configuration(
    repository: str,
    lfsconfig_path: str,
    mirror_remote: str,
    expected_endpoint: str,
) -> None:
    """Reject endpoint overrides that would route LFS away from this Gogs repo."""
    for key in ("lfs.url", "lfs.pushurl"):
        values = _read_git_config_values(repository, key)
        _validate_lfs_endpoint_values(key, values, expected_endpoint)

    for remote in dict.fromkeys(("origin", mirror_remote)):
        for setting in ("lfsurl", "lfspushurl"):
            key = f"remote.{remote}.{setting}"
            values = _read_git_config_values(repository, key)
            _validate_lfs_endpoint_values(key, values, expected_endpoint)

    if os.path.lexists(lfsconfig_path):
        # Query both keys before replacing lfs.url so malformed config fails
        # before any repository action. .lfsconfig is the helper-managed file;
        # its download URL is replaced, while an upload override is preserved
        # only when it already names the intended endpoint.
        _read_git_config_values(
            repository,
            "lfs.url",
            config_file=lfsconfig_path,
            all_values=True,
        )
        push_urls = _read_git_config_values(
            repository,
            "lfs.pushurl",
            config_file=lfsconfig_path,
            all_values=True,
        )
        _validate_lfs_endpoint_values(
            ".lfsconfig lfs.pushurl",
            push_urls,
            expected_endpoint,
        )


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
    if os.environ.get("GIT_CONFIG"):
        raise ValueError(
            "GIT_CONFIG redirects Git config writes; unset it before configuring"
        )

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

    for target_name in (".lfsconfig", ".gitattributes"):
        _validate_config_target(os.path.join(root, target_name))

    lfs_url = f"{gogs}/info/lfs"
    lfsconfig_path = os.path.join(root, ".lfsconfig")
    config_target = _git_config_target(root)
    _validate_push_url_configuration(
        root,
        config_target,
        mirror_remote,
        github,
        gogs,
        combined_push=combined_push,
    )
    remotes = set(_git(root, ["remote"]).stdout.splitlines())
    _validate_lfs_endpoint_configuration(
        root,
        lfsconfig_path,
        mirror_remote,
        lfs_url,
    )
    _git(root, ["lfs", "version"])
    _git(root, ["lfs", "env"])
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
        [
            "config",
            "--file",
            lfsconfig_path,
            "--replace-all",
            "lfs.url",
            lfs_url,
        ]
    )
    actions.append(
        ["config", "--replace-all", f"remote.{mirror_remote}.pushurl", gogs]
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
