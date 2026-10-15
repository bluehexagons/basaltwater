"""Disposable first-party services, TLS and Guacamole protocol fixtures.

Only loopback listeners and temporary state are used. All setup, provider,
broker and desktop operations are replaced before requests can be accepted.
"""

from __future__ import annotations

import asyncio
import base64
from contextlib import ExitStack
import copy
import hashlib
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import signal
import ssl
import subprocess
import threading
import time
from unittest.mock import patch

from aiohttp import ClientSession, web

from common.service_tools import device_pairing_service as pairing
from common.service_tools import guacamole_service as guacamole
from common.service_tools import web_panel_service as panel
from common.service_tools.privilege_approval import ApprovalServer
from common.service_tools.web_panel_features import PanelFeatures
from lib.privilege_auth import password_record


def create_certificates(directory: Path) -> dict[str, Path]:
    """Create a fixture-only CA and leaf, including a deliberately wrong name."""
    ca, key = directory / "ca.crt", directory / "ca.key"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-subj", "/CN=Basaltwater disposable browser fixture", "-addext", "basicConstraints=critical,CA:TRUE",
                    "-keyout", str(key), "-out", str(ca)], check=True, capture_output=True, timeout=30)
    result = {"ca": ca}
    for name, san in (("server", "DNS:localhost,IP:127.0.0.1"), ("invalid", "DNS:wrong.example.test")):
        leaf_key, csr, cert, extensions = [directory / f"{name}.{suffix}" for suffix in ("key", "csr", "crt", "ext")]
        extensions.write_text(f"subjectAltName={san}\nbasicConstraints=critical,CA:FALSE\nextendedKeyUsage=serverAuth\n")
        subprocess.run(["openssl", "req", "-new", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=localhost",
                        "-keyout", str(leaf_key), "-out", str(csr)], check=True, capture_output=True, timeout=30)
        subprocess.run(["openssl", "x509", "-req", "-in", str(csr), "-CA", str(ca), "-CAkey", str(key),
                        "-CAcreateserial", "-days", "1", "-extfile", str(extensions), "-out", str(cert)],
                       check=True, capture_output=True, timeout=30)
        result[name] = cert
        result[name + "_key"] = leaf_key
    return result


