#!/usr/bin/env python3
"""Small local broker for issuing provider-native device pairing links."""

from __future__ import annotations

import argparse
import html
import ipaddress
import json
import os
import re
import secrets
import signal
import socketserver
import stat
import subprocess
import threading
import time
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler
from typing import Any
from urllib.parse import parse_qs, urlsplit


MAX_REQUEST_BYTES = 4096
MAX_PROVIDER_OUTPUT_BYTES = 128 * 1024
MAX_CONNECT_OUTPUT_BYTES = 64 * 1024
MAX_CONNECT_INPUT_BYTES = 512
NONCE_TTL_SECONDS = 10 * 60
CONNECT_JOB_TTL_SECONDS = 15 * 60
RATE_WINDOW_SECONDS = 60
RATE_LIMIT = 5
MAX_RATE_SOURCES = 1024
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$"
)
_ANSI_ESCAPE_RE = re.compile(
    r"(?:\x1b\][^\x07]*(?:\x07|\x1b\\)|"
    r"\x1b(?:[@-Z\\^_]|\[[0-?]*[ -/]*[@-~]))"
)
_INCOMPLETE_ANSI_ESCAPE_RE = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*|\][^\x07]*)?$"
)
_CONNECT_URL_RE = re.compile(r"https?://[^\s<>]+")
_RELAY_INSTALL_PROMPT_RE = re.compile(
    r"The T3 relay client is required for T3 Connect\.\s+"
    r"Download and install version [^?\r\n]{1,128}\?",
    re.IGNORECASE,
)
_PAGE_STYLE = """
:root {
  color-scheme: light;
  font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
    "Segoe UI", sans-serif;
  color: #172033;
  background: #f4f7fb;
}
* { box-sizing: border-box; }
body { margin: 0; min-height: 100vh; background: #f4f7fb; }
main { width: min(100% - 2rem, 60rem); margin: 0 auto; padding: 2.5rem 0 4rem; }
.page-header { margin-bottom: 1.75rem; }
.eyebrow { margin: 0 0 .4rem; color: #52647d; font-size: .76rem;
  font-weight: 700; letter-spacing: .11em; text-transform: uppercase; }
h1, h2, h3 { color: #101828; line-height: 1.2; }
h1 { margin: 0; font-size: clamp(1.8rem, 4vw, 2.5rem); }
h2 { margin: 0; font-size: 1.25rem; }
h3 { margin: 0; font-size: 1rem; }
p { line-height: 1.55; }
.lead { margin: .6rem 0 0; color: #475467; font-size: 1.05rem; }
.muted { color: #667085; }
.card { margin-top: 1.25rem; padding: 1.35rem; border: 1px solid #d9e1ec;
  border-radius: .9rem; background: #fff; box-shadow: 0 8px 24px rgba(16,24,40,.05); }
.card-heading { margin-bottom: 1rem; }
.card-heading p { margin: .45rem 0 0; }
.action-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(15rem, 1fr)); gap: .8rem; }
.action-card { display: flex; flex-direction: column; gap: .55rem; padding: 1rem;
  border: 1px solid #d9e1ec; border-radius: .7rem; background: #fbfcfe; }
.action-card p { margin: 0; font-size: .9rem; }
.actions { display: flex; flex-wrap: wrap; align-items: center; gap: .65rem; margin-top: 1rem; }
form { margin: 0; }
button, a.button { display: inline-flex; align-items: center; justify-content: center; min-height: 2.7rem;
  padding: .65rem 1rem; border: 1px solid #155eef; border-radius: .5rem; background: #155eef;
  color: #fff; font: inherit; font-weight: 650; text-decoration: none; cursor: pointer; }
button:hover, a.button:hover { background: #004eeb; }
button.secondary, a.button.secondary { border-color: #b7c4d6; background: #fff; color: #344054; }
button.secondary:hover, a.button.secondary:hover { background: #f2f4f7; }
button.danger { border-color: #d92d20; background: #d92d20; }
button:focus-visible, a.button:focus-visible, a.text-link:focus-visible, input:focus-visible, summary:focus-visible {
  outline: 3px solid #84adff; outline-offset: 2px; }
.notice { margin: 1rem 0; padding: .85rem 1rem; border: 1px solid #cbd5e1;
  border-radius: .65rem; background: #f8fafc; }
.notice p { margin: .3rem 0 0; }
.notice.info { border-color: #b8d4fe; background: #eff6ff; }
.notice.success { border-color: #abefc6; background: #ecfdf3; }
.notice.error { border-color: #fecdca; background: #fff5f4; color: #912018; }
.status-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(10rem, 1fr));
  gap: .65rem; margin: 1rem 0; }
.status-item { margin: 0; padding: .75rem; border: 1px solid #e4e7ec; border-radius: .6rem; background: #fcfcfd; }
.status-label { display: block; color: #667085; font-size: .78rem; }
.status-value { display: block; margin-top: .2rem; font-weight: 700; overflow-wrap: anywhere; }
.status-value.ok { color: #067647; }
.status-value.off { color: #b42318; }
.status-empty { margin: 1rem 0; }
.settings { margin-top: 1.25rem; padding-top: 1.15rem; border-top: 1px solid #eaecf0; }
.settings p { margin: .4rem 0 0; }
.checkbox { display: inline-flex; align-items: flex-start; gap: .55rem; color: #344054; }
.checkbox input { width: 1.1rem; height: 1.1rem; margin: .15rem 0 0; accent-color: #155eef; }
input[type=text] { width: min(100%, 32rem); min-height: 2.7rem; margin-top: .45rem; padding: .6rem .7rem;
  border: 1px solid #b7c4d6; border-radius: .5rem; color: #172033; font: inherit; }
.field { display: flex; flex-direction: column; max-width: 34rem; margin-top: 1rem; color: #344054; font-weight: 600; }
.output { margin: 1rem 0; border: 1px solid #d9e1ec; border-radius: .6rem; background: #fbfcfe; }
.output summary { padding: .75rem 1rem; color: #344054; font-weight: 650; cursor: pointer; }
pre { max-height: 20rem; margin: 0; padding: .9rem 1rem; overflow: auto; border-top: 1px solid #e4e7ec;
  background: #101828; color: #e6edf7; font: .9rem/1.6 ui-monospace, SFMono-Regular, Menlo, monospace;
  white-space: pre-wrap; overflow-wrap: anywhere; tab-size: 4; }
.link-box { margin: 1rem 0; padding: .8rem; border: 1px solid #d9e1ec; border-radius: .6rem; background: #f8fafc; }
.link-box code { display: block; margin-top: .35rem; overflow-wrap: anywhere; }
.footer-nav { margin-top: 1.25rem; }
a.text-link { color: #155eef; font-weight: 650; text-decoration: none; }
a.text-link:hover { text-decoration: underline; }
@media (max-width: 38rem) {
  main { width: min(100% - 1rem, 60rem); padding-top: 1.25rem; }
  .card { padding: 1rem; }
  button, a.button { width: 100%; }
  .actions form { width: 100%; }
}
"""


