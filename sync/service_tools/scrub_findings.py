"""Persistent integrity findings and conservative single-file recovery."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from lib.atomic_io import _fsync_directory, read_json_file, write_json_atomic

METADATA_DIR = ".basaltwater-scrub"
ADVICE = {
    "unrepairable": "Restore from an independent copy; existing parity has insufficient recovery blocks.",
    "repairable": "Run repair to reconstruct a staged copy using existing parity.",
    "missing_parity": "Confirm the file is healthy, then explicitly accept it to create parity.",
    "invalid_parity": "Recover parity from backup, or confirm healthy content before accepting a new baseline.",
    "io_error": "Check mounts, permissions, and disk health, then verify again.",
    "changed_during_scan": "Stop writers and verify again; the scan is inconclusive.",
    "changed_unverified": "Verify against existing parity; timestamps do not prove an intentional edit.",
    "tool_error": "Inspect the PAR2 evidence and resolve the execution error before retrying.",
    "repair_failed": "Repair did not recover valid content; restore an independent copy.",
    "missing_file": "Restore or repair the missing file; recovery evidence has been retained.",
}


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def file_identity(path: str) -> list[int] | None:
    """Detect replacements and ordinary concurrent writes without hashing twice."""
    try:
        info = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"Expected a regular file: {path}")
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


class Findings:
    """Atomic per-database report; callers serialize mutations with storage-ops lock."""

    def __init__(self, directory: str, database: str):
        from sync.service_tools.scrub_par2 import _confined_path

        self.directory = os.path.abspath(directory)
        self.database = os.path.abspath(database)
        self.root = os.path.join(self.database, METADATA_DIR)
        self.path = os.path.join(self.root, "findings.json")
        _confined_path(self.path, self.database)
        self.data = {"version": 1, "directory": self.directory, "files": {}}
        try:
            data = read_json_file(self.path, max_bytes=64 * 1024 * 1024)
        except FileNotFoundError:
            return
        if (not isinstance(data, dict) or data.get("version") != 1
                or data.get("directory") != self.directory or not isinstance(data.get("files"), dict)):
            raise ValueError(f"Invalid or mismatched scrub findings: {self.path}")
        for relative, entry in data["files"].items():
            if (not isinstance(relative, str) or os.path.isabs(relative)
                    or relative in ("", ".") or ".." in Path(relative).parts
                    or not isinstance(entry, dict) or entry.get("state") not in ("open", "resolved")
                    or not isinstance(entry.get("category"), str)
                    or not isinstance(entry.get("history", []), list)):
                raise ValueError(f"Invalid scrub finding in {self.path}")
        self.data = data

    def active(self, relative: str) -> bool:
        return self.data["files"].get(relative, {}).get("state") == "open"

    def save(self) -> None:
        from sync.service_tools.scrub_par2 import _confined_path

        _confined_path(self.path, self.database)
        os.makedirs(self.root, mode=0o700, exist_ok=True)
        write_json_atomic(self.path, self.data)

    def finish_scan(self, *, verify: bool, completed: bool) -> None:
        self.data["last_full_scan" if verify else "last_parity_update"] = {
            "finished_at": stamp(), "completed": completed,
            "open_findings": sum(self.active(path) for path in self.data["files"]),
        }
        self.save()

    def record(self, relative: str, result: dict, *, resolution: str = "verified") -> None:
        from sync.service_tools.scrub_par2 import _confined_path

        _confined_path(os.path.join(self.directory, relative), self.directory)
        previous = self.data["files"].get(relative, {})
        healthy = result["category"] == "healthy"
        if healthy and not previous:
            return
        now = stamp()
        entry = dict(previous)
        if previous:
            entry["history"] = [*previous.get("history", []),
                                {key: previous[key] for key in ("category", "last_checked", "recovery", "resolution") if key in previous}][-20:]
        entry.update(result)
        entry.update({"state": "resolved" if healthy else "open", "last_checked": now})
        if healthy:
            entry.update({"resolved_at": now, "resolution": resolution})
            entry["advice"] = f"Resolved by {resolution}."
        else:
            entry.update({"first_seen": previous.get("first_seen", now), "last_seen": now,
                          "advice": ADVICE.get(result["category"], "Inspect evidence before changing data.")})
            entry.pop("resolved_at", None)
            entry.pop("resolution", None)
        self.data["files"][relative] = entry
        self.save()


def examine(file_path: str, directory: str, database: str, *, repair: bool = False) -> dict:
    """Run one PAR2 operation, retaining its classification and bounded evidence."""
    from sync.service_tools.scrub_par2 import _confined_path, _parity_files

    relative = os.path.relpath(file_path, directory)
    if Path(relative).parts[0] == METADATA_DIR:
        raise ValueError(f"{METADATA_DIR} is reserved for scrub metadata")
    base = os.path.join(database, relative + ".par2")
    _confined_path(file_path, directory)
    _confined_path(base, database)
    parity = _parity_files(base)
    for path in parity:
        _confined_path(path, database)
    if not parity:
        return {"category": "missing_parity", "evidence": "No existing parity set", "returncode": None}
    try:
        before = file_identity(file_path)
        parity_before = [file_identity(path) for path in parity]
        # A temporary output file bounds memory even for verbose PAR2 output.
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(
                ["par2", "repair" if repair else "verify", "-B", directory,
                 base if os.path.exists(base) else parity[0]],
                stdout=output, stderr=subprocess.STDOUT, cwd=directory,
                check=False, timeout=14400,
            )
            output.seek(0, os.SEEK_END)
            output.seek(max(0, output.tell() - 8192))
            evidence = output.read().decode("utf-8", errors="replace")
        after = file_identity(file_path)
        category = {0: "healthy", 1: "repairable", 2: "unrepairable",
                    4: "invalid_parity", 5: "repair_failed", 6: "io_error"}.get(result.returncode, "tool_error")
        if ((not repair and before != after)
                or parity_before != [file_identity(path) for path in parity]):
            category = "changed_during_scan"
        elif not repair and before is None and category in {"healthy", "repairable", "unrepairable"}:
            category = "missing_file"
        newer = bool(before and all(parity_before)
                     and before[3] > max(info[3] for info in parity_before) + 1_000_000_000)
        return {"category": category, "returncode": result.returncode,
                "evidence": evidence, "file_identity": after,
                "content_change_uncertain": newer and category != "healthy"}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"category": "io_error" if isinstance(exc, OSError) else "tool_error",
                "returncode": None, "evidence": str(exc)[:8192]}


def _copy_durable(source: str, destination: str) -> None:
    before = file_identity(source)
    if before is None:
        raise FileNotFoundError(source)
    os.makedirs(os.path.dirname(destination), mode=0o700, exist_ok=True)
    shutil.copy2(source, destination)
    with open(destination, "rb") as stream:
        os.fsync(stream.fileno())
    if file_identity(source) != before:
        raise RuntimeError(f"File changed while copying: {source}")
    _fsync_directory(os.path.dirname(destination))


def remediate(file_path: str, directory: str, database: str, redundancy: int,
              action: str, *, backup: str | None = None) -> dict:
    """Retain originals, work in isolation, verify, then publish one candidate."""
    from sync.service_tools.scrub_par2 import _confined_path, _parity_files, create_par2

    if action not in {"repair", "restore", "accept"}:
        raise ValueError("Unknown scrub remediation")
    _confined_path(file_path, directory)
    if Path(file_path).is_relative_to(Path(database)):
        raise ValueError("Cannot remediate the parity database as source data")
    relative = os.path.relpath(file_path, directory)
    if Path(relative).parts[0] == METADATA_DIR:
        raise ValueError(f"{METADATA_DIR} is reserved for scrub metadata")
    base = os.path.join(database, relative + ".par2")
    _confined_path(base, database)
    before = file_identity(file_path)
    parity = _parity_files(base)
    for path in parity:
        _confined_path(path, database)
    parity_before = [file_identity(path) for path in parity]
    if action != "accept" and not parity:
        raise ValueError("Existing parity is required to verify repair or restoration; use accept only for confirmed healthy content")
    if action == "restore":
        if not backup or not os.path.isabs(backup):
            raise ValueError("Restore requires an absolute backup path on the target host")
        _confined_path(backup, os.path.dirname(backup))
        file_identity(backup)
    report = Findings(directory, database)
    os.makedirs(report.root, mode=0o700, exist_ok=True)
    recovery = tempfile.mkdtemp(prefix="recovery-", dir=report.root)
    source_tree = os.path.join(recovery, "original")
    original_db = os.path.join(recovery, "parity")
    work = os.path.join(recovery, "work")
    work_db = os.path.join(recovery, "work-parity")
    candidate = os.path.join(work, relative)
    os.makedirs(os.path.dirname(candidate), mode=0o700, exist_ok=True)
    os.makedirs(work_db, mode=0o700, exist_ok=True)
    if before is not None:
        _copy_durable(file_path, os.path.join(source_tree, relative))
        _copy_durable(file_path, candidate)
    for path in parity:
        _copy_durable(path, os.path.join(original_db, os.path.relpath(path, database)))
        if action != "accept":
            _copy_durable(path, os.path.join(work_db, os.path.relpath(path, database)))
    for root, _, _ in os.walk(recovery, topdown=False):
        _fsync_directory(root)
    manifest = {"action": action, "file": file_path, "database": database,
                "backup": backup, "started_at": stamp(), "state": "prepared"}
    manifest_path = os.path.join(recovery, "operation.json")
    write_json_atomic(manifest_path, manifest)
    report.record(relative, {"category": "changed_unverified", "evidence": f"{action} staged; originals retained",
                             "recovery": recovery})
    if action == "restore":
        _copy_durable(backup, candidate)
    elif action == "repair":
        outcome = examine(candidate, work, work_db, repair=True)
        if outcome["category"] != "healthy":
            outcome["recovery"] = recovery
            report.record(relative, outcome)
            return outcome
    else:
        if not file_identity(candidate) or os.path.getsize(candidate) == 0:
            raise ValueError("Cannot accept absent or empty content as a PAR2 baseline")
        if not create_par2(candidate, work, work_db, redundancy, os.path.join(recovery, "create.log")):
            raise RuntimeError(f"Parity creation failed; originals retained at {recovery}")
    outcome = examine(candidate, work, work_db)
    outcome["recovery"] = recovery
    if outcome["category"] != "healthy":
        report.record(relative, outcome)
        return outcome
    if (file_identity(file_path) != before or parity_before != [file_identity(path) for path in parity]):
        raise RuntimeError(f"Live data or parity changed during remediation; originals retained at {recovery}")
    _confined_path(file_path, directory)
    _confined_path(base, database)
    if action == "accept":
        new_parity = _parity_files(os.path.join(work_db, relative + ".par2"))
        if not new_parity:
            raise RuntimeError("PAR2 did not produce a parity set")
        # Persist a recoverable intent before replacing the multi-file parity set.
        manifest["state"] = "publishing"
        write_json_atomic(manifest_path, manifest)
        for path in parity:
            os.unlink(path)
        os.makedirs(os.path.dirname(base), exist_ok=True)
        for path in new_parity:
            with open(path, "rb") as stream:
                os.fsync(stream.fileno())
            os.replace(path, os.path.join(database, os.path.relpath(path, work_db)))
        _fsync_directory(os.path.dirname(base))
    else:
        # Stage beside the target so publication is atomic across filesystems.
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".scrub-restore-", dir=os.path.dirname(file_path))
        os.close(descriptor)
        try:
            _copy_durable(candidate, temporary)
            if before is not None:
                info = os.stat(file_path)
                os.chown(temporary, info.st_uid, info.st_gid)
                os.chmod(temporary, stat.S_IMODE(info.st_mode))
            if file_identity(file_path) != before:
                raise RuntimeError("Live file changed before publication")
            os.replace(temporary, file_path)
            _fsync_directory(os.path.dirname(file_path))
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    manifest.update({"state": "completed", "completed_at": stamp()})
    write_json_atomic(manifest_path, manifest)
    outcome["file_identity"] = file_identity(file_path)
    report.record(relative, outcome, resolution="accepted" if action == "accept" else action)
    return outcome
