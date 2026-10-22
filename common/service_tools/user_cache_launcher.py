#!/usr/bin/env python3
"""Observe protected account executables while maintenance runs without privilege.

This entrypoint imports only the standard library before permanently demoting
the worker. The privileged parent accepts one fixed request on an inherited
socket; it never reads account configuration or invokes account tools.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import pwd
import select
import socket
import stat
import subprocess
import sys
import time


MAX_RESPONSE_BYTES = 1024 * 1024
MAX_PROCESSES = 100000
SCAN_SECONDS = 10


def _trusted_path(path: Path) -> None:
    """Require root-controlled code and ancestors, allowing owned sticky roots."""
    for component in (*reversed(path.parents), path):
        info = component.lstat()
        if info.st_uid != 0 or stat.S_ISLNK(info.st_mode):
            raise ValueError("maintenance launcher code must be controlled by root")
        shared = info.st_mode & 0o022
        if shared and not (stat.S_ISDIR(info.st_mode) and info.st_mode & stat.S_ISVTX):
            raise ValueError("maintenance launcher code must not be publicly writable")
        if component != path and not stat.S_ISDIR(info.st_mode):
            raise ValueError("invalid maintenance launcher ancestor")
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022:
        raise ValueError("invalid maintenance launcher file")


def _account(username: str, root: Path):
    """Load the standalone username validator without importing the lib package."""
    validators = root / "lib/validators.py"
    _trusted_path(validators)
    # Compile the protected source directly: do not import the lib package or
    # trust an independently writable __pycache__ during privileged startup.
    namespace: dict = {"__name__": "_cleanup_validators"}
    source = validators.read_bytes()
    if len(source) > 65536:
        raise ValueError("username validator exceeds its size limit")
    exec(compile(source, str(validators), "exec"), namespace)
    if not namespace["validate_username"](username):
        raise ValueError("invalid maintenance account")
    account = pwd.getpwnam(username)
    if account.pw_uid == 0:
        raise ValueError("maintenance requires a non-root account")
    home = account.pw_dir
    if not os.path.isabs(home) or os.path.normpath(home) != home or any(ord(c) < 32 for c in home):
        raise ValueError("invalid maintenance home")
    info = os.lstat(home)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != account.pw_uid:
        raise ValueError("maintenance requires the account's own home directory")
    return account


def _status(descriptor: int) -> tuple[tuple[int, ...], str]:
    """Read bounded kernel identity/state metadata through the pinned PID directory."""
    file = os.open("status", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
    try:
        content = bytearray()
        while True:
            block = os.read(file, 16385 - len(content))
            if not block:
                break
            content.extend(block)
            if len(content) > 16384:
                raise OSError("process status limit exceeded")
    finally:
        os.close(file)
    fields = dict(line.split(b":", 1) for line in bytes(content).splitlines() if b":" in line)
    identities = tuple(int(value) for value in fields[b"Uid"].split())
    state = fields[b"State"].split()[0].decode("ascii")
    if len(identities) != 4 or any(value < 0 for value in identities) or len(state) != 1 or state not in "RSDZTtXxKWPI":
        raise ValueError("invalid process identity")
    return identities, state


def _executables(uid: int) -> list[str]:
    """Inspect only real /proc, returning raw executable links without resolving paths."""
    targets: set[str] = set()
    deadline = time.monotonic() + SCAN_SECONDS
    root = os.open("/proc", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        with os.scandir(root) as entries:
            for count, entry in enumerate(entries, 1):
                if count > MAX_PROCESSES or time.monotonic() >= deadline:
                    raise OSError("process inventory limit exceeded")
                if not entry.name.isascii() or not entry.name.isdigit():
                    continue
                descriptor = -1
                try:
                    descriptor = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
                    before, state = _status(descriptor)
                    if uid not in before or state in {"Z", "X", "x"}:
                        continue
                    try:
                        target = os.readlink("exe", dir_fd=descriptor)
                    except FileNotFoundError:
                        _ids, latest_state = _status(descriptor)
                        if latest_state in {"Z", "X", "x"}:
                            continue
                        raise OSError("remaining account process has no verifiable executable") from None
                    after, _state = _status(descriptor)
                    if before != after:
                        raise OSError("process account changed during observation")
                    current = os.stat(entry.name, dir_fd=root, follow_symlinks=False)
                    pinned = os.fstat(descriptor)
                    if (current.st_dev, current.st_ino) != (pinned.st_dev, pinned.st_ino):
                        raise OSError("process identity changed during observation")
                    if not target.startswith("/") or "\0" in target:
                        raise ValueError("invalid executable link")
                    targets.add(target)
                except (FileNotFoundError, ProcessLookupError):
                    # A pinned proc directory never starts referring to a reused
                    # PID. Only ignore disappearance when its pathname is gone.
                    try:
                        os.stat(entry.name, dir_fd=root, follow_symlinks=False)
                    except (FileNotFoundError, ProcessLookupError):
                        continue
                    raise OSError("remaining process cannot be inspected") from None
                finally:
                    if descriptor >= 0:
                        os.close(descriptor)
    finally:
        os.close(root)
    return sorted(targets)


def _serve(channel: socket.socket, worker: subprocess.Popen, uid: int) -> None:
    """Bound requests and responses; no caller-selected paths, PIDs, or commands."""
    channel.settimeout(15)
    while worker.poll() is None:
        if not select.select([channel], [], [], 1)[0]:
            continue
        request = bytearray()
        while not request.endswith(b"\n"):
            part = channel.recv(9 - len(request))
            if not part:
                return
            request.extend(part)
            if len(request) > 8:
                raise ValueError("invalid process observer request")
        if request != b"inspect\n":
            raise ValueError("invalid process observer request")
        try:
            response = json.dumps({"executables": _executables(uid)}, ensure_ascii=True).encode() + b"\n"
            if len(response) > MAX_RESPONSE_BYTES:
                raise ValueError("process observation limit exceeded")
        except (OSError, ValueError, KeyError, UnicodeError):
            response = b'{"error":"process inventory unavailable"}\n'
        channel.sendall(response)


def launch(account, root: Path, *, dry_run: bool, agent_storage_only: bool) -> int:
    """Keep privilege in the observer; setpriv irreversibly demotes the worker."""
    parent, child = socket.socketpair()
    worker = None
    try:
        command = [
            "/usr/bin/setpriv", f"--reuid={account.pw_uid}", f"--regid={account.pw_gid}",
            "--clear-groups", "--inh-caps=-all", "--ambient-caps=-all", "--bounding-set=-all",
            "--no-new-privs", "--", "/usr/bin/python3", "-I",
            str(root / "common/service_tools/user_cache_maintenance.py"),
            "--process-observer-fd", str(child.fileno()),
        ]
        if dry_run:
            command.append("--dry-run")
        if agent_storage_only:
            command.append("--agent-storage-only")
        environment = {
            "HOME": account.pw_dir, "USER": account.pw_name, "LOGNAME": account.pw_name,
            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8",
        }
        worker = subprocess.Popen(command, env=environment, cwd=account.pw_dir,
                                  pass_fds=(child.fileno(),), close_fds=True, stdin=subprocess.DEVNULL)
        child.close()
        _serve(parent, worker, account.pw_uid)
        return worker.wait(timeout=5)
    finally:
        parent.close()
        child.close()
        if worker is not None and worker.poll() is None:
            worker.terminate()
            try:
                worker.wait(timeout=5)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run account cleanup with a read-only privileged process observer")
    parser.add_argument("--user", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--agent-storage-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        if os.geteuid() != 0 or not sys.flags.isolated:
            raise ValueError("use the managed root launcher with isolated Python (-I)")
        script = Path(os.path.abspath(__file__))
        _trusted_path(script)
        root = script.parents[2]
        account = _account(args.user, root)
        return launch(account, root, dry_run=args.dry_run, agent_storage_only=args.agent_storage_only)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(f"Maintenance launcher failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
