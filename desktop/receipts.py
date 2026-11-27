"""Structured desktop failures without guessing whether input was delivered."""

from __future__ import annotations


class DesktopRequestError(ValueError, RuntimeError):
    """Preserve a worker's receipt through transport and CLI boundaries."""

    def __init__(self, receipt: dict):
        super().__init__(receipt["error"])
        self.receipt = receipt


def input_snapshot(receipt: dict) -> dict:
    """Link captures to the latest input outcome without retaining input text/errors."""
    return {key: receipt[key] for key in (
        "input_id", "generation", "observation_id", "geometry", "reason", "dispatch_stage", "input_delivery",
        "input_received_at", "input_received_monotonic_ns", "input_dispatch_at", "input_dispatch_monotonic_ns",
        "input_completed_at", "input_completed_monotonic_ns", "submitted_characters", "completion_unknown",
        "touch_frames_completed", "touch_contacts_released", "touch_frame_completed_monotonic_ns",
    ) if key in receipt}


def confirm_input_completion(receipt: dict) -> dict:
    """Gate follow-up actions on the helper's delivery acknowledgement."""
    if receipt.get("error") or receipt.get("ok") is False or receipt.get("input_delivery") == "completed":
        return receipt
    if "input_delivery" not in receipt and receipt.get("input_completed_at"):
        return {**receipt, "input_delivery": "completed"}
    return {**receipt, "input_delivery": receipt.get("input_delivery") or "unknown",
            "reason": "input-completion-unverified", "stage": "input_confirmation",
            "error": "Input completion was not confirmed. Inspect before further input."}


def input_failure(error: Exception, payload: dict, *, stage: str = "before-input") -> dict:
    receipt = getattr(error, "receipt", {})
    return {"error": str(error), "reason": receipt.get("reason", "request-refused" if stage == "before-input" else "input-failed"),
            "dispatch_stage": stage,
            "input_delivery": "not-sent" if stage == "before-input" else "unknown",
            **{key: receipt[key] for key in ("capture_freshness",) if key in receipt},
            **payload.get("_input_receipt", {}),
            **{key: payload[key] for key in ("input_id", "generation", "observation_id") if key in payload}}


def input_request_failure(error: Exception, payload: dict) -> dict:
    """Keep authoritative delivery evidence; an unstructured reply proves nothing."""
    receipt = getattr(error, "receipt", {})
    if receipt.get("input_delivery") in ("not-sent", "completed", "partial", "unknown"):
        return dict(receipt)
    detail = "\n".join([str(error), *getattr(error, "__notes__", [])])
    return {**input_failure(error, payload, stage="transport"), "reason": "transport-failed",
            "error": f"{detail}; input outcome may be partial or unknown; inspect input before further actions; do not replay"}
