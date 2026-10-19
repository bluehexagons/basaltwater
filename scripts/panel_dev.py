#!/usr/bin/env python3
"""Serve the real panel locally with disposable state and simulated host operations."""

from __future__ import annotations

import argparse
import base64
from contextlib import ExitStack
import hashlib
import html
from http import HTTPStatus
from http.server import ThreadingHTTPServer
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch
import uuid
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from common.service_tools import web_panel_service as panel
from lib.atomic_io import read_json_file, write_json_atomic
from lib.validation import validate_filesystem_path, validate_network_port
from scripts.panel_scenarios import SCENARIOS, Scenario

SURFACES = ("panel", "pairing", "connect", "approval", "desktop", "console")
ENTRY_PATHS = {"panel": "/", "pairing": "/devices", "connect": "/connect/t3code",
               "approval": "/", "desktop": "/desktop/", "console": "/console/"}

RELOAD_SCRIPT = """'use strict';
const toolbar = document.getElementById('panel-development');
let dirty = false;
document.addEventListener('input', event => { if (!toolbar.contains(event.target)) dirty = true; });
setInterval(async () => {
  try {
    const response = await fetch('/__dev__/status', {cache: 'no-store'});
    if (!response.ok) return;
    const status = await response.json();
    if (status.generation !== toolbar.dataset.generation) {
      if (!dirty && !document.querySelector('#screen canvas')) location.replace(location.href);
      else document.getElementById('panel-reload').hidden = false;
    }
  } catch (_) { /* The worker is restarting; try again next interval. */ }
}, 1000);
"""


def development_html(body: str, settings: dict, generation: str, csrf: str) -> str:
    """Add the same controls to every production surface in development only."""
    import re

    options = "".join(f'<option{" selected" if name == settings["scenario"] else ""}>{name}</option>' for name in SCENARIOS)
    button_style = "flex:1 1 10rem;width:auto;min-width:0;min-height:44px;margin:0;padding:10px 14px;white-space:normal;line-height:1.4"
    toolbar = f'''<aside id="panel-development" data-generation="{generation}" style="display:grid;gap:14px;padding:16px;border:1px solid var(--line,#667085);border-radius:12px;margin-bottom:20px;overflow-wrap:anywhere">
<strong>Local development · simulated operations</strong>
<form method="post" action="/__dev__/control" style="display:flex;flex-wrap:wrap;align-items:end;gap:12px;margin:0"><input type="hidden" name="csrf" value="{html.escape(csrf, quote=True)}">
<label style="display:grid;gap:6px;flex:1 1 14rem;min-width:0;margin:0">Scenario <select name="scenario" style="width:100%;min-width:0;min-height:44px;margin:0">{options}</select></label>
<div style="display:flex;flex-wrap:wrap;gap:8px;flex:2 1 22rem;min-width:0">
<button name="action" value="scenario" style="{button_style}">Switch scenario</button><button name="action" value="reset" style="{button_style}">Reset sample data</button></div></form>
<a id="panel-reload" href="" hidden>Changes ready — reload when finished editing</a>
<p style="margin:0;font-size:.85rem;line-height:1.5">Temporary data only. Use sample credentials. Provider, approval and desktop actions are simulated.</p></aside>'''
    body = re.sub(r"<main\b[^>]*>", lambda match: match[0] + toolbar, body, count=1)
    if settings["refresh"]:
        script = '<script src="/__dev__/reload.js" defer></script>'
        body = body.replace("</body>", script + "</body>") if "</body>" in body else body.replace("</html>", script + "</html>")
    return body


def development_control(body: bytes, csrf: str, settings: dict, directory: Path) -> None:
    """Validate development controls independently of each service's real forms."""
    if not 1 <= len(body) <= 1024:
        raise ValueError("Invalid control size")
    values = parse_qs(body.decode(), keep_blank_values=True, max_num_fields=3)
    if set(values) != {"csrf", "scenario", "action"} or any(len(value) != 1 for value in values.values()):
        raise ValueError("Invalid controls")
    if not secrets.compare_digest(values["csrf"][0].encode(), csrf.encode()):
        raise PermissionError("Invalid CSRF token")
    if values["scenario"][0] not in SCENARIOS or values["action"][0] not in {"scenario", "reset"}:
        raise ValueError("Unknown development control")
    write_json_atomic(str(directory / "request.json"), {
        "scenario": values["scenario"][0] if values["action"] == ["scenario"] else settings["scenario"]})


