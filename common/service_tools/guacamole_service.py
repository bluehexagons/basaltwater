#!/usr/bin/env python3
"""Authenticated, bounded desktop tunnel and fixed private XRDP relay.

The HTTP socket is reachable only by the panel account and Nginx. Nginx must
overwrite the authenticated identity and transport headers. guacd and the
TCP RDP relay run inside this service's private network namespace.
"""

from __future__ import annotations

import argparse
import asyncio
import codecs
import contextlib
from dataclasses import dataclass, field
import html
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import stat
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aiohttp import WSMsgType, web

from common.service_tools.guacamole_protocol import Parser, filter_instruction, handshake, instruction
from common.service_tools.web_panel_templates import panel_navigation, render_document
from desktop import session_runtime as desktop
from lib.atomic_io import read_json_file
from lib.guacamole_config import HTTP_SOCKET, RDP_SOCKET, RELEASE
from lib.validation import validate_filesystem_path
from lib.validators import validate_username

ASSETS = Path(__file__).resolve().parents[2] / "desktop/config"
MAX_SESSION = 3600
IDLE_TIMEOUT = 600
ATTACH_TIMEOUT = 30
BODY_LIMIT = 8192


def load_configuration(path: str) -> dict:
    validate_filesystem_path(path, must_exist=True)
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError("Guacamole configuration must be a root-owned regular file")
    value = read_json_file(path, max_bytes=16384)
    if (not isinstance(value, dict) or value.get("version") != 1
            or not validate_username(value.get("username", ""))
            or type(value.get("uid")) is not int or value["uid"] != os.getuid() or value["uid"] == 0
            or type(value.get("socket_gid")) is not int or value["socket_gid"] < 0
            or not isinstance(value.get("certificate"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", value["certificate"])
            or not isinstance(value.get("origins"), list) or not 1 <= len(value["origins"]) <= 64
            or any(not isinstance(origin, str) or not origin.startswith("https://") or len(origin) > 300 for origin in value["origins"])):
        raise ValueError("Invalid Guacamole configuration")
    return value


async def desktop_call(action: str, *, generation: str | None = None, geometry: list[int] | None = None,
                       pause_token: str | None = None) -> dict:
    payload = {"action": action}
    if generation is not None:
        payload["generation"] = generation
        if geometry is not None:
            payload["geometry"] = geometry
    if action.startswith("browser-"):
        payload["pause_token"] = pause_token
    function = desktop.start if action == "start" else desktop.status if action == "status" else lambda: desktop.request(payload)
    return await asyncio.wait_for(asyncio.to_thread(function), 65 if action == "start" else 35)


@dataclass
class Session:
    identifier: str
    generation: str
    geometry: list[int]
    password: str = field(repr=False)
    created: float = field(default_factory=time.monotonic)
    activity: float = field(default_factory=time.monotonic)
    attached: bool = False
    ready: bool = False
    controlled: bool = False
    pause_token: str | None = field(default=None, repr=False)
    keys: set[str] = field(default_factory=set)
    pointer: list[str] | None = None
    socket: web.WebSocketResponse | None = None
    upstream: asyncio.StreamWriter | None = None
    closed: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    def release_input(self) -> None:
        """Release held keys/buttons whenever browser control ends."""
        self.controlled = False
        if self.upstream is not None and not self.upstream.is_closing():
            self._release_upstream_input()
        self.keys.clear()
        self.pointer = None

    def _release_upstream_input(self) -> None:
        with contextlib.suppress(OSError):
            for key in self.keys:
                self.upstream.write(instruction("key", key, "0").encode())
            if self.pointer is not None:
                self.upstream.write(instruction("mouse", *self.pointer, "0").encode())

    def revoke(self) -> None:
        """Invalidate immediately, including an in-flight native handshake."""
        self.closed.set()
        self.ready = False
        self.release_input()
        self.password = ""
        if self.upstream is not None:
            self.upstream.close()

    async def close(self) -> None:
        self.revoke()
        if self.socket is not None and self.socket.prepared:
            await self.socket.close()


class Gateway:
    def __init__(self, config: dict):
        self.config = config
        self.csrf = secrets.token_urlsafe(32)
        self.session: Session | None = None
        self.lock = asyncio.Lock()

    def authenticate(self, request: web.Request, *, origin: bool = False) -> None:
        expected = "https://" + request.headers.get("Host", "")
        if (request.headers.get("X-Forwarded-Proto") != "https"
                or request.headers.get("X-Guacamole-User") != self.config["username"]
                or expected not in self.config["origins"]
                or (origin and request.headers.get("Origin") != expected)):
            raise web.HTTPForbidden(text="Desktop access denied")

    async def form(self, request: web.Request, allowed: set[str]) -> dict:
        self.authenticate(request, origin=True)
        if request.query or request.content_type != "application/json":
            raise web.HTTPBadRequest(text="Invalid desktop request")
        try:
            # read_json_file's duplicate-key rule is also applied to requests.
            def pairs(items):
                result = {}
                for key, value in items:
                    if key in result:
                        raise ValueError("Duplicate field")
                    result[key] = value
                return result
            value = json.loads(await request.text(), object_pairs_hook=pairs)
        except (ValueError, UnicodeError):
            raise web.HTTPBadRequest(text="Invalid desktop request") from None
        if (not isinstance(value, dict) or set(value) != allowed | {"csrf"}
                or not isinstance(value.get("csrf"), str)
                or not secrets.compare_digest(value["csrf"].encode("utf-8"), self.csrf.encode("ascii"))):
            raise web.HTTPForbidden(text="Invalid desktop request")
        return value

    async def page(self, request: web.Request) -> web.Response:
        self.authenticate(request)
        if request.query:
            raise web.HTTPBadRequest(text="Invalid desktop URL")
        content = f'''<section class="desktop-panel"><h2>Shared desktop</h2>
<p>Connect to observe the agent. Take control pauses agent desktop actions. Disconnect releases your browser control.</p>
<form id="connect-form"><input type="hidden" id="csrf" value="{self.csrf}">
<label>Account <input value="{html.escape(self.config['username'], quote=True)}" readonly autocomplete="username"></label>
<label>Unix account password <input id="password" type="password" required maxlength="1024" autocomplete="off"></label>
<button id="connect">Connect to desktop</button></form>
<div class="desktop-controls"><button id="take" class="primary-control" aria-pressed="false" disabled>Take control</button>
<button id="observe" disabled>Observe only</button><button id="resume" disabled>Resume agent</button>
<button id="disconnect" type="button">Disconnect browser session</button><button id="fullscreen" disabled>Fullscreen</button></div>
<div class="logout-controls"><button id="logout" type="button" disabled>Log out desktop</button>
<span id="logout-confirmation" hidden>Log out and close desktop applications?
<button id="confirm-logout" type="button">Log out desktop now</button><button id="cancel-logout" type="button">Cancel</button></span></div>
<p id="status" role="status" aria-live="polite">Disconnected</p>
<p id="lifetime">Connections last at most one hour and expire after ten minutes without input or a control action.</p>
<div id="screen" tabindex="0" aria-label="Remote desktop"></div>
<p>Clipboard, file transfer, audio and recording are disabled. Closing this connection keeps the desktop running.
Keep administrator sign-ins and privilege approvals on your own device.</p>
<noscript>JavaScript is required for the browser desktop.</noscript></section>'''
        navigation = list(panel_navigation())
        navigation.insert(2, ("/desktop/", "Desktop", "desktop"))
        page = render_document(title="Desktop", style=CSS,
                               header='<header><p class="eyebrow">Workspace</p><h1>Desktop</h1></header>',
                               content=content, navigation=navigation,
                               footer='<footer>Basaltwater · Guacamole</footer>')
        page = page.replace("</body>", '<script src="/desktop/assets/all.js"></script><script src="/desktop/assets/desktop.js"></script></body>')
        return web.Response(text=page, content_type="text/html")

    async def asset(self, request: web.Request) -> web.Response:
        self.authenticate(request)
        if request.query:
            raise web.HTTPBadRequest(text="Invalid asset URL")
        paths = {"all.js": Path(RELEASE) / "share/all.js", "desktop.js": ASSETS / "guacamole.js"}
        path = paths.get(request.match_info["name"])
        if path is None:
            raise web.HTTPNotFound()
        return web.Response(body=path.read_bytes(), content_type="application/javascript")

    async def connect(self, request: web.Request) -> web.Response:
        value = await self.form(request, {"password"})
        password = value.pop("password")
        if not isinstance(password, str) or not 1 <= len(password) <= 1024 or "\0" in password:
            raise web.HTTPBadRequest(text="Enter the Unix account password")
        async with self.lock:
            if self.session is not None:
                old = self.session
                snapshot = await desktop_call("status")
                if (not old.closed.is_set() and snapshot.get("state") == "running"
                        and snapshot.get("generation") == old.generation
                        and snapshot.get("geometry") == old.geometry
                        and (old.attached or time.monotonic() - old.created < ATTACH_TIMEOUT)
                        and time.monotonic() - old.created < MAX_SESSION
                        and time.monotonic() - old.activity < IDLE_TIMEOUT):
                    raise web.HTTPConflict(text="A browser session is already open; disconnect it first")
                await self.retire(old)
            # Starting is an explicit authenticated action. It reuses the
            # managed display and never starts a second GUI or stores a password.
            snapshot = await desktop_call("start")
            if snapshot.get("state") != "running" or not snapshot.get("generation"):
                raise web.HTTPServiceUnavailable(text="Shared desktop is unavailable")
            geometry = snapshot.get("geometry")
            if not isinstance(geometry, list) or len(geometry) != 2 or any(type(n) is not int or not 1 <= n <= 16384 for n in geometry):
                raise web.HTTPServiceUnavailable(text="Desktop geometry is unavailable")
            self.session = Session(secrets.token_urlsafe(32), snapshot["generation"], geometry, password)
            return web.json_response({"attachment": self.session.identifier, "expires_in": ATTACH_TIMEOUT})

    def selected_session(self, identifier: str) -> Session:
        session = self.session
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", identifier) or session is None or session.closed.is_set() or not secrets.compare_digest(identifier, session.identifier):
            raise web.HTTPForbidden(text="Desktop session expired")
        return session

    def check_snapshot(self, session: Session, snapshot: dict) -> None:
        if (self.session is not session or session.closed.is_set()
                or snapshot.get("state") != "running"
                or snapshot.get("generation") != session.generation
                or snapshot.get("geometry") != session.geometry):
            session.revoke()
            if self.session is session:
                self.session = None
            message = ("Desktop logged out. Connect to start a new desktop."
                       if snapshot.get("state") in {"stopped", "stopping"}
                       else "Desktop session changed; reconnect")
            raise web.HTTPConflict(text=message)

    def state(self, session: Session, snapshot: dict) -> dict:
        self.check_snapshot(session, snapshot)
        if (snapshot.get("paused") is not True
                or snapshot.get("browser_pause") != session.pause_token):
            session.release_input()
        if snapshot.get("browser_pause") != session.pause_token:
            session.pause_token = None
        now = time.monotonic()
        remaining = max(0, int(MAX_SESSION - (now - session.created)))
        idle = max(0, int(IDLE_TIMEOUT - (now - session.activity)))
        if not remaining or not idle:
            session.revoke()
            raise web.HTTPConflict(text="Desktop session expired; reconnect")
        return {"controlled": session.controlled, "paused": snapshot.get("paused") is True,
                "expires_in": remaining, "idle_expires_in": idle}

    async def retire(self, session: Session) -> None:
        """Invalidate before conditionally releasing this connection's pause."""
        session.revoke()
        if self.session is session:
            self.session = None
        token, session.pause_token = session.pause_token, None
        if token is not None:
            # A replaced desktop, explicit local pause, or later browser owner
            # cannot be resumed by this session's cleanup. The supervisor's
            # short lease also recovers from failed cleanup or a gateway crash.
            with contextlib.suppress(OSError, ValueError, RuntimeError, asyncio.TimeoutError):
                await desktop_call("browser-release", generation=session.generation, pause_token=token)

    async def refresh_control(self, session: Session, snapshot: dict) -> dict:
        self.state(session, snapshot)
        if session.pause_token is not None:
            snapshot = await desktop_call("browser-refresh", generation=session.generation,
                                          geometry=session.geometry, pause_token=session.pause_token)
            self.state(session, snapshot)
        return snapshot

    async def take_control(self, session: Session) -> dict:
        snapshot = await desktop_call("browser-take", generation=session.generation,
                                      geometry=session.geometry, pause_token=session.pause_token)
        token = snapshot.get("browser_pause")
        if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{32}", token):
            raise RuntimeError("Desktop supervisor needs updating before browser control")
        session.pause_token = token
        self.check_snapshot(session, snapshot)
        session.controlled = snapshot.get("paused") is True
        return snapshot

    async def status(self, request: web.Request) -> web.Response:
        value = await self.form(request, {"session"})
        async with self.lock:
            session = self.selected_session(value["session"])
            if not session.ready:
                raise web.HTTPConflict(text="Desktop display is not ready")
            return web.json_response(self.state(session, await desktop_call("status")))

    async def control(self, request: web.Request) -> web.Response:
        value = await self.form(request, {"session", "action"})
        if not isinstance(value["action"], str) or value["action"] not in {"take", "observe", "resume"}:
            raise web.HTTPBadRequest(text="Invalid desktop action")
        async with self.lock:
            session = self.selected_session(value["session"])
            if not session.ready:
                raise web.HTTPConflict(text="Connect before taking control")
            session.release_input()
            snapshot = await desktop_call("status")
            self.state(session, snapshot)
            action = value["action"]
            if action == "take":
                snapshot = await self.take_control(session)
            elif action == "resume":
                snapshot = await desktop_call("resume",
                                              generation=session.generation, geometry=session.geometry)
                self.check_snapshot(session, snapshot)
                session.pause_token = None
            session.controlled = action == "take" and snapshot.get("paused") is True
            session.activity = time.monotonic()
            return web.json_response(self.state(session, snapshot))

    async def disconnect(self, request: web.Request) -> web.Response:
        value = await self.form(request, {"session"})
        async with self.lock:
            identifier = value["session"]
            # A just-logged-out page may race the WebSocket cleanup. Treat its
            # disconnect as complete only when no replacement slot exists.
            if self.session is None and isinstance(identifier, str) and re.fullmatch(r"[A-Za-z0-9_-]{43}", identifier):
                session = None
            else:
                session = self.selected_session(identifier) if identifier is not None else self.session
            if session:
                await self.retire(session)
        if session:
            await session.close()
        return web.json_response({"disconnected": True})

    async def logout(self, request: web.Request) -> web.Response:
        value = await self.form(request, {"session"})
        async with self.lock:
            session = self.selected_session(value["session"])
            if not session.ready:
                raise web.HTTPConflict(text="Connect before logging out the desktop")
            session.release_input()
            self.state(session, await desktop_call("status"))
            snapshot = await self.take_control(session)
            result = await desktop_call("browser-logout", generation=session.generation,
                                        geometry=session.geometry, pause_token=session.pause_token)
            session.activity = time.monotonic()
            # Keep the display/input alive for unsaved-work dialogs or a
            # canceled logout. The watcher closes it when the desktop ends.
            return web.json_response({**self.state(session, snapshot),
                                      "logout_requested": result.get("logout_requested") is True})

    async def websocket(self, request: web.Request) -> web.WebSocketResponse:
        self.authenticate(request, origin=True)
        if set(request.query) != {"attachment"} or len(request.query.getall("attachment")) != 1:
            raise web.HTTPForbidden(text="Invalid desktop attachment")
        ws = web.WebSocketResponse(protocols=("guacamole",), heartbeat=20, timeout=2, max_msg_size=65536, compress=False)
        if not ws.can_prepare(request).ok or "guacamole" not in [p.strip() for p in request.headers.get("Sec-WebSocket-Protocol", "").split(",")]:
            raise web.HTTPBadRequest(text="A Guacamole WebSocket is required")
        async with self.lock:
            session = self.selected_session(request.query["attachment"])
            if session.attached or time.monotonic() - session.created >= ATTACH_TIMEOUT:
                raise web.HTTPForbidden(text="Desktop attachment expired or already used")
            session.attached = True
            session.socket = ws
            # Claim once before any asynchronous work, including upstream login.
        tasks = [asyncio.create_task(session.closed.wait()), asyncio.create_task(self.login(session))]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if session.closed.is_set():
                raise web.HTTPConflict(text="Desktop attachment revoked")
            reader, writer, identifier = tasks[1].result()
            async with self.lock:
                self.selected_session(session.identifier)
                await ws.prepare(request)
            await ws.send_str(instruction("", identifier))
            tasks.extend([asyncio.create_task(self.to_browser(session, reader, ws)),
                          asyncio.create_task(self.from_browser(session, writer, ws)),
                          asyncio.create_task(self.watch(session))])
            await asyncio.wait([tasks[0], *tasks[2:]], return_when=asyncio.FIRST_COMPLETED)
        except (OSError, ValueError, asyncio.TimeoutError, asyncio.IncompleteReadError):
            if not ws.prepared:
                raise web.HTTPBadGateway(text="Desktop login failed; check the Unix password and XRDP readiness") from None
        finally:
            session.revoke()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            async with self.lock:
                await self.retire(session)
            await session.close()
        return ws

    async def login(self, session: Session):
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", 4822, limit=65536), 5)
            session.upstream = writer
            if session.closed.is_set():
                raise ValueError("Desktop attachment revoked")
            identifier = await asyncio.wait_for(handshake(reader, writer, self.config["username"], session.password,
                                                         self.config["certificate"], session.geometry), 30)
            return reader, writer, identifier
        finally:
            session.password = ""

    async def to_browser(self, session: Session, reader: asyncio.StreamReader, ws: web.WebSocketResponse) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")()
        parser = Parser()
        while data := await reader.read(65536):
            text = decoder.decode(data)
            if text:
                elements = parser.feed(text)
                if not session.ready and any(row[0] == "sync" for row in elements):
                    async with self.lock:
                        self.check_snapshot(session, await desktop_call("status"))
                        session.ready = True
                await ws.send_str(text)
        decoder.decode(b"", final=True)

    async def from_browser(self, session: Session, writer: asyncio.StreamWriter, ws: web.WebSocketResponse) -> None:
        parser = Parser()
        async for message in ws:
            if message.type != WSMsgType.TEXT:
                return
            elements_list = parser.feed(message.data)
            async with self.lock:
                if self.session is not session or session.closed.is_set():
                    return
                if any(elements[0] in {"key", "mouse"} for elements in elements_list) and session.controlled:
                    snapshot = await desktop_call("status")
                    self.state(session, snapshot)
                for elements in elements_list:
                    # Guacamole's tunnel ping belongs to this WebSocket layer,
                    # not guacd. It cannot renew user inactivity or authorize input.
                    if elements[0] == "" and len(elements) == 3 and elements[1] == "ping" and elements[2].isascii() and elements[2].isdigit():
                        await ws.send_str(instruction(*elements))
                        continue
                    accepted = filter_instruction(elements, session.controlled)
                    if accepted is not None:
                        if elements[0] == "key":
                            if elements[2] == "1":
                                if len(session.keys) >= 64 and elements[1] not in session.keys:
                                    raise ValueError("Too many simultaneously held keys")
                                session.keys.add(elements[1])
                            else:
                                session.keys.discard(elements[1])
                        elif elements[0] == "mouse":
                            session.pointer = elements[1:3] if elements[3] != "0" else None
                        writer.write(accepted.encode("utf-8"))
                        if elements[0] in {"key", "mouse"}:
                            session.activity = time.monotonic()
                await writer.drain()

    async def watch(self, session: Session) -> None:
        while self.session is session:
            await asyncio.sleep(2)
            now = time.monotonic()
            if now - session.created >= MAX_SESSION or now - session.activity >= IDLE_TIMEOUT:
                return
            async with self.lock:
                if self.session is not session or session.closed.is_set():
                    return
                try:
                    await self.refresh_control(session, await desktop_call("status"))
                except web.HTTPConflict:
                    return

    async def expire_pending(self) -> None:
        while True:
            await asyncio.sleep(1)
            async with self.lock:
                if self.session and not self.session.attached and time.monotonic() - self.session.created >= ATTACH_TIMEOUT:
                    await self.retire(self.session)


CSS = """
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text)}
.sidebar{position:fixed;inset:0 auto 0 0;width:235px;padding:24px 12px;background:var(--panel);overflow:auto}
.sidebar a{display:flex;gap:10px;padding:10px;text-decoration:none;color:var(--text);border-radius:8px}
.nav-group{color:var(--muted);font-size:.8rem;margin:20px 10px 8px}main{margin-left:235px;padding:24px;max-width:1920px}
header,.desktop-panel{padding:20px;border:1px solid var(--line);border-radius:12px;background:var(--panel);margin-bottom:20px}
.eyebrow{color:var(--accent);font-size:.85rem}h1{margin:10px 0}p{line-height:1.6}label{display:flex;flex-direction:column;gap:6px}
form,.desktop-controls{display:flex;gap:12px;align-items:end;flex-wrap:wrap;margin:16px 0}
button,input{font:inherit;padding:10px 14px;border:1px solid var(--line);border-radius:8px}
input{color:var(--text);background:var(--bg)}button{color:var(--text);background:var(--bg);cursor:pointer}
#connect,.primary-control{color:var(--panel);background:var(--accent);font-weight:700}
.primary-control{font-size:1.15rem;padding:14px 22px;border:2px solid var(--accent)}
.primary-control[aria-pressed="true"]{box-shadow:0 0 0 3px var(--line)}
.logout-controls{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:16px 0}
#logout-confirmation:not([hidden]){display:flex;align-items:center;gap:12px;flex-wrap:wrap}
button:focus-visible{outline:3px solid var(--accent);outline-offset:3px}
button:disabled{opacity:.5;cursor:default}#screen{position:relative;overflow:auto;width:100%;min-height:240px;background:#111}
#screen:fullscreen{display:flex;align-items:center;justify-content:center}#status{font-weight:600}.skip-link{position:absolute;left:-10000px}.skip-link:focus{left:10px}
footer{padding:20px;color:var(--muted)}@media(max-width:900px){.sidebar{position:static;width:auto}.nav-links{display:flex;overflow:auto}.nav-links a{flex:none}.nav-group{display:none}main{margin:0;padding:12px}}
"""

GATEWAY_KEY = web.AppKey("gateway", Gateway)


def create_app(config: dict) -> web.Application:
    gateway = Gateway(config)

    @web.middleware
    async def security(request, handler):
        try:
            response = await handler(request)
        except web.HTTPException as exc:
            response = web.Response(body=exc.body, status=exc.status, headers=exc.headers)
        except (OSError, ValueError, RuntimeError, asyncio.TimeoutError):
            response = web.Response(status=503, text="Desktop unavailable; inspect the desktop and service readiness")
        if not response.prepared:
            response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                                     "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
                                     "Content-Security-Policy": "default-src 'none'; script-src 'self'; connect-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"})
        return response

    app = web.Application(client_max_size=BODY_LIMIT, middlewares=[security])
    app[GATEWAY_KEY] = gateway
    app.router.add_get("/desktop/", gateway.page)
    app.router.add_get("/desktop/assets/{name}", gateway.asset)
    app.router.add_post("/desktop/session", gateway.connect)
    app.router.add_post("/desktop/control", gateway.control)
    app.router.add_post("/desktop/status", gateway.status)
    app.router.add_post("/desktop/disconnect", gateway.disconnect)
    app.router.add_post("/desktop/logout", gateway.logout)
    app.router.add_get("/desktop/ws", gateway.websocket)

    async def shutdown(application):
        async with gateway.lock:
            session = gateway.session
            if session:
                await gateway.retire(session)
        if session:
            await session.close()
    app.on_shutdown.append(shutdown)

    async def lifecycle(application):
        expiry = asyncio.create_task(gateway.expire_pending())
        yield
        expiry.cancel()
        await asyncio.gather(expiry, return_exceptions=True)
        await shutdown(application)
    app.cleanup_ctx.append(lifecycle)
    return app


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    while data := await reader.read(65536):
        writer.write(data)
        await writer.drain()


