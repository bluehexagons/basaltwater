"""Structured desktop failures without guessing whether input was delivered."""

from __future__ import annotations


class DesktopRequestError(ValueError, RuntimeError):
    """Preserve a worker's receipt through transport and CLI boundaries."""

    def __init__(self, receipt: dict):
        super().__init__(receipt["error"])
        self.receipt = receipt


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