def source_snapshot(extra: list[Path] | None = None) -> dict[str, tuple[int, int]]:
    """Watch panel code and its libraries, excluding state and generated exports."""
    paths = [path for directory in ("common/service_tools", "lib", "scripts", "desktop/config")
             for path in (ROOT / directory).rglob("*") if path.suffix in {".py", ".js", ".css", ".html", ".template"}]
    for path in extra or []:
        paths.extend(path.rglob("*") if path.is_dir() else [path])
    result = {}
    for path in paths:
        try:
            info = path.stat()
            if not path.is_file():
                continue
            result[str(path)] = (info.st_mtime_ns, info.st_size)
        except FileNotFoundError:
            pass
    return result


def private_directory(directory: Path) -> None:
    validate_filesystem_path(str(directory), must_exist=True)
    if any(path.is_symlink() for path in (directory, *directory.parents)):
        raise ValueError("Development state must not traverse symlinks")
    info = directory.stat()
    if not directory.is_dir() or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError("Development state must be a private directory owned by this account")


def development_handler(state: panel.WebPanelState, settings: dict, generation: str, directory: Path):
    """Build a development-only handler without altering the production class."""
    authorization = "Basic " + base64.b64encode(f"{settings['username']}:{settings['password']}".encode()).decode()

    class Handler(panel.WebPanelHandler):
        def log_message(self, *args) -> None:
            pass

        def authenticated(self) -> bool:
            # Reject browser DNS rebinding and forged proxy identity/transport.
            if self.headers.get("Host") not in {urlsplit(settings["url"]).netloc, "localhost:" + settings["url"].rsplit(":", 1)[1]}:
                self._send(HTTPStatus.FORBIDDEN, "Unexpected development host\n", "text/plain")
                return False
            if not secrets.compare_digest(self.headers.get("Authorization", "").encode(), authorization.encode()):
                self._send(HTTPStatus.UNAUTHORIZED, "Disposable panel login required\n", "text/plain",
                           headers={"WWW-Authenticate": 'Basic realm="Basaltwater panel development"'})
                return False
            for header in ("X-Forwarded-Proto", "X-Forwarded-Host", "X-Real-IP", "X-Basaltwater-User"):
                while header in self.headers:
                    del self.headers[header]
            self.headers["X-Forwarded-Proto"] = "https" if settings["https"] else "http"
            self.headers["X-Basaltwater-User"] = state.manifest["username"]
            return True

        def _send(self, status, body, content_type, *, headers=None) -> None:
            if content_type == "text/html":
                body = development_html(body, settings, generation, state.csrf_token)
            super()._send(status, body, content_type, headers=headers)

        def send_header(self, keyword, value) -> None:
            if keyword.lower() == "content-security-policy":
                value = value.replace("script-src ", "connect-src 'self'; script-src 'self' ")
            super().send_header(keyword, value)

        def do_GET(self) -> None:
            if not self.authenticated():
                return
            if self.path == "/__dev__/status":
                self._send_json(HTTPStatus.OK, {"generation": generation, "scenario": settings["scenario"]})
            elif self.path == "/__dev__/reload.js":
                self._send(HTTPStatus.OK, RELOAD_SCRIPT, "application/javascript")
            elif self.path == "/__dev__/project":
                self._send(HTTPStatus.OK, "<!doctype html><title>Synthetic project</title><h1>Synthetic project</h1>", "text/html")
            else:
                super().do_GET()

        def do_POST(self) -> None:
            if not self.authenticated():
                return
            if self.path != "/__dev__/control":
                super().do_POST()
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 1 <= length <= 1024:
                    raise ValueError("Invalid control size")
                development_control(self.rfile.read(length), state.csrf_token, settings, directory)
            except PermissionError:
                self._send(HTTPStatus.FORBIDDEN, "Invalid CSRF token\n", "text/plain")
                return
            except (ValueError, UnicodeError):
                self._send(HTTPStatus.BAD_REQUEST, "Invalid development controls\n", "text/plain")
                return
            self._send(HTTPStatus.SEE_OTHER, "", "text/plain", headers={"Location": "/"})

    Handler.state = state
    return Handler


