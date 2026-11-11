#!/usr/bin/env python3
"""Supervised, approved single-file recovery independent of the web panel."""

from __future__ import annotations

import argparse
import errno
from collections.abc import Iterator
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, "/opt/basaltwater")

from lib.atomic_io import read_json_file, remove_file_durable, write_json_atomic
from lib.data_actions import DATA_STATE, DATA_UNIT, HEX_ID, finding_revision, validate_data_action
from lib.machine_state import PERSISTENT_STATE_DIR as STATE_DIR, can_manage_system_services, load_setup_config
from lib.operation_state import OperationStateStore
from lib.privilege_execution import ENVIRONMENT
from lib.privilege_policy import protected_path
from lib.runtime_config import RuntimeConfig
from sync.service_tools.scrub_findings import ADVICE, Findings, file_identity, examine, dispose, disposition_preflight
from sync.service_tools.scrub_manage import execute, selected_jobs
from sync.service_tools.storage_ops import LOCK_FILE, OperationLock, resolve_scrub_database_path

RESULTS = {"running", "succeeded", "findings", "failed", "interrupted"}
FAILURES = {"", "storage_busy", "setup_recovery_required", "finding_changed", "file_changed", "identity_unavailable", "overlapping_scrubs", "reserved_path", "execution_rejected", "job_interrupted", "read_only", "permission_denied", "no_space", "cross_device"}


class DataJobRejected(ValueError):
    """A safe, fixed reason for refusing a stale or conflicting operation."""


def _failure(exc: Exception) -> str:
    if isinstance(exc, DataJobRejected):
        return str(exc)
    if isinstance(exc, OSError):
        return {errno.EROFS: "read_only", errno.EACCES: "permission_denied",
                errno.EPERM: "permission_denied", errno.ENOSPC: "no_space",
                errno.EDQUOT: "no_space", errno.EXDEV: "cross_device"}.get(exc.errno, "execution_rejected")
    return "execution_rejected"


def _setup_blocked() -> bool:
    try:
        os.lstat(STATE_DIR + "/setup-operation.json")
    except FileNotFoundError:
        return False
    return True


@contextmanager
def _setup_owner() -> Iterator[None]:
    protected_path(STATE_DIR, directory=True)
    marker = STATE_DIR + "/setup-operation.json"
    for path in (marker, marker + ".lock"):
        if os.path.lexists(path):
            protected_path(path)
    with OperationStateStore(marker).ownership() as owner:
        if owner.load() is not None:
            raise DataJobRejected("setup_recovery_required")
        yield


def _payload_path(job_id: str) -> str:
    if not isinstance(job_id, str) or not HEX_ID.fullmatch(job_id):
        raise ValueError("Invalid data job identity")
    return os.path.join(os.path.dirname(DATA_STATE), "data-payload-" + job_id + ".json")


def stage_data_job(parameters: dict) -> None:
    """Stage only an already-claimed plan; user paths never enter systemd argv."""
    parameters = validate_data_action(parameters)
    path = _payload_path(parameters["job_id"])
    protected_path(os.path.dirname(path), directory=True)
    try:
        os.lstat(path)
    except FileNotFoundError:
        pass
    else:
        raise ValueError("Data job payload already exists; inspect before retrying")
    write_json_atomic(path, parameters, mode=0o600)


def consume_data_job(job_id: str) -> dict:
    path = _payload_path(job_id)
    protected_path(path)
    parameters = validate_data_action(read_json_file(path, max_bytes=16384))
    if parameters["job_id"] != job_id:
        raise ValueError("Data job payload identity mismatch")
    remove_file_durable(path)
    return parameters


def _unit_state() -> str:
    try:
        result = subprocess.run(["/usr/bin/systemctl", "show", DATA_UNIT, "--property=ActiveState", "--value"],
                                capture_output=True, text=True, timeout=2, check=False, env=ENVIRONMENT)
        if result.returncode == 0 and result.stdout.strip() in {"active", "activating", "deactivating", "inactive", "failed"}:
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "unavailable"


def _record() -> dict:
    protected_path(os.path.dirname(DATA_STATE), directory=True)
    try:
        os.lstat(DATA_STATE)
    except FileNotFoundError:
        return {}
    protected_path(DATA_STATE)
    value = read_json_file(DATA_STATE, max_bytes=65536)
    if not isinstance(value, dict) or set(value) not in ({"parameters", "status", "category", "failure", "started", "finished"}, {"parameters", "status", "category", "failure", "started", "finished", "items"}):
        raise ValueError("Invalid data job result")
    validate_data_action(value["parameters"])
    if "items" in value:
        if (not isinstance(value["items"], list) or len(value["items"]) != len(value["parameters"].get("items", []))):
            raise ValueError("Invalid batch results")
        for selected, item in zip(value["parameters"]["items"], value["items"]):
            if (not isinstance(item, dict) or set(item) != {"file", "finding", "status", "category", "failure", "started", "finished", "destination"}
                    or item["file"] != selected["file"] or item["finding"] != selected["finding"]
                    or item["status"] not in {"pending", "running", "completed", "skipped", "failed", "unresolved"}
                    or item["failure"] not in FAILURES
                    or item["category"] not in {"", "healthy", "quarantine", "delete", *ADVICE}
                    or not isinstance(item["destination"], str)):
                raise ValueError("Invalid batch item result")
    if (value["status"] not in RESULTS or value["category"] not in {"", "healthy", *ADVICE}
            or value["failure"] not in FAILURES
            or type(value["started"]) not in {int, float}
            or value["finished"] is not None and type(value["finished"]) not in {int, float}):
        raise ValueError("Invalid data job metadata")
    return value


