"""Save desktop observations without returning their full payload to the caller."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile


def save_observation(result: dict) -> dict:
    """Retain the exact JSON result privately and return a bounded preview.

    Output shortening is independent of scan completeness. In particular,
    a truncated accessibility scan stays truncated in the summary.
    """
    directory = Path(tempfile.mkdtemp(prefix="basaltwater-desktop-"))
    try:
        os.chmod(directory, 0o700)
        path = directory / "observation.json"
        with path.open("x", encoding="utf-8") as output:
            os.fchmod(output.fileno(), 0o600)
            json.dump(result, output, ensure_ascii=True)
            output.write("\n")
        summary = {
            "artifact": str(path),
            "bytes": path.stat().st_size,
            "counts": {},
            "preview": {},
        }
        for key in ("generation", "pid", "root", "truncated", "coverage", "condition", "state", "error"):
            value = result.get(key)
            if key in result and isinstance(value, (str, int, float, bool, type(None))):
                summary[key] = value[:512] if isinstance(value, str) else value
        for key in ("elements", "windows"):
            items = result.get(key)
            if not isinstance(items, list):
                continue
            summary["counts"][key] = len(items)
            summary["preview"][key] = [
                {field: value[:160] if isinstance(value, str) else value
                 for field in ("ref", "id", "pid", "name", "role", "title")
                 if isinstance((value := item.get(field)), (str, int))}
                for item in items[:3] if isinstance(item, dict)
            ]
        return summary
    except BaseException:
        shutil.rmtree(directory)
        raise
