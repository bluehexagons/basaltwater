"""Read-only Godot version and shallow export-template inventory."""

from __future__ import annotations

import os
from pathlib import Path
import re
import stat
import subprocess

from lib.streamed_process import run_captured
from lib.validation import validate_filesystem_path


ENTRY_LIMIT = 1024
VERSION = re.compile(r"(4\.[0-9]+(?:\.[0-9]+)?\.[A-Za-z0-9_-]+(?:\.mono)?(?:\.double)?)\.[A-Za-z0-9_-]+(?:\.[0-9a-f]+)?")


def _template_root(engine: str) -> Path:
    executable = Path(engine).resolve()
    if any((executable.parent / marker).is_file() for marker in ("_sc_", "._sc_")):
        return executable.parent / "editor_data" / "export_templates"
    data = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    validate_filesystem_path(data)
    if not Path(data).is_absolute():
        raise ValueError("XDG_DATA_HOME must be absolute for export diagnosis")
    return Path(data) / "godot" / "export_templates"


def inspect_exports(info: dict) -> dict:
    """Inventory names/presence only; never import a project or verify exports."""
    result = {"ok": False, "state": "unverified", "engine_version": None,
              "expected_template_version": None, "template_root": None,
              "installed": [], "mismatching_templates": [], "missing_templates": [],
              "truncated": False, "export_readiness": "unverified"}
    if info["kind"] != "godot" or not info.get("engine"):
        return {**result, "reason": "A discoverable Godot engine and project.godot are required"}
    # --version exits before project initialization; never pass --path or cwd=project.
    try:
        probe = run_captured([info["engine"], "--version"], timeout=3, max_bytes=4096,
                             cwd="/", env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return {**result, "reason": "Godot version probe unavailable, timed out or exceeded its output limit"}
    version = probe.stdout.strip()
    match = VERSION.fullmatch(version)
    if probe.returncode != 0 or match is None:
        return {**result, "reason": "Godot version probe failed or returned an unsupported version format"}
    info["engine_version"] = version
    expected = match[1]
    root = _template_root(info["engine"])
    result.update(engine_version=version, expected_template_version=expected, template_root=str(root))
    remaining = ENTRY_LIMIT
    try:
        with os.scandir(root) as directories:
            for directory in directories:
                remaining -= 1
                if remaining < 0:
                    result["truncated"] = True
                    break
                if not directory.is_dir(follow_symlinks=False):
                    continue
                files = []
                with os.scandir(directory.path) as entries:
                    for entry in entries:
                        remaining -= 1
                        if remaining < 0:
                            result["truncated"] = True
                            break
                        metadata = entry.stat(follow_symlinks=False)
                        if stat.S_ISREG(metadata.st_mode) and metadata.st_size > 0:
                            files.append(entry.name)
                result["installed"].append({"version": directory.name, "files": sorted(files)})
                if result["truncated"]:
                    break
    except FileNotFoundError:
        if root.exists():
            raise  # A disappearing inventory entry is not a missing installation.
    result["installed"].sort(key=lambda item: item["version"])
    result["mismatching_templates"] = [item["version"] for item in result["installed"] if item["version"] != expected]
    matching = next((item for item in result["installed"] if item["version"] == expected), None)
    # version.txt alone is not a template installation. Do not infer target-specific completeness.
    if matching is None or not any(name != "version.txt" for name in matching["files"]):
        result["missing_templates"] = [expected]
    result["ok"] = not result["truncated"] and not result["missing_templates"]
    result["state"] = "present" if result["ok"] else "unverified" if result["truncated"] else "missing"
    return result