class PairingError(RuntimeError):
    """A safe provider failure that may be shown without secret output."""


def _sanitize_connect_output(value: str) -> str:
    """Turn terminal-oriented CLI output into readable plain text."""

    cleaned = _ANSI_ESCAPE_RE.sub("", value)
    cleaned = _INCOMPLETE_ANSI_ESCAPE_RE.sub("", cleaned)
    cleaned = cleaned.replace("\x1b", "").replace("\r\n", "\n")
    readable: list[str] = []
    for character in cleaned:
        codepoint = ord(character)
        if character == "\r":
            if readable and readable[-1] != "\n":
                readable.append("\n")
            continue
        if character == "\b":
            if readable and readable[-1] not in {"\n", "\t"}:
                readable.pop()
            continue
        if character in {"\n", "\t"} or (32 <= codepoint < 127) or codepoint > 159:
            readable.append(character)
    return "".join(readable).lstrip("\n")


def _load_provider_config(path: str) -> dict[str, dict[str, Any]]:
    with open(path, encoding="utf-8") as file_obj:
        payload = json.load(file_obj)
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("Unsupported device-pairing provider configuration")
    raw_providers = payload.get("providers")
    if not isinstance(raw_providers, dict) or not raw_providers:
        raise ValueError("Device-pairing provider configuration is empty")

    providers: dict[str, dict[str, Any]] = {}
    for name, raw_provider in raw_providers.items():
        if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9-]{1,32}", name):
            raise ValueError("Invalid device-pairing provider name")
        if not isinstance(raw_provider, dict):
            raise ValueError(f"Invalid configuration for provider {name}")
        command = raw_provider.get("command")
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(part, str) and part for part in command)
            or any("\x00" in part for part in command if isinstance(part, str))
            or not os.path.isabs(command[0])
        ):
            raise ValueError(f"Provider {name} must use an absolute command")
        for field in ("label", "base_url_flag", "url_field", "expires_field"):
            if not isinstance(raw_provider.get(field), str) or not raw_provider[field]:
                raise ValueError(f"Provider {name} has an invalid {field}")
        public_port = raw_provider.get("public_port")
        if not isinstance(public_port, int) or isinstance(public_port, bool):
            raise ValueError(f"Provider {name} has an invalid public port")
        if not 1 <= public_port <= 65535:
            raise ValueError(f"Provider {name} has an invalid public port")
        https_public_port = raw_provider.get("https_public_port")
        if https_public_port is not None and (
            not isinstance(https_public_port, int)
            or isinstance(https_public_port, bool)
            or not 1024 <= https_public_port <= 65535
        ):
            raise ValueError(f"Provider {name} has an invalid HTTPS public port")
        connect = raw_provider.get("connect")
        if connect is not None:
            if not isinstance(connect, dict):
                raise ValueError(f"Provider {name} has an invalid T3 Connect configuration")
            for key in ("link_command", "status_command", "unlink_command"):
                command_value = connect.get(key)
                if (
                    not isinstance(command_value, list)
                    or not command_value
                    or not all(isinstance(part, str) and part for part in command_value)
                    or any(
                        "\x00" in part
                        for part in command_value
                        if isinstance(part, str)
                    )
                    or not os.path.isabs(command_value[0])
                ):
                    raise ValueError(f"Provider {name} has an invalid T3 Connect {key}")
            restart_request = connect.get("restart_request")
            if (
                not isinstance(restart_request, str)
                or not os.path.isabs(restart_request)
                or len(restart_request) > 4096
                or "\x00" in restart_request
            ):
                raise ValueError(f"Provider {name} has an invalid T3 Connect restart request")
        providers[name] = raw_provider
    return providers


