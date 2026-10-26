"""Pinned, bounded source snapshots for shared builds.

Snapshots contain committed regular files and directories in a small framed
format. Git history is kept as a separate filtered bundle so a worker can
materialize only the metadata needed by ``git describe`` under its build UID.
This module never checks out or executes Git metadata while unpacking.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import hashlib
import os
import re
import shutil
import signal
import stat
import struct
import subprocess
import tempfile
import time
import unicodedata
from contextlib import contextmanager
from typing import Any, BinaryIO, Callable, Iterator, Mapping, Sequence

from lib.cicd_deadline import command_timeout
from lib.project_manifest import MANIFEST_FILENAME
from lib.validation import validate_filesystem_path, validate_no_control_characters


MAGIC = b"BASALTWATER-SOURCE\x00\x01\n"
_READ = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_CHUNK = 64 * 1024
_MAX_TAG_ARGUMENT_BYTES = 1024 * 1024
_MAX_REVISION = re.compile(rb"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_MAX_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_LFS_POINTER_SIGNATURE = re.compile(
    rb"\Aversion https://git-lfs\.github\.com/spec/v1\r?\n"
)
_FORBIDDEN_COMPONENTS = frozenset({
    ".git", ".gitconfig", ".git-credentials", ".git-credential-cache",
    ".git-credential-store", ".netrc", ".npmrc", ".pypirc", ".dockercfg",
    ".aws", ".azure", ".docker", ".ssh", ".gnupg", "id_rsa", "id_ed25519",
    "known_hosts", "authorized_keys",
})
_WINDOWS_DEVICES = frozenset({
    "con", "prn", "aux", "nul", *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
})

MAX_SOURCE_ENTRIES = 100_000
MAX_SOURCE_FILE_BYTES = 64 * 1024 * 1024
MAX_SOURCE_BYTES = 2 * 1024**3
MAX_HISTORY_BYTES = 2 * 1024**3
MAX_SNAPSHOT_BYTES = 5 * 1024**3
MAX_SOURCE_METADATA_BYTES = 64 * 1024**2
MAX_SOURCE_PATH_BYTES = 4096
MAX_COMMAND_SECONDS = 3600
MAX_MANIFEST_BYTES = 256 * 1024
MAX_BLOB_BATCH_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class SourceLimits:
    """Caller-selectable lower bounds within the module's fixed ceilings."""

    max_entries: int = MAX_SOURCE_ENTRIES
    max_file_bytes: int = MAX_SOURCE_FILE_BYTES
    max_source_bytes: int = MAX_SOURCE_BYTES
    max_history_bytes: int = MAX_HISTORY_BYTES
    max_snapshot_bytes: int = MAX_SNAPSHOT_BYTES
    max_metadata_bytes: int = MAX_SOURCE_METADATA_BYTES
    max_path_bytes: int = MAX_SOURCE_PATH_BYTES
    command_timeout_seconds: int = 120

    def __post_init__(self) -> None:
        bounds = {
            "max_entries": (1, MAX_SOURCE_ENTRIES),
            "max_file_bytes": (1, MAX_SOURCE_FILE_BYTES),
            "max_source_bytes": (1, MAX_SOURCE_BYTES),
            "max_history_bytes": (1, MAX_HISTORY_BYTES),
            "max_snapshot_bytes": (1, MAX_SNAPSHOT_BYTES),
            "max_metadata_bytes": (1, MAX_SOURCE_METADATA_BYTES),
            "max_path_bytes": (1, MAX_SOURCE_PATH_BYTES),
            "command_timeout_seconds": (1, MAX_COMMAND_SECONDS),
        }
        for name, (minimum, maximum) in bounds.items():
            value = getattr(self, name)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"Source limit {name} is outside its supported range")


DEFAULT_SOURCE_LIMITS = SourceLimits()


CommandRunner = Callable[..., bytes]


@dataclass(frozen=True)
class _TreeEntry:
    path: str
    kind: str
    object_id: str = ""
    executable: bool = False
    size: int = 0