def worker(directory: Path, descriptor: int) -> int:
    """Run in a fresh interpreter so reloads cannot retain stale imported code."""
    private_directory(directory)
    settings = read_json_file(str(directory / "settings.json"))
    if settings["scenario"] not in SCENARIOS:
        raise ValueError("Unknown panel scenario")
    home = directory / "home"
    home.mkdir(mode=0o700, exist_ok=True)
    private_directory(home)
    done = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: done.set())
    with ExitStack() as stack:
        # No subprocess from any handler or scheduler can reach the host.
        stack.enter_context(patch.object(subprocess, "run", side_effect=RuntimeError("Host commands disabled in panel development")))
        stack.enter_context(patch.object(subprocess, "Popen", side_effect=RuntimeError("Processes disabled in panel development")))
        if settings["surface"] != "panel":
            from scripts.panel_specialized import serve

            return serve(directory, descriptor, settings, done, stack)
        state = panel.WebPanelState({
            "host": "development.example.test", "username": "operator", "title": "Development machine",
            "system_type": "server_dev", "features": {}, "services": [], "access": [],
            "panel_url": settings["url"] + "/",
        }, agent_home=str(home), audit_snapshot_path=str(home / "audit.json"),
            notification_log_path=str(home / "notifications.json"), ingest_token_path=str(home / "ingest-token"))
        Scenario(state, settings["scenario"], stack)
        if settings["scenario"] not in {"minimal", "empty"}:
            state.manifest["services"] = [{"label": "Synthetic project", "url": settings["url"] + "/__dev__/project",
                                            "description": "Disposable local destination"}]
        generation = uuid.uuid4().hex

        handler = development_handler(state, settings, generation, directory)
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler, bind_and_activate=False)
        server.socket.close()
        server.socket = socket.socket(fileno=descriptor)
        server.server_address = server.socket.getsockname()
        server.daemon_threads = True
        if settings["https"]:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(directory / "certificates/server.crt", directory / "certificates/server.key")
            server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        try:
            # Only the synthetic prompt executor runs; publishing never starts.
            if state.features().prompts:
                state.agent_tasks.start()
            thread.start()
            write_json_atomic(str(directory / "ready.json"), {"generation": generation})
            done.wait()
        finally:
            state.agent_tasks.close()
            if thread.ident is not None:
                server.shutdown()
                thread.join(5)
            server.server_close()
    return 0


