"""Bounded Go build-cache cleanup with a narrow completed-run exemption."""

from __future__ import annotations

import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import subprocess
import time
from typing import Callable

from lib.agent_storage_inventory import inspect_storage
from lib.streamed_process import run_captured
from lib.validation import validate_filesystem_path


_GO_NAMES = re.compile(r"^(?:go(?:1\.[\w.-]+)?|compile|link|asm|cgo|gopls)(?:[- ].*)?$")
_TEMP_PROGRAM = re.compile(r"^/(?:tmp|var/tmp)/go-build[0-9]+/b[0-9]+/exe/[^/]+$")


class _Unknown(Exception):
    pass


def _read(path: str, limit: int = 65536) -> bytes:
    with open(path, "rb") as stream:
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise _Unknown("process_metadata_limit")
    return value


def _arguments(directory: str) -> list[str]:
    data = _read(os.path.join(directory, "cmdline"))
    if not data or not data.endswith(b"\0"):
        raise _Unknown("process_arguments_unavailable")
    return [value.decode("utf-8", errors="strict") for value in data[:-1].split(b"\0")]


def _process(directory: str) -> tuple[int, int, str]:
    fields = _read(os.path.join(directory, "stat")).decode("utf-8").rsplit(") ", 1)[1].split()
    return int(fields[1]), int(fields[19]), fields[0]


def _executable(directory: str, *, process_root: bool = False) -> tuple[str, tuple[int, int]]:
    target = os.readlink(os.path.join(directory, "exe"))
    validate_filesystem_path(target)
    if not os.path.isabs(target) or os.path.normpath(target) != target:
        raise _Unknown("unrecognized_executable")
    opened = os.stat(os.path.join(directory, "exe"))
    named_path = os.path.join(directory, "root", target.lstrip("/")) if process_root else target
    named = os.stat(named_path, follow_symlinks=False)
    identity = (opened.st_dev, opened.st_ino)
    if not stat.S_ISREG(named.st_mode) or identity != (named.st_dev, named.st_ino):
        raise _Unknown("executable_changed")
    return target, identity


def _installed_go(executable: str, uid: int) -> bool:
    """Recognize an owned Go installation, not an arbitrary process named go."""
    root = Path(executable).parent.parent
    info = os.stat(executable, follow_symlinks=False)
    if Path(executable).name != "go" or Path(executable).parent.name != "bin":
        return False
    if info.st_uid not in (0, uid) or info.st_mode & 0o022 or not info.st_mode & 0o111:
        return False
    version = _read(str(root / "VERSION"), 4096)
    with open(executable, "rb") as stream:
        magic = stream.read(4)
    return magic == b"\x7fELF" and re.match(rb"go1\.[0-9]+", version) is not None


