"""Owned, recoverable publication of isolated static onboarding instructions."""

from __future__ import annotations

import base64
import hashlib
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from lib.atomic_io import read_json_file, write_bytes_atomic, write_json_atomic, remove_file_durable
from lib.config import SetupConfig
from lib.headscale_onboarding import onboarding_url, render_onboarding_html, render_stylesheet, resolve_control_url
from lib.machine_state import can_manage_system_services
from lib.operation_state import OperationStateStore
from lib.remote_utils import get_os_id, is_dry_run, run
from lib.validation import validate_filesystem_path, validate_network_ip_or_cidr
from web.ssl_steps import CERTBOT_NGINX_DEPLOY_HOOK, obtain_letsencrypt_certificate, setup_certificate_renewal

ROOT = Path("/var/www/basaltwater-headscale-onboarding")
SITE = Path("/etc/nginx/sites-available/basaltwater-headscale-onboarding")
ENABLED = Path("/etc/nginx/sites-enabled/basaltwater-headscale-onboarding")
MARKER = Path("/var/lib/basaltwater/headscale-onboarding-recovery.json")
MANIFEST = Path("/var/lib/basaltwater/headscale-onboarding.json")
LOCK = Path("/etc/nginx/.basaltwater-nginx-operation.json")
RENEWAL_HOOK = Path(CERTBOT_NGINX_DEPLOY_HOOK)
ASSETS = ("index.html", "style.css", "onboarding-qr.png")
ZONE = "basaltwater_headscale_onboarding"


def preflight_onboarding(config: SetupConfig) -> None:
    """Admit a static web host without requiring TUN or firewall capability."""
    page = onboarding_url(config.headscale_onboarding_domain)
    control = resolve_control_url(config)
    render_onboarding_html(control, page)
    _validate_suffix(config, control, page)
    tuple(validate_network_ip_or_cidr(s) for s in config.effective_access_sources())
    if config.dry_run or is_dry_run():
        return
    if (not can_manage_system_services(config.machine_type) or os.geteuid() != 0
            or get_os_id() != "debian" or not shutil.which("apt-get")):
        raise ValueError("Onboarding requires Debian, APT, root and system service capability")


def _safe(path: Path, *, link: bool = False) -> None:
    validate_filesystem_path(str(path), must_exist=False)
    for ancestor in reversed(path.parents):
        try:
            info = ancestor.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise ValueError(f"Unsafe onboarding ancestor: {ancestor}")
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if info.st_uid != os.geteuid() or (not link and info.st_mode & 0o022):
            raise ValueError(f"Unsafe onboarding ownership: {path}")
        if link:
            if not path.is_symlink() or os.readlink(path) != str(SITE):
                raise ValueError("Onboarding enabled link requires adoption/review")
        elif not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise ValueError(f"Unsafe onboarding path: {path}")


def _files() -> list[Path]:
    return [SITE, *(ROOT / name for name in ASSETS), MANIFEST]


def _ownership(domain: str) -> None:
    for path in (ROOT, SITE, ENABLED, MANIFEST, MARKER, LOCK, RENEWAL_HOOK,
                 Path(str(LOCK) + ".lock"), Path(str(LOCK) + ".last.json")):
        _safe(path, link=path == ENABLED)
    if MARKER.exists():
        raise RuntimeError("Onboarding snapshot requires recovery/review")
    present = ROOT.exists() or SITE.exists() or ENABLED.is_symlink()
    if not MANIFEST.exists():
        if present:
            raise ValueError("Unknown onboarding installation requires adoption/review")
        return
    manifest = read_json_file(str(MANIFEST))
    if not isinstance(manifest, dict) or manifest.get("version") != 1 or manifest.get("domain") != domain:
        raise ValueError("Onboarding ownership manifest mismatch; review required")
    if not ROOT.is_dir() or {p.name for p in ROOT.iterdir()} != set(ASSETS):
        raise ValueError("Unexpected onboarding content requires adoption/review")
    for path in _files()[:-1]:
        _safe(path)
        if not path.is_file() or manifest.get("hashes", {}).get(str(path)) != hashlib.sha256(path.read_bytes()).hexdigest():
            raise ValueError("Modified onboarding content requires adoption/review")
    if not ENABLED.is_symlink():
        raise ValueError("Missing onboarding link requires adoption/review")


