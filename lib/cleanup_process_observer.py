"""Private client for the maintenance launcher's read-only process observer."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
import socket
import struct


MAX_RESPONSE_BYTES = 1024 * 1024
_observer: ContextVar[ProcessObserver | None] = ContextVar("cleanup_process_observer", default=None)


class ProcessObserver:
    """Consume fresh executable observations from the root parent, never a file."""

    def __init__(self, descriptor: int) -> None:
        if descriptor < 3:
            raise OSError("invalid process observer descriptor")
        self.failed = False
        self.socket = socket.socket(fileno=descriptor)
        try:
            self.socket.set_inheritable(False)
            if self.socket.family != socket.AF_UNIX or self.socket.type != socket.SOCK_STREAM:
                raise OSError("invalid process observer transport")
            pid, uid, _gid = struct.unpack("3i", self.socket.getsockopt(
                socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"),
            ))
            if uid != 0 or pid != os.getppid() or os.getuid() == 0 or os.getuid() != os.geteuid():
                raise OSError("process observer must be the privileged launcher parent")
            self.socket.settimeout(15)
        except BaseException:
            self.socket.close()
            raise

    def inspect(self) -> tuple[str, ...]:
        """Request a complete scan; malformed, oversized, or lost replies fail closed."""
        if self.failed:
            raise OSError("privileged process observation unavailable")
        try:
            self.socket.sendall(b"inspect\n")
            chunks = bytearray()
            while not chunks.endswith(b"\n"):
                part = self.socket.recv(min(65536, MAX_RESPONSE_BYTES + 1 - len(chunks)))
                if not part:
                    raise OSError("process observer disconnected")
                chunks.extend(part)
                if len(chunks) > MAX_RESPONSE_BYTES:
                    raise OSError("process observer response exceeded its limit")
            response = json.loads(chunks)
            if not isinstance(response, dict) or set(response) != {"executables"}:
                raise ValueError("incomplete process observation")
            paths = response["executables"]
            if not isinstance(paths, list) or any(
                not isinstance(path, str) or not path.startswith("/") or "\0" in path
                for path in paths
            ):
                raise ValueError("invalid process observation")
            return tuple(paths)
        except (OSError, ValueError, RecursionError) as exc:
            # A delayed reply cannot establish freshness for another request.
            # Keep this failed client installed, so later policies also defer.
            self.failed = True
            self.socket.close()
            # Do not expose privileged reply bodies or process paths in logs.
            raise OSError("privileged process observation unavailable") from exc


@contextmanager
def use_process_observer(descriptor: int | None):
    """Scope the inherited channel to this maintenance run and close it afterward."""
    if descriptor is None:
        yield
        return
    observer = ProcessObserver(descriptor)
    token = _observer.set(observer)
    try:
        yield
    finally:
        _observer.reset(token)
        observer.socket.close()


def observed_executables(uid: int) -> tuple[str, ...] | None:
    """Return a fresh observation, or None when no launcher channel was installed."""
    observer = _observer.get()
    if observer is None:
        return None
    if uid != os.getuid():
        raise OSError("process observer account mismatch")
    return observer.inspect()
