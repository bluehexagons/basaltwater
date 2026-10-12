"""Non-secret provenance for installed basaltwater source snapshots."""

from __future__ import annotations

import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
from importlib import metadata as importlib_metadata
from typing import Any

from lib.atomic_io import read_json_file, write_json_atomic
from lib.agent_probe import run_probe
from lib.types import JSONDict
from lib.validation import validate_filesystem_path


INSTALLATION_METADATA_FILENAME = ".basaltwater-installation.json"
REFRESH_METADATA_FILENAME = ".basaltwater-refresh.json"
INSTALLATION_METADATA_SCHEMA = 1
SETUP_SNAPSHOT_TYPE = "setup-snapshot"
_COMMIT_RE = re.compile(r"[0-9a-f]{40,64}")
_VERSION_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+-]*")


def _project_version(project_root: str) -> str:
    """Read the project version without requiring an installed wheel."""

    pyproject_path = os.path.join(project_root, "pyproject.toml")
    try:
        descriptor = os.open(pyproject_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as file_obj:
            if not stat.S_ISREG(os.fstat(file_obj.fileno()).st_mode):
                raise ValueError("Project version metadata must be regular")
            body = file_obj.read(65537)
            if len(body) > 65536:
                raise ValueError("Project version metadata exceeds its size limit")
            in_project = False
            for raw_line in body.decode("utf-8").splitlines():
                line = raw_line.strip()
                if line.startswith("[") and line.endswith("]"):
                    in_project = line == "[project]"
                    continue
                if not in_project:
                    continue
                match = re.fullmatch(
                    r"version\s*=\s*['\"]([^'\"]+)['\"]",
                    line,
                )
                if match and _VERSION_RE.fullmatch(match.group(1)):
                    return match.group(1)
    except (OSError, ValueError, UnicodeError):
        pass

    try:
        version = importlib_metadata.version("basaltwater")
    except importlib_metadata.PackageNotFoundError:
        return "unknown"
    return version if _VERSION_RE.fullmatch(version) else "unknown"


def _git_output(project_root: str, arguments: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", project_root, *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return (result.stdout or "").strip()


def build_setup_snapshot_metadata(project_root: str) -> JSONDict:
    """Describe the controller source copied by one target setup run."""

    source_root = os.path.abspath(project_root)
    validate_filesystem_path(source_root, must_exist=True)
    commit = _git_output(source_root, ["rev-parse", "--verify", "HEAD"])
    if commit is not None and _COMMIT_RE.fullmatch(commit) is None:
        commit = None
    inherited = (
        read_installation_metadata(source_root)
        if commit is None
        else None
    )
    if inherited is not None:
        return {
            "schema_version": INSTALLATION_METADATA_SCHEMA,
            "installation_type": SETUP_SNAPSHOT_TYPE,
            "version": inherited["version"],
            "commit": inherited.get("commit"),
            "branch": inherited.get("branch"),
            "dirty": inherited.get("dirty"),
        }
    branch = _git_output(
        source_root,
        ["symbolic-ref", "--quiet", "--short", "HEAD"],
    )
    status = _git_output(
        source_root,
        ["status", "--porcelain", "--untracked-files=all"],
    )
    return {
        "schema_version": INSTALLATION_METADATA_SCHEMA,
        "installation_type": SETUP_SNAPSHOT_TYPE,
        "version": _project_version(source_root),
        "commit": commit,
        "branch": branch or None,
        "dirty": bool(status) if status is not None else None,
    }


def write_setup_snapshot_metadata(project_root: str, destination: str) -> str:
    """Write source provenance into a staged target runtime tree."""

    target_root = os.path.abspath(destination)
    validate_filesystem_path(target_root, must_exist=True)
    path = os.path.join(target_root, INSTALLATION_METADATA_FILENAME)
    write_json_atomic(
        path,
        build_setup_snapshot_metadata(project_root),
        mode=0o644,
        sort_keys=True,
    )
    return path


def read_installation_metadata(installation_root: str) -> JSONDict | None:
    """Return validated setup-snapshot metadata when one is installed."""

    root = os.path.abspath(installation_root)
    validate_filesystem_path(root, must_exist=True)
    path = os.path.join(root, INSTALLATION_METADATA_FILENAME)
    if os.path.islink(path) or not os.path.isfile(path):
        return None
    try:
        value: Any = read_json_file(path, max_bytes=16384)
    except (OSError, ValueError, UnicodeError):
        return None
    if not isinstance(value, dict):
        return None
    if type(value.get("schema_version")) is not int or value["schema_version"] != INSTALLATION_METADATA_SCHEMA:
        return None
    if value.get("installation_type") != SETUP_SNAPSHOT_TYPE:
        return None
    version = value.get("version")
    commit = value.get("commit")
    branch = value.get("branch")
    dirty = value.get("dirty")
    if not isinstance(version, str) or _VERSION_RE.fullmatch(version) is None:
        return None
    if commit is not None and (
        not isinstance(commit, str) or _COMMIT_RE.fullmatch(commit) is None
    ):
        return None
    if branch is not None and not isinstance(branch, str):
        return None
    if dirty is not None and not isinstance(dirty, bool):
        return None
    return dict(value)


def installation_version(installation_root: str) -> str:
    """Return the installed snapshot or source-tree project version."""
    metadata = read_installation_metadata(installation_root)
    if metadata is not None:
        return str(metadata["version"])
    return _project_version(os.path.abspath(installation_root))


def read_refresh_source_metadata(installation_root: str) -> JSONDict | None:
    """Read a source-activation receipt; it never attests to successful setup."""
    root = os.path.abspath(installation_root)
    validate_filesystem_path(root, must_exist=True)
    path = os.path.join(root, REFRESH_METADATA_FILENAME)
    if os.path.islink(path) or not os.path.isfile(path):
        return None
    try:
        value = read_json_file(path, max_bytes=16384)
    except (OSError, ValueError, UnicodeError):
        return None
    if (not isinstance(value, dict) or set(value) != {"schema_version", "source"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1):
        return None
    source = value["source"]
    if (not isinstance(source, dict) or set(source) != {"version", "commit", "dirty"}
            or not isinstance(source["version"], str) or not _VERSION_RE.fullmatch(source["version"])
            or not isinstance(source["commit"], str) or not _COMMIT_RE.fullmatch(source["commit"])
            or (source["dirty"] is not None and type(source["dirty"]) is not bool)):
        return None
    return dict(source)


def write_refresh_source_metadata(installation_root: str) -> str:
    """Record observed Git source after activation, before saved setup replay."""
    source = inspect_installation_provenance(installation_root)["running_source"]
    if source is None:
        raise ValueError("Cannot record refresh source without an observed Git revision")
    path = os.path.join(os.path.abspath(installation_root), REFRESH_METADATA_FILENAME)
    write_json_atomic(path, {"schema_version": 1, "source": source}, mode=0o644, sort_keys=True)
    return path


def inspect_installation_provenance(installation_root: str) -> JSONDict:
    """Separate a recorded setup receipt from the source actually running now.

    Only inspect a Git root, never the enclosing project's repository. Neither
    branches, status filenames nor installation paths are returned.
    """
    root = os.path.abspath(installation_root)
    validate_filesystem_path(root, must_exist=True)
    receipt = read_installation_metadata(root)
    snapshot = ({key: receipt.get(key) for key in ("version", "commit", "dirty")}
                if receipt else None)
    refresh = read_refresh_source_metadata(root)
    source = None
    if os.path.exists(os.path.join(root, ".git")):
        environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        environment.update({"GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"})
        def git(arguments: list[str]) -> str | None:
            result = run_probe(["git", "-c", "core.fsmonitor=false", "-C", root, *arguments], timeout=5, env=environment)
            return result.output.strip() if result.issue is None else None
        git_root = git(["rev-parse", "--show-toplevel"])
        if git_root and os.path.realpath(git_root) == os.path.realpath(root):
            commit = git(["rev-parse", "--verify", "HEAD"])
            if commit and _COMMIT_RE.fullmatch(commit):
                status = git(["status", "--porcelain", "--untracked-files=normal"])
                source = {"version": _project_version(root), "commit": commit,
                          "dirty": bool(status) if status is not None else None}
    evidence = source or refresh or snapshot
    return {
        "version": evidence["version"] if evidence else _project_version(root),
        "commit": evidence["commit"] if evidence else None,
        "dirty": evidence["dirty"] if evidence else None,
        "installation_type": "source-checkout" if source else "managed-checkout" if refresh else "setup-snapshot" if snapshot else "unrecorded",
        "evidence_source": "running-git" if source else "refresh-receipt" if refresh else "setup-receipt" if snapshot else "unrecorded",
        "setup_snapshot": snapshot,
        "refresh_receipt": refresh,
        "running_source": source,
    }


def _same_revision(left: object, right: object) -> bool | None:
    commits = [value.get("commit") if isinstance(value, dict) else None for value in (left, right)]
    if any(not isinstance(commit, str) or not _COMMIT_RE.fullmatch(commit) for commit in commits):
        return None
    return commits[0] == commits[1]


def _cachyos_service_context() -> JSONDict:
    """Observe managed web unit identity without guessing its loaded source."""
    from lib.cachyos_doctor import _owned_socket, _probe

    uid = os.getuid()
    result = {"unit": "basaltwater-cachyos-t3.service", "state": "unknown",
              "invocation_id": None, "source": None}
    if uid == 0 or os.geteuid() != uid or not _owned_socket(Path(f"/run/user/{uid}/bus"), uid):
        return result
    status, output = _probe([
        "/usr/bin/systemctl", "--user", "show", result["unit"],
        "--property=LoadState", "--property=ActiveState", "--property=InvocationID",
    ], uid)
    lines = output.splitlines()
    values = dict(line.split("=", 1) for line in lines if "=" in line)
    if status != "ok" or len(lines) != 3 or set(values) != {"LoadState", "ActiveState", "InvocationID"}:
        return result
    if values["LoadState"] == "not-found":
        result["state"] = "absent"
    elif values["LoadState"] == "loaded" and values["ActiveState"] in {
        "active", "inactive", "failed", "activating", "deactivating", "reloading",
    }:
        result["state"] = values["ActiveState"]
        if re.fullmatch(r"[0-9a-f]{32}", values["InvocationID"]):
            result["invocation_id"] = values["InvocationID"]
    return result


def inspect_installation_context(installation_root: str, *, managed_root: str | None = None) -> JSONDict:
    """Compare known source receipts; never change a launcher or infer service code."""
    from lib.cachyos import is_cachyos

    invoked = inspect_installation_provenance(installation_root)
    native = is_cachyos()
    if managed_root is None:
        home = Path(pwd.getpwuid(os.getuid()).pw_dir)
        target = home / ".local/share/basaltwater" if native else Path("/opt/basaltwater")
    else:
        validate_filesystem_path(managed_root)
        target = Path(managed_root).expanduser()
        validate_filesystem_path(str(target), must_exist=True)
    managed = None
    try:
        if stat.S_ISREG((target / "basaltwater.py").lstat().st_mode):
            managed = (invoked if target.resolve() == Path(installation_root).resolve()
                       else inspect_installation_provenance(str(target)))
    except OSError:
        pass
    activation = managed.get("refresh_receipt") if managed else None
    snapshot = managed.get("setup_snapshot") if managed else None
    last_setup = service = None
    if native:
        from lib.cachyos_refresh import inspect_successful_setup_source

        last_setup = inspect_successful_setup_source()
        service = _cachyos_service_context()
    return {
        "invoked": invoked,
        "managed": managed,
        "managed_source_selection": "explicit" if managed_root is not None else "profile-default",
        "activation_receipt": activation,
        "setup_snapshot": snapshot,
        "last_successful_setup": last_setup,
        "service_context": service,
        "same_revision": {
            "invoked_managed": _same_revision(invoked, managed),
            "managed_activation": _same_revision(managed, activation),
            "managed_snapshot": _same_revision(managed, snapshot),
            "managed_last_setup": _same_revision(managed, last_setup),
            "managed_service": _same_revision(managed, service.get("source") if service else None),
        },
    }
