#!/usr/bin/env python3
"""Keep T3 Code startup credentials out of persistent service logs."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import pwd
import re
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import threading
from typing import BinaryIO

SOURCE_ROOT = str(Path(__file__).resolve().parents[2])
if SOURCE_ROOT not in sys.path:
    sys.path.insert(0, SOURCE_ROOT)

from lib.validation import validate_filesystem_path


_REDACTED = "[redacted]"
_SERVICE_RELATIVE_PATH = Path(".config/systemd/user/t3code.service")
_LOG_RELATIVE_PATH = Path(".t3/userdata/logs/boot-service.log")
# Bump this when redaction rules change so existing logs are rescanned once.
_LOG_FILTER_MARKER = ".basaltwater-t3-log-filter"
_SERVICE_LOG_ROTATION = re.compile(r"^boot-service\.log\.[1-9][0-9]*$")
_SENSITIVE_FIELD = re.compile(
    r"(?i)(?P<prefix>['\"]?[a-z0-9_-]*"
    r"(?:api[_-]?key|authorization|cookie|credential|pairing[_-]?url|"
    r"pairurl|password|secret|token)['\"]?\s*[:=]\s*)"
    r"(?P<value>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s,;}\]]+)"
)
_PLAIN_SECRET_LABEL = re.compile(
    r"(?i)(?P<prefix>\b(?:access\s+token|authorization|cookie|credential|"
    r"pairing\s+url|pairurl|password|refresh\s+token|token)\s*:\s*).*$"
)
_BEARER_TOKEN = re.compile(r"(?i)(\bbearer\s+)\S+")
_QR_CHARACTERS = frozenset(" \t\r\n█▀▄")
_QR_MARKERS = frozenset("█▀▄")
_MAX_UNIT_BYTES = 256 * 1024
_FILTER_REVISION = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class T3ServiceError(RuntimeError):
    """A safe-to-log failure while resolving or supervising the T3 service."""


def redact_line(line: str) -> str:
    """Redact credential fields and terminal QR rows from one output line."""

    qr_characters = set(line.rstrip("\r\n"))
    if qr_characters <= _QR_CHARACTERS and qr_characters & _QR_MARKERS:
        return ""
    redacted = _PLAIN_SECRET_LABEL.sub(lambda match: match["prefix"] + _REDACTED, line)

    def redact_field(match: re.Match[str]) -> str:
        value = match["value"]
        if value.startswith('"'):
            replacement = f'"{_REDACTED}"'
        elif value.startswith("'"):
            replacement = f"'{_REDACTED}'"
        else:
            replacement = _REDACTED
        return match["prefix"] + replacement

    redacted = _SENSITIVE_FIELD.sub(
        redact_field,
        redacted,
    )
    return _BEARER_TOKEN.sub(lambda match: match[1] + _REDACTED, redacted)


def filter_stream(source: BinaryIO, destination: BinaryIO) -> None:
    """Copy a child stream while redacting each complete output line."""

    for raw_line in source:
        decoded = raw_line.decode("utf-8", errors="surrogateescape")
        safe_line = redact_line(decoded)
        if safe_line:
            destination.write(safe_line.encode("utf-8", errors="surrogateescape"))
            destination.flush()


def parse_systemd_exec_start(contents: str) -> list[str]:
    """Read the single upstream ExecStart command from a T3 user unit."""

    in_service = False
    commands: list[str] = []
    for line in contents.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_service = stripped == "[Service]"
            continue
        if not in_service or stripped.startswith(("#", ";")):
            continue
        if stripped.startswith("ExecStart="):
            value = stripped[len("ExecStart=") :]
            if not value:
                commands.clear()
            else:
                commands.append(value)

    if len(commands) != 1:
        raise T3ServiceError("The T3 user service has no single upstream start command")
    try:
        arguments = shlex.split(commands[0], posix=True)
    except ValueError as exc:
        raise T3ServiceError("The T3 user service start command is malformed") from exc
    if not arguments or not os.path.isabs(arguments[0]):
        raise T3ServiceError("The T3 user service start command is not an absolute path")
    # T3 escapes systemd specifier markers as %% when rendering its unit.
    decoded_arguments = [argument.replace("%%", "%") for argument in arguments]
    validate_filesystem_path(decoded_arguments[0], must_exist=True)
    return decoded_arguments


def resolve_upstream_command(home: str | os.PathLike[str]) -> list[str]:
    """Resolve T3's current command from its own service file, not a pinned CLI."""

    unit_path = Path(home) / _SERVICE_RELATIVE_PATH
    validate_filesystem_path(str(unit_path), must_exist=True)
    try:
        info = unit_path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_UNIT_BYTES:
            raise T3ServiceError("The T3 user service file is not a regular unit")
        contents = unit_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise T3ServiceError("The T3 user service file could not be read") from exc
    command = parse_systemd_exec_start(contents)
    if not os.path.isfile(command[0]) or not os.access(command[0], os.X_OK):
        raise T3ServiceError("The T3 user service executable is unavailable")
    return command