@contextmanager
def _directory(path: str) -> Iterator[int]:
    """Open a directory path without following any symbolic-link component."""
    validate_filesystem_path(path, must_exist=True)
    descriptor = os.open("/", _DIRECTORY)
    try:
        for part in os.path.abspath(path).split("/")[1:]:
            if not part:
                continue
            child = os.open(part, _DIRECTORY, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def _private_parent(path: str) -> Iterator[tuple[int, str]]:
    """Open a private, caller-owned parent and refuse existing destinations."""
    validate_filesystem_path(path)
    absolute = os.path.abspath(path)
    parent, name = os.path.split(absolute)
    if not name or name in {".", ".."}:
        raise ValueError("Source destination must name a new file or directory")
    with _directory(parent) as descriptor:
        info = os.fstat(descriptor)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError("Source destination parent must be private and caller-owned")
        try:
            os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise FileExistsError("Source destination already exists")
        yield descriptor, name


@contextmanager
def _temporary_directory(parent: int) -> Iterator[tuple[str, int]]:
    name = ".source-stage-" + os.urandom(16).hex()
    os.mkdir(name, mode=0o700, dir_fd=parent)
    descriptor = os.open(name, _DIRECTORY, dir_fd=parent)
    os.fchmod(descriptor, 0o700)
    try:
        yield name, descriptor
    finally:
        os.close(descriptor)
        try:
            shutil.rmtree(name, dir_fd=parent)
        except FileNotFoundError:
            pass


@contextmanager
def _temporary_file(parent: int, prefix: str) -> Iterator[tuple[str, int]]:
    name = prefix + os.urandom(16).hex()
    descriptor = os.open(
        name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
        dir_fd=parent,
    )
    try:
        yield name, descriptor
    finally:
        os.close(descriptor)
        try:
            os.unlink(name, dir_fd=parent)
        except FileNotFoundError:
            pass


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    """Terminate the isolated Git process group with bounded cleanup."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass


def _default_runner(
    command: Sequence[str], *, cwd: str, env: Mapping[str, str],
    input_data: bytes | None, timeout: float, max_output_bytes: int,
    file_size_limit: int | None = None,
) -> bytes:
    """Run Git with spooled, size-limited output and process-group cleanup."""
    if os.name != "posix":
        raise NotImplementedError("Bounded Git source snapshots require a POSIX runner")
    import resource

    stdout_stream: BinaryIO | int
    capture_stdout = max_output_bytes > 0
    output_context = tempfile.TemporaryFile(mode="w+b") if capture_stdout else None
    stdout_stream = output_context if output_context is not None else subprocess.DEVNULL
    limit = file_size_limit if file_size_limit is not None else max_output_bytes

    def limit_child() -> None:
        if limit > 0:
            _, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
            selected = limit if hard == resource.RLIM_INFINITY else min(limit, hard)
            resource.setrlimit(resource.RLIMIT_FSIZE, (selected, selected))
        os.umask(0o077)

    try:
        process = subprocess.Popen(
            list(command), cwd=cwd, env=dict(env),
            stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
            stdout=stdout_stream, stderr=subprocess.DEVNULL,
            start_new_session=True, preexec_fn=limit_child,
        )
        try:
            process.communicate(input=input_data, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            _stop_process(process)
            raise TimeoutError("Git source snapshot command timed out") from None
        except BaseException:
            _stop_process(process)
            raise
        if process.returncode != 0:
            raise ValueError("Git source snapshot command failed or exceeded its output limit")
        if output_context is None:
            return b""
        output_context.seek(0, os.SEEK_END)
        size = output_context.tell()
        if size > max_output_bytes:
            raise ValueError("Git source command exceeded its output limit")
        output_context.seek(0)
        result = output_context.read(max_output_bytes + 1)
        if len(result) > max_output_bytes:
            raise ValueError("Git source command exceeded its output limit")
        return result
    finally:
        if output_context is not None:
            output_context.close()


def _git_environment(home: str) -> dict[str, str]:
    """Use a small environment with Git config, helpers and network prompts off."""
    return {
        "PATH": os.defpath,
        "HOME": home,
        "LANG": "C",
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_PAGER": "cat",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_LFS_SKIP_SMUDGE": "1",
        "GIT_ATTR_NOSYSTEM": "1",
    }


def _invoke(
    runner: CommandRunner, args: Sequence[str], *, cwd: str, env: Mapping[str, str],
    deadline: float, limits: SourceLimits, input_data: bytes | None = None,
    max_output_bytes: int = 4096, file_size_limit: int | None = None,
) -> bytes:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Git source snapshot command timed out")
    timeout = command_timeout(min(float(limits.command_timeout_seconds), remaining))
    command = [
        "git", "-C", cwd,
        "-c", "core.hooksPath=/dev/null",
        "-c", "core.fsmonitor=false",
        "-c", "core.untrackedCache=false",
        "-c", "core.attributesFile=/dev/null",
        *args,
    ]
    result = runner(
        command, cwd=cwd, env=env, input_data=input_data, timeout=timeout,
        max_output_bytes=max_output_bytes, file_size_limit=file_size_limit,
    )
    if not isinstance(result, bytes) or len(result) > max_output_bytes:
        raise ValueError("Git source command returned invalid or oversized output")
    return result


def _validate_source_path(path: str, limits: SourceLimits) -> str:
    validate_filesystem_path(path, must_exist=False)
    try:
        encoded = path.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ValueError("Git source path is not valid UTF-8") from exc
    if len(encoded) > limits.max_path_bytes or path.startswith("/") or "\\" in path or ":" in path:
        raise ValueError("Git source path is outside the supported relative-path limits")
    parts = path.split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Git source path contains traversal or empty components")
    for part in parts:
        validate_no_control_characters(part, "Git source path")
        if any(unicodedata.category(char) == "Cc" for char in part):
            raise ValueError("Git source path contains control characters")
        folded = unicodedata.normalize("NFC", part).casefold()
        if folded in _FORBIDDEN_COMPONENTS:
            raise ValueError("Git source contains forbidden Git or credential metadata")
        if part.endswith((".", " ")):
            raise ValueError("Git source path is not portable")
        device = part.split(".", 1)[0].casefold()
        if device in _WINDOWS_DEVICES:
            raise ValueError("Git source path is not portable")
    if (len(parts) >= 3 and [part.casefold() for part in parts[:2]] == [".config", "gh"]
            and parts[2].casefold() == "hosts.yml"):
        raise ValueError("Git source contains forbidden credential metadata")
    return path


def _source_tree(raw: bytes, revision: str, limits: SourceLimits) -> list[_TreeEntry]:
    if len(raw) > limits.max_metadata_bytes:
        raise ValueError("Git source tree metadata exceeds its byte limit")
    if not raw.endswith(b"\x00"):
        raise ValueError("Git returned an incomplete source tree")
    files: list[_TreeEntry] = []
    directories: set[str] = set()
    canonical: dict[str, str] = {}
    paths: dict[str, str] = {}
    object_width = len(revision)

    for record in raw[:-1].split(b"\x00"):
        header, separator, raw_path = record.partition(b"\t")
        if not separator:
            raise ValueError("Git returned malformed source tree metadata")
        try:
            mode_raw, object_type, object_id_raw = header.split(b" ")
            path = raw_path.decode("utf-8", errors="strict")
            object_id = object_id_raw.decode("ascii")
        except (ValueError, UnicodeError) as exc:
            raise ValueError("Git returned malformed source tree metadata") from exc
        path = _validate_source_path(path, limits)
        if (len(object_id) != object_width
                or re.fullmatch(r"[0-9a-f]+", object_id) is None):
            raise ValueError("Git returned an invalid object identity")
        if mode_raw == b"100644" and object_type == b"blob":
            executable = False
        elif mode_raw == b"100755" and object_type == b"blob":
            executable = True
        elif mode_raw == b"120000":
            raise ValueError("Git source symlinks are unsupported")
        elif mode_raw == b"160000" and object_type == b"commit":
            raise ValueError("Git submodules are unsupported")
        else:
            raise ValueError("Git source contains an unsupported file type")
        canonical_path = unicodedata.normalize("NFC", path).casefold()
        if canonical_path in canonical:
            raise ValueError("Git source contains duplicate or case-colliding paths")
        canonical[canonical_path] = path
        paths[path] = "file"
        parts = path.split("/")
        for index in range(1, len(parts)):
            parent = "/".join(parts[:index])
            canonical_parent = unicodedata.normalize("NFC", parent).casefold()
            prior = canonical.get(canonical_parent)
            if prior is not None and prior != parent:
                raise ValueError("Git source contains duplicate or case-colliding paths")
            canonical[canonical_parent] = parent
            if paths.get(parent) == "file":
                raise ValueError("Git source file conflicts with a directory")
            if parent not in directories:
                directories.add(parent)
                if len(files) + len(directories) > limits.max_entries:
                    raise ValueError("Git source entry count exceeds its limit")
        files.append(_TreeEntry(path, "file", object_id, executable))
        if len(files) + len(directories) > limits.max_entries:
            raise ValueError("Git source entry count exceeds its limit")

    for directory in directories:
        if paths.get(directory) == "file":
            raise ValueError("Git source file conflicts with a directory")
        paths[directory] = "directory"
    ordered_directories = sorted(directories, key=lambda value: (value.count("/"), value))
    ordered_files = sorted(files, key=lambda item: item.path)
    entries = [
        *(_TreeEntry(path, "directory") for path in ordered_directories),
        *ordered_files,
    ]
    if not entries:
        raise ValueError("Git source entry count is empty")
    if MANIFEST_FILENAME not in {entry.path for entry in files}:
        raise ValueError(f"Git source must track {MANIFEST_FILENAME}")
    return entries


def _object_sizes(raw: bytes, files: Sequence[_TreeEntry], limits: SourceLimits) -> list[_TreeEntry]:
    expected = [entry for entry in files if entry.kind == "file"]
    if expected and not raw.endswith(b"\n"):
        raise ValueError("Git returned incomplete source object metadata")
    lines = raw[:-1].split(b"\n") if raw else []
    if len(lines) != len(expected):
        raise ValueError("Git returned incomplete source object metadata")
    result: list[_TreeEntry] = []
    total = 0
    for line, entry in zip(lines, expected, strict=True):
        try:
            object_id, object_type, raw_size = line.decode("ascii").split(" ")
            size = int(raw_size)
        except (ValueError, UnicodeError) as exc:
            raise ValueError("Git returned malformed source object metadata") from exc
        if object_id != entry.object_id or object_type != "blob" or size < 0:
            raise ValueError("Git source object metadata does not match its tree")
        if size > limits.max_file_bytes:
            raise ValueError("Git source file exceeds its byte limit")
        total += size
        if total > limits.max_source_bytes:
            raise ValueError("Git source exceeds its total byte limit")
        if entry.path == MANIFEST_FILENAME and size > MAX_MANIFEST_BYTES:
            raise ValueError("Git source manifest exceeds its byte limit")
        result.append(_TreeEntry(entry.path, entry.kind, entry.object_id,
                                 entry.executable, size))
    return result


def _parse_batch(raw: bytes, entries: Sequence[_TreeEntry]) -> list[bytes]:
    result: list[bytes] = []
    offset = 0
    for entry in entries:
        line_end = raw.find(b"\n", offset, min(len(raw), offset + 256))
        if line_end < 0:
            raise ValueError("Git returned a truncated source object")
        header = raw[offset:line_end]
        expected = f"{entry.object_id} blob {entry.size}".encode("ascii")
        if header != expected:
            raise ValueError("Git source object changed during snapshot preparation")
        offset = line_end + 1
        end = offset + entry.size
        if end >= len(raw):
            raise ValueError("Git returned a truncated source object")
        content = raw[offset:end]
        offset = end
        if offset >= len(raw) or raw[offset:offset + 1] != b"\n":
            raise ValueError("Git returned a malformed source object")
        offset += 1
        result.append(content)
    if offset != len(raw):
        raise ValueError("Git returned unexpected source object bytes")
    return result


def _git_tags(
    invoke: Callable[..., bytes], revision: str, limits: SourceLimits,
) -> list[tuple[str, str]]:
    raw = invoke(
        ["for-each-ref", "--sort=refname", f"--merged={revision}",
         "--format=%(objectname) %(refname)", "refs/tags"],
        max_output_bytes=limits.max_metadata_bytes,
    )
    if not raw:
        return []
    if not raw.endswith(b"\n"):
        raise ValueError("Git returned incomplete tag metadata")
    tags: list[tuple[str, str]] = []
    total_bytes = 0
    seen: set[str] = set()
    for line in raw[:-1].split(b"\n"):
        try:
            object_id_raw, ref_raw = line.split(b" ", 1)
            object_id = object_id_raw.decode("ascii")
            ref = ref_raw.decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise ValueError("Git returned invalid tag metadata") from exc
        except ValueError as exc:
            raise ValueError("Git returned malformed tag metadata") from exc
        if (not ref.startswith("refs/tags/") or len(ref) <= len("refs/tags/")
                or len(object_id) != len(revision)
                or _MAX_REVISION.fullmatch(object_id.encode("ascii")) is None
                or ref in seen):
            raise ValueError("Git returned an invalid tag reference")
        validate_no_control_characters(ref, "Git tag reference")
        seen.add(ref)
        tags.append((ref, object_id))
        total_bytes += len(line) + 1
        if len(tags) > limits.max_entries or total_bytes > _MAX_TAG_ARGUMENT_BYTES:
            raise ValueError("Git tag metadata exceeds its count or byte limit")
    return tags


def _reject_grafts(
    invoke: Callable[..., bytes], repository: str,
) -> None:
    raw = invoke(["rev-parse", "--git-path", "info/grafts"], max_output_bytes=8192)
    if not raw.endswith(b"\n") or raw.count(b"\n") != 1:
        raise ValueError("Git returned invalid graft metadata location")
    try:
        path = os.fsdecode(raw[:-1])
    except UnicodeError as exc:
        raise ValueError("Git returned invalid graft metadata location") from exc
    validate_filesystem_path(path)
    if not os.path.isabs(path):
        path = os.path.join(repository, path)
    if os.path.lexists(path):
        raise ValueError("Local Git graft metadata is unsupported for shared builds")


def _copy_info_attributes(
    invoke: Callable[..., bytes], repository: str, private_git_directory: str,
    limits: SourceLimits,
) -> None:
    """Copy bounded local attribute data into the isolated Git view."""
    raw = invoke(
        ["rev-parse", "--git-path", "info/attributes"], max_output_bytes=8192,
    )
    if not raw.endswith(b"\n") or raw.count(b"\n") != 1:
        raise ValueError("Git returned invalid attributes file metadata")
    try:
        path = os.fsdecode(raw[:-1])
    except UnicodeError as exc:
        raise ValueError("Git returned invalid attributes file metadata") from exc
    if not os.path.isabs(path):
        path = os.path.join(repository, path)
    path = os.path.abspath(path)
    validate_filesystem_path(path)
    if not os.path.lexists(path):
        return

    source_parent, source_name = os.path.split(path)
    with _directory(source_parent) as parent:
        descriptor = os.open(source_name, _READ, dir_fd=parent)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("Git info attributes must be a regular file")
            if before.st_size > limits.max_metadata_bytes:
                raise ValueError("Git info attributes exceed their byte limit")
            contents = bytearray()
            while True:
                read_size = min(_CHUNK, limits.max_metadata_bytes + 1 - len(contents))
                chunk = os.read(descriptor, read_size)
                if not chunk:
                    break
                contents.extend(chunk)
                if len(contents) > limits.max_metadata_bytes:
                    raise ValueError("Git info attributes exceed their byte limit")
            after = os.fstat(descriptor)
            try:
                current_path = os.stat(source_name, dir_fd=parent, follow_symlinks=False)
            except OSError as exc:
                raise ValueError("Git info attributes changed during snapshot preparation") from exc

            def identity(info: os.stat_result) -> tuple[int, ...]:
                return (
                    info.st_dev, info.st_ino, info.st_mode, info.st_size,
                    info.st_mtime_ns, info.st_ctime_ns,
                )
            if identity(before) != identity(after) or identity(after) != identity(current_path):
                raise ValueError("Git info attributes changed during snapshot preparation")
        finally:
            os.close(descriptor)

    private_info = os.path.join(private_git_directory, "info")
    if not os.path.lexists(private_info):
        with _directory(private_git_directory) as git_directory:
            os.mkdir("info", mode=0o700, dir_fd=git_directory)
    with _directory(private_info) as info:
        copied = os.open(
            "attributes", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600, dir_fd=info,
        )
        with os.fdopen(copied, "wb") as output:
            output.write(contents)
            output.flush()
            os.fsync(output.fileno())


def _git_conversion_options(invoke: Callable[..., bytes]) -> list[str]:
    """Return validated, built-in conversion settings from the source repo."""
    unset = "__basaltwater_unset__"
    settings = (
        ("core.autocrlf", "false", frozenset({"false", "true", "input"})),
        ("core.eol", "native", frozenset({"lf", "crlf", "native"})),
        ("core.safecrlf", "false", frozenset({"false", "true", "warn"})),
        ("core.checkRoundtripEncoding", unset, None),
    )
    options: list[str] = []
    for name, default, allowed in settings:
        raw = invoke(
            ["config", "--default", default, "--get", name],
            max_output_bytes=256,
        )
        if not raw.endswith(b"\n") or raw.count(b"\n") != 1:
            raise ValueError("Git returned invalid worktree conversion configuration")
        try:
            value = raw[:-1].decode("ascii").strip()
        except UnicodeError as exc:
            raise ValueError("Git returned invalid worktree conversion configuration") from exc
        if value == unset:
            continue
        if allowed is not None:
            value = value.lower()
            if value not in allowed:
                raise ValueError("Git worktree conversion configuration is unsupported")
        elif (len(value) > 128
              or re.fullmatch(r"[A-Za-z0-9._,+-]*", value) is None):
            raise ValueError("Git worktree encoding configuration is unsupported")
        options.extend(("-c", f"{name}={value}"))
    return options


def _prepare_history_repository(
    invoke: Callable[..., bytes], repository: str, history_directory: str,
    revision: str, tags: Sequence[tuple[str, str]], limits: SourceLimits,
) -> Callable[..., bytes]:
    """Build a private bare Git view with a detached pinned HEAD and tags."""
    raw = invoke(["rev-parse", "--git-path", "objects"], max_output_bytes=8192)
    if not raw.endswith(b"\n") or raw.count(b"\n") != 1:
        raise ValueError("Git returned invalid object directory metadata")
    try:
        object_directory = os.fsdecode(raw[:-1])
    except UnicodeError as exc:
        raise ValueError("Git returned invalid object directory metadata") from exc
    if not os.path.isabs(object_directory):
        object_directory = os.path.join(repository, object_directory)
    object_directory = os.path.abspath(object_directory)
    validate_filesystem_path(object_directory, must_exist=True)
    if "\n" in object_directory or "\r" in object_directory:
        raise ValueError("Git object directory path is unsupported")
    with _directory(object_directory):
        pass

    private_git_directory = os.path.join(history_directory, "metadata.git")
    template_directory = os.path.join(history_directory, "empty-template")
    os.mkdir(template_directory, mode=0o700)
    init_command = ["init", "--bare", f"--template={template_directory}"]
    if len(revision) == 64:
        init_command.append("--object-format=sha256")
    init_command.append(private_git_directory)
    invoke(init_command, max_output_bytes=8192)

    _copy_info_attributes(invoke, repository, private_git_directory, limits)

    with _directory(os.path.join(private_git_directory, "objects", "info")) as info:
        descriptor = os.open(
            "alternates", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600, dir_fd=info,
        )
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(os.fsencode(object_directory) + b"\n")
                output.flush()
                os.fsync(output.fileno())
        except BaseException:
            try:
                os.unlink("alternates", dir_fd=info)
            except OSError:
                pass
            raise

    def history_invoke(args: Sequence[str], **kwargs) -> bytes:
        return invoke([f"--git-dir={private_git_directory}", *args], **kwargs)

    update_records = ["start\n", "option no-deref\n", f"update HEAD {revision}\n"]
    update_records.extend(f"create {ref} {object_id}\n" for ref, object_id in tags)
    update_records.extend(("prepare\n", "commit\n"))
    update_input = "".join(update_records).encode("utf-8")
    if len(update_input) > limits.max_metadata_bytes:
        raise ValueError("Git history reference metadata exceeds its byte limit")
    history_invoke(
        ["update-ref", "--stdin"], input_data=update_input, max_output_bytes=0,
    )
    pinned_head = history_invoke(
        ["rev-parse", "--verify", "HEAD^{commit}"], max_output_bytes=80,
    ).strip()
    if pinned_head != revision.encode("ascii"):
        raise ValueError("Private Git history view does not have the resolved HEAD")
    return history_invoke


def _normalized_blob_id(
    invoke: Callable[..., bytes], revision: str, options: Sequence[str],
    path: str, content: bytes,
) -> str:
    raw = invoke(
        [*options, "hash-object", "--stdin", f"--path={path}"],
        env_overrides={"GIT_ATTR_SOURCE": revision}, input_data=content,
        max_output_bytes=len(revision) + 1,
    )
    if not raw.endswith(b"\n") or raw.count(b"\n") != 1:
        raise ValueError("Git returned invalid normalized source object metadata")
    try:
        object_id = raw[:-1].decode("ascii", errors="strict")
    except UnicodeError as exc:
        raise ValueError("Git returned invalid normalized source object metadata") from exc
    if (len(object_id) != len(revision)
            or _MAX_REVISION.fullmatch(object_id.encode("ascii")) is None):
        raise ValueError("Git returned invalid normalized source object metadata")
    return object_id


def _reject_external_filters(
    invoke: Callable[..., bytes], files: Sequence[_TreeEntry], limits: SourceLimits,
) -> None:
    """Inspect attributes before status so a configured clean filter cannot run."""
    raw = invoke(
        ["check-attr", "-z", "--stdin", "filter"],
        input_data=b"".join(entry.path.encode("utf-8") + b"\x00" for entry in files),
        max_output_bytes=limits.max_metadata_bytes,
    )
    if not raw.endswith(b"\x00"):
        raise ValueError("Git returned incomplete filter attribute metadata")
    fields = raw[:-1].split(b"\x00")
    if len(fields) != len(files) * 3:
        raise ValueError("Git returned malformed filter attribute metadata")
    for index, entry in enumerate(files):
        path, attribute, value = fields[index * 3:index * 3 + 3]
        if path != entry.path.encode("utf-8") or attribute != b"filter":
            raise ValueError("Git returned mismatched filter attribute metadata")
        if value not in (b"unspecified", b"unset"):
            raise ValueError("Git clean/smudge filters are unsupported for source preparation")


def _check_clean_repository(
    invoke: Callable[..., bytes], repository: str, files: Sequence[_TreeEntry],
    limits: SourceLimits, normalize_blob: Callable[[str, bytes], str],
) -> None:
    """Verify the index and tracked bytes, then reject visible untracked files."""
    index = invoke(
        ["ls-files", "--stage", "-z"],
        max_output_bytes=limits.max_metadata_bytes,
    )
    if index and not index.endswith(b"\x00"):
        raise ValueError("Git returned incomplete index metadata")
    expected = {
        entry.path: ("100755" if entry.executable else "100644", entry.object_id)
        for entry in files
    }
    actual: dict[str, tuple[str, str]] = {}
    for record in index[:-1].split(b"\x00") if index else ():
        header, separator, raw_path = record.partition(b"\t")
        if not separator:
            raise ValueError("Git returned malformed index metadata")
        try:
            mode_raw, object_id_raw, stage_raw = header.split(b" ")
            path = raw_path.decode("utf-8", errors="strict")
            mode = mode_raw.decode("ascii")
            object_id = object_id_raw.decode("ascii")
            stage = int(stage_raw)
        except (ValueError, UnicodeError) as exc:
            raise ValueError("Git returned malformed index metadata") from exc
        path = _validate_source_path(path, limits)
        if stage != 0 or path in actual:
            raise ValueError("Git index does not match the committed source tree")
        actual[path] = (mode, object_id)
    if actual != expected:
        raise ValueError("Git repository is dirty")
    flags = invoke(["ls-files", "-v", "-z"], max_output_bytes=limits.max_metadata_bytes)
    if flags and not flags.endswith(b"\x00"):
        raise ValueError("Git returned incomplete index flag metadata")
    for record in flags[:-1].split(b"\x00") if flags else ():
        if len(record) < 3 or record[:2] != b"H ":
            raise ValueError("Git repository has hidden or sparse index entries")
    _reject_external_filters(invoke, files, limits)
    with _directory(repository) as root:
        source_bytes = 0
        for entry in files:
            source_bytes += _check_worktree_blob(
                root, entry, limits, source_bytes, normalize_blob,
            )
    status = invoke(
        ["status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=none"],
        max_output_bytes=limits.max_metadata_bytes,
    )
    if status and not status.endswith(b"\x00"):
        raise ValueError("Git returned incomplete status metadata")
    expected_paths = {entry.path.encode("utf-8") for entry in files}
    for record in status[:-1].split(b"\x00") if status else ():
        if len(record) < 4 or record[2:3] != b" ":
            raise ValueError("Git returned malformed status metadata")
        state, path = record[:2], record[3:]
        if state == b"??":
            raise ValueError("Git repository is dirty")
        # Tracked index state and every no-follow worktree byte were checked
        # directly above. Git status can still mark a clean converted file
        # modified when repository stat-cache settings are unusual.
        if state != b" M" or path not in expected_paths:
            raise ValueError("Git repository is dirty")


class _SnapshotWriter:
    def __init__(self, stream: BinaryIO, maximum: int) -> None:
        self.stream = stream
        self.maximum = maximum
        self.size = 0
        self.digest = hashlib.sha256()

    def write(self, data: bytes) -> None:
        if self.size + len(data) > self.maximum:
            raise ValueError("Git source snapshot exceeds its byte limit")
        view = memoryview(data)
        while view:
            written = self.stream.write(view)
            if not written:
                raise OSError("Unable to write source snapshot")
            view = view[written:]
        self.digest.update(data)
        self.size += len(data)


def _metadata_record(writer: _SnapshotWriter, kind: bytes, flags: int, path: str,
                     limits: SourceLimits, size: int | None = None) -> None:
    encoded = path.encode("utf-8")
    if len(encoded) > limits.max_path_bytes:
        raise ValueError("Git source path exceeds its byte limit")
    writer.write(kind + bytes((flags,)) + struct.pack("!I", len(encoded)) + encoded)
    if size is not None:
        writer.write(struct.pack("!Q", size))


def _check_worktree_blob(
    root: int, entry: _TreeEntry, limits: SourceLimits, source_bytes: int,
    normalize_blob: Callable[[str, bytes], str],
) -> int:
    """Compare a no-follow worktree file with its normalized Git blob ID."""
    with _relative_parent(root, entry.path) as (parent, leaf):
        try:
            path_info = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
        except OSError as exc:
            raise ValueError("Git repository is dirty") from exc
        if not stat.S_ISREG(path_info.st_mode):
            raise ValueError("Git repository has a symlink or special tracked worktree file")
        if bool(path_info.st_mode & 0o111) != entry.executable:
            raise ValueError("Git repository is dirty")
        if path_info.st_size > limits.max_file_bytes:
            raise ValueError("Git source file exceeds its byte limit")
        if entry.path == MANIFEST_FILENAME and path_info.st_size > MAX_MANIFEST_BYTES:
            raise ValueError("Git source manifest exceeds its byte limit")
        if source_bytes + path_info.st_size > limits.max_source_bytes:
            raise ValueError("Git source exceeds its total byte limit")
        try:
            descriptor = os.open(leaf, _READ, dir_fd=parent)
        except OSError as exc:
            raise ValueError("Git repository is dirty") from exc
        try:
            opened_info = os.fstat(descriptor)
            def identity(info: os.stat_result) -> tuple[int, ...]:
                return (
                    info.st_dev, info.st_ino, info.st_mode, info.st_size,
                    info.st_mtime_ns, info.st_ctime_ns,
                )
            if (not stat.S_ISREG(opened_info.st_mode)
                    or identity(opened_info) != identity(path_info)):
                raise ValueError("Git repository is dirty")

            algorithm = "sha1" if len(entry.object_id) == 40 else "sha256"
            digest = hashlib.new(algorithm, usedforsecurity=False)
            digest.update(b"blob " + str(opened_info.st_size).encode("ascii") + b"\0")
            read_bytes = 0
            worktree_content = bytearray()
            while True:
                chunk = os.read(descriptor, _CHUNK)
                if not chunk:
                    break
                read_bytes += len(chunk)
                if (read_bytes > limits.max_file_bytes
                        or source_bytes + read_bytes > limits.max_source_bytes):
                    raise ValueError("Git source exceeds its byte limit")
                digest.update(chunk)
                worktree_content.extend(chunk)
            final_info = os.fstat(descriptor)
            try:
                final_path_info = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
            except OSError as exc:
                raise ValueError("Git repository is dirty") from exc
            if (read_bytes != opened_info.st_size
                    or identity(final_info) != identity(opened_info)
                    or identity(final_path_info) != identity(opened_info)):
                raise ValueError("Git repository is dirty")
            if digest.hexdigest() != entry.object_id:
                normalized_oid = normalize_blob(entry.path, bytes(worktree_content))
                if normalized_oid != entry.object_id:
                    raise ValueError("Git repository is dirty")
            return read_bytes
        finally:
            os.close(descriptor)


def _is_lfs_pointer(content: bytes) -> bool:
    """Conservatively reject every file with the Git LFS pointer signature."""
    return _LFS_POINTER_SIGNATURE.match(content) is not None


def _verify_bundle_heads(
    invoke: Callable[..., bytes], bundle_path: str, revision: str,
    tags: Sequence[tuple[str, str]], limits: SourceLimits,
) -> None:
    """Require the bundle to advertise the pinned commit and selected tags."""
    raw = invoke(
        ["bundle", "list-heads", bundle_path],
        max_output_bytes=limits.max_metadata_bytes,
    )
    if raw and not raw.endswith(b"\n"):
        raise ValueError("Git returned incomplete history bundle head metadata")
    recorded: dict[str, str] = {}
    for line in raw[:-1].split(b"\n") if raw else ():
        try:
            object_id_raw, ref_raw = line.split(b" ", 1)
            object_id = object_id_raw.decode("ascii")
            ref = ref_raw.decode("utf-8", errors="strict")
        except (UnicodeError, ValueError) as exc:
            raise ValueError("Git returned malformed history bundle head metadata") from exc
        if (len(object_id) != len(revision)
                or _MAX_REVISION.fullmatch(object_id.encode("ascii")) is None
                or not ref or ref in recorded):
            raise ValueError("Git returned invalid history bundle head metadata")
        recorded[ref] = object_id
    if recorded.get("HEAD") != revision:
        raise ValueError("Git history bundle HEAD does not match the resolved revision")
    expected_tags = dict(tags)
    recorded_tags = {
        ref: object_id for ref, object_id in recorded.items()
        if ref.startswith("refs/tags/")
    }
    if recorded_tags != expected_tags:
        raise ValueError("Git history bundle tag references do not match the resolved revision")


def _read_blob_batches(
    files: Sequence[_TreeEntry], invoke: Callable[..., bytes], limits: SourceLimits,
) -> Iterator[tuple[Sequence[_TreeEntry], list[bytes]]]:
    def read(batch: list[_TreeEntry], total: int) -> tuple[Sequence[_TreeEntry], list[bytes]]:
        metadata_bound = 256 * len(batch)
        if metadata_bound > limits.max_metadata_bytes:
            raise ValueError("Git source object metadata exceeds its byte limit")
        raw = invoke(
            ["cat-file", "--batch"],
            input_data=b"".join(item.object_id.encode("ascii") + b"\n" for item in batch),
            max_output_bytes=total + metadata_bound,
        )
        return tuple(batch), _parse_batch(raw, batch)

    batch: list[_TreeEntry] = []
    batch_bytes = 0
    for entry in files:
        if batch and batch_bytes + entry.size > MAX_BLOB_BATCH_BYTES:
            yield read(batch, batch_bytes)
            batch, batch_bytes = [], 0
        batch.append(entry)
        batch_bytes += entry.size
    if batch:
        yield read(batch, batch_bytes)


def _publish_directory(parent: int, source: str, destination: str) -> None:
    """Atomically publish without replacing an existing destination."""
    library = ctypes.CDLL(None, use_errno=True)
    rename = getattr(library, "renameat2", None)
    if rename is None:
        raise NotImplementedError("Source publication requires no-replace rename support")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(parent, os.fsencode(source), parent, os.fsencode(destination), 1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), destination)


def create_source_snapshot(
    repository: str, destination: str, *,
    limits: SourceLimits = DEFAULT_SOURCE_LIMITS,
    runner: CommandRunner | None = None,
) -> dict[str, str]:
    """Freeze a clean tracked ``HEAD`` into a new private snapshot file.

    The return value is the ``source`` record accepted by
    :func:`lib.build_protocol.parse_build_request`. The snapshot digest covers
    the complete framed file, including its separate Git history bundle; the
    manifest digest covers the exact committed ``basaltwater.json`` bytes.
    Neither digest is embedded in the bytes it hashes.
    """
    validate_filesystem_path(repository, must_exist=True)
    if not isinstance(limits, SourceLimits):
        raise ValueError("Source limits must use SourceLimits")
    selected_runner = runner if runner is not None else _default_runner
    repository_path = os.path.abspath(repository)
    with _directory(repository_path):
        pass
    destination_path = os.path.abspath(destination)
    with _private_parent(destination_path):
        pass
    deadline = time.monotonic() + limits.command_timeout_seconds
    parent_context = _private_parent(destination_path)

    with tempfile.TemporaryDirectory(prefix="basaltw-source-home-") as home:
        env = _git_environment(home)
        initial = _invoke(
            selected_runner, ["rev-parse", "--show-toplevel"], cwd=repository_path,
            env=env, deadline=deadline, limits=limits, max_output_bytes=limits.max_path_bytes + 2,
        )
        if not initial.endswith(b"\n") or initial.count(b"\n") != 1:
            raise ValueError("Git did not return a supported repository root")
        try:
            root = initial[:-1].decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise ValueError("Git repository root is not valid UTF-8") from exc
        validate_filesystem_path(root, must_exist=True)
        root = os.path.abspath(root)
        with _directory(root):
            pass
        try:
            if os.path.commonpath((os.path.realpath(root), os.path.realpath(destination_path))) == os.path.realpath(root):
                raise ValueError("Source snapshot destination must be outside the repository")
        except ValueError:
            raise
        except OSError as exc:
            raise ValueError("Invalid source snapshot destination") from exc

        def invoke(
            args: Sequence[str], *, env_overrides: Mapping[str, str] | None = None,
            **kwargs,
        ) -> bytes:
            command_env = dict(env)
            if env_overrides:
                command_env.update(env_overrides)
            return _invoke(
                selected_runner, args, cwd=root, env=command_env, deadline=deadline,
                limits=limits, **kwargs,
            )

        shallow = invoke(["rev-parse", "--is-shallow-repository"], max_output_bytes=32).strip()
        if shallow != b"false":
            raise ValueError("Shallow Git repositories are unsupported for shared builds")
        _reject_grafts(invoke, root)
        revision_raw = invoke(["rev-parse", "--verify", "HEAD^{commit}"], max_output_bytes=80).strip()
        if _MAX_REVISION.fullmatch(revision_raw) is None:
            raise ValueError("Git did not return a complete HEAD revision")
        revision = revision_raw.decode("ascii")
        tree_raw = invoke(
            ["ls-tree", "-rz", "--full-tree", revision],
            max_output_bytes=limits.max_metadata_bytes,
        )
        entries = _source_tree(tree_raw, revision, limits)
        source_files = [entry for entry in entries if entry.kind == "file"]
        tags = _git_tags(invoke, revision, limits)

        with parent_context as (output_parent, output_name), tempfile.TemporaryDirectory(
            prefix="basaltw-source-history-", dir=os.path.dirname(destination_path),
        ) as history_directory:
            if os.path.commonpath((os.path.realpath(root), os.path.realpath(destination_path))) == os.path.realpath(root):
                raise ValueError("Source snapshot destination must be outside the repository")
            history_invoke = _prepare_history_repository(
                invoke, root, history_directory, revision, tags, limits,
            )
            conversion_options = _git_conversion_options(invoke)

            def normalize_blob(path: str, content: bytes) -> str:
                return _normalized_blob_id(
                    history_invoke, revision, conversion_options, path, content,
                )

            _check_clean_repository(invoke, root, source_files, limits, normalize_blob)
            size_input = b"".join(
                entry.object_id.encode("ascii") + b"\n" for entry in source_files
            )
            sizes_raw = invoke(
                ["cat-file", "--batch-check"], input_data=size_input,
                max_output_bytes=limits.max_metadata_bytes,
            )
            sized = _object_sizes(sizes_raw, source_files, limits)
            size_by_path = {entry.path: entry for entry in sized}
            entries = [size_by_path.get(entry.path, entry) for entry in entries]
            metadata_size = len(MAGIC) + 2 + len(revision) + 4 + 1 + 8 + 32 + 1
            metadata_size += sum(
                1 + 1 + 4 + len(entry.path.encode("utf-8"))
                + (8 if entry.kind == "file" else 0)
                + (32 if entry.kind == "file" else 0)
                for entry in entries
            )
            if metadata_size > limits.max_metadata_bytes:
                raise ValueError("Git source snapshot metadata exceeds its byte limit")
            bundle_path = os.path.join(history_directory, "history.bundle")
            bundle_command = [
                "bundle", "create", "--version=3", bundle_path, "--filter=blob:none",
                "HEAD", *(ref for ref, _object_id in tags),
            ]
            history_invoke(
                bundle_command, max_output_bytes=0,
                file_size_limit=limits.max_history_bytes,
            )
            _verify_bundle_heads(history_invoke, bundle_path, revision, tags, limits)
            if _git_tags(invoke, revision, limits) != tags:
                raise ValueError("Git tag references changed during source snapshot creation")
            bundle_fd = os.open(bundle_path, _READ)
            try:
                bundle_info = os.fstat(bundle_fd)
                if (not stat.S_ISREG(bundle_info.st_mode) or bundle_info.st_nlink != 1
                        or not 1 <= bundle_info.st_size <= limits.max_history_bytes):
                    raise ValueError("Git history bundle is not a bounded regular file")
                bundle_stream = os.fdopen(bundle_fd, "rb")
                bundle_fd = -1
            finally:
                if bundle_fd >= 0:
                    os.close(bundle_fd)

            manifest_digest = ""
            with bundle_stream, _temporary_file(output_parent, ".source-snapshot-") as (temporary, descriptor):
                with os.fdopen(os.dup(descriptor), "wb") as output:
                    writer = _SnapshotWriter(output, limits.max_snapshot_bytes)
                    writer.write(MAGIC)
                    writer.write(struct.pack("!H", len(revision)))
                    writer.write(revision.encode("ascii"))
                    writer.write(struct.pack("!I", len(entries)))
                    for entry in entries:
                        if entry.kind == "directory":
                            _metadata_record(writer, b"D", 0, entry.path, limits)

                    for batch_entries, contents in _read_blob_batches(sized, invoke, limits):
                        for entry, content in zip(batch_entries, contents, strict=True):
                            if _is_lfs_pointer(content):
                                raise ValueError("Git LFS pointer files are unsupported")
                            _metadata_record(writer, b"F", int(entry.executable), entry.path,
                                             limits, entry.size)
                            digest = hashlib.sha256(content)
                            for offset in range(0, len(content), _CHUNK):
                                writer.write(content[offset:offset + _CHUNK])
                            writer.write(digest.digest())
                            if entry.path == MANIFEST_FILENAME:
                                manifest_digest = digest.hexdigest()
                    if not manifest_digest:
                        raise ValueError("Git source manifest is missing")
                    writer.write(b"H" + struct.pack("!Q", bundle_info.st_size))
                    history_digest = hashlib.sha256()
                    history_remaining = bundle_info.st_size
                    while history_remaining:
                        content = bundle_stream.read(min(_CHUNK, history_remaining))
                        if not content:
                            raise ValueError("Git history bundle changed during snapshot creation")
                        writer.write(content)
                        history_digest.update(content)
                        history_remaining -= len(content)
                    if bundle_stream.read(1):
                        raise ValueError("Git history bundle changed during snapshot creation")
                    writer.write(history_digest.digest())
                    writer.write(b"E")
                    output.flush()
                    os.fsync(output.fileno())
                    snapshot_digest = writer.digest.hexdigest()
                    snapshot_size = writer.size
                os.fchmod(descriptor, 0o400)
                os.fsync(descriptor)

                _check_clean_repository(invoke, root, source_files, limits, normalize_blob)
                final_revision = invoke(
                    ["rev-parse", "--verify", "HEAD^{commit}"], max_output_bytes=80,
                ).strip()
                if final_revision != revision_raw:
                    raise ValueError("Git repository changed during source snapshot creation")
                os.link(temporary, output_name, src_dir_fd=output_parent,
                        dst_dir_fd=output_parent, follow_symlinks=False)
                os.fsync(output_parent)

        if snapshot_size > limits.max_snapshot_bytes:
            raise ValueError("Git source snapshot exceeds its byte limit")
        return {
            "revision": revision,
            "snapshot_sha256": snapshot_digest,
            "manifest_sha256": manifest_digest,
        }


class _SnapshotReader:
    def __init__(self, stream: BinaryIO, maximum: int, metadata_maximum: int) -> None:
        self.stream = stream
        self.maximum = maximum
        self.metadata_maximum = metadata_maximum
        self.size = 0
        self.metadata_size = 0
        self.digest = hashlib.sha256()

    def _read(self, size: int, *, metadata: bool) -> bytes:
        if size < 0 or self.size + size > self.maximum:
            raise ValueError("Source snapshot exceeds its byte limit")
        if metadata and self.metadata_size + size > self.metadata_maximum:
            raise ValueError("Source snapshot metadata exceeds its byte limit")
        parts: list[bytes] = []
        remaining = size
        while remaining:
            content = self.stream.read(min(_CHUNK, remaining))
            if not content:
                raise ValueError("Truncated source snapshot")
            parts.append(content)
            self.digest.update(content)
            self.size += len(content)
            if metadata:
                self.metadata_size += len(content)
            remaining -= len(content)
        return b"".join(parts)

    def metadata(self, size: int) -> bytes:
        return self._read(size, metadata=True)

    def payload(self, size: int) -> bytes:
        return self._read(size, metadata=False)

    def copy_payload(self, size: int, output: BinaryIO, file_digest: Any) -> None:
        remaining = size
        while remaining:
            content = self.payload(min(_CHUNK, remaining))
            output.write(content)
            file_digest.update(content)
            remaining -= len(content)


def _validate_expected_source(source: object) -> dict[str, str]:
    if not isinstance(source, dict) or set(source) != {
        "revision", "snapshot_sha256", "manifest_sha256",
    }:
        raise ValueError("Expected source identity has unsupported fields")
    revision = source["revision"]
    if not isinstance(revision, str) or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", revision) is None:
        raise ValueError("Expected source revision is invalid")
    for field in ("snapshot_sha256", "manifest_sha256"):
        if not isinstance(source[field], str) or _MAX_SHA256.fullmatch(source[field]) is None:
            raise ValueError("Expected source digest is invalid")
    return dict(source)


@contextmanager
def _open_private_regular(path: str) -> Iterator[BinaryIO]:
    validate_filesystem_path(path, must_exist=True)
    absolute = os.path.abspath(path)
    parent_path, name = os.path.split(absolute)
    with _directory(parent_path) as parent:
        parent_info = os.fstat(parent)
        if parent_info.st_uid != os.geteuid() or stat.S_IMODE(parent_info.st_mode) & 0o077:
            raise ValueError("Source snapshot parent must be private and caller-owned")
        descriptor = os.open(name, _READ, dir_fd=parent)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077):
            raise ValueError("Source snapshot must be a private regular file")
        stream = os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise
    with stream:
        yield stream


@contextmanager
def _relative_parent(root: int, path: str) -> Iterator[tuple[int, str]]:
    parts = path.split("/")
    descriptor = os.dup(root)
    try:
        for part in parts[:-1]:
            child = os.open(part, _DIRECTORY, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor, parts[-1]
    finally:
        os.close(descriptor)


def _write_extracted_file(reader: _SnapshotReader, root: int, path: str,
                          size: int, executable: bool) -> str:
    with _relative_parent(root, path) as (parent, name):
        descriptor = os.open(
            name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
            dir_fd=parent,
        )
        digest = hashlib.sha256()
        try:
            with os.fdopen(descriptor, "wb") as output:
                reader.copy_payload(size, output, digest)
                expected = reader.metadata(32)
                if digest.digest() != expected:
                    raise ValueError("Source file checksum mismatch")
                output.flush()
                os.fchmod(output.fileno(), 0o755 if executable else 0o644)
                os.fsync(output.fileno())
        except BaseException:
            try:
                os.unlink(name, dir_fd=parent)
            except OSError:
                pass
            raise
    return digest.hexdigest()


def _sync_directories(root: int, paths: Sequence[str]) -> None:
    for path in reversed(paths):
        with _relative_parent(root, path) as (parent, name):
            descriptor = os.open(name, _DIRECTORY, dir_fd=parent)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    os.fsync(root)


def unpack_source_snapshot(
    snapshot: str, destination: str, *, expected_source: object,
    limits: SourceLimits = DEFAULT_SOURCE_LIMITS,
) -> tuple[str, str]:
    """Verify and safely unpack source files plus a separate history bundle.

    Returns ``(checkout_path, history_bundle_path)`` after publishing a new
    private directory. The function treats every snapshot byte as data and
    never invokes Git; workers materialize the bundle under their unprivileged
    build identity after this verification succeeds.
    """
    if not isinstance(limits, SourceLimits):
        raise ValueError("Source limits must use SourceLimits")
    source = _validate_expected_source(expected_source)
    destination_path = os.path.abspath(destination)
    with _private_parent(destination_path) as (parent, name), _open_private_regular(snapshot) as stream:
        snapshot_info = os.fstat(stream.fileno())
        if snapshot_info.st_size > limits.max_snapshot_bytes:
            raise ValueError("Source snapshot exceeds its byte limit")
        with _temporary_directory(parent) as (temporary, stage):
            checkout_name = "checkout"
            os.mkdir(checkout_name, mode=0o700, dir_fd=stage)
            checkout = os.open(checkout_name, _DIRECTORY, dir_fd=stage)
            os.fchmod(checkout, 0o700)
            reader = _SnapshotReader(stream, limits.max_snapshot_bytes, limits.max_metadata_bytes)
            if reader.metadata(len(MAGIC)) != MAGIC:
                raise ValueError("Unsupported source snapshot format")
            revision_size = struct.unpack("!H", reader.metadata(2))[0]
            if revision_size not in (40, 64):
                raise ValueError("Source snapshot revision metadata is invalid")
            revision = reader.metadata(revision_size).decode("ascii", errors="strict")
            if _MAX_REVISION.fullmatch(revision.encode("ascii")) is None or revision != source["revision"]:
                raise ValueError("Source snapshot revision does not match expected source")
            entry_count = struct.unpack("!I", reader.metadata(4))[0]
            if not 1 <= entry_count <= limits.max_entries:
                raise ValueError("Source snapshot entry count exceeds its limit")

            directories: set[str] = set()
            canonical: dict[str, str] = {}
            files_seen: set[str] = set()
            last_directory: tuple[int, str] | None = None
            last_file = ""
            reading_files = False
            total_source_bytes = 0
            manifest_digest = ""
            directory_order: list[str] = []

            try:
                for _ in range(entry_count):
                    kind, flags = reader.metadata(1), reader.metadata(1)[0]
                    path_size = struct.unpack("!I", reader.metadata(4))[0]
                    if not 1 <= path_size <= limits.max_path_bytes:
                        raise ValueError("Source snapshot path exceeds its byte limit")
                    try:
                        path = reader.metadata(path_size).decode("utf-8", errors="strict")
                    except UnicodeError as exc:
                        raise ValueError("Source snapshot path is not valid UTF-8") from exc
                    path = _validate_source_path(path, limits)
                    normalized = unicodedata.normalize("NFC", path).casefold()
                    if normalized in canonical:
                        raise ValueError("Source snapshot contains duplicate or case-colliding paths")
                    canonical[normalized] = path
                    parts = path.split("/")
                    for index in range(1, len(parts)):
                        parent_path = "/".join(parts[:index])
                        if parent_path not in directories:
                            raise ValueError("Source snapshot is missing a parent directory")
                    if kind == b"D":
                        if flags != 0 or reading_files:
                            raise ValueError("Source snapshot directory metadata is invalid")
                        order = (path.count("/"), path)
                        if last_directory is not None and order <= last_directory:
                            raise ValueError("Source snapshot directories are out of order")
                        last_directory = order
                        with _relative_parent(checkout, path) as (directory_parent, leaf):
                            os.mkdir(leaf, mode=0o755, dir_fd=directory_parent)
                            directory_fd = os.open(leaf, _DIRECTORY, dir_fd=directory_parent)
                            try:
                                os.fchmod(directory_fd, 0o755)
                            finally:
                                os.close(directory_fd)
                        directories.add(path)
                        directory_order.append(path)
                        continue
                    if kind != b"F" or flags not in (0, 1):
                        raise ValueError("Source snapshot entry type is invalid")
                    reading_files = True
                    if path in directories or path in files_seen or (last_file and path <= last_file):
                        raise ValueError("Source snapshot files are duplicate or out of order")
                    last_file = path
                    files_seen.add(path)
                    size = struct.unpack("!Q", reader.metadata(8))[0]
                    if size > limits.max_file_bytes:
                        raise ValueError("Source snapshot file exceeds its byte limit")
                    total_source_bytes += size
                    if total_source_bytes > limits.max_source_bytes:
                        raise ValueError("Source snapshot source bytes exceed their limit")
                    if path == MANIFEST_FILENAME and size > MAX_MANIFEST_BYTES:
                        raise ValueError("Source snapshot manifest exceeds its byte limit")
                    file_digest = _write_extracted_file(reader, checkout, path, size, bool(flags))
                    if path == MANIFEST_FILENAME:
                        manifest_digest = file_digest

                if not manifest_digest:
                    raise ValueError("Source snapshot does not contain its manifest")
                if manifest_digest != source["manifest_sha256"]:
                    raise ValueError("Source manifest checksum mismatch")

                if reader.metadata(1) != b"H":
                    raise ValueError("Source snapshot history payload is missing")
                history_size = struct.unpack("!Q", reader.metadata(8))[0]
                if not 1 <= history_size <= limits.max_history_bytes:
                    raise ValueError("Source history bundle exceeds its byte limit")
                history_fd = os.open(
                    "history.bundle", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600, dir_fd=stage,
                )
                history_digest = hashlib.sha256()
                try:
                    with os.fdopen(history_fd, "wb") as history_output:
                        remaining = history_size
                        while remaining:
                            content = reader.payload(min(_CHUNK, remaining))
                            history_output.write(content)
                            history_digest.update(content)
                            remaining -= len(content)
                        expected_history = reader.metadata(32)
                        if history_digest.digest() != expected_history:
                            raise ValueError("Source history checksum mismatch")
                        history_output.flush()
                        os.fchmod(history_output.fileno(), 0o400)
                        os.fsync(history_output.fileno())
                except BaseException:
                    try:
                        os.unlink("history.bundle", dir_fd=stage)
                    except OSError:
                        pass
                    raise
                if reader.metadata(1) != b"E":
                    raise ValueError("Source snapshot terminator is invalid")
                if stream.read(1):
                    raise ValueError("Source snapshot has trailing bytes")
                if reader.digest.hexdigest() != source["snapshot_sha256"]:
                    raise ValueError("Source snapshot checksum mismatch")
                after = os.fstat(stream.fileno())
                if (snapshot_info.st_size, snapshot_info.st_mtime_ns, snapshot_info.st_ctime_ns) != (
                        after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    raise ValueError("Source snapshot changed during extraction")
                _sync_directories(checkout, directory_order)
                os.fsync(stage)
                _publish_directory(parent, temporary, name)
                os.fsync(parent)
            finally:
                os.close(checkout)

    return os.path.join(destination_path, "checkout"), os.path.join(destination_path, "history.bundle")
