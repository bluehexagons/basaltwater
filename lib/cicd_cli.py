"""Controller-side CI/CD build-to-app server connection workflow."""

from __future__ import annotations

import argparse
import base64
import json
import posixpath
import re
import subprocess
import sys
from typing import Any

from lib.cache import load_setup_command
from lib.config import SetupConfig
from lib.ssh_enrollment import (
    enroll_host_key,
    fingerprint_host_keys,
    get_enrolled_host_key_lines,
    is_host_key_enrolled,
)
from lib.ssh_utils import build_ssh_command, shell_join, ssh_batch_mode
from lib.validation import validate_filesystem_path
from lib.validators import validate_host, validate_username


CICD_HOME = "/var/lib/basaltwater/cicd"
DEPLOY_KEY = f"{CICD_HOME}/.ssh/deploy_key"
DEPLOY_PUBLIC_KEY = f"{DEPLOY_KEY}.pub"
DEPLOY_TARGETS_FILE = "/etc/basaltwater/cicd/deploy_targets.json"
DEPLOY_KNOWN_HOSTS = f"{CICD_HOME}/known_hosts"
_TARGET_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_FINGERPRINT_PATTERN = re.compile(r"^SHA256:[A-Za-z0-9+/]{43}$")
_PUBLIC_KEY_PATTERN = re.compile(
    r"^(ssh-ed25519) ([A-Za-z0-9+/]+={0,3})(?: ([^\r\n]{1,256}))?$"
)
_HOST_KEY_TYPES = {
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521",
    "ssh-ed25519",
    "ssh-rsa",
}


_APP_KEY_INSTALL_SCRIPT = r'''
import base64, json, os, pwd, stat, subprocess, sys, tempfile

payload = json.loads(base64.b64decode(sys.argv[1], validate=True))
public_key = payload["public_key"].strip()
deploy_user = payload["deploy_user"]
base_dir = payload["base_dir"]
fields = public_key.split()
if len(fields) < 2 or fields[0] != "ssh-ed25519":
    raise SystemExit("invalid deploy public key")
try:
    base64.b64decode(fields[1], validate=True)
except ValueError:
    raise SystemExit("invalid deploy public key encoding")
if deploy_user != "deploy":
    raise SystemExit("invalid deploy user")
if not base_dir.startswith("/") or os.path.normpath(base_dir) != base_dir or base_dir == "/":
    raise SystemExit("invalid deploy base directory")
if not os.path.isfile("/usr/local/sbin/basaltwater-deploy-admin"):
    raise SystemExit("deploy admin helper is missing; rerun app-server setup")
account = pwd.getpwnam(deploy_user)
if account.pw_uid == 0 or account.pw_dir != "/home/deploy":
    raise SystemExit("deploy account has an unexpected home or user ID")
if os.path.islink(base_dir) or not os.path.isdir(base_dir):
    raise SystemExit("deploy base directory is missing or is not a regular directory")
writable = subprocess.run(
    ["runuser", "-u", deploy_user, "--", "test", "-w", base_dir],
    check=False,
)
if writable.returncode != 0:
    raise SystemExit("deploy base directory is not writable by the deploy account")
ssh_dir = os.path.join(account.pw_dir, ".ssh")
authorized_keys = os.path.join(ssh_dir, "authorized_keys")
if os.path.lexists(ssh_dir) and os.path.islink(ssh_dir):
    raise SystemExit("refusing symlinked deploy SSH directory")
os.makedirs(ssh_dir, mode=0o700, exist_ok=True)
os.chmod(ssh_dir, 0o700)
os.chown(ssh_dir, account.pw_uid, account.pw_gid)
existing = []
if os.path.lexists(authorized_keys):
    info = os.lstat(authorized_keys)
    if not stat.S_ISREG(info.st_mode):
        raise SystemExit("refusing non-regular authorized_keys")
    with open(authorized_keys, encoding="utf-8") as source:
        existing = source.read().splitlines()
identity = tuple(fields[:2])
if not any(tuple(line.split()[:2]) == identity for line in existing):
    existing.append(public_key)
text = "\n".join(existing) + ("\n" if existing else "")
fd, temporary = tempfile.mkstemp(prefix=".authorized_keys.", dir=ssh_dir)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as target:
        target.write(text)
        target.flush()
        os.fsync(target.fileno())
    os.chmod(temporary, 0o600)
    os.chown(temporary, account.pw_uid, account.pw_gid)
    os.replace(temporary, authorized_keys)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
'''


