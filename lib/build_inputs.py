"""Authenticated caller-side admission for pinned build source snapshots.

The transport authenticates and authorizes before calling this module. This
store durably reserves retention capacity, publishes one verified source file
for the internal build worker, then submits the request to ``BuildJobStore``.
It does not own authentication, project policy, or stream deadlines.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import ctypes
import errno
import fcntl
import hashlib
import os
import re
import stat
from typing import Any, BinaryIO

from lib.build_jobs import BuildJobStore
from lib.build_protocol import (
    MAX_ARTIFACT_BYTES,
    MAX_DESCRIPTOR_BYTES,
    decode_document,
    encode_document,
    parse_build_request,
    request_digest,
)
from lib.build_source import DEFAULT_SOURCE_LIMITS
from lib.validation import validate_filesystem_path, validate_no_control_characters


DEFAULT_MAX_RECORDS = 128
DEFAULT_MAX_BYTES = 64 * 1024**3
MAX_RECORDS = 4096
MAX_RETAINED_METADATA_BYTES = MAX_RECORDS * 1024
MAX_INTENT_BYTES = 1024
MAX_PRUNING_INTENT_BYTES = 4096
READ_CHUNK_BYTES = 64 * 1024
RENAME_NOREPLACE = 1
_ID = re.compile(r"[0-9a-f]{32}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_OWNER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}")
_LOCK_NAME = ".build-inputs.lock"
_INITIALIZED_NAME = ".build-inputs.initialized"
_WITNESS = b"basaltwater-build-inputs-v1\n"
_INTENT_FIELDS = frozenset({
    "schema", "request_id", "owner", "request_sha256", "source_size",
    "source_sha256", "reservation_bytes",
})
_SETTLED_FIELDS = _INTENT_FIELDS | {"charge_bytes", "settlement_revision"}
_PRUNING_FIELDS = _SETTLED_FIELDS | {"pruning"}
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
_READ = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)


def _owner(value: object) -> str:
    if not isinstance(value, str) or _OWNER.fullmatch(value) is None:
        raise ValueError("Owner must be a safe label of 1–128 characters")
    validate_no_control_characters(value, "Owner")
    return value


def _identifier(value: object, label: str, pattern: re.Pattern[str] = _ID) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"{label} must be lowercase hexadecimal")
    return value


def _positive_integer(value: object, label: str, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{label} is outside supported limits")
    return value


def _prune_revision(revision: str, kinds: list[str]) -> str:
    return hashlib.sha256(encode_document({"previous_revision": revision, "pruned": kinds})).hexdigest()


def _validate_pruning(value: object, record: dict[str, Any]) -> dict[str, Any]:
    """Validate a bounded two-operation tombstone, without reading payloads."""
    if not isinstance(value, dict) or set(value) != {"payloads", "operations", "job_sha256"}:
        raise ValueError("Build input pruning evidence is invalid")
    _identifier(value["job_sha256"], "Pruning job identity", _SHA256)
    payloads = value["payloads"]
    if not isinstance(payloads, dict) or set(payloads) != {"source", "bundle", "log"}:
        raise ValueError("Build input pruning payload evidence is invalid")
    for kind, item in payloads.items():
        if item is None:
            if kind == "source":
                raise ValueError("Build input pruning requires original source evidence")
            continue
        fields = {"identity", "size", "sha256"}
        if kind == "bundle":
            fields |= {"descriptor_sha256", "descriptor_bytes"}
        if not isinstance(item, dict) or set(item) != fields:
            raise ValueError("Build input pruning payload identity is invalid")
        identity = item["identity"]
        if (not isinstance(identity, list) or len(identity) != 8
                or any(type(number) is not int or not 0 <= number <= 2**64 - 1 for number in identity)
                or identity[2] != os.geteuid() or identity[3] != 1
                or identity[4] != stat.S_IFREG | (0o400 if kind == "source" else 0o600)
                or type(item["size"]) is not int or item["size"] != identity[5]):
            raise ValueError("Build input pruning file identity is invalid")
        maximum = {"source": DEFAULT_SOURCE_LIMITS.max_snapshot_bytes,
                   "bundle": MAX_ARTIFACT_BYTES + MAX_DESCRIPTOR_BYTES + 1024,
                   "log": 16 * 1024**2}[kind]
        if not 0 <= item["size"] <= maximum:
            raise ValueError("Build input pruning payload exceeds its byte bound")
        _identifier(item["sha256"], "Pruning payload digest", _SHA256)
        if kind == "bundle":
            _identifier(item["descriptor_sha256"], "Pruning descriptor digest", _SHA256)
            _positive_integer(item["descriptor_bytes"], "Pruning descriptor size", MAX_DESCRIPTOR_BYTES)
        if kind == "source" and (item["size"] != record["source_size"]
                                 or item["sha256"] != record["source_sha256"]):
            raise ValueError("Build input pruning source identity changed")
    operations = value["operations"]
    if not isinstance(operations, list) or not 1 <= len(operations) <= 2:
        raise ValueError("Build input pruning operation bound exceeded")
    seen: set[str] = set()
    revision = record["settlement_revision"]
    previous = None
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict) or set(operation) != {
            "expected_revision", "kinds", "state", "previous_charge_bytes", "charge_bytes",
        }:
            raise ValueError("Build input pruning operation is invalid")
        kinds = operation["kinds"]
        if (not isinstance(kinds, list) or not kinds
                or any(not isinstance(kind, str) or kind not in {"source", "log"} for kind in kinds)
                or kinds != [kind for kind in ("source", "log") if kind in kinds]
                or seen.intersection(kinds) or operation["expected_revision"] != revision
                or not isinstance(operation["state"], str) or operation["state"] not in {"pending", "completed"}
                or operation["state"] == "pending" and index != len(operations) - 1):
            raise ValueError("Build input pruning operation identity is invalid")
        old = _positive_integer(operation["previous_charge_bytes"], "Previous charge",
                                record["reservation_bytes"])
        new = _positive_integer(operation["charge_bytes"], "Pruned charge", old)
        credit = sum(max(4096, payloads[kind]["size"]) for kind in kinds if payloads[kind] is not None)
        if not credit or new != old - credit or previous is not None and old != previous:
            raise ValueError("Build input pruning charge is invalid")
        if index == 0 and old - sum(max(4096, item["size"]) for item in payloads.values()
                                   if item is not None) < 6 * 4096:
            raise ValueError("Build input pruning omits metadata charge")
        seen.update(kinds)
        previous = new
        revision = _prune_revision(revision, kinds)
    last = operations[-1]
    if record["charge_bytes"] != last["charge_bytes" if last["state"] == "completed"
                                      else "previous_charge_bytes"]:
        raise ValueError("Build input pruning released charge before completion")
    return value


@contextmanager
def _private_root(path: str) -> Iterator[int]:
    """Open a private root while checking no-follow ancestry and rename access."""
    try:
        validate_filesystem_path(path, must_exist=True)
    except ValueError:
        raise ValueError("Build input root is not a safe existing path") from None
    if (not os.path.isabs(path) or os.path.normpath(path) != path or path == "/"
            or any(char.isspace() for char in path) or any(char in path for char in "%:")):
        raise ValueError("Build input root must be a canonical absolute path")

    trusted_owners = {0, os.geteuid()}
    descriptor = os.open("/", _DIRECTORY)
    try:
        parent_info = os.fstat(descriptor)
        if not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_uid not in trusted_owners:
            raise ValueError("Build input root has an untrusted ancestor")
        for part in path.split("/")[1:]:
            try:
                child = os.open(part, _DIRECTORY, dir_fd=descriptor)
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise ValueError("Build input root contains a symlink") from exc
                raise
            try:
                info = os.fstat(child)
                if not stat.S_ISDIR(info.st_mode) or info.st_uid not in trusted_owners:
                    raise ValueError("Build input root has an untrusted ancestor")
                if stat.S_IMODE(parent_info.st_mode) & 0o022:
                    sticky_protection = (
                        parent_info.st_mode & stat.S_ISVTX
                        and parent_info.st_uid in trusted_owners
                        and info.st_uid in trusted_owners
                    )
                    if not sticky_protection:
                        raise ValueError("Build input root has a renameable ancestor")
            except BaseException:
                os.close(child)
                raise
            os.close(descriptor)
            descriptor = child
            parent_info = info

        root_info = os.fstat(descriptor)
        if (root_info.st_uid != os.geteuid() or stat.S_IMODE(root_info.st_mode) != 0o700):
            raise ValueError("Build input root must be effective-UID-owned mode 0700")
        yield descriptor
    finally:
        os.close(descriptor)


def _stat_named(parent_fd: int, name: str, label: str) -> os.stat_result:
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        raise ValueError(f"Build input {label} is missing") from None


def _check_regular(
    info: os.stat_result,
    label: str,
    *,
    modes: frozenset[int],
    max_size: int | None = None,
    exact_size: int | None = None,
) -> None:
    mode = stat.S_IMODE(info.st_mode)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_nlink != 1 or mode not in modes):
        raise ValueError(f"Build input {label} must be an owned, unlinked regular file with a private mode")
    if exact_size is not None and info.st_size != exact_size:
        raise ValueError(f"Build input {label} has an unexpected size")
    if max_size is not None and not 0 <= info.st_size <= max_size:
        raise ValueError(f"Build input {label} exceeds its reserved size")


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("Build input file write made no progress")
        view = view[written:]


def _rename_noreplace(parent_fd: int, source: str, destination: str) -> None:
    """Publish a source file atomically without replacing any existing name."""
    library = ctypes.CDLL(None, use_errno=True)
    rename = getattr(library, "renameat2", None)
    if rename is None:
        raise NotImplementedError("Build input publication requires no-replace rename support")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    result = rename(
        parent_fd, os.fsencode(source), parent_fd, os.fsencode(destination), RENAME_NOREPLACE,
    )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), destination)


class BuildInputStore:
    """Bounded durable source admission for the worker's fixed input root.

    ``root`` must already be the effective UID's private ``0700`` worker input
    directory. Every accepted request keeps its immutable intent and original
    reservation. Only explicit trusted terminal settlement/pruning can lower its
    charge; admission never settles, prunes or resumes pending deletion.
    """

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        max_records: int = DEFAULT_MAX_RECORDS,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        raw_root = os.fspath(root)
        if not isinstance(raw_root, str):
            raise ValueError("Build input root must be text")
        if type(max_records) is not int or not 1 <= max_records <= MAX_RECORDS:
            raise ValueError("Build input record limit is outside supported bounds")
        if type(max_bytes) is not int or not 1 <= max_bytes <= 2**63 - 1:
            raise ValueError("Build input byte limit is outside supported bounds")
        self.root = raw_root
        self.max_records = max_records
        self.max_bytes = max_bytes
        with _private_root(self.root):
            pass

    @contextmanager
    def _locked(self, root_fd: int) -> Iterator[None]:
        """Hold one stable cross-process lock and verify its initialization witness."""
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
        try:
            lock_fd = os.open(
                _LOCK_NAME, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=root_fd,
            )
            os.fchmod(lock_fd, 0o600)
            os.fsync(lock_fd)
            os.fsync(root_fd)
        except FileExistsError:
            lock_fd = os.open(_LOCK_NAME, flags, dir_fd=root_fd)
        try:
            lock_info = os.fstat(lock_fd)
            _check_regular(lock_info, "store lock", modes=frozenset({0o600}))
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            named_lock = _stat_named(root_fd, _LOCK_NAME, "store lock")
            _check_regular(named_lock, "store lock", modes=frozenset({0o600}))
            if not _same_file(lock_info, named_lock):
                raise ValueError("Build input store lock changed while opening")
            self._initialize_lock(root_fd, lock_fd)
            yield
        finally:
            os.close(lock_fd)

    def _initialize_lock(self, root_fd: int, lock_fd: int) -> None:
        initialized_info = None
        try:
            initialized_info = os.stat(_INITIALIZED_NAME, dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        lock_bytes = os.pread(lock_fd, len(_WITNESS) + 1, 0)
        if initialized_info is not None:
            _check_regular(initialized_info, "initialization witness", modes=frozenset({0o600}))
            marker_fd = os.open(_INITIALIZED_NAME, _READ, dir_fd=root_fd)
            try:
                opened = os.fstat(marker_fd)
                _check_regular(opened, "initialization witness", modes=frozenset({0o600}))
                if not _same_file(initialized_info, opened) or os.read(marker_fd, len(_WITNESS) + 1) != _WITNESS:
                    raise ValueError("Build input initialization witness is invalid")
            finally:
                os.close(marker_fd)
            if lock_bytes != _WITNESS:
                raise ValueError("Build input stable lock lost its initialization witness")
            return

        # A crash during first-time initialization can be completed only before
        # any intent exists. Existing records with no witness require review.
        if lock_bytes not in (b"", _WITNESS):
            raise ValueError("Build input stable lock is corrupt")
        names = set(os.listdir(root_fd)) - {_LOCK_NAME}
        if names:
            raise ValueError("Build input initialization witness is missing with retained data")
        if lock_bytes == b"":
            if os.pwrite(lock_fd, _WITNESS, 0) != len(_WITNESS):
                raise OSError("Build input stable lock witness write was incomplete")
            os.fsync(lock_fd)
        marker_fd = os.open(
            _INITIALIZED_NAME,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=root_fd,
        )
        try:
            os.fchmod(marker_fd, 0o600)
            _write_all(marker_fd, _WITNESS)
            os.fsync(marker_fd)
        finally:
            os.close(marker_fd)
        os.fsync(root_fd)

    @staticmethod
    def _validate_intent(value: object, *, request_id: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("Build input store contains an invalid retained intent")
        schema = value.get("schema")
        if type(schema) is not int or schema not in {1, 2, 3}:
            raise ValueError("Build input intent has an unsupported schema")
        if set(value) != {1: _INTENT_FIELDS, 2: _SETTLED_FIELDS, 3: _PRUNING_FIELDS}[schema]:
            raise ValueError("Build input store contains an invalid retained intent")
        actual_id = _identifier(value["request_id"], "Request ID")
        if actual_id != request_id:
            raise ValueError("Build input intent filename does not match its request ID")
        principal = _owner(value["owner"])
        request_sha256 = _identifier(value["request_sha256"], "Request digest", _SHA256)
        source_sha256 = _identifier(value["source_sha256"], "Source digest", _SHA256)
        source_size = _positive_integer(
            value["source_size"], "Source size", DEFAULT_SOURCE_LIMITS.max_snapshot_bytes,
        )
        reservation = _positive_integer(value["reservation_bytes"], "Input reservation", 2**63 - 1)
        if reservation < source_size + MAX_DESCRIPTOR_BYTES + 1024 + 256 * 1024**2 + 2:
            raise ValueError("Build input intent has an incomplete retention reservation")
        result = {
            "schema": schema,
            "request_id": actual_id,
            "owner": principal,
            "request_sha256": request_sha256,
            "source_size": source_size,
            "source_sha256": source_sha256,
            "reservation_bytes": reservation,
        }
        if schema in {2, 3}:
            charge = _positive_integer(value["charge_bytes"], "Retained charge", reservation)
            if schema == 2 and charge < source_size + MAX_INTENT_BYTES:
                raise ValueError("Build input retained charge omits source or metadata")
            result["charge_bytes"] = charge
            result["settlement_revision"] = _identifier(
                value["settlement_revision"], "Settlement revision", _SHA256,
            )
        if schema == 3:
            result["pruning"] = _validate_pruning(value["pruning"], result)
        return result

    def _read_intent(self, root_fd: int, request_id: str, *,
                     reconcile: bool = True) -> tuple[dict[str, Any], int]:
        name = request_id + ".upload.json"
        named = _stat_named(root_fd, name, "intent")
        descriptor = os.open(name, _READ, dir_fd=root_fd)
        try:
            opened = os.fstat(descriptor)
            _check_regular(opened, "intent", modes=frozenset({0o600}), max_size=MAX_PRUNING_INTENT_BYTES)
            if not _same_file(named, opened):
                raise ValueError("Build input intent changed while opening")
            chunks = []
            total = 0
            while total <= MAX_PRUNING_INTENT_BYTES:
                chunk = os.read(descriptor, min(READ_CHUNK_BYTES, MAX_PRUNING_INTENT_BYTES + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            raw = b"".join(chunks)
            if len(raw) > MAX_PRUNING_INTENT_BYTES:
                raise ValueError("Build input intent exceeds its encoded-size limit")
            record = self._validate_intent(decode_document(raw, maximum=MAX_PRUNING_INTENT_BYTES), request_id=request_id)
            maximum = MAX_PRUNING_INTENT_BYTES if record["schema"] == 3 else MAX_INTENT_BYTES
            if encode_document(record, maximum=maximum) != raw:
                raise ValueError("Build input intent is not canonically encoded")
            if not reconcile:
                identity = lambda info: (info.st_dev, info.st_ino, info.st_uid, info.st_nlink,
                                         info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                if (len(raw) != opened.st_size or identity(os.fstat(descriptor)) != identity(opened)
                        or identity(_stat_named(root_fd, name, "intent")) != identity(opened)):
                    raise ValueError("Build input intent changed during capacity inspection")
            # Reconcile a replacement whose directory sync/reply was lost before
            # admission can use its lower charge. Failure still refuses admission.
            if reconcile and record["schema"] in {2, 3}:
                os.fsync(descriptor)
                os.fsync(root_fd)
            return record, len(raw)
        finally:
            os.close(descriptor)

    def _payload_info(
        self,
        root_fd: int,
        request_id: str,
        record: dict[str, Any],
        *,
        published: bool,
    ) -> os.stat_result | None:
        name = request_id + (".source" if published else ".part")
        try:
            named = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        modes = frozenset({0o400}) if published else frozenset({0o400, 0o600})
        _check_regular(
            named,
            "published source" if published else "partial source",
            modes=modes,
            exact_size=record["source_size"] if published else None,
            max_size=None if published else record["source_size"],
        )
        return named

    def _scan_intents(
        self, root_fd: int, *, pruning_temporary: str | None = None, reconcile: bool = True,
    ) -> tuple[dict[str, tuple[dict[str, Any], int, os.stat_result | None, os.stat_result | None]], int, int]:
        if reconcile:
            names = set(os.listdir(root_fd))
        else:
            names = set()
            with os.scandir(root_fd) as entries:
                for entry in entries:
                    names.add(entry.name)
                    if len(names) > 3 * self.max_records + 2:
                        raise ValueError("Build input root exceeds its entry bound")
        allowed = {_LOCK_NAME, _INITIALIZED_NAME}
        intent_ids: set[str] = set()
        source_ids: set[str] = set()
        part_ids: set[str] = set()
        for name in names:
            if name in allowed:
                continue
            # Only explicit prune may inspect its own fixed temporary. It must
            # validate an exact staged transition before discarding that file.
            if name == pruning_temporary:
                continue
            match = re.fullmatch(r"([0-9a-f]{32})\.upload\.json", name)
            if match is not None:
                intent_ids.add(match.group(1))
                continue
            match = re.fullmatch(r"([0-9a-f]{32})\.source", name)
            if match is not None:
                source_ids.add(match.group(1))
                continue
            match = re.fullmatch(r"([0-9a-f]{32})\.part", name)
            if match is not None:
                part_ids.add(match.group(1))
                continue
            raise ValueError("Build input root contains an unrecognized retained entry")
        if (source_ids | part_ids) - intent_ids:
            raise ValueError("Build input root contains source data without a durable intent")
        if len(intent_ids) > self.max_records:
            raise ValueError("Build input store exceeds its retained record limit")

        intents: dict[str, tuple[dict[str, Any], int, os.stat_result | None, os.stat_result | None]] = {}
        reserved = 0
        metadata_bytes = 0
        for request_id in sorted(intent_ids):
            record, encoded_size = self._read_intent(root_fd, request_id, reconcile=reconcile)
            source_info = self._payload_info(root_fd, request_id, record, published=True)
            part_info = self._payload_info(root_fd, request_id, record, published=False)
            if source_info is not None and part_info is not None:
                raise ValueError("Build input has both published and partial source evidence")
            if record["schema"] == 3:
                operations = record["pruning"]["operations"]
                selected = [op for op in operations if "source" in op["kinds"]]
                if (part_info is not None or source_info is None and not selected
                        or source_info is not None and selected and selected[0]["state"] == "completed"):
                    raise ValueError("Build input source conflicts with pruning evidence")
            intents[request_id] = record, encoded_size, source_info, part_info
            reserved += record.get("charge_bytes", record["reservation_bytes"])
            metadata_bytes += encoded_size
        if reserved > self.max_bytes:
            raise ValueError("Build input store exceeds its retained byte reservation")
        if metadata_bytes > min(MAX_RETAINED_METADATA_BYTES, self.max_records * MAX_PRUNING_INTENT_BYTES):
            raise ValueError("Build input store exceeds its retained metadata bound")
        return intents, reserved, metadata_bytes

    @contextmanager
    def capacity_snapshot(self) -> Iterator[tuple[
        dict[str, tuple[dict[str, Any], int, os.stat_result | None, os.stat_result | None]], int, int,
    ]]:
        """Yield trusted private accounting evidence under an existing input lock.

        Nonblocking and strictly noninitializing: require both exact witnesses,
        never sync settled metadata or open payloads. Hold this context while
        taking a short job capacity snapshot, in the admission lock order.
        """
        with _private_root(self.root) as root_fd:
            lock_fd = os.open(_LOCK_NAME, _READ, dir_fd=root_fd)
            try:
                opened = os.fstat(lock_fd)
                _check_regular(opened, "store lock", modes=frozenset({0o600}), exact_size=len(_WITNESS))
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise RuntimeError("Build input accounting is busy") from None
                named = _stat_named(root_fd, _LOCK_NAME, "store lock")
                _check_regular(named, "store lock", modes=frozenset({0o600}), exact_size=len(_WITNESS))
                if not _same_file(opened, named) or os.pread(lock_fd, len(_WITNESS) + 1, 0) != _WITNESS:
                    raise ValueError("Build input initialization witness is invalid")
                marker_fd = os.open(_INITIALIZED_NAME, _READ, dir_fd=root_fd)
                try:
                    marker = os.fstat(marker_fd)
                    _check_regular(marker, "initialization witness", modes=frozenset({0o600}),
                                   exact_size=len(_WITNESS))
                    if (not _same_file(marker, _stat_named(root_fd, _INITIALIZED_NAME, "witness"))
                            or os.read(marker_fd, len(_WITNESS) + 1) != _WITNESS):
                        raise ValueError("Build input initialization witness is invalid")
                finally:
                    os.close(marker_fd)
                yield self._scan_intents(root_fd, reconcile=False)
            finally:
                os.close(lock_fd)

    @staticmethod
    def _reservation(source_size: int, request: dict[str, Any]) -> int:
        limits = request["limits"]
        return (
            source_size + limits["scratch_bytes"] + limits["artifact_bytes"]
            + MAX_DESCRIPTOR_BYTES + 1024 + limits["log_bytes"]
        )

    def _create_intent(self, root_fd: int, record: dict[str, Any]) -> None:
        encoded = encode_document(record, maximum=MAX_INTENT_BYTES)
        if len(encoded) > MAX_INTENT_BYTES:
            raise ValueError("Build input intent exceeds its encoded-size limit")
        name = record["request_id"] + ".upload.json"
        descriptor = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=root_fd,
        )
        try:
            os.fchmod(descriptor, 0o600)
            _write_all(descriptor, encoded)
            os.fsync(descriptor)
            named = _stat_named(root_fd, name, "new intent")
            opened = os.fstat(descriptor)
            _check_regular(opened, "new intent", modes=frozenset({0o600}), exact_size=len(encoded))
            if not _same_file(named, opened):
                raise ValueError("Build input intent changed while persisting")
            os.fsync(root_fd)
        finally:
            os.close(descriptor)

    def _sync_part(self, descriptor: int) -> None:
        """Persist completed bytes and make a staged input read-only."""
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o400)
        os.fsync(descriptor)

    def _save_settlement(self, root_fd: int, record: dict[str, Any]) -> None:
        """Atomically replace only bounded intent metadata under the input lock.

        A pre-replace failure leaves the prior charge intact. A post-replace
        sync failure is ambiguous; reopening syncs the retained revision before
        using it, and the same settlement revision can be retried safely.
        """
        request_id = record["request_id"]
        self._validate_intent(record, request_id=request_id)
        encoded = encode_document(record, maximum=(MAX_PRUNING_INTENT_BYTES
                                  if record["schema"] == 3 else MAX_INTENT_BYTES))
        name = request_id + ".upload.json"
        temporary = request_id + ".settlement.tmp"
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0), 0o600, dir_fd=root_fd,
        )
        try:
            os.fchmod(descriptor, 0o600)
            _write_all(descriptor, encoded)
            os.fsync(descriptor)
            _check_regular(_stat_named(root_fd, name, "intent"), "intent", modes=frozenset({0o600}))
            os.replace(temporary, name, src_dir_fd=root_fd, dst_dir_fd=root_fd)
            os.fsync(root_fd)
        finally:
            os.close(descriptor)
            try:
                os.unlink(temporary, dir_fd=root_fd)
            except FileNotFoundError:
                pass

    def _sync_after_rename(self, root_fd: int) -> None:
        """Persist the published directory entry after no-replace rename."""
        os.fsync(root_fd)

    @staticmethod
    def _read_chunk(source: BinaryIO, maximum: int) -> bytes:
        chunk = source.read(maximum)
        if not isinstance(chunk, bytes):
            raise ValueError("Build input stream did not return bytes")
        if len(chunk) > maximum:
            raise ValueError("Build input stream exceeded its bounded read")
        return chunk

    def _consume_stream(self, source: BinaryIO, source_size: int, expected_sha256: str) -> None:
        digest = hashlib.sha256()
        remaining = source_size
        while remaining:
            chunk = self._read_chunk(source, min(READ_CHUNK_BYTES, remaining))
            if not chunk:
                raise ValueError("Build input stream ended before its declared size")
            digest.update(chunk)
            remaining -= len(chunk)
        if self._read_chunk(source, 1):
            raise ValueError("Build input stream contains trailing bytes")
        if digest.hexdigest() != expected_sha256:
            raise ValueError("Build input stream does not match the pinned source digest")

    def _write_part(
        self,
        root_fd: int,
        request_id: str,
        source: BinaryIO,
        source_size: int,
        expected_sha256: str,
    ) -> None:
        part_name = request_id + ".part"
        source_name = request_id + ".source"
        descriptor = os.open(
            part_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=root_fd,
        )
        try:
            os.fchmod(descriptor, 0o600)
            _check_regular(os.fstat(descriptor), "new partial source", modes=frozenset({0o600}), exact_size=0)
            digest = hashlib.sha256()
            remaining = source_size
            while remaining:
                chunk = self._read_chunk(source, min(READ_CHUNK_BYTES, remaining))
                if not chunk:
                    self._sync_part(descriptor)
                    raise ValueError("Build input stream ended before its declared size")
                _write_all(descriptor, chunk)
                digest.update(chunk)
                remaining -= len(chunk)
            trailing = self._read_chunk(source, 1)
            self._sync_part(descriptor)
            if trailing:
                raise ValueError("Build input stream contains trailing bytes")
            if digest.hexdigest() != expected_sha256:
                raise ValueError("Build input stream does not match the pinned source digest")
            _check_regular(
                os.fstat(descriptor), "completed partial source", modes=frozenset({0o400}),
                exact_size=source_size,
            )
            named = _stat_named(root_fd, part_name, "partial source")
            if not _same_file(named, os.fstat(descriptor)):
                raise ValueError("Build input partial source changed before publication")
            _rename_noreplace(root_fd, part_name, source_name)
            self._sync_after_rename(root_fd)
        finally:
            os.close(descriptor)

    def _verify_published(
        self,
        root_fd: int,
        request_id: str,
        source_size: int,
        expected_sha256: str,
    ) -> None:
        name = request_id + ".source"
        named_before = _stat_named(root_fd, name, "published source")
        _check_regular(
            named_before, "published source", modes=frozenset({0o400}), exact_size=source_size,
        )
        descriptor = os.open(name, _READ, dir_fd=root_fd)
        try:
            before = os.fstat(descriptor)
            _check_regular(before, "published source", modes=frozenset({0o400}), exact_size=source_size)
            if not _same_file(named_before, before):
                raise ValueError("Build input source changed while opening")
            digest = hashlib.sha256()
            total = 0
            while True:
                chunk = os.read(descriptor, READ_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > source_size:
                    raise ValueError("Published build source exceeds its reserved size")
                digest.update(chunk)
            after = os.fstat(descriptor)
            named_after = _stat_named(root_fd, name, "published source")
            identity = (before.st_dev, before.st_ino, before.st_uid, before.st_nlink,
                        before.st_mode, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            after_identity = (after.st_dev, after.st_ino, after.st_uid, after.st_nlink,
                              after.st_mode, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            named_identity = (named_after.st_dev, named_after.st_ino, named_after.st_uid,
                              named_after.st_nlink, named_after.st_mode, named_after.st_size,
                              named_after.st_mtime_ns, named_after.st_ctime_ns)
            if identity != after_identity or identity != named_identity:
                raise ValueError("Published build source changed while verifying")
            if total != source_size or digest.hexdigest() != expected_sha256:
                raise ValueError("Published build source does not match its retained intent")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.fsync(root_fd)

    def admit(
        self,
        request: object,
        *,
        owner: str,
        source: BinaryIO | None,
        source_size: int,
        jobs: BuildJobStore,
    ) -> dict[str, Any]:
        """Persist source intent, publish verified bytes, and queue the job.

        Authentication and authorization must already have succeeded. A
        repeated call may omit ``source`` only after the matching source file
        has been published or durably pruned; a supplied replay stream is
        consumed and verified. Pruned replay requires the exact terminal job
        and never calls submit or recreates source.
        """
        principal = _owner(owner)
        job = parse_build_request(request)
        digest = request_digest(job)
        request_id = job["request_id"]
        source_size = _positive_integer(
            source_size, "Source size", DEFAULT_SOURCE_LIMITS.max_snapshot_bytes,
        )
        if source is not None and not callable(getattr(source, "read", None)):
            raise ValueError("Build input source must be a binary stream")
        expected_source_sha256 = job["source"]["snapshot_sha256"]
        reservation = self._reservation(source_size, job)
        if reservation > 2**63 - 1:
            raise ValueError("Build input reservation exceeds supported bounds")
        expected_intent = {
            "schema": 1,
            "request_id": request_id,
            "owner": principal,
            "request_sha256": digest,
            "source_size": source_size,
            "source_sha256": expected_source_sha256,
            "reservation_bytes": reservation,
        }
        encoded_intent = encode_document(expected_intent, maximum=MAX_INTENT_BYTES)

        with _private_root(self.root) as root_fd:
            with self._locked(root_fd):
                intents, reserved, metadata_bytes = self._scan_intents(root_fd)
                existing = intents.get(request_id)
                if existing is not None:
                    record, _encoded_size, source_info, part_info = existing
                    if record["owner"] != principal:
                        raise PermissionError("Build input request ID belongs to another principal")
                    if any(record[key] != value for key, value in expected_intent.items() if key != "schema"):
                        raise ValueError("Build input request ID is already bound to different inputs")
                    if part_info is not None:
                        raise ValueError("Incomplete build input intent is retained for operator reconciliation")
                    if record["schema"] == 3:
                        if any(op["state"] == "pending" for op in record["pruning"]["operations"]):
                            raise ValueError("Build input pruning is pending explicit completion")
                        # Never call submit after pruning: a missing job must
                        # not create replacement work, even if source remains.
                        receipt = jobs.replay_terminal(
                            job, owner=principal, expected_job_sha256=record["pruning"]["job_sha256"],
                        )
                        if source_info is not None:
                            self._verify_published(root_fd, request_id, source_size, expected_source_sha256)
                        if source is not None:
                            self._consume_stream(source, source_size, expected_source_sha256)
                        return receipt
                    if source_info is None:
                        raise ValueError("Build input intent has no complete published source")
                    self._verify_published(root_fd, request_id, source_size, expected_source_sha256)
                    if source is not None:
                        self._consume_stream(source, source_size, expected_source_sha256)
                    return jobs.submit(job, owner=principal)

                if source is None:
                    raise ValueError("A new build input admission requires a source stream")
                if len(intents) >= self.max_records:
                    raise ValueError("Build input store is full; retained evidence cannot be pruned")
                if reserved + reservation > self.max_bytes:
                    raise ValueError("Build input store lacks capacity for the full retention reservation")
                if len(encoded_intent) > MAX_INTENT_BYTES:
                    raise ValueError("Build input intent exceeds its encoded-size limit")
                metadata_limit = min(MAX_RETAINED_METADATA_BYTES, self.max_records * MAX_PRUNING_INTENT_BYTES)
                if metadata_bytes + len(encoded_intent) > metadata_limit:
                    raise ValueError("Build input store lacks retained metadata capacity")

                # The exclusive, synced intent is durable before the source
                # stream is read or the fixed partial-file name is created.
                self._create_intent(root_fd, expected_intent)
                self._write_part(
                    root_fd, request_id, source, source_size, expected_source_sha256,
                )
                self._verify_published(root_fd, request_id, source_size, expected_source_sha256)
                return jobs.submit(job, owner=principal)