def job_status() -> dict:
    """Read only fixed local metadata and unit state, never NAS files or evidence."""
    try:
        before = _record()
        active = _unit_state()
        after = _record()
        if before.get("parameters", {}).get("job_id") != after.get("parameters", {}).get("job_id"):
            raise ValueError("Data job changed during observation")
        if after.get("status") == "running" and active in {"inactive", "failed"}:
            after.update(status="interrupted", failure="job_interrupted")
            _interrupt_items(after)
        if "items" in after:
            # Keep socket status within MAX_MESSAGE even at the parameter byte
            # limit. Paths/revisions are already in parameters.items; detailed
            # quarantine destinations remain in the durable root-only record.
            after["items"] = [{"index": index, **{key: item[key] for key in
                                ("status", "category", "failure", "started", "finished")}}
                              for index, item in enumerate(after["items"])]
        return {"unit_state": active, "latest": after,
                "blocked": _setup_blocked(),
                "available": active != "unavailable" and can_manage_system_services()}
    except (OSError, ValueError, RuntimeError, TypeError):
        return {"unit_state": "unavailable", "latest": {}, "blocked": True, "available": False}


def recover(parameters: dict, saved: dict) -> tuple[dict, int]:
    """Match a configured job, reject stale evidence, then use the CLI's guards."""
    parameters = validate_data_action(parameters)
    config = RuntimeConfig.from_dict(saved)
    args = SimpleNamespace(action=parameters["action"], directory=parameters["directory"],
                           database=parameters["database"], file=parameters["file"],
                           backup=parameters["backup"] or None, yes=True)
    jobs = selected_jobs(config, args)
    directory, database, _redundancy = jobs[0]
    report = Findings(directory, database)
    relative = os.path.relpath(parameters["file"], directory)
    entry = report.data["files"].get(relative, {})
    if entry.get("state") != "open" or not secrets.compare_digest(finding_revision(entry), parameters["finding"]):
        raise DataJobRejected("finding_changed")
    if parameters["action"] != "verify":
        if "file_identity" not in entry:
            raise DataJobRejected("identity_unavailable")
        if entry["file_identity"] != file_identity(parameters["file"]):
            raise DataJobRejected("file_changed")
        args.reviewed_identity = entry["file_identity"]
    return execute(args, config)


def _interrupt_items(value: dict) -> None:
    for item in value.get("items", []):
        if item["status"] in {"running", "pending"}:
            item.update(status="unresolved" if item["status"] == "running" else "skipped",
                        failure="job_interrupted", finished=time.time())


def _save_record(value: dict) -> None:
    """Keep batch evidence after the latest-result slot moves to another job."""
    if "items" in value:
        claim = _payload_path(value["parameters"]["job_id"]) + ".claimed"
        protected_path(claim)
        # Save the permanent record first. A failed evidence write must stop
        # processing, even when the panel's latest slot could still be written.
        write_json_atomic(claim, value, mode=0o600)
    write_json_atomic(DATA_STATE, value, mode=0o600)