def _normalized_host(value: str) -> str:
    host = value.strip()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if not _HOSTNAME_RE.fullmatch(host):
            raise PairingError("The requested public host is invalid")
        return host.lower()
    return f"[{address}]" if address.version == 6 else str(address)


def _public_base_url(
    handler: BaseHTTPRequestHandler,
    port: int,
    https_port: int | None = None,
) -> str:
    scheme = (handler.headers.get("X-Forwarded-Proto") or "http").strip().lower()
    if scheme not in {"http", "https"}:
        raise PairingError("The requested public URL scheme is invalid")
    forwarded_host = handler.headers.get("X-Forwarded-Host") or ""
    try:
        parsed_host = urlsplit(f"//{forwarded_host}")
        host = _normalized_host(parsed_host.hostname or "")
    except ValueError as exc:
        raise PairingError("The requested public host is invalid") from exc
    effective_port = https_port if scheme == "https" and https_port else port
    default_port = 443 if scheme == "https" else 80
    suffix = "" if effective_port == default_port else f":{effective_port}"
    return f"{scheme}://{host}{suffix}"


def _safe_pairing_url(value: object, expected_base: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) > 8192
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise PairingError("The provider did not return a pairing URL")
    try:
        parsed = urlsplit(value)
        expected = urlsplit(expected_base)
    except ValueError as exc:
        raise PairingError("The provider returned an unexpected pairing URL") from exc
    fragment = parse_qs(parsed.fragment, keep_blank_values=True)
    tokens = fragment.get("token") or []
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.scheme != expected.scheme
        or parsed.netloc != expected.netloc
        or parsed.path != "/pair"
        or parsed.query
        or parsed.username is not None
        or parsed.password is not None
        or set(fragment) != {"token"}
        or len(tokens) != 1
        or not 1 <= len(tokens[0]) <= 4096
    ):
        raise PairingError("The provider returned an unexpected pairing URL")
    return value