def _waiting_run(directory: str, uid: int, deadline: float, proc_root: str) -> bool:
    original = _process(directory)
    executable = _executable(directory)
    if not _installed_go(executable[0], uid):
        return False
    args = _arguments(directory)
    sources = args[2:]
    # This deliberately excludes build flags and application arguments. Hook
    # invocations include at least the tool/program pathname as an argument.
    if len(args) < 3 or args[1] != "run" or any(not arg or arg.startswith("-") for arg in sources):
        return False
    if len(sources) != 1 and not all(source.endswith(".go") for source in sources):
        return False
    children: set[int] = set()
    with os.scandir(os.path.join(directory, "task")) as threads:
        for index, thread in enumerate(threads):
            if index >= 256 or time.monotonic() >= deadline:
                raise _Unknown("thread_inventory_limit")
            if not thread.name.isdigit():
                continue
            values = _read(os.path.join(thread.path, "children")).split()
            if len(values) > 256:
                raise _Unknown("child_inventory_limit")
            children.update(int(value) for value in values)
    if len(children) != 1:
        return False
    child = os.path.join(proc_root, str(next(iter(children))))
    child_state = _process(child)
    if child_state[0] != int(os.path.basename(directory)) or child_state[2] in ("Z", "X"):
        return False
    if os.stat(child, follow_symlinks=False).st_uid != uid:
        return False
    program, identity = _executable(child, process_root=True)
    if not _TEMP_PROGRAM.fullmatch(program) or _arguments(child) != [program]:
        return False
    # A managed worker's PrivateTmp hides the login session's temporary files.
    # Verify this one owned child's executable through its own proc root; do
    # not broaden the worker mount namespace or inspect unrelated processes.
    program_path = os.path.join(child, "root", program.lstrip("/"))
    with open(program_path, "rb") as stream:
        if stream.read(4) != b"\x7fELF":
            return False
    # Check the entire temporary program subtree without following links.
    current = Path(program_path)
    for _ in range(4):
        info = os.stat(current, follow_symlinks=False)
        if info.st_uid != uid or stat.S_ISLNK(info.st_mode) or info.st_mode & 0o022:
            return False
        current = current.parent
    temporary = os.stat(current, follow_symlinks=False)
    if not stat.S_ISDIR(temporary.st_mode) or stat.S_ISLNK(temporary.st_mode):
        return False
    if temporary.st_uid not in (0, uid) or (temporary.st_mode & 0o022 and not temporary.st_mode & stat.S_ISVTX):
        return False
    final_parent, final_child = _process(directory), _process(child)
    return (original[:2] == final_parent[:2] and executable == _executable(directory)
            and child_state[:2] == final_child[:2] and final_child[2] not in ("Z", "X")
            and (program, identity) == _executable(child, process_root=True))


def go_build_activity(*, proc_root: str = "/proc") -> dict:
    """Classify normal current-user Go tools; never return argv or environments.

    Ordinary Go tools retain their standard process names. Deliberately renamed
    tools or hooks that rewrite their identities are outside this observation
    boundary. This snapshot cannot prevent a new build starting afterwards.
    """
    result = {"state": "eligible", "blocking_pids": [], "waiting_run_pids": [], "reason": None}
    deadline = time.monotonic() + 3
    try:
        uid = os.geteuid()
        with os.scandir(proc_root) as processes:
            for index, entry in enumerate(processes):
                if index >= 16384 or time.monotonic() >= deadline:
                    raise _Unknown("process_inventory_limit")
                if not entry.name.isdigit():
                    continue
                try:
                    if entry.stat(follow_symlinks=False).st_uid != uid:
                        continue
                    name = _read(os.path.join(entry.path, "comm"), 64).decode("utf-8").strip()
                    if not _GO_NAMES.fullmatch(name):
                        continue
                    if _process(entry.path)[2] in ("Z", "X"):
                        continue
                    if name == "go" and _waiting_run(entry.path, uid, deadline, proc_root):
                        result["waiting_run_pids"].append(int(entry.name))
                    else:
                        result["blocking_pids"].append(int(entry.name))
                except FileNotFoundError:
                    # A vanished candidate invalidates the observation rather
                    # than being mistaken for a verified waiting wrapper.
                    raise _Unknown("process_changed_during_inspection")
        if time.monotonic() >= deadline:
            raise _Unknown("process_inventory_limit")
        if result["blocking_pids"]:
            result.update(state="deferred_active", reason="active_go_build_or_unverified_command")
    except (OSError, ValueError, IndexError, UnicodeError, _Unknown):
        result.update(state="deferred_unknown", reason="process_inventory_incomplete")
    result["blocking_pids"].sort()
    result["waiting_run_pids"].sort()
    return result