async def relay(reader, writer, *, outside: bool) -> None:
    """The only namespace escape is a fixed, account-private local XRDP socket."""
    upstream = None
    tasks = []
    try:
        other_reader, upstream = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", 3389) if outside else asyncio.open_unix_connection(RDP_SOCKET), 5)
        tasks = [asyncio.create_task(pipe(reader, upstream)), asyncio.create_task(pipe(other_reader, writer))]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except OSError:
        pass
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        writer.close()
        if upstream:
            upstream.close()


def private_socket(path: str, gid: int | None = None) -> socket.socket:
    validate_filesystem_path(path, must_exist=False)
    if os.path.lexists(path):
        info = os.lstat(path)
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError("Refusing an unsafe Guacamole socket")
        os.unlink(path)
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(path)
    os.chmod(path, 0o600 if gid is None else 0o660)
    if gid is not None:
        os.chown(path, -1, gid)
    listener.listen(16)
    listener.setblocking(False)
    return listener


def notify_ready() -> None:
    """Let systemd's unit transaction wait for actual listener readiness."""
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return
    if address.startswith("@"):
        address = "\0" + address[1:]
    elif not address.startswith("/"):
        raise ValueError("Invalid service notification socket")
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notifier:
        notifier.settimeout(5)
        notifier.sendto(b"READY=1", address)


