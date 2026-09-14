#!/usr/bin/env python3
"""Manage infra_tools static HTTPS publications and loopback forwards."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
from functools import wraps
import grp
import hashlib
import ipaddress
import json
import os
import pwd
import re
import shlex
import shutil
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser


SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
if SOURCE_ROOT not in sys.path:
    sys.path.insert(0, SOURCE_ROOT)

from common.service_tools import godot_web_publish, static_web_publish
from lib.validation import validate_positive_integer
from lib.local_http import open_loopback
from lib.remote_utils import CommandTimeoutError, run as run_command


POLICY_FILE = "/etc/infra-tools/internal-web/policy.json"
MUTATION_LOCK_FILE = "/etc/infra-tools/internal-web/mutation.lock"
FORWARD_STATE_FILE = "/etc/infra-tools/internal-web/forwards.json"
FORWARD_NGINX_SITE = "/etc/nginx/sites-available/infra-tools-web-forwards"
FORWARD_NGINX_LINK = "/etc/nginx/sites-enabled/infra-tools-web-forwards"
GAMES_ROOT = "/srv/infra-tools/web/games"
PREVIEW_STATE_FILE = "/etc/infra-tools/internal-web/previews.json"
PREVIEW_RUNTIME_ROOT = "/var/lib/infra_tools/internal-web-previews"
SYSTEMD_ROOT = "/etc/systemd/system"
_FORWARD_MARKER = "# Managed by infra_tools HTTPS forwarding"
_FORWARD_RULE_PREFIX = "infra_tools HTTPS forward"
_PREVIEW_UNIT_PREFIX = "infra-web-preview"
_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_USERNAME_PATTERN = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
_SAFE_PATH_PATTERN = re.compile(r"^/[A-Za-z0-9_./-]+$")
_UFW_NUMBERED_RULE_RE = re.compile(r"^\[\s*(\d+)\]\s+(.*)$")
_BODY_SIZE_PATTERN = re.compile(r"^([1-9][0-9]{0,9})([kKmMgG]?)$")
_PROFILES = ("general", "godot", "syncthing")
_MAX_FORWARD_BODY_BYTES = 1024**3
_SYSTEM_COMMAND_TIMEOUT_SECONDS = 60
_PREVIEW_INSTALL_TIMEOUT_SECONDS = 30 * 60


@contextmanager
def _gateway_lock():
    """Hold one root-owned lock through planning, activation, and rollback."""
    if os.geteuid() != 0:
        raise RuntimeError("Run HTTPS forward and preview mutations with sudo")
    descriptor = os.open(
        MUTATION_LOCK_FILE,
        os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
        0o600,
    )
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != 0:
            raise RuntimeError("Refusing unsafe internal-web mutation lock")
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another internal-web mutation is running; retry after it finishes") from exc
        yield
    finally:
        os.close(descriptor)


def _serialized_mutation(function):
    @wraps(function)
    def locked(*args, **kwargs):
        with _gateway_lock():
            return function(*args, **kwargs)
    return locked


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage the infra_tools HTTPS publishing gateway",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    publish = commands.add_parser("publish", help="Publish static content")
    publish_commands = publish.add_subparsers(dest="publish_kind", required=True)
    godot = publish_commands.add_parser("godot", help="Export and publish a Godot game")
    godot.add_argument("game", nargs="?")
    godot.add_argument("project_positional", nargs="?")
    godot.add_argument("--project", dest="project_option")
    godot.add_argument("--preset", default="Web")
    godot.add_argument("--debug", action="store_true")
    godot.add_argument("--no-precompress", action="store_true")
    godot.add_argument("--json", action="store_true")
    godot.add_argument("--open", action="store_true")
    godot.add_argument("--build-timeout", type=validate_positive_integer, default=3600)
    site_publish = publish_commands.add_parser(
        "site",
        help="Build and publish a generic static site",
    )
    static_web_publish.add_publish_arguments(site_publish)

    site = commands.add_parser("site", help="Manage published static sites")
    site_commands = site.add_subparsers(dest="site_command", required=True)
    site_list = site_commands.add_parser("list", aliases=["ls"], help="List sites")
    site_list.add_argument("--json", action="store_true")
    site_url = site_commands.add_parser("url", help="Print a published site URL")
    site_url.add_argument("name")
    site_doctor = site_commands.add_parser("doctor", help="Verify a published site")
    site_doctor.add_argument("name")
    site_doctor.add_argument("--json", action="store_true")
    site_remove = site_commands.add_parser("remove", help="Remove a published site")
    site_remove.add_argument("name")
    site_remove.add_argument("--yes", action="store_true")
    site_remove.add_argument("--json", action="store_true")

    list_command = commands.add_parser("list", aliases=["ls"], help="List published games")
    list_command.add_argument("--json", action="store_true")

    url = commands.add_parser("url", help="Print a published game URL")
    url.add_argument("game")

    remove = commands.add_parser("remove", help="Remove a published game")
    remove.add_argument("game")
    remove.add_argument("--yes", action="store_true", help="Confirm permanent removal")

    doctor = commands.add_parser("doctor", help="Verify a managed HTTPS endpoint")
    doctor.add_argument("name")
    doctor.add_argument("--json", action="store_true")

    ca = commands.add_parser("ca", help="Show the managed CA path and fingerprint")
    ca.add_argument("--json", action="store_true")

    forward = commands.add_parser("forward", help="Manage HTTPS loopback forwards")
    forward_commands = forward.add_subparsers(dest="forward_command", required=True)
    forward_add = forward_commands.add_parser("add", help="Add or update a forward")
    forward_add.add_argument("name")
    forward_add.add_argument(
        "--listen",
        default="auto",
        help="External HTTPS port or 'auto' (default: auto)",
    )
    forward_add.add_argument(
        "--to",
        required=True,
        metavar="LOOPBACK:PORT",
        help="Loopback HTTP upstream, such as 127.0.0.1:3000",
    )
    forward_add.add_argument("--profile", choices=_PROFILES, default="general")
    forward_add.add_argument(
        "--wait",
        type=float,
        default=0,
        metavar="SECONDS",
        help="Wait for the upstream HTTP endpoint before exposing it",
    )
    forward_add.add_argument(
        "--health",
        default="/",
        metavar="PATH",
        help="HTTP readiness path used with --wait (default: /)",
    )
    forward_add.add_argument(
        "--max-body-size",
        metavar="SIZE",
        help="Maximum proxied request body, such as 50m (default: Nginx 1m)",
    )
    forward_add.add_argument("--open", action="store_true")
    forward_add.add_argument("--json", action="store_true")
    forward_remove = forward_commands.add_parser("remove", help="Remove a forward")
    forward_remove.add_argument("name")
    forward_remove.add_argument("--json", action="store_true")
    forward_list = forward_commands.add_parser("list", aliases=["ls"], help="List forwards")
    forward_list.add_argument("--json", action="store_true")
    forward_url = forward_commands.add_parser("url", help="Print a forward URL")
    forward_url.add_argument("name")
    forward_prune = forward_commands.add_parser(
        "prune",
        help="Remove confirmed dead forwards owned by the requesting user",
    )
    forward_prune.add_argument("--yes", action="store_true")
    forward_prune.add_argument("--json", action="store_true")
    forward_reconcile = forward_commands.add_parser(
        "reconcile",
        help="Reconcile generated Nginx and UFW state",
    )
    forward_reconcile.add_argument("--json", action="store_true")

    preview = commands.add_parser("preview", help="Manage supervised live previews")
    preview_commands = preview.add_subparsers(dest="preview_command", required=True)
    preview_start = preview_commands.add_parser(
        "start",
        help="Start and expose a preview",
        epilog=(
            "Append -- COMMAND [ARGS...] to override automatic Vite detection; "
            "use {host} and {port} placeholders when needed."
        ),
    )
    preview_start.add_argument("name")
    preview_start.add_argument("--project", default=".")
    preview_start.add_argument("--port", default="auto")
    preview_start.add_argument("--listen", default="auto")
    preview_start.add_argument("--profile", choices=_PROFILES, default="general")
    preview_start.add_argument("--wait", type=float, default=30)
    preview_start.add_argument("--health", default="/")
    preview_start.add_argument("--replace", action="store_true")
    preview_start.add_argument("--open", action="store_true")
    preview_start.add_argument("--json", action="store_true")
    preview_stop = preview_commands.add_parser("stop", help="Stop and remove a preview")
    preview_stop.add_argument("name")
    preview_stop.add_argument("--json", action="store_true")
    preview_list = preview_commands.add_parser("list", aliases=["ls"], help="List previews")
    preview_list.add_argument("--json", action="store_true")
    preview_url = preview_commands.add_parser("url", help="Print a preview URL")
    preview_url.add_argument("name")
    preview_logs = preview_commands.add_parser("logs", help="Show preview service logs")
    preview_logs.add_argument("name")
    preview_logs.add_argument("--lines", type=int, default=100)
    preview_prune = preview_commands.add_parser("prune", help="Remove stopped previews")
    preview_prune.add_argument("--yes", action="store_true")
    preview_prune.add_argument("--json", action="store_true")
    return parser


def _validate_name(value: str, label: str = "name") -> str:
    if not _NAME_PATTERN.fullmatch(value):
        raise ValueError(
            f"{label} must use lowercase letters, digits, '-' or '_' and start "
            "with a letter or digit"
        )
    return value


def _validate_username(value: object) -> str:
    if not isinstance(value, str) or not _USERNAME_PATTERN.fullmatch(value):
        raise RuntimeError("Invalid internal-web username")
    return value


def _validate_managed_path(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or not _SAFE_PATH_PATTERN.fullmatch(value)
        or os.path.normpath(value) != value
    ):
        raise RuntimeError(f"Invalid {label} path in internal-web policy")
    return value


def _read_json(path: str, label: str) -> object:
    if os.path.islink(path):
        raise RuntimeError(f"Refusing symlinked {label}: {path}")
    try:
        with open(path, encoding="utf-8") as file_obj:
            return json.load(file_obj)
    except FileNotFoundError as exc:
        raise RuntimeError(f"{label} is not configured") from exc
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Could not read {label}") from exc


def _validate_base_url(value: object) -> str:
    if not isinstance(value, str):
        raise RuntimeError("Invalid base URL in internal-web policy")
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise RuntimeError("Invalid base URL in internal-web policy")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise RuntimeError("Invalid base URL port in internal-web policy") from exc
    return value.rstrip("/")


def _load_policy() -> dict[str, object]:
    value = _read_json(POLICY_FILE, "internal-web policy")
    if not isinstance(value, dict) or value.get("version") != 1:
        raise RuntimeError("Invalid internal-web policy")
    base_url = _validate_base_url(value.get("base_url"))
    cert_path = _validate_managed_path(value.get("certificate"), "certificate")
    key_path = _validate_managed_path(value.get("certificate_key"), "certificate key")
    raw_users = value.get("users")
    if not isinstance(raw_users, list) or not raw_users:
        raise RuntimeError("Internal-web policy has no users")
    users = list(dict.fromkeys(_validate_username(user) for user in raw_users))
    raw_sources = value.get("access_sources", [])
    if not isinstance(raw_sources, list):
        raise RuntimeError("Invalid access sources in internal-web policy")
    sources: list[str] = []
    for source in raw_sources:
        if not isinstance(source, str):
            raise RuntimeError("Invalid access source in internal-web policy")
        try:
            ipaddress.ip_network(source, strict=False)
        except ValueError as exc:
            raise RuntimeError("Invalid access source in internal-web policy") from exc
        sources.append(source)
    port_min = value.get("forward_port_min")
    port_max = value.get("forward_port_max")
    if (
        not isinstance(port_min, int)
        or isinstance(port_min, bool)
        or not isinstance(port_max, int)
        or isinstance(port_max, bool)
        or not 1024 <= port_min <= port_max <= 65535
    ):
        raise RuntimeError("Invalid forwarding range in internal-web policy")
    ca_certificate = value.get("ca_certificate")
    if ca_certificate is not None:
        ca_certificate = _validate_managed_path(ca_certificate, "CA certificate")
    return {
        "access_sources": list(dict.fromkeys(sources)),
        "base_url": base_url,
        "ca_certificate": ca_certificate,
        "certificate": cert_path,
        "certificate_key": key_path,
        "forward_port_max": port_max,
        "forward_port_min": port_min,
        "users": users,
        "version": 1,
    }


def _parse_upstream(value: str) -> tuple[str, int]:
    host = ""
    port_text = ""
    if value.startswith("["):
        closing = value.find("]")
        if closing < 0 or closing + 1 >= len(value) or value[closing + 1] != ":":
            raise ValueError("--to must be a loopback host and port")
        host = value[1:closing]
        port_text = value[closing + 2 :]
    else:
        host, separator, port_text = value.rpartition(":")
        if not separator:
            raise ValueError("--to must be a loopback host and port")
    try:
        address = ipaddress.ip_address(host)
        port = int(port_text)
    except ValueError as exc:
        raise ValueError("--to must be a loopback host and port") from exc
    if not address.is_loopback or not 1024 <= port <= 65535:
        raise ValueError("--to must use loopback and an unprivileged port")
    return str(address), port


def _validate_body_size(value: object, label: str) -> str | None:
    """Validate a bounded Nginx request-body size without allowing directives."""

    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a size such as 50m")
    match = _BODY_SIZE_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError(
            f"{label} must be a positive integer with optional k, m, or g suffix"
        )
    number = int(match.group(1))
    unit = match.group(2).lower()
    multiplier = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}[unit]
    if number * multiplier > _MAX_FORWARD_BODY_BYTES:
        raise ValueError(f"{label} must not exceed 1g")
    return f"{number}{unit}"


def _validate_route(value: object, policy: dict[str, object]) -> dict[str, object]:
    if not isinstance(value, dict):
        raise RuntimeError("Invalid HTTPS forward state")
    name = _validate_name(str(value.get("name", "")), "forward name")
    owner = _validate_username(value.get("owner"))
    if owner not in policy["users"]:
        raise RuntimeError("HTTPS forward owner is not allowed by policy")
    listen = value.get("listen")
    target_port = value.get("target_port")
    if (
        not isinstance(listen, int)
        or isinstance(listen, bool)
        or not policy["forward_port_min"] <= listen <= policy["forward_port_max"]
        or not isinstance(target_port, int)
        or isinstance(target_port, bool)
        or not 1024 <= target_port <= 65535
    ):
        raise RuntimeError("Invalid port in HTTPS forward state")
    target_host = value.get("target_host")
    try:
        address = ipaddress.ip_address(str(target_host))
    except ValueError as exc:
        raise RuntimeError("Invalid target in HTTPS forward state") from exc
    if not address.is_loopback:
        raise RuntimeError("HTTPS forward target is not loopback")
    profile = value.get("profile")
    if profile not in _PROFILES:
        raise RuntimeError("Invalid profile in HTTPS forward state")
    try:
        max_body_size = _validate_body_size(
            value.get("max_body_size"),
            "HTTPS forward maximum body size",
        )
    except ValueError as exc:
        raise RuntimeError("Invalid maximum body size in HTTPS forward state") from exc
    return {
        "listen": listen,
        "name": name,
        "owner": owner,
        "profile": profile,
        "target_host": str(address),
        "target_port": target_port,
        **({"max_body_size": max_body_size} if max_body_size is not None else {}),
    }


def _load_forwards(policy: dict[str, object]) -> list[dict[str, object]]:
    if not os.path.exists(FORWARD_STATE_FILE):
        return []
    value = _read_json(FORWARD_STATE_FILE, "HTTPS forward state")
    if not isinstance(value, dict) or value.get("version") != 1:
        raise RuntimeError("Invalid HTTPS forward state")
    raw_routes = value.get("routes")
    if not isinstance(raw_routes, list):
        raise RuntimeError("Invalid HTTPS forward routes")
    routes = [_validate_route(route, policy) for route in raw_routes]
    names = [str(route["name"]) for route in routes]
    ports = [int(route["listen"]) for route in routes]
    if len(names) != len(set(names)) or len(ports) != len(set(ports)):
        raise RuntimeError("Duplicate HTTPS forward name or port")
    return sorted(routes, key=lambda route: (int(route["listen"]), str(route["name"])))


def _write_text_atomic(path: str, content: str, mode: int) -> None:
    if os.path.islink(path):
        raise RuntimeError(f"Refusing symlinked managed file: {path}")
    parent = os.path.dirname(path)
    os.makedirs(parent, mode=0o755, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(
        dir=parent,
        prefix=f".{os.path.basename(path)}-",
        text=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file_obj:
            file_obj.write(content)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    finally:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass


def _write_forward_state(routes: list[dict[str, object]]) -> None:
    _write_text_atomic(
        FORWARD_STATE_FILE,
        json.dumps({"routes": routes, "version": 1}, indent=2, sort_keys=True) + "\n",
        0o644,
    )


def _forward_url(base_url: str, port: int) -> str:
    parsed = urllib.parse.urlsplit(base_url)
    hostname = parsed.hostname or "localhost"
    url_host = f"[{hostname}]" if ":" in hostname else hostname
    return f"https://{url_host}:{port}/"


def render_forward_nginx(
    routes: list[dict[str, object]],
    policy: dict[str, object],
) -> str:
    """Render isolated TLS listeners for validated loopback forwards."""

    blocks = [_FORWARD_MARKER]
    for route in routes:
        target_host = str(route["target_host"])
        upstream_host = f"[{target_host}]" if ":" in target_host else target_host
        max_body_size = route.get("max_body_size")
        body_size_directive = (
            f"\n    client_max_body_size {max_body_size};"
            if max_body_size is not None
            else ""
        )
        profile_headers = ""
        if route["profile"] == "godot":
            profile_headers = """
        add_header Cross-Origin-Opener-Policy "same-origin" always;
        add_header Cross-Origin-Embedder-Policy "require-corp" always;"""
        host_header = (
            "$proxy_host" if route["profile"] == "syncthing" else "$http_host"
        )
        blocks.append(
            f"""
