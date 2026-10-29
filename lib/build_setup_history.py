"""Explicit, local archival of completed controller setup evidence.

Archives preserve bytes and identities in ``archived-ATTEMPT`` directories.
They are audit evidence, not executable inputs at their former absolute paths.
No remote state is queried and no payload is read, removed or replayed.
"""

from __future__ import annotations

from contextlib import ExitStack
import hashlib
import os
import re
import sys
from typing import Any

from lib.atomic_io import rename_path_durable, write_json_atomic
from lib.build_protocol import encode_document
from lib.concurrency import ResourceBusyError, resource_lock
from lib.build_setup_recovery import (
    BuildSetupInspectionError, MAX_PREPARED_BYTES, MAX_REPORT_BYTES, MAX_STATE_BYTES,
    _APP_PHASES, _Evidence, _applications, _fields, _hex, _identity, _operation,
    _path, _plans, _require, _timestamp,
)
from lib.operation_state import OperationRecord
from lib.validators import validate_host


ARCHIVE_PREFIX = "archived-"
COMPLETION_FILE = "profile-success.json"


class BuildSetupHistoryError(ValueError):
    """A fixed refusal without retained paths, commands or environment values."""

    def __init__(self, code: str = "invalid_evidence") -> None:
        self.code = code
        super().__init__("Build setup history maintenance refused: " + code)


def count_retained_attempts(host_root: str) -> int:
    """Count active slots; explicitly archived directories retain separate data."""
    with os.scandir(host_root) as entries:
        return sum(entry.is_dir(follow_symlinks=False)
                   and re.fullmatch(ARCHIVE_PREFIX + r"[0-9a-f]{32}", entry.name) is None
                   for entry in entries)


def record_setup_success(attempt: str, operation: OperationRecord) -> None:
    """Persist the acknowledged final profile before the setup guard clears."""
    write_json_atomic(os.path.join(attempt, COMPLETION_FILE),
                      {"schema": 1, "operation": operation.to_dict()}, mode=0o600)


def _absent(parent: int, name: str) -> None:
    try:
        os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise BuildSetupHistoryError("unfinished_operation")


def _success(value: object) -> OperationRecord:
    proof = _fields(value, {"schema", "operation"})
    _require(type(proof["schema"]) is int and proof["schema"] == 1)
    operation = _operation(proof["operation"], "build_setup", None, {"bootstrapping", "delivering", "profile_applying"})
    _require(operation.phases == ("bootstrapping", "delivering", "profile_applying")
             and operation.status == "in_progress")
    return operation


def _legacy_success(value: object) -> OperationRecord:
    result = _fields(value, {"version", "outcome", "completed_at", "operation"})
    _require(type(result["version"]) is int and result["version"] == 1 and result["outcome"] == "succeeded")
    _timestamp(result["completed_at"])
    return _success({"schema": 1, "operation": result["operation"]})


def _inspect(evidence: _Evidence, journal_path: object, attempt_id: object) -> tuple[dict, dict]:
    journal = _path(os.fspath(journal_path))
    _require(os.path.basename(journal) == "setup.json")
    identifier = _hex(attempt_id, 32)
    host_path = os.path.dirname(journal)
    host = evidence.root(host_path)
    evidence.lock(host, "setup.json.lock")
    _absent(host, "setup.json")
    evidence.absent.append((host, "setup.json"))
    archived_name = ARCHIVE_PREFIX + identifier
    attempt = evidence.open(host, identifier, directory=True, optional=True)
    archived = evidence.open(host, archived_name, directory=True, optional=True)
    _require((attempt is None) != (archived is None))
    moved = attempt is None
    selected = archived if moved else attempt
    original_path = host_path + "/" + identifier
    proof = evidence.json(selected, COMPLETION_FILE, optional=True)
    legacy = proof is None
    operation = (_legacy_success(evidence.json(host, "setup.json.last.json"))
                 if legacy else _success(proof))
    context = _fields(operation.context, {
        "schema", "attempt", "host", "builder", "profile_sha256", "prepared_sha256",
        "delivery_receipt", "expected_releases",
    })
    _require(type(context["schema"]) is int and context["schema"] == 1
             and isinstance(context["host"], str) and len(context["host"]) <= 253 and validate_host(context["host"])
             and isinstance(context["builder"], str) and len(context["builder"]) <= 253 and validate_host(context["builder"])
             and operation.resource == context["host"] and context["attempt"] == original_path
             and os.path.basename(host_path) == hashlib.sha256(context["host"].lower().rstrip(".").encode()).hexdigest())
    _hex(context["profile_sha256"])
    _hex(context["prepared_sha256"])
    prepared = evidence.json(selected, "prepared.json", maximum=MAX_PREPARED_BYTES)
    encoded = encode_document(prepared, maximum=MAX_PREPARED_BYTES)
    _require(hashlib.sha256(encoded).hexdigest() == context["prepared_sha256"])
    plans = _plans(prepared)
    delivery = evidence.open(selected, "delivery", directory=True)
    evidence.lock(delivery, "delivery.json.lock")
    _absent(delivery, "delivery.json")
    evidence.absent.append((delivery, "delivery.json"))
    result = evidence.json(delivery, "delivery.json.last.json")
    _fields(result, {"version", "outcome", "completed_at", "operation"})
    _require(type(result["version"]) is int and result["version"] == 1 and result["outcome"] == "succeeded")
    _timestamp(result["completed_at"])
    completed = _operation(result["operation"], "build_delivery", "controller-workspace", set(_APP_PHASES))
    entries = _applications(completed.context, plans)
    _require(completed.phase == "completed" and all(entry["status"] == "completed" for entry in entries))
    name = "receipt-" + plans[0]["idempotency_key"] + ".json"
    receipt = evidence.json(delivery, name, maximum=MAX_REPORT_BYTES)
    _require(_applications(receipt, plans) == entries
             and context["delivery_receipt"] == original_path + "/delivery/" + name)
    expected = [{key: entry[key] for key in ("application", "operation_id", "generation", "artifact_sha256")}
                for entry in entries]
    _require(context["expected_releases"] == expected
             and all(type(item["generation"]) is int for item in context["expected_releases"]))
    digest = hashlib.sha256()
    for document, maximum in ((operation.to_dict(), MAX_STATE_BYTES), (prepared, MAX_PREPARED_BYTES),
                              (result, MAX_STATE_BYTES), (receipt, MAX_REPORT_BYTES)):
        digest.update(encode_document(document, maximum=maximum))
        digest.update(b"\n")
    report = {"schema_version": 1, "attempt_id": identifier, "revision": digest.hexdigest(),
              "archived": moved, "setup_operation_id": operation.operation_id,
              "remote_state": "unobserved", "payload_contents": "unverified",
              "applications": [{key: entry[key] for key in (
                  "application", "request_id", "idempotency_key", "operation_id", "generation", "artifact_sha256",
              )} for entry in entries]}
    return report, {"host": host, "host_name": context["host"], "host_path": host_path, "attempt": selected, "legacy": legacy,
                    "proof": {"schema": 1, "operation": operation.to_dict()}}


