#!/usr/bin/env python3
"""Run full PAR2 scrubs in a deadline-supervised process group."""

from __future__ import annotations

import grp
import json
import math
import os
import pwd
import select
import subprocess
import sys
import tempfile
import time
from typing import Any

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from lib.streamed_process import caller_process_group
from lib.validation import (
    validate_database_path,
    validate_filesystem_path,
    validate_redundancy_percentage,
)
from lib.validators import validate_username
from sync.service_tools.sync_rsync import (
    _process_group_exists,
    _terminate_process_group,
)


SCRUB_PREFLIGHT_TIMEOUT_SECONDS = 120
SCRUB_OPERATION_TIMEOUT_SECONDS = 14400
WORKER_REQUEST_LIMIT = 256 * 1024
WORKER_REPORT_LIMIT = 16 * 1024
WORKER_SAMPLE_LIMIT = 5
WORKER_SAMPLE_PATH_LIMIT = 256
WORKER_ERROR_LIMIT = 4096
WORKER_STARTED_MARKER = b"SCRUB\n"
WORKER_SCRIPT = os.path.abspath(__file__)


def _scrub_log_file(log_dir: str, directory: str, database: str) -> str:
    """Return the stable per-scrub log path without touching the filesystem."""
    import hashlib

    scrub_id = hashlib.md5(f"{directory}:{database}".encode()).hexdigest()[:8]
    return os.path.join(log_dir, f"scrub-{scrub_id}.log")


def _mount_declarations(config: Any) -> tuple[list[str], list[str]]:
    """Copy only mount paths from a setup/runtime config, never SMB credentials."""
    from lib.task_utils import configured_storage_mounts

    mounts = configured_storage_mounts(config)
    smb_specs = getattr(config, "smb_mounts", None) or []
    if not isinstance(smb_specs, (list, tuple)):
        raise ValueError("Invalid configured SMB mounts")
    smb_mounts: list[str] = []
    for spec in smb_specs:
        if not isinstance(spec, (list, tuple)) or not spec or not isinstance(spec[0], str):
            raise ValueError("Invalid configured SMB mount declaration")
        smb_mounts.append(os.path.abspath(spec[0]))
    return mounts, sorted(set(smb_mounts))


def _validate_request_inputs(
    directory: str, database: str, redundancy: int | str, username: str,
    log_dir: str, verify: bool, suppress_notifications: bool,
) -> tuple[int, str, str]:
    """Apply input-only checks before starting the supervised worker."""
    for path, label in ((directory, "Scrub directory"), (database, "Scrub database"),
                        (log_dir, "Scrub log directory")):
        validate_filesystem_path(path)
        if not os.path.isabs(path):
            raise ValueError(f"{label} must be an absolute path: {path}")
    validate_database_path(database)
    if not isinstance(username, str) or not validate_username(username):
        raise ValueError("Invalid scrub username")
    if type(verify) is not bool or type(suppress_notifications) is not bool:
        raise ValueError("Invalid scrub worker flags")
    redundancy_value = validate_redundancy_percentage(str(redundancy))
    if not 1 <= redundancy_value <= 100:
        raise ValueError("Redundancy percentage must be between 1 and 100")
    return redundancy_value, os.path.abspath(log_dir), _scrub_log_file(log_dir, directory, database)


def _worker_request(
    directory: str, database: str, redundancy: int | str, verify: bool, config: Any, *,
    log_dir: str, log_file: str | None, suppress_notifications: bool,
) -> dict[str, Any]:
    """Build a small request containing only scrub and mount declarations."""
    username = getattr(config, "username", None)
    redundancy_value, normalized_log_dir, default_log_file = _validate_request_inputs(
        directory, database, redundancy, username, log_dir, verify, suppress_notifications,
    )
    mounts, smb_mounts = _mount_declarations(config)
    worker_log_file = default_log_file if log_file is None else os.path.abspath(log_file)
    validate_filesystem_path(worker_log_file)
    return {
        "version": 1,
        "directory": directory,
        "database": database,
        "redundancy": redundancy_value,
        "verify": verify,
        "suppress_notifications": suppress_notifications,
        "username": username,
        "mounts": mounts,
        "smb_mounts": smb_mounts,
        "log_dir": normalized_log_dir,
        "log_file": worker_log_file,
    }