def _cache_identity(path: str, home: str) -> tuple[int, int]:
    validate_filesystem_path(path)
    if not os.path.isabs(path) or os.path.normpath(path) != path or path == home:
        raise ValueError("Go cache must be an absolute normalized directory below the current home")
    if os.path.commonpath((home, path)) != home or os.path.realpath(path) != path:
        raise ValueError("Go cache must be below the current home without symbolic links")
    info = os.stat(path, follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise ValueError("Go cache must be a private directory owned by the current account")
    return info.st_dev, info.st_ino


def _measure(home: str, path: str) -> dict:
    report = inspect_storage(home=home, paths=[path], timeout=10)
    if report["issues"] or not report["candidates"]:
        raise ValueError("Go cache inventory unavailable")
    row = report["candidates"][0]
    if row["status"] != "measured" or row["issues"] or row["other_owner_entries"]:
        raise ValueError("Go cache inventory is incomplete or contains unsupported entries")
    return {key: row[key] for key in ("allocated_bytes", "apparent_bytes", "files")}


def clean_go_build_cache(*, apply: bool = False,
                         expected_home: str | None = None,
                         search_path: str | None = None,
                         needs_cleanup: Callable[[str, int], bool] | None = None) -> dict:
    """Preview or execute exactly native `go clean -cache` for this account."""
    result = {"status": "failed", "read_only": not apply, "path": None, "activity": None,
              "before": None, "after": None, "free_bytes_before": None, "free_bytes_after": None,
              "cleanup_executed": False,
              "errors": [], "notes": ["A fresh process snapshot cannot exclude builds starting afterwards.",
                  "Module downloads, toolchains and temporary running programs are preserved."]}
    phase = "account validation"
    try:
        if os.getuid() != os.geteuid() or os.geteuid() == 0:
            raise ValueError("Run Go cache cleanup directly as the non-root cache owner")
        home = pwd.getpwuid(os.geteuid()).pw_dir
        validate_filesystem_path(home, must_exist=True)
        if expected_home is not None and expected_home != home:
            raise ValueError("Maintenance home does not match the current account")
        environment = os.environ.copy()
        environment.update(HOME=home, GOFLAGS="", GOTOOLCHAIN="local")
        if search_path is not None:
            environment["PATH"] = search_path
        phase = "Go installation validation"
        executable = shutil.which("go", path=environment.get("PATH"))
        if not executable:
            result["status"] = "unavailable"
            return result
        executable = os.path.realpath(executable)
        if not _installed_go(executable, os.geteuid()):
            raise ValueError("Selected executable is not a recognized Go installation")
        executable_info = os.stat(executable)
        phase = "Go cache path query"
        query = run_captured([executable, "env", "GOCACHE"], cwd=home, env=environment, timeout=30, max_bytes=4096)
        if query.returncode or len(query.stdout.strip().splitlines()) != 1:
            raise ValueError("Go cache path query failed")
        path = query.stdout.strip()
        result["path"] = path
        phase = "Go cache validation"
        try:
            cache_identity = _cache_identity(path, home)
        except FileNotFoundError:
            result["status"] = "absent"
            return result
        phase = "Go cache inventory"
        result["before"] = _measure(home, path)
        if needs_cleanup is not None and not needs_cleanup(path, result["before"]["apparent_bytes"]):
            result["status"] = "below_limits"
            return result
        result["free_bytes_before"] = shutil.disk_usage(path).free
        # Pin the cache and disable configuration/hooks/toolchain switching for
        # the cleanup command. Do all expensive measurement before this guard.
        environment.update(GOCACHE=path, GOENV="off", GOFLAGS="", GOCACHEPROG="", GOTOOLCHAIN="local")
        result["activity"] = go_build_activity()
        if result["activity"]["state"] != "eligible":
            result["status"] = result["activity"]["state"]
            return result
        phase = "Go cache and executable revalidation"
        if cache_identity != _cache_identity(path, home):
            raise ValueError("Go cache changed before cleanup")
        current = os.stat(executable)
        if (executable_info.st_dev, executable_info.st_ino) != (current.st_dev, current.st_ino):
            raise ValueError("Go executable changed before cleanup")
        if not apply:
            result["status"] = "preview"
            return result
        phase = "native Go cache cleanup"
        cleaned = run_captured([executable, "clean", "-cache"], cwd=home, env=environment,
                               timeout=120, max_bytes=16384)
        if cleaned.returncode:
            raise ValueError(f"Go cache cleanup failed (exit {cleaned.returncode})")
        result["cleanup_executed"] = True
        result["status"] = "cleaned"
        phase = "Go cache cleanup verification"
        result["after"] = _measure(home, path)
        result["free_bytes_after"] = shutil.disk_usage(path).free
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        result["status"] = "failed"
        # Native output and environments can contain private configuration.
        result["errors"].append(f"{phase} failed")
    return result
