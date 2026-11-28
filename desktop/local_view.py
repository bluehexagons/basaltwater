"""Resolve a verified preview for the caller's local viewer, without displaying it."""

from __future__ import annotations

from pathlib import Path

from desktop.decision_audit import _capture, _load_receipt
from desktop.qa_artifacts import MAX_MEDIA_BYTES
from desktop.record_bundles import _frame, _read


def local_view(result: str, presentation: str = "unattempted") -> dict:
    response, _ = _load_receipt(result)
    observed = _capture(response)
    if not observed.get("observation_id"):
        raise ValueError("Result has no successful capture observation")
    artifacts = response.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("Expected named capture artifacts in the saved result")
    selected = {}
    for item in artifacts:
        if not isinstance(item, dict):
            raise ValueError("Invalid capture artifact entry")
        kind = item.get("kind")
        if kind in ("screenshot_preview", "capture_receipt"):
            if kind in selected or not isinstance(item.get("path"), str):
                raise ValueError("Ambiguous preview or capture receipt")
            selected[kind] = item
    if len(selected) != 2:
        raise ValueError("Expected both screenshot_preview and capture_receipt")
    receipt, _ = _load_receipt(selected["capture_receipt"]["path"])
    preview = receipt.get("preview")
    item = selected["screenshot_preview"]
    if (receipt.get("ok") is not True or receipt.get("observation_id") != observed["observation_id"]
            or not isinstance(preview, dict) or preview != observed.get("preview")
            or preview.get("output") != item["path"] or item.get("sha256") != preview.get("artifact_sha256")
            or preview.get("source_sha256") != receipt.get("artifact_sha256")):
        raise ValueError("Named preview and capture receipt do not match the observation")
    size = preview.get("geometry")
    if not isinstance(size, list) or len(size) != 2 or any(type(n) is not int or n <= 0 for n in size):
        raise ValueError("Invalid preview geometry")
    content = _read(item["path"], MAX_MEDIA_BYTES)
    _frame(content, {"sha256": item.get("sha256"), "bytes": len(content), "width": size[0], "height": size[1]})
    return {"schema_version": 1, "ok": True, "capture_result": str(Path(result).absolute()),
            "observation_id": observed["observation_id"], "artifacts": list(selected.values()),
            "geometry": size, "presentation_reported": presentation, "input_authority": False}
