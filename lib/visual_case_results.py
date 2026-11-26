"""Validate project case checkpoints and retain evidence from interrupted checks."""

from __future__ import annotations

import os
from pathlib import Path
import re
import stat

from lib.atomic_io import read_json_file
from lib.validation import validate_filesystem_path, validate_no_control_characters


CASE_STATUSES = {"passed", "failed", "skipped", "incomplete"}
CAPTURE_SUFFIXES = {".png", ".pfm"}
DEVICE_LOST = re.compile(r"\bVK_ERROR_DEVICE_LOST\b|\bVulkan\b[^\n]{0,160}\bdevice (?:was )?lost\b", re.IGNORECASE)


def _text(value: object, name: str, limit: int = 512) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= limit or not value.strip():
        raise ValueError(f"{name} must have 1–{limit} characters")
    validate_no_control_characters(value, name)
    return value


def _capture(root: Path, value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Case capture path must be a string")
    validate_filesystem_path(value)
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts or relative.suffix.lower() not in CAPTURE_SUFFIXES:
        raise ValueError("Case captures must be relative PNG or PFM paths inside the evidence directory")
    path = root
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError("Case captures cannot follow symlinks")
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("Case capture must be a saved regular file")
    return str(path)


def read_case_checkpoint(path: Path) -> dict:
    value = read_json_file(str(path), max_bytes=256 * 1024)
    if (not isinstance(value, dict) or set(value) - {"version", "complete", "cases", "renderer_error"}
            or type(value.get("version")) is not int or value["version"] != 1
            or type(value.get("complete")) is not bool
            or not isinstance(value.get("cases"), list) or len(value["cases"]) > 128):
        raise ValueError("Case checkpoint requires version 1, complete and at most 128 ordered cases")
    cases = {}
    for case in value["cases"]:
        if (not isinstance(case, dict) or set(case) - {"id", "status", "captures", "message"}
                or not isinstance(case.get("status"), str) or case["status"] not in CASE_STATUSES):
            raise ValueError("Case entries require an ID and a recognized status")
        case_id = _text(case.get("id"), "Case ID", 128)
        if case_id in cases or (value["complete"] and case["status"] == "incomplete"):
            raise ValueError("Case IDs must be unique and complete checkpoints cannot contain incomplete cases")
        captures = case.get("captures", [])
        if not isinstance(captures, list) or len(captures) > 16:
            raise ValueError("Each case may reference at most 16 captures")
        cases[case_id] = {"id": case_id, "status": case["status"],
                          "captures": [_capture(path.parent, capture) for capture in captures]}
        if "message" in case:
            cases[case_id]["message"] = _text(case["message"], "Case message")
    result = {"complete": value["complete"], "cases": list(cases.values()),
              "last_completed_case": next((key for key in reversed(cases) if cases[key]["status"] in {"passed", "failed"}), None)}
    if "renderer_error" in value:
        error = value["renderer_error"]
        if not isinstance(error, dict) or set(error) != {"code", "message"}:
            raise ValueError("Renderer error requires a code and message")
        result["renderer_error"] = {"code": _text(error["code"], "Renderer error code", 128),
                                    "message": _text(error["message"], "Renderer error message"),
                                    "source": "project"}
    return result


def saved_captures(root: Path) -> tuple[list[str], bool]:
    """Look for saved images on a failed run without following linked directories."""
    captures = []
    visited = 0
    pending = [root]
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                visited += 1
                if visited > 1024 or len(captures) >= 64:
                    return sorted(captures), True
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
                elif Path(entry.name).suffix.lower() in CAPTURE_SUFFIXES and entry.is_file(follow_symlinks=False):
                    captures.append(entry.path)
    return sorted(captures), False