def _collisions(domain: str) -> None:
    result = run(["nginx", "-T"], capture_output=True)
    chunks = re.split(r"(?m)^# configuration file (.*):\n", result.stdout)
    sections = [("", chunks[0]), *zip(chunks[1::2], chunks[2::2])]
    for owner, content in sections:
        clean = "\n".join(line.split("#", 1)[0] for line in content.splitlines())
        for names in re.findall(r"\bserver_name\s+([^;]+);", clean):
            if owner in {str(SITE), str(ENABLED)}:
                continue
            for name in names.split():
                name = name.strip('"\'').lower().rstrip(".")
                suffix = name.lstrip("*.")
                if (name == domain or name.startswith("~") or name.startswith(("*.", "."))
                        and (domain == suffix or domain.endswith("." + suffix))
                        or name.endswith(".*") and domain.startswith(name[:-1])):
                    raise ValueError("Onboarding hostname collides with active Nginx configuration")
        if owner not in {str(SITE), str(ENABLED)} and re.search(r"\bzone=" + ZONE + r"\b", clean):
            raise ValueError("Onboarding rate zone collides with active Nginx configuration")


def _vhost(domain: str, *, tls: bool, sources: tuple[str, ...] = ()) -> str:
    domain = urlsplit(onboarding_url(domain)).hostname
    acl = "\n".join(f"allow {validate_network_ip_or_cidr(s)};" for s in sources)
    if sources:
        acl += "\ndeny all;"
    common = f'''server_name {domain}; root {ROOT}; access_log off; error_log /dev/null;
if ($host != {domain}) {{ return 444; }}
{acl}
'''
    http = f'''server {{ listen 80; listen [::]:80; {common}
location ^~ /.well-known/acme-challenge/ {{ allow all; root /var/www/letsencrypt; }}
location / {{ try_files /__basaltwater_redirect_missing__ @onboarding_https; }}
location @onboarding_https {{ return 301 https://{domain}/; }}
}}\n'''
    if not tls:
        return http
    return http + f'''limit_req_zone $binary_remote_addr zone={ZONE}:10m rate=5r/s;
server {{ listen 443 ssl; listen [::]:443 ssl; {common}
ssl_certificate /etc/letsencrypt/live/{domain}/fullchain.pem;
ssl_certificate_key /etc/letsencrypt/live/{domain}/privkey.pem;
disable_symlinks on;
add_header Strict-Transport-Security "max-age=31536000" always;
add_header Content-Security-Policy "default-src 'none'; style-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'" always;
add_header X-Frame-Options DENY always;
add_header X-Content-Type-Options nosniff always;
add_header Referrer-Policy no-referrer always;
limit_req zone={ZONE} burst=20 nodelay;
location = / {{ limit_except GET {{ deny all; }} try_files /index.html =404; }}
location = /style.css {{ limit_except GET {{ deny all; }} try_files $uri =404; }}
location = /onboarding-qr.png {{ limit_except GET {{ deny all; }} try_files $uri =404; }}
location / {{ return 404; }}
}}\n'''


def _activate(active: bool) -> None:
    run(["nginx", "-t"], capture_output=True)
    run(["systemctl", "reload" if active else "start", "nginx"])


def _validate_suffix(config: SetupConfig, control: str, page: str) -> None:
    suffix = (getattr(config, "headscale_base_domain", None) or "").lower().rstrip(".")
    for host in (urlsplit(page).hostname, urlsplit(control).hostname):
        if suffix and (host == suffix or host.endswith("." + suffix) or suffix.endswith("." + host)):
            raise ValueError("MagicDNS suffix collides with public hostname")