def _base_report(*, error: str | None = None) -> dict[str, Any]:
    return {
        "version": 1,
        "ok": False,
        "completed": False,
        "files_processed": 0,
        "files_created": 0,
        "files_verified": 0,
        "files_repaired": 0,
        "files_failed_count": 0,
        "files_unrepairable_count": 0,
        "files_unrepairable_sample": [],
        "directory_disk_usage_percent": None,
        "database_disk_usage_percent": None,
        "error": error[:WORKER_ERROR_LIMIT] if error else None,
    }


def _report_from_result(result: Any, directory_usage: dict[str, int],
                        database_usage: dict[str, int]) -> dict[str, Any]:
    if not isinstance(result, dict) or type(result.get("ok")) is not bool \
            or type(result.get("completed")) is not bool:
        raise ValueError("PAR2 scrub returned an invalid result")
    failed = result.get("files_failed", [])
    unrepairable = result.get("files_unrepairable", [])
    if (not isinstance(failed, list) or any(not isinstance(path, str) for path in failed)
            or not isinstance(unrepairable, list)
            or any(not isinstance(path, str) for path in unrepairable)):
        raise ValueError("PAR2 scrub returned invalid file findings")
    report = _base_report()
    report.update({
        "ok": result["ok"],
        "completed": result["completed"],
        "files_processed": result.get("files_processed", 0),
        "files_created": result.get("files_created", 0),
        "files_verified": result.get("files_verified", 0),
        "files_repaired": result.get("files_repaired", 0),
        "files_failed_count": len(failed),
        "files_unrepairable_count": len(unrepairable),
        "files_unrepairable_sample": [
            path[:WORKER_SAMPLE_PATH_LIMIT] for path in unrepairable[:WORKER_SAMPLE_LIMIT]
        ],
        "directory_disk_usage_percent": directory_usage["usage_percent"],
        "database_disk_usage_percent": database_usage["usage_percent"],
    })
    for key in ("files_processed", "files_created", "files_verified", "files_repaired"):
        if type(report[key]) is not int or report[key] < 0:
            raise ValueError(f"PAR2 scrub returned an invalid {key} count")
    if report["ok"] and (not report["completed"] or report["files_failed_count"]
                          or report["files_unrepairable_count"]):
        raise ValueError("PAR2 scrub reported inconsistent completion state")
    if report["completed"] and report["files_failed_count"]:
        raise ValueError("PAR2 scrub reported completion with operational failures")
    if report["completed"] and not report["ok"] and not report["files_unrepairable_count"]:
        raise ValueError("PAR2 scrub reported a damaged completion without findings")
    for key in ("directory_disk_usage_percent", "database_disk_usage_percent"):
        if type(report[key]) is not int or not 0 <= report[key] <= 100:
            raise ValueError(f"PAR2 scrub returned invalid {key}")
    return report


