"""Internal root-only compositor for one admitted Debian build job.

This module is deliberately not connected to a command, service, or transport.
The caller owns authentication, source staging, retention bounds, enrollment,
and activation policy. A durable running claim is written before this module
touches job inputs or host resources.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import copy
import hashlib
import os
import re
import stat
from typing import Any

from lib.atomic_io import parse_json
from lib.build_artifacts import create_artifact
from lib.build_execution import (
    BUILD_HOME,
    HISTORY_PATH,
    INPUT_PATH,
    WORK_ROOT,
    WORKSPACE,
    BuildExecutionResult,
    run_systemd_build,
)
from lib.build_jobs import BuildJobStore, WorkerLease
from lib.build_manifest import DEBIAN_PLATFORM, check_tool_readiness, validate_manifest_request
from lib.build_protocol import MAX_DESCRIPTOR_BYTES, MAX_REQUEST_BYTES, encode_document, parse_build_request
from lib.build_scratch import ScratchHandle, create_scratch
from lib.build_source import MAX_MANIFEST_BYTES, unpack_source_snapshot
from lib.project_manifest import MANIFEST_FILENAME, Manifest, parse_manifest
from lib.setup_directories import directory_fd
from lib.validation import validate_filesystem_path, validate_no_control_characters


_REQUEST_ID = re.compile(r"[0-9a-f]{32}")
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
_READ = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
_SAFE_FAILURE = "Build outcome is unconfirmed; the running claim and evidence were retained"
_ARTIFACT_SIZE_OVERHEAD = MAX_DESCRIPTOR_BYTES + 1024
_SOURCE_NAME = os.path.relpath(os.path.dirname(WORKSPACE), WORK_ROOT)
_CHECKOUT_NAME = os.path.basename(WORKSPACE)
_HISTORY_NAME = os.path.basename(HISTORY_PATH)
_HOME_NAME = os.path.relpath(BUILD_HOME, WORK_ROOT)
_REQUEST_NAME = os.path.relpath(INPUT_PATH, WORK_ROOT)
_HOME_TMP_NAME = "tmp"


class _KnownFailure(Exception):
    """A validated pre-execution rejection or a confirmed failed build."""


@contextmanager
def _private_root(path: str | os.PathLike[str]) -> Iterator[tuple[str, int]]:
    """Open one private root after verifying every path ancestor is trusted."""
    raw = os.fspath(path)
    if not isinstance(raw, str):
        raise _KnownFailure
    try:
        validate_filesystem_path(raw, must_exist=True)
    except ValueError:
        raise _KnownFailure from None
    if (not os.path.isabs(raw) or os.path.normpath(raw) != raw or raw == "/"
            or any(char.isspace() for char in raw) or any(char in raw for char in "%:")):
        raise _KnownFailure

    trusted_owners = {0, os.geteuid()}
    descriptor = os.open("/", _DIRECTORY)
    try:
        parent_info = os.fstat(descriptor)
        if not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_uid not in trusted_owners:
            raise _KnownFailure
        for part in raw.split("/")[1:]:
            child = os.open(part, _DIRECTORY, dir_fd=descriptor)
            try:
                info = os.fstat(child)
                if not stat.S_ISDIR(info.st_mode) or info.st_uid not in trusted_owners:
                    raise _KnownFailure
                if stat.S_IMODE(parent_info.st_mode) & 0o022:
                    sticky_protection = (
                        parent_info.st_mode & stat.S_ISVTX
                        and parent_info.st_uid in trusted_owners
                        and info.st_uid in trusted_owners
                    )
                    if not sticky_protection:
                        raise _KnownFailure
            except BaseException:
                os.close(child)
                raise
            os.close(descriptor)
            descriptor = child
            parent_info = info

        info = os.fstat(descriptor)
        if (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700):
            raise _KnownFailure
        yield raw, descriptor
    finally:
        os.close(descriptor)


def _require_root() -> None:
    if os.geteuid() != 0:
        raise PermissionError("The internal build worker requires root")


def _require_build_identity(uid: int, gid: int) -> None:
    if type(uid) is not int or type(gid) is not int or uid <= 0 or gid <= 0:
        raise ValueError("Build UID and GID must be positive unprivileged IDs")


def _input_file(root_fd: int, root_path: str, request_id: str) -> tuple[int, str, os.stat_result]:
    name = request_id + ".source"
    try:
        named = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
    except FileNotFoundError:
        raise _KnownFailure from None
    if (not stat.S_ISREG(named.st_mode) or named.st_uid != os.geteuid() or named.st_nlink != 1
            or stat.S_IMODE(named.st_mode) not in {0o400, 0o600} or named.st_size <= 0):
        raise _KnownFailure
    descriptor = os.open(name, _READ, dir_fd=root_fd)
    try:
        info = os.fstat(descriptor)
        if ((info.st_dev, info.st_ino) != (named.st_dev, named.st_ino)
                or not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) not in {0o400, 0o600}
                or info.st_size <= 0):
            raise RuntimeError("Build source input changed while opening")
        return descriptor, os.path.join(root_path, name), info
    except BaseException:
        os.close(descriptor)
        raise


def _verify_input_unchanged(root_fd: int, descriptor: int, name: str,
                            before: os.stat_result) -> None:
    after = os.fstat(descriptor)
    named = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
    identity = (before.st_dev, before.st_ino, before.st_uid, before.st_nlink, before.st_mode,
                before.st_size, before.st_mtime_ns, before.st_ctime_ns)
    if identity != (after.st_dev, after.st_ino, after.st_uid, after.st_nlink, after.st_mode,
                    after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise RuntimeError("Build source input changed during verification")
    if identity != (named.st_dev, named.st_ino, named.st_uid, named.st_nlink, named.st_mode,
                    named.st_size, named.st_mtime_ns, named.st_ctime_ns):
        raise RuntimeError("Build source input path changed during verification")


def _read_committed_manifest(checkout_path: str, expected_digest: str) -> Manifest:
    """Parse the exact bounded manifest bytes in the verified source checkout."""
    with directory_fd(checkout_path) as checkout_fd:
        checkout_info = os.fstat(checkout_fd)
        if (checkout_info.st_uid != os.geteuid() or stat.S_IMODE(checkout_info.st_mode) != 0o700):
            raise ValueError("Verified source checkout has an unexpected owner or mode")
        descriptor = os.open(MANIFEST_FILENAME, _READ, dir_fd=checkout_fd)
        try:
            before = os.fstat(descriptor)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid() or before.st_nlink != 1
                    or stat.S_IMODE(before.st_mode) not in {0o644, 0o755}
                    or before.st_size > MAX_MANIFEST_BYTES):
                raise ValueError("Committed build manifest is not a bounded regular file")
            with os.fdopen(os.dup(descriptor), "rb") as source:
                raw = source.read(MAX_MANIFEST_BYTES + 1)
            after = os.fstat(descriptor)
            if len(raw) != before.st_size or len(raw) > MAX_MANIFEST_BYTES:
                raise ValueError("Committed build manifest exceeds its byte limit")
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError("Committed build manifest changed while being read")
        finally:
            os.close(descriptor)
    if hashlib.sha256(raw).hexdigest() != expected_digest:
        raise ValueError("Committed build manifest does not match the admitted source")
    return parse_manifest(parse_json(raw))


def _check_request_policy(job: dict[str, Any], manifest: Manifest) -> None:
    if job["platform"] != DEBIAN_PLATFORM:
        raise _KnownFailure
    if any(component["type"] not in {"static", "service"} for component in job["components"]):
        raise _KnownFailure
    if any(component.type not in {"static", "service"} for component in manifest.components):
        raise _KnownFailure


def _observed_tools(authorize: Callable[[str, dict[str, Any]], object],
                    owner: str, job: dict[str, Any]) -> dict[str, str]:
    try:
        result = authorize(owner, copy.deepcopy(job))
    except PermissionError:
        raise _KnownFailure from None
    if not isinstance(result, dict) or len(result) > 128:
        raise RuntimeError("Build authorization returned an invalid tool observation")
    if any(not isinstance(key, str) or not isinstance(value, str) for key, value in result.items()):
        raise RuntimeError("Build authorization returned an invalid tool observation")
    return dict(result)


def _ready_toolchains(manifest: Manifest, observed: dict[str, str]) -> dict[str, str]:
    try:
        ready = check_tool_readiness(manifest, observed)
    except ValueError:
        raise _KnownFailure from None
    for name, version in ready.items():
        try:
            version.encode("utf-8", errors="strict")
            validate_no_control_characters(version, "Build tool version")
        except (UnicodeError, ValueError):
            raise _KnownFailure from None
        if len(version) > 1024:
            raise _KnownFailure
    return ready


def _cancel_requested(store: BuildJobStore, request_id: str, owner: str) -> bool:
    status = store.status(request_id, owner=owner)
    if (not isinstance(status, dict) or status.get("request_id") != request_id
            or status.get("state") != "running" or type(status.get("cancel_requested")) is not bool):
        raise RuntimeError("Build cancellation status is unavailable")
    return status["cancel_requested"]


def _chown_descriptor(descriptor: int, uid: int, gid: int) -> None:
    """Small native boundary for handing verified scratch entries to the build UID."""
    os.fchown(descriptor, uid, gid)


def _handoff_tree(directory: int, uid: int, gid: int, *, root: bool = False) -> None:
    """Hand only a fresh, regular-file checkout tree to the unprivileged UID."""
    info = os.fstat(directory)
    expected_mode = 0o700 if root else 0o755
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != expected_mode:
        raise RuntimeError("Verified source directory has an unexpected owner or mode")
    for name in os.listdir(directory):
        before = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if stat.S_ISDIR(before.st_mode):
            child = os.open(name, _DIRECTORY, dir_fd=directory)
            try:
                opened = os.fstat(child)
                if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                    raise RuntimeError("Verified source directory changed during ownership handoff")
                if opened.st_uid != os.geteuid() or stat.S_IMODE(opened.st_mode) != 0o755:
                    raise RuntimeError("Verified source directory has an unexpected owner or mode")
                _handoff_tree(child, uid, gid)
            finally:
                os.close(child)
        elif stat.S_ISREG(before.st_mode):
            descriptor = os.open(name, _READ, dir_fd=directory)
            try:
                opened = os.fstat(descriptor)
                expected_mode = 0o755 if before.st_mode & 0o111 else 0o644
                if ((opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                        or opened.st_uid != os.geteuid() or opened.st_nlink != 1
                        or stat.S_IMODE(opened.st_mode) != expected_mode):
                    raise RuntimeError("Verified source file changed during ownership handoff")
                _chown_descriptor(descriptor, uid, gid)
                os.fchmod(descriptor, expected_mode)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        else:
            raise RuntimeError("Verified source contains a link or special file")
    _chown_descriptor(directory, uid, gid)
    os.fchmod(directory, 0o755)
    os.fsync(directory)


def _root_regular(descriptor: int, label: str, *, mode: int) -> None:
    info = os.fstat(descriptor)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != mode):
        raise RuntimeError(f"Scratch {label} has an unexpected owner, type, or mode")


def _verify_fresh_scratch_root(handle: ScratchHandle) -> None:
    with directory_fd(handle.mount_path) as filesystem:
        info = os.fstat(filesystem)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise RuntimeError("Fresh scratch filesystem root has an unexpected owner or mode")


def _provision_layout(handle: ScratchHandle, job: dict[str, Any], uid: int, gid: int) -> None:
    """Provision scratch using descriptor-relative creation and no-follow opens."""
    with directory_fd(handle.mount_path) as filesystem:
        root_info = os.fstat(filesystem)
        if root_info.st_uid != os.geteuid() or stat.S_IMODE(root_info.st_mode) != 0o700:
            raise RuntimeError("Fresh scratch filesystem root has an unexpected owner or mode")
        source = os.open(_SOURCE_NAME, _DIRECTORY, dir_fd=filesystem)
        try:
            source_info = os.fstat(source)
            if source_info.st_uid != os.geteuid() or stat.S_IMODE(source_info.st_mode) != 0o700:
                raise RuntimeError("Unpacked source parent has an unexpected owner or mode")
            checkout = os.open(_CHECKOUT_NAME, _DIRECTORY, dir_fd=source)
            try:
                _handoff_tree(checkout, uid, gid, root=True)
            finally:
                os.close(checkout)
            history = os.open(_HISTORY_NAME, _READ, dir_fd=source)
            try:
                _root_regular(history, "history bundle", mode=0o400)
                os.fchmod(history, 0o444)
                os.fsync(history)
            finally:
                os.close(history)
            os.fchmod(source, 0o755)
            os.fsync(source)
        finally:
            os.close(source)

        os.mkdir(_HOME_NAME, mode=0o700, dir_fd=filesystem)
        home = os.open(_HOME_NAME, _DIRECTORY, dir_fd=filesystem)
        try:
            os.fchmod(home, 0o700)
            os.mkdir(_HOME_TMP_NAME, mode=0o700, dir_fd=home)
            temporary = os.open(_HOME_TMP_NAME, _DIRECTORY, dir_fd=home)
            try:
                _chown_descriptor(temporary, uid, gid)
                os.fchmod(temporary, 0o700)
                os.fsync(temporary)
            finally:
                os.close(temporary)
            _chown_descriptor(home, uid, gid)
            os.fchmod(home, 0o700)
            os.fsync(home)
        finally:
            os.close(home)

        request = os.open(_REQUEST_NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                          | getattr(os, "O_CLOEXEC", 0), 0o400, dir_fd=filesystem)
        try:
            _chown_descriptor(request, os.geteuid(), os.getegid())
            os.fchmod(request, 0o444)
            with os.fdopen(os.dup(request), "wb") as output:
                output.write(encode_document(job, maximum=MAX_REQUEST_BYTES))
                output.flush()
                os.fsync(output.fileno())
            _root_regular(request, "request file", mode=0o444)
            os.fsync(filesystem)
        finally:
            os.close(request)

        os.fchmod(filesystem, 0o755)
        os.fsync(filesystem)


def _create_log(root_fd: int, root_path: str, request_id: str) -> str:
    name = request_id + ".log"
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                         | getattr(os, "O_CLOEXEC", 0), 0o600, dir_fd=root_fd)
    try:
        _chown_descriptor(descriptor, os.geteuid(), os.getegid())
        os.fchmod(descriptor, 0o600)
        _root_regular(descriptor, "log", mode=0o600)
        os.fsync(descriptor)
        os.fsync(root_fd)
    finally:
        os.close(descriptor)
    return os.path.join(root_path, name)


def _verify_artifact(root_fd: int, request_id: str, value: object, limit: int) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"sha256", "size", "descriptor"}:
        raise RuntimeError("Build artifact publication returned an invalid identity")
    expected_digest, expected_size = value["sha256"], value["size"]
    if (not isinstance(expected_digest, str) or re.fullmatch(r"[0-9a-f]{64}", expected_digest) is None
            or type(expected_size) is not int or not 1 <= expected_size <= limit):
        raise RuntimeError("Build artifact publication returned an invalid identity")
    descriptor = os.open(request_id + ".bundle", _READ, dir_fd=root_fd)
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                or before.st_nlink != 1 or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_size != expected_size):
            raise RuntimeError("Published build artifact has an unexpected owner, type, or size")
        digest, size = hashlib.sha256(), 0
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            while content := stream.read(1024 * 1024):
                digest.update(content)
                size += len(content)
        after = os.fstat(descriptor)
        if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                or size != expected_size or digest.hexdigest() != expected_digest):
            raise RuntimeError("Published build artifact changed during verification")
    finally:
        os.close(descriptor)
    return value


def _finish_known_failure(lease: WorkerLease, store: BuildJobStore, request_id: str, owner: str,
                          handle: ScratchHandle | None) -> dict[str, Any]:
    cancelled_before = _cancel_requested(store, request_id, owner)
    if handle is not None:
        handle.release(work_stopped=True)
    cancelled_after = _cancel_requested(store, request_id, owner)
    if cancelled_before and not cancelled_after:
        raise RuntimeError("Build cancellation status changed unexpectedly")
    outcome = "cancelled" if cancelled_after else "failed"
    return lease.finish(request_id, outcome=outcome, work_stopped=True)


def _finish_success(lease: WorkerLease, store: BuildJobStore, request_id: str, owner: str,
                    handle: ScratchHandle, artifact: dict[str, Any]) -> dict[str, Any]:
    if _cancel_requested(store, request_id, owner):
        raise _KnownFailure
    handle.release(work_stopped=True)
    if _cancel_requested(store, request_id, owner):
        return lease.finish(request_id, outcome="cancelled", work_stopped=True)
    try:
        return lease.finish(request_id, outcome="succeeded", work_stopped=True, artifact=artifact)
    except ValueError:
        # The durable finish check closes the race between status() and the
        # terminal write. Only a confirmed cancellation may turn this into a
        # cancelled result; the published bundle is retained as an orphan.
        status = store.status(request_id, owner=owner)
        if status.get("cancel_requested") is True and status.get("state") == "running":
            return lease.finish(request_id, outcome="cancelled", work_stopped=True)
        raise RuntimeError(_SAFE_FAILURE) from None


def run_next(
    store: BuildJobStore,
    *,
    uid: int,
    gid: int,
    inputs_root: str | os.PathLike[str],
    scratch_root: str | os.PathLike[str],
    artifacts_root: str | os.PathLike[str],
    logs_root: str | os.PathLike[str],
    authorize: Callable[[str, dict[str, Any]], object],
) -> dict[str, Any] | None:
    """Claim and run at most one admitted request; return only public job status.

    ``authorize(owner, request)`` is a trusted policy callback. It must recheck
    current project policy and return a dictionary of observed tool versions;
    raise ``PermissionError`` to deny the request. It is called once after the
    durable claim and again immediately before launch.

    Known validation and confirmed build failures release a verified scratch
    filesystem before finishing the job. Any unconfirmed system, storage,
    publication, or cleanup outcome retains the running claim and evidence and
    raises one fixed error without exception details. A later worker must use
    the store's explicit trusted recovery API; this function never replays or
    recovers an interrupted claim.
    """
    _require_root()
    _require_build_identity(uid, gid)
    if not isinstance(store, BuildJobStore) or not callable(authorize):
        raise ValueError("A build store and trusted authorization callback are required")

    try:
        with store.worker() as lease:
            claim = lease.claim()
            if claim is None:
                return None
            request_id = claim.get("request_id")
            owner = claim.get("owner")
            if (not isinstance(request_id, str) or _REQUEST_ID.fullmatch(request_id) is None
                    or not isinstance(owner, str)):
                raise RuntimeError("Build store returned an invalid private claim")
            job = parse_build_request(claim["request"])
            if job["request_id"] != request_id:
                raise RuntimeError("Build claim identity does not match its request")

            handle: ScratchHandle | None = None
            try:
                # Authorization is checked before allocating scratch, using only
                # the owner saved in the private durable claim.
                first_observation = _observed_tools(authorize, owner, job)
                if _cancel_requested(store, request_id, owner):
                    raise _KnownFailure

                with _private_root(inputs_root) as (input_path, input_fd), \
                     _private_root(scratch_root) as (scratch_path, _scratch_fd), \
                     _private_root(artifacts_root) as (artifact_path, artifact_fd), \
                     _private_root(logs_root) as (log_root, log_fd):
                    expected_mount = os.path.join(scratch_path, request_id, "fs")
                    handle = create_scratch(scratch_path, request_id,
                                            size_bytes=job["limits"]["scratch_bytes"])
                    if (handle.request_id != request_id or handle.mount_path != expected_mount
                            or handle.size_bytes != job["limits"]["scratch_bytes"]):
                        raise RuntimeError("Scratch adapter returned a mismatched handle")
                    _verify_fresh_scratch_root(handle)
                    if _cancel_requested(store, request_id, owner):
                        raise _KnownFailure

                    source_fd, source_path, source_info = _input_file(input_fd, input_path, request_id)
                    try:
                        destination = os.path.join(handle.mount_path, _SOURCE_NAME)
                        try:
                            checkout, _history = unpack_source_snapshot(
                                source_path, destination, expected_source=job["source"],
                            )
                        except ValueError:
                            raise _KnownFailure from None
                        _verify_input_unchanged(input_fd, source_fd, request_id + ".source", source_info)
                    finally:
                        os.close(source_fd)
                    if _cancel_requested(store, request_id, owner):
                        raise _KnownFailure

                    try:
                        manifest = _read_committed_manifest(checkout, job["source"]["manifest_sha256"])
                        validated = validate_manifest_request(manifest, job)
                        _check_request_policy(validated, manifest)
                    except ValueError:
                        raise _KnownFailure from None
                    _ready_toolchains(manifest, first_observation)
                    if _cancel_requested(store, request_id, owner):
                        raise _KnownFailure

                    _provision_layout(handle, validated, uid, gid)
                    if _cancel_requested(store, request_id, owner):
                        raise _KnownFailure
                    log_path = _create_log(log_fd, log_root, request_id)

                    # Re-read both policy and installed tools at the launch
                    # boundary; only these fresh observations enter the bundle.
                    second_observation = _observed_tools(authorize, owner, validated)
                    toolchains = _ready_toolchains(manifest, second_observation)
                    if _cancel_requested(store, request_id, owner):
                        raise _KnownFailure
                    result = run_systemd_build(
                        validated, uid=uid, gid=gid, scratch_path=handle.mount_path,
                        log_path=log_path,
                        cancelled=lambda: _cancel_requested(store, request_id, owner),
                    )
                    if not isinstance(result, BuildExecutionResult) or result.state not in {
                            "succeeded", "failed", "cancelled"}:
                        raise RuntimeError("Build execution outcome is unconfirmed")

                    if result.state == "succeeded":
                        if _cancel_requested(store, request_id, owner):
                            raise _KnownFailure
                        artifact_destination = os.path.join(artifact_path, request_id + ".bundle")
                        try:
                            artifact = create_artifact(
                                checkout, validated, artifact_destination, toolchains=toolchains,
                            )
                        except ValueError:
                            raise _KnownFailure from None
                        artifact = _verify_artifact(
                            artifact_fd, request_id, artifact,
                            validated["limits"]["artifact_bytes"] + _ARTIFACT_SIZE_OVERHEAD,
                        )
                        return _finish_success(lease, store, request_id, owner, handle, artifact)

                    if result.state == "cancelled" and not _cancel_requested(store, request_id, owner):
                        raise RuntimeError("Build adapter reported cancellation without a durable request")
                    raise _KnownFailure
            except _KnownFailure:
                return _finish_known_failure(lease, store, request_id, owner, handle)
    except BaseException:
        raise RuntimeError(_SAFE_FAILURE) from None


__all__ = ["run_next"]
