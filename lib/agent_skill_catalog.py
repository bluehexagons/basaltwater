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
            try:
                check_skill_path(destination, uid)
                entrypoint = _read(destination / "SKILL.md", uid)
                if entrypoint is None:
                    status = "incomplete"
                elif (destination / "SKILL.md").stat().st_mode & 0o022:
                    status = "unsafe"
                elif _MARKER not in entrypoint:
                    status = "personal"
                else:
                    files = _source_bundle(source)
                    recorded = _inventory(destination, uid)
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
                    for relative, (expected, mode) in files.items():
                        path = destination / relative
                        content = _read(path, uid)
                        if content is None:
                            status = "incomplete"
                            continue
                        if path.stat().st_mode & 0o022:
                            status = "unsafe"
                            break
                        different = different or content != expected or stat.S_IMODE(path.stat().st_mode) != mode
                    if status == "current" and different:
                        status = "different"
            except (OSError, ValueError, RuntimeError):
                status = "unsafe"
            entries.append({"name": name, "path": str(destination / "SKILL.md"), "status": status})
        catalogs.append({"path": str(root), "providers": list(providers), "skills": entries})
    return {
        "catalogs": catalogs,
        "session_access": "unverified",
        "instructions": "Catalog presence does not prove that a session enabled or loaded a skill. "
                        "Check the active agent's skill list and configuration; pass relevant absolute "
                        "skill paths to fresh subagents. Refresh managed guidance through the platform's setup.",
    }
