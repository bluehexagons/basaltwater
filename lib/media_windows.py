"""Native Windows-only containment for optional Python file media inspection.

No desktop, POSIX controller, installation or shell invocation dependencies.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
import ctypes as c
from ctypes import wintypes as w
import json
import msvcrt
import os
from pathlib import Path
import re
import stat
import subprocess
import threading
import tempfile
import time

k = c.WinDLL("kernel32", use_last_error=True)
a = c.WinDLL("advapi32", use_last_error=True)


def api(dll, name, result, *args):
    fn = getattr(dll, name)
    fn.restype, fn.argtypes = result, args
    return fn


close = api(k, "CloseHandle", w.BOOL, w.HANDLE)
create_file = api(k, "CreateFileW", w.HANDLE, w.LPCWSTR, w.DWORD, w.DWORD,
                  c.c_void_p, w.DWORD, w.DWORD, w.HANDLE)
attributes = api(k, "GetFileAttributesW", w.DWORD, w.LPCWSTR)
drive_type = api(k, "GetDriveTypeW", w.UINT, w.LPCWSTR)


def checked(value):
    if not value or value == c.c_void_p(-1).value:
        raise c.WinError(c.get_last_error())
    return value


def validate_path(path: str, *, must_exist: bool = False) -> None:
    """Windows path validator: local fixed drive, no devices, ADS or reparses."""
    if (not re.match(r"\A[A-Za-z]:[\\/]", path) or ":" in path[2:]
            or re.search(r'[<>"|?*\x00-\x1f]', path)
            or any(part.endswith((".", " ")) or part in (".", "..")
                   or re.match(r"(?i)^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)", part)
                   for part in re.split(r"[\\/]", path[3:]) if part)):
        raise ValueError("Media paths require absolute local drive paths without streams or devices")
    full = Path(path)
    if drive_type(full.anchor) != 3:
        raise ValueError("Media requires a local fixed drive")
    for candidate in (full, *full.parents):
        flags = attributes(str(candidate))
        if flags == 0xffffffff:
            if candidate == full and must_exist:
                raise FileNotFoundError(path)
            continue
        if flags & 0x400:
            raise ValueError("Reparse points are not accepted in media paths")


@contextmanager
def pinned(path: Path, *, file: bool = False):
    """Hold ancestors against replacement and the input against writes/deletion."""
    validate_path(str(path), must_exist=True)
    with ExitStack() as stack:
        handles = []
        for candidate in reversed((path, *path.parents)):
            handle = checked(create_file(str(candidate), 0x80000000 if file and candidate == path else 0,
                                         1 if file and candidate == path else 3, None, 3,
                                         0x02200000, None))
            stack.callback(close, handle)
            handles.append(handle)
        validate_path(str(path), must_exist=True)
        yield handles[-1]


def snapshot(source: str, max_bytes: int) -> bytes:
    path = Path(source)
    with pinned(path, file=True) as handle:
        # Reject alternate streams even when the requested name has no colon.
        class Stream(c.Structure):
            _fields_ = [("size", c.c_longlong), ("name", w.WCHAR * 296)]
        first = api(k, "FindFirstStreamW", w.HANDLE, w.LPCWSTR, c.c_int, c.POINTER(Stream), w.DWORD)
        next_stream = api(k, "FindNextStreamW", w.BOOL, w.HANDLE, c.POINTER(Stream))
        item = Stream()
        search = checked(first(source, 0, c.byref(item), 0))
        try:
            while True:
                if item.name != "::$DATA":
                    raise ValueError("Alternate input streams are not supported")
                if not next_stream(search, c.byref(item)):
                    if c.get_last_error() != 38:
                        raise c.WinError(c.get_last_error())
                    break
        finally:
            api(k, "FindClose", w.BOOL, w.HANDLE)(search)
        duplicate = w.HANDLE()
        current = api(k, "GetCurrentProcess", w.HANDLE)()
        checked(api(k, "DuplicateHandle", w.BOOL, w.HANDLE, w.HANDLE, w.HANDLE,
                    c.POINTER(w.HANDLE), w.DWORD, w.BOOL, w.DWORD)(
                        current, handle, current, c.byref(duplicate), 0, False, 2))
        fd = msvcrt.open_osfhandle(duplicate.value, os.O_RDONLY | os.O_BINARY)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not 0 < before.st_size <= max_bytes or not stat.S_ISREG(before.st_mode):
                raise ValueError("Input must be a nonempty bounded regular file")
            data = stream.read(max_bytes + 1)
            after = os.fstat(stream.fileno())
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if identity(before) != identity(after) or len(data) != before.st_size:
            raise ValueError("Input changed during snapshot")
        return data


class Security(c.Structure):
    _fields_ = [("length", w.DWORD), ("descriptor", c.c_void_p), ("inherit", w.BOOL)]


@contextmanager
def owner_security():
    # Owner Rights grants only the object's owner; administrators/System retain
    # recovery access. Protected DACL prevents broad parent ACL inheritance.
    descriptor = c.c_void_p()
    checked(api(a, "ConvertStringSecurityDescriptorToSecurityDescriptorW", w.BOOL,
                w.LPCWSTR, w.DWORD, c.POINTER(c.c_void_p), c.c_void_p)(
                    "D:P(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)", 1, c.byref(descriptor), None))
    try:
        yield Security(c.sizeof(Security), descriptor, False)
    finally:
        api(k, "LocalFree", c.c_void_p, c.c_void_p)(descriptor)


def private_directory(folder: Path) -> None:
    validate_path(str(folder))
    with pinned(folder.parent), owner_security() as security:
        checked(api(k, "CreateDirectoryW", w.BOOL, w.LPCWSTR, c.POINTER(Security))(
            str(folder), c.byref(security)))


@contextmanager
def parent_lock(parent: Path):
    # Open-or-create without truncation; a held descriptor permits no sharing.
    # Kept after release, so deletion/recreation cannot split the admission lock.
    with pinned(parent):
        lock = parent / ".basaltwater-media.lock"
        validate_path(str(lock))
        with owner_security() as security:
            handle = checked(create_file(str(lock), 0xc0000000, 0, c.byref(security), 4, 0x00200000, None))
        try:
            validate_path(str(lock), must_exist=True)
            yield
        finally:
            close(handle)


def write_json_atomic(path: str, value: dict, **_kwargs) -> None:
    target = Path(path)
    temporary = target.with_suffix(".tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, target)


def discover() -> dict:
    result = {}
    for name in ("ffmpeg", "ffprobe"):
        command = os.environ.get("BASALTWATER_" + name.upper() + "_EXE") or None
        if command:
            try:
                validate_path(command, must_exist=True)
            except FileNotFoundError:
                # WinGet upgrades can remove a versioned executable path.
                command = None
        if not command:
            # Use absolute PATH entries only; shutil.which on Python 3.11
            # implicitly searches the current working directory first.
            for folder in map(Path, os.get_exec_path()):
                candidate = folder / (name + ".exe")
                if folder.is_absolute() and candidate.is_file():
                    command = str(candidate)
                    validate_path(command, must_exist=True)
                    break
        if command:
            if Path(command).suffix.lower() != ".exe" or not Path(command).is_file():
                raise ValueError("Media tools must be regular .exe files")
        result[name] = command
    return result


class BasicLimit(c.Structure):
    _fields_ = [("process_time", c.c_longlong), ("job_time", c.c_longlong), ("flags", w.DWORD),
                ("minimum", c.c_size_t), ("maximum", c.c_size_t), ("active", w.DWORD),
                ("affinity", c.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]


class ExtendedLimit(c.Structure):
    _fields_ = [("basic", BasicLimit), ("io", c.c_ulonglong * 6),
                ("process_memory", c.c_size_t), ("job_memory", c.c_size_t),
                ("peak_process", c.c_size_t), ("peak_job", c.c_size_t)]


def run_captured(argv, *, input_data=None, timeout=180, max_bytes=2*1024*1024, cwd=None, env=None, binary=False):
    """Start suspended, assign bounded kill-on-close Job, then resume vendor code.

    Pipe workers never retain more than max_bytes combined. Poll output disk
    limits as well: Windows Jobs have no per-file byte limit.
    """
    job = checked(api(k, "CreateJobObjectW", w.HANDLE, c.c_void_p, w.LPCWSTR)(None, None))
    process = None
    workers = []
    chunks = [bytearray(), bytearray()]
    failure = threading.Event()
    guard = threading.Lock()
    deadline = time.monotonic() + timeout
    try:
        if env is None:
            system = os.environ["SystemRoot"]
            env = {"SystemRoot": system, "WINDIR": system,
                   "PATH": str(Path(argv[0]).absolute().parent) + ";" + str(Path(system) / "System32"),
                   "TEMP": cwd or tempfile.gettempdir(), "TMP": cwd or tempfile.gettempdir(),
                   "AV_LOG_FORCE_NOCOLOR": "1", "PYTHONUTF8": "1"}
        limits = ExtendedLimit()
        limits.basic.flags = 0x2000 | 0x100 | 0x200 | 0x8 | 0x2 | 0x4
        limits.basic.active = 8
        limits.basic.process_time = 90 * 10_000_000
        limits.basic.job_time = 90 * 10_000_000
        limits.process_memory = limits.job_memory = 1536 * 1024 * 1024
        checked(api(k, "SetInformationJobObject", w.BOOL, w.HANDLE, c.c_int, c.c_void_p, w.DWORD)(
            job, 9, c.byref(limits), c.sizeof(limits)))
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   cwd=cwd, env=env, creationflags=0x4 | 0x08000000)
        checked(api(k, "AssignProcessToJobObject", w.BOOL, w.HANDLE, w.HANDLE)(job, int(process._handle)))
        status = api(c.WinDLL("ntdll"), "NtResumeProcess", c.c_long, w.HANDLE)(int(process._handle))
        if status != 0:
            raise RuntimeError("Cannot resume contained media process")

        def drain(stream, index):
            try:
                while block := stream.read(8192):
                    with guard:
                        if len(chunks[0]) + len(chunks[1]) + len(block) > max_bytes:
                            failure.set()
                            return
                        chunks[index].extend(block)
            except OSError:
                failure.set()

        def feed():
            try:
                if input_data:
                    process.stdin.write(input_data)
            except (BrokenPipeError, OSError):
                pass
            finally:
                try:
                    process.stdin.close()
                except OSError:
                    # A bounded frame sample intentionally closes its input
                    # early; buffered stdin may report that again on close.
                    pass

        for fn, args in ((drain, (process.stdout, 0)), (drain, (process.stderr, 1)), (feed, ())):
            worker = threading.Thread(target=fn, args=args, daemon=True)
            workers.append(worker)
            worker.start()
        while True:
            if failure.is_set():
                raise RuntimeError("Media subprocess output exceeded limit or pipe failed")
            if time.monotonic() >= deadline:
                raise TimeoutError("Total media deadline exceeded")
            if cwd:
                sizes = [p.stat().st_size for p in Path(cwd).iterdir() if p.is_file()]
                if max(sizes, default=0) > 16*1024*1024 or sum(sizes) > 64*1024*1024:
                    raise RuntimeError("Media output disk budget exceeded")
            if process.poll() is not None and all(not t.is_alive() for t in workers):
                break
            time.sleep(.01)
        accounting = c.create_string_buffer(48)
        returned = w.DWORD()
        query = api(k, "QueryInformationJobObject", w.BOOL, w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.POINTER(w.DWORD))
        checked(query(job, 1, accounting, 48, c.byref(returned)))
        checked(query(job, 9, c.byref(limits), c.sizeof(limits), c.byref(returned)))
        result = subprocess.CompletedProcess(argv, process.returncode,
                                           bytes(chunks[0]) if binary else bytes(chunks[0]).decode("utf-8", "replace"),
                                           bytes(chunks[1]).decode("utf-8", "replace"))
        result.job_cpu_seconds = (c.c_longlong.from_buffer(accounting, 0).value
                                  + c.c_longlong.from_buffer(accounting, 8).value) / 10_000_000
        result.peak_job_commit_bytes = limits.peak_job
        return result
    finally:
        # No breakaway flags: descendants cannot escape this Job. Close is also
        # effective if this analyzer is terminated before its finally block.
        cleanup_error = None
        try:
            checked(api(k, "TerminateJobObject", w.BOOL, w.HANDLE, w.UINT)(job, 1))
            end = time.monotonic() + 5
            accounting = c.create_string_buffer(48)
            returned = w.DWORD()
            while True:
                checked(api(k, "QueryInformationJobObject", w.BOOL, w.HANDLE, c.c_int, c.c_void_p,
                            w.DWORD, c.POINTER(w.DWORD))(job, 1, accounting, 48, c.byref(returned)))
                if w.DWORD.from_buffer(accounting, 40).value == 0:
                    break
                if time.monotonic() >= end:
                    raise RuntimeError("Owned media Job cleanup could not be confirmed")
                time.sleep(.01)
        except OSError as exc:
            cleanup_error = exc
        except RuntimeError as exc:
            cleanup_error = exc
        finally:
            close(job)
        if process:
            if process.poll() is None:
                process.kill()  # also handles a failed Job assignment
            process.wait(timeout=5)
            for worker in workers:
                worker.join(5)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
            if any(worker.is_alive() for worker in workers):
                raise RuntimeError("Media pipe cleanup could not be confirmed")
        if cleanup_error:
            raise RuntimeError("Owned media Job cleanup could not be confirmed") from cleanup_error
