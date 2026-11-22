"""Collect bulky completed controller payloads while preserving history proof."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
import stat
import sys

from lib.atomic_io import read_json_file, write_json_atomic
from lib.agent_storage_inventory import _mounts
from lib.build_protocol import encode_document
from lib.build_setup_history import _inspect
from lib.build_setup_recovery import _Evidence, _hex
from lib.concurrency import resource_lock
from lib.managed_storage import filesystem


MARKER = "payload-retirement.json"
MAX_ENTRIES = 100000
MAX_BYTES = 32 * 1024**2


def _inventory(parent: int, names: list[str]) -> dict:
    result = {}
    device = os.fstat(parent).st_dev
    parent_path = os.readlink(f"/proc/self/fd/{parent}")
    for mount in _mounts():
        if any(mount == parent_path + "/" + name or mount.startswith(parent_path + "/" + name + "/") for name in names):
            raise ValueError("Controller payload contains a mounted filesystem")

    def walk(directory, name, relative, depth):
        if depth > 64 or len(result) >= MAX_ENTRIES:
            raise ValueError("Controller payload inventory exceeds bounds")
        try:
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            return
        if (info.st_uid != os.geteuid() or info.st_dev != device
                or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
                or stat.S_ISREG(info.st_mode) and info.st_nlink != 1):
            raise ValueError("Controller payload has unsafe ownership, type, links or mount")
        identity = [info.st_dev, info.st_ino, info.st_mode, info.st_uid]
        if stat.S_ISREG(info.st_mode):
            identity += [info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns]
        result[relative] = dict(identity=identity, logical_bytes=info.st_size, allocated_bytes=info.st_blocks * 512)
        if stat.S_ISDIR(info.st_mode):
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            try:
                for entry in sorted(os.listdir(child)):
                    walk(child, entry, relative + "/" + entry, depth + 1)
            finally:
                os.close(child)

    for name in names:
        parts = name.split("/")
        if len(parts) == 1:
            walk(parent, name, name, 0)
        else:
            directory = os.open(parts[0], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                walk(directory, parts[1], name, 0)
            finally:
                os.close(directory)
    return result


def _delete(parent: int, relative: str, directory: bool) -> None:
    descriptor = os.dup(parent)
    try:
        parts = relative.split("/")
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        if directory:
            os.rmdir(parts[-1], dir_fd=descriptor)
        else:
            os.unlink(parts[-1], dir_fd=descriptor)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def collect_completed_payloads(journal: str, attempt_id: str, *, expected_revision: str | None = None) -> dict:
    """Inspect or apply an exact plan under resource -> setup -> delivery locks.

    Pending deletion can resume only with its original revision. No source or
    bundle is ever replayed, and compact metadata remains available to history.
    """
    if expected_revision is not None:
        _hex(expected_revision)
    with ExitStack() as stack:
        evidence = _Evidence(stack)
        report, state = _inspect(evidence, journal, attempt_id)
        host = state["host_name"]
        evidence.verify()
        if expected_revision is not None:
            stack.close()
            stack.enter_context(resource_lock("setup-target", host.lower().rstrip(".")))
            evidence = _Evidence(stack)
            report, state = _inspect(evidence, journal, attempt_id)
            evidence.verify()
            if state["host_name"] != host:
                raise ValueError("Controller setup identity changed")
        attempt = state["attempt"]
        names = ["sources"]
        for item in report["applications"]:
            identifier = item["request_id"]
            names.extend(["delivery/artifact-" + identifier + ".bundle", "delivery/verified-" + identifier])
        current = _inventory(attempt, names)
        marker_path = state["host_path"] + "/" + ("archived-" if report["archived"] else "") + attempt_id + "/" + MARKER
        marker = None
        if os.path.lexists(marker_path):
            info = os.stat(marker_path, follow_symlinks=False)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
                raise ValueError("Unsafe controller retirement marker")
            marker = read_json_file(marker_path, max_bytes=MAX_BYTES)
            if (not isinstance(marker, dict) or set(marker) != {"schema", "revision", "history_revision", "state", "entries"}
                    or marker["schema"] != 1 or marker["state"] not in {"pending", "completed"}
                    or marker["history_revision"] != report["revision"] or not isinstance(marker["entries"], dict)):
                raise ValueError("Controller retirement proof differs")
            original = marker["entries"]
            # Fixed payload roots and fresh inventory constrain all deletion
            # paths; never execute a path merely because it is in saved JSON.
            if any(path not in original or item["identity"] != original[path]["identity"]
                   for path, item in current.items()):
                raise ValueError("Controller payload changed during retirement")
            if marker["state"] == "completed" and current:
                raise ValueError("Retired controller payload reappeared")
        else:
            original = current
        revision = hashlib.sha256(encode_document({"history": report["revision"], "entries": original}, maximum=MAX_BYTES)).hexdigest()
        if marker is not None and marker["revision"] != revision:
            raise ValueError("Controller retirement revision is invalid")
        result = dict(attempt_id=attempt_id, revision=revision, archived=report["archived"],
                      status="completed" if marker and marker["state"] == "completed" else "pending" if marker else "eligible",
                      allocated_bytes=sum(item["allocated_bytes"] for item in current.values()),
                      paths=names, owner_uid=os.geteuid(), retained_metadata=True)
        attempt_path = os.path.dirname(marker_path)
        result["candidates"] = [dict(path=attempt_path + "/" + name, owner_uid=os.geteuid(),
            category="completed_controller_payload", rebuild_cost="New build/setup required; metadata remains historical",
            allocated_bytes=sum(item["allocated_bytes"] for path, item in current.items()
                                if path == name or path.startswith(name + "/"))) for name in names]
        result["before"] = filesystem(attempt_path)
        result["after"] = result["before"]
        if expected_revision is None:
            return result
        if expected_revision != revision:
            raise ValueError("Controller cleanup revision changed")
        evidence.verify()
        if state["legacy"]:
            write_json_atomic(os.path.dirname(marker_path) + "/profile-success.json", state["proof"], mode=0o600)
        if marker is None:
            marker = dict(schema=1, revision=revision, history_revision=report["revision"], state="pending", entries=original)
            write_json_atomic(marker_path, marker, mode=0o600)
        # Recheck all named payload identities after persisting intent.
        fresh = _inventory(attempt, names)
        if fresh != current:
            raise ValueError("Controller payload changed before deletion")
        for path in sorted(current, key=lambda value: (value.count("/"), value), reverse=True):
            _delete(attempt, path, stat.S_ISDIR(current[path]["identity"][2]))
        os.fsync(attempt)
        marker["state"] = "completed"
        write_json_atomic(marker_path, marker, mode=0o600)
        return result | {"status": "completed", "remaining_allocated_bytes": 0, "after": filesystem(attempt_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", required=True)
    parser.add_argument("--attempt", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-revision")
    args = parser.parse_args()
    if not sys.dont_write_bytecode:
        parser.error("Use python3 -B to prevent inspection bytecode writes")
    if args.apply != (args.expected_revision is not None):
        parser.error("Apply requires the preview revision")
    try:
        report = collect_completed_payloads(args.journal, args.attempt, expected_revision=args.expected_revision)
        print(json.dumps(report))
        return 0
    except (OSError, ValueError, RuntimeError):
        print(json.dumps(dict(status="refused", reason="busy_unfinished_changed_or_unsafe_evidence")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
