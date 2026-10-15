"""Fixed local SSH console using the panel's authenticated Guacamole tunnel."""

from __future__ import annotations

import asyncio
import html
from pathlib import Path
import secrets
import time

from aiohttp import web

from common.service_tools.guacamole_protocol import handshake
from common.service_tools.guacamole_service import (
    ASSETS, ATTACH_TIMEOUT, CSS, Gateway, IDLE_TIMEOUT, MAX_SESSION, Session,
)
from common.service_tools.web_panel_templates import panel_navigation, render_document
from lib.guacamole_config import RELEASE


class ConsoleGateway(Gateway):
    """One independent shell; no desktop start, pause or control operations."""

    login_error = "Console login failed; check the Unix password and private SSH service"
    allow_resize = True

    async def page(self, request: web.Request) -> web.Response:
        self.authenticate(request)
        if request.query:
            raise web.HTTPBadRequest(text="Invalid console URL")
        keys = "".join(f'<button type="button" data-key="{code}" disabled>{label}</button>'
                       for code, label in ((65289, "Tab"), (65307, "Esc"), (65288, "Backspace"),
                                           (65293, "Enter"), (65361, "←"), (65362, "↑"), (65364, "↓"), (65363, "→")))
        content = f'''<section class="desktop-panel"><h2>Unix shell</h2>
<p>Connect as <strong>{html.escape(self.config['username'])}</strong> without starting the desktop.
This shell has the account's normal permissions and does not pause agent tasks.</p>
<p><a href="/desktop/">Open shared desktop</a></p>
<form id="connect-form"><input type="hidden" id="csrf" value="{self.csrf}">
<label>Account<input value="{html.escape(self.config['username'], quote=True)}" readonly autocomplete="username"></label>
<label>Unix account password<input id="password" type="password" required maxlength="1024" autocomplete="off"></label>
<button id="connect">Connect to console</button></form>
<div id="desktop-view"><div class="desktop-controls">
<button id="disconnect" type="button">Disconnect</button><button id="fullscreen" type="button" disabled>Fullscreen</button>
<button id="keyboard-toggle" type="button" aria-expanded="false" aria-controls="keyboard-panel" disabled>Keyboard</button>
<label>Text size<select id="font-size"><option value="10">Small</option><option value="12" selected>Medium</option><option value="16">Large</option><option value="20">Extra large</option></select></label>
</div><p id="status" role="status" aria-live="polite">Disconnected</p>
<p id="lifetime">Sessions end after one hour or ten minutes without input.</p>
<div id="screen" tabindex="0" aria-label="Remote Unix console"></div>
<div id="keyboard-panel" hidden><label>Type to console<textarea id="console-keyboard" rows="1" autocapitalize="off" autocomplete="off" autocorrect="off" spellcheck="false" disabled></textarea></label>
<div class="keyboard-keys" role="group" aria-label="Console keys">
<button type="button" data-modifier="65507" aria-pressed="false" disabled>Ctrl</button>
<button type="button" data-modifier="65513" aria-pressed="false" disabled>Alt</button>{keys}</div></div></div>
<p>Click the console to type. On phones, use Keyboard for text and extra keys; swipe with two fingers to scroll.
Text size applies when connecting; the terminal fits the available width.</p>
<p>Disconnect closes this shell. Use <code>tmux</code> or <code>screen</code> for work that should survive disconnects.
Clipboard and file transfer are disabled.</p><noscript>JavaScript is required for the browser console.</noscript></section>'''
        navigation = panel_navigation(current="console", available=frozenset({"dashboard", "desktop", "console", "services", "jobs", "logs"}))
        page = render_document(title="Console", style=CSS + "#console-keyboard{width:100%}#screen{min-height:280px}#connect-form[hidden]{display:none}",
                               header='<header><p class="eyebrow">Workspace</p><h1>Console</h1><p>Shell access through Guacamole</p></header>',
                               content=content, navigation=navigation, footer='<footer>Basaltwater · Guacamole SSH</footer>')
        page = page.replace("</body>", '<script src="/console/assets/all.js"></script><script src="/console/assets/console.js"></script></body>')
        return web.Response(text=page, content_type="text/html")

    async def asset(self, request: web.Request) -> web.Response:
        self.authenticate(request)
        if request.query:
            raise web.HTTPBadRequest(text="Invalid asset URL")
        paths = {"all.js": Path(RELEASE) / "share/all.js", "console.js": ASSETS / "guacamole-console.js"}
        path = paths.get(request.match_info["name"])
        if path is None:
            raise web.HTTPNotFound()
        return web.Response(body=path.read_bytes(), content_type="application/javascript")

    async def connect(self, request: web.Request) -> web.Response:
        value = await self.form(request, {"password", "width", "height", "font_size"})
        password = value.pop("password")
        if not isinstance(password, str) or not 1 <= len(password) <= 1024 or "\0" in password:
            raise web.HTTPBadRequest(text="Enter the Unix account password")
        if (type(value["width"]) is not int or not 240 <= value["width"] <= 3840
                or type(value["height"]) is not int or not 180 <= value["height"] <= 2160
                or type(value["font_size"]) is not int or value["font_size"] not in {10, 12, 16, 20}):
            raise web.HTTPBadRequest(text="Invalid console dimensions or font size")
        async with self.lock:
            if self.session is not None:
                old = self.session
                if (not old.closed.is_set() and (old.attached or time.monotonic() - old.created < ATTACH_TIMEOUT)
                        and time.monotonic() - old.created < MAX_SESSION
                        and time.monotonic() - old.activity < IDLE_TIMEOUT):
                    raise web.HTTPConflict(text="A console is already open; disconnect it first")
                await self.retire(old)
            self.session = Session(secrets.token_urlsafe(32), "", [value["width"], value["height"]],
                                   password, font_size=str(value["font_size"]))
            return web.json_response({"attachment": self.session.identifier, "expires_in": ATTACH_TIMEOUT})

    def remaining(self, session: Session) -> dict:
        self.selected_session(session.identifier)
        now = time.monotonic()
        result = {"expires_in": max(0, int(MAX_SESSION - (now - session.created))),
                  "idle_expires_in": max(0, int(IDLE_TIMEOUT - (now - session.activity)))}
        if not all(result.values()):
            session.revoke()
            raise web.HTTPConflict(text="Console session expired; reconnect")
        return result

    async def validate_input(self, session: Session) -> None:
        self.remaining(session)
        # Input starts only after the native terminal supplies its first sync.
        session.controlled = True

    async def status(self, request: web.Request) -> web.Response:
        value = await self.form(request, {"session"})
        async with self.lock:
            return web.json_response(self.remaining(self.selected_session(value["session"])))

    async def login(self, session: Session):
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", 4822, limit=65536), 5)
            session.upstream = writer
            if session.closed.is_set():
                raise ValueError("Console attachment revoked")
            identifier = await asyncio.wait_for(handshake(
                reader, writer, self.config["username"], session.password, "", session.geometry,
                ssh_host_key=self.config["ssh_host_key"], font_size=session.font_size), 30)
            return reader, writer, identifier
        finally:
            session.password = ""

    async def watch(self, session: Session) -> None:
        while self.session is session:
            await asyncio.sleep(2)
            try:
                self.remaining(session)
            except web.HTTPException:
                return


CONSOLE_KEY = web.AppKey("console", ConsoleGateway)


def register_console(app: web.Application, config: dict) -> None:
    gateway = ConsoleGateway(config)
    app[CONSOLE_KEY] = gateway
    app.router.add_get("/console/", gateway.page)
    app.router.add_get("/console/assets/{name}", gateway.asset)
    app.router.add_post("/console/session", gateway.connect)
    app.router.add_post("/console/status", gateway.status)
    app.router.add_post("/console/disconnect", gateway.disconnect)
    app.router.add_get("/console/ws", gateway.websocket)

    async def shutdown(application):
        async with gateway.lock:
            session = gateway.session
            if session:
                await gateway.retire(session)
        if session:
            await session.close()

    async def lifecycle(application):
        expiry = asyncio.create_task(gateway.expire_pending())
        yield
        expiry.cancel()
        await asyncio.gather(expiry, return_exceptions=True)
        await shutdown(application)
    app.on_shutdown.append(shutdown)
    app.cleanup_ctx.append(lifecycle)
