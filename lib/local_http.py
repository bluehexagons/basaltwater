"""HTTP readiness probes restricted to literal loopback addresses."""

from __future__ import annotations

import ipaddress
import urllib.parse
import urllib.request


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def open_loopback(request: str | urllib.request.Request, *, timeout: float):
    url = request if isinstance(request, str) else request.full_url
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme not in ("http", "https")
        or parsed.username is not None
        or parsed.password is not None
        or not ipaddress.ip_address(parsed.hostname or "").is_loopback
    ):
        raise ValueError("Readiness URL must use a literal loopback address")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    return opener.open(request, timeout=timeout)
