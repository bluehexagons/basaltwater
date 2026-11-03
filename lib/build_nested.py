"""Root-private scheduling evidence for SSH-authorized nested runner builds."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import os
import re
import stat

from lib.atomic_io import write_json_atomic
from lib.build_coordinator import drained
from lib.build_jobs import _owner
from lib.build_protocol import decode_document
from lib.build_runner_lease import MAX_MESSAGE, borrow, validate_proof, verify_proof
from lib.setup_directories import directory_fd
from lib.validation import validate_filesystem_path

_NAME = re.compile(r"[0-9a-f]{32}\.json")


class NestedRequests:
    def __init__(self, config: dict):
        self.path = config["roots"]["state"] + "-runner-leases"
        validate_filesystem_path(self.path)
        for root in config["roots"].values():
            if os.path.commonpath((root, self.path)) in {root, self.path}:
                raise ValueError("Nested scheduling evidence overlaps a configured storage root")
        self.maximum = config["max_records"]

    @contextmanager
    def _directory(self, *, create: bool = False):
        with directory_fd(self.path, create=create, mode=0o700) as directory:
            info = os.fstat(directory)
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise ValueError("Nested build evidence must be private and owned")
            yield directory

    def _names(self, directory: int) -> list[str]:
        names = []
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.name == "requests.lock":
                    continue
                if len(names) >= self.maximum or _NAME.fullmatch(entry.name) is None:
                    raise ValueError("Nested build evidence is full or uncertain")
                names.append(entry.name)
        return sorted(names)

    @staticmethod
    @contextmanager
    def _lock(directory: int):
        lock = os.open("requests.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                       0o600, dir_fd=directory)
        try:
            info = os.fstat(lock)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
                raise ValueError("Unsafe nested build evidence lock")
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield
        finally:
            os.close(lock)

    @staticmethod
    def _read(directory: int, name: str) -> dict:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            before = os.fstat(descriptor)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                    or before.st_nlink != 1 or stat.S_IMODE(before.st_mode) != 0o600
                    or before.st_size > MAX_MESSAGE):
                raise ValueError("Unsafe nested build evidence")
            raw = os.read(descriptor, MAX_MESSAGE + 1)
            after = os.fstat(descriptor)
            identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            if identity(before) != identity(after) or len(raw) != before.st_size:
                raise ValueError("Nested build evidence changed while reading")
        finally:
            os.close(descriptor)
        value = decode_document(raw, maximum=MAX_MESSAGE)
        if (not isinstance(value, dict) or set(value) != {"schema", "owner", "proof"}
                or type(value["schema"]) is not int or value["schema"] != 1):
            raise ValueError("Invalid nested build evidence")
        value["owner"] = _owner(value["owner"])
        value["proof"] = validate_proof(value["proof"])
        if name != value["proof"]["request_id"] + ".json":
            raise ValueError("Nested build evidence has a mismatched request ID")
        return value

    def register(self, request: dict, proof: object, *, owner: str) -> None:
        """Persist exact proof before admission, under unchanged SSH authority."""
        value = {"schema": 1, "owner": _owner(owner), "proof": verify_proof(proof, request)}
        name = request["request_id"] + ".json"
        with self._directory(create=True) as directory:
            with self._lock(directory):
                names = self._names(directory)
                if name in names:
                    if self._read(directory, name) != value:
                        raise ValueError("Nested build request already has different retained authority")
                    return
                if len(names) >= self.maximum:
                    raise ValueError("Nested build evidence is full")
                write_json_atomic(os.path.join(self.path, name), value, mode=0o600)

    @contextmanager
    def fifo_guard(self):
        """Prevent late admission publication from bypassing parent recovery."""
        with self._directory(create=True) as directory:
            with self._lock(directory):
                records = [self._read(directory, name) for name in self._names(directory)]
                yield frozenset((record["proof"]["request_id"], record["owner"],
                                 record["proof"]["request_sha256"]) for record in records)

    def run_pending(self, jobs, execute) -> bool:
        """Reconcile parent loss before FIFO work, or run one exact nested job."""
        try:
            with self._directory() as directory:
                with self._lock(directory):
                    names = self._names(directory)
                    # A vanished published record is uncertain evidence. Only
                    # an absent directory means nested intake is unused.
                    try:
                        records = [self._read(directory, name) for name in names]
                    except FileNotFoundError as error:
                        raise ValueError("Nested build evidence disappeared") from error
        except FileNotFoundError:
            return False
        for record in records:
            proof, owner = record["proof"], record["owner"]
            request_id = proof["request_id"]
            if not jobs.matches_request(request_id, owner=owner, request_sha256=proof["request_sha256"]):
                continue  # Failed/colliding admission grants no scheduling authority.
            try:
                status = jobs.status(request_id, owner=owner)
            except ValueError as error:
                if str(error) == "Build job was not found":
                    continue  # A retained intent may precede source admission.
                raise
            if status["state"] != "queued":
                continue
            if drained():
                jobs.cancel(request_id, owner=owner)
                continue
            try:
                with borrow(proof) as lease:
                    result = execute(request_id=request_id, owner=owner,
                                     request_sha256=proof["request_sha256"], cancelled=lease.cancelled)
                    if result is None:
                        # A store drain/expiry must not leave the parent waiting
                        # synchronously on queued work held by its own lease.
                        current = jobs.status(request_id, owner=owner)
                        if current["state"] == "queued":
                            jobs.cancel(request_id, owner=owner)
                return True
            except (OSError, ValueError):
                # No execution is retried. A lost parent or failed handoff
                # cancels queued intent. Any already-running claim/evidence
                # remains governed by the compositor's uncertain-outcome rule.
                current = jobs.status(request_id, owner=owner)
                if current["state"] == "queued":
                    jobs.cancel(request_id, owner=owner)
                elif current["state"] == "running":
                    raise RuntimeError("Nested build cleanup is uncertain") from None
        return False