_BUILD_TARGET_INSTALL_SCRIPT = r'''
import base64, json, os, pwd, re, subprocess, sys, tempfile

payload = json.loads(base64.b64decode(sys.argv[1], validate=True))
target_name = payload["target_name"]
host = payload["host"]
port = payload["port"]
base_dir = payload["base_dir"]
known_name = host if port == 22 else "[{}]:{}".format(host, port)
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", target_name):
    raise SystemExit("invalid deploy target name")
if not isinstance(port, int) or not 1 <= port <= 65535:
    raise SystemExit("invalid deploy target port")
if not base_dir.startswith("/") or os.path.normpath(base_dir) != base_dir or base_dir == "/":
    raise SystemExit("invalid deploy base directory")
scan_lines = payload["known_host_lines"]
if not isinstance(scan_lines, list) or not scan_lines:
    raise SystemExit("missing deploy target host key")
for line in scan_lines:
    fields = line.split()
    if (
        len(fields) != 3
        or fields[0] != known_name
        or fields[1] not in {
            "ecdsa-sha2-nistp256",
            "ecdsa-sha2-nistp384",
            "ecdsa-sha2-nistp521",
            "ssh-ed25519",
            "ssh-rsa",
        }
    ):
        raise SystemExit("invalid deploy target host-key entry")
if payload["deploy_user"] != "deploy":
    raise SystemExit("invalid deploy target user")

webhook = pwd.getpwnam("webhook")
home = "/var/lib/basaltwater/cicd"
known_hosts = os.path.join(home, "known_hosts")
targets_file = "/etc/basaltwater/cicd/deploy_targets.json"
os.makedirs(home, mode=0o750, exist_ok=True)
os.makedirs(os.path.dirname(targets_file), mode=0o755, exist_ok=True)

def atomic_write(path, text, mode, uid, gid):
    fd, temporary = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as target:
            target.write(text)
            target.flush()
            os.fsync(target.fileno())
        os.chmod(temporary, mode)
        os.chown(temporary, uid, gid)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

existing_known_hosts = ""
if os.path.lexists(known_hosts):
    if os.path.islink(known_hosts) or not os.path.isfile(known_hosts):
        raise SystemExit("refusing non-regular build known_hosts file")
    with open(known_hosts, encoding="utf-8") as source:
        existing_known_hosts = source.read()
fd, filtered_path = tempfile.mkstemp(prefix=".known_hosts.filter.", dir=home)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as target:
        target.write(existing_known_hosts)
    removal = subprocess.run(
        ["ssh-keygen", "-R", known_name, "-f", filtered_path],
        check=False,
        capture_output=True,
        text=True,
    )
    if removal.returncode not in (0, 1):
        raise SystemExit("could not reconcile the build known_hosts file")
    with open(filtered_path, encoding="utf-8") as source:
        retained = source.read().rstrip("\n")
finally:
    if os.path.exists(filtered_path):
        os.unlink(filtered_path)
    if os.path.exists(filtered_path + ".old"):
        os.unlink(filtered_path + ".old")
known_text = "\n".join(part for part in (retained, *scan_lines) if part) + "\n"
atomic_write(known_hosts, known_text, 0o644, webhook.pw_uid, webhook.pw_gid)

targets = {}
if os.path.lexists(targets_file):
    if os.path.islink(targets_file) or not os.path.isfile(targets_file):
        raise SystemExit("refusing non-regular deploy target configuration")
    with open(targets_file, encoding="utf-8") as source:
        targets = json.load(source)
    if not isinstance(targets, dict):
        raise SystemExit("deploy target configuration is not an object")
targets[target_name] = {
    "host": host,
    "user": payload["deploy_user"],
    "ssh_port": port,
    "base_dir": base_dir,
    "ssh_key": "/var/lib/basaltwater/cicd/.ssh/deploy_key",
}
atomic_write(
    targets_file,
    json.dumps(targets, indent=2, sort_keys=True) + "\n",
    0o644,
    0,
    0,
)
'''


