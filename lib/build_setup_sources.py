"""Prepare public, pinned setup sources without running repository code."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from typing import Iterator, Mapping, Sequence
from urllib.parse import urlsplit

from lib.atomic_io import parse_json
from lib.build_source import (
    CommandRunner, DEFAULT_SOURCE_LIMITS, MAX_MANIFEST_BYTES, SourceLimits,
    create_source_snapshot,
)
from lib.cicd_deadline import command_timeout
from lib.cicd_deploy_policy import validate_nginx_path
from lib.deploy_utils import parse_deploy_spec, parse_repository_source
from lib.project_manifest import MANIFEST_FILENAME, Manifest, parse_manifest
from lib.setup_directories import directory_fd
from lib.validation import validate_deploy_specs, validate_filesystem_path
from lib.validators import validate_host


MAX_PLANS = 16
_POLL_SECONDS = 0.1
_REVISION = re.compile(rb"(?:[0-9a-f]{40}|[0-9a-f]{64})\n\Z")
_LIMIT_EXEC = """\
import os, resource, sys
requested = int(sys.argv[1])
_, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
bound = requested if hard == resource.RLIM_INFINITY else min(requested, hard)
resource.setrlimit(resource.RLIMIT_FSIZE, (bound, bound))
os.execvpe(sys.argv[2], sys.argv[2:], os.environ)
"""


@dataclass(frozen=True)
class PreparedSource:
    """One destination and its explicitly declared, pinned source input."""

    destination: str
    manifest: Manifest
    source: dict[str, str]
    source_path: str


def _plans(deploy_specs: list[list[str]]) -> list[tuple[str, str, str | None]]:
    if (not isinstance(deploy_specs, list) or not 1 <= len(deploy_specs) <= MAX_PLANS
            or any(not isinstance(item, list) or len(item) != 2
                   or any(not isinstance(value, str) for value in item)
                   for item in deploy_specs)):
        raise ValueError("Source preparation requires 1..16 standard deployment pairs")
    if any(len(targets) > MAX_PLANS * 4351 or len(source) > 5134
           for targets, source in deploy_specs):
        raise ValueError("Source preparation deployment pair exceeds its input limit")
    validate_deploy_specs(deploy_specs)
    plans = []
    for destinations, source in deploy_specs:
        url, ref = parse_repository_source(source)
        parsed = urlsplit(url)
        if (len(url) > 4096 or parsed.scheme != "https" or not parsed.hostname
                or not validate_host(parsed.hostname) or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment
                or not parsed.path or parsed.path == "/"):
            raise ValueError("Build setup sources require public HTTPS repositories without credentials")
        # Also reject malformed/out-of-range ports before starting any process.
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError("Invalid public HTTPS repository port")
        for raw in destinations.split(","):
            destination = raw.strip()
            domain, path = parse_deploy_spec(destination)
            if len(destination) > 4350 or domain is None or not validate_host(domain):
                raise ValueError("Build source destination requires a literal DOMAIN[/PATH]")
            validate_nginx_path(path)
            plans.append((destination, url, ref))
            if len(plans) > MAX_PLANS:
                raise ValueError("Source preparation supports at most 16 expanded destinations")
    return plans


class _Storage:
    """Observe aggregate private storage; this is not a filesystem reservation."""

    def __init__(self, root: str, limits: SourceLimits) -> None:
        self.root = root
        self.maximum = (limits.max_source_bytes + 2 * limits.max_history_bytes
                        + limits.max_snapshot_bytes + 2 * limits.max_metadata_bytes)
        self.entries = 4 * limits.max_entries + 128
        self.reserve = limits.max_metadata_bytes
        self.failure: Exception | None = None
        self.stopped = threading.Event()

    def _scan(self, descriptor: int) -> Iterator[os.stat_result]:
        with os.scandir(descriptor) as entries:
            for entry in entries:
                try:
                    info = entry.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue
                yield info
                if stat.S_ISDIR(info.st_mode):
                    try:
                        child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                        dir_fd=descriptor)
                    except FileNotFoundError:
                        continue
                    try:
                        yield from self._scan(child)
                    finally:
                        os.close(child)
                elif not stat.S_ISREG(info.st_mode):
                    raise ValueError("Setup source storage contains a link or special file")

    def check(self, headroom: int = 0) -> None:
        if self.failure is not None:
            raise self.failure
        size = count = 0
        with directory_fd(self.root) as descriptor:
            for info in self._scan(descriptor):
                count += 1
                size += max(info.st_size, info.st_blocks * 512)
                if size > self.maximum or count > self.entries:
                    raise ValueError("Aggregate setup source storage exceeds its limit")
        if size + headroom > self.maximum:
            raise ValueError("Aggregate setup source storage has insufficient headroom")
        if shutil.disk_usage(self.root).free < headroom + self.reserve:
            raise ValueError("Insufficient controller disk headroom for setup sources")

    def _watch(self) -> None:
        while not self.stopped.wait(_POLL_SECONDS):
            try:
                self.check()
            except FileNotFoundError:
                continue  # A temporary directory disappeared during the scan.
            except Exception as exc:
                self.failure = exc
                return

    @contextmanager
    def monitor(self) -> Iterator[None]:
        watcher = threading.Thread(target=self._watch, daemon=True)
        watcher.start()
        try:
            yield
            self.check()
        finally:
            self.stopped.set()
            watcher.join()
        if self.failure is not None:
            raise self.failure


def _stop(process: subprocess.Popen[bytes]) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass


def _run(
    command: Sequence[str], *, cwd: str, env: Mapping[str, str],
    input_data: bytes | None, timeout: float, max_output_bytes: int,
    file_size_limit: int | None, storage: _Storage,
) -> bytes:
    """Bound Git time/output/files, checking aggregate disk while it runs."""
    if os.name != "posix" or not sys.executable:
        raise NotImplementedError("Bounded setup source preparation requires POSIX Python")
    selected = max(file_size_limit or 0, max_output_bytes, 1)
    # A fresh isolated interpreter sets limits before exec. Python preexec_fn
    # would be unsafe while the storage monitor thread is running.
    launch = [sys.executable, "-I", "-S", "-c", _LIMIT_EXEC, str(selected), *command]
    # Keep the spool named so aggregate scans account for its allocated blocks.
    # A zero output bound follows CommandRunner's discard-stdout contract.
    output_context = (tempfile.NamedTemporaryFile(prefix=".git-output-", dir=storage.root)
                      if max_output_bytes > 0 else nullcontext(None))
    with output_context as output:
        process = subprocess.Popen(
            launch, cwd=cwd, env=dict(env),
            stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
            stdout=output if output is not None else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True, umask=0o077,
        )
        deadline = time.monotonic() + timeout
        try:
            first = True
            while True:
                storage.check()
                if output is not None and output.tell() > max_output_bytes:
                    raise ValueError("Git setup source output exceeds its limit")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Git setup source command timed out")
                try:
                    process.communicate(input=input_data if first else None,
                                        timeout=min(_POLL_SECONDS, remaining))
                    break
                except subprocess.TimeoutExpired:
                    first = False
            if process.returncode != 0:
                raise ValueError("Git setup source command failed or exceeded its file limit")
            if output is None:
                return b""
            output.seek(0)
            result = output.read(max_output_bytes + 1)
            if len(result) > max_output_bytes:
                raise ValueError("Git setup source output exceeds its limit")
            return result
        except BaseException:
            _stop(process)
            raise


def _environment(home: str) -> dict[str, str]:
    return {
        "PATH": os.defpath, "HOME": home, "XDG_CONFIG_HOME": home,
        "LANG": "C", "LC_ALL": "C", "TMPDIR": home,
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_ATTR_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/bin/false",
        "SSH_ASKPASS": "/bin/false", "GIT_SSH_COMMAND": "/bin/false",
        "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_NO_LAZY_FETCH": "1", "GIT_LFS_SKIP_SMUDGE": "1",
        "GIT_PAGER": "cat",
    }


def _manifest(repository: str) -> tuple[Manifest, str]:
    path = os.path.join(repository, MANIFEST_FILENAME)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        raise ValueError(f"Build setup requires an explicit tracked {MANIFEST_FILENAME}") from None
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Build setup manifest must be a regular file")
        content = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(content) > MAX_MANIFEST_BYTES:
        raise ValueError("Build setup manifest exceeds its byte limit")
    # Parse the exact bytes being hashed, rather than reopening a mutable path.
    return parse_manifest(parse_json(content)), hashlib.sha256(content).hexdigest()


def prepare_setup_sources(
    deploy_specs: list[list[str]], destination: str, *,
    limits: SourceLimits = DEFAULT_SOURCE_LIMITS,
    runner: CommandRunner | None = None,
) -> list[PreparedSource]:
    """Prepare 1..16 public HTTPS plans under an existing empty owned 0700 root.

    The caller owns this directory's lifecycle on success and failure. No build,
    cache, credential lookup, SSH, LFS or submodule operation is performed.
    ``runner`` has the source-snapshot runner contract and is for mocked tests.
    """
    if not isinstance(limits, SourceLimits):
        raise ValueError("Source limits must use SourceLimits")
    plans = _plans(deploy_specs)
    if not isinstance(destination, str):
        raise ValueError("Source preparation destination must be a text path")
    validate_filesystem_path(destination, must_exist=True)
    root = os.path.abspath(destination)
    with directory_fd(root) as descriptor:
        info = os.fstat(descriptor)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError("Source preparation destination must be caller-owned mode 0700")
        if os.listdir(descriptor):
            raise ValueError("Source preparation destination must be empty")

    storage = _Storage(root, limits)
    # One aggregate budget across every clone, checkout, snapshot and temporary.
    storage.check()
    home = os.path.join(root, "home")
    template = os.path.join(root, "empty-template")
    os.mkdir(home, 0o700)
    os.mkdir(template, 0o700)
    environment = _environment(home)
    deadline = time.monotonic() + limits.command_timeout_seconds * (1 + len(plans))

    def bounded_runner(command, *, cwd, env, input_data, timeout,
                       max_output_bytes, file_size_limit=None):
        storage.check()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Setup source preparation timed out")
        # Apply these even to the snapshot backend's isolated metadata commands.
        safe_command = [command[0], "-c", f"core.hooksPath={template}",
                        "-c", "core.fsmonitor=false", "-c", "core.attributesFile=/dev/null",
                        "-c", "credential.helper=", "-c", "credential.interactive=false",
                        "-c", "core.askPass=/bin/false", "-c", "protocol.allow=never",
                        "-c", "protocol.https.allow=always", "-c", "http.followRedirects=false",
                        "-c", "gc.auto=0", "-c", "maintenance.auto=false", *command[1:]]
        safe_env = dict(environment)
        # The backend uses this validated pinned attribute source for built-in conversions.
        if "GIT_ATTR_SOURCE" in env:
            safe_env["GIT_ATTR_SOURCE"] = env["GIT_ATTR_SOURCE"]
        kwargs = dict(cwd=cwd, env=safe_env, input_data=input_data,
                      timeout=command_timeout(min(timeout, remaining)),
                      max_output_bytes=max_output_bytes,
                      file_size_limit=file_size_limit or limits.max_history_bytes)
        if runner is None:
            result = _run(safe_command, storage=storage, **kwargs)
        else:
            result = runner(safe_command, **kwargs)
        storage.check()
        if not isinstance(result, bytes) or len(result) > max_output_bytes:
            raise ValueError("Git setup source returned invalid or oversized output")
        return result

    def git(repository: str, args: Sequence[str], maximum: int = 4096) -> bytes:
        return bounded_runner(["git", "-C", repository, *args], cwd=repository,
                              env=environment, input_data=None,
                              timeout=limits.command_timeout_seconds,
                              max_output_bytes=maximum)

    repositories: dict[str, str] = {}
    selections: dict[tuple[str, str | None], str] = {}
    snapshots: dict[tuple[str, str], tuple[Manifest, dict[str, str], str]] = {}
    prepared = []
    with storage.monitor():
        for target, url, ref in plans:
            if url not in repositories:
                storage.check(limits.max_history_bytes + limits.max_source_bytes)
                repository = os.path.join(root, f"repository-{len(repositories)}")
                os.mkdir(repository, 0o700)
                try:
                    # Bare clone retains exact refs/heads and refs/tags, including
                    # a literal branch named HEAD; no ambiguous remote alias lookup.
                    git(repository, ["clone", "--bare", "--no-checkout",
                                     "--no-recurse-submodules", f"--template={template}",
                                     "--", url, os.path.join(repository, ".git")])
                except ValueError as exc:
                    raise ValueError("Public HTTPS clone failed; private repositories and credentials "
                                     f"are unsupported for build setup ({exc})") from None
                git(repository, ["config", "--local", "core.bare", "false"])
                repositories[url] = repository
                # Freeze every selection before any detached checkout changes HEAD.
                for selected_ref in dict.fromkeys(item[2] for item in plans if item[1] == url):
                    revision_spec = "HEAD"
                    if selected_ref is not None:
                        # Even refs/... names undergo rev-parse's DWIM fallback.
                        # Verify the exact namespace, then peel only its object ID.
                        object_raw = git(repository, ["show-ref", "--verify", "--hash",
                                                      selected_ref], 80)
                        if _REVISION.fullmatch(object_raw) is None:
                            raise ValueError("Git did not return a full explicit ref object ID")
                        revision_spec = object_raw[:-1].decode("ascii")
                    raw = git(repository, ["rev-parse", "--verify",
                                           revision_spec + "^{commit}"], 80)
                    if _REVISION.fullmatch(raw) is None:
                        raise ValueError("Git did not return a full pinned commit")
                    selections[url, selected_ref] = raw[:-1].decode("ascii")
            repository = repositories[url]
            selection = (url, ref)
            revision = selections[selection]
            key = (url, revision)
            if key not in snapshots:
                storage.check(limits.max_source_bytes)
                git(repository, ["checkout", "--no-recurse-submodules", "--detach", "--force",
                                 revision])
                manifest, manifest_digest = _manifest(repository)
                storage.check(limits.max_snapshot_bytes + limits.max_history_bytes)
                path = os.path.join(root, f"source-{len(snapshots)}.snapshot")
                source = create_source_snapshot(repository, path, limits=limits, runner=bounded_runner)
                if (source["revision"] != revision
                        or source["manifest_sha256"] != manifest_digest):
                    raise ValueError("Pinned snapshot and parsed manifest identity differ")
                snapshots[key] = manifest, source, path
            manifest, source, path = snapshots[key]
            # Consumers can render a manifest without mutating another destination's input.
            prepared.append(PreparedSource(target, deepcopy(manifest), dict(source), path))
    return prepared