def _write_worker_report(report_fd: int, report: dict[str, Any]) -> None:
    payload = json.dumps(report, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > WORKER_REPORT_LIMIT:
        payload = json.dumps(
            _base_report(error="Scrub worker report exceeds size limit"),
            separators=(",", ":"),
        ).encode("utf-8")
    with os.fdopen(report_fd, "wb") as stream:
        stream.write(payload)
        stream.flush()


def _ensure_directory(path: str, username: str) -> None:
    """Create and own a directory inline so chown stays in the worker group."""
    if os.path.exists(path):
        if not os.path.isdir(path):
            raise NotADirectoryError(f"Path exists but is not a directory: {path}")
        return
    os.makedirs(path, exist_ok=True)
    user = pwd.getpwnam(username)
    group = grp.getgrnam(username)
    os.chown(path, user.pw_uid, group.gr_gid)


def _validate_worker_mounts(directory: str, database: str, config: Any) -> None:
    """Reject unavailable endpoints and every declared mount inside either tree."""
    from lib.task_utils import configured_storage_mounts
    from sync.service_tools.storage_ops import validate_mounts_for_operation

    for path, label in ((directory, "scrub"), (database, "scrub")):
        valid, error = validate_mounts_for_operation([path], config, label)
        if not valid:
            raise RuntimeError(error)
    endpoints = (os.path.abspath(directory), os.path.abspath(database))
    for mount in configured_storage_mounts(config):
        if any(mount != endpoint and os.path.commonpath([endpoint, mount]) == endpoint
               for endpoint in endpoints):
            valid, error = validate_mounts_for_operation([mount], config, "nested scrub mount")
            if not valid:
                raise RuntimeError(error)


def _perform_worker_scrub(request: dict[str, Any], started_fd: int) -> dict[str, Any]:
    """Validate and prepare paths, then scrub and gather metrics in this worker."""
    from lib.disk_utils import get_disk_usage_details
    from lib.mount_utils import validate_smb_connectivity
    from lib.runtime_config import RuntimeConfig
    from lib.task_utils import check_path_on_smb_mount
    from sync.service_tools.scrub_par2 import _confined_path, scrub_directory

    config = RuntimeConfig.from_dict({
        "username": request["username"],
        "storage_mounts": [["scrub", root] for root in request["mounts"]],
        "smb_mounts": [[root] for root in request["smb_mounts"]],
    })
    directory, database = request["directory"], request["database"]
    validate_filesystem_path(directory, must_exist=True)
    validate_database_path(database)
    _validate_worker_mounts(directory, database, config)
    _confined_path(directory, directory)
    _confined_path(database, database)
    if not os.path.isdir(directory):
        raise ValueError(f"Scrub root is not an existing directory: {directory}")

    _ensure_directory(directory, request["username"])
    database_parent = os.path.dirname(database) or os.path.sep
    _ensure_directory(database_parent, request["username"])

    smb_checks = []
    if check_path_on_smb_mount(directory, config):
        smb_checks.append((directory, "Source"))
    if check_path_on_smb_mount(database, config):
        probe_path = database if os.path.isdir(database) else database_parent
        smb_checks.append((probe_path, "Database"))
    for path, label in smb_checks:
        if not validate_smb_connectivity(path):
            raise RuntimeError(f"{label} SMB connectivity failed: {path}")

    os.makedirs(request["log_dir"], exist_ok=True)
    os.write(started_fd, WORKER_STARTED_MARKER)
    result = scrub_directory(
        directory,
        database,
        request["redundancy"],
        request["log_file"],
        verify=request["verify"],
        suppress_notifications=request["suppress_notifications"],
    )
    directory_usage = get_disk_usage_details(directory)
    database_usage = get_disk_usage_details(database_parent)
    return _report_from_result(result, directory_usage, database_usage)


def _worker_config_is_valid(request: Any) -> bool:
    expected = {
        "version", "directory", "database", "redundancy", "verify",
        "suppress_notifications", "username", "mounts", "smb_mounts",
        "log_dir", "log_file",
    }
    if (not isinstance(request, dict) or set(request) != expected
            or type(request.get("version")) is not int or request["version"] != 1):
        return False
    if (not all(isinstance(request.get(key), str) for key in
                ("directory", "database", "username", "log_dir", "log_file"))
            or type(request.get("redundancy")) is not int
            or type(request.get("verify")) is not bool
            or type(request.get("suppress_notifications")) is not bool):
        return False
    if any(not os.path.isabs(request[key]) for key in ("directory", "database", "log_dir", "log_file")):
        return False
    if not validate_username(request["username"]):
        return False
    if not 1 <= request["redundancy"] <= 100:
        return False
    paths_are_valid = all(
        isinstance(request.get(key), list)
        and all(isinstance(path, str) and os.path.isabs(path) for path in request[key])
        for key in ("mounts", "smb_mounts")
    )
    if not paths_are_valid:
        return False
    try:
        for key in ("directory", "database", "log_dir", "log_file"):
            validate_filesystem_path(request[key])
        validate_database_path(request["database"])
        for key in ("mounts", "smb_mounts"):
            for path in request[key]:
                validate_filesystem_path(path)
    except (OSError, TypeError, ValueError):
        return False
    return True


def _scrub_worker(report_fd: int, started_fd: int) -> int:
    report = _base_report(error="Scrub worker failed before completion")
    try:
        request = json.load(sys.stdin)
        if not _worker_config_is_valid(request):
            raise ValueError("Invalid scrub worker request")
        with caller_process_group():
            report = _perform_worker_scrub(request, started_fd)
    except Exception as exc:
        report = _base_report(error=f"{type(exc).__name__}: {exc}")
        print(f"Scrub worker failed: {exc}", file=sys.stderr)
    finally:
        try:
            _write_worker_report(report_fd, report)
        except OSError as exc:
            print(f"Scrub worker could not write its report: {exc}", file=sys.stderr)
            return_code = 1
        else:
            return_code = 0
        try:
            os.close(started_fd)
        except OSError:
            pass
    return return_code


def _read_phase(phase_fd: int, pending: bytes, *, wait: float) -> tuple[bytes, bool, bool]:
    readable, _, _ = select.select([phase_fd], [], [], wait)
    if not readable:
        return pending, False, False
    chunk = os.read(phase_fd, 64)
    if not chunk:
        return pending, False, True
    pending += chunk
    if pending == WORKER_STARTED_MARKER:
        return b"", True, False
    if WORKER_STARTED_MARKER.startswith(pending):
        return pending, False, False
    return pending, False, False


def _supervise_worker(
    process: subprocess.Popen[Any], phase_fd: int, *,
    preflight_timeout: float, operation_timeout: float,
) -> tuple[bool, str | None]:
    """Apply separate preflight and work deadlines, then confirm group exit."""
    started = False
    phase_pending = b""
    phase_closed = False
    phase_error: str | None = None
    deadline = time.monotonic() + preflight_timeout

    while process.poll() is None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if process.poll() is None:
                phase = "scrub" if started else "scrub preflight"
                _terminate_process_group(process)
                return started, f"{phase} timed out after {operation_timeout if started else preflight_timeout:g}s"
            break
        if not phase_closed:
            phase_pending, received, phase_closed = _read_phase(
                phase_fd, phase_pending, wait=min(0.1, remaining),
            )
            if received:
                if started:
                    phase_error = "Scrub worker sent a duplicate phase marker"
                else:
                    started = True
                    deadline = time.monotonic() + operation_timeout
            elif phase_pending and not WORKER_STARTED_MARKER.startswith(phase_pending):
                phase_error = "Scrub worker sent an invalid phase marker"
            elif phase_closed and phase_pending:
                phase_error = "Scrub worker closed an incomplete phase marker"
        else:
            try:
                process.wait(timeout=min(0.1, remaining))
            except subprocess.TimeoutExpired:
                pass
        if phase_error is not None:
            _terminate_process_group(process)
            return started, phase_error

    try:
        returncode = process.wait()
    except KeyboardInterrupt:
        _terminate_process_group(process)
        raise

    # Drain the one short marker after a fast worker exit before deciding
    # whether a successful report is allowed to claim that work started.
    while not phase_closed:
        phase_pending, received, phase_closed = _read_phase(phase_fd, phase_pending, wait=0)
        if received:
            if started:
                phase_error = "Scrub worker sent a duplicate phase marker"
            else:
                started = True
        if not received and not phase_closed:
            break
    if phase_pending and not WORKER_STARTED_MARKER.startswith(phase_pending):
        phase_error = "Scrub worker sent an invalid phase marker"
    if phase_error is not None:
        _terminate_process_group(process)
        return started, phase_error

    if _process_group_exists(process.pid):
        _terminate_process_group(process)
        return started, "Scrub worker exited while descendants remained in its process group"
    if returncode != 0:
        return started, f"Scrub worker exited with status {returncode}"
    return started, None


def _read_worker_report(stream) -> dict[str, Any]:
    stream.seek(0)
    payload = stream.read(WORKER_REPORT_LIMIT + 1)
    if len(payload) > WORKER_REPORT_LIMIT:
        raise ValueError("Scrub worker report exceeds size limit")
    report = json.loads(payload)
    expected = set(_base_report())
    if not isinstance(report, dict) or set(report) != expected:
        raise ValueError("Invalid scrub worker report")
    if (type(report["version"]) is not int or report["version"] != 1
            or type(report["ok"]) is not bool or type(report["completed"]) is not bool
            or any(type(report[key]) is not int or report[key] < 0 for key in (
                "files_processed", "files_created", "files_verified", "files_repaired",
                "files_failed_count", "files_unrepairable_count",
            ))
            or not isinstance(report["files_unrepairable_sample"], list)
            or len(report["files_unrepairable_sample"]) > WORKER_SAMPLE_LIMIT
            or any(not isinstance(path, str) or not path
                   or len(path) > WORKER_SAMPLE_PATH_LIMIT
                   for path in report["files_unrepairable_sample"])
            or report["files_unrepairable_count"] < len(report["files_unrepairable_sample"])
            or bool(report["files_unrepairable_count"]) != bool(report["files_unrepairable_sample"])
            or len(report["files_unrepairable_sample"]) != min(
                report["files_unrepairable_count"], WORKER_SAMPLE_LIMIT,
            )
            or any(report[key] is not None and
                   (type(report[key]) is not int or not 0 <= report[key] <= 100)
                   for key in ("directory_disk_usage_percent", "database_disk_usage_percent"))
            or (report["error"] is not None and
                (not isinstance(report["error"], str) or len(report["error"]) > WORKER_ERROR_LIMIT))
            or (report["ok"] and (not report["completed"] or report["files_failed_count"]
                                  or report["files_unrepairable_count"] or report["error"] is not None))
            or (report["completed"] and report["files_failed_count"])
            or (report["completed"] and not report["ok"]
                and not report["files_unrepairable_count"])
            or (report["completed"] and report["error"] is not None)
            or (report["completed"] and any(report[key] is None for key in (
                "directory_disk_usage_percent", "database_disk_usage_percent",
            )))):
        raise ValueError("Invalid scrub worker report")
    return {
        **report,
        "files_unrepairable": list(report["files_unrepairable_sample"]),
    }


def _failed_result(error: str) -> dict[str, Any]:
    report = _base_report(error=error)
    report.pop("version")
    report["files_unrepairable"] = []
    return report


def _positive_deadline(value: float, label: str) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{label} must be a positive finite number")
    try:
        seconds = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{label} must be a positive finite number") from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError(f"{label} must be a positive finite number")
    return seconds


def run_scrub_supervised(
    directory: str, database: str, redundancy: int | str, verify: bool, config: Any, *,
    log_dir: str, log_file: str | None = None, suppress_notifications: bool = True,
    preflight_timeout: float = SCRUB_PREFLIGHT_TIMEOUT_SECONDS,
    operation_timeout: float = SCRUB_OPERATION_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Run physical validation, preparation, PAR2, and metrics under supervision."""
    preflight_bound = _positive_deadline(preflight_timeout, "Scrub preflight deadline")
    operation_bound = _positive_deadline(operation_timeout, "Scrub operation deadline")
    try:
        request = _worker_request(
            directory, database, redundancy, verify, config,
            log_dir=log_dir, log_file=log_file,
            suppress_notifications=suppress_notifications,
        )
        request_payload = json.dumps(request, ensure_ascii=False).encode("utf-8")
        if len(request_payload) > WORKER_REQUEST_LIMIT:
            raise ValueError("Scrub worker request exceeds size limit")
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        return _failed_result(str(exc))

    with tempfile.TemporaryFile(mode="w+b") as report, tempfile.TemporaryFile(
        mode="w+", encoding="utf-8",
    ) as request_stream:
        request_stream.write(request_payload.decode("utf-8"))
        request_stream.flush()
        request_stream.seek(0)
        phase_read, phase_write = os.pipe()
        process = None
        try:
            process = subprocess.Popen(
                [sys.executable, "-I", "-u", WORKER_SCRIPT, "--scrub-worker",
                 str(report.fileno()), str(phase_write)],
                stdin=request_stream,
                stdout=None,
                stderr=None,
                start_new_session=True,
                pass_fds=(report.fileno(), phase_write),
            )
            os.close(phase_write)
            phase_write = -1
            started, supervisor_error = _supervise_worker(
                process, phase_read,
                preflight_timeout=preflight_bound,
                operation_timeout=operation_bound,
            )
        except BaseException:
            if process is not None:
                _terminate_process_group(process)
            raise
        finally:
            if phase_write >= 0:
                os.close(phase_write)
            os.close(phase_read)

        if supervisor_error:
            return _failed_result(supervisor_error)
        try:
            result = _read_worker_report(report)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return _failed_result(f"Invalid scrub worker report: {exc}")
        if result["completed"] and not started:
            return _failed_result("Scrub worker reported success without completing physical preflight")
        if not result["completed"] and not result.get("error"):
            result["error"] = "Scrub worker reported an incomplete scan"
        return result


def main() -> int:
    if len(sys.argv) == 4 and sys.argv[1] == "--scrub-worker":
        return _scrub_worker(int(sys.argv[2]), int(sys.argv[3]))
    print("Usage: scrub_runner.py --scrub-worker REPORT_FD STARTED_FD", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
