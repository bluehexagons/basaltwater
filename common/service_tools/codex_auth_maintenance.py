#!/usr/bin/env python3
"""Refresh renewable Codex authentication before it becomes unusable."""

from __future__ import annotations

import json
import os
import pwd
import select
import shutil
import stat
import subprocess
import sys
import time
from logging import ERROR
from typing import Any, BinaryIO

SOURCE_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
)
if SOURCE_ROOT not in sys.path:
    sys.path.insert(0, SOURCE_ROOT)

from lib.agent_credentials import inspect_codex_auth_file
from lib.logging_utils import get_service_logger, log_event
from lib.validation import validate_filesystem_path


logger = get_service_logger(
    "codex_auth_maintenance",
    "common",
    use_syslog=True,
)

_PROTOCOL_TIMEOUT_SECONDS = 60
_MAX_PROTOCOL_LINE_BYTES = 1024 * 1024
_MAX_PROTOCOL_MESSAGES = 256
_INITIALIZE_REQUEST_ID = 1
_ACCOUNT_REQUEST_ID = 2


class AuthRefreshError(RuntimeError):
    """A fixed diagnostic category, without provider response contents."""

    def __init__(self, reason: str, error_code: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.error_code = error_code


def _response_error(response: dict[str, Any], stage: str) -> AuthRefreshError:
    """Extract only a numeric JSON-RPC code; messages may contain secrets."""

    error = response.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    return AuthRefreshError(
        stage + "_rpc_error",
        code if type(code) is int and -(2**31) <= code < 2**31 else None,
    )


def _write_message(stream: BinaryIO, message: dict[str, Any]) -> None:
    payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
    stream.write(payload + b"\n")
    stream.flush()


def _read_response(
    process: subprocess.Popen[bytes],
    request_id: int,
    deadline: float,
    buffer: bytearray,
) -> dict[str, Any]:
    """Read one bounded JSON-lines response while ignoring notifications."""

    if process.stdout is None:
        raise RuntimeError("Codex app server stdout is unavailable")
    for _message_index in range(_MAX_PROTOCOL_MESSAGES):
        while b"\n" not in buffer:
            if len(buffer) > _MAX_PROTOCOL_LINE_BYTES:
                raise RuntimeError(
                    "Codex app server response exceeded the size limit"
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("Codex app server response timed out")
            ready, _writable, _exceptional = select.select(
                [process.stdout],
                [],
                [],
                remaining,
            )
            if not ready:
                raise RuntimeError("Codex app server response timed out")
            chunk = os.read(process.stdout.fileno(), 64 * 1024)
            if not chunk:
                raise RuntimeError("Codex app server closed before responding")
            buffer.extend(chunk)

        raw_line, _separator, remainder = buffer.partition(b"\n")
        buffer[:] = remainder
        if len(raw_line) > _MAX_PROTOCOL_LINE_BYTES:
            raise RuntimeError("Codex app server response exceeded the size limit")
        try:
            response = json.loads(raw_line.decode("utf-8"))
        except (TypeError, UnicodeDecodeError, ValueError, RecursionError):
            continue
        if isinstance(response, dict) and response.get("id") == request_id:
            return response
    raise RuntimeError("Codex app server sent too many unrelated messages")


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    """Bound cleanup of the short-lived app server."""

    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def refresh_codex_auth(codex_path: str, home: str) -> bool:
    """Ask Codex's managed auth implementation to refresh its own token."""

    validate_filesystem_path(codex_path, must_exist=True)
    validate_filesystem_path(home, must_exist=True)
    if not os.path.isfile(codex_path) or not os.access(codex_path, os.X_OK):
        raise RuntimeError("Codex executable is not a runnable file")

    environment = os.environ.copy()
    environment.pop("OPENAI_API_KEY", None)
    environment.update(
        {
            "CODEX_HOME": os.path.join(home, ".codex"),
            "HOME": home,
            "PATH": os.pathsep.join(
                (
                    os.path.join(home, ".local", "bin"),
                    os.path.join(home, ".opencode", "bin"),
                    "/usr/local/bin",
                    "/usr/bin",
                    "/bin",
                )
            ),
            "PWD": home,
            "XDG_CACHE_HOME": os.path.join(home, ".cache"),
            "XDG_CONFIG_HOME": os.path.join(home, ".config"),
            "XDG_DATA_HOME": os.path.join(home, ".local", "share"),
            "XDG_STATE_HOME": os.path.join(home, ".local", "state"),
        }
    )
    process = subprocess.Popen(
        [codex_path, "app-server"],
        cwd=home,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + _PROTOCOL_TIMEOUT_SECONDS
    response_buffer = bytearray()
    try:
        if process.stdin is None:
            raise RuntimeError("Codex app server stdin is unavailable")
        _write_message(
            process.stdin,
            {
                "id": _INITIALIZE_REQUEST_ID,
                "method": "initialize",
                "params": {
                    "clientInfo": {
                        "name": "basaltwater-auth-maintenance",
                        "title": "basaltwater Codex authentication maintenance",
                        "version": "1",
                    }
                },
            },
        )
        initialized = _read_response(
            process,
            _INITIALIZE_REQUEST_ID,
            deadline,
            response_buffer,
        )
        if initialized.get("error") is not None:
            raise _response_error(initialized, "initialize")

        _write_message(process.stdin, {"method": "initialized"})
        _write_message(
            process.stdin,
            {
                "id": _ACCOUNT_REQUEST_ID,
                "method": "account/read",
                "params": {"refreshToken": True},
            },
        )
        account_response = _read_response(
            process,
            _ACCOUNT_REQUEST_ID,
            deadline,
            response_buffer,
        )
        if account_response.get("error") is not None:
            raise _response_error(account_response, "account_read")
        result = account_response.get("result")
        if not isinstance(result, dict) or "account" not in result:
            raise AuthRefreshError("invalid_account_response")
        account = result["account"]
        if account is None:
            raise AuthRefreshError("no_account_returned")
        if not isinstance(account, dict) or account.get("type") != "chatgpt":
            raise AuthRefreshError("unexpected_account_type")
        return True
    except (BrokenPipeError, OSError) as exc:
        raise RuntimeError("Codex app server communication failed") from exc
    finally:
        try:
            if process.stdin is not None:
                process.stdin.close()
        except OSError:
            pass
        _stop_process(process)
        try:
            if process.stdout is not None:
                process.stdout.close()
        except OSError:
            pass


def _credential_file_is_private(path: str) -> bool:
    """Require a regular credential readable only by its owning service user."""

    try:
        metadata = os.lstat(path)
    except OSError:
        return False
    return bool(
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == os.getuid()
        and stat.S_IMODE(metadata.st_mode) & 0o077 == 0
    )


def _credential_directory_is_owner_controlled(path: str) -> bool:
    """Reject a credential directory another local account could replace."""

    try:
        metadata = os.lstat(path)
    except OSError:
        return False
    return bool(
        stat.S_ISDIR(metadata.st_mode)
        and metadata.st_uid == os.getuid()
        and stat.S_IMODE(metadata.st_mode) & 0o022 == 0
    )


def maintain_codex_auth(
    *,
    home: str | None = None,
    codex_path: str | None = None,
) -> int:
    """Inspect file-backed auth and refresh only when freshness requires it."""

    if home is None:
        try:
            account = pwd.getpwuid(os.getuid())
        except KeyError:
            log_event(logger, "Could not resolve the Codex maintenance account", level=ERROR)
            return 1
        home = account.pw_dir
    home = os.path.abspath(home)
    validate_filesystem_path(home, must_exist=True)
    codex_home = os.path.join(home, ".codex")
    auth_path = os.path.join(codex_home, "auth.json")
    if not os.path.lexists(auth_path):
        log_event(logger, "Codex credentials not configured, skipping refresh")
        return 0
    if not _credential_directory_is_owner_controlled(codex_home):
        log_event(logger, "Codex credential directory is unsafe", level=ERROR)
        return 1

    if not _credential_file_is_private(auth_path):
        log_event(
            logger,
            "Codex credential file has unsafe ownership, type, or permissions",
            level=ERROR,
        )
        return 1

    metadata = inspect_codex_auth_file(auth_path)
    status = metadata.get("status")
    auth_mode = metadata.get("auth_mode")
    if auth_mode == "api_key":
        log_event(logger, "Codex API-key authentication does not require refresh")
        return 0
    if status == "invalid":
        log_event(logger, "Codex credential file is invalid or unreadable", level=ERROR)
        return 1
    if auth_mode != "chatgpt":
        log_event(logger, "Codex credential mode could not be maintained", level=ERROR)
        return 1
    if metadata.get("refresh_token_present") is not True:
        log_event(logger, "Codex ChatGPT credential has no refresh token", level=ERROR)
        return 1
    if status == "current":
        log_event(logger, "Codex authentication is current")
        return 0

    if codex_path is None:
        codex_path = shutil.which("codex")
    if not codex_path:
        log_event(logger, "Codex executable is unavailable for authentication refresh", level=ERROR)
        return 1

    log_event(logger, "Refreshing Codex authentication", prior_status=status)
    try:
        refreshed = refresh_codex_auth(codex_path, home)
    except AuthRefreshError as exc:
        log_event(
            logger,
            "Codex authentication refresh failed",
            level=ERROR,
            reason=exc.reason,
            rpc_error_code=exc.error_code,
        )
        return 1
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        log_event(
            logger,
            "Codex authentication refresh failed",
            level=ERROR,
            failure_type=type(exc).__name__,
        )
        return 1
    if not refreshed:
        log_event(logger, "Codex authentication refresh did not return a ChatGPT account", level=ERROR)
        return 1

    if not _credential_file_is_private(auth_path):
        log_event(
            logger,
            "Codex credential file became unsafe during refresh",
            level=ERROR,
        )
        return 1
    updated = inspect_codex_auth_file(auth_path)
    if (
        updated.get("auth_mode") != "chatgpt"
        or updated.get("status") != "current"
        or updated.get("refresh_token_present") is not True
    ):
        log_event(
            logger,
            "Codex authentication remained stale after refresh",
            level=ERROR,
        )
        return 1
    log_event(logger, "Codex authentication refreshed successfully")
    return 0


def main() -> int:
    """Run one scheduled authentication maintenance check."""

    try:
        return maintain_codex_auth()
    except (OSError, RuntimeError, ValueError) as exc:
        log_event(
            logger,
            "Codex authentication maintenance failed",
            level=ERROR,
            failure_type=type(exc).__name__,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
