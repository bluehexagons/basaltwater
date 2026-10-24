"""Read-only inventory of installed Basaltwater guidance, independent of a harness."""

from __future__ import annotations

import os
from pathlib import Path
import stat

from lib.agent_skill_bundles import (
    SKILL_CATALOG_PROVIDERS,
    _INVENTORY,
    _MARKER,
    _digest,
    _inventory,
    _read,
    _source_bundle,
    check_skill_path,
)
from lib.validation import validate_filesystem_path


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "common/agent_skills"


def _measurement(status: str = "unmeasured") -> dict[str, object]:
    return {"status": status, "measured_files": 0,
            "utf8_bytes": 0 if status == "complete" else None,
            "code_points": 0 if status == "complete" else None}


def _measure(content: bytes) -> dict[str, object]:
    """Count exact bytes and decoded code points without newline normalization."""
    try:
        characters = len(content.decode("utf-8"))
    except UnicodeDecodeError:
        return _measurement("incomplete")
    return {"status": "complete", "measured_files": 1,
            "utf8_bytes": len(content), "code_points": characters}


def _combined_status(statuses: list[str]) -> str:
    if not statuses or all(status == "unmeasured" for status in statuses):
        return "unmeasured"
    return "complete" if all(status == "complete" for status in statuses) else "incomplete"


def _sum_measurements(measurements: list[dict[str, object]]) -> dict[str, object]:
    status = _combined_status([item["status"] for item in measurements])
    result = _measurement(status)
    result["measured_files"] = sum(item["measured_files"] for item in measurements)
    if result["measured_files"]:
        for key in ("utf8_bytes", "code_points"):
            result[key] = sum(item[key] or 0 for item in measurements)
    return result


def _footprint(entrypoint: dict[str, object], references: dict[str, object]) -> dict[str, object]:
    return {"status": _combined_status([entrypoint["status"], references["status"]]),
            "entrypoint": entrypoint, "references": references}


def _sum_footprints(footprints: list[dict[str, object]]) -> dict[str, object]:
    return _footprint(*(_sum_measurements([item[key] for item in footprints])
                        for key in ("entrypoint", "references")))


def _is_reference(name: str) -> bool:
    return name.startswith("references/") and name.lower().endswith(".md")


def inspect_skill_catalogs(home: str) -> dict[str, object]:
    """Compare known skill bundles with this CLI's source; never inspect auth/config."""
    validate_filesystem_path(home, must_exist=True)
    uid = Path(home).stat().st_uid
    sources = {path.name: path for path in sorted(SOURCE_ROOT.iterdir()) if path.is_dir()}
    catalogs = []
    for directory, providers in SKILL_CATALOG_PROVIDERS:
        root = Path(home) / directory / "skills"
        entries = []
        for name, source in sources.items():
            destination = root / name
            if not os.path.lexists(destination):
                continue
            status = "current"
            entrypoint_size = _measurement()
            references_size = _measurement()
            try:
                check_skill_path(destination, uid)
                entrypoint = _read(destination / "SKILL.md", uid)
                if entrypoint is None:
                    status = "incomplete"
                    entrypoint_size = _measurement("incomplete")
                elif (destination / "SKILL.md").stat().st_mode & 0o022:
                    status = "unsafe"
                elif _MARKER not in entrypoint:
                    status = "personal"
                else:
                    entrypoint_size = _measure(entrypoint)
                    files = _source_bundle(source)
                    recorded, complete = _inventory(destination, uid)
                    if not complete:
                        status = "incomplete"
                    inventory = destination / _INVENTORY
                    if inventory.exists() and inventory.stat().st_mode & 0o022:
                        raise RuntimeError(f"Unsafe managed agent skill inventory: {inventory}")
                    expected_inventory = {
                        name: _digest(content)
                        for name, (content, _mode) in files.items() if name != "SKILL.md"
                    }
                    # Legacy bundles may lack an inventory. Existing records
                    # must match too, including resources removed from source.
                    different = inventory.exists() and recorded != expected_inventory
                    references = []
                    # Obsolete recorded references are outside the known source
                    # reads. Report an incomplete measurement without opening them.
                    references_complete = not any(
                        _is_reference(name) and name not in files for name in recorded
                    )
                    for relative, (expected, mode) in files.items():
                        path = destination / relative
                        content = entrypoint if relative == "SKILL.md" else _read(path, uid)
                        if content is None:
                            status = "incomplete"
                            if _is_reference(relative):
                                references_complete = False
                            continue
                        if path.stat().st_mode & 0o022:
                            status = "unsafe"
                            break
                        if _is_reference(relative):
                            references.append(_measure(content))
                        different = different or content != expected or stat.S_IMODE(path.stat().st_mode) != mode
                    references_size = _sum_measurements(references) if references else _measurement("complete")
                    if not references_complete:
                        references_size["status"] = "incomplete"
                    if references_size["status"] != "complete" and not references_size["measured_files"]:
                        references_size = _measurement("incomplete")
                    if status == "current" and different:
                        status = "different"
            except (OSError, ValueError, RuntimeError):
                status = "unsafe"
            if status == "unsafe":
                entrypoint_size, references_size = _measurement(), _measurement()
            entries.append({"name": name, "path": str(destination / "SKILL.md"), "status": status,
                            "instruction_footprint": _footprint(entrypoint_size, references_size)})
        catalogs.append({"path": str(root), "providers": list(providers), "skills": entries,
                         "instruction_footprint": _sum_footprints([entry["instruction_footprint"] for entry in entries])})
    return {
        "catalogs": catalogs,
        "instruction_footprint": _sum_footprints([
            entry["instruction_footprint"] for catalog in catalogs for entry in catalog["skills"]
        ]),
        "session_access": "unverified",
        "instructions": "Catalog presence does not prove that a session enabled or loaded a skill. "
                        "Check the active agent's skill list and configuration; pass relevant absolute "
                        "skill paths to fresh subagents. Refresh managed guidance through the platform's setup.",
    }
