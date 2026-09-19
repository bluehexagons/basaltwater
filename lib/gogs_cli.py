"""Operator commands for basaltwater-managed Gogs services."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from typing import Any

from lib.cache import load_setup_command
from lib.gogs_repository import configure_github_gogs_repository
from lib.ssh_utils import build_ssh_command, ssh_batch_mode
from lib.validators import validate_host, validate_username


DEFAULT_MIN_FREE_BYTES = 1024 * 1024 * 1024
DEFAULT_MIN_FREE_INODES = 10_000
DEFAULT_MAX_UPDATE_AGE_SECONDS = 9 * 24 * 60 * 60


def add_gogs_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register Gogs operator commands."""
    parser = subparsers.add_parser(
        "gogs",
        help="Inspect Gogs or configure a local GitHub/Gogs repository",
    )
    commands = parser.add_subparsers(dest="gogs_command", help="Gogs command")
    health = commands.add_parser(
        "health",
        help="Check Gogs service, storage, SQLite, LFS, updates, and nginx",
    )
    health.add_argument("host", help="Remote Gogs host")
    health.add_argument("--username", "-u", help="SSH username")
    health.add_argument("--key", "-i", dest="ssh_key", help="SSH private key")
    health.add_argument("--json", action="store_true", help="Output stable JSON")
    health.add_argument(
        "--min-free-bytes",
        type=int,
        default=DEFAULT_MIN_FREE_BYTES,
        help="Fail below this many available bytes",
    )
    health.add_argument(
        "--min-free-inodes",
        type=int,
        default=DEFAULT_MIN_FREE_INODES,
        help="Fail below this many available inodes",
    )
    configure = commands.add_parser(
        "repo-configure",
        help="Use GitHub for code and Gogs for a Git mirror and all LFS objects",
    )
    configure.add_argument(
        "repository",
        nargs="?",
        default=".",
        help="Clean local Git worktree root (default: current directory)",
    )
    configure.add_argument(
        "--github-url",
        required=True,
        help="Canonical GitHub HTTPS repository URL",
    )
    configure.add_argument(
        "--gogs-url",
        required=True,
        help="Matching Gogs HTTPS repository URL",
    )
    configure.add_argument(
        "--mirror-remote",
        default="gogs",
        help="Gogs remote name (default: gogs)",
    )
    configure.add_argument(
        "--track",
        action="append",
        default=[],
        metavar="PATTERN",
        help="Add a Git LFS tracking pattern; repeatable",
    )
    configure.add_argument(
        "--no-combined-push",
        action="store_true",
        help="Do not make a push to origin also update the Gogs Git mirror",
    )
    configure.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print the Git changes without applying them",
    )


def _resolve_connection(
    host: str,
    username: str | None,
    ssh_key: str | None,
) -> tuple[str, str | None]:
    config = load_setup_command(host)
    if config:
        username = username or config.username
        ssh_key = ssh_key or config.ssh_key
    return username or "root", ssh_key