class ConnectJob:
    """Run the interactive headless Connect command without logging its output."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._raw_output = ""
        self._output = ""
        self._relay_install_confirmed = False
        self._finished_at: float | None = None
        self._returncode: int | None = None
        self._error: str | None = None

    def _append_output(self, text: str) -> None:
        process: subprocess.Popen[str] | None = None
        with self._lock:
            self._raw_output = (self._raw_output + text)[-MAX_CONNECT_OUTPUT_BYTES:]
            self._output = _sanitize_connect_output(self._raw_output)
            if (
                not self._relay_install_confirmed
                and _RELAY_INSTALL_PROMPT_RE.search(self._output)
            ):
                self._relay_install_confirmed = True
                process = self._process
        if process is None or process.stdin is None:
            return
        with self._lock:
            if self._process is not process or process.stdin is None:
                return
            try:
                self._write_input(process, b"y\n")
            except (BrokenPipeError, OSError, ValueError):
                if self._process is process:
                    self._error = "T3 relay installation confirmation could not be sent"

    @staticmethod
    def _write_input(process: subprocess.Popen[str], content: bytes) -> None:
        """Never hold the job lock waiting for a provider to drain its stdin."""
        assert process.stdin is not None
        descriptor = process.stdin.fileno()
        os.set_blocking(descriptor, False)
        if os.write(descriptor, content) != len(content):
            raise OSError('Incomplete T3 Connect input write')

    def _expire(self, process: subprocess.Popen[str]) -> None:
        """Enforce the deadline even when no browser requests a snapshot."""
        with self._lock:
            if self._process is not process:
                return
            self._error = "The T3 Connect operation expired; start it again"
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        # Reap even if a descendant is keeping the output reader blocked.
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass

    def _watch(self, process: subprocess.Popen[str], timer: threading.Timer) -> None:
        assert process.stdout is not None
        try:
            for chunk in iter(lambda: process.stdout.read(1), ""):
                if chunk:
                    self._append_output(chunk)
            returncode = process.wait()
        except OSError:
            returncode = process.poll()
        finally:
            timer.cancel()
            process.stdout.close()
            with self._lock:
                if process.stdin is not None:
                    process.stdin.close()
        with self._lock:
            self._returncode = returncode if returncode is not None else 1
            self._finished_at = time.monotonic()
            self._process = None
        if returncode == 0:
            request_path = self.config["restart_request"]
            try:
                descriptor = os.open(
                    request_path,
                    os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                    0o600,
                )
                os.close(descriptor)
            except OSError:
                with self._lock:
                    self._error = (
                        "Connect completed, but the T3 service restart "
                        "could not be requested"
                    )

    def start(self) -> None:
        with self._lock:
            if self._process is not None:
                raise PairingError("A T3 Connect operation is already running")
            self._raw_output = ""
            self._output = ""
            self._relay_install_confirmed = False
            self._error = None
            self._returncode = None
            self._finished_at = None
            try:
                process = subprocess.Popen(
                    self.config["link_command"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    errors="replace",
                    bufsize=1,
                    env=os.environ.copy(),
                    start_new_session=True,
                )
            except (OSError, ValueError) as exc:
                raise PairingError("T3 Connect could not be started") from exc
            self._process = process
        timer = threading.Timer(CONNECT_JOB_TTL_SECONDS, self._expire, args=(process,))
        timer.daemon = True
        timer.start()
        threading.Thread(target=self._watch, args=(process, timer), daemon=True).start()

    def send_input(self, value: str) -> None:
        encoded = value.encode("utf-8")
        if (
            not value
            or len(encoded) > MAX_CONNECT_INPUT_BYTES
            or any(
                ord(character) < 32 and character not in "\t"
                for character in value
            )
        ):
            raise PairingError("T3 Connect input is invalid")
        with self._lock:
            process = self._process
            if process is None or process.stdin is None:
                raise PairingError("No T3 Connect operation is waiting for input")
            try:
                self._write_input(process, encoded + b"\n")
            except BlockingIOError as exc:
                raise PairingError(
                    "The T3 Connect operation is not accepting input; try again later"
                ) from exc
            except (BrokenPipeError, OSError, ValueError) as exc:
                raise PairingError(
                    "The T3 Connect operation is no longer running"
                ) from exc

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            process = self._process
            output = self._output
            returncode = self._returncode
            error = self._error
        return {
            "active": process is not None,
            "error": error,
            "output": output,
            "returncode": returncode,
        }


def _connect_output_html(output: str) -> str:
    """Render only validated HTTP(S) URLs in CLI output as links."""

    rendered: list[str] = []
    cursor = 0
    for match in _CONNECT_URL_RE.finditer(output):
        value = match.group(0).rstrip(".,)")
        try:
            parsed = urlsplit(value)
        except ValueError:
            continue
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or any(ord(character) < 32 for character in value)
        ):
            continue
        rendered.append(html.escape(output[cursor : match.start()]))
        escaped = html.escape(value, quote=True)
        rendered.append(
            f'<a href="{escaped}" target="_blank" rel="noopener noreferrer">'
            f"{html.escape(value)}</a>"
        )
        if len(value) < len(match.group(0)):
            rendered.append(html.escape(match.group(0)[len(value) :]))
        cursor = match.end()
    rendered.append(html.escape(output[cursor:]))
    return "".join(rendered)


class PairingState:
    def __init__(self, providers: dict[str, dict[str, Any]]) -> None:
        self.providers = providers
        self._lock = threading.Lock()
        self._nonces: dict[str, float] = {}
        self._requests: dict[str, list[float]] = {}
        self._connect_jobs: dict[str, ConnectJob] = {
            name: ConnectJob(provider["connect"])
            for name, provider in providers.items()
            if isinstance(provider.get("connect"), dict)
        }

    def new_nonce(self) -> str:
        now = time.monotonic()
        nonce = secrets.token_urlsafe(32)
        with self._lock:
            self._nonces = {
                key: created
                for key, created in self._nonces.items()
                if now - created <= NONCE_TTL_SECONDS
            }
            while len(self._nonces) >= 128:
                oldest = min(self._nonces, key=self._nonces.__getitem__)
                self._nonces.pop(oldest, None)
            self._nonces[nonce] = now
        return nonce

    def consume_nonce(self, nonce: str) -> bool:
        now = time.monotonic()
        with self._lock:
            created = self._nonces.pop(nonce, None)
        return created is not None and now - created <= NONCE_TTL_SECONDS

    def nonce_is_valid(self, nonce: str) -> bool:
        now = time.monotonic()
        with self._lock:
            created = self._nonces.get(nonce)
            if created is None:
                return False
            if now - created > NONCE_TTL_SECONDS:
                self._nonces.pop(nonce, None)
                return False
            return True

    def allow_request(self, source: str) -> bool:
        now = time.monotonic()
        with self._lock:
            active_requests: dict[str, list[float]] = {}
            for key, timestamps in self._requests.items():
                recent_timestamps = [
                    timestamp
                    for timestamp in timestamps
                    if now - timestamp <= RATE_WINDOW_SECONDS
                ]
                if recent_timestamps:
                    active_requests[key] = recent_timestamps
            self._requests = active_requests
            if source not in self._requests:
                while len(self._requests) >= MAX_RATE_SOURCES:
                    oldest = next(iter(self._requests))
                    self._requests.pop(oldest, None)
            recent = self._requests.get(source, [])
            if len(recent) >= RATE_LIMIT:
                self._requests[source] = recent
                return False
            recent.append(now)
            self._requests[source] = recent
        return True

    def issue(self, provider_name: str, base_url: str) -> tuple[str, str]:
        provider = self.providers.get(provider_name)
        if provider is None:
            raise PairingError("Unknown device-pairing provider")
        command = [
            *provider["command"],
            provider["base_url_flag"],
            base_url,
        ]
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
                env=os.environ.copy(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PairingError("The pairing provider could not be started") from exc
        if result.returncode != 0:
            raise PairingError("The pairing provider could not issue a link")
        if len(result.stdout.encode("utf-8")) > MAX_PROVIDER_OUTPUT_BYTES:
            raise PairingError("The pairing provider returned too much data")
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise PairingError("The pairing provider returned invalid data") from exc
        if not isinstance(payload, dict):
            raise PairingError("The pairing provider returned invalid data")
        pair_url = _safe_pairing_url(payload.get(provider["url_field"]), base_url)
        expires = payload.get(provider["expires_field"])
        if not isinstance(expires, str) or len(expires) > 128:
            expires = "soon"
        return pair_url, expires

    def connect_snapshot(self, provider_name: str) -> dict[str, object]:
        job = self._connect_jobs.get(provider_name)
        if job is None:
            raise PairingError("T3 Connect is not configured")
        snapshot = job.snapshot()
        try:
            result = subprocess.run(
                job.config["status_command"],
                check=False,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=10,
                env=os.environ.copy(),
            )
            if (
                result.returncode == 0
                and len(result.stdout.encode("utf-8")) <= MAX_PROVIDER_OUTPUT_BYTES
            ):
                payload = json.loads(result.stdout)
                if isinstance(payload, dict):
                    snapshot["status"] = {
                        key: payload.get(key)
                        for key in (
                            "desired",
                            "authenticated",
                            "linked",
                            "provisioned",
                            "relayUrl",
                            "publishEnabled",
                        )
                        if isinstance(payload.get(key), (bool, str))
                        or payload.get(key) is None
                    }
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        return snapshot

    def start_connect(self, provider_name: str) -> None:
        job = self._connect_jobs.get(provider_name)
        if job is None:
            raise PairingError("T3 Connect is not configured")
        job.start()

    def set_connect_enabled(self, provider_name: str, enabled: bool) -> None:
        job = self._connect_jobs.get(provider_name)
        if job is None:
            raise PairingError("T3 Connect is not configured")
        status = self.connect_snapshot(provider_name).get("status")
        desired = bool(isinstance(status, dict) and status.get("desired") is True)
        if enabled:
            if not desired:
                job.start()
        else:
            self.unlink_connect(provider_name)

    def send_connect_input(self, provider_name: str, value: str) -> None:
        job = self._connect_jobs.get(provider_name)
        if job is None:
            raise PairingError("T3 Connect is not configured")
        job.send_input(value)

    def unlink_connect(self, provider_name: str) -> None:
        provider = self.providers.get(provider_name)
        if provider is None or not isinstance(provider.get("connect"), dict):
            raise PairingError("T3 Connect is not configured")
        job = self._connect_jobs[provider_name]
        if job.snapshot()["active"]:
            raise PairingError("Finish the current T3 Connect operation first")
        try:
            result = subprocess.run(
                job.config["unlink_command"],
                check=False,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=30,
                env=os.environ.copy(),
            )
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            raise PairingError("T3 Connect could not be disabled") from exc
        if result.returncode != 0:
            raise PairingError("T3 Connect could not be disabled")
        try:
            descriptor = os.open(
                job.config["restart_request"],
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                0o600,
            )
            os.close(descriptor)
        except OSError as exc:
            raise PairingError(
                "T3 Connect was disabled, but the T3 service restart "
                "could not be requested"
            ) from exc


class PairingRequestHandler(BaseHTTPRequestHandler):
    server_version = "basaltwater-device-pairing"
    sys_version = ""

    @property
    def state(self) -> PairingState:
        return self.server.pairing_state  # type: ignore[attr-defined,no-any-return]

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _send_html(
        self,
        status: HTTPStatus,
        title: str,
        body: str,
        *,
        nonce: str | None = None,
    ) -> None:
        page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>{_PAGE_STYLE}</style>
</head><body><main><header class="page-header">
<p class="eyebrow">basaltwater</p><h1>{html.escape(title)}</h1>
</header>{body}</main></body></html>"""
        encoded = page.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Pragma", "no-cache")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'",
        )
        if nonce is not None:
            secure = (self.headers.get("X-Forwarded-Proto") or "").lower() == "https"
            cookie = f"basaltwater_pairing_nonce={nonce}; Path=/; HttpOnly; SameSite=Strict"
            if secure:
                cookie += "; Secure"
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(encoded)

    def _nonce_cookie(self) -> str:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie") or "")
        except CookieError:
            return ""
        morsel = cookie.get("basaltwater_pairing_nonce")
        return morsel.value if morsel is not None else ""

    def _page_nonce(self) -> str:
        current = self._nonce_cookie()
        if current and self.state.nonce_is_valid(current):
            return current
        return self.state.new_nonce()

    def _connect_section(
        self,
        name: str,
        provider: dict[str, Any],
        nonce: str,
        *,
        error: str | None = None,
    ) -> str:
        snapshot = self.state.connect_snapshot(name)
        status = snapshot.get("status")
        status_items: list[str] = []
        value_labels = {
            "desired": ("Enabled", "Disabled"),
            "authenticated": ("Complete", "Pending"),
            "linked": ("Linked", "Not linked"),
            "provisioned": ("Active", "Not active"),
        }
        if isinstance(status, dict):
            for key, label in (
                ("desired", "Startup"),
                ("authenticated", "Authorization"),
                ("linked", "Account"),
                ("provisioned", "Relay"),
            ):
                value = status.get(key)
                if isinstance(value, bool):
                    value_label = value_labels[key][0 if value else 1]
                    state_class = "ok" if value else "off"
                    status_items.append(
                        '<div class="status-item"><dt class="status-label">'
                        f"{label}</dt><dd class=\"status-value {state_class}\">"
                        f"{value_label}</dd></div>"
                    )
            relay_url = status.get("relayUrl")
            if isinstance(relay_url, str) and relay_url:
                status_items.append(
                    '<div class="status-item"><dt class="status-label">Relay address</dt>'
                    f'<dd class="status-value">{html.escape(relay_url, quote=True)}</dd></div>'
                )
        status_block = (
            f'<dl class="status-grid" aria-label="T3 Connect status">{"".join(status_items)}</dl>'
            if status_items
            else '<p class="status-empty muted">Current status is not available yet. '
            'Refresh after the authorization flow finishes.</p>'
        )
        output = str(snapshot.get("output") or "")
        error_text = error or snapshot.get("error")
        error_block = (
            '<div class="notice error"><strong>Action needs attention</strong>'
            f"<p>{html.escape(str(error_text))}</p></div>"
            if error_text
            else ""
        )
        escaped_name = html.escape(name, quote=True)
        escaped_nonce = html.escape(nonce, quote=True)
        active = bool(snapshot.get("active"))
        enabled = bool(isinstance(status, dict) and status.get("desired") is True)
        returncode = snapshot.get("returncode")
        finished = (
            not active
            and isinstance(returncode, int)
            and not isinstance(returncode, bool)
        )
        if active:
            progress_block = (
                '<div class="notice info"><strong>Authorization in progress</strong>'
                "<p>The known relay-install confirmation is accepted automatically. "
                "When T3 asks for an authorization code or response, enter it here.</p></div>"
            )
        elif finished and returncode == 0:
            progress_block = (
                '<div class="notice success"><strong>Authorization completed</strong>'
                "<p>The managed service is applying the new relay state. Refresh in "
                "a moment to confirm that the relay is active.</p></div>"
            )
        elif finished:
            progress_block = (
                '<div class="notice error"><strong>Authorization did not complete</strong>'
                "<p>Review the command output below, then try again.</p></div>"
            )
        else:
            progress_block = (
                '<p class="muted">Start authorization to install or re-authorize the '
                "T3 Connect relay on this machine.</p>"
            )
        output_block = (
            f'<details class="output"{" open" if active or finished and returncode != 0 else ""}>'
            "<summary>Show T3 command output</summary>"
            f"<pre>{_connect_output_html(output)}</pre></details>"
            if output
            else ""
        )
        if active:
            action = (
                f'<form method="post" action="/connect/{escaped_name}" class="field">'
                f'<input type="hidden" name="nonce" value="{escaped_nonce}">'
                '<input type="hidden" name="intent" value="input">'
                f'<label for="connect-input-{escaped_name}">Response or authorization code</label>'
                f'<input id="connect-input-{escaped_name}" type="text" name="input" maxlength="512" '
                'autocomplete="off" placeholder="Enter the requested response" required>'
                '<button type="submit">Send response</button></form>'
                '<div class="actions">'
                f'<form method="get" action="/connect/{escaped_name}">'
                '<button class="secondary" type="submit">Refresh status</button></form>'
                '</div>'
            )
        else:
            checked = " checked" if enabled else ""
            action = (
                '<div class="settings"><h3>Startup behavior</h3>'
                '<p class="muted">Keep Connect enabled after a restart. Turning this '
                'on starts authorization if the machine is not already authorized; '
                'turning it off stops the tunnel.</p>'
                f'<form method="post" action="/connect/{escaped_name}" class="actions">'
                f'<input type="hidden" name="nonce" value="{escaped_nonce}">'
                '<input type="hidden" name="intent" value="toggle">'
                f'<label class="checkbox"><input type="checkbox" name="enabled"{checked}> '
                '<span>Start Connect automatically</span></label>'
                '<button type="submit">Apply setting</button></form></div>'
                '<div class="actions">'
                f'<form method="get" action="/connect/{escaped_name}">'
                '<button class="secondary" type="submit">Refresh status</button></form>'
                f'<form method="post" action="/connect/{escaped_name}">'
                f'<input type="hidden" name="nonce" value="{escaped_nonce}">'
                '<input type="hidden" name="intent" value="start">'
                '<button class="secondary" type="submit">Start authorization</button></form>'
                '</div>'
            )
        provider_label = html.escape(str(provider.get("label") or "T3 Code"))
        heading_id = f"connect-heading-{escaped_name}"
        return (
            f'<section class="card" aria-labelledby="{heading_id}" aria-live="polite">'
            '<div class="card-heading"><p class="eyebrow">Relay access</p>'
            f'<h2 id="{heading_id}">{provider_label} Connect</h2>'
            '<p class="muted">Authorize this machine with T3 Connect. T3 installs its '
            'pinned relay client when requested, and basaltwater keeps the tunnel '
            'managed across restarts.</p></div>'
            f"{progress_block}{status_block}{error_block}{output_block}"
            + action
            + "</section>"
        )

    def _connect_page(
        self,
        name: str,
        provider: dict[str, Any],
        nonce: str,
        *,
        error: str | None = None,
    ) -> str:
        return (
            self._connect_section(name, provider, nonce, error=error)
            + '<nav class="footer-nav"><a class="text-link" href="/">'
            "← Back to device pairing</a></nav>"
        )

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        connect_match = re.fullmatch(r"/connect/([a-z0-9-]{1,32})", path)
        if connect_match is not None:
            provider_name = connect_match.group(1)
            provider = self.state.providers.get(provider_name)
            if provider is None or provider_name not in self.state._connect_jobs:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            nonce = self._page_nonce()
            self._send_html(
                HTTPStatus.OK,
                "T3 Connect",
                self._connect_page(provider_name, provider, nonce),
                nonce=nonce,
            )
            return
        if path != "/":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        nonce = self._page_nonce()
        buttons = []
        connect_sections = []
        for name, provider in sorted(self.state.providers.items()):
            escaped_name = html.escape(name, quote=True)
            escaped_nonce = html.escape(nonce, quote=True)
            escaped_label = html.escape(provider["label"])
            buttons.append(
                '<div class="action-card">'
                f'<form method="post" action="/pair/{escaped_name}">'
                f'<input type="hidden" name="nonce" value="{escaped_nonce}">'
                '<input type="hidden" name="intent" value="current">'
                f'<button type="submit">Pair this browser</button>'
                f'<p class="muted">Open an authenticated T3 session here.</p></form>'
                f'<form method="post" action="/pair/{escaped_name}">'
                f'<input type="hidden" name="nonce" value="{escaped_nonce}">'
                '<input type="hidden" name="intent" value="other">'
                f'<button class="secondary" type="submit">Pair another {escaped_label} client</button>'
                f'<p class="muted">Create a link to copy into the desktop or mobile app.</p></form>'
                '</div>'
            )
            if name in self.state._connect_jobs:
                connect_sections.append(
                    self._connect_section(name, provider, nonce)
                )
        body = (
            '<section class="card"><div class="card-heading">'
            '<p class="eyebrow">Device enrollment</p><h2>Pair a device</h2>'
            '<p class="muted">Create a short-lived, one-time T3 Code link. '
            'Each browser or app should use its own link.</p></div>'
            '<div class="action-grid">'
            + "".join(buttons)
            + "</div><p class=\"muted\">Never share a pairing link. It grants access "
            "to this machine and expires automatically.</p></section>"
            + "".join(connect_sections)
        )
        self._send_html(HTTPStatus.OK, "Device pairing", body, nonce=nonce)

    def do_POST(self) -> None:
        parsed_path = urlsplit(self.path)
        match = re.fullmatch(r"/(pair|connect)/([a-z0-9-]{1,32})", parsed_path.path)
        if match is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = (self.headers.get("Content-Type") or "").split(";", 1)[0]
        if content_type.strip().lower() != "application/x-www-form-urlencoded":
            self.send_error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = MAX_REQUEST_BYTES + 1
        if not 0 <= length <= MAX_REQUEST_BYTES:
            self.send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return
        try:
            form = parse_qs(
                self.rfile.read(length).decode("utf-8", errors="strict")
            )
        except UnicodeDecodeError:
            self.send_error(HTTPStatus.BAD_REQUEST)
            return
        submitted_nonce = (form.get("nonce") or [""])[0]
        intent = (form.get("intent") or [""])[0]
        route = match.group(1)
        allowed_intents = {
            "pair": {"current", "other"},
            "connect": {"start", "input", "toggle"},
        }
        if (
            not secrets.compare_digest(submitted_nonce, self._nonce_cookie())
            or not self.state.consume_nonce(submitted_nonce)
            or intent not in allowed_intents[route]
        ):
            self._send_html(
                HTTPStatus.FORBIDDEN,
                "Pairing request rejected",
                '<section class="card"><div class="notice error">'
                "<strong>This form has expired</strong>"
                "<p>Reload the enrollment page and try again.</p></div>"
                '<nav class="footer-nav"><a class="text-link" href="/">'
                "← Back to device pairing</a></nav></section>",
            )
            return
        raw_source = (self.headers.get("X-Real-IP") or "").strip()
        try:
            source = str(ipaddress.ip_address(raw_source))
        except ValueError:
            source = "local"
        if intent != "input" and not self.state.allow_request(source):
            self._send_html(
                HTTPStatus.TOO_MANY_REQUESTS,
                "Pairing temporarily limited",
                '<section class="card"><div class="notice error">'
                "<strong>Too many requests</strong>"
                "<p>Wait one minute before requesting another link.</p></div>"
                '<nav class="footer-nav"><a class="text-link" href="/">'
                "← Back to device pairing</a></nav></section>",
            )
            return
        provider_name = match.group(2)
        provider = self.state.providers.get(provider_name)
        if provider is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if route == "connect":
            if provider_name not in self.state._connect_jobs:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                if intent == "start":
                    self.state.start_connect(provider_name)
                elif intent == "input":
                    self.state.send_connect_input(
                        provider_name,
                        (form.get("input") or [""])[0],
                    )
                elif intent == "toggle":
                    self.state.set_connect_enabled(
                        provider_name,
                        (form.get("enabled") or [""])[0] == "on",
                    )
            except PairingError as exc:
                nonce = self.state.new_nonce()
                self._send_html(
                    HTTPStatus.BAD_REQUEST,
                    "T3 Connect action failed",
                    self._connect_page(
                        provider_name,
                        provider,
                        nonce,
                        error=str(exc),
                    ),
                    nonce=nonce,
                )
                return
            nonce = self.state.new_nonce()
            self._send_html(
                HTTPStatus.OK,
                "T3 Connect",
                self._connect_page(provider_name, provider, nonce),
                nonce=nonce,
            )
            return
        try:
            base_url = _public_base_url(
                self,
                provider["public_port"],
                provider.get("https_public_port"),
            )
            pair_url, expires = self.state.issue(provider_name, base_url)
        except PairingError as exc:
            self._send_html(
                HTTPStatus.BAD_GATEWAY,
                "Could not create pairing link",
                '<section class="card"><div class="notice error">'
                "<strong>Could not create pairing link</strong>"
                f"<p>{html.escape(str(exc))}</p></div>"
                '<nav class="footer-nav"><a class="text-link" href="/">'
                "← Back to device pairing</a></nav></section>",
            )
            return
        encoded_url = html.escape(pair_url, quote=True)
        link_label = (
            f"Pair this browser with {html.escape(provider['label'])}"
            if intent == "current"
            else "Open pairing link"
        )
        body = (
            '<section class="card"><p class="eyebrow">One-time link ready</p>'
            '<h2>Continue in T3 Code</h2>'
            '<p class="lead">Use this link once to authorize the selected device. '
            'It expires automatically and cannot be reused.</p>'
            f'<p><a class="button" href="{encoded_url}">{link_label}</a></p>'
            '<div class="link-box"><span class="muted">Pairing link</span>'
            f'<code>{html.escape(pair_url)}</code></div>'
            f'<p class="muted">Expires {html.escape(expires)}. Keep this link private.</p>'
            '<nav class="footer-nav"><a class="text-link" href="/">'
            '← Back to device pairing</a></nav></section>'
        )
        self._send_html(HTTPStatus.OK, "Pairing link created", body)


class ThreadingUnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, socket_path: str, state: PairingState) -> None:
        self.pairing_state = state
        super().__init__(socket_path, PairingRequestHandler)


def main() -> int:
    parser = argparse.ArgumentParser(description="basaltwater device-pairing broker")
    parser.add_argument("--config", required=True)
    parser.add_argument("--socket", required=True)
    args = parser.parse_args()

    providers = _load_provider_config(args.config)
    socket_path = os.path.abspath(args.socket)
    socket_parent = os.path.dirname(socket_path)
    if not os.path.isdir(socket_parent):
        raise RuntimeError(f"Pairing socket directory does not exist: {socket_parent}")
    if os.path.lexists(socket_path):
        mode = os.lstat(socket_path).st_mode
        if not stat.S_ISSOCK(mode):
            raise RuntimeError(f"Refusing non-socket pairing path: {socket_path}")
        os.unlink(socket_path)

    server = ThreadingUnixServer(socket_path, PairingState(providers))
    os.chmod(socket_path, 0o660)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        try:
            os.unlink(socket_path)
        except FileNotFoundError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
