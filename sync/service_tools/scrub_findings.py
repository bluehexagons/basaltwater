"""Persistent integrity findings and conservative single-file recovery."""

from __future__ import annotations

import os
import ctypes
import hashlib
import shutil
import stat
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from lib.atomic_io import _fsync_directory, read_json_file, write_json_atomic

METADATA_DIR = ".basaltwater-scrub"
QUARANTINE_DIR = ".basaltwater-quarantine"
_UNREVIEWED = object()
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
        dispositions = data.get("dispositions", {})
        if not isinstance(dispositions, dict):
            raise ValueError("Invalid dispositions")
        for relative, entry in dispositions.items():
            if (not isinstance(relative, str) or not relative or relative == "."
                    or os.path.isabs(relative) or ".." in Path(relative).parts
                    or not isinstance(entry, dict) or entry.get("action") not in {"quarantine", "delete"}
                    or entry.get("state") not in {"intent", "completed"}):
                raise ValueError("Invalid disposition")

    def disposed(self, relative: str) -> bool:
        return relative in self.data.get("dispositions", {})

    def disposition_blocked(self, relative: str, identity: object) -> bool:
        previous = self.data.get("dispositions", {}).get(relative)
        return bool(previous and (previous["state"] == "intent" or previous.get("file_identity") == identity))

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


