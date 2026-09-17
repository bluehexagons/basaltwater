#!/usr/bin/env python3
"""Independent HTTPS approval page, running as a dedicated non-root identity."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import html
import json
import os
import secrets
import socket
import ssl
import sys
import time
from urllib.parse import parse_qs, urlsplit

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, "/opt/infra_tools")

from lib.privilege_auth import authenticate, validate_auth
from lib.privilege_client import exchange
from lib.privilege_policy import APPROVAL_SOCKET, ID_PATTERN, canonical, load_policy

STYLE = """
body{font:17px system-ui,sans-serif;color:#172a3a;background:#eef2f5;margin:0}
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
            '<title>Privilege approvals · infra-tools</title><style>' + STYLE + '</style>'
            '<main><h1>Privilege approvals</h1>' + content +
            '<footer>Approve from your own device. Keep this password out of the agent VM’s browser and chat.</footer></main></html>')


def csrf(secret: bytes, request: dict, username: str) -> str:
    return hmac.new(secret, canonical([request["id"], request["digest"], username]).encode(), hashlib.sha256).hexdigest()


def render_request(request: dict, token: str) -> str:
    escape = lambda value: html.escape(str(value), quote=True)
    plan = request["plan"]
    expires = datetime.fromtimestamp(request["expires"], timezone.utc).isoformat()
    fields = [("Machine identity", plan["machine"]), ("Requesting account UID", plan["uid"]),
              ("Operation", plan["operation"]), ("Command", canonical(plan["argv"])), ("Parameters", canonical(plan["parameters"])),
              ("Expected effects", plan["effect"]), ("Expires (UTC)", expires),
              ("Status", request["state"])]
    content = '<p><a href="/">All requests</a></p><dl>'
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
    return page(content)


class ApprovalHandler(BaseHTTPRequestHandler):
    server_version = "infra-tools-approval"
    sys_version = ""

    def log_message(self, format, *args):
        # Never log Authorization, request bodies, or URLs containing request IDs.
        pass

    def setup(self):
        self.request.settimeout(5)
        super().setup()

    def respond(self, status: int, content: str, *, challenge: bool = False, location: str | None = None):
        payload = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        # no-referrer makes Chromium send Origin: null on form POSTs.
        # Preserve same-origin form provenance without leaking URLs cross-origin.
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.send_header("Connection", "close")
        if challenge:
            self.send_header("WWW-Authenticate", 'Basic realm="infra-tools privilege approvals", charset="UTF-8"')
        if location:
            self.send_header("Location", location)
        self.end_headers()
        self.wfile.write(payload)
        self.close_connection = True

    def authorized(self) -> bool:
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

    def request_id(self) -> str:
        prefix = "/requests/"
        value = self.path[len(prefix):] if self.path.startswith(prefix) else ""
        if not ID_PATTERN.fullmatch(value):
            raise ValueError("Request not found")
        return value

    def do_GET(self):
        if not self.authorized():
            return
        try:
            if self.path == "/":
                records = self.server.exchange({"action": "list"})["requests"]
                items = ''.join(f'<li><a href="/requests/{row["id"]}">{html.escape(row["operation"])}</a> · '
                                f'{html.escape(row["state"])} · UID {row["uid"]}</li>' for row in records)
                content = page('<p>' + html.escape(self.server.origin) + '</p><p>Recent agent requests. Select a request to review its effects.</p><ul>' + items + '</ul>')
            else:
                request = self.server.exchange({"action": "status", "id": self.request_id()})
                content = render_request(request, csrf(self.server.secret, request, self.server.auth["username"]))
            self.respond(200, content)
        except (OSError, ValueError):
            self.respond(503, page("<p>Request unavailable. No approval was submitted.</p>"))

    def do_POST(self):
        if not self.authorized():
            return
        try:
            if self.headers.get_all("Origin") != [self.server.origin]:
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
            request_id = self.request_id()
            request = self.server.exchange({"action": "status", "id": request_id})
            expected = csrf(self.server.secret, request, self.server.auth["username"])
            if not hmac.compare_digest(form["csrf"][0].encode(), expected.encode()) or form["decision"][0] not in {"approve", "deny"}:
                raise ValueError("Invalid decision token")
            self.server.exchange({"action": "decide", "id": request_id, "digest": form["digest"][0],
                                  "approve": form["decision"][0] == "approve", "actor": self.server.auth["username"]})
            self.respond(303, "", location="/requests/" + request_id)
        except (OSError, ValueError):
            self.respond(409, page("<p>Approval was not confirmed. Reload the request to check its status before taking further action.</p>"))


class ApprovalServer(HTTPServer):
    """Bounded sequential server: one password verification or broker call at a time."""
    def __init__(self, address, auth, origin, exchange_function=None):
        self.auth = validate_auth(auth)
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


def main():
    if os.geteuid() == 0:
        raise SystemExit("Approval web service must not run as root")
    policy = load_policy()
    credentials = os.environ["CREDENTIALS_DIRECTORY"]
    with open(os.path.join(credentials, "auth"), encoding="utf-8") as source:
        auth = json.load(source)
    origin = urlsplit(policy["origin"])
    # Bind IPv6 dual-stack where supported so IPv4 and IPv6 origins both work.
    class DualStackServer(ApprovalServer):
        address_family = socket.AF_INET6

        def server_bind(self):
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            super().server_bind()

    if socket.has_dualstack_ipv6():
        server = DualStackServer(("::", origin.port), auth, policy["origin"])
    else:
        server = ApprovalServer(("0.0.0.0", origin.port), auth, policy["origin"])
    server.tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.tls.minimum_version = ssl.TLSVersion.TLSv1_2
    server.tls.load_cert_chain(os.path.join(credentials, "cert"), os.path.join(credentials, "key"))
    server.serve_forever()


if __name__ == "__main__":
    main()
