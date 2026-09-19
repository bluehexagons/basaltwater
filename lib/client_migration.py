"""One-time migration of the recent default controller workspace."""

from __future__ import annotations

import os
from pathlib import Path

from lib.concurrency import resource_lock
from lib.rename_migration import _move_plan, _safe


def migrate_client_workspace(destination: Path) -> None:
    """Move configuration before lookup, preserving every file's bytes and mode.

    Each rename is atomic. A retry plans only the remaining source entries,
    including after an interrupted merge into an existing workspace. This
    deliberately does not relocate source installations or manage services.
    """
    sources = [destination.with_name(name) for name in ("infra_tools", "infra-tools")]
    if not any(os.path.lexists(source) for source in sources):
        return
    with resource_lock("client-migration", str(destination), wait=True):
        actions: list[dict] = []
        occupied: dict[Path, Path] = {}
        for source in sources:
            if not os.path.lexists(source):
                continue
            for path in (source, destination):
                _safe(path)
                if os.path.lexists(path) and (
                    path.is_symlink() or not path.is_dir() or path.stat().st_uid != os.geteuid()
                ):
                    raise ValueError(f"Unsafe client workspace: {path}")
            _move_plan(source, destination, actions, occupied)
        # Preflight both historical spellings before moving any data. Never
        # interpret or rewrite credential values, saved remote paths or keys.
        # Tighten existing merge destinations before exposing moved files.
        for action in actions:
            if action["kind"] == "chmod" and Path(action["old"]).exists():
                Path(action["old"]).chmod(action["after"])
        # Remove empty sources last, so retries retain the source permission
        # metadata until destination permissions have been applied.
        order = {"move": 0, "chmod": 1, "rmdir": 2}
        for action in sorted(actions, key=lambda item: order[item["kind"]]):
            old = Path(action["old"])
            if action["kind"] == "move":
                new = Path(action["new"])
                if os.path.lexists(new):
                    raise ValueError(f"Client migration destination appeared: {new}")
                old.rename(new)
            elif action["kind"] == "rmdir":
                old.rmdir()
            elif action["kind"] == "chmod":
                old.chmod(action["after"])