def _remote_health_script(min_free_bytes: int, min_free_inodes: int) -> str:
    """Return a root-side Python health probe with no external dependencies."""
    return f'''
import configparser, glob, json, os, re, subprocess, time, urllib.parse

STATE = "/opt/basaltwater/state/gogs.json"
UPDATE_STATE = "/opt/basaltwater/state/gogs_update.json"
MIN_FREE_BYTES = {min_free_bytes}
MIN_FREE_INODES = {min_free_inodes}
MAX_UPDATE_AGE_SECONDS = {DEFAULT_MAX_UPDATE_AGE_SECONDS}

def run(*args):
    return subprocess.run(args, check=False, capture_output=True, text=True, timeout=60)

def directory_size(path):
    if not os.path.exists(path):
        return 0
    result = run("du", "-sx", "-B1", "--", path)
    try:
        return int(result.stdout.split()[0]) if result.returncode == 0 else None
    except (IndexError, ValueError):
        return None

with open(STATE, encoding="utf-8") as source:
    state = json.load(source)
data_path = state["data_path"]
config_path = state["config_path"]

service = run("systemctl", "is-active", "gogs")
service_active = service.returncode == 0 and service.stdout.strip() == "active"

mount = run("findmnt", "-J", "-o", "SOURCE,FSTYPE,TARGET", "-T", data_path)
mount_value = {{}}
if mount.returncode == 0:
    try:
        filesystems = json.loads(mount.stdout).get("filesystems", [])
        if filesystems:
            mount_value = filesystems[0]
    except (ValueError, TypeError):
        pass

stats = os.statvfs(data_path)
free_bytes = stats.f_bavail * stats.f_frsize
free_inodes = stats.f_favail
paths = [
    data_path,
    data_path + "/data",
    data_path + "/data/lfs-objects",
    data_path + "/data/tmp/lfs-objects",
    data_path + "/repositories",
    data_path + "/log",
]
path_health = {{}}
for path in paths:
    access = run("runuser", "-u", "git", "--", "test", "-r", path, "-a", "-w", path)
    path_health[path] = os.path.isdir(path) and access.returncode == 0

database_path = data_path + "/data/gogs.db"
database = run("sqlite3", database_path, "PRAGMA quick_check;")
sqlite_healthy = database.returncode == 0 and database.stdout.strip() == "ok"

usage = {{
    "repositories": directory_size(data_path + "/repositories"),
    "lfs_objects": directory_size(data_path + "/data/lfs-objects"),
    "attachments": directory_size(data_path + "/data/attachments"),
    "logs": directory_size(data_path + "/log"),
}}

update = run(
    "systemctl", "show", "auto-update-gogs.service",
    "--property=ActiveState,Result,ExecMainStatus", "--no-pager",
)
update_state = {{}}
for line in update.stdout.splitlines():
    if "=" in line:
        key, value = line.split("=", 1)
        update_state[key] = value
update_failed = update_state.get("ActiveState") == "failed" or update_state.get("Result") not in (None, "", "success")
update_state["available"] = update.returncode == 0
timer = run(
    "systemctl", "show", "auto-update-gogs.timer",
    "--property=ActiveState,NextElapseUSecRealtime", "--no-pager",
)
timer_state = {{}}
for line in timer.stdout.splitlines():
    if "=" in line:
        key, value = line.split("=", 1)
        timer_state[key] = value
timer_state["available"] = timer.returncode == 0
timer_active = timer_state.get("ActiveState") == "active"
timer_scheduled = timer_state.get("NextElapseUSecRealtime") not in (None, "", "n/a")

update_check = {{}}
update_check_source = UPDATE_STATE if os.path.exists(UPDATE_STATE) else STATE
try:
    update_check_age_seconds = max(0, int(time.time() - os.path.getmtime(update_check_source)))
except OSError:
    update_check_age_seconds = None
if update_check_source == UPDATE_STATE:
    try:
        with open(UPDATE_STATE, encoding="utf-8") as source:
            update_check = json.load(source)
    except (OSError, ValueError, TypeError):
        update_check = {{}}
update_check_successful = (
    update_check.get("schema_version") == 1
    and update_check.get("successful") is True
    if update_check_source == UPDATE_STATE
    else True
)
update_check_stale = (
    update_check_age_seconds is None
    or update_check_age_seconds > MAX_UPDATE_AGE_SECONDS
)

app = configparser.ConfigParser(interpolation=None)
app.read(config_path)
external_url = app.get("server", "EXTERNAL_URL", fallback="")
remote_lfs_endpoint_configured = bool(external_url) and not bool(
    re.match(r"^https?://(?:127\\.0\\.0\\.1|localhost)(?::|/)", external_url)
)
nginx_required = external_url.startswith("https://")

nginx_limit_bytes = None
nginx_site_path = None
nginx_content = ""
for nginx_path in glob.glob("/etc/nginx/sites-enabled/gogs_*"):
    try:
        content = open(nginx_path, encoding="utf-8").read()
    except OSError:
        continue
    nginx_site_path = nginx_path
    nginx_content = content
    match = re.search(r"client_max_body_size\\s+(\\d+)([kKmMgG]?)\\s*;", content)
    if match:
        scale = {{"": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}}[match.group(2).lower()]
        nginx_limit_bytes = int(match.group(1)) * scale
        break

nginx_service = run("systemctl", "is-active", "nginx")
nginx_active = nginx_service.returncode == 0 and nginx_service.stdout.strip() == "active"
nginx_validation = run("nginx", "-t") if nginx_required else None
nginx_config_valid = not nginx_required or nginx_validation.returncode == 0
frontend_healthy = not nginx_required
frontend_mode = "direct"
if nginx_required and nginx_active and nginx_config_valid and nginx_site_path:
    parsed_url = urllib.parse.urlsplit(external_url)
    hostname = parsed_url.hostname or ""
    public_port = parsed_url.port or 443
    if re.search(r"listen\\s+[^;]*ssl", nginx_content):
        frontend_mode = "tls"
        cert_match = re.search(r"ssl_certificate\\s+([^;]+);", nginx_content)
        cert_path = cert_match.group(1).strip() if cert_match else ""
        if hostname and cert_path:
            frontend = run(
                "curl", "--fail", "--silent", "--output", "/dev/null",
                "--noproxy", "*", "--connect-timeout", "3", "--max-time", "5",
                "--cacert", cert_path,
                "--resolve", f"{{hostname}}:{{public_port}}:127.0.0.1",
                external_url,
            )
            frontend_healthy = frontend.returncode == 0
    else:
        frontend_mode = "cloudflare-origin"
        frontend = run(
            "curl", "--fail", "--silent", "--output", "/dev/null",
            "--noproxy", "*", "--connect-timeout", "3", "--max-time", "5",
            "-H", f"Host: {{hostname}}", "http://127.0.0.1/",
        )
        frontend_healthy = bool(hostname) and frontend.returncode == 0

filesystem = str(mount_value.get("fstype", ""))
healthy = all((
    service_active,
    sqlite_healthy,
    all(path_health.values()),
    filesystem.lower() not in ("cifs", "smb3"),
    free_bytes >= MIN_FREE_BYTES,
    free_inodes >= MIN_FREE_INODES,
    not update_failed,
    update_state["available"],
    timer_active,
    timer_scheduled,
    update_check_successful,
    not update_check_stale,
    not nginx_required or nginx_active,
    nginx_config_valid,
    frontend_healthy,
    (not nginx_required and nginx_limit_bytes is None) or (nginx_limit_bytes is not None and nginx_limit_bytes >= 512 * 1024 * 1024),
))

print(json.dumps({{
    "healthy": healthy,
    "service_active": service_active,
    "release": {{"tag": state.get("tag_name"), "archive_sha256": state.get("archive_sha256")}},
    "storage": {{
        "data_path": data_path,
        "source": mount_value.get("source"),
        "filesystem": mount_value.get("fstype"),
        "mount_target": mount_value.get("target"),
        "free_bytes": free_bytes,
        "free_inodes": free_inodes,
        "min_free_bytes": MIN_FREE_BYTES,
        "min_free_inodes": MIN_FREE_INODES,
        "paths_healthy": path_health,
        "usage_bytes": usage,
    }},
    "sqlite_healthy": sqlite_healthy,
    "update_job": {{"failed": update_failed, **update_state}},
    "update_timer": {{"active": timer_active, "scheduled": timer_scheduled, **timer_state}},
    "update_check": {{
        **update_check,
        "age_seconds": update_check_age_seconds,
        "max_age_seconds": MAX_UPDATE_AGE_SECONDS,
        "stale": update_check_stale,
        "successful": update_check_successful,
    }},
    "nginx_upload_limit_bytes": nginx_limit_bytes,
    "nginx": {{
        "required": nginx_required,
        "active": nginx_active,
        "config_valid": nginx_config_valid,
        "site_path": nginx_site_path,
    }},
    "frontend": {{"healthy": frontend_healthy, "mode": frontend_mode}},
    "external_url": external_url,
    "remote_lfs_endpoint_configured": remote_lfs_endpoint_configured,
}}, sort_keys=True))
'''


