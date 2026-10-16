"""Disposable adapters and server for pairing, Connect, approvals and Guacamole."""

from __future__ import annotations

import asyncio
import base64
from collections import deque
from contextlib import ExitStack
import copy
from http.server import HTTPServer, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import socket
import ssl
import subprocess
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit
import uuid

from aiohttp import ClientSession, web

from common.service_tools import device_pairing_service as pairing
from common.service_tools import privilege_approval as approval
from lib.atomic_io import read_json_file, write_json_atomic
from lib.privilege_auth import password_record
from scripts.panel_dev import RELOAD_SCRIPT, development_control, development_html


class SpecializedScenario:
    """Keep synthetic decisions and relay state across source reloads only."""

    def __init__(self, home: Path, settings: dict, stack: ExitStack) -> None:
        self.path = home / "specialized.json"
        self.lock = threading.RLock()
        self.name = settings["scenario"]
        self.url = settings["url"]
        records = {}
        if self.name != "empty":
            for digit, state in zip("abcd", ("pending", "approved", "denied", "expired")):
                if self.name == "minimal" and digit != "a":
                    continue
                records[digit * 32] = {
                    "id": digit * 32, "digest": digit * 64, "state": state,
                    "created": time.time(), "expires": time.time() + (3600 if state != "expired" else -1),
                    "reason": "Synthetic development request <untrusted>",
                    "plan": {"machine": "c" * 32, "uid": 1000, "operation": "development-only",
                             "argv": ["synthetic-no-executor"], "parameters": {}, "effect": "Record a sample decision only"},
                }
        self.data = read_json_file(str(self.path)) if self.path.exists() else {
            "records": records, "desired": self.name == "full", "linked": self.name == "full",
            "active": False, "output": "", "returncode": None, "error": None,
        }
        self.save()
        stack.enter_context(patch.object(subprocess, "run", side_effect=self.command))
        scenario = self

        class ConnectJob(pairing.ConnectJob):
            def __init__(self, config):
                super().__init__(config)
                self.restore()

            def restore(self):
                self._process = SimpleNamespace(stdin=True) if scenario.data["active"] else None
                self._output = scenario.data["output"]
                self._returncode = scenario.data["returncode"]
                self._error = scenario.data["error"]

            def start(self):
                with scenario.lock:
                    if scenario.data["active"]:
                        raise pairing.PairingError("A T3 Connect operation is already running")
                    failed = scenario.name == "degraded"
                    scenario.data.update(active=not failed, desired=True, linked=False,
                        output="Synthetic authorization failed" if failed else
                               "Open " + scenario.url + "/__dev__/authorize and enter any sample response.",
                        returncode=1 if failed else None, error="Synthetic relay unavailable" if failed else None)
                    scenario.save()
                    self.restore()

            def _write_input(self, process, content):
                # The production send_input validates size/control characters.
                # Discard the submitted response; no credential is persisted.
                with scenario.lock:
                    scenario.data.update(active=False, desired=True, linked=True,
                        output="Synthetic authorization completed; no provider was contacted.", returncode=0, error=None)
                    scenario.save()
                    self.restore()

        stack.enter_context(patch.object(pairing, "ConnectJob", ConnectJob))

    def save(self) -> None:
        write_json_atomic(str(self.path), self.data)

    def providers(self) -> dict:
        port = urlsplit(self.url).port
        return {"t3code": {"label": "T3 Code", "command": ["development-pair"], "base_url_flag": "--base-url",
            "url_field": "url", "expires_field": "expires", "public_port": port, "https_public_port": port,
            "connect": {"link_command": ["development-link"], "unlink_command": ["development-unlink"],
                        "status_command": ["development-status"], "restart_request": str(self.path.parent / "restart-request")}}}

    def command(self, command, **kwargs):
        with self.lock:
            if command == ["development-status"]:
                result = {"desired": self.data["desired"], "authenticated": self.data["linked"],
                          "linked": self.data["linked"], "provisioned": self.data["linked"],
                          "relayUrl": None, "publishEnabled": False}
            elif command == ["development-unlink"]:
                if self.name == "degraded":
                    return subprocess.CompletedProcess(command, 1, "", "")
                self.data.update(desired=False, linked=False)
                self.save()
                result = {}
            elif (len(command) == 3 and command[:2] == ["development-pair", "--base-url"]
                  and command[2] in {self.url, self.url.replace("127.0.0.1", "localhost")}):
                if self.name == "degraded":
                    return subprocess.CompletedProcess(command, 1, "", "")
                result = {"url": command[2] + "/pair#token=synthetic-only", "expires": "in five minutes (simulated)"}
            else:
                raise RuntimeError("Unexpected host command in specialized panel development")
            return subprocess.CompletedProcess(command, 0, json.dumps(result), "")

    def exchange(self, message: dict) -> dict:
        with self.lock:
            if self.name == "degraded":
                raise OSError("Synthetic broker unavailable")
            for row in self.data["records"].values():
                if row["state"] == "pending" and row["expires"] <= time.time():
                    row["state"] = "expired"
            if message["action"] == "list":
                return {"requests": [{"id": row["id"], "operation": row["plan"]["operation"],
                                      "state": row["state"], "uid": 1000} for row in self.data["records"].values()]}
            row = self.data["records"].get(message["id"])
            if row is None:
                raise ValueError("Unknown synthetic request")
            if message["action"] == "decide":
                if row["state"] != "pending" or message["digest"] != row["digest"]:
                    raise ValueError("Synthetic request expired or already decided")
                row["state"] = "approved" if message["approve"] else "denied"
                self.save()
            return copy.deepcopy(row)


