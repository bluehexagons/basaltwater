"""Durable, bounded staging for externally built target artifacts.

The caller reserves a target admission before staging and remains responsible
for authentication, policy, deadlines, and activation. This store binds that
reservation to one verified immutable bundle without executing its contents.
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
import shutil
import stat
from typing import Any, BinaryIO

from lib.build_admission import DeploymentAdmissionStore
from lib.build_artifacts import MAGIC, unpack_artifact
from lib.build_protocol import (
    MAX_ARTIFACT_BYTES,
    MAX_DESCRIPTOR_BYTES,
    decode_document,
    encode_document,
    parse_build_request,
    request_digest,
)
from lib.validation import validate_filesystem_path, validate_no_control_characters


DEFAULT_MAX_RECORDS = 128
DEFAULT_MAX_BYTES = 64 * 1024**3
MAX_RECORDS = 4096
MAX_INTENT_BYTES = 2048
MAX_RETAINED_METADATA_BYTES = MAX_RECORDS * MAX_INTENT_BYTES
READ_CHUNK_BYTES = 64 * 1024
RENAME_NOREPLACE = 1
_ID = re.compile(r"[0-9a-f]{32}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_OWNER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}")
_APPLICATION = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_LOCK_NAME = ".build-target-artifacts.lock"
_INITIALIZED_NAME = ".build-target-artifacts.initialized"
_WITNESS = b"basaltwater-build-target-artifacts-v1\n"
_UNCERTAIN_CONTENT = b"target-artifact-staging-uncertain-v1\n"
_INTENT_FIELDS = frozenset({
    "schema", "operation_id", "owner", "application", "generation", "request_id",
    "request_sha256", "source", "bindings_sha256", "artifact_sha256", "artifact_size",
    "expanded_limit", "reservation_bytes",
})
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
_READ = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)


def _owner(value: object) -> str:
    if not isinstance(value, str) or _OWNER.fullmatch(value) is None:
        raise ValueError("Owner must be a safe label of 1–128 characters")
    validate_no_control_characters(value, "Owner")
    return value


def _hex(value: object, label: str, pattern: re.Pattern[str] = _ID) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"{label} must be lowercase hexadecimal")
    return value


def _application(value: object) -> str:
    if not isinstance(value, str) or _APPLICATION.fullmatch(value) is None:
        raise ValueError("Application must be a lowercase slug")
    return value


def _positive_integer(value: object, label: str, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{label} is outside supported limits")
    return value


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev, info.st_ino, info.st_uid, info.st_nlink, info.st_mode,
        info.st_size, info.st_mtime_ns, info.st_ctime_ns,
    )


def _check_regular(
    info: os.stat_result,
    label: str,
    *,
    modes: frozenset[int],
    max_size: int | None = None,
    exact_size: int | None = None,
) -> None:
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) not in modes):
        raise ValueError(f"Target artifact {label} must be a private, unlinked regular file")
    if exact_size is not None and info.st_size != exact_size:
        raise ValueError(f"Target artifact {label} has an unexpected size")
    if max_size is not None and not 0 <= info.st_size <= max_size:
        raise ValueError(f"Target artifact {label} exceeds its retained size")


@contextmanager
def _private_root(path: str) -> Iterator[int]:
    """Open a canonical private root and require protected rename ancestry."""
    try:
        validate_filesystem_path(path, must_exist=True)
    except ValueError:
        raise ValueError("Target artifact root is not a safe existing path") from None
    if (not os.path.isabs(path) or os.path.normpath(path) != path or path == "/"
            or any(char.isspace() for char in path) or any(char in path for char in "%:")):
        raise ValueError("Target artifact root must be a canonical absolute path")

    trusted_owners = {0, os.geteuid()}
    descriptor = os.open("/", _DIRECTORY)
    try:
        parent_info = os.fstat(descriptor)
        if not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_uid not in trusted_owners:
            raise ValueError("Target artifact root has an untrusted ancestor")
        for part in path.split("/")[1:]:
            try:
                child = os.open(part, _DIRECTORY, dir_fd=descriptor)
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise ValueError("Target artifact root contains a symlink") from exc
                raise
            try:
                info = os.fstat(child)
                if not stat.S_ISDIR(info.st_mode) or info.st_uid not in trusted_owners:
                    raise ValueError("Target artifact root has an untrusted ancestor")
                if stat.S_IMODE(parent_info.st_mode) & 0o022:
                    sticky_protection = (
                        parent_info.st_mode & stat.S_ISVTX
                        and parent_info.st_uid in trusted_owners
                        and info.st_uid in trusted_owners
                    )
                    if not sticky_protection:
                        raise ValueError("Target artifact root has a renameable ancestor")
            except BaseException:
                os.close(child)
                raise
            os.close(descriptor)
            descriptor = child
            parent_info = info

        root_info = os.fstat(descriptor)
        if root_info.st_uid != os.geteuid() or stat.S_IMODE(root_info.st_mode) != 0o700:
            raise ValueError("Target artifact root must be effective-UID-owned mode 0700")
        yield descriptor
    finally:
        os.close(descriptor)


def _write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("Target artifact write made no progress")
        view = view[written:]


def _rename_noreplace(parent_fd: int, source: str, destination: str) -> None:
    """Atomically publish one file without replacing any existing name."""
    library = ctypes.CDLL(None, use_errno=True)
    rename = getattr(library, "renameat2", None)
    if rename is None:
        raise NotImplementedError("Target artifact publication requires no-replace rename support")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    result = rename(
        parent_fd, os.fsencode(source), parent_fd, os.fsencode(destination), RENAME_NOREPLACE,
    )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), destination)


class BuildTargetArtifactStore:
    """Durable target staging with immutable per-admission artifact binding.

    ``root`` must already be an effective-UID-owned private ``0700`` directory.
    A complete artifact is acknowledged only after its intent, bytes, and ready
    marker have been synced. Partial and ambiguous records are retained and
    never resumed or rebound. Caller authentication, authorization, and stream
    deadlines remain outside this class.
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
            raise ValueError("Target artifact root must be text")
        if type(max_records) is not int or not 1 <= max_records <= MAX_RECORDS:
            raise ValueError("Target artifact record limit is outside supported bounds")
        if type(max_bytes) is not int or not 1 <= max_bytes <= 2**63 - 1:
            raise ValueError("Target artifact byte limit is outside supported bounds")
        self.root = raw_root
        self.max_records = max_records
        self.max_bytes = max_bytes
        with _private_root(self.root):
            pass

    @contextmanager
    def _locked(self) -> Iterator[int]:
        """Hold the stable root lock and verify its durable initialization witness."""
        with _private_root(self.root) as root_fd:
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
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise RuntimeError("Target artifact store is busy") from exc
                named = os.stat(_LOCK_NAME, dir_fd=root_fd, follow_symlinks=False)
                _check_regular(named, "store lock", modes=frozenset({0o600}))
                if _identity(named) != _identity(lock_info):
                    raise ValueError("Target artifact stable lock changed while opening")
                self._initialize_lock(root_fd, lock_fd)
                yield root_fd
            finally:
                os.close(lock_fd)

    def _initialize_lock(self, root_fd: int, lock_fd: int) -> None:
        marker_info = None
        try:
            marker_info = os.stat(_INITIALIZED_NAME, dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        lock_bytes = os.pread(lock_fd, len(_WITNESS) + 1, 0)
        if marker_info is not None:
            _check_regular(marker_info, "initialization witness", modes=frozenset({0o600}))
            marker_fd = os.open(_INITIALIZED_NAME, _READ, dir_fd=root_fd)
            try:
                opened = os.fstat(marker_fd)
                _check_regular(opened, "initialization witness", modes=frozenset({0o600}))
                if (_identity(marker_info) != _identity(opened)
                        or os.read(marker_fd, len(_WITNESS) + 1) != _WITNESS
                        or lock_bytes != _WITNESS):
                    raise ValueError("Target artifact initialization witness is invalid")
            finally:
                os.close(marker_fd)
            return

        if lock_bytes not in (b"", _WITNESS):
            raise ValueError("Target artifact stable lock witness is corrupt")
        if set(os.listdir(root_fd)) - {_LOCK_NAME}:
            raise ValueError("Target artifact initialization witness is missing with retained data")
        if lock_bytes == b"":
            if os.pwrite(lock_fd, _WITNESS, 0) != len(_WITNESS):
                raise OSError("Target artifact stable lock witness write was incomplete")
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
    def _reservation(artifact_size: int, expanded_limit: int) -> int:
        return artifact_size + expanded_limit + MAX_DESCRIPTOR_BYTES + MAX_INTENT_BYTES

    @classmethod
    def _validate_intent(cls, value: object, *, operation_id: str) -> dict[str, Any]:
        if not isinstance(value, dict) or set(value) != _INTENT_FIELDS:
            raise ValueError("Target artifact store contains an invalid staging intent")
        if type(value["schema"]) is not int or value["schema"] != 1:
            raise ValueError("Target artifact intent has an unsupported schema")
        actual_id = _hex(value["operation_id"], "Operation ID")
        if actual_id != operation_id:
            raise ValueError("Target artifact intent filename does not match its operation ID")
        principal = _owner(value["owner"])
        application = _application(value["application"])
        generation = _positive_integer(value["generation"], "Generation", 2**63 - 1)
        request_id = _hex(value["request_id"], "Request ID")
        if request_id == operation_id:
            raise ValueError("Admission operation ID must differ from the build request ID")
        request_sha256 = _hex(value["request_sha256"], "Request digest", _SHA256)
        source = value["source"]
        if not isinstance(source, dict) or set(source) != {
            "revision", "snapshot_sha256", "manifest_sha256",
        }:
            raise ValueError("Target artifact intent source identity is invalid")
        revision = source["revision"]
        if not isinstance(revision, str) or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", revision) is None:
            raise ValueError("Target artifact intent source revision is invalid")
        clean_source = {
            "revision": revision,
            "snapshot_sha256": _hex(source["snapshot_sha256"], "Source digest", _SHA256),
            "manifest_sha256": _hex(source["manifest_sha256"], "Manifest digest", _SHA256),
        }
        bindings_sha256 = _hex(value["bindings_sha256"], "Bindings digest", _SHA256)
        artifact_sha256 = _hex(value["artifact_sha256"], "Artifact digest", _SHA256)
        artifact_size = _positive_integer(value["artifact_size"], "Artifact size", 2**63 - 1)
        expanded_limit = _positive_integer(value["expanded_limit"], "Expanded verification limit", MAX_ARTIFACT_BYTES)
        reservation = _positive_integer(value["reservation_bytes"], "Retention reservation", 2**63 - 1)
        if reservation != cls._reservation(artifact_size, expanded_limit):
            raise ValueError("Target artifact intent has an incomplete retention reservation")
        if artifact_size > len(MAGIC) + 8 + MAX_DESCRIPTOR_BYTES + expanded_limit:
            raise ValueError("Target artifact intent exceeds its bounded wire size")
        return {
            "schema": 1,
            "operation_id": actual_id,
            "owner": principal,
            "application": application,
            "generation": generation,
            "request_id": request_id,
            "request_sha256": request_sha256,
            "source": clean_source,
            "bindings_sha256": bindings_sha256,
            "artifact_sha256": artifact_sha256,
            "artifact_size": artifact_size,
            "expanded_limit": expanded_limit,
            "reservation_bytes": reservation,
        }

    @staticmethod
    def _entry_name(operation_id: str, suffix: str) -> str:
        return operation_id + suffix

    def _read_intent(self, root_fd: int, operation_id: str) -> tuple[dict[str, Any], int]:
        name = self._entry_name(operation_id, ".intent.json")
        named = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        _check_regular(named, "intent", modes=frozenset({0o600}), max_size=MAX_INTENT_BYTES)
        descriptor = os.open(name, _READ, dir_fd=root_fd)
        try:
            before = os.fstat(descriptor)
            _check_regular(before, "intent", modes=frozenset({0o600}), max_size=MAX_INTENT_BYTES)
            if _identity(named) != _identity(before):
                raise ValueError("Target artifact intent changed while opening")
            chunks = []
            total = 0
            while total <= MAX_INTENT_BYTES:
                chunk = os.read(descriptor, min(READ_CHUNK_BYTES, MAX_INTENT_BYTES + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            raw = b"".join(chunks)
            after = os.fstat(descriptor)
            named_after = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
            if _identity(before) != _identity(after) or _identity(before) != _identity(named_after):
                raise ValueError("Target artifact intent changed while reading")
            if len(raw) > MAX_INTENT_BYTES:
                raise ValueError("Target artifact intent exceeds its encoded-size limit")
            record = self._validate_intent(
                decode_document(raw, maximum=MAX_INTENT_BYTES), operation_id=operation_id,
            )
            if encode_document(record, maximum=MAX_INTENT_BYTES) != raw:
                raise ValueError("Target artifact intent is not canonically encoded")
            return record, len(raw)
        finally:
            os.close(descriptor)

    def _read_fixed(self, root_fd: int, name: str, label: str, *, modes: frozenset[int]) -> bytes:
        named = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        _check_regular(named, label, modes=modes, max_size=MAX_INTENT_BYTES)
        descriptor = os.open(name, _READ, dir_fd=root_fd)
        try:
            before = os.fstat(descriptor)
            _check_regular(before, label, modes=modes, max_size=MAX_INTENT_BYTES)
            if _identity(named) != _identity(before):
                raise ValueError(f"Target artifact {label} changed while opening")
            raw = bytearray()
            while len(raw) <= MAX_INTENT_BYTES:
                chunk = os.read(descriptor, min(READ_CHUNK_BYTES, MAX_INTENT_BYTES + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
            after = os.fstat(descriptor)
            named_after = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
            if _identity(before) != _identity(after) or _identity(before) != _identity(named_after):
                raise ValueError(f"Target artifact {label} changed while reading")
            if len(raw) > MAX_INTENT_BYTES:
                raise ValueError(f"Target artifact {label} exceeds its size limit")
            return bytes(raw)
        finally:
            os.close(descriptor)

    def _readiness(self, record: dict[str, Any]) -> bytes:
        return encode_document({
            "schema": 1,
            "operation_id": record["operation_id"],
            "request_sha256": record["request_sha256"],
            "artifact_sha256": record["artifact_sha256"],
            "artifact_size": record["artifact_size"],
        }, maximum=MAX_INTENT_BYTES)

    def _scan(self, root_fd: int) -> tuple[dict[str, tuple[dict[str, Any], int, set[str]]], int, int]:
        names = set(os.listdir(root_fd))
        allowed = {_LOCK_NAME, _INITIALIZED_NAME}
        suffixes = (".intent.json", ".part", ".bundle", ".ready", ".verify", ".uncertain")
        entries: dict[str, set[str]] = {}
        for name in names:
            if name in allowed:
                continue
            matched = False
            for suffix in suffixes:
                if name.endswith(suffix):
                    operation_id = name[:-len(suffix)]
                    if _ID.fullmatch(operation_id) is not None:
                        entries.setdefault(operation_id, set()).add(suffix)
                        matched = True
                        break
            if not matched:
                raise ValueError("Target artifact root contains an unrecognized retained entry")
        for operation_id, suffix_set in entries.items():
            if ".intent.json" not in suffix_set:
                raise ValueError("Target artifact data exists without its durable intent")
            if ".bundle" in suffix_set and ".part" in suffix_set:
                raise ValueError("Target artifact has conflicting partial and published evidence")
            if ".ready" in suffix_set and (".bundle" not in suffix_set
                                             or ".part" in suffix_set or ".verify" in suffix_set):
                raise ValueError("Target artifact ready marker has ambiguous bundle evidence")
            for suffix in suffix_set - {".intent.json", ".verify"}:
                name = self._entry_name(operation_id, suffix)
                named = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
                if suffix == ".bundle":
                    _check_regular(named, "published bundle", modes=frozenset({0o400}))
                elif suffix == ".part":
                    _check_regular(named, "partial bundle", modes=frozenset({0o400, 0o600}))
                else:
                    _check_regular(named, "staging marker", modes=frozenset({0o400, 0o600}),
                                   max_size=MAX_INTENT_BYTES)
            if ".verify" in suffix_set:
                name = self._entry_name(operation_id, ".verify")
                info = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
                if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                        or stat.S_IMODE(info.st_mode) != 0o700):
                    raise ValueError("Target artifact verification tree is unsafe")
        if len(entries) > self.max_records:
            raise ValueError("Target artifact store exceeds its retained record limit")

        records: dict[str, tuple[dict[str, Any], int, set[str]]] = {}
        reserved = 0
        metadata_bytes = 0
        for operation_id in sorted(entries):
            record, encoded_size = self._read_intent(root_fd, operation_id)
            suffix_set = entries[operation_id]
            if ".ready" in suffix_set:
                marker = self._read_fixed(
                    root_fd, self._entry_name(operation_id, ".ready"), "ready marker",
                    modes=frozenset({0o400, 0o600}),
                )
                if marker != self._readiness(record):
                    raise ValueError("Target artifact ready marker does not match its intent")
            if ".uncertain" in suffix_set:
                marker = self._read_fixed(
                    root_fd, self._entry_name(operation_id, ".uncertain"), "uncertainty marker",
                    modes=frozenset({0o400, 0o600}),
                )
                if marker != _UNCERTAIN_CONTENT:
                    raise ValueError("Target artifact uncertainty marker is invalid")
            payload = suffix_set & {".part", ".bundle"}
            for suffix in payload:
                info = os.stat(self._entry_name(operation_id, suffix), dir_fd=root_fd,
                               follow_symlinks=False)
                if suffix == ".bundle":
                    _check_regular(info, "published bundle", modes=frozenset({0o400}),
                                   exact_size=record["artifact_size"])
                else:
                    _check_regular(info, "partial bundle", modes=frozenset({0o400, 0o600}),
                                   max_size=record["artifact_size"])
            records[operation_id] = record, encoded_size, suffix_set
            reserved += record["reservation_bytes"]
            metadata_bytes += encoded_size
        if reserved > self.max_bytes:
            raise ValueError("Target artifact store exceeds its retained byte reservation")
        if metadata_bytes > min(MAX_RETAINED_METADATA_BYTES, self.max_records * MAX_INTENT_BYTES):
            raise ValueError("Target artifact store exceeds its retained metadata bound")
        return records, reserved, metadata_bytes

    def _create_file(self, root_fd: int, name: str, content: bytes, label: str) -> None:
        descriptor = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=root_fd,
        )
        try:
            os.fchmod(descriptor, 0o600)
            _write_all(descriptor, content)
            os.fsync(descriptor)
            named = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
            opened = os.fstat(descriptor)
            _check_regular(opened, label, modes=frozenset({0o600}), exact_size=len(content))
            if _identity(named) != _identity(opened):
                raise ValueError(f"Target artifact {label} changed while persisting")
            os.fsync(root_fd)
        finally:
            os.close(descriptor)

    def _create_intent(self, root_fd: int, record: dict[str, Any]) -> None:
        encoded = encode_document(record, maximum=MAX_INTENT_BYTES)
        if len(encoded) > MAX_INTENT_BYTES:
            raise ValueError("Target artifact intent exceeds its encoded-size limit")
        self._create_file(
            root_fd, self._entry_name(record["operation_id"], ".intent.json"), encoded,
            "new intent",
        )

    @staticmethod
    def _read_chunk(source: BinaryIO, maximum: int) -> bytes:
        chunk = source.read(maximum)
        if not isinstance(chunk, bytes) or len(chunk) > maximum:
            raise ValueError("Target artifact stream exceeded its bounded binary read")
        return chunk

    def _seal_part(self, descriptor: int) -> None:
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o400)
        os.fsync(descriptor)

    @staticmethod
    def _sync_after_rename(root_fd: int) -> None:
        """Persist the no-replace bundle publication before marking it ready."""
        os.fsync(root_fd)

    def _write_part(
        self,
        root_fd: int,
        record: dict[str, Any],
        source: BinaryIO,
    ) -> None:
        operation_id = record["operation_id"]
        name = self._entry_name(operation_id, ".part")
        descriptor = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=root_fd,
        )
        try:
            os.fchmod(descriptor, 0o600)
            os.fsync(root_fd)
            digest = hashlib.sha256()
            remaining = record["artifact_size"]
            try:
                while remaining:
                    chunk = self._read_chunk(source, min(READ_CHUNK_BYTES, remaining))
                    if not chunk:
                        raise ValueError("Target artifact stream ended before its declared size")
                    _write_all(descriptor, chunk)
                    digest.update(chunk)
                    remaining -= len(chunk)
                trailing = self._read_chunk(source, 1)
                self._seal_part(descriptor)
                if trailing:
                    raise ValueError("Target artifact stream contains trailing bytes")
                if digest.hexdigest() != record["artifact_sha256"]:
                    raise ValueError("Target artifact stream does not match its pinned digest")
                _check_regular(
                    os.fstat(descriptor), "completed partial bundle", modes=frozenset({0o400}),
                    exact_size=record["artifact_size"],
                )
            except BaseException:
                try:
                    self._seal_part(descriptor)
                except BaseException:
                    pass
                raise
        finally:
            os.close(descriptor)

    def _consume_replay(self, source: BinaryIO, record: dict[str, Any]) -> None:
        digest = hashlib.sha256()
        remaining = record["artifact_size"]
        while remaining:
            chunk = self._read_chunk(source, min(READ_CHUNK_BYTES, remaining))
            if not chunk:
                raise ValueError("Target artifact replay stream ended before its declared size")
            digest.update(chunk)
            remaining -= len(chunk)
        if self._read_chunk(source, 1):
            raise ValueError("Target artifact replay stream contains trailing bytes")
        if digest.hexdigest() != record["artifact_sha256"]:
            raise ValueError("Target artifact replay stream does not match its pinned digest")

    def _verify_file(self, root_fd: int, record: dict[str, Any], suffix: str) -> tuple[int, ...]:
        name = self._entry_name(record["operation_id"], suffix)
        named_before = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        _check_regular(named_before, "bundle", modes=frozenset({0o400}),
                       exact_size=record["artifact_size"])
        descriptor = os.open(name, _READ, dir_fd=root_fd)
        try:
            before = os.fstat(descriptor)
            _check_regular(before, "bundle", modes=frozenset({0o400}),
                           exact_size=record["artifact_size"])
            if _identity(before) != _identity(named_before):
                raise ValueError("Target artifact bundle changed while opening")
            digest = hashlib.sha256()
            total = 0
            while True:
                chunk = os.read(descriptor, READ_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > record["artifact_size"]:
                    raise ValueError("Target artifact bundle exceeds its retained size")
                digest.update(chunk)
            after = os.fstat(descriptor)
            named_after = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
            if (_identity(before) != _identity(after)
                    or _identity(before) != _identity(named_after)):
                raise ValueError("Target artifact bundle changed while verifying")
            if total != record["artifact_size"] or digest.hexdigest() != record["artifact_sha256"]:
                raise ValueError("Target artifact bundle does not match its retained digest")
            return _identity(after)
        finally:
            os.close(descriptor)

    def _verify_with_unpack(
        self,
        root_fd: int,
        record: dict[str, Any],
        request: dict[str, Any],
        suffix: str,
    ) -> None:
        operation_id = record["operation_id"]
        verification_name = self._entry_name(operation_id, ".verify")
        os.mkdir(verification_name, mode=0o700, dir_fd=root_fd)
        verification_fd = os.open(verification_name, _DIRECTORY, dir_fd=root_fd)
        try:
            verification_info = os.fstat(verification_fd)
            if (verification_info.st_uid != os.geteuid()
                    or stat.S_IMODE(verification_info.st_mode) != 0o700):
                raise ValueError("Target artifact verification tree is unsafe")
            os.fsync(root_fd)
        finally:
            os.close(verification_fd)
        bundle_path = os.path.join(self.root, self._entry_name(operation_id, suffix))
        destination = os.path.join(self.root, verification_name, "verified")
        unpack_artifact(
            bundle_path,
            request,
            destination,
            expected_sha256=record["artifact_sha256"],
        )
        shutil.rmtree(verification_name, dir_fd=root_fd)
        os.fsync(root_fd)

    def _verify_request(
        self,
        root_fd: int,
        record: dict[str, Any],
        request: dict[str, Any],
        suffix: str,
    ) -> None:
        self._verify_file(root_fd, record, suffix)
        self._verify_with_unpack(root_fd, record, request, suffix)
        self._verify_file(root_fd, record, suffix)

    def _create_ready(self, root_fd: int, record: dict[str, Any]) -> None:
        try:
            self._create_file(
                root_fd,
                self._entry_name(record["operation_id"], ".ready"),
                self._readiness(record),
                "ready marker",
            )
        except BaseException:
            try:
                self._create_file(
                    root_fd,
                    self._entry_name(record["operation_id"], ".uncertain"),
                    _UNCERTAIN_CONTENT,
                    "uncertainty marker",
                )
            except BaseException:
                pass
            raise

    def _admission(
        self,
        admissions: DeploymentAdmissionStore,
        operation_id: str,
        generation: int,
        request: dict[str, Any],
        *,
        application: str,
        owner: str,
        bindings_sha256: str,
        artifact_sha256: str,
        allow_completed: bool,
    ) -> dict[str, Any]:
        status = admissions.status(operation_id, owner=owner)
        if status["operation_id"] != operation_id or status["owner"] != owner:
            raise ValueError("Target artifact admission identity does not match")
        if status["application"] != application:
            raise ValueError("Target artifact application does not match its admission")
        if status["generation"] != generation:
            raise ValueError("Target artifact generation does not match its admission")
        if (status["request_id"] != request["request_id"]
                or status["project"] != request["project"]
                or status["request_sha256"] != request_digest(request)
                or status["source"] != request["source"]):
            raise ValueError("Build request does not match the target artifact admission")
        if status["bindings_sha256"] != bindings_sha256:
            raise ValueError("Target bindings do not match the artifact admission")
        state = status["state"]
        if state == "reserved":
            if (status["effective_status"] != "reserved" or status["expired"]
                    or status["superseded"] or status["artifact_sha256"] is not None):
                raise ValueError("Target artifact admission is expired or superseded")
            return status
        if (allow_completed and state == "completed"
                and status["artifact_sha256"] == artifact_sha256):
            return status
        if state == "cancelled":
            raise ValueError("Target artifact admission is cancelled")
        if state == "completed":
            raise ValueError("Completed admission is bound to a different artifact")
        if state in {"activating", "uncertain"}:
            raise RuntimeError("Target artifact admission activation is uncertain")
        raise ValueError("Target artifact admission is not current and reserved")

    @staticmethod
    def _intent_matches(expected: dict[str, Any], retained: dict[str, Any]) -> None:
        if retained["owner"] != expected["owner"]:
            raise PermissionError("Target artifact operation belongs to another principal")
        if retained != expected:
            raise ValueError("Target artifact operation is already bound to different inputs")

    @staticmethod
    def _receipt(record: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation_id": record["operation_id"],
            "application": record["application"],
            "generation": record["generation"],
            "request_id": record["request_id"],
            "request_sha256": record["request_sha256"],
            "source_sha256": record["source"]["snapshot_sha256"],
            "bindings_sha256": record["bindings_sha256"],
            "artifact_sha256": record["artifact_sha256"],
            "artifact_size": record["artifact_size"],
        }

    def stage(
        self,
        operation_id: str,
        generation: int,
        request: object,
        *,
        application: str,
        owner: str,
        bindings_sha256: str,
        artifact_sha256: str,
        artifact_size: int,
        source: BinaryIO | None,
        admissions: DeploymentAdmissionStore,
    ) -> dict[str, Any]:
        """Durably bind one reserved admission to one verified bundle.

        A new operation needs a stream. Replays may omit it only when a complete
        ready bundle is retained; supplied replay streams are also consumed and
        verified. Admission checks occur outside the staging lock so an
        activation callback can safely acquire this store while holding the
        admission lock.
        """
        operation_id = _hex(operation_id, "Operation ID")
        if type(generation) is not int or not 1 <= generation <= 2**63 - 1:
            raise ValueError("Generation is outside supported limits")
        app = _application(application)
        principal = _owner(owner)
        bindings = _hex(bindings_sha256, "Bindings digest", _SHA256)
        artifact = _hex(artifact_sha256, "Artifact digest", _SHA256)
        job = parse_build_request(request)
        if operation_id == job["request_id"]:
            raise ValueError("Admission operation ID must differ from the build request ID")
        size = _positive_integer(artifact_size, "Artifact size", 2**63 - 1)
        expanded_limit = job["limits"]["artifact_bytes"]
        if size > len(MAGIC) + 8 + MAX_DESCRIPTOR_BYTES + expanded_limit:
            raise ValueError("Artifact wire size exceeds the request bounds")
        if source is not None and not callable(getattr(source, "read", None)):
            raise ValueError("Target artifact source must be a binary stream")
        if not isinstance(admissions, DeploymentAdmissionStore):
            raise ValueError("Target artifact staging requires a deployment admission store")

        reservation = self._reservation(size, expanded_limit)
        if reservation > 2**63 - 1:
            raise ValueError("Target artifact reservation exceeds supported bounds")
        expected = {
            "schema": 1,
            "operation_id": operation_id,
            "owner": principal,
            "application": app,
            "generation": generation,
            "request_id": job["request_id"],
            "request_sha256": request_digest(job),
            "source": job["source"],
            "bindings_sha256": bindings,
            "artifact_sha256": artifact,
            "artifact_size": size,
            "expanded_limit": expanded_limit,
            "reservation_bytes": reservation,
        }

        # This first check happens before the store lock or any stream read.
        initial_status = self._admission(
            admissions, operation_id, generation, job, application=app, owner=principal,
            bindings_sha256=bindings, artifact_sha256=artifact, allow_completed=True,
        )
        created = False
        with self._locked() as root_fd:
            records, reserved, metadata_bytes = self._scan(root_fd)
            retained = records.get(operation_id)
            if retained is not None:
                record, _encoded_size, suffixes = retained
                self._intent_matches(expected, record)
                if ".uncertain" in suffixes:
                    raise RuntimeError("Target artifact staging is uncertain and requires reconciliation")
                if ".ready" not in suffixes or ".bundle" not in suffixes:
                    raise ValueError("Incomplete or ambiguous target artifact evidence is retained")
                if initial_status["state"] == "completed" and initial_status["artifact_sha256"] != artifact:
                    raise ValueError("Completed admission is bound to a different artifact")
                self._verify_request(root_fd, record, job, ".bundle")
                if source is not None:
                    self._consume_replay(source, record)
            else:
                if initial_status["state"] != "reserved":
                    raise ValueError("A completed admission cannot start fresh artifact staging")
                if source is None:
                    raise ValueError("A new target artifact stage requires a binary stream")
                if len(records) >= self.max_records:
                    raise ValueError("Target artifact store is full; retained evidence cannot be pruned")
                if reserved + reservation > self.max_bytes:
                    raise ValueError("Target artifact store lacks capacity for the full reservation")
                if metadata_bytes + MAX_INTENT_BYTES > min(
                    MAX_RETAINED_METADATA_BYTES, self.max_records * MAX_INTENT_BYTES,
                ):
                    raise ValueError("Target artifact store lacks retained metadata capacity")
                self._create_intent(root_fd, expected)
                self._write_part(root_fd, expected, source)
                self._verify_request(root_fd, expected, job, ".part")
                created = True

        # Recheck the reservation after consuming and verifying the complete body.
        self._admission(
            admissions, operation_id, generation, job, application=app, owner=principal,
            bindings_sha256=bindings, artifact_sha256=artifact,
            allow_completed=not created,
        )

        if created:
            # The stream and archive are verified, and admission status was read
            # without the staging lock. Reacquire the stable lock before publish.
            with self._locked() as root_fd:
                records, _reserved, _metadata_bytes = self._scan(root_fd)
                retained = records.get(operation_id)
                if retained is None:
                    raise ValueError("Target artifact intent disappeared before publication")
                record, _encoded_size, suffixes = retained
                self._intent_matches(expected, record)
                if ".uncertain" in suffixes:
                    raise RuntimeError("Target artifact staging is uncertain and requires reconciliation")
                if suffixes != {".intent.json", ".part"}:
                    raise ValueError("Target artifact evidence changed before publication")
                self._verify_file(root_fd, record, ".part")
                _rename_noreplace(
                    root_fd, self._entry_name(operation_id, ".part"),
                    self._entry_name(operation_id, ".bundle"),
                )
                # Failure here is uncertain: keep the published evidence but do
                # not create the ready marker that permits replay acknowledgement.
                self._sync_after_rename(root_fd)
                self._verify_file(root_fd, record, ".bundle")
                self._create_ready(root_fd, record)

        # Final status check occurs outside the staging lock, including replays.
        self._admission(
            admissions, operation_id, generation, job, application=app, owner=principal,
            bindings_sha256=bindings, artifact_sha256=artifact, allow_completed=not created,
        )
        return self._receipt(expected)

    @contextmanager
    def open_verified_bundle(
        self,
        operation_id: str,
        generation: int,
        request: object,
        *,
        application: str,
        owner: str,
        bindings_sha256: str,
        artifact_sha256: str,
        artifact_size: int,
    ) -> Iterator[BinaryIO]:
        """Open retained bytes for a trusted activation callback.

        The caller must already hold the admission activation lock and have
        checked current owner, application policy, generation, expiry,
        cancellation, request identity, and bindings. This accessor deliberately
        does not call ``admissions.status()``. It holds the staging lock and a
        read-only file descriptor until the context exits, verifies the intent
        and full bundle hash/identity, and accepts no path from the caller.
        """
        operation_id = _hex(operation_id, "Operation ID")
        if type(generation) is not int or not 1 <= generation <= 2**63 - 1:
            raise ValueError("Generation is outside supported limits")
        app = _application(application)
        principal = _owner(owner)
        bindings = _hex(bindings_sha256, "Bindings digest", _SHA256)
        artifact = _hex(artifact_sha256, "Artifact digest", _SHA256)
        size = _positive_integer(artifact_size, "Artifact size", 2**63 - 1)
        job = parse_build_request(request)
        expected = {
            "schema": 1,
            "operation_id": operation_id,
            "owner": principal,
            "application": app,
            "generation": generation,
            "request_id": job["request_id"],
            "request_sha256": request_digest(job),
            "source": job["source"],
            "bindings_sha256": bindings,
            "artifact_sha256": artifact,
            "artifact_size": size,
            "expanded_limit": job["limits"]["artifact_bytes"],
            "reservation_bytes": self._reservation(size, job["limits"]["artifact_bytes"]),
        }
        if operation_id == job["request_id"]:
            raise ValueError("Admission operation ID must differ from the build request ID")

        with self._locked() as root_fd:
            records, _reserved, _metadata_bytes = self._scan(root_fd)
            retained = records.get(operation_id)
            if retained is None:
                raise ValueError("Verified target artifact was not found")
            record, _encoded_size, suffixes = retained
            self._intent_matches(expected, record)
            if ".uncertain" in suffixes:
                raise RuntimeError("Target artifact staging is uncertain and requires reconciliation")
            if ".ready" not in suffixes or ".bundle" not in suffixes:
                raise ValueError("Target artifact is not durably published")
            marker = self._read_fixed(
                root_fd, self._entry_name(operation_id, ".ready"), "ready marker",
                modes=frozenset({0o400, 0o600}),
            )
            if marker != self._readiness(record):
                raise ValueError("Target artifact ready marker does not match its intent")
            identity = self._verify_file(root_fd, record, ".bundle")
            name = self._entry_name(operation_id, ".bundle")
            descriptor = os.open(name, _READ, dir_fd=root_fd)
            try:
                opened = os.fstat(descriptor)
                _check_regular(opened, "published bundle", modes=frozenset({0o400}), exact_size=size)
                named = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
                if _identity(opened) != identity or _identity(named) != identity:
                    raise ValueError("Target artifact bundle changed before activation")
                os.lseek(descriptor, 0, os.SEEK_SET)
                stream = os.fdopen(descriptor, "rb")
                descriptor = -1
                with stream:
                    yield stream
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
