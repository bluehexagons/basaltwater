#!/usr/bin/env python3
"""Run a command and retain its output in private temporary files."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from typing import Sequence


_CLEANUP_SECONDS = 2.0


def _positive_finite(value: str) -> float:
    try:
        result = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be a finite positive number") from exc
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("timeout must be a finite positive number")
    return result


def _terminate_group(process: subprocess.Popen[bytes]) -> None:
    """Stop the child process group with bounded TERM then KILL waits."""
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    else:
        try:
            process.terminate()
        except ProcessLookupError:
            pass

    try:
        process.wait(timeout=_CLEANUP_SECONDS)
    except subprocess.TimeoutExpired:
        pass

    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=_CLEANUP_SECONDS)
    except subprocess.TimeoutExpired:
        pass


def _private_file(path: Path):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.fchmod(descriptor, 0o600)
    return os.fdopen(descriptor, "wb")


def _emit(result: dict[str, object]) -> None:
    print(json.dumps(result, separators=(",", ":")))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=_positive_finite, default=300.0)
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if "--" not in raw_args:
        parser.error("separate the command and its arguments with --")
    separator = raw_args.index("--")
    args = parser.parse_args(raw_args[:separator])
    command = raw_args[separator + 1:]
    if not command:
        parser.error("provide a command after --")

    artifact_dir = Path(tempfile.mkdtemp(prefix="basaltwater-output-"))
    os.chmod(artifact_dir, 0o700)
    stdout_path = artifact_dir / "stdout.log"
    stderr_path = artifact_dir / "stderr.log"
    started = time.monotonic()
    status = "completed"
    return_code = 127
    launch_error: str | None = None

    with _private_file(stdout_path) as stdout_file, _private_file(stderr_path) as stderr_file:
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                shell=False,
                start_new_session=(os.name == "posix"),
            )
        except OSError as exc:
            status = "launch_error"
            detail = exc.strerror or "could not start command"
            launch_error = f"{detail} (errno {exc.errno})" if exc.errno is not None else detail
        else:
            try:
                return_code = process.wait(timeout=args.timeout)
                if return_code < 0:
                    return_code = 128 + -return_code
            except subprocess.TimeoutExpired:
                status = "timeout"
                return_code = 124
                _terminate_group(process)
            except KeyboardInterrupt:
                status = "interrupted"
                return_code = 130
                _terminate_group(process)

    result: dict[str, object] = {
        "status": status,
        "stdout_path": str(stdout_path),
        "stdout_bytes": stdout_path.stat().st_size,
        "stderr_path": str(stderr_path),
        "stderr_bytes": stderr_path.stat().st_size,
        "return_code": return_code,
        "duration_seconds": round(time.monotonic() - started, 3),
    }
    if launch_error is not None:
        result["error"] = launch_error
    _emit(result)
    return return_code


if __name__ == "__main__":
    sys.exit(main())
