"""Non-secret metadata and freshness checks for agent credential files."""

from __future__ import annotations

import base64
import binascii
import json
import os
from datetime import datetime, timedelta, timezone

from lib.types import JSONDict


MAX_AGENT_CREDENTIAL_BYTES = 4 * 1024 * 1024
CODEX_REFRESH_INTERVAL = timedelta(days=8)
CODEX_EXPIRY_WARNING = timedelta(days=1)


def _utc_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _jwt_timestamp(token: object, claim: str) -> datetime | None:
    """Read an unverified timestamp claim without retaining token contents."""

    if not isinstance(token, str):
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        encoded = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
    except (
        binascii.Error,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
        RecursionError,
    ):
        return None
    value = payload.get(claim) if isinstance(payload, dict) else None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        return datetime.fromtimestamp(value, timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None


def _timestamp_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def inspect_codex_auth_payload(
    payload: bytes,
    *,
    now: datetime | None = None,
) -> JSONDict:
    """Return only safe freshness metadata from a Codex ``auth.json`` payload."""

    observed_at = now or datetime.now(timezone.utc)
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    observed_at = observed_at.astimezone(timezone.utc)
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        return {
            "status": "invalid",
            "auth_mode": None,
            "warnings": ["credential_file_invalid"],
        }
    if not isinstance(value, dict):
        return {
            "status": "invalid",
            "auth_mode": None,
            "warnings": ["credential_file_invalid"],
        }

    tokens = value.get("tokens")
    if not isinstance(tokens, dict):
        tokens = {}
    raw_auth_mode = value.get("auth_mode")
    api_key = value.get("OPENAI_API_KEY")
    api_key_present = isinstance(api_key, str) and bool(api_key.strip())
    # Emit fixed classifications only; auth files are secret-bearing input.
    if raw_auth_mode == "chatgpt" or tokens:
        auth_mode = "chatgpt"
    elif api_key_present:
        auth_mode = "api_key"
    else:
        auth_mode = None

    last_refresh = _utc_datetime(value.get("last_refresh"))
    access_token = tokens.get("access_token")
    issued_at = _jwt_timestamp(access_token, "iat")
    expires_at = _jwt_timestamp(access_token, "exp")
    refresh_token = tokens.get("refresh_token")
    refresh_token_present = isinstance(refresh_token, str) and bool(
        refresh_token.strip()
    )
    access_token_expired = (
        observed_at >= expires_at if expires_at is not None else None
    )
    refresh_overdue = (
        observed_at - last_refresh >= CODEX_REFRESH_INTERVAL
        if last_refresh is not None and observed_at >= last_refresh
        else False
    )
    expires_soon = bool(
        expires_at is not None
        and observed_at < expires_at <= observed_at + CODEX_EXPIRY_WARNING
    )

    warnings: list[str] = []
    if access_token_expired:
        warnings.append("access_token_expired")
    elif expires_soon:
        warnings.append("access_token_expires_soon")
    if refresh_overdue:
        warnings.append("refresh_overdue")
    if auth_mode == "chatgpt" and not refresh_token_present:
        warnings.append("refresh_token_missing")

    if auth_mode == "chatgpt":
        if access_token_expired or (refresh_overdue and expires_at is None):
            status = "refresh_required"
        elif refresh_overdue:
            # Our maintenance interval is not the provider's token lifetime.
            # Keep renewal due without declaring an unexpired token unusable.
            status = "refresh_due"
        elif expires_soon:
            status = "expires_soon"
        elif expires_at is not None or last_refresh is not None:
            status = "current"
        else:
            status = "unknown"
    elif api_key_present:
        status = "current"
    elif auth_mode is None:
        status = "unknown"
    else:
        status = "unknown"

    return {
        "status": status,
        "auth_mode": auth_mode,
        "last_refresh": _timestamp_text(last_refresh),
        "last_refresh_age_seconds": (
            max(0, int((observed_at - last_refresh).total_seconds()))
            if last_refresh is not None
            else None
        ),
        "access_token_issued_at": _timestamp_text(issued_at),
        "access_token_expires_at": _timestamp_text(expires_at),
        "access_token_expired": access_token_expired,
        "refresh_token_present": refresh_token_present,
        "api_key_present": api_key_present,
        "warnings": warnings,
    }


def inspect_codex_auth_file(
    path: str,
    *,
    now: datetime | None = None,
) -> JSONDict:
    """Inspect a regular Codex credential file without returning its contents."""

    if os.path.islink(path) or not os.path.isfile(path):
        return {
            "status": "invalid",
            "auth_mode": None,
            "warnings": ["credential_file_invalid"],
        }
    try:
        with open(path, "rb") as file_obj:
            payload = file_obj.read(MAX_AGENT_CREDENTIAL_BYTES + 1)
    except OSError:
        payload = b""
    if not payload or len(payload) > MAX_AGENT_CREDENTIAL_BYTES:
        return {
            "status": "invalid",
            "auth_mode": None,
            "warnings": ["credential_file_invalid"],
        }
    return inspect_codex_auth_payload(payload, now=now)


def codex_auth_warning(metadata: JSONDict) -> str | None:
    """Format a concise warning from safe Codex credential metadata."""

    status = metadata.get("status")
    if status == "invalid":
        return "Codex credential file is invalid or unreadable"
    warnings = metadata.get("warnings")
    if not isinstance(warnings, list) or not warnings:
        return None

    details: list[str] = []
    if "access_token_expired" in warnings:
        expiry = metadata.get("access_token_expires_at")
        details.append(f"cached access token expired at {expiry or 'an unknown time'}")
    elif "access_token_expires_soon" in warnings:
        expiry = metadata.get("access_token_expires_at")
        details.append(f"cached access token expires at {expiry or 'an unknown time'}")
    if "refresh_overdue" in warnings:
        last_refresh = metadata.get("last_refresh")
        details.append(f"last refresh was {last_refresh or 'over eight days ago'}")
    if "refresh_token_missing" in warnings:
        details.append("no refresh token is present")
    if not details:
        return None
    return "Codex credentials need attention: " + "; ".join(details)


def codex_auth_is_healthy(metadata: JSONDict) -> bool:
    """Return whether metadata has no known blocking freshness problem."""

    return metadata.get("status") not in {"invalid", "refresh_required"}


__all__ = [
    "codex_auth_is_healthy",
    "codex_auth_warning",
    "inspect_codex_auth_file",
    "inspect_codex_auth_payload",
]
