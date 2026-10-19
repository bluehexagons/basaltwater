"""Controller transport for an existing, non-root CachyOS desktop account.

Only source and the requested setup arguments cross this boundary. Controller
credentials, root server bootstrap, and agent forwarding are never involved.
"""

from __future__ import annotations

import argparse
from copy import copy
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid

from lib.config import SetupConfig
from lib.ssh_utils import build_ssh_command, ssh_batch_mode
from lib.streamed_process import run_streamed
from lib.validation import validate_filesystem_path, validate_positive_integer
from lib.validators import validate_host, validate_username


ARGS_NAME = ".remote_setup_args.json"
MAX_SOURCE_BYTES = 128 * 1024 * 1024


def controller_config(args: argparse.Namespace) -> SetupConfig:
    """Validate using the local profile contract without inspecting this host."""
    from lib.cachyos import LOCAL_HOSTS, cachyos_config_from_args

    if not validate_host(args.host) or args.host in LOCAL_HOSTS:
        raise ValueError("Remote CachyOS setup requires a valid remote HOST")
    if not validate_username(args.username) or args.username == "root":
        raise ValueError("Specify the existing non-root desktop USER after HOST")
    if getattr(args, "restart_if_needed", False) or getattr(args, "wait_for_restart", False):
        raise ValueError("Remote CachyOS setup does not support the Debian restart workflow")
    copied = argparse.Namespace(**vars(args))
    copied.host = "localhost"
    # Identity belongs to the transport, never to the target profile/payload.
    copied.ssh_key = None
    if getattr(copied, "install_ssh", None) is None:
        copied.install_ssh = True
    return cachyos_config_from_args(copied)


def _identity_path(value: str | None) -> str | None:
    if value is None:
        return None
    path = os.path.abspath(os.path.expanduser(value))
    validate_filesystem_path(path, must_exist=True)
    if not os.path.isfile(path) or not os.access(path, os.R_OK):
        raise ValueError("SSH identity must be a readable regular file")
    return path


def _source_archive(config: SetupConfig, directory: str) -> bytes:
    from lib.setup_common import (
        _expand_remote_args, _write_remote_args_file,
        copy_project_files, create_tar_from_dir,
    )

    copy_project_files(directory)
    # Generic server serialization infers --lan-access on the controller.
    # Desktop LAN discovery must instead happen on the actual workstation.
    serialized = copy(config)
    serialized.lan_access = False
    tokens = _expand_remote_args(serialized.to_remote_args())
    if config.lan_access:
        tokens.append("--lan-access")
    _write_remote_args_file(directory, tokens)
    total = 0
    for current, _directories, files in os.walk(directory):
        for name in files:
            total += os.stat(os.path.join(current, name)).st_size
            if total > MAX_SOURCE_BYTES:
                raise ValueError("CachyOS source upload exceeds 128 MiB")
    data = create_tar_from_dir(directory)
    if len(data) > MAX_SOURCE_BYTES:
        raise ValueError("CachyOS source upload exceeds 128 MiB")
    return data


def _ssh(host: str, username: str, key: str | None, command: str) -> list[str]:
    argv = build_ssh_command(host, username, key, remote_command=command,
                             allow_host_key_prompt=True)
    # Override user SSH config too: this path never forwards controller agents.
    argv[1:1] = ["-o", "ForwardAgent=no", "-o", "ClearAllForwardings=yes"]
    return argv


