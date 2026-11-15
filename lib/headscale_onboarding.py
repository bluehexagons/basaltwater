"""Static Headscale onboarding page rendering and safe URL validation."""

from __future__ import annotations

from html import escape
from urllib.parse import urlsplit

from lib.headscale_config import validate_headscale_url
from lib.validators import validate_host


def onboarding_url(domain: str) -> str:
    if not validate_host(domain) or "." not in domain.rstrip("."):
        raise ValueError("Onboarding domain must be a DNS hostname")
    return "https://" + domain.lower().rstrip(".") + "/"


def resolve_control_url(config: object) -> str:
    value = getattr(config, "tailscale_login_server", None) or getattr(config, "headscale_domain", None)
    if not value:
        raise ValueError("A trusted Headscale HTTPS endpoint is required")
    return validate_headscale_url(value if "://" in value else "https://" + value)


def render_onboarding_message(control_url: str) -> str:
    return f"Use the official Tailscale app and select this custom control server: {escape(validate_headscale_url(control_url))}"


def render_onboarding_html(control_url: str, page_url: str) -> str:
    control = escape(validate_headscale_url(control_url), quote=True)
    parsed = urlsplit(onboarding_url(urlsplit(page_url).hostname or ""))
    canonical_page = f"https://{parsed.netloc}/"
    page = escape(canonical_page, quote=True)
    command = escape(f"tailscale login --login-server={validate_headscale_url(control_url)}")
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><title>Join this private network</title><link rel="stylesheet" href="/style.css"></head><body><main><header><p class="eyebrow">PRIVATE NETWORK · DEVICE SETUP</p><h1>Join this network</h1><p>Install the official Tailscale app, choose the custom server below, then ask your network administrator to approve your device.</p></header><section><h2>1. Install the official app</h2><p><a href="https://tailscale.com/download">Download Tailscale from the official site</a>. Mobile users can also find Tailscale in the Apple App Store or Google Play.</p></section><section><h2>2. Select the custom server</h2><p>In the app, choose <strong>Use an alternate server</strong> or <strong>Custom control server</strong>, then enter:</p><code>{control}</code><p>On Linux, run this command after installing the official client:</p><pre><code>{command}</code></pre></section><section><h2>3. Ask your administrator to approve</h2><p>Finish the sign-in flow and send the pending Auth ID to your administrator through your established private channel. The administrator must verify it is yours and approve that exact request. Approval adds this device to the network's shared trust boundary.</p></section><section><h2>Before you connect</h2><p>Network administrators can expire devices. Device expiry is separate from the one-hour lifetime of an optional, single-use machine setup key. This page does not issue keys or approve devices.</p><p>To opt out of Tailscale startup logging, configure <code>--no-logs-no-support</code> before first startup where supported. On Linux, pass it to <code>tailscaled</code>; on Windows use the service configuration; macOS and mobile app support varies by client variant. Verify the current official platform guidance before relying on this setting.</p></section><footer><p>Instructions: <a href="{page}">{page}</a></p><img src="/onboarding-qr.png" width="160" height="160" alt="QR code linking to this onboarding page"></footer></main></body></html>'''


def render_stylesheet() -> str:
    return """*{box-sizing:border-box}body{margin:0;background:#f3f6f4;color:#17251f;font:17px/1.6 system-ui,sans-serif}main{max-width:760px;margin:0 auto;padding:2rem 1.2rem 4rem}header{padding:2.2rem 0 1.4rem;border-bottom:1px solid #cad5ce}.eyebrow{color:#386b52;font-size:.78rem;font-weight:700;letter-spacing:.12em}h1{font-size:clamp(2.5rem,8vw,4.2rem);line-height:1.05;margin:.5rem 0 1rem}section{padding:1rem 1.25rem;margin:1rem 0;background:white;border:1px solid #dce4df;border-radius:12px}h2{font-size:1.2rem}a{color:#145f43;text-decoration-thickness:2px}a:focus-visible{outline:3px solid #e49e38;outline-offset:3px}code,pre{overflow-wrap:anywhere}code{background:#edf2ee;padding:.18rem .35rem;border-radius:4px}pre{white-space:pre-wrap;background:#17251f;color:white;padding:1rem;border-radius:8px}footer{display:flex;justify-content:space-between;align-items:center;gap:1rem;margin-top:2rem}img{max-width:40vw;height:auto}@media(max-width:520px){footer{align-items:flex-start;flex-direction:column}}"""
