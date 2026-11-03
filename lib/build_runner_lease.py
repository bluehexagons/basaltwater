"""Local, exact-request delegation of a running Actions job's capacity lease.

This grants scheduling capacity only. Build admission still authenticates its
principal through the ordinary restricted SSH endpoint and current policy.
No workflow environment variable can select or bypass a coordinator lease.
"""

from __future__ import annotations

import array
from contextlib import contextmanager
import hmac
import os
from pathlib import Path
import pwd
import re
import secrets
import select
import socket
import struct

from lib.build_protocol import decode_document, encode_document, request_digest

MAX_MESSAGE = 2048
MAX_REQUESTS = 128
_ID = re.compile(r"[0-9a-f]{32}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_CGROUP = re.compile(r"0::(/system.slice/basaltwater-github-runner(?:-[a-f0-9]{16})?\.service)\n")


def _address(worker_pid: int) -> str:
    if type(worker_pid) is not int or not 1 < worker_pid < 2**31:
        raise ValueError("Invalid parent runner identity")
    return "\0basaltwater-runner-lease-v1-" + str(worker_pid)


def validate_proof(value: object) -> dict:
    if (not isinstance(value, dict)
            or set(value) != {"worker_pid", "request_id", "request_sha256", "token"}):
        raise ValueError("Invalid runner lease proof")
    _address(value["worker_pid"])
    for key, pattern in (("request_id", _ID), ("request_sha256", _DIGEST), ("token", _DIGEST)):
        if not isinstance(value[key], str) or pattern.fullmatch(value[key]) is None:
            raise ValueError("Invalid runner lease proof")
    return dict(value)


def _peer(connection: socket.socket) -> tuple[int, int, int]:
    return struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))


def _runner_group(pid: int) -> str:
    group = Path(f"/proc/{pid}/cgroup").read_text()
    if _CGROUP.fullmatch(group) is None:
        raise ValueError("Process is outside a managed runner service")
    return group


def _descends_from(pid: int, worker_pid: int) -> bool:
    group = _runner_group(worker_pid)
    for _ in range(128):
        if pid == worker_pid:
            return True
        if pid <= 1 or _runner_group(pid) != group:
            return False
        pid = int(Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()[1])
    return False


def _message(connection: socket.socket) -> dict:
    data, ancillary, flags, _address_value = connection.recvmsg(MAX_MESSAGE + 1, socket.CMSG_SPACE(16))
    # Messages from clients never carry descriptors. Close unexpected FDs even
    # on rejection; receiving SCM_RIGHTS itself installs them in this process.
    unexpected = []
    for level, kind, payload in ancillary:
        if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
            descriptors = array.array("i")
            descriptors.frombytes(payload[:len(payload) - len(payload) % descriptors.itemsize])
            unexpected.extend(descriptors)
    for descriptor in unexpected:
        os.close(descriptor)
    if unexpected or flags or not data or len(data) > MAX_MESSAGE:
        raise ValueError("Invalid runner lease message")
    value = decode_document(data, maximum=MAX_MESSAGE)
    if not isinstance(value, dict):
        raise ValueError("Invalid runner lease message")
    return value


def _connect(proof: dict) -> socket.socket:
    from lib.github_runner_config import ACCOUNT

    connection = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    try:
        connection.settimeout(2)
        connection.connect(_address(proof["worker_pid"]))
        pid, uid, _gid = _peer(connection)
        if uid != pwd.getpwnam(ACCOUNT).pw_uid or _runner_group(pid) != _runner_group(proof["worker_pid"]):
            raise ValueError("Untrusted runner lease guardian")
        return connection
    except BaseException:
        connection.close()
        raise