def configure_headscale_onboarding(config: SetupConfig) -> None:
    """Publish only owned paths, retaining a blocking snapshot on uncertainty."""
    domain = getattr(config, "headscale_onboarding_domain", None)
    if not domain:
        return
    page = onboarding_url(domain)
    domain = urlsplit(page).hostname
    control = resolve_control_url(config)
    html = render_onboarding_html(control, page).encode()
    _validate_suffix(config, control, page)
    sources = tuple(validate_network_ip_or_cidr(s) for s in config.effective_access_sources())
    if config.dry_run or is_dry_run():
        print(f"  [DRY-RUN] Would publish static onboarding at {page}")
        return
    preflight_onboarding(config)
    _ownership(domain)
    with OperationStateStore(str(LOCK)).ownership() as lock:
        if lock.load() is not None:
            raise RuntimeError("Unfinished Nginx operation requires recovery")
        _ownership(domain)
        _collisions(domain)
        active = run(["systemctl", "is-active", "--quiet", "nginx"], check=False).returncode == 0
        timer_active = run(["systemctl", "is-active", "--quiet", "certbot.timer"], check=False).returncode == 0
        timer_enabled = run(["systemctl", "is-enabled", "--quiet", "certbot.timer"], check=False).returncode == 0
        snapshot_paths = [*_files(), RENEWAL_HOOK]
        snapshot = {str(p): {"data": base64.b64encode(p.read_bytes()).decode(),
                               "mode": stat.S_IMODE(p.stat().st_mode), "uid": p.stat().st_uid,
                               "gid": p.stat().st_gid} if p.exists() else None for p in snapshot_paths}
        state = {"version": 1, "files": snapshot, "linked": ENABLED.is_symlink(),
                 "root_existed": ROOT.exists(), "nginx_active": active,
                 "timer_active": timer_active, "timer_enabled": timer_enabled}
        renewal_started = False
        _mkdirs(MARKER.parent, mode=0o700)
        record = lock.begin("headscale-onboarding", domain, "snapshot", context={"snapshot": str(MARKER)})
        try:
            write_json_atomic(str(MARKER), state, mode=0o600)
            run(["apt-get", "install", "-y", "qrencode"], capture_output=True)
            qr = subprocess.run(["qrencode", "-o", "-", "-t", "PNG"], input=page.encode(), check=True, capture_output=True).stdout
            candidate = {"index.html": html, "style.css": render_stylesheet().encode(), "onboarding-qr.png": qr}
            _mkdirs(ROOT, mode=0o755)
            for name, content in candidate.items():
                write_bytes_atomic(str(ROOT / name), content, mode=0o644)
            write_bytes_atomic(str(SITE), _vhost(domain, tls=False, sources=sources).encode(), mode=0o644)
            if not ENABLED.is_symlink():
                ENABLED.symlink_to(SITE)
                _sync_enabled()
            lock.transition(record.operation_id, "http-validation")
            _activate(active)
            if not obtain_letsencrypt_certificate([domain], config.ssl_email):
                raise RuntimeError("Onboarding requires a valid trusted certificate")
            write_bytes_atomic(str(SITE), _vhost(domain, tls=True, sources=sources).encode(), mode=0o644)
            lock.transition(record.operation_id, "tls-validation")
            _activate(True)
            renewal_started = True
            setup_certificate_renewal()
            write_json_atomic(str(MANIFEST), {"version": 1, "domain": domain,
                              "hashes": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in _files()[:-1]}}, mode=0o600)
        except BaseException:
            try:
                for path in snapshot_paths:
                    previous = snapshot[str(path)]
                    if previous is None:
                        remove_file_durable(str(path))
                    else:
                        write_bytes_atomic(str(path), base64.b64decode(previous["data"]), mode=previous["mode"], uid=previous["uid"], gid=previous["gid"])
                if not state["linked"]:
                    remove_file_durable(str(ENABLED))
                if not state["root_existed"] and ROOT.exists():
                    ROOT.rmdir()
                    _sync_directory(ROOT.parent)
                run(["nginx", "-t"], capture_output=True)
                run(["systemctl", "reload" if active else "stop", "nginx"])
                if renewal_started:
                    run(["systemctl", "enable" if timer_enabled else "disable", "certbot.timer"])
                    run(["systemctl", "start" if timer_active else "stop", "certbot.timer"])
            except BaseException as rollback_error:
                lock.transition(record.operation_id, "rollback-uncertain", status="recovery_required")
                raise RuntimeError(f"Onboarding rollback uncertain; retain {MARKER} and {LOCK} for recovery") from rollback_error
            lock.complete(record.operation_id, outcome="rolled_back", keep_ownership=True)
            remove_file_durable(str(MARKER))
            raise
        lock.complete(record.operation_id, keep_ownership=True)
        remove_file_durable(str(MARKER))
    print(f"  Onboarding page: {page}; source-restricted pages require pre-join network access")


def _sync_enabled() -> None:
    _sync_directory(ENABLED.parent)


def _mkdirs(path: Path, *, mode: int) -> None:
    missing = []
    while not path.exists():
        missing.append(path)
        path = path.parent
    for directory in reversed(missing):
        directory.mkdir(mode=mode)
        directory.chmod(mode)
        _sync_directory(directory.parent)


def _sync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