def run_cachyos_remote_command(args: argparse.Namespace) -> int:
    """Upload privately, then run target setup with the user's terminal intact."""
    try:
        config = controller_config(args)
        key = _identity_path(getattr(args, "ssh_key", None))
        timeout = validate_positive_integer(
            os.environ.get("BASALTWATER_SETUP_TIMEOUT", "14400"), "Setup timeout",
        )
        if config.dry_run:
            print(f"[DRY-RUN] CachyOS setup as {args.username}@{args.host}; "
                  f"SSH {'enabled' if config.install_ssh else 'disabled'}")
            print("[DRY-RUN] Would upload source privately and run remote_setup.py as the existing user; "
                  "CachyOS x86_64, account/HOME, and selected GUI session checks run on apply")
            print("[DRY-RUN] No SSH connection or target mutation was performed")
            return 0
        with tempfile.TemporaryDirectory(prefix="basaltwater-cachyos-build-") as build:
            data = _source_archive(config, build)
        stage = "/tmp/.basaltwater-cachyos-" + uuid.uuid4().hex
        quoted = shlex.quote(stage)
        lease = shlex.quote(stage + "/.lease-" + uuid.uuid4().hex)
        cleanup = f"if [ -f {lease} ] && [ ! -L {quoted} ]; then rm -rf -- {quoted}; fi"
        # Exclusive creation in /tmp avoids trusting target stdout or $TMPDIR.
        # A failed upload owns its cleanup; the controller also retries below.
        upload = (
            f"set -eu; umask 077; mkdir -m 700 -- {quoted}; "
            f"trap 'rm -rf -- {quoted}' EXIT HUP INT TERM; "
            f"touch {lease}; "
            f"tar xzf - -C {quoted} --no-same-owner --no-same-permissions; "
            "trap - EXIT HUP INT TERM"
        )
        finish = (
            "setup_result=$?; trap - EXIT HUP INT TERM; "
            f"if ! {{ {cleanup}; }}; then "
            "echo 'Could not remove private setup staging' >&2; "
            '[ "$setup_result" -ne 0 ] || setup_result=1; fi; exit "$setup_result"'
        )
        execute = (
            f"set -eu; trap {shlex.quote(finish)} EXIT; "
            "trap 'exit 129' HUP; trap 'exit 130' INT; trap 'exit 143' TERM; "
            f"cd {quoted}; export PYTHONDONTWRITEBYTECODE=1; "
            f"python3 -u -m lib.cachyos_remote --target-args {quoted}/{ARGS_NAME}"
        )

        def bounded(command: str, seconds: int) -> str:
            return shlex.join([
                "timeout", "--signal=TERM", "--kill-after=10s", str(seconds),
                "/bin/sh", "-c", command,
            ])

        status = 1
        completed = False
        try:
            uploaded = run_streamed(
                _ssh(args.host, args.username, key, bounded(upload, 300)),
                timeout=310, input_data=data,
                on_output=lambda line: print(line, end=""),
                interactive=not ssh_batch_mode(),
            )
            if uploaded:
                return uploaded
            command = _ssh(args.host, args.username, key, bounded(execute, timeout))
            if sys.stdin.isatty():
                command.insert(1, "-t")
            # Inherit terminal stdin/stdout so sudo inside individual target
            # steps can prompt. The Python setup process itself stays non-root.
            status = subprocess.run(command, check=False, timeout=timeout + 15).returncode
            # A definitive remote result includes its EXIT cleanup. A new SSH
            # connection would fail after an intentional --no-ssh handover.
            completed = 0 <= status < 255
        finally:
            if not completed:
                try:
                    result = subprocess.run(
                        _ssh(args.host, args.username, key, bounded(cleanup, 30)),
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, timeout=45, check=False,
                    )
                    if result.returncode:
                        print(f"Warning: Could not verify private staging cleanup at {stage}", file=sys.stderr)
                        status = status or 1
                except (OSError, subprocess.SubprocessError):
                    print(f"Warning: Could not verify private staging cleanup at {stage}", file=sys.stderr)
                    status = status or 1
        return status
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        # Subprocess exceptions can contain commands/output; keep webhook
        # arguments and arbitrary remote diagnostics out of controller logs.
        print(f"Error: Remote CachyOS setup failed ({type(exc).__name__})")
        if isinstance(exc, ValueError):
            print(str(exc))
        return 1


def _private_directory(path: Path) -> None:
    """Reject symlinks and foreign/writable parents before runtime publication."""
    validate_filesystem_path(str(path))
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise ValueError(f"Unsafe user runtime directory: {path}")


@contextmanager
def _target_runtime(source: Path):
    """Keep service helper paths stable after the temporary upload is removed."""
    home = Path.home()
    root = home
    # Keep snapshots outside the installer's managed Git checkout: its cleanup
    # intentionally removes untracked source and would delete live helpers.
    for component in (".local", "share", "basaltwater-cachyos-remote"):
        root = root / component
        _private_directory(root)
    descriptor = os.open(root / "setup.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise ValueError("Unsafe CachyOS remote setup lock")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        runtime, previous = root / "runtime", root / "previous"
        for path in (runtime, previous):
            if path.exists() or path.is_symlink():
                _private_directory(path)
        if previous.exists():
            if not runtime.exists():
                previous.rename(runtime)
            else:
                shutil.rmtree(previous)
        with tempfile.TemporaryDirectory(prefix="incoming-", dir=root) as incoming:
            staged = Path(incoming) / "runtime"
            shutil.copytree(source, staged, ignore=shutil.ignore_patterns(
                ARGS_NAME, "__pycache__", ".lease-*",
            ))
            # copytree preserves source permissions, including a writable root.
            # Publish a private runtime that passes the next run's safety check.
            staged.chmod(0o700)
            if runtime.exists():
                runtime.rename(previous)
            try:
                staged.rename(runtime)
            except BaseException:
                if previous.exists():
                    previous.rename(runtime)
                raise
        if previous.exists():
            shutil.rmtree(previous)
        yield runtime
    finally:
        os.close(descriptor)


def _ensure_launcher(runtime: Path) -> None:
    """Install the user command on new targets, preserving existing installers."""
    from lib.orchestrator_bootstrap import install_launcher

    directory = Path.home() / ".local/bin"
    _private_directory(directory)
    if not os.path.lexists(directory / "basaltw"):
        install_launcher(str(runtime / "basaltwater.py"), target_dir=str(directory))


def main() -> int:
    """Target entry point; preflight before publishing any persistent source."""
    from lib.arg_parser import create_setup_argument_parser
    from lib.cachyos import cachyos_config_from_args, preflight_cachyos
    from remote_setup import _load_args_file

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-args", required=True)
    args = parser.parse_args()
    validate_filesystem_path(args.target_args, must_exist=True)
    # Validate and consume private arguments before making a persistent copy.
    tokens = _load_args_file(args.target_args)
    setup_parser = create_setup_argument_parser("CachyOS remote setup", for_remote=True, allow_steps=True)
    parsed = setup_parser.parse_args(tokens)
    parsed.host = "localhost"
    config = cachyos_config_from_args(parsed, for_remote=True)
    preflight_cachyos(config)
    source = Path(__file__).resolve().parent.parent
    from lib.atomic_io import write_json_atomic

    write_json_atomic(args.target_args, tokens, mode=0o600)
    with _target_runtime(source) as runtime:
        _ensure_launcher(runtime)
        return subprocess.run([
            sys.executable, "-u", str(runtime / "remote_setup.py"),
            "--args-file", args.target_args,
        ], check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