def inspect_remote_gogs(
    host: str,
    username: str | None,
    ssh_key: str | None,
    min_free_bytes: int,
    min_free_inodes: int,
) -> dict[str, Any]:
    """Run the Gogs probe over SSH and return its JSON result."""
    if not validate_host(host):
        raise ValueError(f"Invalid Gogs host: {host}")
    if min_free_bytes < 0 or min_free_inodes < 0:
        raise ValueError("Gogs capacity thresholds must be non-negative")
    username, ssh_key = _resolve_connection(host, username, ssh_key)
    if not validate_username(username):
        raise ValueError(f"Invalid username: {username}")
    script = _remote_health_script(min_free_bytes, min_free_inodes)
    interpreter = f"python3 -c {shlex.quote(script)}"
    remote_command = interpreter if username == "root" else f"sudo -n {interpreter}"
    command = build_ssh_command(
        host,
        username,
        ssh_key,
        batch_mode=ssh_batch_mode(),
        remote_command=remote_command,
    )
    result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=90)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "remote probe failed"
        raise RuntimeError(f"Gogs health check failed: {detail}")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Gogs health check returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError("Gogs health check returned an invalid result")
    return value


def _format_health(value: dict[str, Any], host: str) -> str:
    storage = value.get("storage", {})
    update = value.get("update_job", {})
    timer = value.get("update_timer", {})
    check = value.get("update_check", {})
    usage = storage.get("usage_bytes", {})
    lines = [
        f"Gogs health for {host}: {'healthy' if value.get('healthy') else 'UNHEALTHY'}",
        f"  Service: {'active' if value.get('service_active') else 'inactive'}",
        f"  SQLite: {'ok' if value.get('sqlite_healthy') else 'FAILED'}",
        f"  Storage: {storage.get('source')} ({storage.get('filesystem')}) at {storage.get('mount_target')}",
        f"  Free: {storage.get('free_bytes')} bytes, {storage.get('free_inodes')} inodes",
        "  Usage: " + ", ".join(f"{name}={size}" for name, size in usage.items()),
        f"  Update job: {'FAILED' if update.get('failed') else 'ok'}",
        f"  Update timer: {'active' if timer.get('active') and timer.get('scheduled') else 'FAILED'}",
        f"  Last update check: {'STALE' if check.get('stale') else 'ok'} ({check.get('age_seconds')} seconds ago)",
        f"  Nginx upload limit: {value.get('nginx_upload_limit_bytes')}",
        "  Nginx: "
        + (
            "not required"
            if not value.get("nginx", {}).get("required")
            else (
                "ok"
                if value.get("nginx", {}).get("active")
                and value.get("nginx", {}).get("config_valid")
                else "FAILED"
            )
        ),
        "  Public web endpoint: "
        + ("ok" if value.get("frontend", {}).get("healthy") else "FAILED"),
        "  Remote LFS endpoint: "
        + (
            "configured (network reachability not probed)"
            if value.get("remote_lfs_endpoint_configured")
            else "loopback-only"
        ),
    ]
    return "\n".join(lines)


