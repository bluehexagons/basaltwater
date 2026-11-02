#!/usr/bin/env python3
"""Isolated approval reviews for the main panel and optional standalone HTTPS UI."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import html
import json
import os
import pwd
import secrets
import socket
import socketserver
import ssl
import struct
import sys
import time
import threading
from urllib.parse import parse_qs, urlsplit

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, "/opt/basaltwater")

from lib.privilege_auth import authenticate, validate_auth
from lib.privilege_client import exchange
from lib.privilege_policy import APPROVAL_SOCKET, ID_PATTERN, PANEL_APPROVAL_SOCKET, canonical, load_policy
from common.service_tools.web_panel_templates import render_favicon

STYLE = """
body{font:17px system-ui,sans-serif;color:#172a3a;background:#eef2f5;margin:0;overflow-wrap:anywhere}
main{max-width:850px;margin:3rem auto;padding:2rem;background:white;border-radius:12px}
h1{margin-top:0}a{color:#145c93}code,pre{overflow-wrap:anywhere;white-space:pre-wrap}
dt{font-weight:650;margin-top:1rem}dd{margin:.3rem 0}button{font:inherit;padding:.7rem 1.2rem;
margin:.5rem .5rem 0 0;border:1px solid #36526a;border-radius:6px;cursor:pointer}
button[value=approve]{background:#184f73;color:white}.notice{border-left:4px solid #d49828;padding:1rem;background:#fff6e5}
li{margin:1rem 0}.muted{color:#4d6070}footer{margin-top:2rem;font-size:.85rem}
"""


def page(content: str) -> str:
    return ('<!doctype html><html lang="en"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<link rel="icon" href="/favicon.svg" type="image/svg+xml">'
            '<title>Privilege approvals · basaltwater</title><style>' + STYLE + '</style>'
            '<main><h1>Privilege approvals</h1>' + content +
            '<footer>Approve from your own device. Keep this password out of the agent VM’s browser and chat.</footer></main></html>')


def csrf(secret: bytes, request: dict, username: str) -> str:
    return hmac.new(secret, canonical([request["id"], request["digest"], username]).encode(), hashlib.sha256).hexdigest()


def render_request(request: dict, token: str, *, integrated: bool = False) -> str:
    escape = lambda value: html.escape(str(value), quote=True)
    plan = request["plan"]
    expires = datetime.fromtimestamp(request["expires"], timezone.utc).isoformat()
    fields = [("Machine identity", plan["machine"]), ("Requesting account UID", plan["uid"]),
              ("Operation", plan["operation"]), ("Command", canonical(plan["argv"])), ("Parameters", canonical(plan["parameters"])),
              ("Expected effects", plan["effect"]),
              ("Review window", f'{round(request["expires"] - request["created"])} seconds'),
              ("Expires (UTC)", expires),
              ("Status", request["state"])]
    if request.get("execution"):
        execution = request["execution"]
        fields.extend([("Exit code", execution["exit_code"]), ("Execution detail", execution["failure"] or "Completed")])
    listing = "/approvals" if integrated else "/"
    content = f'<p><a href="{listing}">All requests</a></p><dl>'
    content += ''.join(f'<dt>{escape(label)}</dt><dd>{escape(value)}</dd>' for label, value in fields)
    content += '</dl><p class="notice">Agent-provided explanation (untrusted): ' + escape(request["reason"]) + '</p>'
    if request["state"] == "pending":
        content += (f'<form method="post" action="/requests/{request["id"]}">'
                    f'<input type="hidden" name="csrf" value="{escape(token)}">'
                    f'<input type="hidden" name="digest" value="{escape(request["digest"])}">'
                    '<button name="decision" value="approve">Approve once</button>'
                    '<button name="decision" value="deny">Deny</button></form>')
    else:
        content += '<p>This request cannot be approved again. Refresh to see the latest execution status.</p>'
    return approval_page(content, integrated=integrated)


def approval_page(content: str, *, integrated: bool = False) -> str:
    if integrated:
        from common.service_tools.web_panel_approval import render_approval_page

        return render_approval_page(content)
    return page(content)


def browser_origin(origin: str) -> str:
    """Use the origin serialization browsers send, including default ports."""
    parsed = urlsplit(origin)
    default = 443 if parsed.scheme == "https" else 80
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    return f"{parsed.scheme}://{host}" + (f":{parsed.port}" if parsed.port != default else "")


class ApprovalHandler(BaseHTTPRequestHandler):
    server_version = "basaltwater-approval"
    sys_version = ""

    def log_message(self, format, *args):
        # Never log Authorization, request bodies, or URLs containing request IDs.
        pass

    def setup(self):
        self.request.settimeout(5)
        super().setup()

    def respond(self, status: int, content: str, *, challenge: bool = False, location: str | None = None,
                content_type: str = "text/html; charset=utf-8"):
        payload = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        # no-referrer makes Chromium send Origin: null on form POSTs.
        # Preserve same-origin form provenance without leaking URLs cross-origin.
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.send_header("Connection", "close")
        if challenge:
            self.send_header("WWW-Authenticate", 'Basic realm="basaltwater privilege approvals", charset="UTF-8"')
        if location:
            self.send_header("Location", location)
        self.end_headers()
        self.wfile.write(payload)
        self.close_connection = True

    def authorized(self) -> bool:
        self.approval_origin = self.server.origin
        if self.server.integrated:
            _pid, uid, _gid = struct.unpack("3i", self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != self.server.proxy_uid or self.headers.get_all("X-Basaltwater-User") != [self.server.auth["username"]]:
                self.respond(403, self.page("<p>Authenticated panel proxy required.</p>"))
                return False
            hosts = self.headers.get_all("Host") or []
            origin = next((value for value in self.server.origins if hosts == [urlsplit(value).netloc]), None)
            if origin is None:
                self.respond(400, self.page("<p>Incorrect approval host.</p>"))
                return False
            self.approval_origin = origin
            return True
        if self.headers.get_all("Host") != [urlsplit(self.server.origin).netloc]:
            self.respond(400, page("<p>Incorrect approval host.</p>"))
            return False
        now = time.monotonic()
        while self.server.failures and self.server.failures[0] < now - 60:
            self.server.failures.popleft()
        if len(self.server.failures) >= 20:
            self.respond(429, page("<p>Too many unsuccessful logins. Retry in one minute.</p>"))
            return False
        headers = self.headers.get_all("Authorization") or []
        if len(headers) != 1 or not authenticate(headers[0], self.server.auth):
            if headers:
                self.server.failures.append(now)
            self.respond(401, page("<p>Sign in with the separate approval password.</p>"), challenge=True)
            return False
        return True

    def page(self, content: str) -> str:
        return approval_page(content, integrated=self.server.integrated)

    def request_id(self) -> str:
        prefix = "/requests/"
        value = self.path[len(prefix):] if self.path.startswith(prefix) else ""
        if not ID_PATTERN.fullmatch(value):
            raise ValueError("Request not found")
        return value

    def do_GET(self):
        if not self.authorized():
            return
        if self.path == "/favicon.svg":
            self.respond(200, render_favicon(), content_type="image/svg+xml")
            return
        try:
            if self.path == ("/approvals" if self.server.integrated else "/"):
                records = self.server.exchange({"action": "list"})["requests"]
                items = ''.join(f'<li><a href="/requests/{row["id"]}">{html.escape(row["operation"])}</a> · '
                                f'{html.escape(row["state"])} · UID {row["uid"]}</li>' for row in records)
                content = self.page('<p>Review the exact action, effects and expiry before deciding. '
                                    'Approval permits one execution attempt.</p>'
                                    + ('<ul>' + items + '</ul>' if items else '<p>No recent approval requests.</p>'))
            else:
                request = self.server.exchange({"action": "status", "id": self.request_id()})
                content = render_request(request, csrf(self.server.secret, request, self.server.auth["username"]),
                                         integrated=self.server.integrated)
            self.respond(200, content)
        except (OSError, ValueError):
            self.respond(503, self.page("<p>Request unavailable. No approval was submitted.</p>"))

    def do_POST(self):
        if not self.authorized():
            return
        request_id = None
        try:
            request_id = self.request_id()
            if self.headers.get_all("Origin") != [self.approval_origin]:
                raise ValueError("Cross-origin approval rejected")
            if (self.headers.get("Content-Type") != "application/x-www-form-urlencoded"
                or self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length") or []) != 1):
                raise ValueError("Invalid form")
            length = int(self.headers["Content-Length"])
            if not 0 < length <= 2048:
                raise ValueError("Invalid form size")
            form = parse_qs(self.rfile.read(length).decode("utf-8"), strict_parsing=True, max_num_fields=3)
            if set(form) != {"csrf", "digest", "decision"} or any(len(v) != 1 for v in form.values()):
                raise ValueError("Invalid form fields")
            request = self.server.exchange({"action": "status", "id": request_id})
            expected = csrf(self.server.secret, request, self.server.auth["username"])
            if not hmac.compare_digest(form["csrf"][0].encode(), expected.encode()) or form["decision"][0] not in {"approve", "deny"}:
                raise ValueError("Invalid decision token")
            self.server.exchange({"action": "decide", "id": request_id, "digest": form["digest"][0],
                                  "approve": form["decision"][0] == "approve", "actor": self.server.auth["username"]})
            self.respond(303, "", location="/requests/" + request_id)
        except (OSError, ValueError):
            recovery = (f'<p><a href="/requests/{request_id}">Check request status</a></p>'
                        if request_id else "")
            listing = "/approvals" if self.server.integrated else "/"
            self.respond(409, self.page("<p>Approval was not confirmed. Check its status before taking further action.</p>"
                                   + recovery + f'<p><a href="{listing}">All requests</a></p>'))


class ApprovalServer(HTTPServer):
    """Bounded sequential server: one password verification or broker call at a time."""
    def __init__(self, address, auth, origin, exchange_function=None, *, integrated=False):
        self.integrated = integrated
        self.auth = auth if integrated else validate_auth(auth)
        self.origin = origin
        self.secret = secrets.token_bytes(32)
        self.failures = deque(maxlen=20)
        self.exchange = exchange_function or (lambda message: exchange(message, APPROVAL_SOCKET))
        super().__init__(address, ApprovalHandler)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(5)
        try:
            if hasattr(self, "tls"):
                connection = self.tls.wrap_socket(connection, server_side=True)
            return connection, address
        except OSError:
            connection.close()
            raise

    def handle_error(self, request, client_address):
        print("Approval connection failed", file=sys.stderr)


class PanelApprovalServer(ApprovalServer):
    """Only Nginx's UID may forward authenticated panel requests here."""
    address_family = socket.AF_UNIX

    def __init__(self, path, origins, username, proxy_uid, proxy_gid, exchange_function=None):
        self.proxy_uid = proxy_uid
        self.origins = tuple(browser_origin(value) for value in origins)
        super().__init__(path, {"username": username}, self.origins[0], exchange_function, integrated=True)
        os.chown(path, -1, proxy_gid)
        os.chmod(path, 0o660)

    def server_bind(self):
        socketserver.UnixStreamServer.server_bind(self)


def main():
    if os.geteuid() == 0:
        raise SystemExit("Approval web service must not run as root")
    policy = load_policy()
    panel_origin = os.environ.get("BASALTWATER_PANEL_ORIGIN")
    if panel_origin:
        proxy = pwd.getpwnam("www-data")
        if os.path.lexists(PANEL_APPROVAL_SOCKET):
            os.unlink(PANEL_APPROVAL_SOCKET)
        panel = PanelApprovalServer(PANEL_APPROVAL_SOCKET,
                                    os.environ.get("BASALTWATER_PANEL_ORIGINS", panel_origin).split(","),
                                    os.environ["BASALTWATER_PANEL_USER"], proxy.pw_uid, proxy.pw_gid)
        if os.environ.get("BASALTWATER_STANDALONE", "1") == "0":
            panel.serve_forever()
            return
        threading.Thread(target=panel.serve_forever, daemon=True).start()
    credentials = os.environ["CREDENTIALS_DIRECTORY"]
    with open(os.path.join(credentials, "auth"), encoding="utf-8") as source:
        auth = json.load(source)
    standalone_origin = os.environ.get("BASALTWATER_STANDALONE_ORIGIN", policy["origin"])
    origin = urlsplit(standalone_origin)
    # Bind IPv6 dual-stack where supported so IPv4 and IPv6 origins both work.
    class DualStackServer(ApprovalServer):
        address_family = socket.AF_INET6

        def server_bind(self):
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            super().server_bind()

    if socket.has_dualstack_ipv6():
        server = DualStackServer(("::", origin.port), auth, standalone_origin)
    else:
        server = ApprovalServer(("0.0.0.0", origin.port), auth, standalone_origin)
    server.tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.tls.minimum_version = ssl.TLSVersion.TLSv1_2
    server.tls.load_cert_chain(os.path.join(credentials, "cert"), os.path.join(credentials, "key"))
    server.serve_forever()


if __name__ == "__main__":
    main()
