"""HTTP readiness probes restricted to literal loopback addresses."""

from __future__ import annotations

import ipaddress
import ssl
import urllib.error
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


def http_status_is_healthy(status: int, headers: dict[str, str]) -> bool:
    """Allow a valid authentication challenge as evidence of a live server."""
    return status < 400 or (status == 401 and bool(headers.get("www-authenticate", "").strip()))


def http_transport_issue(error: Exception) -> str:
    if isinstance(error, RuntimeError) and error.__cause__ is not None:
        error = error.__cause__
    if isinstance(error, urllib.error.URLError):
        error = error.reason
    if isinstance(error, ssl.SSLCertVerificationError):
        return "certificate_untrusted"
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, ConnectionRefusedError):
        return "connection_refused"
    return "transport_failure"


def has_corp_isolation_headers(headers: dict[str, str]) -> bool:
    return (
        headers.get("cross-origin-opener-policy") == "same-origin"
        and headers.get("cross-origin-embedder-policy") == "require-corp"
    )