def run_gogs_command(args: argparse.Namespace) -> int:
    """Dispatch Gogs operator commands."""
    if args.gogs_command == "repo-configure":
        try:
            result = configure_github_gogs_repository(
                args.repository,
                args.github_url,
                args.gogs_url,
                mirror_remote=args.mirror_remote,
                track_patterns=args.track,
                combined_push=not args.no_combined_push,
                dry_run=args.dry_run,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        prefix = "Would configure" if args.dry_run else "Configured"
        print(f"{prefix} {result['repository']}")
        print(f"  GitHub origin: {result['github_url']}")
        print(f"  Gogs mirror: {result['gogs_url']} ({result['mirror_remote']})")
        print(f"  Git LFS endpoint: {result['lfs_url']}")
        if args.dry_run:
            print("Planned commands:")
            for action in result["actions"]:
                print(f"  {action}")
        else:
            print(
                "Review and commit .lfsconfig and .gitattributes, then run "
                "git push origin."
            )
        return 0
    if args.gogs_command != "health":
        print("Error: Gogs command required", file=sys.stderr)
        return 1
    try:
        value = inspect_remote_gogs(
            args.host,
            args.username,
            args.ssh_key,
            args.min_free_bytes,
            args.min_free_inodes,
        )
    except (ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(value, indent=2, sort_keys=True) if args.json else _format_health(value, args.host))
    return 0 if value.get("healthy") is True else 1
