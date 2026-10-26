#!/usr/bin/env python3
"""Root-controlled SSH entrypoint for externally built target deployments.

This is an internal transport boundary. Setup, a public CLI, listener and
service installation are intentionally outside this entrypoint.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stdout
import os
from pathlib import Path
import platform
import re
import signal
import stat
import sys
from typing import BinaryIO, Iterator


DEFAULT_CONFIG = "/etc/basaltwater/build-target/config.json"
SSH_COMMAND = "basaltwater-target-v1"
CONNECTION_SECONDS = 900
CONFIG_OVERHEAD_BYTES = 64 * 1024
MAX_RECORDS = 4096
MIN_BYTES = 256 * 1024**2
MAX_BYTES = 8 * 1024**4
_ROOTS = frozenset({"admission", "artifacts", "releases", "verify"})
_PRINCIPAL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}")
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _trusted_code(path: Path, *, directory: bool = False) -> None:
    """Check the installed entrypoint and every ancestor before imports."""
    for component in (*reversed(path.parents), path):
        info = component.lstat()
        if info.st_uid != 0 or stat.S_ISLNK(info.st_mode):
            raise ValueError("Target code must be controlled by root")
        if info.st_mode & 0o022:
            if component == path or not (stat.S_ISDIR(info.st_mode) and info.st_mode & stat.S_ISVTX):
                raise ValueError("Target code has a writable ancestor")
        if component != path and not stat.S_ISDIR(info.st_mode):
            raise ValueError("Invalid target code ancestor")
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise ValueError("Target code has an invalid file type")


_LIBRARY_PACKAGES = (
    "lib", "common", "web", "plugins", "security", "sync", "smb", "desktop", "game", "deploy",
)


def _trusted_libraries(root: Path) -> None:
    """Cover the same importable package tree as the builder entrypoint."""
    def walk_error(error: OSError) -> None:
        raise error

    for package in _LIBRARY_PACKAGES:
        path = root / package
        _trusted_code(path, directory=True)
        for current, directories, files in os.walk(path, followlinks=False, onerror=walk_error):
            for name in directories:
                _trusted_code(Path(current) / name, directory=True)
            for name in files:
                _trusted_code(Path(current) / name)


def _positive(value: object, maximum: int, label: str, *, minimum: int = 1) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("Invalid target " + label)
    return value


def _overlap(left: str, right: str) -> bool:
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def parse_config(value: object) -> dict:
    """Validate detached target policy, private roots and storage ceilings."""
    from lib.build_target_policy import parse_target_policy
    from lib.validation import validate_filesystem_path

    if not isinstance(value, dict) or set(value) != {
        "schema", "roots", "policy", "max_records", "max_bytes",
    }:
        raise ValueError("Invalid target configuration fields")
    if type(value["schema"]) is not int or value["schema"] != 1:
        raise ValueError("Unsupported target configuration")

    roots = value["roots"]
    if not isinstance(roots, dict) or set(roots) != _ROOTS:
        raise ValueError("Invalid target roots")
    for path in roots.values():
        if not isinstance(path, str):
            raise ValueError("Invalid target root")
        validate_filesystem_path(path, must_exist=False)
        if (not os.path.isabs(path) or path.startswith("//")
                or os.path.normpath(path) != path or path == "/"
                or any(char.isspace() or char in "%:" for char in path)):
            raise ValueError("Invalid target root")
    paths = sorted(roots.values())
    if any(_overlap(first, second)
           for index, first in enumerate(paths) for second in paths[index + 1:]):
        raise ValueError("Target roots must be distinct non-overlapping directories")

    policy = parse_target_policy(value["policy"])
    deployment_roots = [item["deployment_root"]
                        for item in policy["applications"].values()]
    if any(_overlap(private, deployment)
           for private in roots.values() for deployment in deployment_roots):
        raise ValueError("Target private roots cannot overlap deployment roots")

    return {
        "schema": 1,
        "roots": dict(roots),
        "policy": policy,
        "max_records": _positive(value["max_records"], MAX_RECORDS, "record limit"),
        "max_bytes": _positive(value["max_bytes"], MAX_BYTES, "storage limit", minimum=MIN_BYTES),
    }


@contextmanager
def _private_directory(path: str) -> Iterator[int]:
    """Open a root-owned private directory through trusted no-follow parents."""
    from lib.validation import validate_filesystem_path

    validate_filesystem_path(path)
    if (not isinstance(path, str) or not path.startswith("/") or path.startswith("//")
            or os.path.normpath(path) != path):
        raise ValueError("Target private directory must be a canonical absolute path")
    descriptor = os.open("/", _DIRECTORY)
    try:
        parent = os.fstat(descriptor)
        for part in path.split("/")[1:]:
            if not part:
                continue
            child = os.open(part, _DIRECTORY, dir_fd=descriptor)
            try:
                info = os.fstat(child)
                if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0
                        or parent.st_uid != 0):
                    raise ValueError("Target private directory has an untrusted owner")
                if parent.st_mode & 0o022 and not parent.st_mode & stat.S_ISVTX:
                    raise ValueError("Target private directory has a writable ancestor")
            except BaseException:
                os.close(child)
                raise
            os.close(descriptor)
            descriptor, parent = child, info
        if parent.st_uid != 0 or stat.S_IMODE(parent.st_mode) != 0o700:
            raise ValueError("Target private directory must be root-owned mode 0700")
        yield descriptor
    finally:
        os.close(descriptor)


def _file_identity(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_uid, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def load_config(path: str = DEFAULT_CONFIG) -> dict:
    """Read the bounded root-owned configuration and validate all storage roots."""
    from lib.build_target_policy import MAX_POLICY_BYTES
    from lib.build_protocol import decode_document
    from lib.validation import validate_filesystem_path

    validate_filesystem_path(path)
    if (not isinstance(path, str) or not path.startswith("/") or path.startswith("//")
            or os.path.normpath(path) != path or path.endswith("/")):
        raise ValueError("Target configuration path must be canonical and absolute")
    maximum = MAX_POLICY_BYTES + CONFIG_OVERHEAD_BYTES
    parent_path, name = os.path.split(path)
    with _private_directory(parent_path) as parent_fd:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_fd,
        )
        try:
            before = os.fstat(descriptor)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != 0 or before.st_nlink != 1
                    or stat.S_IMODE(before.st_mode) != 0o600 or before.st_size > maximum):
                raise ValueError("Target configuration is not a bounded private root-owned file")
            raw = os.read(descriptor, maximum + 1)
            after = os.fstat(descriptor)
            if (len(raw) != before.st_size or len(raw) > maximum
                    or _file_identity(before) != _file_identity(after)):
                raise ValueError("Target configuration changed during reading")
        finally:
            os.close(descriptor)

    configured = parse_config(decode_document(raw, maximum=maximum))
    for root in configured["roots"].values():
        with _private_directory(root):
            pass
    return configured


def require_platform() -> None:
    """Restrict this target activation slice to its initial qualified platform."""
    release = platform.freedesktop_os_release()
    if (platform.system() != "Linux" or platform.machine() != "x86_64"
            or release.get("ID") != "debian" or release.get("VERSION_ID") != "13"):
        raise ValueError("Target deployments require Debian 13 x86-64")


def current_policy_loader(config: dict, *, config_path: str):
    """Reload policy each time and reject structural changes during a request."""
    structure = (
        config["schema"], tuple(sorted(config["roots"].items())),
        config["max_records"], config["max_bytes"],
    )

    def load_policy() -> dict:
        current = load_config(config_path)
        observed = (
            current["schema"], tuple(sorted(current["roots"].items())),
            current["max_records"], current["max_bytes"],
        )
        if observed != structure:
            raise PermissionError("Target configuration changed; restart after draining")
        return current["policy"]

    return load_policy


@contextmanager
def connection_deadline(seconds: int = CONNECTION_SECONDS) -> Iterator[None]:
    """Bound the complete SSH request, including framed artifact transfer."""
    def expired(_signal: int, _frame: object) -> None:
        raise TimeoutError("Target connection expired")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


@contextmanager
def _protocol_stdout() -> Iterator[BinaryIO]:
    """Keep the wire private while Python and inherited fd1 use diagnostics."""
    stdout = sys.stdout
    inheritable = os.get_inheritable(1)
    # os.dup() returns a non-inheritable descriptor: exec'd helpers must not
    # receive another way to write to the protocol channel.
    descriptor = os.dup(1)
    try:
        output_stream = os.fdopen(descriptor, "wb", buffering=0)
    except BaseException:
        os.close(descriptor)
        raise
    with output_stream:
        try:
            os.dup2(2, 1)
            stdout.flush()
            with redirect_stdout(sys.stderr):
                yield output_stream
        finally:
            # Cached references to the original Python stdout still use fd1.
            # Drain their diagnostics before restoring the transport descriptor,
            # even when serving or flushing raises (including interruption).
            try:
                stdout.flush()
            finally:
                os.dup2(descriptor, 1, inheritable=inheritable)


def serve(config: dict, *, principal: str, config_path: str) -> None:
    """Compose target admission, artifact verification and activation for SSH."""
    input_stream = sys.stdin.buffer
    with connection_deadline():
        with _protocol_stdout() as output_stream:
            from lib.build_admission import DeploymentAdmissionStore
            from lib.build_target_activation import TargetReleaseActivator
            from lib.build_target_artifacts import BuildTargetArtifactStore
            from lib.build_target_service import BuildTargetService

            roots = config["roots"]
            admissions = DeploymentAdmissionStore(os.path.join(roots["admission"], "admissions.json"))
            artifacts = BuildTargetArtifactStore(
                roots["artifacts"],
                max_records=config["max_records"], max_bytes=config["max_bytes"],
            )
            activator = TargetReleaseActivator(
                roots["releases"], roots["verify"],
                open_artifact=artifacts.open_verified_bundle,
            )
            service = BuildTargetService(
                admissions=admissions,
                load_policy=current_policy_loader(config, config_path=config_path),
                stage_artifact=artifacts.stage,
                activate_release=activator,
            )
            service.serve_once(principal=principal, input_stream=input_stream,
                               output_stream=output_stream)


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise ValueError("Invalid target invocation")

    def exit(self, status: int = 0, _message: str | None = None) -> None:
        if status:
            raise ValueError("Invalid target invocation")
        raise ValueError("Invalid target invocation")


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = _ArgumentParser(description="Serve one protected target deployment request",
                             add_help=False)
    parser.add_argument("--serve", action="store_true", required=True)
    parser.add_argument("--principal", required=True)
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Validate the fixed forced command, then serve exactly one request."""
    try:
        args = _arguments(argv)
        if (os.geteuid() != 0 or not sys.flags.isolated
                or os.environ.get("SSH_ORIGINAL_COMMAND") != SSH_COMMAND
                or not _PRINCIPAL.fullmatch(args.principal)):
            raise ValueError("Invalid target invocation")

        script = Path(os.path.abspath(__file__))
        _trusted_code(script)
        root = script.parents[2]
        _trusted_libraries(root)
        sys.path.insert(0, str(root))
        require_platform()
        from lib.build_target_maintenance import target_lease

        # The shared lease begins before the first configuration read and is
        # retained through the complete typed operation and response. Requests
        # arriving during enrollment refuse immediately instead of waiting
        # with stale authorization. The durable gate also rejects failed setup.
        with target_lease():
            config = load_config(args.config)
            serve(config, principal=args.principal, config_path=args.config)
        return 0
    except BaseException:
        print("Target operation is unavailable; inspect protected host evidence", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