def recover_batch(parameters: dict, saved: dict, value: dict) -> None:
    """Preflight all reviewed identities, then persist each boundary without replay."""
    config = RuntimeConfig.from_dict(saved)
    directory, database = parameters["directory"], parameters["database"]

    def check(item: dict) -> dict:
        args = SimpleNamespace(action=parameters["action"], directory=directory, database=database,
                               file=item["file"], backup=None)
        selected_jobs(config, args)
        report = Findings(directory, database)
        relative = os.path.relpath(item["file"], directory)
        entry = report.data["files"].get(relative, {})
        if entry.get("state") != "open" or not secrets.compare_digest(finding_revision(entry), item["finding"]):
            raise DataJobRejected("finding_changed")
        if "file_identity" not in entry:
            raise DataJobRejected("identity_unavailable")
        if entry["file_identity"] != file_identity(item["file"]):
            raise DataJobRejected("file_changed")
        if parameters["action"] != "verify":
            # Disposition updates one report. Reject other protecting tasks,
            # including paused ones, rather than leave their evidence stale.
            path = Path(item["file"])
            for root, parity, _redundancy, _interval in config.scrub_specs:
                root = os.path.normpath(root)
                parity = resolve_scrub_database_path(root, parity)
                if path.is_relative_to(parity):
                    raise DataJobRejected("reserved_path")
                if path.is_relative_to(root) and (root, parity) != (directory, database):
                    raise DataJobRejected("overlapping_scrubs")
            if report.disposition_blocked(relative, entry["file_identity"]):
                raise DataJobRejected("execution_rejected")
            disposition_preflight(item["file"], directory, database, parameters["job_id"])
        return entry

    try:
        for item in value["items"]:
            check(item)
    except (OSError, ValueError, RuntimeError) as exc:
        reason = _failure(exc)
        for item in value["items"]:
            item.update(status="skipped", failure=reason, finished=time.time())
        raise DataJobRejected(reason) from exc
    for item in value["items"]:
        try:
            entry = check(item)
        except (OSError, ValueError, RuntimeError) as exc:
            item.update(status="skipped", failure=_failure(exc), finished=time.time())
            _save_record(value)
            continue
        item.update(status="running", started=time.time())
        _save_record(value)
        try:
            if parameters["action"] == "verify":
                result = examine(item["file"], directory, database, reviewed_identity=entry["file_identity"])
                Findings(directory, database).record(os.path.relpath(item["file"], directory), result)
            else:
                result = dispose(item["file"], directory, database, parameters["action"],
                                 entry["file_identity"], parameters["job_id"])
            item.update(status="completed", category=result["category"], destination=result.get("destination", ""))
        except (OSError, ValueError, RuntimeError) as exc:
            item.update(status="failed" if parameters["action"] == "verify" else "unresolved", failure=_failure(exc))
        finally:
            item["finished"] = time.time()
            _save_record(value)
    succeeded = all(item["status"] == "completed" and item["category"] in {"healthy", "delete", "quarantine"} for item in value["items"])
    value.update(status="succeeded" if succeeded else "findings")


def run_action(parameters: dict) -> int:
    parameters = validate_data_action(parameters)
    if os.geteuid() != 0 or not can_manage_system_services():
        raise PermissionError("Data jobs require a root service on a supported host")
    protected_path(os.path.dirname(DATA_STATE), directory=True)
    lock_path = DATA_STATE + ".lock"
    if os.path.lexists(lock_path):
        protected_path(lock_path)
    with open(lock_path, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        previous = _record()
        if previous.get("parameters", {}).get("job_id") == parameters["job_id"]:
            raise ValueError("This data job was already claimed")
        if os.path.lexists(DATA_STATE):
            protected_path(DATA_STATE)
        value = {"parameters": parameters, "status": "running", "started": time.time(),
                 "finished": None, "category": "", "failure": ""}
        if "items" in parameters:
            # An exclusive durable claim outlives the single latest-result slot.
            claim = _payload_path(parameters["job_id"]) + ".claimed"
            descriptor = os.open(claim, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            from lib.atomic_io import _fsync_directory
            _fsync_directory(os.path.dirname(claim))
            value["items"] = [{**item, "status": "pending", "category": "", "failure": "",
                               "started": None, "finished": None, "destination": ""} for item in parameters["items"]]
        _save_record(value)
        try:
            # Match routine edits: setup ownership precedes the storage lock,
            # and spans fresh configuration loading through the entire recovery.
            with _setup_owner():
                with OperationLock(LOCK_FILE) as storage_lock:
                    if not storage_lock.acquire():
                        raise DataJobRejected("storage_busy")
                    saved = load_setup_config()
                    if saved is None:
                        raise ValueError("Saved setup unavailable")
                    if "items" in parameters:
                        recover_batch(parameters, saved, value)
                    else:
                        result, code = recover(parameters, saved)
                        category = result.get("category")
                        if category not in {"healthy", *ADVICE}:
                            raise ValueError("Unexpected recovery result")
                        value.update(status="succeeded" if code == 0 and category == "healthy" else "findings", category=category)
        except DataJobRejected as exc:
            value.update(status="failed", failure=str(exc))
        except (OSError, ValueError, RuntimeError) as exc:
            value.update(status="failed", failure=_failure(exc))
        except BaseException:
            value.update(status="interrupted", failure="job_interrupted")
            raise
        finally:
            if value["status"] == "interrupted":
                _interrupt_items(value)
            elif value["status"] == "failed":
                for item in value.get("items", []):
                    if item["status"] == "pending":
                        item.update(status="skipped", failure=value["failure"], finished=time.time())
                    elif item["status"] == "running":
                        item.update(status="unresolved", failure=value["failure"], finished=time.time())
            value["finished"] = time.time()
            _save_record(value)
        return 0 if value["status"] == "succeeded" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id")
    args = parser.parse_args()
    os.umask(0o077)
    def interrupted(_signum, _frame):
        raise SystemExit(1)
    signal.signal(signal.SIGTERM, interrupted)
    return run_action(consume_data_job(args.job_id))


if __name__ == "__main__":
    raise SystemExit(main())
