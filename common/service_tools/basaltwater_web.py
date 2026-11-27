#!/usr/bin/env python3
"""Manage basaltwater static HTTPS publications and loopback forwards."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import fcntl
from functools import wraps
import grp
import hashlib
import http.client
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
from lib.atomic_io import read_json_file
from lib.local_http import (
    has_corp_isolation_headers,
    http_status_is_healthy,
    http_transport_issue,
    open_direct,
    open_loopback,
)
from lib.validation import (
    validate_filesystem_path,
    validate_network_ip,
    validate_network_ip_or_cidr,
    validate_network_port,
    validate_positive_integer,
)
from lib.remote_utils import CommandTimeoutError, run as run_command
from lib.node_project import NodePackageManager, select_package_manager
from lib.t3_preview import add_t3_preview_arguments, run_t3_preview_command
from lib.tool_results import add_json_option, exception_issue, failure, from_report, success, write_result
from lib.ufw_ingress import verify_live_ufw_ingress


POLICY_FILE = "/etc/basaltwater/internal-web/policy.json"
MUTATION_LOCK_FILE = "/etc/basaltwater/internal-web/mutation.lock"
FORWARD_STATE_FILE = "/etc/basaltwater/internal-web/forwards.json"
FORWARD_NGINX_SITE = "/etc/nginx/sites-available/basaltwater-web-forwards"
FORWARD_NGINX_LINK = "/etc/nginx/sites-enabled/basaltwater-web-forwards"
PREVIEW_STATE_FILE = "/etc/basaltwater/internal-web/previews.json"
PREVIEW_RUNTIME_ROOT = "/var/lib/basaltwater/internal-web-previews"
SYSTEMD_ROOT = "/etc/systemd/system"
_FORWARD_MARKER = "# Managed by basaltwater HTTPS forwarding"
_FORWARD_RULE_PREFIX = "basaltwater HTTPS forward"
_PREVIEW_UNIT_PREFIX = "basaltwater-web-preview"
_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_USERNAME_PATTERN = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
_SAFE_PATH_PATTERN = re.compile(r"^/[A-Za-z0-9_./-]+$")
_UFW_NUMBERED_RULE_RE = re.compile(r"^\[\s*(\d+)\]\s+(.*)$")
_BODY_SIZE_PATTERN = re.compile(r"^([1-9][0-9]{0,9})([kKmMgG]?)$")
_PROFILES = ("general", "godot", "syncthing")
_MAX_FORWARD_BODY_BYTES = 1024**3
_SYSTEM_COMMAND_TIMEOUT_SECONDS = 60
_PREVIEW_INSTALL_TIMEOUT_SECONDS = 30 * 60


def _validate_lock_timeout(value: str) -> int:
    if value == "0":
        return 0
    seconds = validate_positive_integer(value, "lock timeout")
    if seconds > 300:
        raise ValueError("Lock timeout must be at most 300 seconds")
    return seconds


@contextmanager
def _gateway_lock(timeout: int = 0):
    """Hold one root-owned lock through planning, activation, and rollback."""
    timeout = _validate_lock_timeout(str(timeout))
    if os.geteuid() != 0:
        raise RuntimeError("HTTPS gateway mutations require the control service")
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
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                remaining = deadline - time.monotonic()
                if timeout == 0:
                    raise RuntimeError("Another internal-web mutation is running; retry after it finishes") from exc
                if remaining <= 0:
                    raise RuntimeError(
                        "Another internal-web mutation is still running after "
                        f"waiting {timeout}s; retry after it finishes"
                    ) from exc
                time.sleep(min(0.2, remaining))
        yield
    finally:
        os.close(descriptor)


def _serialized_mutation(function):
    @wraps(function)
    def locked(*args, **kwargs):
        timeout = getattr(args[0], "lock_timeout", 0) if args else 0
        with _gateway_lock(timeout):
            return function(*args, **kwargs)
    return locked


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage the basaltwater HTTPS publishing gateway",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    publish = commands.add_parser("publish", help="Publish static content")
    publish_commands = publish.add_subparsers(dest="publish_kind", required=True)
    godot = publish_commands.add_parser("godot", help="Export and publish a Godot game")
    godot_web_publish.add_legacy_arguments(godot)
    site_publish = publish_commands.add_parser(
        "site",
        help="Build or publish a static site or Godot web export",
    )
    static_web_publish.add_publish_arguments(site_publish)

    site = commands.add_parser("site", help="Manage published sites and Godot web exports")
    site_commands = site.add_subparsers(dest="site_command", required=True)
    site_list = site_commands.add_parser("list", aliases=["ls"], help="List sites")
    site_list.add_argument("--json", action="store_true")
    site_url = site_commands.add_parser("url", help="Print a published site URL")
    site_url.add_argument("name")
    site_doctor = site_commands.add_parser("doctor", help="Verify a published site")
    site_doctor.add_argument("name")
    site_doctor.add_argument("--json", action="store_true")
    site_verify = site_commands.add_parser("verify", help="Verify publication hashes and optional local source/build")
    site_verify.add_argument("name")
    site_verify.add_argument("--project")
    site_verify.add_argument("--output")
    site_verify.add_argument("--local-only", action="store_true", help="Skip served HTTPS bytes")
    site_verify.add_argument("--timeout", type=validate_positive_integer, default=60)
    site_verify.add_argument("--json", action="store_true")
    site_remove = site_commands.add_parser("remove", help="Remove a published site")
    site_remove.add_argument("name")
    site_remove.add_argument("--yes", action="store_true")
    site_remove.add_argument("--json", action="store_true")
    migrate_legacy = site_commands.add_parser("migrate-legacy", help="Move the current user's legacy games into sites")
    add_json_option(migrate_legacy)

    list_command = commands.add_parser("list", aliases=["ls"], help="List published games")
    list_command.add_argument("--json", action="store_true")

    url = commands.add_parser("url", help="Print a published game URL")
    url.add_argument("game")

    remove = commands.add_parser("remove", help="Remove a published game")
    remove.add_argument("game")
    remove.add_argument("--yes", action="store_true", help="Confirm permanent removal")
    add_json_option(remove)

    doctor = commands.add_parser("doctor", help="Legacy check for a Godot site or HTTPS forward")
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
    forward_add.add_argument(
        "--lock-timeout", type=_validate_lock_timeout, default=0, metavar="SECONDS",
        help="Wait up to 300 seconds for another gateway mutation (default: 0)",
    )
    forward_remove = forward_commands.add_parser("remove", help="Remove a forward")
    forward_remove.add_argument("name")
    forward_remove.add_argument("--json", action="store_true")
    forward_list = forward_commands.add_parser("list", aliases=["ls"], help="List forwards")
    forward_list.add_argument("--json", action="store_true")
    forward_url = forward_commands.add_parser("url", help="Print a forward URL")
    forward_url.add_argument("name")
    forward_doctor = forward_commands.add_parser("doctor", help="Verify a named HTTPS forward")
    forward_doctor.add_argument("name")
    forward_doctor.add_argument("--json", action="store_true")
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
    forward_reconcile.add_argument(
        "--lock-timeout", type=_validate_lock_timeout, default=0, metavar="SECONDS",
        help="Wait up to 300 seconds for another gateway mutation (default: 0)",
    )
    forward_reconcile.add_argument(
        "--policy", metavar="FILE",
        help="Apply a staged policy under the gateway lock; restore it on failure",
    )

    preview = commands.add_parser("preview", help="Manage supervised live previews")
    preview_commands = preview.add_subparsers(dest="preview_command", required=True)
    preview_resolve = preview_commands.add_parser(
        "resolve", help="Check a loopback service for T3 preview or a managed HTTPS forward",
    )
    add_t3_preview_arguments(preview_resolve)
    preview_resolve.add_argument("--route", choices=("gateway", "t3"), default="gateway")
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
    preview_start.add_argument("--script", help="Vite package script to run instead of detecting dev/preview")
    preview_start.add_argument("--port", default="auto")
    preview_start.add_argument("--listen", default="auto")
    preview_start.add_argument("--profile", choices=_PROFILES, default="general")
    preview_start.add_argument("--wait", type=float, default=30)
    preview_start.add_argument("--health", default="/")
    preview_start.add_argument("--max-body-size", help="Per-route request-body limit (at most 1g)")
    preview_start.add_argument("--replace", action="store_true")
    preview_start.add_argument("--open", action="store_true")
    preview_start.add_argument("--json", action="store_true")
    preview_restart = preview_commands.add_parser(
        "restart", help="Restart an owned preview and wait for HTTP readiness",
    )
    preview_restart.add_argument("name")
    preview_restart.add_argument("--wait", type=float, default=30)
    preview_restart.add_argument("--json", action="store_true")
    preview_stop = preview_commands.add_parser("stop", help="Stop and remove a preview")
    preview_stop.add_argument("name")
    preview_stop.add_argument("--json", action="store_true")
    preview_list = preview_commands.add_parser("list", aliases=["ls"], help="List previews")
    preview_list.add_argument("--json", action="store_true")
    preview_status = preview_commands.add_parser("status", help="Check a preview's checkout, route and reported builds")
    preview_status.add_argument("name")
    preview_status.add_argument("--json", action="store_true")
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
    return _validate_policy(value)


def _validate_policy(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or value.get("version") != 1:
        raise RuntimeError("Invalid internal-web policy")
    base_url = _validate_base_url(value.get("base_url"))
    cert_path = _validate_managed_path(value.get("certificate"), "certificate")
    key_path = _validate_managed_path(value.get("certificate_key"), "certificate key")
    raw_users = value.get("users")
    if not isinstance(raw_users, list) or not raw_users:
        raise RuntimeError("Internal-web policy has no users")
    users = list(dict.fromkeys(_validate_username(user) for user in raw_users))
    sources = _validate_forward_sources(value.get("access_sources", []))
    raw_route_sources = value.get("route_access_sources", {})
    if not isinstance(raw_route_sources, dict):
        raise RuntimeError("Invalid route access sources in internal-web policy")
    route_sources = {
        _validate_name(name, "forward policy name"): _validate_forward_sources(allowed)
        for name, allowed in raw_route_sources.items()
    }
    upstream_sources = _validate_upstream_access_sources(value.get("upstream_access_sources", {}))
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
        "access_sources": sources,
        "base_url": base_url,
        "ca_certificate": ca_certificate,
        "certificate": cert_path,
        "certificate_key": key_path,
        "forward_port_max": port_max,
        "forward_port_min": port_min,
        **({"route_access_sources": route_sources} if route_sources else {}),
        **({"upstream_access_sources": upstream_sources} if upstream_sources else {}),
        "users": users,
        "version": 1,
    }


def _validate_forward_sources(value: object) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(source, str) for source in value):
        raise RuntimeError("Invalid access sources in internal-web policy")
    try:
        for source in value:
            validate_network_ip_or_cidr(source, "internal-web access source")
    except ValueError as exc:
        raise RuntimeError("Invalid access source in internal-web policy") from exc
    return list(dict.fromkeys(value))


def _validate_upstream_access_sources(value: object) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        raise RuntimeError("Invalid upstream access sources in internal-web policy")
    sources = {}
    for port, allowed in value.items():
        if not isinstance(port, str) or re.fullmatch(r"[1-9][0-9]{3,4}", port) is None:
            raise RuntimeError("Invalid protected upstream port in internal-web policy")
        try:
            validate_network_port(int(port), "Protected upstream port", minimum=1024)
        except ValueError as exc:
            raise RuntimeError("Invalid protected upstream port in internal-web policy") from exc
        sources[port] = _validate_forward_sources(allowed)
    return sources


def _intersect_sources(first: list[str] | None, second: list[str] | None) -> list[str] | None:
    if first is None:
        return second
    if second is None or first == second:
        return first
    first_networks = [(ipaddress.ip_network(source, strict=False), source) for source in first]
    second_networks = [(ipaddress.ip_network(source, strict=False), source) for source in second]
    matches = {}
    for left, left_source in first_networks:
        for right, right_source in second_networks:
            if left.overlaps(right):
                # Aligned CIDRs overlap only when one contains the other.
                if left.prefixlen >= right.prefixlen:
                    matches.setdefault(left, left_source)
                else:
                    matches.setdefault(right, right_source)
    return list(matches.values())


def _forward_access_sources(
    routes: list[dict[str, object]],
    policy: dict[str, object],
) -> dict[int, list[str] | None]:
    """Resolve explicit restrictions by listener port. An empty list is loopback-only."""
    named = policy.get("route_access_sources", {})
    protected = policy.get("upstream_access_sources", {})
    by_listener = {int(route["listen"]): route for route in routes}
    if not named and not protected:
        return dict.fromkeys(by_listener)

    effective = {}
    for listen, route in by_listener.items():
        sources = named.get(route["name"])
        uses_default = sources is None
        # A protected port covers every accepted loopback address. The listener
        # lookup covers managed chains even though the inner hop sees loopback.
        target = int(route["target_port"])
        visited = {listen}
        while True:
            sources = _intersect_sources(sources, protected.get(str(target)))
            if sources == [] or target in visited or target not in by_listener:
                break
            visited.add(target)
            downstream = by_listener[target]
            downstream_sources = named.get(downstream["name"])
            uses_default = uses_default or downstream_sources is None
            sources = _intersect_sources(sources, downstream_sources)
            target = int(downstream["target_port"])
        if sources is not None and uses_default:
            # Retain the ordinary firewall default for each hop with no named policy.
            sources = _intersect_sources(sources, policy["access_sources"] or None)
        effective[listen] = sources
    return effective


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


def _forward_route_url(
    policy: dict[str, object], listen: int, sources: list[str] | None,
) -> str:
    base_url = str(policy["base_url"])
    if sources == [] and policy.get("ca_certificate"):
        base_url = "https://localhost"
    return _forward_url(base_url, listen)


def render_forward_nginx(
    routes: list[dict[str, object]],
    policy: dict[str, object],
) -> str:
    """Render isolated TLS listeners for validated loopback forwards."""

    blocks = [_FORWARD_MARKER]
    sources_by_listener = _forward_access_sources(routes, policy)
    for route in routes:
        target_host = str(route["target_host"])
        upstream_host = f"[{target_host}]" if ":" in target_host else target_host
        max_body_size = route.get("max_body_size")
        access_sources = sources_by_listener[int(route["listen"])]
        access_policy = ""
        if access_sources is not None:
            # A server-specific trust list overrides the global real-IP trust.
            # These TCP listeners cannot receive requests from a Unix socket.
            access_policy = "\n    set_real_ip_from unix:;\n    allow 127.0.0.0/8;\n    allow ::1;"
            for source in access_sources:
                access_policy += f"\n    allow {ipaddress.ip_network(source, strict=False)};"
            access_policy += "\n    deny all;\n    satisfy all;"
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
    ssl_protocols TLSv1.2 TLSv1.3;{body_size_directive}{access_policy}

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
    sources_by_listener = _forward_access_sources(routes, policy)
    for route in routes:
        port = int(route["listen"])
        name = str(route["name"])
        route_sources = sources_by_listener[port]
        if route_sources == []:
            continue
        sources = policy["access_sources"] if route_sources is None else route_sources
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
    restricted = {}
    for listen, sources in _forward_access_sources(routes, policy).items():
        if sources is None:
            sources = policy["access_sources"] or None
        if sources is not None:
            restricted["tcp", listen] = sources
    verify_live_ufw_ingress(restricted, run_command)


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
    *,
    rollback_policy: dict[str, object] | None = None,
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
    except Exception as activation_error:
        _restore_file(FORWARD_NGINX_SITE, previous_site, 0o644)
        _restore_file(FORWARD_STATE_FILE, previous_state, 0o644)
        if link_created:
            try:
                os.unlink(FORWARD_NGINX_LINK)
            except FileNotFoundError:
                pass
        try:
            restore_policy = policy if rollback_policy is None else rollback_policy
            old_routes = _load_forwards(restore_policy) if previous_state is not None else []
            _reconcile_firewall(old_routes, restore_policy)
            _run_checked(["systemctl", "reload", "nginx"], "Could not restore Nginx")
        except Exception as rollback_error:
            raise RuntimeError(
                f"{activation_error}; HTTPS forwarding rollback failed: {rollback_error}"
            ) from activation_error
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


def _site_account() -> pwd.struct_passwd:
    account = pwd.getpwuid(os.getuid())
    if account.pw_uid == 0:
        raise RuntimeError("Run site commands as the configured non-root user")
    _validate_username(account.pw_name)
    return account


def _game_records(account: pwd.struct_passwd) -> list[dict[str, object]]:
    return [
        {
            "debug": record.get("debug"),
            "game": record["site"],
            "preset": record.get("preset"),
            "published_at": record.get("published_at"),
            "title": record.get("title") or record["site"],
            "url": record["url"],
        }
        for record in static_web_publish.list_sites(account) if record["profile"] == "godot"
    ]


def _print_games(as_json: bool) -> int:
    records = _game_records(_site_account())
    if as_json:
        return write_result(success(games=records))
    elif not records:
        print("No games published")
    else:
        for record in records:
            print(f"{record['game']}\t{record['url'] or '-'}")
    return 0


def _remove_game(game: str, confirmed: bool, as_json: bool) -> int:
    static_web_publish.remove_site(_site_account(), game, confirmed, profile="godot")
    if as_json:
        return write_result(success(game=game, removed=True))
    print(f"Removed published game {game}")
    return 0


def _print_sites(as_json: bool) -> int:
    account = _site_account()
    records = static_web_publish.list_sites(account)
    if as_json:
        return write_result(success(sites=records))
    elif not records:
        print("No sites published")
    else:
        for record in records:
            print(f"{record['site']}\t{record.get('url') or '-'}")
    return 0


def _site_path(account: pwd.struct_passwd, name: str) -> str:
    return os.path.join(static_web_publish.SITES_ROOT, account.pw_name, name)


def _site_exists(path: str) -> bool:
    return os.path.isdir(path) and not os.path.islink(path)


def _site_url(account: pwd.struct_passwd, name: str) -> str:
    url = static_web_publish.published_url(account.pw_name, name)
    if not url:
        raise RuntimeError("Internal-web base URL is unavailable")
    return url


def _print_site_url(name: str, *, profile: str | None = None) -> int:
    _validate_name(name, "site name")
    account = _site_account()
    path = _site_path(account, name)
    if not _site_exists(path) or (profile is not None and static_web_publish.site_profile(path) != profile):
        raise RuntimeError(f"Published site does not exist: {name}")
    print(_site_url(account, name))
    return 0


def _remove_site(name: str, confirmed: bool, as_json: bool) -> int:
    account = _site_account()
    static_web_publish.remove_site(account, name, confirmed)
    if as_json:
        return write_result(success(name=name, removed=True))
    else:
        print(f"Removed published site {name}")
    return 0


def _https_headers(url: str) -> tuple[int, dict[str, str]]:
    request = urllib.request.Request(
        url,
        headers={"Range": "bytes=0-0", "User-Agent": "basaltwater-web-doctor/1"},
    )
    try:
        with open_direct(request, timeout=15) as response:
            return response.status, {key.lower(): value for key, value in response.headers.items()}
    except urllib.error.HTTPError as exc:
        with exc:
            return exc.code, {key.lower(): value for key, value in exc.headers.items()}
    except (OSError, http.client.HTTPException) as exc:
        raise RuntimeError(f"HTTPS request failed for {url}: {exc}") from exc


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
        headers={"User-Agent": "basaltwater-web-readiness/1"},
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
    raise TimeoutError(
        f"Upstream did not become ready at http://{host}:{port}{health_path} "
        f"within {seconds:g} seconds"
    )


def _open_url(url: str, username: str | None = None) -> None:
    try:
        if username and os.geteuid() == 0:
            opener = shutil.which("xdg-open")
            if opener:
                subprocess.Popen(
                    ["runuser", "-u", username, "--", opener, url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                return
        elif webbrowser.open(url):
            return
    except (OSError, webbrowser.Error):
        pass
    print(f"Local browser unavailable. Open {url} in your browser.", file=sys.stderr)


def _site_doctor(name: str, as_json: bool) -> int:
    _validate_name(name, "site name")
    account = _site_account()
    path = _site_path(account, name)
    if not _site_exists(path):
        raise RuntimeError(f"Published site does not exist: {name}")
    return _diagnose_site(account, name, path, as_json)


def _diagnose_site(account: pwd.struct_passwd, name: str, path: str, as_json: bool) -> int:
    url = _site_url(account, name)
    profile = static_web_publish.site_profile(path)
    wasm_url = None
    if profile == "godot":
        wasm_path = "index.wasm"
        if not os.path.isfile(os.path.join(path, wasm_path)):
            # Some older snapshots used a different name. Nginx blocks hidden paths.
            for current_dir, directories, files in os.walk(path):
                directories[:] = sorted(name for name in directories if not name.startswith("."))
                wasm_name = next((name for name in sorted(files)
                                  if not name.startswith(".") and name.endswith(".wasm")), None)
                if wasm_name:
                    wasm_path = os.path.relpath(os.path.join(current_dir, wasm_name), path)
                    break
        wasm_url = urllib.parse.urljoin(url, urllib.parse.quote(wasm_path))
    return _diagnose_endpoint(name, as_json, url, profile, wasm_url)


def _doctor(name: str, as_json: bool, *, forward_only: bool = False) -> int:
    _validate_name(name)
    account = _site_account()
    if not forward_only:
        path = _site_path(account, name)
        if _site_exists(path) and static_web_publish.site_profile(path) == "godot":
            return _diagnose_site(account, name, path, as_json)
    policy = _load_policy()
    routes = _load_forwards(policy)
    route = next((item for item in routes if item["name"] == name), None)
    if route is None:
        if forward_only:
            raise RuntimeError(f"HTTPS forward does not exist: {name}")
        raise RuntimeError(f"No published Godot site or HTTPS forward named {name}")
    if route["owner"] != account.pw_name:
        raise RuntimeError(f"HTTPS forward is owned by another user: {name}")
    listen = int(route["listen"])
    url = _forward_route_url(policy, listen, _forward_access_sources(routes, policy)[listen])
    return _diagnose_endpoint(name, as_json, url, str(route["profile"]))


def _diagnose_endpoint(
    name: str, as_json: bool, url: str, profile: str, wasm_url: str | None = None,
) -> int:
    status = None
    headers: dict[str, str] = {}
    wasm_headers: dict[str, str] = {}
    errors: list[str] = []
    transport_failure: dict[str, str] = {}
    component = "endpoint"
    try:
        status, headers = _https_headers(url)
        if not http_status_is_healthy(status, headers):
            errors.append(f"HTTPS endpoint returned {status}")
        if profile == "godot":
            if headers.get("cross-origin-opener-policy") != "same-origin":
                errors.append("missing Cross-Origin-Opener-Policy: same-origin")
            if headers.get("cross-origin-embedder-policy") != "require-corp":
                errors.append("missing Cross-Origin-Embedder-Policy: require-corp")
        if wasm_url:
            component = "wasm"
            wasm_status, wasm_headers = _https_headers(wasm_url)
            if not 200 <= wasm_status < 300:
                errors.append(f"WASM endpoint returned {wasm_status}")
            content_type = wasm_headers.get("content-type", "").split(";", 1)[0]
            if content_type != "application/wasm":
                errors.append(f"WASM content type is {content_type or 'missing'}")
    except RuntimeError as exc:
        errors.append(str(exc))
        transport_failure = {"component": component, "issue": http_transport_issue(exc), "error": str(exc)}
    result = {
        "errors": errors,
        "headers": headers,
        "name": name,
        "network_origin": "vm",
        "client_device_checked": False,
        "ok": not errors,
        "status": status,
        "url": url,
        "wasm_headers": wasm_headers,
        **transport_failure,
    }
    if as_json:
        return write_result(from_report(result, ok=not errors, issue="endpoint_unhealthy", error="; ".join(errors)))
    elif errors:
        print(f"{name}: VM HTTPS check failed ({'; '.join(errors)})")
    else:
        print(f"{name}: VM HTTPS check returned {status} at {url}. Client device untested.")
    return 0 if not errors else 1


def _print_ca(as_json: bool) -> int:
    policy = _load_policy()
    result = _certificate_trust(policy)
    if as_json:
        return write_result(success(**result))
    elif result["status"] == "public":
        print("The internal-web endpoint uses a publicly trusted certificate")
    elif result["status"] == "local_ca":
        print(
            f"{result['ca_certificate']}\n{result['url']}\n"
            f"SHA-256 {result['sha256']}"
        )
    else:
        print(f"Certificate trust could not be verified: {result['reason']}")
    return 0


def _certificate_trust(policy: dict[str, object]) -> dict[str, object]:
    """Classify the live endpoint using the CA selected by the policy."""

    certificate = str(policy["certificate"])
    ca_path = policy.get("ca_certificate")
    if isinstance(ca_path, str) and not os.path.isfile(ca_path):
        return {"status": "unknown", "reason": "configured CA certificate is missing"}
    if ca_path is None and not certificate.startswith("/etc/letsencrypt/live/"):
        return {
            "status": "unknown",
            "reason": "no managed public certificate or local CA is configured",
        }

    try:
        parsed = urllib.parse.urlsplit(str(policy["base_url"]))
        hostname = parsed.hostname
        if not hostname:
            raise ValueError("invalid endpoint hostname")
        _verify_live_tls(hostname, parsed.port or 443, ca_path if isinstance(ca_path, str) else None)
        if isinstance(ca_path, str):
            digest = hashlib.sha256()
            with open(ca_path, "rb") as file_obj:
                while chunk := file_obj.read(1024 * 1024):
                    digest.update(chunk)
            return {
                "status": "local_ca",
                "ca_certificate": ca_path,
                "sha256": digest.hexdigest(),
                "url": f"{str(policy['base_url']).rstrip('/')}/basaltwater-ca.crt",
            }
        local_ca = "/srv/basaltwater/web/basaltwater-ca.crt"
        if os.path.isfile(local_ca):
            try:
                _verify_live_tls(hostname, parsed.port or 443, local_ca)
            except (OSError, ValueError, ssl.SSLError):
                pass
            else:
                return {"status": "unknown", "reason": "live endpoint uses the VM-local CA"}
        return {"status": "public", "publicly_trusted": True}
    except (OSError, ValueError, ssl.SSLError) as exc:
        return {"status": "unknown", "reason": f"live TLS verification failed ({type(exc).__name__})"}


def _verify_live_tls(hostname: str, port: int, ca_path: str | None) -> None:
    """Require a valid chain and matching hostname at the configured endpoint."""

    context = ssl.create_default_context(cafile=ca_path)
    with socket.create_connection((hostname, port), timeout=3) as connection:
        with context.wrap_socket(connection, server_hostname=hostname):
            pass


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
    url = _forward_route_url(policy, listen, _forward_access_sources(updated, policy)[listen])
    result = {**route, "url": url}
    if args.json:
        write_result(success(**result))
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
        return write_result(success(name=name, removed=True))
    else:
        print(f"Removed HTTPS forward {name}")
    return 0


def _forward_list(as_json: bool) -> int:
    policy = _load_policy()
    account = pwd.getpwuid(os.getuid())
    username = account.pw_name
    forwards = _load_forwards(policy)
    sources_by_listener = _forward_access_sources(forwards, policy)
    try:
        previews = {str(item["name"]): item for item in _load_previews(policy)}
    except (OSError, RuntimeError, ValueError):
        # A bad preview record must not hide the independently managed forwards.
        previews = {}
    routes = [
        {
            **route,
            "ready": _tcp_ready(str(route["target_host"]), int(route["target_port"])),
            "readiness_origin": "vm",
            "readiness_check": "upstream_tcp",
            "client_device_checked": False,
            "host_only": sources_by_listener[int(route["listen"])] == [],
            "url": _forward_route_url(policy, int(route["listen"]), sources_by_listener[int(route["listen"])]),
        }
        for route in forwards
        if os.geteuid() == 0 or route["owner"] == username
    ]
    for route in routes:
        preview = previews.get(str(route["name"]))
        if preview and _preview_matches_route(preview, route):
            route.update(project_name=os.path.basename(str(preview["project"])), unit=preview["unit"])
    if as_json:
        return write_result(success(forwards=routes))
    elif not routes:
        print("No HTTPS forwards configured")
    else:
        for route in routes:
            print(
                f"{route['name']}\t{route['url']}\thttp://"
                f"{route['target_host']}:{route['target_port']}\t{route['profile']}\t"
                f"{'ready' if route['ready'] else 'down'} (VM upstream TCP check)"
            )
    return 0


def _forward_print_url(name: str) -> int:
    _validate_name(name, "forward name")
    policy = _load_policy()
    routes = _load_forwards(policy)
    route = next((item for item in routes if item["name"] == name), None)
    if route is None:
        raise RuntimeError(f"HTTPS forward does not exist: {name}")
    account = pwd.getpwuid(os.getuid())
    if os.geteuid() != 0 and route["owner"] != account.pw_name:
        raise RuntimeError(f"HTTPS forward is owned by another user: {name}")
    listen = int(route["listen"])
    print(_forward_route_url(policy, listen, _forward_access_sources(routes, policy)[listen]))
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
        return write_result(success(**result))
    elif removed:
        print(f"Removed {len(removed)} dead forward(s): {', '.join(removed)}")
    else:
        print("No dead forwards found")
    return 0


@_serialized_mutation
def _forward_reconcile(args: argparse.Namespace) -> int:
    previous_content = None
    previous_policy = None
    content = None
    changed = False
    if args.policy is not None:
        validate_filesystem_path(args.policy, must_exist=True)
        policy = _validate_policy(_read_json(args.policy, "staged internal-web policy"))
        if os.path.lexists(POLICY_FILE):
            previous_policy = _load_policy()
            with open(POLICY_FILE, encoding="utf-8") as file_obj:
                previous_content = file_obj.read()
            for key in ("route_access_sources", "upstream_access_sources"):
                merged = previous_policy.get(key, {}) | policy.get(key, {})
                if merged:
                    policy[key] = merged
        content = json.dumps(policy, indent=2, sort_keys=True) + "\n"
        changed = content != previous_content
        # Reject a policy that would orphan existing previews before writing it.
        _load_previews(policy)
    else:
        policy = _load_policy()
    routes = _load_forwards(policy)
    try:
        if content is not None:
            if changed:
                _write_text_atomic(POLICY_FILE, content, 0o644)
            else:
                os.chmod(POLICY_FILE, 0o644)
        if previous_policy is not None:
            _apply_forwards(routes, policy, rollback_policy=previous_policy)
        else:
            _apply_forwards(routes, policy)
    except Exception:
        if changed:
            _restore_file(POLICY_FILE, previous_content, 0o644)
        raise
    if args.json:
        return write_result(success(forwards=len(routes), policy_changed=changed, reconciled=True))
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


def _project_node_command(project: str, command: list[str]) -> list[str]:
    """Select Node only after runuser/systemd has switched to the project owner."""
    return [
        sys.executable, "-I", os.path.join(SOURCE_ROOT, "basaltwater.py"),
        "node", "exec", "--project", project, "--", *command,
    ]


def _automatic_preview_workflow(
    project: str, script: str | None,
) -> tuple[NodePackageManager, dict[str, object], str]:
    """Validate project metadata independently of the eventual preview port."""
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
    script = script if script is not None else next(
        (name for name in ("dev", "preview") if isinstance(scripts.get(name), str)),
        None,
    )
    if script is None:
        raise ValueError("No dev or preview script found; provide a command after --")
    manager = select_package_manager(project, package)
    manager.script_command(package, script)
    return manager, package, script


def _automatic_preview_command(project: str, port: int, *, script: str | None = None) -> list[str]:
    manager, package, script = _automatic_preview_workflow(project, script)
    command = manager.script_command(
        package, script, corepack=True,
        arguments=("--host", "127.0.0.1", "--port", str(port), "--strictPort"),
    )
    return _project_node_command(project, command)


def _install_preview_dependencies(project: str, account: pwd.struct_passwd) -> None:
    if os.path.isdir(os.path.join(project, "node_modules")):
        return
    manager = select_package_manager(project, _read_preview_package(project))
    command = manager.install_command(corepack=True)
    command = _resolve_preview_executable(_project_node_command(project, command), account)
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


def _preview_command(raw: list[str], project: str, port: int, *, script: str | None = None) -> list[str]:
    command = raw[1:] if raw and raw[0] == "--" else list(raw)
    if not command:
        return _automatic_preview_command(project, port, script=script)
    if script is not None:
        raise ValueError("--script cannot be combined with an explicit preview command")
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
    return '"' + value.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"') + '"'


def _systemd_working_directory(project: str) -> str:
    """WorkingDirectory takes a literal path, not a quoted argv item."""
    validate_filesystem_path(project)
    if not os.path.isabs(project):
        raise ValueError("Preview working directory must be absolute")
    # A final slash preserves trailing spaces and prevents line continuation.
    return project.replace("%", "%%").rstrip("/") + "/"


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
Description=basaltwater live preview: {name} ({account.pw_name})
After=network.target

[Service]
Type=simple
User={account.pw_name}
Group={group_name}
WorkingDirectory={_systemd_working_directory(project)}
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


def _preview_matches_route(record: dict[str, object], route: dict[str, object]) -> bool:
    return all(route[key] == record[key] for key in ("owner", "target_host", "target_port", "listen", "profile"))


def _require_preview_route(record: dict[str, object], routes: list[dict[str, object]]) -> dict[str, object]:
    route = next((item for item in routes if item["name"] == record["name"]), None)
    if route is None:
        raise RuntimeError("No HTTPS forward for this preview")
    if not _preview_matches_route(record, route):
        raise RuntimeError("HTTPS forward no longer matches its managed preview")
    return route


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
        raise RuntimeError("Live-preview mutations require the control service")
    name = _validate_name(args.name, "preview name")
    policy = _load_policy()
    owner = _requesting_username(policy)
    account = pwd.getpwnam(owner)
    project = _preview_project(args.project, account)
    if not 0 < args.wait <= 300:
        raise ValueError("--wait must be greater than 0 and at most 300 seconds")
    health = _validate_health_path(args.health)
    body_size = _validate_body_size(args.max_body_size, "--max-body-size")
    if args.script is not None and args.preview_argv:
        raise ValueError("--script cannot be combined with an explicit preview command")
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
        if not args.preview_argv:
            _automatic_preview_workflow(project, args.script)
        _stop_preview_record(existing, policy, routes, previews)
    conflicting_route = next((route for route in routes if route["name"] == name), None)
    if conflicting_route is not None:
        raise RuntimeError(f"HTTPS forward already uses the preview name: {name}")

    port = _select_target_port(args.port, routes, previews)
    command = _resolve_preview_executable(
        _preview_command(args.preview_argv, project, port, script=args.script),
        account,
        project,
    )
    listen = _select_listen_port(args.listen, routes, policy, None)
    if not args.preview_argv:
        _install_preview_dependencies(project, account)
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
        if body_size is not None:
            route["max_body_size"] = body_size
        updated_routes = [*routes, route]
        _apply_forwards(updated_routes, policy)
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
    url = _forward_route_url(policy, listen, _forward_access_sources(updated_routes, policy)[listen])
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
        write_result(success(**result))
    else:
        print(f"{name}: {url} (unit {unit}, loopback {port})")
    if args.open:
        _open_url(url, owner)
    return 0


@_serialized_mutation
def _preview_restart(args: argparse.Namespace) -> int:
    if os.geteuid() != 0:
        raise RuntimeError("Live-preview mutations require the control service")
    if not 0 < args.wait <= 300:
        raise ValueError("--wait must be greater than 0 and at most 300 seconds")
    policy = _load_policy()
    owner = _requesting_username(policy)
    record = _preview_for_user(args.name, _load_previews(policy), owner)
    _preview_project(str(record["project"]), pwd.getpwnam(owner))
    routes = _load_forwards(policy)
    route = _require_preview_route(record, routes)
    unit = str(record["unit"])
    _run_checked(["systemctl", "restart", unit], "Restarting live preview")
    _wait_for_upstream(
        str(record["target_host"]), int(record["target_port"]),
        str(record["health"]), args.wait,
    )
    if not _preview_active(unit):
        raise RuntimeError(f"Live-preview service is not active; inspect preview logs {args.name}")
    listen = int(route["listen"])
    url = _forward_route_url(policy, listen, _forward_access_sources(routes, policy)[listen])
    if args.json:
        return write_result(success(name=args.name, unit=unit, url=url))
    else:
        print(f"Restarted {args.name}: {url}")
    return 0


@_serialized_mutation
def _preview_stop(name: str, as_json: bool) -> int:
    if os.geteuid() != 0:
        raise RuntimeError("Live-preview mutations require the control service")
    policy = _load_policy()
    owner = _requesting_username(policy)
    routes = _load_forwards(policy)
    previews = _load_previews(policy)
    record = _preview_for_user(name, previews, owner)
    _stop_preview_record(record, policy, routes, previews)
    if as_json:
        return write_result(success(name=name, removed=True))
    else:
        print(f"Stopped and removed live preview {name}")
    return 0


def _preview_list(as_json: bool) -> int:
    policy = _load_policy()
    account = pwd.getpwuid(os.getuid())
    sources = _forward_access_sources(_load_forwards(policy), policy)
    previews = [
        {
            **preview,
            "active": _preview_active(str(preview["unit"])),
            "url": _forward_route_url(policy, int(preview["listen"]), sources.get(int(preview["listen"]))),
        }
        for preview in _load_previews(policy)
        if os.geteuid() == 0 or preview["owner"] == account.pw_name
    ]
    if as_json:
        return write_result(success(previews=previews))
    elif not previews:
        print("No live previews configured")
    else:
        for preview in previews:
            print(
                f"{preview['name']}\t{preview['url']}\t{preview['project']}\t"
                f"{'active' if preview['active'] else 'stopped'}"
            )
    return 0


def _build_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|\+00:00)", value,
    ):
        raise ValueError("Preview build timestamps must be ISO 8601 UTC")
    return datetime.fromisoformat(value)


def _preview_build_status(account: pwd.struct_passwd, project: str, name: str) -> dict[str, object]:
    directory = os.path.join(account.pw_dir, ".local", "state", "basaltwater", "preview-builds")
    path = os.path.join(directory, f"{name}.json")
    result = {"status": "not-reported", "report": path, "last_success_at": None, "latest_error": None}
    try:
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            return result
        parent = os.lstat(directory)
        if (
            os.path.realpath(directory) != directory or not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != account.pw_uid or parent.st_mode & 0o077
            or not stat.S_ISREG(info.st_mode) or info.st_uid != account.pw_uid or info.st_mode & 0o077
        ):
            raise ValueError("Build report and its directory must be private and owned by the preview account")
        report = read_json_file(path, max_bytes=8192)
        if (not isinstance(report, dict) or set(report) != {"version", "project", "last_success_at", "latest_error"}
                or type(report["version"]) is not int or report["version"] != 1):
            raise ValueError("Invalid version 1 preview build report")
        if report["project"] != project:
            raise ValueError("Build report belongs to a different checkout")
        success, failure = report["last_success_at"], report["latest_error"]
        success_at = _build_timestamp(success) if success is not None else None
        failure_at = None
        if failure is not None:
            if not isinstance(failure, dict) or set(failure) != {"at", "message"}:
                raise ValueError("Build error must contain at and message")
            failure_at = _build_timestamp(failure["at"])
            message = failure["message"]
            if not isinstance(message, str) or not 1 <= len(message) <= 512 or not message.isprintable() or not message.strip():
                raise ValueError("Build error must be a single printable line of up to 512 characters")
        if failure_at is not None and (success_at is None or failure_at >= success_at):
            result["status"] = "failed"
        elif success_at is not None:
            result["status"] = "succeeded"
        result.update(last_success_at=success, latest_error=failure)
    except (OSError, ValueError) as exc:
        result.update(status="unavailable", error=str(exc))
    return result


def _preview_status(name: str, as_json: bool) -> int:
    policy = _load_policy()
    account = pwd.getpwuid(os.getuid())
    record = _preview_for_user(name, _load_previews(policy), account.pw_name)
    active = _preview_active(str(record["unit"]))
    project = str(record["project"])
    checkout_error = None
    try:
        if _preview_project(project, account) != project:
            raise ValueError("Configured checkout path now resolves to another directory")
    except (OSError, ValueError) as exc:
        checkout_error = str(exc)
    build = _preview_build_status(account, project, name)
    url = None
    https: dict[str, object] = {"ok": False, "status": None}
    try:
        routes = _load_forwards(policy)
        route = _require_preview_route(record, routes)
        listen = int(route["listen"])
        url = _forward_route_url(policy, listen, _forward_access_sources(routes, policy)[listen])
        status, headers = _https_headers(url.rstrip("/") + str(record["health"]))
        https["status"] = status
        if not http_status_is_healthy(status, headers):
            raise RuntimeError(f"HTTPS endpoint returned {status}")
        if record["profile"] == "godot" and not has_corp_isolation_headers(headers):
            raise RuntimeError("HTTPS endpoint is missing Godot cross-origin isolation headers")
        https["ok"] = True
    except (OSError, RuntimeError, ValueError) as exc:
        https["error"] = str(exc)
    ok = bool(active and checkout_error is None and https["ok"] and build["status"] not in ("failed", "unavailable"))
    result = {**record, "ok": ok, "active": active, "url": url, "https": https, "build": build,
              "checkout_error": checkout_error, "network_origin": "vm", "client_device_checked": False}
    if as_json:
        return write_result(from_report(result, ok=ok, issue="preview_unhealthy",
                                        error="Live preview is unhealthy. Inspect its service, checkout and build details."))
    else:
        print(f"{name}\t{project}")
        if checkout_error:
            print(f"Checkout unavailable ({checkout_error})")
        print(f"Service {'active' if active else 'inactive'}")
        if https["ok"]:
            print(f"VM HTTPS check returned {https['status']} at {url}")
        else:
            print(f"VM HTTPS check failed: {https['error']}")
        print("Client device untested")
        print(f"Reported build status {build['status']}")
        if build["last_success_at"]:
            print(f"Last successful rebuild at {build['last_success_at']}")
        if latest_error := build["latest_error"]:
            print(f"Latest build error at {latest_error['at']}")
            print(latest_error["message"])
        if build["status"] == "unavailable":
            print(build["error"])
    return 0 if ok else 1


def _preview_upstream_headers(url: str) -> tuple[int, dict[str, str]]:
    request = urllib.request.Request(url, headers={"User-Agent": "basaltwater-web-preview/1"})
    try:
        with open_loopback(request, timeout=3) as response:
            return response.status, {key.lower(): value for key, value in response.headers.items()}
    except urllib.error.HTTPError as exc:
        with exc:
            return exc.code, {key.lower(): value for key, value in exc.headers.items()}


def _preview_resolve(args: argparse.Namespace) -> int:
    """Check native T3 navigation or resolve an owned HTTPS route without mutation."""
    if args.route == "t3":
        return run_t3_preview_command(args)
    if args.protocol is not None or args.path is not None:
        raise ValueError("--protocol and --path require --route t3")
    host = "127.0.0.1" if args.host is None else validate_network_ip(args.host, "preview host")
    url_host = f"[{host}]" if ":" in host else host
    host, port = _parse_upstream(f"{url_host}:{args.port}")
    upstream_url = f"http://{url_host}:{port}/"
    result: dict[str, object] = {
        "ok": False,
        "upstream_url": upstream_url,
        "url": None,
        "navigation": None,
        "network_origin": "command_host",
        "client_device_checked": False,
        "component": "gateway",
        "issue": None,
        "remediation": None,
        "remediation_argv": None,
    }
    try:
        policy = _load_policy()
        owner = _requesting_username(policy)
        routes = _load_forwards(policy)
    except (OSError, RuntimeError, ValueError) as exc:
        result.update(
            issue="gateway_unavailable", error=str(exc),
            remediation="Check gateway policy and ownership; rerun the saved gateway-capable VM setup.",
        )
    else:
        route = next((item for item in routes if (
            item["owner"] == owner and item["target_host"] == host
            and item["target_port"] == port and item["profile"] == args.profile
        )), None)
        result["component"] = "upstream"
        result["remediation"] = "Start or repair the loopback HTTP server, then retry resolution."
        try:
            status, headers = _preview_upstream_headers(upstream_url)
            result["upstream_status"] = status
            if not http_status_is_healthy(status, headers):
                result.update(issue="http_error", error=f"Loopback HTTP endpoint returned {status}")
            elif route is None:
                name_base = f"preview-{owner}-{'v6-' if ':' in host else ''}{port}"
                name = name_base
                suffix = 2
                names = {item["name"] for item in routes}
                while name in names:
                    name = f"{name_base}-{suffix}"
                    suffix += 1
                command = [
                    "basaltwater-web", "forward", "add", name,
                    "--listen", "auto", "--to", f"{url_host}:{port}",
                    "--profile", args.profile, "--wait", "30", "--json",
                ]
                result.update(
                    component="gateway", issue="forward_missing", remediation_argv=command,
                    error="No owned HTTPS forward matches the loopback service and profile.",
                    remediation="Run the supplied arguments within the requested preview scope, then retry resolution.",
                )
            else:
                listen = int(route["listen"])
                url = _forward_route_url(policy, listen, _forward_access_sources(routes, policy)[listen])
                result.update(
                    component="gateway", url=url, name=route["name"],
                    remediation=(
                        "Check the HTTPS listener, VM routing and saved access-source policy. "
                        f"Run basaltwater-web forward doctor {route['name']}."
                    ),
                )
                status, headers = _https_headers(url)
                result["gateway_status"] = status
                if not http_status_is_healthy(status, headers):
                    result.update(issue="http_error", error=f"Gateway HTTPS endpoint returned {status}")
                elif args.profile == "godot" and not has_corp_isolation_headers(headers):
                    result.update(issue="isolation_headers_missing", error="Gateway is missing Godot cross-origin isolation headers")
                else:
                    result.update(
                        ok=True, navigation={"url": url}, remediation=None,
                    )
        except (OSError, RuntimeError) as exc:
            issue = http_transport_issue(exc)
            result.update(issue=issue, error=str(exc))
            if issue == "certificate_untrusted":
                result["remediation"] = "Inspect VM certificate trust with basaltwater-web ca; preserve TLS verification."
    return write_result(from_report(result, ok=bool(result["ok"]), issue="preview_unavailable",
                                    error="Could not resolve a reachable HTTPS preview."))


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
    listen = int(record["listen"])
    sources = _forward_access_sources(_load_forwards(policy), policy)
    print(_forward_route_url(policy, listen, sources.get(listen)))
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
        raise RuntimeError("Live-preview mutations require the control service")
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
        return write_result(success(removed=removed))
    elif removed:
        print(f"Removed {len(removed)} stopped preview(s): {', '.join(removed)}")
    else:
        print("No stopped previews found")
    return 0


def _publish_site(args: argparse.Namespace) -> int:
    result = static_web_publish.publish(args)
    static_web_publish.print_publish_result(result, as_json=args.json)
    if args.open:
        _open_url(str(result["url"]))
    return 0 if result["ok"] else 1


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
        if os.geteuid() != 0:
            from common.service_tools import basaltwater_web_control

            if basaltwater_web_control.is_delegated_mutation(raw_args):
                delegated_args = list(raw_args)
                if args.command == "preview" and args.preview_command == "start":
                    project = args.project
                    if not project.startswith("~"):
                        project = os.path.abspath(project)
                    delegated_args.extend(["--project", project])
                    if preview_argv:
                        delegated_args.extend(["--", *preview_argv])
                return basaltwater_web_control.request(delegated_args, as_json=args.json)
        if args.command == "publish":
            if args.publish_kind == "godot":
                return godot_web_publish.publish_legacy(args)
            if args.publish_kind == "site":
                return _publish_site(args)
        if args.command == "site":
            if args.site_command in ("list", "ls"):
                return _print_sites(args.json)
            if args.site_command == "url":
                return _print_site_url(args.name)
            if args.site_command == "doctor":
                return _site_doctor(args.name, args.json)
            if args.site_command == "verify":
                result = static_web_publish.verify_site(args)
                if args.json:
                    return write_result(from_report(result, ok=result["ok"], issue="verification_failed",
                                                    error="Could not verify the published site. Inspect the verification details."))
                else:
                    print(f"{args.name}: {'verified' if result['ok'] else result['issue']}")
                    if result.get("remediation"):
                        print(result["remediation"])
                return 0 if result["ok"] else 1
            if args.site_command == "remove":
                return _remove_site(args.name, args.yes, args.json)
            if args.site_command == "migrate-legacy":
                urls = static_web_publish.migrate_legacy_sites()
                if args.json:
                    return write_result(success(urls=urls))
                for url in urls:
                    print(f"Migrated Godot site to {url}")
                return 0
        if args.command in ("list", "ls"):
            return _print_games(args.json)
        if args.command == "url":
            return _print_site_url(args.game, profile="godot")
        if args.command == "remove":
            return _remove_game(args.game, args.yes, args.json)
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
            if args.forward_command == "doctor":
                return _doctor(args.name, args.json, forward_only=True)
            if args.forward_command == "prune":
                return _forward_prune(args.yes, args.json)
            if args.forward_command == "reconcile":
                return _forward_reconcile(args)
        if args.command == "preview":
            if args.preview_command == "resolve":
                return _preview_resolve(args)
            if args.preview_command == "start":
                return _preview_start(args)
            if args.preview_command == "restart":
                return _preview_restart(args)
            if args.preview_command == "stop":
                return _preview_stop(args.name, args.json)
            if args.preview_command in ("list", "ls"):
                return _preview_list(args.json)
            if args.preview_command == "status":
                return _preview_status(args.name, args.json)
            if args.preview_command == "url":
                return _preview_print_url(args.name)
            if args.preview_command == "logs":
                return _preview_logs(args.name, args.lines)
            if args.preview_command == "prune":
                return _preview_prune(args.yes, args.json)
    except (OSError, RuntimeError, ValueError) as exc:
        as_json = bool(getattr(args, "json", False))
        if as_json:
            operation = next((value for value in (getattr(args, "preview_command", None), getattr(args, "forward_command", None),
                                                   getattr(args, "site_command", None), args.command) if value), args.command)
            issue = exception_issue(exc)
            fields = {}
            if operation == "resolve":
                fields["navigation"] = None
            if operation in {"add", "migrate-legacy", "prune", "publish", "reconcile", "remove", "restart", "start", "stop"}:
                fields.update(stage=operation.replace("-", "_"), effect="none" if issue == "invalid_input" else "unknown")
            return write_result(failure(issue, str(exc), **fields))
        print(f"Error: {exc}", file=sys.stderr)
        if isinstance(exc, TimeoutError):
            return 124
        return 2 if isinstance(exc, ValueError) else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