# {route['name']} ({route['owner']}, {route['profile']})
server {{
    listen {route['listen']} ssl;
    listen [::]:{route['listen']} ssl;
    http2 on;
    server_name _;

    ssl_certificate {policy['certificate']};
    ssl_certificate_key {policy['certificate_key']};
    ssl_protocols TLSv1.2 TLSv1.3;{body_size_directive}

    location / {{
        proxy_pass http://{upstream_host}:{route['target_port']};
        proxy_http_version 1.1;
        proxy_set_header Host {host_header};
        proxy_set_header X-Forwarded-Host $http_host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 1d;
        proxy_send_timeout 1d;{profile_headers}
        add_header X-Content-Type-Options "nosniff" always;
        add_header Cache-Control "no-cache" always;
    }}
}}
""".rstrip()
        )
    return "\n".join(blocks) + "\n"


def _run_bounded(
    command: list[str],
    *,
    capture_output: bool = False,
    text: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        return run_command(
            command,
            check=False,
            capture_output=capture_output,
            text=text,
            timeout=_SYSTEM_COMMAND_TIMEOUT_SECONDS,
        )
    except CommandTimeoutError as exc:
        raise RuntimeError(str(exc)) from exc


def _run_checked(command: list[str], label: str) -> subprocess.CompletedProcess[str]:
    try:
        result = _run_bounded(command, capture_output=True)
    except RuntimeError as exc:
        raise RuntimeError(f"{label}: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or label).strip()
        raise RuntimeError(f"{label}: {detail}")
    return result


def _ufw_rules() -> list[tuple[int, str, str]]:
    result = _run_checked(["ufw", "status", "numbered"], "Could not inspect UFW")
    rules: list[tuple[int, str, str]] = []
    for line in result.stdout.splitlines():
        match = _UFW_NUMBERED_RULE_RE.match(line.strip())
        if not match:
            continue
        comment = line.split("#", 1)[1].strip() if "#" in line else ""
        rules.append((int(match.group(1)), comment, line))
    return rules


def _desired_firewall_rules(
    routes: list[dict[str, object]],
    policy: dict[str, object],
) -> dict[str, list[str]]:
    desired: dict[str, list[str]] = {}
    sources = policy["access_sources"]
    for route in routes:
        port = int(route["listen"])
        name = str(route["name"])
        if sources:
            for source in sources:
                comment = f"{_FORWARD_RULE_PREFIX} {name} {port}/tcp source {source}"
                desired[comment] = [
                    "ufw",
                    "allow",
                    "from",
                    str(source),
                    "to",
                    "any",
                    "port",
                    str(port),
                    "proto",
                    "tcp",
                    "comment",
                    comment,
                ]
        else:
            comment = f"{_FORWARD_RULE_PREFIX} {name} {port}/tcp global"
            desired[comment] = ["ufw", "allow", f"{port}/tcp", "comment", comment]
    return desired


def _ufw_rule_matches_port(line: str, port: int) -> bool:
    return re.search(rf"(?<![0-9]){port}/tcp(?![0-9])", line) is not None


def _reconcile_firewall(
    routes: list[dict[str, object]],
    policy: dict[str, object],
) -> None:
    if shutil.which("ufw") is None:
        if not routes:
            return
        raise RuntimeError("HTTPS forwarding requires UFW")
    status = _run_checked(["ufw", "status"], "Could not inspect UFW status")
    if "Status: active" not in status.stdout:
        if not routes:
            return
        raise RuntimeError("HTTPS forwarding requires an active UFW firewall")
    existing = _ufw_rules()
    managed_comments = {
        comment for _number, comment, _line in existing if comment.startswith(_FORWARD_RULE_PREFIX)
    }
    for route in routes:
        listen_port = int(route["listen"])
        conflicts = [
            line
            for _number, comment, line in existing
            if _ufw_rule_matches_port(line, listen_port)
            and "ALLOW IN" in line
            and not comment.startswith(_FORWARD_RULE_PREFIX)
        ]
        if conflicts:
            raise RuntimeError(
                f"Unmanaged UFW rules already expose HTTPS forward port {route['listen']}"
            )

    desired = _desired_firewall_rules(routes, policy)
    for comment, command in desired.items():
        if comment not in managed_comments:
            _run_checked(command, f"Could not add UFW rule for {comment}")

    observed = _ufw_rules()
    observed_comments = {comment for _number, comment, _line in observed}
    missing = set(desired) - observed_comments
    if missing:
        raise RuntimeError("UFW did not retain requested HTTPS forward rules")
    stale_numbers = [
        number
        for number, comment, _line in observed
        if comment.startswith(_FORWARD_RULE_PREFIX) and comment not in desired
    ]
    for number in sorted(stale_numbers, reverse=True):
        _run_checked(
            ["ufw", "--force", "delete", str(number)],
            "Could not remove stale HTTPS forward firewall rule",
        )


def _ensure_forward_link() -> bool:
    if os.path.lexists(FORWARD_NGINX_LINK):
        if not (
            os.path.islink(FORWARD_NGINX_LINK)
            and os.path.realpath(FORWARD_NGINX_LINK) == FORWARD_NGINX_SITE
        ):
            raise RuntimeError("Refusing unmanaged HTTPS forward Nginx link")
        return False
    os.symlink(FORWARD_NGINX_SITE, FORWARD_NGINX_LINK)
    return True


def _restore_file(path: str, previous: str | None, mode: int) -> None:
    if previous is None:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
    else:
        _write_text_atomic(path, previous, mode)


def _apply_forwards(
    routes: list[dict[str, object]],
    policy: dict[str, object],
) -> None:
    if os.geteuid() != 0:
        raise RuntimeError("Run HTTPS forward mutations with sudo")
    previous_site = None
    previous_state = None
    try:
        with open(FORWARD_NGINX_SITE, encoding="utf-8") as file_obj:
            previous_site = file_obj.read()
        if _FORWARD_MARKER not in previous_site:
            raise RuntimeError("Refusing unmanaged HTTPS forward Nginx site")
    except FileNotFoundError:
        pass
    try:
        with open(FORWARD_STATE_FILE, encoding="utf-8") as file_obj:
            previous_state = file_obj.read()
    except FileNotFoundError:
        pass

    link_created = False
    try:
        _write_text_atomic(FORWARD_NGINX_SITE, render_forward_nginx(routes, policy), 0o644)
        link_created = _ensure_forward_link()
        _run_checked(["nginx", "-t"], "Nginx rejected HTTPS forwarding")
        _reconcile_firewall(routes, policy)
        _write_forward_state(routes)
        _run_checked(["systemctl", "reload", "nginx"], "Could not reload Nginx")
    except Exception:
        _restore_file(FORWARD_NGINX_SITE, previous_site, 0o644)
        _restore_file(FORWARD_STATE_FILE, previous_state, 0o644)
        if link_created:
            try:
                os.unlink(FORWARD_NGINX_LINK)
            except FileNotFoundError:
                pass
        try:
            old_routes = _load_forwards(policy) if previous_state is not None else []
            _reconcile_firewall(old_routes, policy)
            _run_checked(["systemctl", "reload", "nginx"], "Could not restore Nginx")
        except Exception:
            pass
        raise


def _requesting_username(policy: dict[str, object]) -> str:
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and sudo_user != "root":
        username = _validate_username(sudo_user)
    else:
        username = _validate_username(pwd.getpwuid(os.getuid()).pw_name)
    if username not in policy["users"]:
        raise RuntimeError(f"User is not allowed to manage HTTPS forwards: {username}")
    return username


def _port_available(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", port))
    except OSError:
        return False
    finally:
        sock.close()
    return True


def _select_listen_port(
    requested: str,
    routes: list[dict[str, object]],
    policy: dict[str, object],
    existing: dict[str, object] | None,
) -> int:
    used = {int(route["listen"]) for route in routes if route is not existing}
    if requested == "auto":
        if existing is not None:
            return int(existing["listen"])
        for port in range(int(policy["forward_port_min"]), int(policy["forward_port_max"]) + 1):
            if port not in used and _port_available(port):
                return port
        raise RuntimeError("No HTTPS forwarding port is available")
    try:
        port = int(requested)
    except ValueError as exc:
        raise ValueError("--listen must be 'auto' or an integer port") from exc
    if not policy["forward_port_min"] <= port <= policy["forward_port_max"]:
        raise ValueError(
            "--listen must be inside the managed forwarding range "
            f"{policy['forward_port_min']}-{policy['forward_port_max']}"
        )
    if port in used:
        raise ValueError(f"HTTPS port {port} is already assigned")
    if existing is None or port != existing["listen"]:
        if not _port_available(port):
            raise ValueError(f"HTTPS port {port} is already in use")
    return port


def _game_account() -> pwd.struct_passwd:
    account = pwd.getpwuid(os.getuid())
    if account.pw_uid == 0:
        raise RuntimeError("Run game commands as the configured non-root user")
    _validate_username(account.pw_name)
    return account


def _user_root(account: pwd.struct_passwd) -> str:
    root = os.path.join(GAMES_ROOT, account.pw_name)
    if os.path.islink(root) or not os.path.isdir(root):
        raise RuntimeError(f"Managed publishing directory is unavailable: {root}")
    if os.stat(root).st_uid != account.pw_uid:
        raise RuntimeError(f"Publishing directory is not owned by {account.pw_name}")
    return root


def _game_records(account: pwd.struct_passwd) -> list[dict[str, object]]:
    root = _user_root(account)
    records: list[dict[str, object]] = []
    for name in sorted(os.listdir(root)):
        if name.startswith(".") or name == godot_web_publish.CATALOG_FILE:
            continue
        path = os.path.join(root, name)
        if os.path.islink(path) or not os.path.isdir(path):
            continue
        metadata = godot_web_publish._metadata_for_game(path)
        records.append(
            {
                "debug": metadata.get("debug"),
                "game": name,
                "preset": metadata.get("preset"),
                "published_at": metadata.get("published_at"),
                "title": metadata.get("title") or name,
                "url": godot_web_publish._published_url(account.pw_name, name),
            }
        )
    return records


def _print_games(as_json: bool) -> int:
    records = _game_records(_game_account())
    if as_json:
        print(json.dumps({"games": records}, sort_keys=True))
    elif not records:
        print("No games published")
    else:
        for record in records:
            print(f"{record['game']}\t{record['url'] or '-'}")
    return 0


def _print_game_url(game: str) -> int:
    _validate_name(game, "game")
    account = _game_account()
    path = os.path.join(_user_root(account), game)
    if os.path.islink(path) or not os.path.isdir(path):
        raise RuntimeError(f"Published game does not exist: {game}")
    url = godot_web_publish._published_url(account.pw_name, game)
    if not url:
        raise RuntimeError("Internal-web base URL is unavailable")
    print(url)
    return 0


def _remove_game(game: str, confirmed: bool) -> int:
    _validate_name(game, "game")
    if not confirmed:
        raise ValueError("Removing a published game requires --yes")
    account = _game_account()
    root = _user_root(account)
    path = os.path.join(root, game)
    if os.path.islink(path) or not os.path.isdir(path):
        raise RuntimeError(f"Published game does not exist: {game}")
    if os.stat(path).st_uid != account.pw_uid:
        raise RuntimeError(f"Published game is not owned by {account.pw_name}: {game}")
    shutil.rmtree(path)
    godot_web_publish.write_user_catalog(root, account.pw_name)
    print(f"Removed published game {game}")
    return 0


def _print_sites(as_json: bool) -> int:
    account = _game_account()
    records = static_web_publish.list_sites(account)
    if as_json:
        print(json.dumps({"sites": records}, sort_keys=True))
    elif not records:
        print("No sites published")
    else:
        for record in records:
            print(f"{record['site']}\t{record.get('url') or '-'}")
    return 0


def _print_site_url(name: str) -> int:
    _validate_name(name, "site name")
    account = _game_account()
    path = os.path.join(static_web_publish.SITES_ROOT, account.pw_name, name)
    if os.path.islink(path) or not os.path.isdir(path):
        raise RuntimeError(f"Published site does not exist: {name}")
    url = static_web_publish.published_url(account.pw_name, name)
    if not url:
        raise RuntimeError("Internal-web base URL is unavailable")
    print(url)
    return 0


def _remove_site(name: str, confirmed: bool, as_json: bool) -> int:
    account = _game_account()
    static_web_publish.remove_site(account, name, confirmed)
    if as_json:
        print(json.dumps({"name": name, "removed": True}, sort_keys=True))
    else:
        print(f"Removed published site {name}")
    return 0


def _https_headers(url: str) -> tuple[int, dict[str, str]]:
    request = urllib.request.Request(
        url,
        headers={"Range": "bytes=0-0", "User-Agent": "infra-web-doctor/1"},
    )
    try:
        with urllib.request.urlopen(
            request,
            context=ssl.create_default_context(),
            timeout=15,
        ) as response:
            return response.status, {key.lower(): value for key, value in response.headers.items()}
    except urllib.error.HTTPError as exc:
        return exc.code, {key.lower(): value for key, value in exc.headers.items()}
    except (OSError, urllib.error.URLError) as exc:
        raise RuntimeError(f"HTTPS request failed for {url}: {exc}") from exc


def _forward_https_status_is_healthy(
    status: int,
    headers: dict[str, str],
) -> bool:
    """Accept a deliberate HTTP authentication challenge from a live forward."""

    return status < 400 or (
        status == 401 and bool(headers.get("www-authenticate", "").strip())
    )


def _validate_health_path(value: str) -> str:
    if (
        not value.startswith("/")
        or value.startswith("//")
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError("Health path must start with one '/' and contain no controls")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.fragment:
        raise ValueError("Health path must be a relative HTTP path")
    return value


def _tcp_ready(host: str, port: int, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _http_ready(host: str, port: int, path: str, timeout: float = 1.0) -> bool:
    url_host = f"[{host}]" if ":" in host else host
    request = urllib.request.Request(
        f"http://{url_host}:{port}{path}",
        headers={"User-Agent": "infra-web-readiness/1"},
    )
    try:
        with open_loopback(request, timeout=timeout) as response:
            return response.status < 500
    except urllib.error.HTTPError as exc:
        return exc.code < 500
    except (OSError, urllib.error.URLError):
        return False


def _wait_for_upstream(host: str, port: int, path: str, seconds: float) -> None:
    if not 0 < seconds <= 300:
        raise ValueError("Wait duration must be greater than 0 and at most 300 seconds")
    health_path = _validate_health_path(path)
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if _http_ready(host, port, health_path):
            return
        time.sleep(0.2)
    raise RuntimeError(
        f"Upstream did not become ready at http://{host}:{port}{health_path} "
        f"within {seconds:g} seconds"
    )


def _open_url(url: str, username: str | None = None) -> None:
    if username and os.geteuid() == 0:
        subprocess.Popen(
            ["runuser", "-u", username, "--", "xdg-open", url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    else:
        webbrowser.open(url)


def _site_doctor(name: str, as_json: bool) -> int:
    _validate_name(name, "site name")
    account = _game_account()
    site_path = os.path.join(static_web_publish.SITES_ROOT, account.pw_name, name)
    if os.path.islink(site_path) or not os.path.isdir(site_path):
        raise RuntimeError(f"Published site does not exist: {name}")
    url = static_web_publish.published_url(account.pw_name, name)
    if not url:
        raise RuntimeError("Internal-web base URL is unavailable")
    status, headers = _https_headers(url)
    errors = [f"HTTPS endpoint returned {status}"] if status >= 400 else []
    result = {
        "errors": errors,
        "headers": headers,
        "name": name,
        "ok": not errors,
        "status": status,
        "url": url,
    }
    if as_json:
        print(json.dumps(result, sort_keys=True))
    elif errors:
        print(f"{name}: failed ({'; '.join(errors)})")
    else:
        print(f"{name}: healthy ({url})")
    return 0 if not errors else 1


def _doctor(name: str, as_json: bool) -> int:
    _validate_name(name)
    account = _game_account()
    game_path = os.path.join(_user_root(account), name)
    profile = "general"
    wasm_url = None
    if os.path.isdir(game_path) and not os.path.islink(game_path):
        url = godot_web_publish._published_url(account.pw_name, name)
        if not url:
            raise RuntimeError("Internal-web base URL is unavailable")
        profile = "godot"
        for current_dir, _dirs, files in os.walk(game_path):
            wasm_name = next((file_name for file_name in files if file_name.endswith(".wasm")), None)
            if wasm_name:
                relative = os.path.relpath(os.path.join(current_dir, wasm_name), game_path)
                wasm_url = urllib.parse.urljoin(url, urllib.parse.quote(relative))
                break
    else:
        policy = _load_policy()
        routes = _load_forwards(policy)
        route = next((item for item in routes if item["name"] == name), None)
        if route is None:
            raise RuntimeError(f"No published game or HTTPS forward named {name}")
        if route["owner"] != account.pw_name:
            raise RuntimeError(f"HTTPS forward is owned by another user: {name}")
        url = _forward_url(str(policy["base_url"]), int(route["listen"]))
        profile = str(route["profile"])

    status, headers = _https_headers(url)
    errors: list[str] = []
    if not _forward_https_status_is_healthy(status, headers):
        errors.append(f"HTTPS endpoint returned {status}")
    if profile == "godot":
        if headers.get("cross-origin-opener-policy") != "same-origin":
            errors.append("missing Cross-Origin-Opener-Policy: same-origin")
        if headers.get("cross-origin-embedder-policy") != "require-corp":
            errors.append("missing Cross-Origin-Embedder-Policy: require-corp")
    wasm_headers: dict[str, str] = {}
    if wasm_url:
        wasm_status, wasm_headers = _https_headers(wasm_url)
        if wasm_status >= 400:
            errors.append(f"WASM endpoint returned {wasm_status}")
        content_type = wasm_headers.get("content-type", "").split(";", 1)[0]
        if content_type != "application/wasm":
            errors.append(f"WASM content type is {content_type or 'missing'}")
    result = {
        "errors": errors,
        "headers": headers,
        "name": name,
        "ok": not errors,
        "status": status,
        "url": url,
        "wasm_headers": wasm_headers,
    }
    if as_json:
        print(json.dumps(result, sort_keys=True))
    elif errors:
        print(f"{name}: failed ({'; '.join(errors)})")
    else:
        print(f"{name}: healthy ({url})")
    return 0 if not errors else 1


def _print_ca(as_json: bool) -> int:
    policy = _load_policy()
    path = policy.get("ca_certificate")
    if not isinstance(path, str) or not os.path.isfile(path):
        result = {"ca_certificate": None, "publicly_trusted": True}
    else:
        digest = hashlib.sha256()
        with open(path, "rb") as file_obj:
            while chunk := file_obj.read(1024 * 1024):
                digest.update(chunk)
        result = {
            "ca_certificate": path,
            "publicly_trusted": False,
            "sha256": digest.hexdigest(),
            "url": f"{str(policy['base_url']).rstrip('/')}/infra-tools-ca.crt",
        }
    if as_json:
        print(json.dumps(result, sort_keys=True))
    elif result["publicly_trusted"]:
        print("The internal-web endpoint uses a publicly trusted certificate")
    else:
        print(
            f"{result['ca_certificate']}\n{result['url']}\n"
            f"SHA-256 {result['sha256']}"
        )
    return 0


@_serialized_mutation
def _forward_add(args: argparse.Namespace) -> int:
    name = _validate_name(args.name, "forward name")
    policy = _load_policy()
    owner = _requesting_username(policy)
    routes = _load_forwards(policy)
    existing = next((route for route in routes if route["name"] == name), None)
    if existing is not None and existing["owner"] != owner:
        raise RuntimeError(f"HTTPS forward is owned by another user: {name}")
    target_host, target_port = _parse_upstream(args.to)
    if args.wait < 0 or args.wait > 300:
        raise ValueError("--wait must be from 0 through 300 seconds")
    _validate_health_path(args.health)
    if args.wait:
        _wait_for_upstream(target_host, target_port, args.health, args.wait)
    max_body_size = _validate_body_size(args.max_body_size, "--max-body-size")
    listen = _select_listen_port(args.listen, routes, policy, existing)
    route = {
        "listen": listen,
        "name": name,
        "owner": owner,
        "profile": args.profile,
        "target_host": target_host,
        "target_port": target_port,
        **({"max_body_size": max_body_size} if max_body_size is not None else {}),
    }
    updated = [item for item in routes if item["name"] != name]
    updated.append(route)
    updated.sort(key=lambda item: (int(item["listen"]), str(item["name"])))
    _apply_forwards(updated, policy)
    result = {**route, "url": _forward_url(str(policy["base_url"]), listen)}
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(
            f"{name}: {result['url']} -> http://{target_host}:{target_port} "
            f"({args.profile})"
        )
    if args.open:
        _open_url(str(result["url"]), owner)
    return 0


@_serialized_mutation
def _forward_remove(args: argparse.Namespace) -> int:
    name = _validate_name(args.name, "forward name")
    policy = _load_policy()
    owner = _requesting_username(policy)
    routes = _load_forwards(policy)
    existing = next((route for route in routes if route["name"] == name), None)
    if existing is None:
        raise RuntimeError(f"HTTPS forward does not exist: {name}")
    if existing["owner"] != owner:
        raise RuntimeError(f"HTTPS forward is owned by another user: {name}")
    _apply_forwards([route for route in routes if route["name"] != name], policy)
    if args.json:
        print(json.dumps({"name": name, "removed": True}, sort_keys=True))
    else:
        print(f"Removed HTTPS forward {name}")
    return 0


def _forward_list(as_json: bool) -> int:
    policy = _load_policy()
    account = pwd.getpwuid(os.getuid())
    username = account.pw_name
    routes = [
        {
            **route,
            "ready": _tcp_ready(str(route["target_host"]), int(route["target_port"])),
            "url": _forward_url(str(policy["base_url"]), int(route["listen"])),
        }
        for route in _load_forwards(policy)
        if os.geteuid() == 0 or route["owner"] == username
    ]
    if as_json:
        print(json.dumps({"forwards": routes}, sort_keys=True))
    elif not routes:
        print("No HTTPS forwards configured")
    else:
        for route in routes:
            print(
                f"{route['name']}\t{route['url']}\thttp://"
                f"{route['target_host']}:{route['target_port']}\t{route['profile']}\t"
                f"{'ready' if route['ready'] else 'down'}"
            )
    return 0


def _forward_route(name: str) -> tuple[dict[str, object], dict[str, object]]:
    _validate_name(name, "forward name")
    policy = _load_policy()
    routes = _load_forwards(policy)
    route = next((item for item in routes if item["name"] == name), None)
    if route is None:
        raise RuntimeError(f"HTTPS forward does not exist: {name}")
    account = pwd.getpwuid(os.getuid())
    if os.geteuid() != 0 and route["owner"] != account.pw_name:
        raise RuntimeError(f"HTTPS forward is owned by another user: {name}")
    return policy, route


def _forward_print_url(name: str) -> int:
    policy, route = _forward_route(name)
    print(_forward_url(str(policy["base_url"]), int(route["listen"])))
    return 0


@_serialized_mutation
def _forward_prune(confirmed: bool, as_json: bool) -> int:
    if not confirmed:
        raise ValueError("Pruning dead HTTPS forwards requires --yes")
    policy = _load_policy()
    owner = _requesting_username(policy)
    routes = _load_forwards(policy)
    preview_names = {
        str(preview["name"])
        for preview in _load_previews(policy)
        if preview["owner"] == owner
    }
    removed = [
        str(route["name"])
        for route in routes
        if route["owner"] == owner
        and route["name"] not in preview_names
        and not _tcp_ready(str(route["target_host"]), int(route["target_port"]))
    ]
    updated = [route for route in routes if str(route["name"]) not in removed]
    if removed:
        _apply_forwards(updated, policy)
    result = {"removed": removed}
    if as_json:
        print(json.dumps(result, sort_keys=True))
    elif removed:
        print(f"Removed {len(removed)} dead forward(s): {', '.join(removed)}")
    else:
        print("No dead forwards found")
    return 0


@_serialized_mutation
def _forward_reconcile(as_json: bool) -> int:
    policy = _load_policy()
    routes = _load_forwards(policy)
    _apply_forwards(routes, policy)
    if as_json:
        print(json.dumps({"forwards": len(routes), "reconciled": True}, sort_keys=True))
    else:
        print(f"Reconciled {len(routes)} HTTPS forward(s)")
    return 0


def _validate_preview(value: object, policy: dict[str, object]) -> dict[str, object]:
    if not isinstance(value, dict):
        raise RuntimeError("Invalid live-preview state")
    name = _validate_name(str(value.get("name", "")), "preview name")
    owner = _validate_username(value.get("owner"))
    if owner not in policy["users"]:
        raise RuntimeError("Live-preview owner is not allowed by policy")
    unit = value.get("unit")
    expected_unit = f"{_PREVIEW_UNIT_PREFIX}-{owner}-{name}.service"
    if unit != expected_unit:
        raise RuntimeError("Invalid live-preview systemd unit")
    project = value.get("project")
    if (
        not isinstance(project, str)
        or not os.path.isabs(project)
        or any(ord(character) < 32 or ord(character) == 127 for character in project)
    ):
        raise RuntimeError("Invalid live-preview project path")
    target_host = value.get("target_host")
    if target_host != "127.0.0.1":
        raise RuntimeError("Live previews must target IPv4 loopback")
    target_port = value.get("target_port")
    listen = value.get("listen")
    if (
        not isinstance(target_port, int)
        or isinstance(target_port, bool)
        or not 1024 <= target_port <= 65535
        or not isinstance(listen, int)
        or isinstance(listen, bool)
        or not policy["forward_port_min"] <= listen <= policy["forward_port_max"]
    ):
        raise RuntimeError("Invalid live-preview port")
    profile = value.get("profile")
    if profile not in _PROFILES:
        raise RuntimeError("Invalid live-preview profile")
    health = _validate_health_path(str(value.get("health", "/")))
    created_at = value.get("created_at")
    if not isinstance(created_at, str) or not created_at:
        raise RuntimeError("Invalid live-preview creation time")
    return {
        "created_at": created_at,
        "health": health,
        "listen": listen,
        "name": name,
        "owner": owner,
        "profile": profile,
        "project": project,
        "target_host": target_host,
        "target_port": target_port,
        "unit": unit,
    }


def _load_previews(policy: dict[str, object]) -> list[dict[str, object]]:
    if not os.path.exists(PREVIEW_STATE_FILE):
        return []
    value = _read_json(PREVIEW_STATE_FILE, "live-preview state")
    if not isinstance(value, dict) or value.get("version") != 1:
        raise RuntimeError("Invalid live-preview state")
    raw_previews = value.get("previews")
    if not isinstance(raw_previews, list):
        raise RuntimeError("Invalid live-preview records")
    previews = [_validate_preview(preview, policy) for preview in raw_previews]
    identities = [(str(item["owner"]), str(item["name"])) for item in previews]
    ports = [int(item["target_port"]) for item in previews]
    if len(identities) != len(set(identities)) or len(ports) != len(set(ports)):
        raise RuntimeError("Duplicate live-preview identity or target port")
    return sorted(previews, key=lambda item: (str(item["owner"]), str(item["name"])))


def _write_preview_state(previews: list[dict[str, object]]) -> None:
    _write_text_atomic(
        PREVIEW_STATE_FILE,
        json.dumps({"previews": previews, "version": 1}, indent=2, sort_keys=True) + "\n",
        0o644,
    )


def _preview_project(value: str, account: pwd.struct_passwd) -> str:
    if value == "~":
        value = account.pw_dir
    elif value.startswith("~/"):
        value = os.path.join(account.pw_dir, value[2:])
    elif value.startswith("~"):
        raise ValueError("--project may not reference another user's home")
    unresolved = os.path.abspath(value)
    if os.path.islink(unresolved):
        raise ValueError("--project may not be a symlink")
    project = os.path.realpath(unresolved)
    if not os.path.isdir(project):
        raise ValueError("--project must be a real directory")
    if os.stat(project).st_uid != account.pw_uid:
        raise ValueError("--project must be owned by the requesting user")
    return project


def _select_target_port(
    requested: str,
    routes: list[dict[str, object]],
    previews: list[dict[str, object]],
) -> int:
    used = {int(route["target_port"]) for route in routes}
    used.update(int(preview["target_port"]) for preview in previews)
    if requested == "auto":
        for port in range(3000, 8000):
            if port not in used and _port_available(port):
                return port
        raise RuntimeError("No private live-preview port is available")
    try:
        port = int(requested)
    except ValueError as exc:
        raise ValueError("--port must be 'auto' or an integer") from exc
    if not 1024 <= port <= 65535:
        raise ValueError("--port must be an unprivileged TCP port")
    if port in used or not _port_available(port):
        raise ValueError(f"Private preview port {port} is already in use")
    return port


def _read_preview_package(project: str) -> dict[str, object]:
    path = os.path.join(project, "package.json")
    if not os.path.isfile(path) or os.path.islink(path):
        raise ValueError("No safe package.json found; provide a command after --")
    try:
        with open(path, encoding="utf-8") as file_obj:
            value = json.load(file_obj)
    except (OSError, ValueError) as exc:
        raise ValueError("Could not read package.json") from exc
    if not isinstance(value, dict):
        raise ValueError("package.json must contain an object")
    return value


def _automatic_preview_command(project: str, port: int) -> list[str]:
    package = _read_preview_package(project)
    scripts = package.get("scripts")
    dependencies = {
        key
        for field in ("dependencies", "devDependencies")
        for key in (
            package.get(field).keys() if isinstance(package.get(field), dict) else []
        )
    }
    if "vite" not in dependencies:
        raise ValueError("Automatic preview currently requires Vite; provide a command after --")
    if not isinstance(scripts, dict):
        raise ValueError("package.json has no scripts; provide a command after --")
    script = next(
        (name for name in ("dev", "preview") if isinstance(scripts.get(name), str)),
        None,
    )
    if script is None:
        raise ValueError("No dev or preview script found; provide a command after --")
    if os.path.isfile(os.path.join(project, "pnpm-lock.yaml")):
        command = ["corepack", "pnpm", "run", script]
    elif os.path.isfile(os.path.join(project, "yarn.lock")):
        command = ["corepack", "yarn", "run", script]
    else:
        command = ["npm", "run", script]
    return [*command, "--", "--host", "127.0.0.1", "--port", str(port), "--strictPort"]


def _install_preview_dependencies(project: str, account: pwd.struct_passwd) -> None:
    if os.path.isdir(os.path.join(project, "node_modules")):
        return
    if os.path.isfile(os.path.join(project, "pnpm-lock.yaml")):
        command = ["corepack", "pnpm", "install", "--frozen-lockfile"]
    elif os.path.isfile(os.path.join(project, "yarn.lock")):
        command = ["corepack", "yarn", "install", "--immutable"]
    elif os.path.isfile(os.path.join(project, "package-lock.json")):
        command = ["npm", "ci"]
    else:
        command = ["npm", "install"]
    command = _resolve_preview_executable(command, account)
    runtime_path = ":".join(
        dict.fromkeys(
            [
                os.path.dirname(command[0]),
                os.path.join(account.pw_dir, ".local", "bin"),
                "/usr/local/bin",
                "/usr/bin",
                "/bin",
            ]
        )
    )
    try:
        result = run_command(
            [
                "runuser",
                "-u",
                account.pw_name,
                "--",
                "env",
                f"HOME={account.pw_dir}",
                f"PATH={runtime_path}",
                *command,
            ],
            cwd=project,
            check=False,
            timeout=_PREVIEW_INSTALL_TIMEOUT_SECONDS,
        )
    except CommandTimeoutError as exc:
        raise RuntimeError(
            "Preview dependency installation exceeded "
            f"{_PREVIEW_INSTALL_TIMEOUT_SECONDS} seconds"
        ) from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"Preview dependency installation failed with exit code {result.returncode}"
        )


def _preview_command(raw: list[str], project: str, port: int) -> list[str]:
    command = raw[1:] if raw and raw[0] == "--" else list(raw)
    if not command:
        return _automatic_preview_command(project, port)
    replaced = [
        argument.replace("{port}", str(port)).replace("{host}", "127.0.0.1")
        for argument in command
    ]
    if not replaced[0] or any(
        any(ord(character) < 32 or ord(character) == 127 for character in argument)
        for argument in replaced
    ):
        raise ValueError("Preview command arguments must be non-empty and contain no controls")
    return replaced


def _resolve_preview_executable(
    command: list[str],
    account: pwd.struct_passwd,
    working_directory: str | None = None,
) -> list[str]:
    executable = command[0]
    if os.path.isabs(executable):
        resolved = os.path.realpath(executable)
    else:
        candidates: list[str | None] = []
        if working_directory is not None and os.path.sep in executable:
            candidates.append(os.path.join(working_directory, executable))
        candidates.extend(
            [
                shutil.which(executable),
                os.path.join(account.pw_dir, ".local", "bin", executable),
                os.path.join(account.pw_dir, ".nvm", "current", "bin", executable),
                os.path.join("/usr/local/bin", executable),
                os.path.join("/usr/bin", executable),
            ]
        )
        resolved = next(
            (
                os.path.realpath(candidate)
                for candidate in candidates
                if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK)
            ),
            "",
        )
    if not resolved or not os.path.isfile(resolved) or not os.access(resolved, os.X_OK):
        raise ValueError(f"Preview executable is unavailable: {executable}")
    return [resolved, *command[1:]]


def _systemd_quote(value: str) -> str:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("Systemd value contains control characters")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_preview_unit(
    name: str,
    account: pwd.struct_passwd,
    project: str,
    launcher: str,
) -> str:
    """Render a bounded system service that executes only as the requesting user."""

    _validate_name(name, "preview name")
    group_name = _validate_username(grp.getgrgid(account.pw_gid).gr_name)
    return f"""[Unit]