class SyntheticBroker:
    """No command executor, policy file, socket, or real request is reachable."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.records = {}
        for digit in ("a", "b"):
            identifier = digit * 32
            self.records[identifier] = {
                "id": identifier, "digest": digit * 64, "created": time.time(), "expires": time.time() + 3600,
                "state": "pending", "reason": "Synthetic browser test <untrusted>",
                "plan": {"machine": "c" * 32, "uid": 1000, "operation": "fixture-only",
                         "argv": ["fixture-no-executor"], "parameters": {}, "effect": "Record a synthetic decision only"},
            }

    def __call__(self, message: dict) -> dict:
        with self.lock:
            if message["action"] == "list":
                return {"requests": [{"id": row["id"], "operation": "fixture-only", "state": row["state"], "uid": 1000}
                                     for row in self.records.values()]}
            row = self.records[message["id"]]
            if message["action"] == "decide":
                if row["state"] != "pending" or message["digest"] != row["digest"]:
                    raise ValueError("Synthetic request expired or already decided")
                row["state"] = "approved" if message["approve"] else "denied"
            return copy.deepcopy(row)


class FixtureEnvironment:
    """Run production handlers behind a tiny synthetic authenticated TLS proxy."""

    def __init__(self, directory: Path, *, guacamole_assets: Path | None = None) -> None:
        self.directory = directory
        self.guacamole_assets = guacamole_assets
        self.stack = ExitStack()
        self.username, self.password = "browser-fixture", secrets.token_urlsafe(24)
        self.authorization = "Basic " + base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
        self.broker = SyntheticBroker()
        self.urls: dict[str, str] = {}
        self.updates = 0
        self.issues = 0
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.runners = []
        self.desktop = {"state": "running", "generation": "fixture-generation", "geometry": [640, 480],
                        "paused": False, "browser_pause": None}

    def __enter__(self) -> FixtureEnvironment:
        try:
            self.certificates = create_certificates(self.directory)
            # Fail closed for every unexpected external command from a handler.
            self.stack.enter_context(patch.object(subprocess, "run", side_effect=self.provider_command))
            self.stack.enter_context(patch.object(subprocess, "Popen", side_effect=AssertionError("No processes in browser fixtures")))
            self.stack.enter_context(patch.object(panel, "discover_certificate_trust", side_effect=self.certificate_trust))
            self.stack.enter_context(patch.object(panel, "discover_basaltwater_web_services", return_value=[]))
            self.stack.enter_context(patch.object(guacamole, "desktop_call", side_effect=self.desktop_call))
            self.stack.enter_context(patch.object(guacamole.Gateway, "navigation", return_value=panel.panel_navigation(current="desktop")))
            self.stack.enter_context(patch.object(guacamole.Gateway, "login", new=self.protocol_login))
            self.thread.start()
            self.stack.callback(self.stop_loop)
            self.client = asyncio.run_coroutine_threadsafe(self.create_client(), self.loop).result(10)
            self.backends = {"panel": self.http_server(panel.WebPanelHandler),
                             "pairing": self.http_server(pairing.PairingRequestHandler)}
            state = self.panel_state()
            self.backends["panel"].RequestHandlerClass.state = state
            providers = {"t3code": {"label": "T3 Code", "command": ["fixture-pair"], "base_url_flag": "--base-url",
                                    "url_field": "url", "expires_field": "expires", "public_port": 1, "https_public_port": 1,
                                    "connect": {"link_command": ["fixture-disabled"], "unlink_command": ["fixture-disabled"],
                                                "status_command": ["fixture-connect-status"],
                                                "restart_request": str(self.directory / "unused-restart")}}}
            self.backends["pairing"].pairing_state = pairing.PairingState(providers)
            self.backends["approval"] = ApprovalServer(("127.0.0.1", 0), password_record(self.username, self.password),
                                                        "https://localhost", self.broker)
            self.start_http(self.backends["approval"])
            asyncio.run_coroutine_threadsafe(self.start_frontends(), self.loop).result(15)
            self.backends["approval"].origin = self.urls["approval"]
            port = int(self.urls["pairing"].rsplit(":", 1)[1])
            providers["t3code"].update(public_port=port, https_public_port=port)
            state.manifest["services"] = [{"label": "Disposable project", "url": self.urls["panel"] + "/project/", "description": "Synthetic web surface"}]
            return self
        except BaseException:
            self.stack.close()
            raise

    def __exit__(self, *args) -> None:
        self.stack.close()

    def start_http(self, server) -> None:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.stack.callback(server.server_close)
        self.stack.callback(thread.join, 5)
        self.stack.callback(server.shutdown)

    def http_server(self, handler):
        class QuietHandler(handler):
            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        server.daemon_threads = True
        self.start_http(server)
        return server

    def panel_state(self):
        # A real WebPanelState owns only temp files; its host observations and
        # mutation entrypoints are fixed before its first request.
        home = self.directory / "home"
        home.mkdir()
        state = panel.WebPanelState({"host": "fixture-host", "username": self.username, "system_type": "server_dev",
                                     "title": "Browser fixture", "features": {"t3_update": True, "notification_ingest": False},
                                     "services": [], "access": []}, agent_home=str(home))
        self.stack.enter_context(patch.object(state, "features", return_value=PanelFeatures()))
        self.stack.enter_context(patch.object(state, "system_overview", return_value=[
            {"label": label, "value": value, "description": "Synthetic host data"}
            for label, value in (("Memory", "42% used"), ("Swap", "0% used"), ("CPU load", "0.20"),
                                 ("Root disk", "25% used"), ("Root inodes", "12% used"), ("Uptime", "1 day"))]))
        self.stack.enter_context(patch.object(state, "audit_snapshot", return_value={"status": "ok", "events": []}))
        self.stack.enter_context(patch.object(state, "service_health", return_value=[
            {"label": "Synthetic service", "value": "active", "description": "Fixture only"}]))
        self.stack.enter_context(patch.object(state, "t3_update_available", return_value=True))
        self.stack.enter_context(patch.object(state, "trigger_t3_update", side_effect=self.synthetic_update))
        return state

    def synthetic_update(self) -> bool:
        self.updates += 1
        return True

    def provider_command(self, command, **kwargs):
        if command == ["fixture-connect-status"]:
            return subprocess.CompletedProcess(command, 0, json.dumps({"desired": True, "authenticated": True,
                "linked": True, "provisioned": True, "relayUrl": None, "publishEnabled": False}), "")
        if command[0] != "fixture-pair":
            raise AssertionError(f"Unexpected command in browser fixture: {command[0]}")
        self.issues += 1
        return subprocess.CompletedProcess(command, 0, json.dumps({"url": command[-1] + "/pair#token=fixture-token", "expires": "soon"}), "")

    def certificate_trust(self) -> dict:
        return {"status": "local", "url": self.urls.get("panel", "") + "/ca.crt",
                "sha256": hashlib.sha256(self.certificates["ca"].read_bytes()).hexdigest()}

    async def create_client(self):
        return ClientSession()

    async def proxy(self, request: web.Request, backend: str) -> web.Response:
        if backend != "approval" and request.headers.get("Authorization") != self.authorization:
            raise web.HTTPUnauthorized(headers={"WWW-Authenticate": 'Basic realm="disposable browser fixture"'})
        if request.path == "/ca.crt":
            return web.Response(body=self.certificates["ca"].read_bytes(), content_type="application/x-x509-ca-cert",
                                headers={"Content-Disposition": 'attachment; filename="fixture-ca.crt"'})
        if request.path in {"/project/", "/pair"}:
            return web.Response(text="<!doctype html><title>Disposable project</title><h1>Synthetic destination</h1>", content_type="text/html")
        if request.path == "/__fixture__/state":
            return web.json_response({"updates": self.updates, "pairing_links": self.issues})
        headers = {key: value for key, value in request.headers.items() if key.lower() not in {"connection", "transfer-encoding", "x-forwarded-proto", "x-real-ip"}}
        source = "192.0.2.2" if request.headers.get("X-Fixture-Engine") == "firefox" else "192.0.2.1"
        headers.update({"X-Forwarded-Proto": "https", "X-Forwarded-Host": request.host, "X-Real-IP": source})
        async with self.client.request(request.method, f"http://127.0.0.1:{self.backends[backend].server_port}{request.rel_url}",
                                       headers=headers, data=await request.read(), allow_redirects=False) as response:
            body = await response.read()
            response_headers = {key: value for key, value in response.headers.items()
                                if key.lower() not in {"connection", "transfer-encoding", "content-length", "server", "date"}}
            return web.Response(body=body, status=response.status, headers=response_headers)

    async def start_frontends(self) -> None:
        for name in ("panel", "pairing", "approval", "invalid", "guacamole"):
            if name == "guacamole" and self.guacamole_assets is None:
                continue
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            cert = "invalid" if name == "invalid" else "server"
            context.load_cert_chain(self.certificates[cert], self.certificates[cert + "_key"])
            if name == "guacamole":
                config = {"username": self.username, "origins": [], "certificate": "d" * 64}
                app = guacamole.create_app(config)
                async def favicon(request):
                    return web.Response(text=panel.render_favicon(), content_type="image/svg+xml")
                app.router.add_get("/favicon.svg", favicon)
                @web.middleware
                async def identity(request, handler):
                    if request.headers.get("Authorization") != self.authorization:
                        raise web.HTTPUnauthorized(headers={"WWW-Authenticate": 'Basic realm="disposable browser fixture"'})
                    headers = {**request.headers, "X-Forwarded-Proto": "https", "X-Guacamole-User": self.username}
                    return await handler(request.clone(headers=headers))
                app.middlewares.insert(0, identity)
                self.stack.enter_context(patch.object(guacamole, "RELEASE", str(self.guacamole_assets.parent.parent)))
            else:
                app = web.Application()
                async def handler(request, backend=name):
                    return await self.proxy(request, "panel" if backend == "invalid" else backend)
                app.router.add_route("*", "/{path:.*}", handler)
            runner = web.AppRunner(app, access_log=None)
            await runner.setup()
            self.runners.append(runner)
            site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=context)
            await site.start()
            self.urls[name] = f"https://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
            if name == "guacamole":
                config["origins"].append(self.urls[name])

    async def desktop_call(self, action: str, **kwargs) -> dict:
        if action == "browser-take":
            self.desktop.update(paused=True, browser_pause="f" * 32)
        elif action in {"resume", "browser-release"}:
            self.desktop.update(paused=False, browser_pause=None)
        elif action not in {"status", "start", "browser-refresh"}:
            raise AssertionError("Unexpected synthetic desktop action")
        return dict(self.desktop)

    async def protocol_login(self, session):
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
        reader = asyncio.StreamReader()
        writer = Writer()
        session.upstream, session.password = writer, ""
        reader.feed_data("".join(guacamole.instruction(*row) for row in (
            ("size", "0", "640", "480"), ("rect", "0", "0", "0", "640", "480"),
            ("cfill", "14", "0", "64", "128", "192", "255"), ("sync", "1"))).encode())
        return reader, writer, "fixture-session"

    async def cleanup(self) -> None:
        for runner in reversed(self.runners):
            await runner.cleanup()
        await self.client.close()

    def stop_loop(self) -> None:
        try:
            asyncio.run_coroutine_threadsafe(self.cleanup(), self.loop).result(10)
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(5)
            self.loop.close()

    def metadata(self) -> dict:
        return {"urls": self.urls, "ca": str(self.certificates["ca"]),
                "username": self.username, "password": self.password,
                "boundary": "synthetic first-party services and disposable Guacamole protocol; no upstream application UI"}


def main() -> None:
    import argparse
    import os

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--guacamole-assets", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    done = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: done.set())
    with FixtureEnvironment(args.directory, guacamole_assets=args.guacamole_assets) as environment:
        (args.directory / "fixture.json").write_text(json.dumps(environment.metadata()))
        done.wait()


if __name__ == "__main__":
    main()