def add_cicd_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register CI/CD connection-management commands."""

    parser = subparsers.add_parser(
        "cicd",
        help="Connect and inspect CI/CD build and app servers",
    )
    commands = parser.add_subparsers(dest="cicd_command", help="CI/CD command")

    connect = commands.add_parser(
        "connect",
        help="Connect a saved build server to a saved app server",
    )
    connect.add_argument("build", help="Saved build-server host, name, or tag")
    connect.add_argument("app", help="Saved app-server host, name, or tag")
    connect.add_argument(
        "--target-name",
        help="Name used by repository deploy_target entries (default: app host)",
    )
    connect.add_argument(
        "--port",
        type=int,
        default=22,
        help="App-server SSH port used for deployments (default: 22)",
    )
    connect.add_argument(
        "--base-dir",
        default="/var/www",
        help="Remote deployment base directory (default: /var/www)",
    )
    connect.add_argument(
        "--fingerprint",
        help="Pinned app SSH host-key fingerprint (SHA256:...)",
    )

    status = commands.add_parser("status", help="Show configured deploy targets")
    status.add_argument("build", help="Saved build-server host, name, or tag")
    status.add_argument("--json", action="store_true", help="Output stable JSON")

    test = commands.add_parser("test", help="Test one build-to-app connection")
    test.add_argument("build", help="Saved build-server host, name, or tag")
    test.add_argument("target", help="Configured deploy target name")


def _load_role(reference: str, *, build_server: bool) -> SetupConfig:
    config = load_setup_command(reference)
    role = "build" if build_server else "app"
    if config is None:
        raise ValueError(f"No saved {role}-server setup found for {reference}")
    enabled = config.is_build_server if build_server else config.is_app_server
    if not enabled:
        flag = "--build-server" if build_server else "--app-server"
        raise ValueError(
            f"Saved host {config.host} is not configured with {flag}"
        )
    if not validate_host(config.host):
        raise ValueError(f"Invalid saved {role}-server host: {config.host}")
    if not validate_username(config.username):
        raise ValueError(f"Invalid saved {role}-server username: {config.username}")
    return config


def _validate_target_name(value: str) -> str:
    if not _TARGET_NAME_PATTERN.fullmatch(value):
        raise ValueError(
            "Target name must start with a letter or number and use only "
            "letters, numbers, '.', '_' or '-'"
        )
    return value


def _validate_base_dir(value: str) -> str:
    validate_filesystem_path(value, must_exist=False)
    normalized = posixpath.normpath(value)
    if not value.startswith("/") or normalized != value or value == "/":
        raise ValueError("Deploy base directory must be a normalized absolute child path")
    return value


def _validate_fingerprint(value: str | None) -> str | None:
    if value is not None and not _FINGERPRINT_PATTERN.fullmatch(value):
        raise ValueError("Fingerprint must use the OpenSSH SHA256:... format")
    return value


def _root_command(config: SetupConfig, argv: list[str]) -> str:
    command = shell_join(argv)
    return command if config.username == "root" else f"sudo -n {command}"


def _run_remote(
    config: SetupConfig,
    remote_command: str,
    *,
    port: int | None = None,
    timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    command = build_ssh_command(
        config.host,
        config.username,
        config.ssh_key,
        port=port,
        remote_command=remote_command,
        batch_mode=ssh_batch_mode(),
        connect_timeout=20,
        server_alive_interval=20,
    )
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _encoded_payload(value: dict[str, Any]) -> str:
    data = json.dumps(value, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(data).decode("ascii")


def _remote_python_command(config: SetupConfig, script: str, payload: dict[str, Any]) -> str:
    return _root_command(
        config,
        ["python3", "-c", script, _encoded_payload(payload)],
    )


def _result_detail(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stderr or result.stdout or "remote command failed").strip()[:500]


def _fetch_build_public_key(build: SetupConfig) -> str:
    result = _run_remote(
        build,
        _root_command(build, ["cat", DEPLOY_PUBLIC_KEY]),
    )
    if result.returncode != 0:
        raise RuntimeError(f"Could not read the build deploy public key: {_result_detail(result)}")
    public_key = result.stdout.strip()
    match = _PUBLIC_KEY_PATTERN.fullmatch(public_key)
    if not match:
        raise RuntimeError("Build server returned an invalid deploy public key")
    check = subprocess.run(
        ["ssh-keygen", "-lf", "-"],
        input=public_key + "\n",
        check=False,
        capture_output=True,
        text=True,
    )
    if check.returncode != 0:
        raise RuntimeError("Build server deploy public key failed ssh-keygen validation")
    return public_key


def _normalized_app_host_keys(app_host: str, port: int) -> list[str]:
    lines = get_enrolled_host_key_lines(app_host, port=port)
    known_name = app_host if port == 22 else f"[{app_host}]:{port}"
    normalized: list[str] = []
    for line in lines:
        fields = line.split()
        if len(fields) < 3 or fields[1] not in _HOST_KEY_TYPES:
            raise RuntimeError("Controller known_hosts contains an invalid app-server key")
        normalized.append(f"{known_name} {fields[1]} {fields[2]}")
    if not normalized:
        raise RuntimeError("Controller known_hosts contains no app-server key")
    result = sorted(set(normalized))
    fingerprint_host_keys("\n".join(result))
    return result


def _ensure_app_trust(app_host: str, port: int, fingerprint: str | None) -> list[str]:
    if is_host_key_enrolled(app_host, port=port):
        lines = _normalized_app_host_keys(app_host, port)
        if fingerprint:
            observed = fingerprint_host_keys("\n".join(lines))
            if fingerprint not in observed.split():
                raise RuntimeError(
                    f"Pinned fingerprint {fingerprint} does not match the enrolled app key"
                )
        return lines

    result = enroll_host_key(
        app_host,
        port=port,
        expected_fingerprint=fingerprint,
    )
    if result != 0:
        raise RuntimeError(f"App-server host key was not enrolled for {app_host}:{port}")
    return _normalized_app_host_keys(app_host, port)


def _install_app_public_key(
    app: SetupConfig,
    public_key: str,
    port: int,
    base_dir: str,
) -> None:
    command = _remote_python_command(
        app,
        _APP_KEY_INSTALL_SCRIPT,
        {
            "deploy_user": "deploy",
            "public_key": public_key,
            "base_dir": base_dir,
        },
    )
    result = _run_remote(app, command, port=port)
    if result.returncode != 0:
        raise RuntimeError(f"Could not authorize the build deploy key: {_result_detail(result)}")


def _install_build_target(
    build: SetupConfig,
    *,
    target_name: str,
    app_host: str,
    port: int,
    base_dir: str,
    known_host_lines: list[str],
) -> None:
    command = _remote_python_command(
        build,
        _BUILD_TARGET_INSTALL_SCRIPT,
        {
            "target_name": target_name,
            "host": app_host,
            "port": port,
            "base_dir": base_dir,
            "deploy_user": "deploy",
            "known_host_lines": known_host_lines,
        },
    )
    result = _run_remote(build, command)
    if result.returncode != 0:
        raise RuntimeError(f"Could not configure the build deploy target: {_result_detail(result)}")


def _load_remote_targets(build: SetupConfig) -> dict[str, dict[str, Any]]:
    result = _run_remote(
        build,
        _root_command(build, ["cat", DEPLOY_TARGETS_FILE]),
    )
    if result.returncode != 0:
        detail = _result_detail(result)
        if "No such file" in detail:
            return {}
        raise RuntimeError(f"Could not read deploy targets: {detail}")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Build server returned invalid deploy target JSON") from exc
    if not isinstance(value, dict) or not all(
        isinstance(name, str) and isinstance(target, dict)
        for name, target in value.items()
    ):
        raise RuntimeError("Build server returned an invalid deploy target mapping")
    return value


def _test_target(build: SetupConfig, target_name: str) -> None:
    targets = _load_remote_targets(build)
    target = targets.get(target_name)
    if target is None:
        raise ValueError(f"Unknown deploy target on {build.host}: {target_name}")
    host = target.get("host")
    user = target.get("user")
    port = target.get("ssh_port")
    key = target.get("ssh_key")
    if (
        not isinstance(host, str)
        or not validate_host(host)
        or user != "deploy"
        or not isinstance(port, int)
        or not 1 <= port <= 65535
        or key != DEPLOY_KEY
    ):
        raise RuntimeError(f"Deploy target {target_name} has invalid connection settings")
    ssh_command = [
        "sudo",
        "-n",
        "-u",
        "webhook",
        "--",
        "env",
        f"HOME={CICD_HOME}",
        "ssh",
        "-i",
        DEPLOY_KEY,
        "-p",
        str(port),
        "-o",
        f"UserKnownHostsFile={DEPLOY_KNOWN_HOSTS}",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        f"{user}@{host}",
        "echo connection-ok",
    ]
    result = _run_remote(build, shell_join(ssh_command), timeout=45)
    output_lines = result.stdout.splitlines()
    if result.returncode != 0 or not output_lines or output_lines[-1] != "connection-ok":
        raise RuntimeError(f"Build-to-app SSH test failed: {_result_detail(result)}")


def connect_build_to_app(
    build_reference: str,
    app_reference: str,
    *,
    target_name: str | None = None,
    port: int = 22,
    base_dir: str = "/var/www",
    fingerprint: str | None = None,
) -> str:
    """Connect saved build and app servers and return the target name."""

    if not 1 <= port <= 65535:
        raise ValueError("App SSH port must be between 1 and 65535")
    fingerprint = _validate_fingerprint(fingerprint)
    build = _load_role(build_reference, build_server=True)
    app = _load_role(app_reference, build_server=False)
    if build.host == app.host:
        raise ValueError("Build and app servers must be different hosts")
    resolved_target = _validate_target_name(target_name or app.host)
    resolved_base_dir = _validate_base_dir(base_dir)

    if not is_host_key_enrolled(build.host):
        raise RuntimeError(
            f"Build server host key is not enrolled; run: basaltw ssh-key enroll {build.host}"
        )
    known_host_lines = _ensure_app_trust(app.host, port, fingerprint)
    public_key = _fetch_build_public_key(build)
    _install_app_public_key(app, public_key, port, resolved_base_dir)
    _install_build_target(
        build,
        target_name=resolved_target,
        app_host=app.host,
        port=port,
        base_dir=resolved_base_dir,
        known_host_lines=known_host_lines,
    )
    _test_target(build, resolved_target)
    return resolved_target


def run_cicd_command(args: argparse.Namespace) -> int:
    """Dispatch controller-side CI/CD commands."""

    try:
        if args.cicd_command == "connect":
            target = connect_build_to_app(
                args.build,
                args.app,
                target_name=args.target_name,
                port=args.port,
                base_dir=args.base_dir,
                fingerprint=args.fingerprint,
            )
            print(f"Connected build server {args.build} to deploy target {target}")
            print(f"  Test again: basaltw cicd test {args.build} {target}")
            return 0
        if args.cicd_command == "status":
            build = _load_role(args.build, build_server=True)
            targets = _load_remote_targets(build)
            if args.json:
                print(json.dumps(targets, indent=2, sort_keys=True))
            elif not targets:
                print(f"No deploy targets configured on {build.host}.")
            else:
                print(f"Deploy targets on {build.host}:")
                for name, target in sorted(targets.items()):
                    print(
                        f"  {name}: {target.get('user')}@{target.get('host')}:"
                        f"{target.get('ssh_port')} -> {target.get('base_dir')}"
                    )
            return 0
        if args.cicd_command == "test":
            build = _load_role(args.build, build_server=True)
            target = _validate_target_name(args.target)
            _test_target(build, target)
            print(f"Build-to-app connection is healthy: {build.host} -> {target}")
            return 0
        print("Error: CI/CD command required (connect, status, test)", file=sys.stderr)
        return 1
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