def sanitize_existing_log(path: str | os.PathLike[str]) -> bool:
    """Redact a stopped service log in place, preserving open append handles."""

    log_path = Path(path)
    validate_filesystem_path(str(log_path), must_exist=False)
    try:
        original_info = log_path.lstat()
    except FileNotFoundError:
        return False
    if (
        not stat.S_ISREG(original_info.st_mode)
        or original_info.st_uid != os.geteuid()
    ):
        return False

    temporary_path: str | None = None
    changed = False
    try:
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=f".{log_path.name}-redact-",
            dir=str(log_path.parent),
        )
        with os.fdopen(descriptor, "wb") as destination:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            source_descriptor = os.open(log_path, flags)
            with os.fdopen(source_descriptor, "rb") as source:
                source_info = os.fstat(source.fileno())
                if (source_info.st_dev, source_info.st_ino) != (
                    original_info.st_dev,
                    original_info.st_ino,
                ):
                    raise T3ServiceError("The T3 service log changed during sanitization")
                for raw_line in source:
                    original_line = raw_line.decode(
                        "utf-8",
                        errors="surrogateescape",
                    )
                    safe_line = redact_line(original_line)
                    encoded = safe_line.encode("utf-8", errors="surrogateescape")
                    if encoded != raw_line:
                        changed = True
                    destination.write(encoded)
            if not changed:
                return False
            destination.flush()
            os.fsync(destination.fileno())
        write_flags = os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
        write_descriptor = os.open(log_path, write_flags)
        with os.fdopen(write_descriptor, "wb") as destination:
            write_info = os.fstat(destination.fileno())
            if (write_info.st_dev, write_info.st_ino) != (
                original_info.st_dev,
                original_info.st_ino,
            ):
                raise T3ServiceError("The T3 service log changed during sanitization")
            os.ftruncate(destination.fileno(), 0)
            os.lseek(destination.fileno(), 0, os.SEEK_SET)
            with open(temporary_path, "rb") as sanitized:
                while chunk := sanitized.read(64 * 1024):
                    destination.write(chunk)
            destination.flush()
            os.fsync(destination.fileno())
        return True
    except T3ServiceError:
        raise
    except OSError as exc:
        raise T3ServiceError("The existing T3 service log could not be sanitized") from exc
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass


