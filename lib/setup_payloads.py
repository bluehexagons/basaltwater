"""Private, expiring setup payloads kept outside the installed source tree."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import math
import os
from pathlib import PurePosixPath
import shutil
import signal
import stat
import sys
import tarfile
import tempfile
import time

from lib.atomic_io import read_json_file, write_json_atomic
from lib.remote_utils import run
from lib.validation import validate_filesystem_path, validate_positive_integer
from lib.setup_prompts import PROMPT_RELAY_ENV


PAYLOAD_ROOT = "/run/basaltwater-setup"
PAYLOAD_NAMES = ("agent_payload", "device_pairing_payload", "web_panel_payload", ".remote_setup_args.json")
MAX_PAYLOAD_BYTES = 64 * 1024 * 1024


def _private_directory(path: str) -> None:
    validate_filesystem_path(path, must_exist=False)
    os.makedirs(path, mode=0o700, exist_ok=True)
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError(f"Unsafe setup payload directory: {path}")


def scrub_expired_payloads() -> None:
    """Scrub expired abandoned leases without touching an active setup."""
    _private_directory(PAYLOAD_ROOT)
    for name in os.listdir(PAYLOAD_ROOT):
        if not name.startswith("payload-"):
            continue
        path = os.path.join(PAYLOAD_ROOT, name)
        _private_directory(path)
        descriptor = os.open(os.path.join(path, "lease.lock"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
                raise RuntimeError("Unsafe setup payload lease")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                continue
            try:
                record = read_json_file(os.path.join(path, "lease.json"))
            except FileNotFoundError:
                # Allow a concurrent creator time to publish its lease.
                if os.stat(path).st_mtime < time.time() - 60:
                    shutil.rmtree(path)
                continue
            if (
                not isinstance(record, dict) or type(record.get("version")) is not int or record.get("version") != 1
                or record.get("uid") != os.getuid()
                or type(record.get("expires_at")) not in (int, float)
                or not math.isfinite(record["expires_at"]) or record["expires_at"] <= 0
            ):
                raise RuntimeError(f"Invalid setup payload lease: {path}")
            if record["expires_at"] <= time.time():
                shutil.rmtree(path)
        finally:
            os.close(descriptor)


@contextmanager
def payload_workspace(timeout: int):
    scrub_expired_payloads()
    path = tempfile.mkdtemp(prefix="payload-", dir=PAYLOAD_ROOT)
    descriptor = os.open(os.path.join(path, "lease.lock"), os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        write_json_atomic(os.path.join(path, "lease.json"), {
            "version": 1, "uid": os.getuid(), "pid": os.getpid(),
            "expires_at": time.time() + timeout + 60,
        }, mode=0o600)
        yield path
    finally:
        try:
            shutil.rmtree(path)
        finally:
            os.close(descriptor)


def move_payloads(source: str, destination: str) -> None:
    for name in PAYLOAD_NAMES:
        path = os.path.join(source, name)
        if os.path.lexists(path):
            shutil.move(path, os.path.join(destination, name))


def link_payloads(source: str, runtime: str) -> None:
    for name in PAYLOAD_NAMES:
        path = os.path.join(source, name)
        if os.path.exists(path):
            os.symlink(path, os.path.join(runtime, name))


def receive_payloads(stream, destination: str) -> None:
    total = 0
    with tarfile.open(fileobj=stream, mode="r|gz") as archive:
        for index, member in enumerate(archive):
            parts = PurePosixPath(member.name).parts
            if (
                not parts or parts[0] not in PAYLOAD_NAMES or ".." in parts
                or member.name.startswith("/") or not (member.isdir() or member.isfile())
                or index >= 10000
            ):
                raise ValueError("Invalid setup payload archive entry")
            total += member.size
            if total > MAX_PAYLOAD_BYTES:
                raise ValueError("Setup payload exceeds 64 MiB")
            path = os.path.join(destination, *parts)
            os.makedirs(path if member.isdir() else os.path.dirname(path), mode=0o700, exist_ok=True)
            if member.isfile():
                with archive.extractfile(member) as source, open(path, "xb") as target:
                    os.chmod(path, 0o600)
                    shutil.copyfileobj(source, target)


class _BoundedPayloadReader:
    """Expose archive EOF without closing the connection needed for prompts."""

    def __init__(self, stream, size: int):
        self.stream = stream
        self.remaining = size

    def read(self, size: int = -1) -> bytes:
        size = self.remaining if size < 0 else min(size, self.remaining)
        if not size:
            return b""
        data = self.stream.read(size)
        if not data:
            raise ValueError("Truncated setup payload archive")
        self.remaining -= len(data)
        return data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=lambda value: validate_positive_integer(value, "timeout"), required=True)
    parser.add_argument("--payload-bytes", type=lambda value: validate_positive_integer(value, "payload bytes"))
    args = parser.parse_args()
    if args.payload_bytes is not None and args.payload_bytes > MAX_PAYLOAD_BYTES + 1024 * 1024:
        raise ValueError("Compressed setup payload exceeds its size limit")
    relay = os.environ.get(PROMPT_RELAY_ENV) == "1"
    if relay and args.payload_bytes is None:
        raise ValueError("Interactive setup requires a framed payload length")
    with payload_workspace(args.timeout) as payload:
        stream = _BoundedPayloadReader(sys.stdin.buffer, args.payload_bytes) if args.payload_bytes else sys.stdin.buffer
        receive_payloads(stream, payload)
        if args.payload_bytes:
            while stream.remaining:
                stream.read(min(65536, stream.remaining))
        runtime = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        link_payloads(payload, runtime)
        try:
            return run([
                sys.executable, "-u", os.path.join(runtime, "remote_setup.py"),
                "--args-file", os.path.join(payload, ".remote_setup_args.json"),
            ], check=False, timeout=args.timeout, interactive=relay).returncode
        finally:
            for name in PAYLOAD_NAMES:
                path = os.path.join(runtime, name)
                if os.path.islink(path):
                    os.unlink(path)


if __name__ == "__main__":
    def terminate_setup(_signum, _frame):
        raise KeyboardInterrupt("Setup terminated")
    signal.signal(signal.SIGTERM, terminate_setup)
    raise SystemExit(main())