def submission_proof(host: str, request: dict) -> dict | None:
    """Discover real runner ancestry, then delegate only a same-host request."""
    from lib.github_runner_config import APP

    pid = os.getpid()
    worker = None
    for _ in range(128):
        if pid <= 1:
            return None
        try:
            executable = os.readlink(f"/proc/{pid}/exe")
            if re.fullmatch(re.escape(APP) + r"(?:/instances/[a-f0-9]{16})?/bin/Runner.Worker", executable):
                worker = pid
                break
            pid = int(Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            return None
    if worker is None:
        return None
    local = False
    for family, kind, protocol, _canonical, address in socket.getaddrinfo(host, 0, type=socket.SOCK_STREAM):
        with socket.socket(family, kind, protocol) as probe:
            try:
                probe.bind(address)
            except OSError:
                continue
            local = True
            break
    if not local:
        return None
    proof = {"worker_pid": worker, "request_id": request["request_id"],
             "request_sha256": request_digest(request), "token": "0" * 64}
    # A missing guardian on a same-host nested submission must fail before SSH,
    # rather than enqueue work that waits forever for the caller's own lease.
    with _connect(proof) as connection:
        connection.sendall(encode_document({"op": "register", "proof": proof}, maximum=MAX_MESSAGE))
        result = validate_proof(_message(connection))
    if any(result[key] != proof[key] for key in ("worker_pid", "request_id", "request_sha256")):
        raise ValueError("Runner lease proof changed its request identity")
    return result


def verify_proof(proof: object, request: dict) -> dict:
    checked = validate_proof(proof)
    if checked["request_id"] != request["request_id"] or checked["request_sha256"] != request_digest(request):
        raise ValueError("Runner lease does not match the submitted request")
    with _connect(checked) as connection:
        connection.sendall(encode_document({"op": "verify", "proof": checked}, maximum=MAX_MESSAGE))
        if _message(connection) != {"ok": True}:
            raise ValueError("Runner lease was refused")
    return checked


def _locked_descriptor(descriptor: int) -> None:
    """Require the actual locked open-file description, never reopen its path."""
    from lib.build_coordinator import open_slot

    canonical = open_slot()
    try:
        actual, expected = os.fstat(descriptor), os.fstat(canonical)
        if ((actual.st_dev, actual.st_ino, actual.st_uid, actual.st_gid, actual.st_mode, actual.st_nlink)
                != (expected.st_dev, expected.st_ino, expected.st_uid, expected.st_gid,
                    expected.st_mode, expected.st_nlink) or actual.st_size == 0):
            raise ValueError("Borrowed runner lease has an invalid identity")
        info = Path(f"/proc/self/fdinfo/{descriptor}").read_text()
        if re.search(r"^lock:\s+\d+: FLOCK\s+ADVISORY\s+WRITE\s", info, re.MULTILINE) is None:
            raise ValueError("Borrowed descriptor does not hold the capacity flock")
    finally:
        os.close(canonical)


class BorrowedLease:
    def __init__(self, connection: socket.socket, descriptor: int, parent: int):
        self.connection = connection
        self.descriptor = descriptor
        self.parent = parent

    def cancelled(self) -> bool:
        if not select.select([self.connection, self.parent], [], [], 0)[0]:
            return False
        # A parent cancellation notification and an unconfirmed socket loss
        # both stop nested work through its normal durable cancellation path.
        return True


@contextmanager
def borrow(proof: dict):
    """Keep the parent lease through confirmed cleanup; never clear its marker."""
    connection = _connect(validate_proof(proof))
    descriptor = -1
    parent = -1
    try:
        connection.sendall(encode_document({"op": "borrow", "proof": proof}, maximum=MAX_MESSAGE))
        data, ancillary, flags, _address_value = connection.recvmsg(MAX_MESSAGE, socket.CMSG_SPACE(16), socket.MSG_CMSG_CLOEXEC)
        received = []
        for level, kind, payload in ancillary:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                descriptors = array.array("i")
                descriptors.frombytes(payload[:len(payload) - len(payload) % descriptors.itemsize])
                received.extend(descriptors)
        if len(received) == 2:
            descriptor, parent = received
            received = []
        for extra in received:
            os.close(extra)
        if flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC) or descriptor < 0 or data != b"borrowed":
            raise ValueError("Runner lease handoff was refused")
        _locked_descriptor(descriptor)
        info = Path(f"/proc/self/fdinfo/{parent}").read_text()
        if re.search(r"^Pid:\s+(?:" + str(proof["worker_pid"]) + r"|-1)$", info, re.MULTILINE) is None:
            raise ValueError("Runner lease handoff has no matching parent pidfd")
        lease = BorrowedLease(connection, descriptor, parent)
        yield lease
        connection.sendall(b"stopped")
        # Acknowledgement closes the race between cleanup and guardian death.
        acknowledgement = connection.recv(32)
        if acknowledgement == b"cancel":
            acknowledgement = connection.recv(32)
        if acknowledgement != b"confirmed":
            raise RuntimeError("Runner lease cleanup acknowledgement is uncertain")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if parent >= 0:
            os.close(parent)
        connection.close()


