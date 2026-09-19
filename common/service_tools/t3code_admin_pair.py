#!/usr/bin/env python3
"""Issue a one-time administrative T3 Code pairing link without persisting secrets."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request


SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
if SOURCE_ROOT not in sys.path:
    sys.path.insert(0, SOURCE_ROOT)

from lib.validation import validate_filesystem_path
from lib.validators import validate_host


ADMINISTRATIVE_SCOPES = (
    "orchestration:read",
    "orchestration:operate",
    "terminal:operate",
    "review:write",
    "relay:read",
    "access:read",
    "access:write",
    "relay:write",
)
_MAX_JSON_BYTES = 64 * 1024
_TEMPORARY_SESSION_TTL = "2m"


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        request: urllib.request.Request,
        file_pointer: object,
        code: int,
        message: str,
        headers: object,
        new_url: str,
    ) -> None:
        return None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Issue an administrative T3 Code pairing link",
    )
    parser.add_argument("--t3-binary", required=True)
    parser.add_argument("--base-dir", required=True)
    parser.add_argument("--server-url", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--label", default="basaltwater remote environment")
    parser.add_argument("--json", action="store_true")
    return parser


def _validated_url(value: str, label: str, *, local_only: bool = False) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"Invalid {label}") from exc
    if local_only:
        try:
            local_host = ipaddress.ip_address(parsed.hostname or "")
        except ValueError:
            local_host = None
        if local_host is None and (parsed.hostname or "").lower() != "localhost":
            raise ValueError(f"Invalid {label}")
    if (
        parsed.scheme not in ({"http"} if local_only else {"http", "https"})
        or not parsed.hostname
        or not validate_host(parsed.hostname)
        or parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or (local_only and port is None)
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ValueError(f"Invalid {label}")
    return value.rstrip("/")


def _validated_label(value: str) -> str:
    label = value.strip()
    if not label or len(label) > 120 or any(ord(character) < 32 for character in label):
        raise ValueError("Invalid pairing label")
    return label


def _load_json(value: str, label: str) -> dict[str, object]:
    if len(value.encode("utf-8")) > _MAX_JSON_BYTES:
        raise RuntimeError(f"{label} returned too much data")
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{label} returned invalid JSON") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError(f"{label} returned invalid JSON")
    return decoded


def _run_t3(command: list[str], label: str) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"{label} could not run") from exc
    if result.returncode != 0:
        raise RuntimeError(f"{label} failed")
    return result


def _issue_temporary_session(t3_binary: str, base_dir: str) -> tuple[str, str]:
    result = _run_t3(
        [
            t3_binary,
            "auth",
            "session",
            "issue",
            "--base-dir",
            base_dir,
            "--ttl",
            _TEMPORARY_SESSION_TTL,
            "--label",
            "basaltwater pairing bootstrap",
            "--subject",
            "basaltwater-pairing-bootstrap",
            "--json",
        ],
        "Temporary T3 administrative session issuance",
    )
    payload = _load_json(result.stdout, "T3 session issuance")
    session_id = payload.get("sessionId")
    token = payload.get("token")
    scopes = payload.get("scopes")
    if (
        not isinstance(session_id, str)
        or not session_id
        or not isinstance(token, str)
        or not token
        or not isinstance(scopes, list)
        or not all(isinstance(scope, str) for scope in scopes)
        or not set(ADMINISTRATIVE_SCOPES).issubset(scopes)
    ):
        if isinstance(session_id, str) and session_id:
            _revoke_session(t3_binary, base_dir, session_id)
        raise RuntimeError("T3 did not issue the required administrative session")
    return session_id, token


def _revoke_session(t3_binary: str, base_dir: str, session_id: str) -> bool:
    try:
        _run_t3(
            [
                t3_binary,
                "auth",
                "session",
                "revoke",
                session_id,
                "--base-dir",
                base_dir,
            ],
            "Temporary T3 administrative session revocation",
        )
    except RuntimeError:
        return False
    return True


def _revoke_pairing_link(t3_binary: str, base_dir: str, pairing_id: str) -> None:
    try:
        _run_t3(
            [
                t3_binary,
                "auth",
                "pairing",
                "revoke",
                pairing_id,
                "--base-dir",
                base_dir,
            ],
            "T3 pairing-link revocation",
        )
    except RuntimeError:
        pass


def _request_pairing_link(
    server_url: str,
    bearer_token: str,
    label: str,
) -> dict[str, object]:
    endpoint = f"{server_url}/api/auth/pairing-token"
    body = json.dumps(
        {
            "label": label,
            "scopes": list(ADMINISTRATIVE_SCOPES),
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json",
            "User-Agent": "basaltwater-t3code-pairing/1",
        },
        method="POST",
    )
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirectHandler(),
    )
    try:
        with opener.open(request, timeout=15) as response:
            payload = response.read(_MAX_JSON_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"T3 rejected administrative pairing with HTTP {exc.code}") from exc
    except (OSError, urllib.error.URLError) as exc:
        raise RuntimeError("Could not reach the local T3 server") from exc
    if len(payload) > _MAX_JSON_BYTES:
        raise RuntimeError("T3 pairing response was too large")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError("T3 pairing response was not UTF-8") from exc
    return _load_json(text, "T3 pairing endpoint")


def _pairing_url(base_url: str, credential: str) -> str:
    parsed = urllib.parse.urlsplit(base_url)
    return urllib.parse.urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            "/pair",
            "",
            urllib.parse.urlencode({"token": credential}),
        )
    )


def issue_administrative_pairing(
    t3_binary: str,
    base_dir: str,
    server_url: str,
    base_url: str,
    label: str,
) -> dict[str, object]:
    """Mint one administrative pairing link and revoke its bootstrap session."""

    validate_filesystem_path(t3_binary, must_exist=True)
    if os.path.islink(t3_binary) or not os.path.isfile(t3_binary):
        raise ValueError("T3 binary must be a regular file")
    validate_filesystem_path(base_dir, must_exist=True)
    if os.path.islink(base_dir) or not os.path.isdir(base_dir):
        raise ValueError("T3 base directory must be a regular directory")
    local_url = _validated_url(server_url, "local T3 server URL", local_only=True)
    public_url = _validated_url(base_url, "public T3 base URL")
    pairing_label = _validated_label(label)

    session_id, token = _issue_temporary_session(t3_binary, base_dir)
    pairing_id: str | None = None
    try:
        pairing = _request_pairing_link(local_url, token, pairing_label)
        raw_pairing_id = pairing.get("id")
        if isinstance(raw_pairing_id, str) and raw_pairing_id:
            pairing_id = raw_pairing_id
        credential = pairing.get("credential")
        expires_at = pairing.get("expiresAt")
        if (
            pairing_id is None
            or not isinstance(credential, str)
            or not credential
            or not isinstance(expires_at, str)
            or not expires_at
        ):
            raise RuntimeError("T3 returned an incomplete administrative pairing link")
    except Exception:
        if pairing_id:
            _revoke_pairing_link(t3_binary, base_dir, pairing_id)
        raise
    finally:
        if not _revoke_session(t3_binary, base_dir, session_id):
            if pairing_id:
                _revoke_pairing_link(t3_binary, base_dir, pairing_id)
            raise RuntimeError("Could not revoke the temporary T3 administrative session")

    return {
        "expiresAt": expires_at,
        "pairUrl": _pairing_url(public_url, credential),
        "scopes": list(ADMINISTRATIVE_SCOPES),
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = issue_administrative_pairing(
            args.t3_binary,
            args.base_dir,
            args.server_url,
            args.base_url,
            args.label,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        if args.json:
            print(json.dumps({"error": str(exc), "ok": False}, sort_keys=True))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print("Issued administrative T3 Code pairing link.")
        print(f"Pairing URL: {result['pairUrl']}")
        print(f"Expires at: {result['expiresAt']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
