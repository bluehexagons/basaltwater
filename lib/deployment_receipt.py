"""Verify a project-owned deployment from the machine running the command."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import http.client
import ipaddress
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from lib.local_http import http_transport_issue, open_direct
from lib.tool_results import add_json_option, failure, write_result
from lib.validation import validate_network_ip, validate_network_port, validate_no_control_characters
from lib.validators import validate_host


_REVISION = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", re.ASCII)
_HEADER = re.compile(r"[A-Za-z0-9-]{1,64}", re.ASCII)
_UNIT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@:-]{0,245}\.service", re.ASCII)


def add_deployment_receipt_parser(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser("deployment-receipt", help="Run and verify a project deployment")
    parser.add_argument("--revision", required=True, help="Expected full commit hash")
    parser.add_argument("--revision-header", default="X-Release-Revision")
    parser.add_argument("--loopback-url", required=True, help="HTTP health URL reporting the running revision")
    parser.add_argument("--lan-url", required=True, help="HTTPS health URL for the same release")
    parser.add_argument("--restart-unit", action="append", default=[], help="Restart this systemd service after activation; repeatable")
    parser.add_argument("--user-units", action="store_true", help="Use the current user's systemd manager for restarts")
    parser.add_argument("--wait", type=int, default=15, help="Seconds to retry transient readiness after activation or a restart")
    add_json_option(parser)
    parser.add_argument("activate", nargs=argparse.REMAINDER, help="Optional release activation command after --")


def _health_url(url: str, *, loopback: bool) -> str:
    validate_no_control_characters(url, "Health URL")
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if ":" in host:
        validate_network_ip(host, "Health URL host")
    elif not validate_host(host):
        raise ValueError("Invalid health URL host")
    if (
        len(url) > 2048 or not url.isascii() or any(char.isspace() for char in url)
        or "\\" in url or "?" in url or "#" in url
        or parsed.username is not None or parsed.password is not None
        or parsed.scheme != ("http" if loopback else "https")
    ):
        raise ValueError("Use an HTTP loopback URL and a LAN HTTPS URL without credentials, queries or fragments")
    if parsed.port is not None:
        validate_network_port(parsed.port, "Health URL port")
    if loopback and host not in {"127.0.0.1", "::1"}:
        raise ValueError("Loopback health URL must use 127.0.0.1 or ::1")
    if not loopback:
        try:
            local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = host.rstrip(".") == "localhost" or host.rstrip(".").endswith(".localhost")
        if local:
            raise ValueError("LAN health URL must use a network address")
    return url


def _command_stage(argv: list[str]) -> dict:
    try:
        result = subprocess.run(argv, stdout=sys.stderr, check=False)
    except KeyboardInterrupt:
        return {"status": "failed", "issue": "interrupted"}
    except OSError:
        return {"status": "failed", "issue": "command_launch_failed"}
    return {"status": "passed" if result.returncode == 0 else "failed", "exit_code": result.returncode}


def _probe(url: str, header: str, timeout: float) -> tuple[dict, str | None]:
    stage = {"url": url, "network_origin": "command_host"}
    request = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
    try:
        with open_direct(request, timeout=timeout) as response:
            status = response.status
            values = response.headers.get_all(header, [])
    except urllib.error.HTTPError as exc:
        with exc:
            return {**stage, "status": "failed", "http_status": exc.code, "issue": "http_error"}, None
    except (OSError, http.client.HTTPException) as exc:
        return {**stage, "status": "failed", "issue": http_transport_issue(exc)}, None
    if not 200 <= status < 300:
        return {**stage, "status": "failed", "http_status": status, "issue": "http_error"}, None
    revision = values[0] if len(values) == 1 and _REVISION.fullmatch(values[0]) else None
    return {**stage, "status": "passed", "http_status": status}, revision


def _wait_for_health(url: str, header: str, wait: int) -> tuple[dict, str | None]:
    deadline = time.monotonic() + wait
    timeout = min(5, wait) if wait else 5
    while True:
        stage, observed = _probe(url, header, timeout)
        remaining = deadline - time.monotonic()
        transient = (
            stage.get("issue") in {"connection_refused", "timeout", "transport_failure"}
            or stage.get("http_status") in {502, 503, 504}
        )
        if remaining <= 0 or not transient:
            return stage, observed
        time.sleep(min(0.25, remaining))
        timeout = max(0.01, min(5, deadline - time.monotonic()))


def _revision_check(observed: str | None, expected: str, health: dict) -> dict:
    if health["status"] != "passed":
        return {"status": "skipped", "issue": "health_check_failed"}
    if observed is None:
        return {"status": "failed", "issue": "revision_header_missing_or_invalid"}
    if observed != expected:
        return {"status": "failed", "issue": "revision_mismatch", "observed": observed}
    return {"status": "passed", "observed": observed}


def run_deployment_receipt(args: argparse.Namespace) -> int:
    try:
        loopback = _health_url(args.loopback_url, loopback=True)
        lan = _health_url(args.lan_url, loopback=False)
        if not _REVISION.fullmatch(args.revision):
            raise ValueError("Expected revision must be a full lowercase Git commit hash")
        if not _HEADER.fullmatch(args.revision_header):
            raise ValueError("Invalid revision header name")
        if any(not _UNIT.fullmatch(unit) for unit in args.restart_unit):
            raise ValueError("Restart units must be literal systemd .service names")
        if args.user_units and not args.restart_unit:
            raise ValueError("--user-units requires --restart-unit")
        if not 0 <= args.wait <= 120:
            raise ValueError("--wait must be between 0 and 120 seconds")
        if args.activate and (args.activate[0] != "--" or len(args.activate) < 2):
            raise ValueError("Pass an activation command after --")
    except ValueError as exc:
        if args.json:
            return write_result(failure("invalid_input", str(exc), stage="validation", effect="none"))
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    stages = {"activation": _command_stage(args.activate[1:]) if args.activate else {"status": "skipped"}}
    blocked = "activation_failed" if stages["activation"]["status"] == "failed" else None
    wait = args.wait if args.activate or args.restart_unit else 0
    systemctl = ["systemctl", "--user"] if args.user_units else ["systemctl"]
    for unit in dict.fromkeys(args.restart_unit):
        if blocked:
            stage = {"status": "skipped", "issue": blocked}
        else:
            stage = _command_stage([*systemctl, "restart", unit])
            if stage["status"] == "failed":
                blocked = "restart_failed"
        stages[f"restart:{unit}"] = stage
    for name, url in (("loopback", loopback), ("lan_https", lan)):
        if blocked:
            stages[name] = {"status": "skipped", "issue": blocked}
            stages[f"{name}_revision"] = {"status": "skipped", "issue": blocked}
            continue
        stages[name], observed = _wait_for_health(url, args.revision_header, wait)
        stages[f"{name}_revision"] = _revision_check(observed, args.revision, stages[name])
    failed = next(((name, stage) for name, stage in stages.items() if stage["status"] == "failed"), None)
    result = {
        "schema_version": 1, "recorded_at": datetime.now(timezone.utc).isoformat(), "ok": failed is None,
        "expected_revision": args.revision, "client_device_checked": False, "stages": stages,
    }
    if failed:
        name, details = failed
        issue = details.get("issue", blocked or "verification_failed")
        result.update(issue=issue, error=f"Deployment stage {name} failed ({issue}). Inspect the stages before retrying.")
        if args.activate or args.restart_unit:
            effect = "applied"
            if blocked:
                effect = "partial" if any(stage["status"] == "passed" for stage in stages.values()) else "unknown"
                if effect == "unknown" and issue == "command_launch_failed":
                    effect = "none"
            result.update(stage=name, effect=effect)
    if args.json:
        return write_result(result)
    else:
        print(f"Expected revision {args.revision}")
        for name, stage in stages.items():
            details = ", ".join(f"{key}={value}" for key, value in stage.items() if key not in {"status", "url"})
            print(f"{name}: {stage['status']}" + (f" ({details})" if details else ""))
        print("HTTP checks ran from this host. Client device untested.")
    return 0 if failed is None else 1
