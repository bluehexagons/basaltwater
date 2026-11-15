"""Reconcile a read-only static Headscale onboarding vhost."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from lib.atomic_io import write_bytes_atomic, write_json_atomic
from lib.config import SetupConfig
from lib.headscale_onboarding import onboarding_url, render_onboarding_html, render_stylesheet, resolve_control_url
from lib.operation_state import OperationStateStore
from lib.remote_utils import is_dry_run, run

ROOT = Path("/var/www/basaltwater-headscale-onboarding")
SITE = Path("/etc/nginx/sites-available/basaltwater-headscale-onboarding")
ENABLED = Path("/etc/nginx/sites-enabled/basaltwater-headscale-onboarding")
MARKER = Path("/var/lib/basaltwater/headscale-onboarding-recovery.json")


def _vhost(domain: str, *, tls: bool) -> str:
    if tls:
        return f'''server {{ listen 443 ssl; server_name {domain}; root {ROOT}; index index.html; access_log off; add_header Strict-Transport-Security "max-age=31536000" always; add_header Content-Security-Policy "default-src 'none'; style-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'" always; add_header X-Frame-Options DENY always; add_header X-Content-Type-Options nosniff always; add_header Referrer-Policy no-referrer always; location / {{ limit_req zone=onboarding burst=20 nodelay; try_files $uri =404; }} }}'''
    return f"server {{ listen 80; server_name {domain}; location /.well-known/acme-challenge/ {{ root /var/www/html; }} location / {{ return 301 https://$host$request_uri; }} }}"


def configure_headscale_onboarding(config: SetupConfig) -> None:
    domain = getattr(config, "headscale_onboarding_domain", None)
    if not domain:
        return
    page_url = onboarding_url(domain)
    control = resolve_control_url(config)
    if control.split("//", 1)[1].split(":", 1)[0].rstrip("/") == domain.lower().rstrip("."):
        raise ValueError("Onboarding and Headscale control origins must differ")
    if config.dry_run or is_dry_run():
        print(f"  [DRY-RUN] Would reconcile static onboarding at {page_url}; restrict routing if LAN-only access is intended")
        return
    run(["apt-get", "update"], capture_output=True)
    run(["apt-get", "install", "-y", "nginx", "certbot", "qrencode"], capture_output=True)
    lock = OperationStateStore("/etc/nginx/.basaltwater-nginx-operation.json")
    with lock.ownership():
        if lock.load() is not None:
            raise RuntimeError("Unfinished Nginx operation requires recovery")
        ROOT.mkdir(parents=True, exist_ok=True, mode=0o755)
        with tempfile.TemporaryDirectory(prefix="headscale-onboarding-") as temp:
            qr = Path(temp) / "qr.png"
            subprocess.run(["qrencode", "-o", str(qr), "-t", "PNG"], input=page_url.encode(), check=True, capture_output=True)
            write_bytes_atomic(str(ROOT / "index.html"), render_onboarding_html(control, page_url).encode(), mode=0o644)
            write_bytes_atomic(str(ROOT / "style.css"), render_stylesheet().encode(), mode=0o644)
            write_bytes_atomic(str(ROOT / "onboarding-qr.png"), qr.read_bytes(), mode=0o644)
        old = SITE.read_bytes() if SITE.exists() else None
        linked = ENABLED.is_symlink() and ENABLED.resolve() == SITE
        try:
            write_bytes_atomic(str(SITE), _vhost(domain, tls=False).encode(), mode=0o644)
            ENABLED.symlink_to(SITE)
            run(["nginx", "-t"], capture_output=True)
            run(["systemctl", "reload", "nginx"])
            run(["certbot", "certonly", "--nginx", "--non-interactive", "--agree-tos", "-m", config.ssl_email, "-d", domain], capture_output=True)
            write_bytes_atomic(str(SITE), _vhost(domain, tls=True).encode(), mode=0o644)
            run(["nginx", "-t"], capture_output=True)
            run(["systemctl", "reload", "nginx"])
        except BaseException:
            MARKER.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            write_json_atomic(str(MARKER), {"site": str(SITE), "domain": domain}, mode=0o600)
            if old is None:
                SITE.unlink(missing_ok=True)
            else:
                write_bytes_atomic(str(SITE), old, mode=0o644)
            if not linked:
                ENABLED.unlink(missing_ok=True)
            run(["nginx", "-t"], capture_output=True)
            run(["systemctl", "reload", "nginx"])
            raise
    print(f"  Onboarding page: {page_url} (LAN-only routing only reaches devices on that LAN)")
