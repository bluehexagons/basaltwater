"""Validated declarative scene assemblies for isolated geometry inspection."""

from __future__ import annotations

import math
from pathlib import Path

from lib.atomic_io import read_json_file
from lib.validation import validate_filesystem_path, validate_no_control_characters


FORMATS = {".obj", ".gltf", ".glb"}


def assembly_inputs(source: str) -> list[dict]:
    """Resolve an assembly's geometry files; never execute project scripts."""
    if Path(source).suffix.lower() != ".json":
        if Path(source).suffix.lower() not in FORMATS | {".blend"}:
            raise ValueError("Source must be .blend, .obj, .gltf, .glb or an assembly .json")
        return []
    data = read_json_file(source, max_bytes=256 * 1024)
    if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
        raise ValueError("Assembly requires version: 1")
    entries = data.get("parts")
    if not isinstance(entries, list) or not 1 <= len(entries) <= 64:
        raise ValueError("Assembly requires 1–64 parts")
    parts, names = [], set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) - {"name", "source", "matrix"}:
            raise ValueError("Assembly parts accept name, source and matrix")
        name, relative = entry.get("name"), entry.get("source")
        if not isinstance(name, str) or not name or len(name) > 64 or name in names:
            raise ValueError("Assembly part names must be unique, nonempty and at most 64 characters")
        validate_no_control_characters(name, "Assembly name")
        names.add(name)
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ValueError("Assembly source must be relative without parent traversal")
        path = (Path(source).parent / relative).resolve()
        validate_filesystem_path(str(path), must_exist=True)
        if not path.is_relative_to(Path(source).parent.resolve()) or path.suffix.lower() not in FORMATS:
            raise ValueError("Assembly geometry must be OBJ/glTF/GLB below the assembly directory")
        matrix = entry.get("matrix", [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])
        if (not isinstance(matrix, list) or len(matrix) != 4 or any(
            not isinstance(row, list) or len(row) != 4 or any(type(n) not in (int, float) or not math.isfinite(n) for n in row)
            for row in matrix
        ) or matrix[3] != [0, 0, 0, 1]):
            raise ValueError("Assembly matrix must be a finite affine 4×4 row-major matrix")
        parts.append({"name": name, "source": str(path), "matrix": matrix})
    return parts
