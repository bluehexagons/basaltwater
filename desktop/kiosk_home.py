"""Offline browser launch page for explicitly configured kiosk destinations."""

from __future__ import annotations

import base64
import hashlib
from html import escape
from urllib.parse import urlsplit

from common.service_tools.web_panel_templates import BRAND_PALETTES, BRAND_SYMBOL
from lib.kiosk_config import validate_kiosk_destinations, validate_kiosk_url


def render_home(url: str, destinations: list[dict[str, str]]) -> str:
    """Render links without scripts, remote assets, discovery or credentials."""
    validate_kiosk_url(url)
    validate_kiosk_destinations(destinations)

    def tokens(theme: str) -> str:
        palette = BRAND_PALETTES[theme]
        return ";".join(f"--{name}:{palette[name]}" for name in
                        ("bg", "panel", "text", "muted", "line", "accent", "accent-soft", "brand-water"))

    styles = (
        ":root{" + tokens("light") + ";color-scheme:light dark}"
        "@media(prefers-color-scheme:dark){:root{" + tokens("dark") + "}}"
        "*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);"
        'font:16px/1.55 "DejaVu Sans",system-ui,sans-serif}'
        "main{max-width:900px;margin:auto;padding:48px 28px}"
        "header{display:flex;align-items:center;gap:12px;color:var(--muted);font-weight:700}"
        "header svg{flex:none}h1{font-size:clamp(30px,5vw,44px);line-height:1.15;"
        "letter-spacing:-.035em;margin:36px 0 12px}"
        "p{color:var(--muted);margin:0 0 28px}"
        "h2{font-size:15px;margin:32px 0 12px;color:var(--muted)}"
        "ul{list-style:none;padding:0;margin:0;border-top:1px solid var(--line)}"
        "li{border-bottom:1px solid var(--line)}"
        "a{display:flex;align-items:center;gap:16px;padding:20px 16px;"
        "text-decoration:none;color:var(--text);overflow-wrap:anywhere}"
        "a:hover{background:var(--accent-soft)}a:focus-visible{outline:3px solid var(--accent);"
        "outline-offset:3px;border-radius:4px}"
        ".host{background:var(--panel);border:1px solid var(--line);border-radius:8px}"
        ".label{flex:1;min-width:0}.name{display:block;font-weight:700;font-size:18px}"
        ".address{display:block;color:var(--muted);font-size:14px;margin-top:3px}"
        ".arrow{font-size:24px;color:var(--accent)}"
        "footer{margin-top:32px;color:var(--muted);font-size:14px}"
        "kbd{font:inherit;font-size:13px;background:var(--panel);border:1px solid var(--line);"
        "border-radius:4px;padding:2px 5px;white-space:nowrap}"
        "@media(max-width:480px){main{padding:28px 16px}a{padding:16px 12px}"
        "h1{margin-top:28px}footer{line-height:1.9}}"
    )
    digest = base64.b64encode(hashlib.sha256(styles.encode()).digest()).decode("ascii")
    policy = f"default-src 'none'; style-src 'sha256-{digest}'; base-uri 'none'; form-action 'none'"

    def link(name: str, target: str, *, host: bool = False) -> str:
        # Show the authority only; queries and fragments may contain private data.
        address = urlsplit(target).netloc
        return (f'<a href="{escape(target, quote=True)}" target="_blank" rel="noopener noreferrer"'
                + (' class="host"' if host else '') + '>'
                f'<span class="label"><span class="name">{escape(name)}</span>'
                f'<span class="address">{escape(address)} · Opens in a new tab</span></span>'
                '<span class="arrow" aria-hidden="true">↗</span></a>')

    links = "".join(f'<li>{link(item["name"], item["url"])}</li>' for item in destinations)
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<meta http-equiv="Content-Security-Policy" content="{escape(policy, quote=True)}">'
        '<meta name="referrer" content="no-referrer"><title>Kiosk · Basaltwater</title>'
        f'<style>{styles}</style></head><body><main>'
        f'<header>{BRAND_SYMBOL}<span>Basaltwater / Local kiosk</span></header>'
        '<h1>Choose your workspace</h1>'
        '<p>Open your home page or one of your saved destinations.</p>'
        + link("Home page", url, host=True)
        + '<section aria-labelledby="destinations"><h2 id="destinations">Saved destinations</h2>'
        f'<ul>{links}</ul></section>'
        '<footer>Keep this tab open to return to your destinations.<br>'
        'Host console: <kbd>Ctrl</kbd> + <kbd>Alt</kbd> + <kbd>F1</kbd>'
        ' · Return here: <kbd>Ctrl</kbd> + <kbd>Alt</kbd> + <kbd>F7</kbd></footer>'
        '</main></body></html>\n'
    )