Description=infra_tools live preview: {name} ({account.pw_name})
After=network.target

[Service]
Type=simple
User={account.pw_name}
Group={group_name}
WorkingDirectory={_systemd_quote(project)}
Environment={_systemd_quote('HOME=' + account.pw_dir)}
ExecStart={_systemd_quote(launcher)}
Restart=on-failure
RestartSec=2
TimeoutStopSec=10
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=full
ProtectControlGroups=true
ProtectKernelModules=true
ProtectKernelTunables=true
LockPersonality=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
RestrictSUIDSGID=true
TasksMax=256
MemoryMax=1G
"""


def _preview_paths(account: pwd.struct_passwd, name: str) -> tuple[str, str, str]:
    unit = f"{_PREVIEW_UNIT_PREFIX}-{account.pw_name}-{name}.service"
    launcher = os.path.join(PREVIEW_RUNTIME_ROOT, f"{account.pw_name}-{name}.sh")
    unit_path = os.path.join(SYSTEMD_ROOT, unit)
    return unit, unit_path, launcher


def _write_preview_service(
    account: pwd.struct_passwd,
    name: str,
    project: str,
    command: list[str],
    port: int,
) -> tuple[str, str]:
    if os.path.lexists(PREVIEW_RUNTIME_ROOT) and (
        os.path.islink(PREVIEW_RUNTIME_ROOT) or not os.path.isdir(PREVIEW_RUNTIME_ROOT)
    ):
        raise RuntimeError(f"Refusing unsafe preview runtime root: {PREVIEW_RUNTIME_ROOT}")
    os.makedirs(PREVIEW_RUNTIME_ROOT, mode=0o755, exist_ok=True)
    unit, unit_path, launcher = _preview_paths(account, name)
    for path, label in ((unit_path, "unit"), (launcher, "launcher")):
        if os.path.lexists(path):
            raise RuntimeError(f"Refusing existing live-preview {label}: {path}")
    runtime_path = ":".join(
        dict.fromkeys(
            [
                os.path.dirname(command[0]),
                os.path.join(account.pw_dir, ".local", "bin"),
                "/usr/local/bin",
                "/usr/bin",
                "/bin",
            ]
        )
    )
    script = (
        "#!/bin/sh\n"
        f"export PATH={shlex.quote(runtime_path)}\n"
        "export HOST=127.0.0.1\n"
        f"export PORT={port}\n"
        f"exec {shlex.join(command)}\n"
    )
    try:
        _write_text_atomic(launcher, script, 0o750)
        os.chown(launcher, account.pw_uid, account.pw_gid)
        os.chmod(launcher, 0o700)
        _write_text_atomic(unit_path, render_preview_unit(name, account, project, launcher), 0o644)
        _run_checked(["systemctl", "daemon-reload"], "Could not reload systemd")
        _run_checked(["systemctl", "start", unit], "Could not start live preview")
    except Exception:
        for path in (unit_path, launcher):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
        try:
            _run_checked(["systemctl", "daemon-reload"], "Could not reload systemd")
        except (OSError, RuntimeError):
            pass
        raise
    return unit, launcher


def _preview_active(unit: str) -> bool:
    try:
        result = _run_bounded(
            ["systemctl", "is-active", "--quiet", unit],
            capture_output=True,
        )
    except (OSError, RuntimeError):
        return False
    return result.returncode == 0


def _remove_preview_service(record: dict[str, object]) -> None:
    unit = str(record["unit"])
    unit_path = os.path.join(SYSTEMD_ROOT, unit)
    launcher = os.path.join(
        PREVIEW_RUNTIME_ROOT,
        f"{record['owner']}-{record['name']}.sh",
    )
    _run_bounded(
        ["systemctl", "stop", unit],
        capture_output=True,
    )
    for path, label in ((unit_path, "unit"), (launcher, "launcher")):
        if os.path.lexists(path):
            if os.path.islink(path) or not os.path.isfile(path):
                raise RuntimeError(f"Refusing unsafe live-preview {label}: {path}")
            os.unlink(path)
    _run_bounded(
        ["systemctl", "daemon-reload"],
        capture_output=True,
    )
    _run_bounded(
        ["systemctl", "reset-failed", unit],
        capture_output=True,
    )


def _preview_for_user(
    name: str,
    previews: list[dict[str, object]],
    username: str,
) -> dict[str, object]:
    _validate_name(name, "preview name")
    record = next(
        (
            preview
            for preview in previews
            if preview["name"] == name and preview["owner"] == username
        ),
        None,
    )
    if record is None:
        raise RuntimeError(f"Live preview does not exist: {name}")
    return record


def _stop_preview_record(
    record: dict[str, object],
    policy: dict[str, object],
    routes: list[dict[str, object]],
    previews: list[dict[str, object]],
) -> None:
    route = next((item for item in routes if item["name"] == record["name"]), None)
    if route is not None and (
        route["owner"] != record["owner"]
        or route["target_host"] != record["target_host"]
        or route["target_port"] != record["target_port"]
    ):
        raise RuntimeError("Live-preview route no longer matches its managed service")
    updated_routes = [item for item in routes if item is not route]
    if route is not None:
        _apply_forwards(updated_routes, policy)
    _remove_preview_service(record)
    previews.remove(record)
    _write_preview_state(previews)
    routes[:] = updated_routes


@_serialized_mutation
def _preview_start(args: argparse.Namespace) -> int:
    if os.geteuid() != 0:
        raise RuntimeError("Run live-preview mutations with sudo")
    name = _validate_name(args.name, "preview name")
    policy = _load_policy()
    owner = _requesting_username(policy)
    account = pwd.getpwnam(owner)
    project = _preview_project(args.project, account)
    if not 0 < args.wait <= 300:
        raise ValueError("--wait must be greater than 0 and at most 300 seconds")
    health = _validate_health_path(args.health)
    routes = _load_forwards(policy)
    previews = _load_previews(policy)
    existing = next(
        (
            preview
            for preview in previews
            if preview["name"] == name and preview["owner"] == owner
        ),
        None,
    )
    if existing is not None:
        if not args.replace:
            raise RuntimeError(f"Live preview already exists: {name}; use --replace")
        _stop_preview_record(existing, policy, routes, previews)
    conflicting_route = next((route for route in routes if route["name"] == name), None)
    if conflicting_route is not None:
        raise RuntimeError(f"HTTPS forward already uses the preview name: {name}")

    port = _select_target_port(args.port, routes, previews)
    if not args.preview_argv:
        _install_preview_dependencies(project, account)
    command = _resolve_preview_executable(
        _preview_command(args.preview_argv, project, port),
        account,
        project,
    )
    listen = _select_listen_port(args.listen, routes, policy, None)
    unit = ""
    launcher = ""
    route_applied = False
    try:
        unit, launcher = _write_preview_service(account, name, project, command, port)
        _wait_for_upstream("127.0.0.1", port, health, args.wait)
        route = {
            "listen": listen,
            "name": name,
            "owner": owner,
            "profile": args.profile,
            "target_host": "127.0.0.1",
            "target_port": port,
        }
        _apply_forwards([*routes, route], policy)
        route_applied = True
        record = {
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "health": health,
            "listen": listen,
            "name": name,
            "owner": owner,
            "profile": args.profile,
            "project": project,
            "target_host": "127.0.0.1",
            "target_port": port,
            "unit": unit,
        }
        previews.append(record)
        previews.sort(key=lambda item: (str(item["owner"]), str(item["name"])))
        _write_preview_state(previews)
    except Exception:
        if route_applied:
            try:
                _apply_forwards(routes, policy)
            except Exception:
                pass
        if unit:
            _remove_preview_service(
                {
                    "name": name,
                    "owner": owner,
                    "unit": unit,
                }
            )
        elif launcher:
            try:
                os.unlink(launcher)
            except FileNotFoundError:
                pass
        raise
    url = _forward_url(str(policy["base_url"]), listen)
    result = {
        "listen": listen,
        "name": name,
        "ok": True,
        "project": project,
        "target_port": port,
        "unit": unit,
        "url": url,
    }
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(f"{name}: {url} (unit {unit}, loopback {port})")
    if args.open:
        _open_url(url, owner)
    return 0


@_serialized_mutation
def _preview_stop(name: str, as_json: bool) -> int:
    if os.geteuid() != 0:
        raise RuntimeError("Run live-preview mutations with sudo")
    policy = _load_policy()
    owner = _requesting_username(policy)
    routes = _load_forwards(policy)
    previews = _load_previews(policy)
    record = _preview_for_user(name, previews, owner)
    _stop_preview_record(record, policy, routes, previews)
    if as_json:
        print(json.dumps({"name": name, "removed": True}, sort_keys=True))
    else:
        print(f"Stopped and removed live preview {name}")
    return 0


def _preview_list(as_json: bool) -> int:
    policy = _load_policy()
    account = pwd.getpwuid(os.getuid())
    previews = [
        {
            **preview,
            "active": _preview_active(str(preview["unit"])),
            "url": _forward_url(str(policy["base_url"]), int(preview["listen"])),
        }
        for preview in _load_previews(policy)
        if os.geteuid() == 0 or preview["owner"] == account.pw_name
    ]
    if as_json:
        print(json.dumps({"previews": previews}, sort_keys=True))
    elif not previews:
        print("No live previews configured")
    else:
        for preview in previews:
            print(
                f"{preview['name']}\t{preview['url']}\t{preview['project']}\t"
                f"{'active' if preview['active'] else 'stopped'}"
            )
    return 0


def _preview_print_url(name: str) -> int:
    policy = _load_policy()
    account = pwd.getpwuid(os.getuid())
    previews = _load_previews(policy)
    if os.geteuid() == 0:
        record = next((item for item in previews if item["name"] == name), None)
        if record is None:
            raise RuntimeError(f"Live preview does not exist: {name}")
    else:
        record = _preview_for_user(name, previews, account.pw_name)
    print(_forward_url(str(policy["base_url"]), int(record["listen"])))
    return 0


def _preview_logs(name: str, lines: int) -> int:
    if not 1 <= lines <= 1000:
        raise ValueError("--lines must be from 1 through 1000")
    policy = _load_policy()
    account = pwd.getpwuid(os.getuid())
    previews = _load_previews(policy)
    if os.geteuid() == 0:
        record = next((item for item in previews if item["name"] == name), None)
        if record is None:
            raise RuntimeError(f"Live preview does not exist: {name}")
    else:
        record = _preview_for_user(name, previews, account.pw_name)
    result = _run_bounded(
        ["journalctl", "-u", str(record["unit"]), "-n", str(lines), "--no-pager"],
    )
    return result.returncode


@_serialized_mutation
def _preview_prune(confirmed: bool, as_json: bool) -> int:
    if os.geteuid() != 0:
        raise RuntimeError("Run live-preview mutations with sudo")
    if not confirmed:
        raise ValueError("Pruning stopped live previews requires --yes")
    policy = _load_policy()
    owner = _requesting_username(policy)
    routes = _load_forwards(policy)
    previews = _load_previews(policy)
    removed: list[str] = []
    for record in list(previews):
        if record["owner"] != owner or _preview_active(str(record["unit"])):
            continue
        _stop_preview_record(record, policy, routes, previews)
        removed.append(str(record["name"]))
    if as_json:
        print(json.dumps({"removed": removed}, sort_keys=True))
    elif removed:
        print(f"Removed {len(removed)} stopped preview(s): {', '.join(removed)}")
    else:
        print("No stopped previews found")
    return 0


def _publish_godot(args: argparse.Namespace) -> int:
    forwarded: list[str] = []
    if args.game:
        forwarded.append(args.game)
    if args.project_positional:
        forwarded.append(args.project_positional)
    if args.project_option:
        forwarded.extend(["--project", args.project_option])
    forwarded.extend(["--preset", args.preset])
    forwarded.extend(["--build-timeout", str(args.build_timeout)])
    if args.debug:
        forwarded.append("--debug")
    if args.no_precompress:
        forwarded.append("--no-precompress")
    if args.json:
        forwarded.append("--json")
    if args.open:
        forwarded.append("--open")
    return godot_web_publish.main(forwarded)


def _publish_site(args: argparse.Namespace) -> int:
    result = static_web_publish.publish(args)
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(f"Published {result['site']} to {result['url']}")
    if args.open:
        _open_url(str(result["url"]))
    return 0


def main(argv: list[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    preview_argv: list[str] = []
    if raw_args[:2] == ["preview", "start"] and "--" in raw_args:
        separator = raw_args.index("--")
        preview_argv = raw_args[separator + 1 :]
        raw_args = raw_args[:separator]
    args = _parser().parse_args(raw_args)
    if args.command == "preview" and args.preview_command == "start":
        args.preview_argv = preview_argv
    try:
        if args.command == "publish":
            if args.publish_kind == "godot":
                return _publish_godot(args)
            if args.publish_kind == "site":
                return _publish_site(args)
        if args.command == "site":
            if args.site_command in ("list", "ls"):
                return _print_sites(args.json)
            if args.site_command == "url":
                return _print_site_url(args.name)
            if args.site_command == "doctor":
                return _site_doctor(args.name, args.json)
            if args.site_command == "remove":
                return _remove_site(args.name, args.yes, args.json)
        if args.command in ("list", "ls"):
            return _print_games(args.json)
        if args.command == "url":
            return _print_game_url(args.game)
        if args.command == "remove":
            return _remove_game(args.game, args.yes)
        if args.command == "doctor":
            return _doctor(args.name, args.json)
        if args.command == "ca":
            return _print_ca(args.json)
        if args.command == "forward":
            if args.forward_command == "add":
                return _forward_add(args)
            if args.forward_command == "remove":
                return _forward_remove(args)
            if args.forward_command in ("list", "ls"):
                return _forward_list(args.json)
            if args.forward_command == "url":
                return _forward_print_url(args.name)
            if args.forward_command == "prune":
                return _forward_prune(args.yes, args.json)
            if args.forward_command == "reconcile":
                return _forward_reconcile(args.json)
        if args.command == "preview":
            if args.preview_command == "start":
                return _preview_start(args)
            if args.preview_command == "stop":
                return _preview_stop(args.name, args.json)
            if args.preview_command in ("list", "ls"):
                return _preview_list(args.json)
            if args.preview_command == "url":
                return _preview_print_url(args.name)
            if args.preview_command == "logs":
                return _preview_logs(args.name, args.lines)
            if args.preview_command == "prune":
                return _preview_prune(args.yes, args.json)
    except (OSError, RuntimeError, ValueError) as exc:
        as_json = bool(getattr(args, "json", False))
        if as_json:
            print(json.dumps({"error": str(exc), "ok": False}, sort_keys=True))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 2 if isinstance(exc, ValueError) else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