def sanitize_managed_log(home: str | os.PathLike[str]) -> bool:
    """Sanitize the old log once per filter version before the service starts."""

    logs_dir = Path(home) / _LOG_RELATIVE_PATH.parent
    log_path = Path(home) / _LOG_RELATIVE_PATH
    marker_path = logs_dir / _LOG_FILTER_MARKER
    if logs_dir.is_symlink() or not logs_dir.is_dir():
        return False
    try:
        marker_info = marker_path.lstat()
        if (
            stat.S_ISREG(marker_info.st_mode)
            and marker_info.st_uid == os.geteuid()
            and marker_path.read_text(encoding="ascii") == f"{_FILTER_REVISION}\n"
        ):
            return False
    except FileNotFoundError:
        pass
    except (OSError, UnicodeError):
        pass

    try:
        log_paths = [
            path
            for path in logs_dir.iterdir()
            if path.name == _LOG_RELATIVE_PATH.name
            or _SERVICE_LOG_ROTATION.fullmatch(path.name)
        ]
    except OSError as exc:
        raise T3ServiceError("The existing T3 service logs could not be listed") from exc
    eligible = True
    sanitized = False
    for service_log in log_paths:
        try:
            log_info = service_log.lstat()
        except FileNotFoundError:
            continue
        if (
            not stat.S_ISREG(log_info.st_mode)
            or log_info.st_uid != os.geteuid()
        ):
            eligible = False
            continue
        sanitized = sanitize_existing_log(service_log) or sanitized

    if not eligible or marker_path.is_symlink() or (
        marker_path.exists() and not marker_path.is_file()
    ):
        return sanitized
    try:
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=f".{_LOG_FILTER_MARKER}-",
            dir=str(logs_dir),
        )
    except OSError as exc:
        raise T3ServiceError("The T3 service log-filter marker could not be created") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as marker:
            marker.write(f"{_FILTER_REVISION}\n")
            marker.flush()
            os.fsync(marker.fileno())
            os.fchmod(marker.fileno(), 0o600)
        os.replace(temporary_path, marker_path)
    except OSError as exc:
        raise T3ServiceError("The T3 service log-filter marker could not be written") from exc
    finally:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
    return sanitized


def _run_filtered_command(
    command: list[str],
    *,
    stdout: BinaryIO,
    stderr: BinaryIO,
) -> int:
    """Run T3 and filter both output streams without buffering the service log."""

    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
    except OSError as exc:
        raise T3ServiceError("The T3 service command could not be started") from exc

    output_lock = threading.Lock()
    stream_errors: list[OSError] = []

    def pump(source: BinaryIO | None, destination: BinaryIO) -> None:
        if source is None:
            return
        try:
            for raw_line in source:
                decoded = raw_line.decode("utf-8", errors="surrogateescape")
                safe_line = redact_line(decoded)
                if safe_line:
                    with output_lock:
                        destination.write(
                            safe_line.encode("utf-8", errors="surrogateescape")
                        )
                        destination.flush()
        except OSError as exc:
            stream_errors.append(exc)
            if process.poll() is None:
                process.terminate()
        finally:
            source.close()

    readers = [
        threading.Thread(target=pump, args=(process.stdout, stdout), daemon=True),
        threading.Thread(target=pump, args=(process.stderr, stderr), daemon=True),
    ]
    previous_handlers: dict[int, object] = {}

    def forward_signal(signum: int, _frame: object) -> None:
        if process.poll() is None:
            try:
                process.send_signal(signum)
            except ProcessLookupError:
                pass

    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        previous_handlers[signum] = signal.signal(signum, forward_signal)
    try:
        for reader in readers:
            reader.start()
        return_code = process.wait()
        for reader in readers:
            reader.join()
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        if process.poll() is None:
            process.terminate()
            process.wait()
    if stream_errors:
        raise T3ServiceError("The T3 service output could not be filtered")
    return return_code if return_code >= 0 else 128 + abs(return_code)


def _current_home() -> Path:
    try:
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except (KeyError, OSError) as exc:
        raise T3ServiceError("The T3 service user's home directory is unavailable") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("upstream", "exec", "sanitize-log"))
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    try:
        if args.mode == "sanitize-log":
            sanitize_managed_log(_current_home())
            return 0
        if args.mode == "upstream":
            command = resolve_upstream_command(_current_home())
        else:
            command = args.command
            if command and command[0] == "--":
                command = command[1:]
            if not command or not os.path.isabs(command[0]):
                raise T3ServiceError("The managed T3 service command is invalid")
        return _run_filtered_command(
            command,
            stdout=sys.stdout.buffer,
            stderr=sys.stderr.buffer,
        )
    except T3ServiceError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
