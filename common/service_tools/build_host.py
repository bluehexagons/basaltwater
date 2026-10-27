#!/usr/bin/env python3
"""Root-controlled entrypoint for typed SSH requests and an independent worker.

Setup must install this file and its libraries outside account-writable trees.
The SSH key's forced command fixes the principal; stdin never supplies it.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import grp
import json
import os
from pathlib import Path
import platform
import pwd
import shutil
import signal
import stat
import subprocess
import sys
import time
from typing import Iterator


DEFAULT_CONFIG = "/etc/basaltwater/build-host/config.json"
BUILD_ACCOUNT = "basaltwater-build"
SSH_COMMAND = "basaltwater-build-v1"
CONNECTION_SECONDS = 900
TOOL_PATH = "/usr/local/go/bin:/usr/local/bin:/usr/bin:/bin"
_ROOTS = {"state", "inputs", "scratch", "artifacts", "logs"}


def _trusted_code(path: Path, *, directory: bool = False) -> None:
    """Check the installed helper before importing any project code."""
    for component in (*reversed(path.parents), path):
        info = component.lstat()
        if info.st_uid != 0 or stat.S_ISLNK(info.st_mode):
            raise ValueError("Builder code must be controlled by root")
        if info.st_mode & 0o022:
            if component == path or not (stat.S_ISDIR(info.st_mode) and info.st_mode & stat.S_ISVTX):
                raise ValueError("Builder code has a writable ancestor")
        if component != path and not stat.S_ISDIR(info.st_mode):
            raise ValueError("Invalid builder code ancestor")
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise ValueError("Builder code has an invalid file type")


def _trusted_libraries(root: Path) -> None:
    """Include importable packages and bytecode in the installed-code boundary."""
    def walk_error(error):
        raise error

    for package in ("lib", "common", "web", "plugins", "security", "sync",
                    "smb", "desktop", "game", "deploy"):
        path = root / package
        _trusted_code(path, directory=True)
        for current, directories, files in os.walk(path, followlinks=False, onerror=walk_error):
            for name in directories:
                _trusted_code(Path(current) / name, directory=True)
            for name in files:
                _trusted_code(Path(current) / name)


def _positive(value: object, maximum: int, label: str, *, minimum: int = 1) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("Invalid builder " + label)
    return value


def parse_config(value: object) -> dict:
    """Validate administrator configuration, keeping all paths out of requests."""
    from lib.build_host_policy import parse_build_host_policy
    from lib.validation import validate_filesystem_path

    if not isinstance(value, dict) or set(value) != {
        "schema", "uid", "gid", "roots", "policy", "max_records", "max_bytes",
    }:
        raise ValueError("Invalid builder configuration fields")
    if type(value["schema"]) is not int or value["schema"] != 1:
        raise ValueError("Unsupported builder configuration")
    roots = value["roots"]
    if not isinstance(roots, dict) or set(roots) != _ROOTS:
        raise ValueError("Invalid builder roots")
    for path in roots.values():
        if not isinstance(path, str):
            raise ValueError("Invalid builder root")
        validate_filesystem_path(path, must_exist=False)
        if (not os.path.isabs(path) or os.path.normpath(path) != path or path == "/"
                or any(char.isspace() or char in "%:" for char in path)):
            raise ValueError("Invalid builder root")
    paths = sorted(roots.values())
    if any(first == second or second.startswith(first + "/")
           for index, first in enumerate(paths) for second in paths[index + 1:]):
        raise ValueError("Builder roots must be distinct non-overlapping directories")
    return {
        "schema": 1,
        "uid": _positive(value["uid"], 2**31 - 1, "UID"),
        "gid": _positive(value["gid"], 2**31 - 1, "GID"),
        "roots": dict(roots),
        "policy": parse_build_host_policy(value["policy"]),
        "max_records": _positive(value["max_records"], 4096, "record limit"),
        "max_bytes": _positive(value["max_bytes"], 8 * 1024**4, "storage limit",
                               minimum=256 * 1024**2),
    }


def load_config(path: str = DEFAULT_CONFIG) -> dict:
    """Read a private, no-follow root-owned configuration and trusted roots."""
    from lib.build_host_policy import MAX_POLICY_BYTES
    from lib.build_protocol import decode_document
    from lib.build_worker import _private_root

    maximum = MAX_POLICY_BYTES + 65536
    with _private_root(os.path.dirname(path)) as (_parent, directory):
        descriptor = os.open(os.path.basename(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=directory)
        try:
            before = os.fstat(descriptor)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != 0 or before.st_nlink != 1
                    or stat.S_IMODE(before.st_mode) != 0o600 or before.st_size > maximum):
                raise ValueError("Builder configuration is not a private root-owned file")
            with os.fdopen(os.dup(descriptor), "rb") as stream:
                raw = stream.read(maximum + 1)
            after = os.fstat(descriptor)
            identity = lambda info: (info.st_dev, info.st_ino, info.st_size,
                                     info.st_mtime_ns, info.st_ctime_ns)
            if len(raw) != before.st_size or identity(before) != identity(after):
                raise ValueError("Builder configuration changed during reading")
        finally:
            os.close(descriptor)
    config = parse_config(decode_document(raw, maximum=maximum))
    require_build_account(config)
    for root in config["roots"].values():
        with _private_root(root):
            pass
    return config


def require_build_account(config: dict) -> None:
    """Bind numeric execution IDs to the dedicated non-login build identity."""
    account = pwd.getpwnam(BUILD_ACCOUNT)
    group = grp.getgrnam(BUILD_ACCOUNT)
    if (account.pw_name != BUILD_ACCOUNT or group.gr_name != BUILD_ACCOUNT
            or account.pw_uid != config["uid"] or account.pw_gid != config["gid"]
            or group.gr_gid != config["gid"] or account.pw_uid <= 0 or account.pw_gid <= 0
            or account.pw_dir != "/nonexistent"
            or account.pw_shell not in {"/usr/sbin/nologin", "/sbin/nologin"}
            or any(member != BUILD_ACCOUNT for member in group.gr_mem)):
        raise ValueError("Builder IDs must match the dedicated non-login build account")
    if any(other.pw_name != BUILD_ACCOUNT
           and (other.pw_uid == account.pw_uid or other.pw_gid == account.pw_gid)
           for other in pwd.getpwall()):
        raise ValueError("The build UID and primary GID cannot be shared with another account")
    if any(other.gr_gid == group.gr_gid and other.gr_name != BUILD_ACCOUNT
           for other in grp.getgrall()):
        raise ValueError("The build GID cannot have another group alias")


def require_platform() -> None:
    """Fail before admission or execution on an unqualified host platform."""
    release = platform.freedesktop_os_release()
    if (platform.system() != "Linux" or platform.machine() != "x86_64"
            or release.get("ID") != "debian" or release.get("VERSION_ID") != "13"):
        raise ValueError("On-demand builds require Debian 13 x86-64")


def observe_tools() -> dict[str, str]:
    """Probe fixed installed tools without repository configuration or commands."""
    result = {}
    environment = {"PATH": TOOL_PATH, "HOME": "/nonexistent", "LANG": "C.UTF-8",
                   "GOTOOLCHAIN": "local", "GOENV": "off"}
    for name, command in (("go", "go"), ("node", "node"), ("python", "python3")):
        executable = shutil.which(command, path=TOOL_PATH)
        if executable is None:
            continue
        try:
            completed = subprocess.run(
                [executable, "version" if name == "go" else "--version"],
                cwd="/", env=environment, stdin=subprocess.DEVNULL, capture_output=True,
                timeout=10, check=False,
            )
            raw = completed.stdout.strip()
            version = raw.decode("utf-8", errors="strict")
            if (completed.returncode == 0 and 0 < len(raw) <= 1024
                    and not any(ord(char) < 32 or ord(char) == 127 for char in version)):
                result[name] = version
        except (OSError, subprocess.SubprocessError, UnicodeError):
            continue
    return result


def authorizer(config: dict, *, config_path: str):
    """Re-read policy before each launch and expose only allowed installed tools."""
    from lib.build_host_policy import authorize_build_request

    def authorize(owner: str, request: dict) -> dict[str, str]:
        current = load_config(config_path)
        if {key: value for key, value in current.items() if key != "policy"} != {
            key: value for key, value in config.items() if key != "policy"
        }:
            raise PermissionError("Builder configuration changed; restart after draining")
        grant = authorize_build_request(current["policy"], principal=owner, request=request,
                                        requested_runtimes=[])
        # The compositor checks the pinned manifest's required_tools against
        # this restricted observation before executing any project command.
        return {key: value for key, value in observe_tools().items() if key in grant["runtimes"]}

    return authorize


@contextmanager
def connection_deadline(seconds: int = CONNECTION_SECONDS) -> Iterator[None]:
    """Bound SSH framing/upload/download waits independently of the worker."""
    def expired(_signal, _frame):
        raise TimeoutError("Builder connection expired")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def run_worker(config: dict, *, config_path: str, once: bool = False) -> None:
    """Keep accepted work independent of any controller's SSH connection."""
    from lib.build_jobs import BuildJobStore
    from lib.build_worker import run_next

    store = BuildJobStore(os.path.join(config["roots"]["state"], "jobs.json"))
    authorize = authorizer(config, config_path=config_path)
    while True:
        run_next(store, uid=config["uid"], gid=config["gid"],
                 inputs_root=config["roots"]["inputs"], scratch_root=config["roots"]["scratch"],
                 artifacts_root=config["roots"]["artifacts"], logs_root=config["roots"]["logs"],
                 authorize=authorize)
        if once:
            return
        time.sleep(2)


