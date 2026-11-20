"""Probe a local development server and describe its T3 navigation target."""

from __future__ import annotations

import argparse
from contextlib import ExitStack, closing
import http.client
import json
import socket
import ssl
import sys
from urllib.parse import quote

from lib.local_http import has_corp_isolation_headers, http_status_is_healthy, http_transport_issue
from lib.validation import validate_network_ip, validate_network_port, validate_no_control_characters, validate_positive_integer


def add_t3_preview_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--port", type=validate_positive_integer, required=True)
    parser.add_argument("--host", default="127.0.0.1", help="Loopback address to probe (127.0.0.1 or ::1 for T3)")
    parser.add_argument("--protocol", choices=("http", "https"), help="T3 preview protocol (default: http)")
    parser.add_argument("--path", help="T3 preview path, with optional query and fragment (default: /)")
    parser.add_argument("--profile", choices=("general", "godot"), default="general")
    parser.add_argument("--json", action="store_true")


def _navigation_path(path: str) -> str:
    validate_no_control_characters(path, "Preview path")
    if not path.startswith("/") or path.startswith("//") or "\\" in path:
        raise ValueError("Preview path must start with one '/' and use no backslashes")
    return quote(path, safe="/%?:@!$&'()*+,;=-._~[]#")


def _upstream_headers(host: str, port: int, protocol: str, path: str) -> tuple[int, dict[str, str]]:
    # Both T3 preview runtimes use localhost as the HTTP and TLS origin. Dial
    # the requested literal address directly to avoid proxies and name resolution.
    with ExitStack() as stack:
        transport = stack.enter_context(socket.create_connection((host, port), timeout=3))
        if protocol == "https":
            transport = stack.enter_context(
                ssl.create_default_context().wrap_socket(transport, server_hostname="localhost"),
            )
        connection = stack.enter_context(closing(http.client.HTTPConnection("localhost", port)))
        connection.sock = transport
        connection.request("GET", path, headers={"Host": f"localhost:{port}", "User-Agent": "basaltwater-web-preview/1"})
        with connection.getresponse() as response:
            return response.status, {key.lower(): value for key, value in response.getheaders()}


def _resolve_t3_preview(args: argparse.Namespace) -> dict[str, object]:
    host = validate_network_ip(args.host, "preview host")
    if host not in ("127.0.0.1", "::1"):
        raise ValueError("T3 environment ports use 127.0.0.1 or ::1")
    port = validate_network_port(args.port, "preview port")
    protocol = args.protocol or "http"
    path = _navigation_path(args.path if args.path is not None else "/")
    request_path, _, _ = path.partition("#")
    authority = f"[{host}]" if host == "::1" else host
    result: dict[str, object] = {
        "ok": False,
        "component": "upstream",
        "upstream_url": f"{protocol}://{authority}:{port}{request_path}",
        "navigation": None,
        "issue": None,
        "remediation": "Start or repair the loopback server, then retry resolution.",
    }
    try:
        status, headers = _upstream_headers(host, port, protocol, request_path)
    except (OSError, http.client.HTTPException) as exc:
        issue = http_transport_issue(exc)
        result.update(issue=issue, error=str(exc))
        if issue == "certificate_untrusted":
            result["remediation"] = "Check the upstream certificate for localhost and trust on the T3 host."
        return result
    result["upstream_status"] = status
    if not http_status_is_healthy(status, headers):
        result.update(issue="http_error", error=f"Loopback endpoint returned {status}")
    elif args.profile == "godot" and not has_corp_isolation_headers(headers):
        result.update(
            issue="isolation_headers_missing",
            error="Loopback endpoint is missing Godot cross-origin isolation headers",
            remediation="Configure isolation headers on the dev server or use a Godot HTTPS forward.",
        )
    else:
        target: dict[str, object] = {"kind": "environment-port", "port": port}
        if args.protocol is not None:
            target["protocol"] = protocol
        if args.path is not None:
            target["path"] = path
        result.update(ok=True, navigation={"target": target}, remediation=None)
    return result


def run_t3_preview_command(args: argparse.Namespace) -> int:
    try:
        result = _resolve_t3_preview(args)
    except ValueError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, sort_keys=True))
    elif result["ok"]:
        print(json.dumps(result["navigation"], sort_keys=True))
    else:
        print(f"{result['component']}: {result['issue']}. {result['remediation']}", file=sys.stderr)
    return 0 if result["ok"] else 1
