"""Static Headscale onboarding page rendering and safe URL validation."""

from __future__ import annotations

from html import escape
import ipaddress
from urllib.parse import urlsplit

from lib.headscale_config import validate_headscale_url
from lib.validators import validate_host


def onboarding_url(domain: str) -> str:
    if not validate_host(domain) or "." not in domain.rstrip("."):
        raise ValueError("Onboarding domain must be a DNS hostname")
    try:
        ipaddress.ip_address(domain.rstrip("."))
    except ValueError:
        return "https://" + domain.lower().rstrip(".") + "/"
    raise ValueError("Onboarding domain must not be an IP address")


def resolve_control_url(config: object) -> str:
    value = (getattr(config, "headscale_domain", None) if getattr(config, "headscale", False)
             else (getattr(config, "headscale_onboarding_control_url", None)
                   or getattr(config, "tailscale_login_server", None)))
    if not value:
        raise ValueError("A trusted Headscale HTTPS endpoint is required")
    return validate_headscale_url("https://" + value if getattr(config, "headscale", False) and "://" not in value else value)


def render_onboarding_message(control_url: str) -> str:
    return f"Use the official Tailscale app and select this custom control server: {escape(validate_headscale_url(control_url))}"


def render_onboarding_html(control_url: str, page_url: str) -> str:
    control_url = validate_headscale_url(control_url)
    control = escape(control_url, quote=True)
    canonical_page = onboarding_url(urlsplit(page_url).hostname or "")
    if page_url != canonical_page or urlsplit(control_url).hostname == urlsplit(page_url).hostname:
        raise ValueError("Use a canonical, separate HTTPS onboarding origin")
    page = escape(canonical_page, quote=True)
    command = escape(f"tailscale login --login-server={validate_headscale_url(control_url)}")
    platforms = f'''<section><h2>Windows</h2><p>Install the <a href="https://tailscale.com/download/windows">official Windows client</a>. In PowerShell or Command Prompt run:</p><pre><code>{command}</code></pre><p>For startup log-upload opt-out, an administrator sets <code>TS_NO_LOGS_NO_SUPPORT=true</code> in <code>%ProgramData%\\Tailscale\\tailscaled-env.txt</code> before starting the Tailscale service; restart an existing service after changing it.</p></section>
<section><h2>macOS</h2><p>Install an <a href="https://tailscale.com/download/mac">official macOS client</a>. Option-click its menu-bar icon, open Debug → Custom Login Server → Add Account, and enter <code>{control}</code>. Follow the browser flow. The CLI also accepts:</p><pre><code>{command}</code></pre><p>The logging daemon flag works only with the open-source command-line tailscaled variant, not the Standalone or App Store app. For that daemon, set <code>TS_NO_LOGS_NO_SUPPORT=true</code> in its launch environment before startup.</p></section>
<section><h2>Linux</h2><p>Follow the <a href="https://tailscale.com/docs/install/linux">official distribution installation instructions</a>. Before starting tailscaled, configure its service environment with <code>TS_NO_LOGS_NO_SUPPORT=true</code> (for systemd, use <code>sudo systemctl edit tailscaled</code> and add <code>[Service]</code> followed by <code>Environment=TS_NO_LOGS_NO_SUPPORT=true</code>). Reload systemd and restart tailscaled, then run:</p><pre><code>sudo {command}</code></pre></section>
<section><h2>iOS</h2><p>Install Tailscale from the <a href="https://tailscale.com/download/ios">official App Store link</a>. Open the account icon → Log in → top-right options → Use custom coordination server. Enter <code>{control}</code> and follow browser registration.</p></section>
<section><h2>Android</h2><p>Install Tailscale from the <a href="https://tailscale.com/download/android">official Android download link</a>. Open Settings → Accounts → three-dot menu → Use an alternate server. Enter <code>{control}</code> and follow browser registration.</p><p>Upstream documents no equivalent pre-start daemon flag for the iOS or Android apps. Do not assume changing servers disables all third-party traffic. Ask your administrator about the managed server logtail policy before connecting.</p></section>'''
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><title>Join this private network</title><link rel="stylesheet" href="/style.css"></head><body><main><header><p class="eyebrow">PRIVATE NETWORK · DEVICE SETUP</p><h1>Join this network</h1><p>Install the official Tailscale app, choose the custom server below, then ask your network administrator to approve your device.</p></header>{platforms}<section><h2>3. Ask your administrator to approve</h2><p>Finish the sign-in flow and send the pending Auth ID to your administrator through your established private channel. The administrator must verify it is yours and approve that exact request. While pending, wait for verified administrator approval. If the request expires, start native login again. If denied, contact your administrator; do not use another identity to bypass the decision. Approval adds this device to the network's shared trust boundary.</p></section><section><h2>Before you connect</h2><p>Network administrators can expire or revoke devices. For offboarding or a lost device, ask them to revoke the registered device and confirm loss of access; deleting a bootstrap key does not remove a connected device. Device expiry is separate from the one-hour lifetime of an optional, single-use machine setup key. This page does not issue keys or approve devices.</p><p>Disabling log upload reduces diagnostic support. See <a href="https://tailscale.com/docs/features/logging">official logging guidance</a>.</p></section><footer><p>Instructions: <a href="{page}">{page}</a></p><img src="/onboarding-qr.png" width="160" height="160" alt="QR code linking to this onboarding page"></footer></main></body></html>'''


def render_stylesheet() -> str:
    return """*{box-sizing:border-box}body{overflow-wrap:anywhere;margin:0;background:#f3f6f4;color:#17251f;font:17px/1.6 system-ui,sans-serif}main{max-width:760px;margin:0 auto;padding:2rem 1.2rem 4rem}header{padding:2.2rem 0 1.4rem;border-bottom:1px solid #cad5ce}.eyebrow{color:#386b52;font-size:.78rem;font-weight:700;letter-spacing:.12em}h1{font-size:clamp(2.5rem,8vw,4.2rem);line-height:1.05;margin:.5rem 0 1rem}section{padding:1rem 1.25rem;margin:1rem 0;background:white;border:1px solid #dce4df;border-radius:12px}h2{font-size:1.2rem}a{color:#145f43;text-decoration-thickness:2px}a:focus-visible{outline:3px solid #e49e38;outline-offset:3px}code,pre{overflow-wrap:anywhere}code{background:#edf2ee;padding:.18rem .35rem;border-radius:4px}pre{white-space:pre-wrap;background:#17251f;color:white;padding:1rem;border-radius:8px}footer{display:flex;justify-content:space-between;align-items:center;gap:1rem;margin-top:2rem}img{max-width:40vw;height:auto}@media(max-width:520px){footer{align-items:flex-start;flex-direction:column}}"""