def maintain_completed_setup(journal_path: str | os.PathLike[str], attempt_id: str, *,
                             expected_revision: str | None = None) -> dict[str, Any]:
    """Inspect, or explicitly archive exactly one completed attempt under locks.

    The revision binds all completion/identity metadata. No payload is opened;
    its integrity is not established. A matching archive retry only syncs the
    existing archive, never replays a deployment. No file or directory is deleted.
    """
    try:
        if expected_revision is not None:
            _hex(expected_revision)
        with ExitStack() as stack:
            evidence = _Evidence(stack)
            report, state = _inspect(evidence, journal_path, attempt_id)
            evidence.verify()
            if expected_revision is None:
                return report
            if report["revision"] != expected_revision:
                raise BuildSetupHistoryError("revision_changed")
            stack.enter_context(resource_lock("setup-target", state["host_name"]))
            host, attempt = state["host"], state["attempt"]
            archived_name = ARCHIVE_PREFIX + attempt_id
            if state["legacy"]:
                # Preserve the only remaining old success proof before the
                # rotating host result can be replaced by a later setup.
                path = state["host_path"] + "/" + (archived_name if report["archived"] else attempt_id)
                write_json_atomic(path + "/" + COMPLETION_FILE, state["proof"], mode=0o600)
                evidence.absent.remove((attempt, COMPLETION_FILE))
                evidence.json(attempt, COMPLETION_FILE)
            evidence.verify()
            if not report["archived"]:
                identity = _identity(os.fstat(attempt))
                rename_path_durable(state["host_path"] + "/" + attempt_id,
                                    state["host_path"] + "/" + archived_name)
                # Children and locks still refer to the same directory inode.
                # Replace just its deliberately moved named edge for rechecks.
                evidence.opened = [entry for entry in evidence.opened if entry[0] != attempt]
                evidence.absent.remove((host, archived_name))
                evidence.absent.append((host, attempt_id))
                relocated = evidence.open(host, archived_name, directory=True)
                _require(_identity(os.fstat(relocated)) == identity)
            # Also finish publication sync after a lost reply or failed fsync.
            os.fsync(attempt)
            os.fsync(host)
            evidence.verify()
            return report | {"archived": True}
    except BuildSetupHistoryError:
        raise
    except ResourceBusyError:
        raise BuildSetupHistoryError("busy") from None
    except BuildSetupInspectionError as error:
        raise BuildSetupHistoryError(error.code) from None
    except OSError:
        raise BuildSetupHistoryError("unavailable") from None
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise BuildSetupHistoryError() from None


def main() -> int:
    """Print one fixed-size report; inspection requires Python's -B startup flag."""
    try:
        arguments = sys.argv[1:]
        if arguments == ["--help"]:
            print("python3 -B -m lib.build_setup_history --journal /absolute/setup.json --attempt ID "
                  "[--archive --expected-revision SHA256]")
            return 0
        if (len(arguments) not in {4, 7} or arguments[0] != "--journal" or arguments[2] != "--attempt"
                or len(arguments) == 7 and arguments[4:6] != ["--archive", "--expected-revision"]):
            raise BuildSetupHistoryError("invalid_arguments")
        if not sys.dont_write_bytecode:
            raise BuildSetupHistoryError("bytecode_enabled_use_python_B")
        report = maintain_completed_setup(arguments[1], arguments[3],
                                          expected_revision=arguments[6] if len(arguments) == 7 else None)
    except BuildSetupHistoryError as error:
        print(encode_document({"schema_version": 1, "error": error.code}).decode(), file=sys.stderr)
        return 2
    print(encode_document(report, maximum=MAX_REPORT_BYTES - 1).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
