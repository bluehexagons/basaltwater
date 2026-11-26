"""Opt-in, worktree-local Godot asset staging and headless import."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile

from lib.atomic_io import write_json_atomic
from lib.streamed_process import run_captured, run_streamed
from lib.validation import validate_filesystem_path


def _relative(value: object, *, project: bool = False) -> Path:
    if not isinstance(value, str):
        raise ValueError("Godot preparation paths must be strings")
    validate_filesystem_path(value)
    path = Path(value)
    if (path.is_absolute() or (not path.parts and (not project or value != "."))
            or any(part.casefold() in {"..", ".git", ".godot"} for part in path.parts)):
        raise ValueError("Godot preparation paths must stay inside the checkout and outside .git and .godot")
    return path


def parse_declaration(value: object) -> dict:
    if not isinstance(value, dict) or set(value) - {"project", "asset_directories"}:
        raise ValueError("godot_prepare accepts project and asset_directories")
    project = _relative(value.get("project", "."), project=True)
    assets = value.get("asset_directories", [])
    if not isinstance(assets, list) or len(assets) > 32:
        raise ValueError("Godot preparation accepts at most 32 asset directories")
    paths = []
    for value in assets:
        path = _relative(value)
        if any(path.is_relative_to(other) or other.is_relative_to(path) for other in paths):
            raise ValueError("Godot asset directories must be distinct and may not overlap")
        paths.append(path)
    return {"project": str(project), "asset_directories": [str(path) for path in paths]}


def add_preparation_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--prepare", action="store_true", help="Stage declared local assets and run headless Godot import before the recipe")
    parser.add_argument("--asset-bundle", metavar="PATH", help="Explicitly approve this external local asset bundle for --prepare")


def bundle_path(value: str | None, *, prepare: bool) -> Path | None:
    if type(prepare) is not bool:
        raise ValueError("Prepare must be a boolean")
    if value is None:
        return None
    if not prepare:
        raise ValueError("--asset-bundle requires --prepare")
    validate_filesystem_path(value)
    path = Path(value).expanduser().resolve(strict=True)
    if not path.is_dir():
        raise ValueError("The approved asset bundle must be an existing directory")
    return path


def _directory_exists(root: Path, relative: Path) -> bool:
    current = root
    for part in relative.parts:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            return False
        if not stat.S_ISDIR(mode):
            raise ValueError(f"Expected a real directory without symlinks: {current}")
    return True


def _require_ignored(repository: Path, path: Path) -> None:
    relative = path.relative_to(repository).as_posix()
    tracked = run_captured(["git", "--literal-pathspecs", "ls-files", "-z", "--", relative],
                           cwd=str(repository), timeout=10, max_bytes=64 * 1024)
    ignored = run_captured(["git", "check-ignore", "--quiet", "--", relative + "/"],
                           cwd=str(repository), timeout=10, max_bytes=4096)
    if tracked.returncode or tracked.stdout or ignored.returncode:
        raise ValueError(f"Godot preparation directory must be ignored and untracked: {relative}")


def preparation_plan(repository: Path, declaration: dict | None, bundle: Path | None) -> dict:
    from desktop.development import project_info

    if declaration is None:
        raise ValueError("The recipe must declare godot_prepare before using --prepare")
    relative = Path(declaration["project"])
    if not _directory_exists(repository, relative):
        raise ValueError("The declared Godot project directory is missing")
    project = repository / relative
    info = project_info(project)
    if info["kind"] != "godot" or info["missing_tools"]:
        raise ValueError("Godot preparation requires project.godot and its engine tools: "
                         + (", ".join(info["missing_tools"]) or str(project)))
    if bundle is not None and (bundle.is_relative_to(repository) or repository.is_relative_to(bundle)):
        raise ValueError("Choose an asset bundle separate from the checkout")
    cache = project / ".godot"
    _directory_exists(project, Path(".godot"))
    _require_ignored(repository, cache)
    assets = {}
    for entry in declaration["asset_directories"]:
        path = Path(entry)
        _require_ignored(repository, project / path)
        if _directory_exists(project, path):
            assets[entry] = "present"
        elif bundle is not None and _directory_exists(bundle, path):
            assets[entry] = "ready-to-stage"
        else:
            assets[entry] = "missing-from-bundle"
    return {"project": str(project), "cache": str(cache), "bundle": str(bundle) if bundle is not None else None,
            "assets": assets, "import_argv": [info["engine"], "--path", str(project), "--headless", "--import"]}


def _reject_special_entries(directory: str, names: list[str]) -> list[str]:
    for name in names:
        mode = os.lstat(os.path.join(directory, name)).st_mode
        if name.casefold() in {".git", ".godot"} or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise ValueError(f"Asset bundles require ordinary files and directories: {Path(directory) / name}")
    return []


def _stage_directory(source: Path, destination: Path) -> None:
    from lib.build_inputs import _rename_noreplace

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".godot-assets-", dir=destination.parent) as temporary:
        staged = Path(temporary) / "assets"
        shutil.copytree(source, staged, symlinks=True, ignore=_reject_special_entries)
        for directory, children, files in os.walk(staged, followlinks=False):
            _reject_special_entries(directory, children + files)
        descriptor = os.open(destination.parent, os.O_DIRECTORY | os.O_RDONLY | os.O_NOFOLLOW)
        try:
            _rename_noreplace(descriptor, str(staged.relative_to(destination.parent)), destination.name)
        finally:
            os.close(descriptor)


def prepare_godot(repository: Path, declaration: dict | None, bundle: Path | None, *,
                  report_path: str, timeout: int, environment: dict[str, str], on_output,
                  descriptors: tuple[int, ...]) -> dict:
    """Stage whole missing directories and stream import progress before GPU work."""
    report = {"schema_version": 1, "status": "planning", "returncode": None}
    write_json_atomic(report_path, report)
    try:
        report.update(preparation_plan(repository, declaration, bundle))
        missing = [entry for entry, status in report["assets"].items() if status == "missing-from-bundle"]
        if missing:
            raise ValueError("Supply the approved --asset-bundle containing: " + ", ".join(missing))
        report["status"] = "staging"
        write_json_atomic(report_path, report)
        for entry, status in report["assets"].items():
            if status == "ready-to-stage":
                on_output(f"Staging Godot assets: {entry}\n")
                _stage_directory(bundle / entry, Path(report["project"]) / entry)
                report["assets"][entry] = "staged"
                write_json_atomic(report_path, report)
        report["status"] = "importing"
        write_json_atomic(report_path, report)
        on_output("Starting headless Godot import\n")
        report["returncode"] = run_streamed(
            report["import_argv"], cwd=report["project"], env=environment, timeout=timeout,
            on_output=on_output, pass_fds=descriptors, cleanup_group_on_exit=True,
        )
        if report["returncode"] != 0:
            raise RuntimeError(f"Headless Godot import exited with status {report['returncode']}")
        on_output("Headless Godot import completed\n")
        report["status"] = "passed"
    except KeyboardInterrupt:
        report["status"] = "interrupted"
        raise
    except TimeoutError:
        report.update(status="timed-out", error=f"Headless Godot import exceeded {timeout} seconds")
        raise
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        report.update(status="failed", error=str(exc))
        raise
    finally:
        write_json_atomic(report_path, report)
    return report