class GuardianBroker:
    """One guardian delegates at most one exact nested build at a time."""

    def __init__(self, lock: int, worker: int, worker_pid: int):
        self.lock = lock
        self.worker = worker
        self.worker_pid = worker_pid
        self.requests: dict[str, dict] = {}
        self.borrower: socket.socket | None = None
        self.parent_exited = False
        self.uncertain = False
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        try:
            self.server.bind(_address(worker_pid))
            self.server.listen(8)
        except BaseException:
            self.server.close()
            raise

    def close(self) -> None:
        if self.borrower is not None:
            self.borrower.close()
        self.server.close()

    def parent_stopped(self) -> None:
        self.parent_exited = True
        if self.borrower is not None:
            self.borrower.sendall(b"cancel")

    def accept(self) -> None:
        connection, _address_value = self.server.accept()
        connection.settimeout(.2)
        try:
            pid, uid, _gid = _peer(connection)
            if uid not in {0, os.geteuid()}:
                raise ValueError("Runner lease caller has no local authority")
            with os.fdopen(os.pidfd_open(pid), "rb", closefd=True):
                message = _message(connection)
                if set(message) != {"op", "proof"} or self.parent_exited:
                    raise ValueError("Runner lease request was refused")
                proof = validate_proof(message["proof"])
                if proof["worker_pid"] != self.worker_pid:
                    raise ValueError("Runner lease parent mismatch")
                if message["op"] == "register":
                    if uid != os.geteuid() or not _descends_from(pid, self.worker_pid):
                        raise ValueError("Runner lease caller is outside this job")
                    previous = self.requests.get(proof["request_id"])
                    if previous is not None:
                        if previous["request_sha256"] != proof["request_sha256"]:
                            raise ValueError("Runner lease request identity changed")
                        proof = previous
                    else:
                        if len(self.requests) >= MAX_REQUESTS:
                            raise ValueError("Runner lease request capacity exhausted")
                        proof["token"] = secrets.token_hex(32)
                        self.requests[proof["request_id"]] = proof
                    connection.sendall(encode_document(proof, maximum=MAX_MESSAGE))
                else:
                    previous = self.requests.get(proof["request_id"])
                    if (uid != 0 or previous is None
                            or any(previous[key] != proof[key] for key in ("worker_pid", "request_id", "request_sha256"))
                            or not hmac.compare_digest(previous["token"], proof["token"])):
                        raise ValueError("Runner lease delegation was refused")
                    if message["op"] == "verify":
                        connection.sendall(b'{"ok":true}')
                    elif message["op"] == "borrow" and self.borrower is None and not self.uncertain:
                        connection.sendmsg([b"borrowed"], [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
                                                            array.array("i", [self.lock, self.worker]))])
                        self.borrower = connection
                        connection = None
                    else:
                        raise ValueError("Runner lease is unavailable")
        except (OSError, ValueError, KeyError, IndexError):
            pass  # Reject malformed/untrusted requests without releasing capacity.
        finally:
            if connection is not None:
                connection.close()

    def finish_borrow(self) -> None:
        if self.borrower is None:
            return
        try:
            if self.borrower.recv(32) != b"stopped":
                raise ValueError("Nested cleanup is unconfirmed")
            self.borrower.sendall(b"confirmed")
        except (OSError, ValueError):
            self.uncertain = True
        finally:
            self.borrower.close()
            self.borrower = None