class SyntheticDesktop:
    """Protocol display only: never connects to guacd, XRDP, SSH or a desktop."""

    def __init__(self, scenario: str):
        self.scenario = scenario
        self.state = {"state": "stopped" if scenario == "empty" else "running", "generation": "development-display",
                      "geometry": [640, 480], "paused": False, "browser_pause": None}

    async def call(self, action, **kwargs):
        if self.scenario == "degraded":
            raise OSError("Synthetic desktop unavailable")
        if action == "browser-take":
            self.state.update(paused=True, browser_pause="f" * 32)
        elif action in {"resume", "browser-release"}:
            self.state.update(paused=False, browser_pause=None)
        elif action == "start":
            self.state["state"] = "running"
        elif action == "browser-logout":
            self.state.update(state="stopped", logout_requested=True)
        elif action not in {"status", "browser-refresh"}:
            raise RuntimeError("Unexpected synthetic desktop action")
        return dict(self.state)

    async def login(self, session):
        from common.service_tools.guacamole_protocol import instruction

        if self.scenario == "degraded":
            session.password = ""
            raise OSError("Synthetic login unavailable")

        class Writer:
            def write(self, data):
                pass
            async def drain(self):
                pass
            def close(self):
                pass
            def is_closing(self):
                return False
            async def wait_closed(self):
                pass

        reader, writer = asyncio.StreamReader(), Writer()
        session.upstream, session.password = writer, ""
        reader.feed_data("".join(instruction(*row) for row in (
            ("size", "0", "640", "480"), ("rect", "0", "0", "0", "640", "480"),
            ("cfill", "14", "0", "64", "128", "192", "255"), ("sync", "1"))).encode())
        return reader, writer, "development-session"


