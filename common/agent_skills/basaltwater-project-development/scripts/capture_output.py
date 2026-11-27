#!/usr/bin/env python3
"""Run a command and retain its output in private temporary files."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
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


def _terminate_group(process: subprocess.Popen[bytes]) -> str | None:
    """Stop the owned process group/tree and report uncertain Windows cleanup."""
    if os.name == "nt":
        # Killing the parent first loses the ancestry taskkill needs for /T.
        taskkill = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "taskkill.exe")
        cleanup_error = None
        try:
            result = subprocess.run(
                [taskkill, "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=_CLEANUP_SECONDS, check=False,
            )
            if result.returncode:
                cleanup_error = f"Windows process-tree cleanup failed (exit code {result.returncode})"
        except (OSError, subprocess.TimeoutExpired) as exc:
            cleanup_error = f"Windows process-tree cleanup failed ({type(exc).__name__})"
        if cleanup_error is not None:
            try:
                process.kill()
            except OSError:
                pass
        try:
            process.wait(timeout=_CLEANUP_SECONDS)
        except (OSError, subprocess.TimeoutExpired):
            cleanup_error = "Windows process did not exit within the cleanup deadline"
        return cleanup_error

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
    try:
        if os.name == "posix":
            os.fchmod(descriptor, 0o600)
        return os.fdopen(descriptor, "wb")
    except OSError:
        os.close(descriptor)
        raise


def _emit(result: dict[str, object]) -> None:
    print(json.dumps(result, separators=(",", ":")))


def _save_summary(path: Path, result: dict[str, object]) -> None:
    """Replace a private summary atomically, including on native Windows."""
    descriptor, temporary = tempfile.mkstemp(prefix=".summary-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            if os.name == "posix":
                os.fchmod(stream.fileno(), 0o600)
            json.dump(result, stream, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit one JSON result object (the default)")
    parser.add_argument("--timeout", type=_positive_finite, default=300.0)
    parser.add_argument("--output-directory", type=Path,
                        help="create a new private directory under an existing parent for logs and summary.json")
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if "--" not in raw_args:
        parser.error("separate the command and its arguments with --")
    separator = raw_args.index("--")
    args = parser.parse_args(raw_args[:separator])
    command = raw_args[separator + 1:]
    if not command:
        parser.error("provide a command after --")

    try:
        if args.output_directory is None:
            artifact_dir = Path(tempfile.mkdtemp(prefix="basaltwater-output-"))
        else:
            artifact_dir = args.output_directory.expanduser().absolute()
            artifact_dir.mkdir(mode=0o700)
        os.chmod(artifact_dir, 0o700)
    except (OSError, ValueError):
        issue = "invalid_input" if args.output_directory else "artifact_save_failed"
        _emit({"schema_version": 1, "ok": False, "issue": issue, "stage": "allocate", "effect": "none",
               "error": "Capture needs a new private directory under an existing writable parent"})
        return 2 if args.output_directory else 1
    stdout_path = artifact_dir / "stdout.log"
    stderr_path = artifact_dir / "stderr.log"
    summary_path = artifact_dir / "summary.json"
    started = time.monotonic()
    status = "completed"
    return_code = 127
    launch_error: str | None = None
    cleanup_error: str | None = None

    result: dict[str, object] = {
        "schema_version": 1,
        "status": "in_progress",
        "summary_path": str(summary_path),
        "stdout_path": str(stdout_path), "stdout_bytes": 0,
        "stderr_path": str(stderr_path), "stderr_bytes": 0,
        "return_code": None, "duration_seconds": None,
    }
    with ExitStack() as files:
        try:
            stdout_file = files.enter_context(_private_file(stdout_path))
            stderr_file = files.enter_context(_private_file(stderr_path))
            _save_summary(summary_path, result)
        except OSError as exc:
            _emit({"schema_version": 1, "ok": False, "issue": "artifact_save_failed", "stage": "allocate",
                   "effect": "none", "error": f"Could not open private capture files: {exc}"})
            return 1
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
                cleanup_error = _terminate_group(process)
            except KeyboardInterrupt:
                status = "interrupted"
                return_code = 130
                cleanup_error = _terminate_group(process)

    result.update(status=status, return_code=return_code, duration_seconds=round(time.monotonic() - started, 3))
    if launch_error is not None:
        result["error"] = launch_error
    if cleanup_error is not None:
        result["cleanup_error"] = cleanup_error
    view = {"ok": status == "completed" and return_code == 0,
            "child_exit_code": return_code if status == "completed" else None}
    artifacts = [("stdout_log", stdout_path, "text/plain"), ("stderr_log", stderr_path, "text/plain")]
    if not view["ok"]:
        view["issue"] = {"timeout": "wait_timeout", "launch_error": "launch_failed",
                         "interrupted": "interrupted"}.get(status, "child_failed")
        view["error"] = launch_error or "Inspect the captured command's result and retained logs."
        view["stage"] = "launch" if status == "launch_error" else "child_execution"
        view["effect"] = "none" if status == "launch_error" else "unknown"
    try:
        result.update(stdout_bytes=stdout_path.stat().st_size, stderr_bytes=stderr_path.stat().st_size)
        _save_summary(summary_path, result)
        artifacts.insert(0, ("command_summary", summary_path, "application/json"))
    except OSError as exc:
        view["summary_save_error"] = str(exc)
        if view["ok"]:
            view.update(ok=False, issue="artifact_save_failed", stage="retain_summary", effect="unknown",
                        error="Command finished but its summary could not be retained.")
            return_code = 1
    view["artifacts"] = [{"kind": kind, "path": str(path), "media_type": media_type}
                         for kind, path, media_type in artifacts if path.is_file()]
    _emit({**result, **view})
    return return_code


if __name__ == "__main__":
    sys.exit(main())
