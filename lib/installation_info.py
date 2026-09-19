"""Non-secret provenance for installed basaltwater source snapshots."""

from __future__ import annotations

import json
import os
import re
import subprocess
from importlib import metadata as importlib_metadata
from typing import Any

from lib.atomic_io import write_json_atomic
from lib.types import JSONDict
from lib.validation import validate_filesystem_path


INSTALLATION_METADATA_FILENAME = ".basaltwater-installation.json"
INSTALLATION_METADATA_SCHEMA = 1
SETUP_SNAPSHOT_TYPE = "setup-snapshot"
_COMMIT_RE = re.compile(r"[0-9a-f]{40,64}")
_VERSION_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+-]*")


def _project_version(project_root: str) -> str:
    """Read the project version without requiring an installed wheel."""

    pyproject_path = os.path.join(project_root, "pyproject.toml")
    try:
        with open(pyproject_path, encoding="utf-8") as file_obj:
            in_project = False
            for raw_line in file_obj:
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
    except OSError:
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
        with open(path, encoding="utf-8") as file_obj:
            value: Any = json.load(file_obj)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    if value.get("schema_version") != INSTALLATION_METADATA_SCHEMA:
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