def stop_worker(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def supervise(args: argparse.Namespace, directory: Path) -> int:
    done = threading.Event()
    previous_handlers = {}
    for signum in (signal.SIGTERM, signal.SIGINT):
        previous_handlers[signum] = signal.signal(signum, lambda *_: done.set())
    try:
        with socket.create_server(("127.0.0.1", args.port)) as listener:
            port = listener.getsockname()[1]
            url = f"{'https' if args.https else 'http'}://127.0.0.1:{port}"
            settings = {"url": url, "scenario": args.scenario, "https": args.https,
                        "surface": args.surface, "entry": ENTRY_PATHS[args.surface],
                        "guacamole_assets": str(args.guacamole_assets) if args.guacamole_assets else None,
                        "refresh": not args.no_refresh, "username": "developer", "password": secrets.token_urlsafe(24)}
            if args.https:
                # The same CA and leaf survive every worker restart in this session.
                from tests.browser.fixtures import create_certificates

                certificates = directory / "certificates"
                certificates.mkdir(mode=0o700)
                created = create_certificates(certificates)
                fingerprint = hashlib.sha256(created["ca"].read_bytes()).hexdigest()
                print(f"Private-profile CA: {created['ca']}\nSHA-256: {fingerprint}", flush=True)
            write_json_atomic(str(directory / "settings.json"), settings)
            print(f"Panel: {url}{settings['entry']}\nLogin: {settings['username']} / {settings['password']}\nTemporary state: {directory}\n"
                  "Ctrl-C stops the server and removes this session's data.", flush=True)
            snapshot = source_snapshot(args.watch)
            first = True
            while not done.is_set():
                ready = directory / "ready.json"
                ready.unlink(missing_ok=True)
                process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--worker", str(directory),
                                            "--socket-fd", str(listener.fileno())], cwd=ROOT, pass_fds=(listener.fileno(),))
                try:
                    deadline = time.monotonic() + 30
                    while not ready.exists() and process.poll() is None and time.monotonic() < deadline and not done.wait(0.1):
                        pass
                    if not ready.exists():
                        if done.is_set():
                            break
                        print("Development worker failed to start; inspect the error above.", file=sys.stderr, flush=True)
                        if first or args.no_reload:
                            return 1
                    else:
                        print(f"Ready · {settings['scenario']} · reload {'off' if args.no_reload else 'on'}", flush=True)
                        if first and args.open:
                            webbrowser.open(url + settings["entry"])
                    first = False
                    while not done.wait(0.4):
                        request = directory / "request.json"
                        if request.exists():
                            requested = read_json_file(str(request))
                            request.unlink()
                            if requested.get("scenario") not in SCENARIOS:
                                raise ValueError("Invalid requested scenario")
                            stop_worker(process)
                            shutil.rmtree(directory / "home", ignore_errors=False)
                            settings["scenario"] = requested["scenario"]
                            write_json_atomic(str(directory / "settings.json"), settings)
                            print("Reset sample data", flush=True)
                            break
                        if not args.no_reload:
                            updated = source_snapshot(args.watch)
                            if updated != snapshot:
                                snapshot = updated
                                print("Source changed; reloading", flush=True)
                                break
                        if process.poll() is not None and ready.exists():
                            print("Development worker exited unexpectedly", file=sys.stderr)
                            return 1
                finally:
                    stop_worker(process)
            return 0
    finally:
        for signum, previous in previous_handlers.items():
            signal.signal(signum, previous)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--surface", choices=SURFACES, default="panel", help="Production panel to develop (default: panel)")
    parser.add_argument("--scenario", choices=SCENARIOS, default="full")
    parser.add_argument("--port", type=int, default=8765, help="Loopback port; 0 selects a free port (default: 8765)")
    parser.add_argument("--https", action="store_true", help="Use a session CA for transport-sensitive forms")
    parser.add_argument("--open", action="store_true", help="Open the URL in your default browser")
    parser.add_argument("--no-reload", action="store_true", help="Disable source watching")
    parser.add_argument("--no-refresh", action="store_true", help="Disable automatic browser refresh")
    parser.add_argument("--watch", type=Path, action="append", default=[], help="Also watch this file or directory (repeatable)")
    parser.add_argument("--guacamole-assets", type=Path, help="Reviewed Guacamole share/all.js for desktop/console")
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--socket-fd", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if os.geteuid() == 0:
        parser.error("Run panel development as a regular development account")
    try:
        validate_network_port(args.port, minimum=0)
        for path in args.watch:
            validate_filesystem_path(str(path.absolute()), must_exist=True)
        if args.worker:
            if args.socket_fd is None:
                parser.error("A worker requires an inherited listener")
            return worker(args.worker, args.socket_fd)
        if args.socket_fd is not None:
            parser.error("An inherited listener is only valid for a worker")
        if args.surface in {"desktop", "console"}:
            from scripts.run_browser_tests import GUACAMOLE_JS_SHA256
            from lib.guacamole_config import RELEASE

            args.guacamole_assets = (args.guacamole_assets or Path(RELEASE) / "share/all.js").absolute()
            validate_filesystem_path(str(args.guacamole_assets), must_exist=True)
            if hashlib.sha256(args.guacamole_assets.read_bytes()).hexdigest() != GUACAMOLE_JS_SHA256:
                raise ValueError("Guacamole all.js does not match the reviewed pinned client")
            args.https = True  # Keep the production gateway's HTTPS/origin checks.
        with tempfile.TemporaryDirectory(prefix="basaltwater-panel-dev-") as temporary:
            return supervise(args, Path(temporary))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Panel development failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
