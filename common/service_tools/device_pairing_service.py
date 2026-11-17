#!/usr/bin/env python3
"""Small local broker for issuing provider-native device pairing links."""

from __future__ import annotations

import argparse
import html
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import socketserver
import stat
import subprocess
import sys
import threading
import time
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler
from typing import Any
from urllib.parse import parse_qs, urlsplit

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from common.service_tools.web_panel_templates import render_favicon


MAX_REQUEST_BYTES = 4096
MAX_PROVIDER_OUTPUT_BYTES = 128 * 1024
NONCE_TTL_SECONDS = 10 * 60
RATE_WINDOW_SECONDS = 60
RATE_LIMIT = 5
MAX_RATE_SOURCES = 1024
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$"
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
.action-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 15rem), 1fr)); gap: .8rem; }
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
button:focus-visible, a.button:focus-visible, a.text-link:focus-visible, input:focus-visible, summary:focus-visible {
  outline: 3px solid #84adff; outline-offset: 2px; }
.notice { margin: 1rem 0; padding: .85rem 1rem; border: 1px solid #cbd5e1;
  border-radius: .65rem; background: #f8fafc; }
.notice p { margin: .3rem 0 0; }
.notice.error { border-color: #fecdca; background: #fff5f4; color: #912018; }
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


class PairingState:
    def __init__(self, providers: dict[str, dict[str, Any]]) -> None:
        self.providers = providers
        self._lock = threading.Lock()
        self._nonces: dict[str, float] = {}
        self._requests: dict[str, list[float]] = {}

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
        script_nonce: str | None = None,
        form_redirect_origin: str | None = None,
    ) -> None:
        page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
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
            "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; form-action 'self'"
            + (f" {form_redirect_origin}" if form_redirect_origin else "")
            + "; base-uri 'none'"
            + (f"; script-src 'nonce-{script_nonce}'" if script_nonce else ""),
        )
        if nonce is not None:
            secure = (self.headers.get("X-Forwarded-Proto") or "").lower() == "https"
            cookie = f"basaltwater_pairing_nonce={nonce}; Path=/; HttpOnly; SameSite=Strict"
            if secure:
                cookie += "; Secure"
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(encoded)

    def _open_browser(self, provider_name: str, provider: dict[str, Any]) -> None:
        """Submit a same-origin, nonce-protected login after Basic Auth."""
        try:
            origin = _public_base_url(
                self, provider["public_port"], provider.get("https_public_port")
            )
        except PairingError:
            self.send_error(HTTPStatus.BAD_REQUEST, "The requested public origin is invalid")
            return
        nonce = self._page_nonce()
        script_nonce = secrets.token_urlsafe(32)
        label = html.escape(provider["label"])
        body = (
            '<section class="card"><h2>Opening ' + label + '</h2>'
            '<p class="lead">Authorizing this browser and opening your environment.</p>'
            f'<form id="open-browser" method="post" action="/pair/{html.escape(provider_name, quote=True)}">'
            f'<input type="hidden" name="nonce" value="{html.escape(nonce, quote=True)}">'
            '<input type="hidden" name="intent" value="open">'
            '<button type="submit">Open ' + label + '</button></form>'
            '<p class="muted">If the environment does not open automatically, use the button above.</p>'
            '<nav class="footer-nav"><a class="text-link" href="/devices">'
            'Device enrollment and T3 Connect</a></nav></section>'
            f'<script nonce="{script_nonce}">document.getElementById("open-browser").requestSubmit();</script>'
        )
        self._send_html(
            HTTPStatus.OK, "Open " + provider["label"], body,
            nonce=nonce, script_nonce=script_nonce,
            form_redirect_origin=origin,
        )

    def _open_environment(self, pair_url: str) -> None:
        """Navigate with the credential in the fragment, never the request path."""
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", pair_url)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Pragma", "no-cache")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()

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

    def _t3_connect_section(self, provider: dict[str, Any]) -> str:
        """Hand off to native controls without issuing credentials or running a CLI."""
        try:
            origin = _public_base_url(
                self, provider["public_port"], provider.get("https_public_port")
            )
            destination = (
                f'<p><a class="button" href="{html.escape(origin, quote=True)}/" '
                'target="_blank" rel="noopener noreferrer">Open T3 Code ↗</a></p>'
            )
        except PairingError:
            destination = (
                '<p class="muted">The T3 address is unavailable. '
                'Open your existing T3 Code connection.</p>'
            )
        return (
            '<section class="card" aria-labelledby="t3-connect-heading">'
            '<div class="card-heading"><h2 id="t3-connect-heading">T3 Connect</h2>'
            '<p>Use T3 Code for account linking, remote access and device approval.</p></div>'
            + destination
            + '<p>In the desktop app, open <strong>Settings → Sharing &amp; access</strong>. '
            'For this headless server, use its T3 terminal or SSH and follow the native prompts:</p>'
            '<pre><code>t3 connect link --headless\nt3 service restart</code></pre>'
            '<p class="muted">Wait for active agent turns to finish before restarting. '
            'Check the relay with <code>t3 connect status</code>. To disable it, run '
            '<code>t3 connect unlink</code>, then <code>t3 service restart</code>.</p></section>'
        )

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/favicon.svg":
            payload = render_favicon().encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(payload)
            return
        if path == "/" and "t3code" in self.state.providers:
            self._open_browser("t3code", self.state.providers["t3code"])
            return
        if path not in {"/", "/devices"}:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        nonce = self._page_nonce()
        buttons = []
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
        body = (
            '<section class="card"><div class="card-heading">'
            '<p class="eyebrow">Device enrollment</p><h2>Pair a device</h2>'
            '<p class="muted">Create a short-lived, one-time T3 Code link. '
            'Each browser or app should use its own link.</p></div>'
            '<div class="action-grid">'
            + "".join(buttons)
            + "</div><p class=\"muted\">Never share a pairing link. It grants access "
            "to this machine and expires automatically.</p></section>"
            + (
                self._t3_connect_section(self.state.providers["t3code"])
                if "t3code" in self.state.providers else ""
            )
        )
        self._send_html(HTTPStatus.OK, "Device pairing", body, nonce=nonce)

    def do_POST(self) -> None:
        parsed_path = urlsplit(self.path)
        match = re.fullmatch(r"/pair/([a-z0-9-]{1,32})", parsed_path.path)
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
        if (
            not secrets.compare_digest(submitted_nonce, self._nonce_cookie())
            or not self.state.consume_nonce(submitted_nonce)
            or intent not in {"open", "current", "other"}
        ):
            self._send_html(
                HTTPStatus.FORBIDDEN,
                "Pairing request rejected",
                '<section class="card"><div class="notice error">'
                "<strong>This form has expired</strong>"
                "<p>Reload the enrollment page and try again.</p></div>"
                '<nav class="footer-nav"><a class="text-link" href="/devices">'
                "← Back to device pairing</a></nav></section>",
            )
            return
        raw_source = (self.headers.get("X-Real-IP") or "").strip()
        try:
            source = str(ipaddress.ip_address(raw_source))
        except ValueError:
            source = "local"
        if not self.state.allow_request(source):
            self._send_html(
                HTTPStatus.TOO_MANY_REQUESTS,
                "Pairing temporarily limited",
                '<section class="card"><div class="notice error">'
                "<strong>Too many requests</strong>"
                "<p>Wait one minute before requesting another link.</p></div>"
                '<nav class="footer-nav"><a class="text-link" href="/devices">'
                "← Back to device pairing</a></nav></section>",
            )
            return
        provider_name = match.group(1)
        provider = self.state.providers.get(provider_name)
        if provider is None:
            self.send_error(HTTPStatus.NOT_FOUND)
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
                '<nav class="footer-nav"><a class="text-link" href="/devices">'
                "← Back to device pairing</a></nav></section>",
            )
            return
        if intent == "open":
            self._open_environment(pair_url)
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
            '<nav class="footer-nav"><a class="text-link" href="/devices">'
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