def _open_directory(path: str) -> int:
    """Pin directory components without following a replaced ancestor."""
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in Path(os.path.abspath(path)).parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def disposition_preflight(path: str, directory: str, database: str, job_id: str = "") -> None:
    from sync.service_tools.scrub_par2 import _confined_path

    _confined_path(path, directory)
    if (Path(path).is_relative_to(database)
            or {METADATA_DIR, QUARANTINE_DIR}.intersection(Path(os.path.relpath(path, directory)).parts)):
        raise ValueError("Reserved disposition path")
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("Disposition requires a regular file with one link")
    if os.stat(directory, follow_symlinks=False).st_dev != info.st_dev:
        raise ValueError("Cross-filesystem quarantine is unavailable")
    if len(os.fsencode(os.path.basename(path) + ".corrupt")) > os.pathconf(directory, "PC_NAME_MAX"):
        raise ValueError("Quarantine filename is too long")
    quarantine_path = os.path.join(directory, QUARANTINE_DIR)
    if os.path.lexists(quarantine_path):
        descriptor = _open_directory(quarantine_path)
        try:
            quarantine_info = os.fstat(descriptor)
            if (quarantine_info.st_uid != 0 or stat.S_IMODE(quarantine_info.st_mode) != 0o700
                    or quarantine_info.st_dev != info.st_dev):
                raise ValueError("Unsafe quarantine")
            marker = os.open(".owner", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
            try:
                marker_info = os.fstat(marker)
                if (not stat.S_ISREG(marker_info.st_mode) or marker_info.st_uid != 0
                        or marker_info.st_nlink != 1 or marker_info.st_mode & 0o077
                        or os.read(marker, 100) != b"basaltwater-quarantine-v1\n"):
                    raise ValueError("Unrecognized quarantine")
            finally:
                os.close(marker)
            quarantine_id = job_id + "-" + hashlib.sha256(os.path.relpath(path, directory).encode()).hexdigest()[:16]
            if job_id and os.path.lexists(os.path.join(quarantine_path, quarantine_id)):
                raise ValueError("Quarantine path collision")
        finally:
            os.close(descriptor)


def dispose(path: str, directory: str, database: str, action: str,
            identity: list[int], job_id: str) -> dict:
    """Persist intent before irreversible effects; ambiguous outcomes retain protection."""
    if action not in {"quarantine", "delete"}:
        raise ValueError("Unknown disposition")
    disposition_preflight(path, directory, database, job_id)
    relative = os.path.relpath(path, directory)
    report = Findings(directory, database)
    if report.disposition_blocked(relative, identity):
        raise ValueError("Disposition already recorded; inspect before further action")
    parent = _open_directory(os.path.dirname(path))
    quarantine = None
    try:
        destination = ""
        if action in {"quarantine", "delete"}:
            root = _open_directory(directory)
            try:
                try:
                    os.mkdir(QUARANTINE_DIR, 0o700, dir_fd=root)
                    created = True
                except FileExistsError:
                    created = False
                quarantine = os.open(QUARANTINE_DIR, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
                info = os.fstat(quarantine)
                if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700 or info.st_dev != identity[0]:
                    raise ValueError("Unsafe or cross-filesystem quarantine")
                marker = b"basaltwater-quarantine-v1\n"
                if created:
                    marker_fd = os.open(".owner", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=quarantine)
                    try:
                        os.write(marker_fd, marker)
                        os.fsync(marker_fd)
                    finally:
                        os.close(marker_fd)
                    os.fsync(quarantine)
                    os.fsync(root)
                else:
                    marker_fd = os.open(".owner", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=quarantine)
                    try:
                        marker_info = os.fstat(marker_fd)
                        if (not stat.S_ISREG(marker_info.st_mode) or marker_info.st_uid != 0
                                or marker_info.st_nlink != 1 or marker_info.st_mode & 0o077
                                or os.read(marker_fd, 100) != marker):
                            raise ValueError("Unrecognized quarantine")
                    finally:
                        os.close(marker_fd)
            finally:
                os.close(root)
            # One private job directory retains the complete source relative path.
            quarantine_id = job_id + "-" + hashlib.sha256(relative.encode()).hexdigest()[:16]
            os.mkdir(quarantine_id, 0o700, dir_fd=quarantine)
            child = os.open(quarantine_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=quarantine)
            os.fsync(quarantine)
            os.close(quarantine)
            quarantine = child
            for part in Path(relative).parts[:-1]:
                os.mkdir(part, 0o700, dir_fd=quarantine)
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=quarantine)
                os.fsync(quarantine)
                os.close(quarantine)
                quarantine = child
            destination = os.path.join(directory, QUARANTINE_DIR, quarantine_id, relative + ".corrupt")
        intent = {"action": action, "state": "intent", "job_id": job_id,
                  "file_identity": identity, "destination": destination, "started_at": stamp()}
        previous = report.data.get("dispositions", {}).get(relative)
        if previous:
            intent["history"] = [*previous.get("history", []),
                                 {key: value for key, value in previous.items() if key != "history"}]
        report.data.setdefault("dispositions", {})[relative] = intent
        report.save()
        info = os.stat(os.path.basename(path), dir_fd=parent, follow_symlinks=False)
        current = [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]
        if current != identity or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("File changed before disposition")
        if action in {"quarantine", "delete"}:
            libc = ctypes.CDLL(None, use_errno=True)
            rename = libc.renameat2
            rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            rename.restype = ctypes.c_int
            if rename(parent, os.fsencode(os.path.basename(path)), quarantine,
                      os.fsencode(os.path.basename(destination)), 1) != 0:
                error = ctypes.get_errno()
                raise OSError(error, os.strerror(error))
            os.fsync(quarantine)
            moved = os.stat(os.path.basename(destination), dir_fd=quarantine, follow_symlinks=False)
            # Pin the bytes privately before deletion. A name swapped in the
            # syscall window is retained for inspection, never unlinked.
            if (not stat.S_ISREG(moved.st_mode) or moved.st_nlink != 1
                    or [moved.st_dev, moved.st_ino, moved.st_size, moved.st_mtime_ns] != identity[:4]):
                raise RuntimeError("File changed during disposition; retained for inspection")
            if action == "delete":
                os.unlink(os.path.basename(destination), dir_fd=quarantine)
                os.fsync(quarantine)
        os.fsync(parent)
        intent.update(state="completed", finished_at=stamp())
        entry = report.data["files"].get(relative)
        if entry:
            entry.update(state="resolved", resolution=action, resolved_at=stamp(),
                         advice="Source disposed; parity and mirror protection retained.")
        report.save()
        return {"category": action, "destination": destination}
    finally:
        os.close(parent)
        if quarantine is not None:
            os.close(quarantine)


def examine(file_path: str, directory: str, database: str, *, repair: bool = False, staged_base: str | None = None,
            reviewed_identity: object = _UNREVIEWED) -> dict:
    """Run one PAR2 operation, retaining its classification and bounded evidence."""
    from sync.service_tools.scrub_par2 import _confined_path, _parity_files
    from sync.service_tools.parity_sets import locate

    relative = os.path.relpath(file_path, directory)
    if Path(relative).parts[0] == METADATA_DIR:
        raise ValueError(f"{METADATA_DIR} is reserved for scrub metadata")
    base, parity = locate(file_path, directory, database) if staged_base is None else (staged_base, _parity_files(staged_base))
    _confined_path(file_path, directory)
    _confined_path(base, database)
    for path in parity:
        _confined_path(path, database)
    if not parity:
        identity = file_identity(file_path)
        if reviewed_identity is not _UNREVIEWED and identity != reviewed_identity:
            raise ValueError("File changed before reviewed verification")
        return {"category": "missing_parity", "evidence": "No existing parity set", "returncode": None,
                "file_identity": identity}
    try:
        before = file_identity(file_path)
        if reviewed_identity is not _UNREVIEWED and before != reviewed_identity:
            raise ValueError("File changed before reviewed verification")
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
              action: str, *, backup: str | None = None, reviewed_identity: object = _UNREVIEWED) -> dict:
    """Retain originals, work in isolation, verify, then publish one candidate."""
    from sync.service_tools.scrub_par2 import _confined_path, create_par2
    from sync.service_tools.parity_sets import locate, publish

    if action not in {"repair", "restore", "accept"}:
        raise ValueError("Unknown scrub remediation")
    _confined_path(file_path, directory)
    if Path(file_path).is_relative_to(Path(database)):
        raise ValueError("Cannot remediate the parity database as source data")
    relative = os.path.relpath(file_path, directory)
    if Path(relative).parts[0] == METADATA_DIR:
        raise ValueError(f"{METADATA_DIR} is reserved for scrub metadata")
    base, parity = locate(file_path, directory, database)
    _confined_path(base, database)
    before = file_identity(file_path)
    if reviewed_identity is not _UNREVIEWED and before != reviewed_identity:
        raise ValueError("File changed after recovery review; verify and review again")
    if action != 'accept' and before is not None and os.stat(file_path).st_nlink > 1:
        raise ValueError('Repair or restore of hard-linked content requires separating the link explicitly first')
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
    source_size = before[2] if before else 0
    candidate_size = max(source_size, os.path.getsize(backup) if backup else 0)
    parity_size = sum(os.path.getsize(path) for path in parity)
    # Keep room for retained originals, work copies, the new generation, and
    # the atomic publication copy. This is a preflight, not a reservation.
    reserve = 1024 * 1024
    needed = 3 * candidate_size + 3 * (parity_size + candidate_size * redundancy // 100) + reserve
    if (shutil.disk_usage(report.root).free < needed
            or (action != 'accept' and shutil.disk_usage(directory).free < candidate_size + reserve)):
        raise OSError('Insufficient free space for retained recovery copies and atomic publication')
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
        publish(candidate, work, work_db, parity)
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
    if (file_identity(file_path) != before or parity_before != [file_identity(path) for path in parity]
            or locate(file_path, directory, database)[1] != parity):
        raise RuntimeError(f"Live data or parity changed during remediation; originals retained at {recovery}")
    _confined_path(file_path, directory)
    _confined_path(base, database)
    if action == "accept":
        new_parity = locate(candidate, work, work_db)[1]
        if not new_parity:
            raise RuntimeError("PAR2 did not produce a parity set")
        # The verified generation becomes active with one atomic record swap.
        manifest["state"] = "publishing"
        write_json_atomic(manifest_path, manifest)
        publish(file_path, directory, database, new_parity)
        expected_identity = before
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
            with open(temporary, "rb") as stream:
                os.fsync(stream.fileno())
            expected_identity = file_identity(temporary)
            if file_identity(file_path) != before:
                raise RuntimeError("Live file changed before publication")
            os.replace(temporary, file_path)
            _fsync_directory(os.path.dirname(file_path))
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    after = file_identity(file_path)
    # Renaming a restored file may update ctime; its inode, size and mtime
    # must still match the durable publication copy. Acceptance changes no
    # source metadata, so compare its complete original identity.
    unchanged = (after == expected_identity if action == "accept"
                 else after is not None and after[:4] == expected_identity[:4])
    if not unchanged:
        outcome.update({"category": "changed_during_scan",
                        "evidence": "Live file changed during recovery publication; verify again before resolving"})
    manifest.update({"state": "completed" if unchanged else "published_unverified",
                     "completed_at": stamp()})
    write_json_atomic(manifest_path, manifest)
    outcome["file_identity"] = after
    report.record(relative, outcome, resolution="accepted" if action == "accept" else action)
    return outcome