async def serve(config: dict | None) -> None:
    if config is None:
        listener = private_socket(RDP_SOCKET)
        server = await asyncio.start_unix_server(lambda r, w: relay(r, w, outside=True), sock=listener)
        async with server:
            notify_ready()
            await server.serve_forever()
        return
    app = create_app(config)
    runner = web.AppRunner(app, access_log=None, shutdown_timeout=10)
    process = None
    server = None
    try:
        server = await asyncio.start_server(lambda r, w: relay(r, w, outside=False), "127.0.0.1", 3389)
        process = await asyncio.create_subprocess_exec(f"{RELEASE}/sbin/guacd", "-f", "-b", "127.0.0.1", "-l", "4822", "-L", "error",
                                                     "-p", str(Path(HTTP_SOCKET).parent / "guacd.pid"),
                                                     env={**{key: value for key, value in os.environ.items() if key != "NOTIFY_SOCKET"},
                                                          "LD_LIBRARY_PATH": f"{RELEASE}/lib"},
                                                     stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        # Fail startup if the pinned native gateway does not listen promptly.
        for _ in range(50):
            if process.returncode is not None:
                raise RuntimeError("guacd failed to start")
            try:
                _, writer = await asyncio.open_connection("127.0.0.1", 4822)
                writer.close()
                await writer.wait_closed()
                break
            except OSError:
                await asyncio.sleep(.1)
        else:
            raise RuntimeError("guacd startup timed out")
        await runner.setup()
        await web.SockSite(runner, private_socket(HTTP_SOCKET, config["socket_gid"])).start()
        notify_ready()
        await process.wait()
        raise RuntimeError("guacd stopped")
    finally:
        await runner.cleanup()
        if server:
            server.close()
            await server.wait_closed()
        if process and process.returncode is None:
            process.terminate()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(process.wait(), 5)
            if process.returncode is None:
                process.kill()
                await process.wait()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--config")
    mode.add_argument("--rdp-relay", action="store_true")
    args = parser.parse_args()
    try:
        asyncio.run(run_service(None if args.rdp_relay else load_configuration(args.config)))
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, RuntimeError):
        print("Guacamole startup failed; check managed configuration, sockets and runtime dependencies", file=sys.stderr)
        return 1
    return 0


async def run_service(config: dict | None) -> None:
    """Release input and close tunnels before systemd terminates native children."""
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    stopping = False
    def stop() -> None:
        nonlocal stopping
        stopping = True
        task.cancel()
    loop.add_signal_handler(signal.SIGTERM, stop)
    try:
        await serve(config)
    except asyncio.CancelledError:
        if not stopping:
            raise
    finally:
        loop.remove_signal_handler(signal.SIGTERM)


if __name__ == "__main__":
    raise SystemExit(main())
