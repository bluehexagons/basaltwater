"""Run a private browser-test service from an existing rootless Podman image."""

from __future__ import annotations

import argparse
from contextlib import closing
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import sys
import tempfile
import time
import urllib.error
from urllib.parse import urlsplit

from lib.agent_probe import run_probe
from lib.local_http import open_loopback
from lib.validation import validate_filesystem_path, validate_network_port, validate_no_control_characters


_PODMAN_CUTOFF_GRACE_SECONDS = 30


def add_test_instance_parser(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser("test-instance", help="Run a disposable loopback browser-test service")
    parser.add_argument("--image", required=True, help="Existing local Podman image")
    parser.add_argument("--port", type=int, required=True, help="HTTP port inside the container")
    parser.add_argument("--health-path", default="/")
    parser.add_argument("--sqlite-snapshot", type=Path, help="Offline, checkpointed SQLite fixture")
    parser.add_argument("--database-env", default="DATABASE_PATH", help="App variable for the copied SQLite fixture")
    parser.add_argument("--seconds", type=int, default=900, help="Maximum container lifetime (default 900 seconds)")
    parser.add_argument("--json", action="store_true", help="Print the ready instance as one JSON line")
    parser.add_argument("argv", nargs=argparse.REMAINDER, help="Optional image command arguments after --")


def _copy_snapshot(source: Path, destination: Path) -> None:
    """Read a checkpointed fixture without writing SQLite sidecars beside it."""
    validate_filesystem_path(str(source), must_exist=True)
    source = source.resolve(strict=True)
    if not source.is_file() or any(Path(str(source) + suffix).exists() for suffix in ("-wal", "-journal", "-shm")):
        raise ValueError("Use an offline, checkpointed SQLite snapshot without sidecar files")
    deadline = time.monotonic() + 30

    def check_deadline(_status: int, _remaining: int, _total: int) -> None:
        if time.monotonic() >= deadline:
            raise TimeoutError("SQLite snapshot copy exceeded 30 seconds")

    destination.touch(mode=0o600, exist_ok=False)
    with (
        closing(sqlite3.connect(source.as_uri() + "?mode=ro&immutable=1", uri=True)) as original,
        closing(sqlite3.connect(destination)) as copy,
    ):
        original.backup(copy, pages=256, progress=check_deadline)
        copy.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        if copy.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise ValueError("SQLite snapshot failed its integrity check")


def _podman(prefix: list[str], argv: list[str], step: str, *, timeout: float = 15) -> str:
    result = run_probe([*prefix, *argv], timeout=timeout, max_bytes=8192)
    if result.issue:
        raise RuntimeError(f"{step} failed ({result.issue})")
    return result.output.strip()


def _wait_for_http(prefix: list[str], name: str, url: str, deadline: float) -> None:
    deadline = min(deadline, time.monotonic() + 30)
    while (remaining := deadline - time.monotonic()) > 0:
        if _podman(prefix, ["inspect", "--format", "{{.State.Running}}", name],
                   "Application startup", timeout=min(15, remaining)) != "true":
            raise RuntimeError("Application exited before becoming ready")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            with open_loopback(url, timeout=min(1, remaining)) as response:
                if 200 <= response.status < 300:
                    return
        except urllib.error.HTTPError as exc:
            exc.close()
        except (OSError, http.client.HTTPException):
            pass
        time.sleep(max(0, min(0.25, deadline - time.monotonic())))
    raise RuntimeError("Loopback HTTP readiness timed out")


def _terminate(_signal: int, _frame: object) -> None:
    raise KeyboardInterrupt


def _run_instance(args: argparse.Namespace, podman: list[str], data: Path) -> int:
    name = data.name
    network_created = False
    start_attempted = False
    try:
        _podman(podman, ["network", "create", "--driver", "bridge", "--internal", name], f"Creation of test network {name}")
        network_created = True
        command = [
            "run", "--detach", "--rm", "--pull=never", "--http-proxy=false", "--image-volume=ignore",
            "--network", name, "--name", name,
            "--timeout", str(args.seconds + _PODMAN_CUTOFF_GRACE_SECONDS),
            "--publish", f"127.0.0.1::{args.port}/tcp", "--userns=keep-id",
            "--user", f"{os.geteuid()}:{os.getegid()}",
        ]
        if args.sqlite_snapshot:
            command.extend(("--volume", f"{data}:/basaltwater-test:rw,Z",
                            "--env", f"{args.database_env}=/basaltwater-test/fixture.sqlite3"))
        start_attempted = True
        _podman(podman, [*command, "--", args.image, *args.argv[1:]], "Container startup", timeout=60)
        deadline = time.monotonic() + args.seconds
        binding = _podman(podman, ["port", name, f"{args.port}/tcp"], "Loopback port lookup",
                          timeout=min(15, args.seconds))
        match = re.fullmatch(r"127\.0\.0\.1:([0-9]{1,5})", binding)
        if not match:
            raise RuntimeError("Podman did not report one IPv4 loopback port")
        validate_network_port(int(match[1]), "Published port")
        url = f"http://{binding}"
        _wait_for_http(podman, name, url + args.health_path, deadline)
        if time.monotonic() >= deadline:
            raise RuntimeError("Instance lifetime elapsed before readiness could be reported")
        if args.json:
            print(json.dumps({"url": url, "container": name, "network_origin": "vm",
                              "lifetime_seconds": args.seconds}), flush=True)
        else:
            print(f"Open {url} in the VM browser. Press Ctrl+C to stop {name}.", flush=True)
        # The wrapper owns the normal cutoff. Podman's later timeout caps the
        # container lifetime if the wrapper dies before it can clean up.
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return 0
        wait = run_probe([*podman, "wait", name], timeout=remaining, max_bytes=8192)
        if wait.issue == "probe_timeout":
            return 0
        if wait.issue:
            raise RuntimeError(f"Container wait failed ({wait.issue})")
        exit_code = wait.output.strip()
        if exit_code != "0":
            raise RuntimeError(f"Container {name} exited with status {exit_code}")
        return 0
    finally:
        # A timed-out start can still have created the container. Remove by
        # our unique name and tear down the network only after it is gone.
        if start_attempted:
            _podman(podman, ["rm", "--force", "--ignore", name], f"Cleanup of container {name}")
        # A failed network create may also have committed before Podman exited.
        if network_created or _podman(
            podman, ["network", "ls", "--filter", f"name=^{name}$", "--format", "{{.Name}}"],
            f"Lookup of test network {name}",
        ) == name:
            _podman(podman, ["network", "rm", name], f"Cleanup of network {name}")


def run_test_instance(args: argparse.Namespace) -> int:
    try:
        validate_network_port(args.port, "Container port", minimum=1024)
        validate_no_control_characters(args.health_path, "Health path")
        path = urlsplit(args.health_path)
        if (not args.health_path.startswith("/") or args.health_path.startswith("//")
                or path.fragment or "\\" in args.health_path or not args.health_path.isascii()
                or any(char.isspace() for char in args.health_path)):
            raise ValueError("Health path must be an ASCII HTTP path starting with one '/'")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9./_:@-]{0,254}", args.image):
            raise ValueError("Provide a local Podman image name or ID")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", args.database_env):
            raise ValueError("Invalid database environment variable")
        if not 5 <= args.seconds <= 7200:
            raise ValueError("Instance lifetime must be between 5 and 7200 seconds")
        if args.argv and args.argv[0] != "--":
            raise ValueError("Pass image command arguments after --")
        if not sys.platform.startswith("linux") or os.geteuid() == 0:
            raise ValueError("Run test-instance as the rootless Podman owner on Linux")
        executable = shutil.which("podman", path="/usr/local/bin:/usr/bin:/bin")
        if not executable:
            raise ValueError("Local rootless Podman is required")
        podman = [executable, "--remote=false"]
        if _podman(podman, ["info", "--format", "{{.Host.Security.Rootless}}"], "Podman readiness") != "true":
            raise RuntimeError("Podman did not confirm rootless mode")
        previous_handler = signal.signal(signal.SIGTERM, _terminate)
        try:
            with tempfile.TemporaryDirectory(prefix="bw-test-", dir="/tmp") as directory:
                data = Path(directory)
                print(f"Preparing test instance {data.name}", file=sys.stderr, flush=True)
                if args.sqlite_snapshot:
                    _copy_snapshot(args.sqlite_snapshot, data / "fixture.sqlite3")
                return _run_instance(args, podman, data)
        finally:
            signal.signal(signal.SIGTERM, previous_handler)
    except KeyboardInterrupt:
        return 130
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
