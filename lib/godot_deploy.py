"""Validation for already-exported Godot web payloads before activation."""

from __future__ import annotations

import os
from pathlib import Path
import stat

from lib.validation import validate_filesystem_path

REQUIRED_FILES = ("index.html", "index.js", "index.wasm", "index.pck")
MAX_FILES = 4096
MAX_BYTES = 2 * 1024 * 1024 * 1024


def validate_godot_export(output: str, staging_root: str) -> None:
    """Require bounded regular files in the staged tree, never source-only input."""
    validate_filesystem_path(output, must_exist=True)
    validate_filesystem_path(staging_root, must_exist=True)
    root = Path(staging_root).resolve()
    path = Path(output)
    resolved = path.resolve()
    if os.path.commonpath((str(root), str(resolved))) != str(root):
        raise RuntimeError("Godot output escapes the staged release")
    current = path
    while current != root and current != current.parent:
        if current.is_symlink():
            raise RuntimeError("Godot output must not contain symlinked directories")
        current = current.parent
    count = total = 0
    for directory, directories, files in os.walk(path, followlinks=False):
        for name in directories + files:
            item = Path(directory) / name
            mode = item.lstat().st_mode
            if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise RuntimeError("Godot export contains a symlink or special file")
            count += 1
            if stat.S_ISREG(mode):
                total += item.stat().st_size
            if count > MAX_FILES or total > MAX_BYTES:
                raise RuntimeError("Godot export exceeds deployment limits")
    validate_godot_web_entrypoint(path)


def validate_godot_web_entrypoint(path: Path) -> None:
    """Check the required Web export files after validating the staged tree."""
    for name in REQUIRED_FILES:
        file = path / name
        if not file.is_file() or file.stat().st_size == 0:
            raise RuntimeError(f"Godot export missing {name}; export Web to index.html first")
    with (path / "index.wasm").open("rb") as source:
        if source.read(4) != b"\x00asm":
            raise RuntimeError("Godot index.wasm is not a WebAssembly binary")
    with (path / "index.pck").open("rb") as source:
        if source.read(4) != b"GDPC":
            raise RuntimeError("Godot index.pck is not a Godot package")