def serve(config: dict, *, principal: str, config_path: str) -> None:
    """Compose authenticated transport with protected input and job stores."""
    from lib.build_host_service import BuildHostService
    from lib.build_inputs import BuildInputStore
    from lib.build_jobs import BuildJobStore

    store = BuildJobStore(os.path.join(config["roots"]["state"], "jobs.json"))
    inputs = BuildInputStore(config["roots"]["inputs"], max_records=config["max_records"],
                             max_bytes=config["max_bytes"])
    service = BuildHostService(jobs=store, load_policy=lambda: load_config(config_path)["policy"],
                               admit_source=inputs.admit, tool_versions=observe_tools,
                               logs_root=config["roots"]["logs"],
                               artifacts_root=config["roots"]["artifacts"])
    with connection_deadline():
        service.serve_once(input_stream=sys.stdin.buffer, output_stream=sys.stdout.buffer,
                           principal=principal)


def retain_terminal(config: dict, *, request_id: str, expected_revision: str | None,
                    prune_kinds: tuple[str, ...] | None = None) -> dict:
    """Run explicit local maintenance using only administrator-configured roots."""
    from lib.build_inputs import BuildInputStore
    from lib.build_jobs import BuildJobStore
    from lib.build_retention import BuildRetentionStore

    roots = config["roots"]
    retention = BuildRetentionStore(
        inputs=BuildInputStore(roots["inputs"], max_records=config["max_records"],
                               max_bytes=config["max_bytes"]),
        jobs=BuildJobStore(os.path.join(roots["state"], "jobs.json")),
        scratch_root=roots["scratch"], artifacts_root=roots["artifacts"], logs_root=roots["logs"],
    )
    if prune_kinds is not None:
        return retention.prune_terminal(request_id, expected_revision=expected_revision, kinds=prune_kinds)
    if expected_revision is None:
        return retention.inspect_retention(request_id)
    return retention.settle_terminal(request_id, expected_revision=expected_revision)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the managed on-demand build host")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--serve", action="store_true")
    modes.add_argument("--worker", action="store_true")
    modes.add_argument("--inspect-retention", metavar="ID")
    modes.add_argument("--inspect-capacity", action="store_true")
    modes.add_argument("--settle-terminal", metavar="ID")
    modes.add_argument("--prune-terminal", metavar="ID")
    parser.add_argument("--expected-revision", metavar="DIGEST")
    parser.add_argument("--prune-kind", action="append", choices=("source", "log"))
    parser.add_argument("--principal")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    args = parser.parse_args(argv)
    try:
        if os.geteuid() != 0 or not sys.flags.isolated:
            raise ValueError("Use the root-controlled isolated Python launcher")
        maintenance = args.inspect_capacity or any(value is not None for value in
                          (args.inspect_retention, args.settle_terminal, args.prune_terminal))
        if (args.serve and (not args.principal or args.once
                            or os.environ.get("SSH_ORIGINAL_COMMAND") != SSH_COMMAND)
                or args.worker and args.principal is not None
                or (args.expected_revision is not None) != (args.settle_terminal is not None
                                                           or args.prune_terminal is not None)
                or args.prune_kind is not None and args.prune_terminal is None
                or maintenance and (args.principal is not None or args.once
                                    or any(name in os.environ for name in
                                           ("SSH_ORIGINAL_COMMAND", "SSH_CONNECTION", "SSH_CLIENT")))):
            raise ValueError("Invalid builder invocation")
        script = Path(os.path.abspath(__file__))
        _trusted_code(script)
        _trusted_libraries(script.parents[2])
        sys.path.insert(0, str(script.parents[2]))
        require_platform()
        config = load_config(args.config)
        if args.serve:
            serve(config, principal=args.principal, config_path=args.config)
        elif args.inspect_capacity:
            from lib.build_capacity import inspect_build_capacity

            result = inspect_build_capacity(config)
            print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        elif maintenance:
            options = ({"prune_kinds": tuple(args.prune_kind or ("source", "log"))}
                       if args.prune_terminal is not None else {})
            result = retain_terminal(config, request_id=args.inspect_retention or args.settle_terminal
                                     or args.prune_terminal, expected_revision=args.expected_revision,
                                     **options)
            print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        else:
            run_worker(config, config_path=args.config, once=args.once)
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError):
        print("Builder operation is unavailable; inspect protected host evidence", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