def http_backend(handler, stack: ExitStack, *, sequential: bool = False):
    server = (HTTPServer if sequential else ThreadingHTTPServer)(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    thread.start()
    stack.callback(server.server_close)
    stack.callback(thread.join, 5)
    stack.callback(server.shutdown)
    return server


def create_app(directory: Path, settings: dict, stack: ExitStack) -> web.Application:
    """Wrap production handlers with authenticated development controls only."""
    from common.service_tools import guacamole_console as console
    from common.service_tools import guacamole_service as guacamole
    from common.service_tools.web_panel_templates import panel_navigation, render_favicon

    generation, csrf = uuid.uuid4().hex, secrets.token_urlsafe(32)
    authorization = "Basic " + base64.b64encode(f"{settings['username']}:{settings['password']}".encode()).decode()
    surface = settings["surface"]
    scenario = SpecializedScenario(directory / "home", settings, stack)
    backend = None
    if surface in {"desktop", "console"}:
        desktop = SyntheticDesktop(settings["scenario"])
        stack.enter_context(patch.object(guacamole, "desktop_call", side_effect=desktop.call))
        stack.enter_context(patch.object(guacamole.Gateway, "login", new=desktop.login))
        stack.enter_context(patch.object(console.ConsoleGateway, "login", new=desktop.login))
        stack.enter_context(patch.object(guacamole.Gateway, "navigation", side_effect=lambda current:
                                        panel_navigation(current=current, available={"desktop", "console"})))
        app = guacamole.create_app({"username": settings["username"], "origins": [settings["url"]],
                                    "certificate": "d" * 64, "ssh_host_key": "synthetic-no-ssh"})
        async def favicon(request):
            return web.Response(text=render_favicon(), content_type="image/svg+xml")
        app.router.add_get("/favicon.svg", favicon)
    else:
        app = web.Application(client_max_size=8192)
        if surface in {"pairing", "connect"}:
            backend = http_backend(pairing.PairingRequestHandler, stack)
            backend.pairing_state = pairing.PairingState(scenario.providers())
        else:
            backend = http_backend(approval.ApprovalHandler, stack, sequential=True)
            backend.auth, backend.origin = password_record(settings["username"], settings["password"]), settings["url"]
            backend.secret, backend.failures, backend.exchange = secrets.token_bytes(32), deque(maxlen=20), scenario.exchange

        async def proxy(request):
            async with app[CLIENT].request(request.method, f"http://127.0.0.1:{backend.server_port}{request.rel_url}",
                    headers=request.headers, data=await request.read(), allow_redirects=False) as response:
                headers = [(key, value) for key, value in response.headers.items()
                           if key.lower() not in {"connection", "transfer-encoding", "content-length", "server", "date"}]
                return web.Response(body=await response.read(), status=response.status, headers=headers)
        app.router.add_route("*", "/{path:.*}", proxy)

    @web.middleware
    async def development(request, handler):
        host = urlsplit(settings["url"]).netloc
        allowed = {host, "localhost:" + host.rsplit(":", 1)[1]} if surface in {"pairing", "connect"} else {host}
        if len(request.headers.getall("Host", [])) != 1 or request.host not in allowed:
            return web.Response(status=403, text="Unexpected development host")
        if (len(request.headers.getall("Authorization", [])) != 1 or
                not secrets.compare_digest(request.headers.get("Authorization", "").encode(), authorization.encode())):
            return web.Response(status=401, text="Disposable panel login required",
                                headers={"WWW-Authenticate": 'Basic realm="Basaltwater panel development"'})
        headers = {key: value for key, value in request.headers.items() if not key.lower().startswith("x-forwarded-")
                   and key.lower() not in {"x-real-ip", "x-guacamole-user"}}
        headers.update({"X-Forwarded-Proto": "https" if settings["https"] else "http",
                        "X-Forwarded-Host": request.host, "X-Real-IP": "127.0.0.1", "X-Guacamole-User": settings["username"]})
        request = request.clone(headers=headers)
        if request.path == "/__dev__/status" and request.method == "GET":
            return web.json_response({"generation": generation, "scenario": settings["scenario"], "surface": surface})
        if request.path == "/__dev__/reload.js" and request.method == "GET":
            return web.Response(text=RELOAD_SCRIPT, content_type="application/javascript")
        if request.path == "/__dev__/control" and request.method == "POST":
            try:
                development_control(await request.read(), csrf, settings, directory)
            except PermissionError:
                return web.Response(status=403, text="Invalid CSRF token")
            except (ValueError, UnicodeError):
                return web.Response(status=400, text="Invalid development controls")
            return web.Response(status=303, headers={"Location": settings["entry"]})
        if request.path == "/" and settings["entry"] != "/" and surface in {"desktop", "console"}:
            return web.Response(status=302, headers={"Location": settings["entry"]})
        if request.path in {"/pair", "/__dev__/authorize"} and request.method == "GET":
            response = web.Response(text="<!doctype html><html><body><main><h1>Synthetic destination</h1>"
                '<p>No device was enrolled. Return to <a href="/devices">device pairing</a> or '
                '<a href="/connect/t3code">Connect</a> and enter any sample response.</p></main></body></html>', content_type="text/html")
        elif surface in {"desktop", "console"} and request.path in {"/desktop/assets/all.js", "/console/assets/all.js"}:
            response = web.Response(body=Path(settings["guacamole_assets"]).read_bytes(), content_type="application/javascript")
        else:
            response = await handler(request)
        if not response.prepared:
            response.headers["Cache-Control"] = "no-store"
            if response.content_type == "text/html":
                response.text = development_html(response.text, settings, generation, csrf)
                policy = response.headers.get("Content-Security-Policy", "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; form-action 'self'")
                directives = [value.strip() for value in policy.split(";") if value.strip()]
                if not any(value.startswith("script-src ") for value in directives):
                    directives.append("script-src 'self'")
                else:
                    directives = [value.replace("script-src ", "script-src 'self' ", 1) if value.startswith("script-src ") else value for value in directives]
                if not any(value.startswith("connect-src ") for value in directives):
                    directives.append("connect-src 'self'")
                response.headers["Content-Security-Policy"] = "; ".join(directives)
        return response

    app.middlewares.insert(0, development)

    async def client_context(application):
        async with ClientSession() as client:
            application[CLIENT] = client
            yield
    app.cleanup_ctx.append(client_context)
    app[GENERATION] = generation
    return app


CLIENT = web.AppKey("development-client", ClientSession)
GENERATION = web.AppKey("development-generation", str)


def serve(directory: Path, descriptor: int, settings: dict, done: threading.Event, stack: ExitStack) -> int:
    async def run():
        app = create_app(directory, settings, stack)
        runner = web.AppRunner(app, access_log=None, shutdown_timeout=2)
        try:
            await runner.setup()
            context = None
            if settings["https"]:
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                context.minimum_version = ssl.TLSVersion.TLSv1_2
                context.load_cert_chain(directory / "certificates/server.crt", directory / "certificates/server.key")
            listener = socket.socket(fileno=descriptor)
            listener.setblocking(False)
            await web.SockSite(runner, listener, ssl_context=context).start()
            write_json_atomic(str(directory / "ready.json"), {"generation": app[GENERATION]})
            while not done.is_set():
                await asyncio.sleep(0.1)
        finally:
            await runner.cleanup()
    asyncio.run(run())
    return 0
