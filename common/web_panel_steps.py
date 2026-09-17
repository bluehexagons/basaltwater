"""Install the optional authenticated infra-tools web panel."""

from __future__ import annotations

import grp
import os
import pwd
import re
import secrets
import shutil
import tempfile
import time
from typing import Any

from common.web_panel_events import (
    WEB_PANEL_AUDIT_DIR,
    WEB_PANEL_AUDIT_SNAPSHOT,
    WEB_PANEL_DATA_DIR,
    WEB_PANEL_INGEST_TOKEN,
    WEB_PANEL_NOTIFICATION_DIR,
    WEB_PANEL_NOTIFICATION_ENDPOINT,
    WEB_PANEL_NOTIFICATION_LOG,
    load_ingest_token,
)
from lib.atomic_io import write_json_atomic, write_text_atomic
from lib.auth_failure_bans import (
    configure_nginx_auth_failure_ban,
    remove_nginx_auth_failure_ban,
)
from lib.config import SetupConfig
from lib.remote_utils import install_package, is_dry_run, is_service_active, run
from lib.validation import validate_filesystem_path
from lib.validators import validate_username


WEB_PANEL_SERVICE_NAME = "infra-tools-web-panel"
WEB_PANEL_SERVICE_FILE = f"/etc/systemd/system/{WEB_PANEL_SERVICE_NAME}.service"
WEB_PANEL_AUDIT_SERVICE_NAME = "infra-tools-web-panel-audit"
WEB_PANEL_AUDIT_SERVICE_FILE = (
    f"/etc/systemd/system/{WEB_PANEL_AUDIT_SERVICE_NAME}.service"
)
WEB_PANEL_AUDIT_TIMER_FILE = (
    f"/etc/systemd/system/{WEB_PANEL_AUDIT_SERVICE_NAME}.timer"
)
WEB_PANEL_CONFIG_DIR = "/etc/infra-tools/web-panel"
WEB_PANEL_MANIFEST = f"{WEB_PANEL_CONFIG_DIR}/config.json"
WEB_PANEL_AUTH_FILE = f"{WEB_PANEL_CONFIG_DIR}/htpasswd"
WEB_PANEL_PAYLOAD_FILE = "/opt/infra_tools/web_panel_payload/htpasswd"
WEB_PANEL_SOCKET = "/run/infra-tools-web-panel/http.sock"
WEB_PANEL_SCRIPT = "/opt/infra_tools/common/service_tools/web_panel_service.py"
WEB_PANEL_AUDIT_SCRIPT = (
    "/opt/infra_tools/common/service_tools/web_panel_audit_export.py"
)
WEB_PANEL_NGINX_SITE = "/etc/nginx/sites-available/infra-tools-web-panel"
WEB_PANEL_NGINX_LINK = "/etc/nginx/sites-enabled/infra-tools-web-panel"
WEB_PANEL_AUTH_FAILURE_LOG = "/var/log/nginx/infra-tools-web-panel-auth-failures.log"
_NGINX_MARKER = "# Managed by infra_tools web panel"
_SERVICE_MARKER = "# Managed by infra_tools web panel"
_INGEST_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,256}$")
_WEB_PANEL_SYSTEM_USER = "infra-web-panel"


def _url_host(host: str) -> str:
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


def web_panel_url(config: SetupConfig) -> str | None:
    """Return the externally useful web panel URL for summaries."""

    if config.web_panel_port is None:
        return None
    scheme = "https" if config.enable_ssl else "http"
    default_port = 443 if scheme == "https" else 80
    suffix = "" if config.web_panel_port == default_port else f":{config.web_panel_port}"
    return f"{scheme}://{_url_host(config.host)}{suffix}/"


def _split_service_spec(spec: str, default_port: int) -> tuple[str, int]:
    normalized = spec.strip()
    if normalized.isdigit():
        return "", int(normalized)
    if ":" not in normalized:
        return normalized, default_port
    domain, separator, raw_port = normalized.rpartition(":")
    if separator and raw_port.isdigit():
        return domain, int(raw_port)
    return normalized, default_port


def _http_url(host: str, port: int | None, scheme: str) -> str:
    default_port = 443 if scheme == "https" else 80
    suffix = "" if port is None or port == default_port else f":{port}"
    return f"{scheme}://{_url_host(host)}{suffix}/"


def _preferred_host(config: SetupConfig, identities: list[str]) -> str:
    if config.system_hostname:
        return config.system_hostname
    return next(
        (
            identity
            for identity in identities
            if identity not in {"localhost", "127.0.0.1", "::1"}
        ),
        identities[0],
    )


def build_web_panel_manifest(
    config: SetupConfig,
    identities: list[str],
) -> dict[str, Any]:
    """Build non-secret configured service and access metadata."""

    host = _preferred_host(config, identities)
    services: list[dict[str, Any]] = []
    if config.privilege_broker_port is not None:
        from lib.privilege_setup import privilege_broker_origin

        services.append({"label": "Privilege approvals", "url": privilege_broker_origin(config) + "/",
                         "description": "Review agent requests using the separate approval password"})
    access: list[dict[str, str]] = [
        {
            "label": "SSH",
            "value": f"ssh {config.username}@{host}",
            "description": "Shell and file transfer",
        }
    ]

    if config.system_type == "server_web" and not (
        config.web_panel_port == 80 and not config.enable_ssl
    ):
        services.append(
            {
                "label": "Web server",
                "url": _http_url(host, None, "http"),
                "description": "Default Nginx site",
            }
        )

    if config.gogs:
        domain, port = _split_service_spec(str(config.gogs[0]), 3000)
        if domain or config.effective_gogs_sources():
            scheme = "https" if config.enable_ssl or config.enable_cloudflare else "http"
            public_port = None if config.enable_cloudflare and domain else port
            services.append(
                {
                    "label": "Gogs",
                    "url": _http_url(domain or host, public_port, scheme),
                    "description": "Git repositories",
                }
            )

    if config.homebox:
        from lib.homebox_config import homebox_settings

        homebox = homebox_settings(config)
        if homebox["domain"]:
            services.append({
                "label": "HomeBox",
                "url": _http_url(homebox["domain"], homebox["public_port"], "https"),
                "description": "Inventory and attachments",
                "probe": {"kind": "homebox", "port": homebox["port"]},
            })

    for label, spec, default_port, description in (
        ("Antistatic lobby", config.antistatic_server, 8080, "Game lobby"),
        ("Antistatic DB", config.antistatic_db, 8081, "Game database"),
    ):
        if not spec:
            continue
        domain, port = _split_service_spec(spec, default_port)
        scheme = (
            "https"
            if domain and (config.enable_ssl or config.enable_cloudflare)
            else "http"
        )
        services.append(
            {
                "label": label,
                "url": _http_url(domain or host, None if domain else port, scheme),
                "description": description,
            }
        )

    if config.enable_rdp:
        access.append(
            {
                "label": "Remote desktop",
                "value": f"{_url_host(host)}:3389",
                "description": "RDP client connection",
            }
        )
    if config.enable_samba:
        access.append(
            {
                "label": "Samba / SMB",
                "value": f"//{_url_host(host)}",
                "description": "Authenticated file shares",
            }
        )
        for share in config.samba_shares or []:
            if len(share) >= 2:
                access.append(
                    {
                        "label": f"SMB share: {share[1]}_{share[0]}",
                        "value": f"smb://{_url_host(host)}/{share[1]}_{share[0]}",
                        "description": str(share[2]) if len(share) >= 3 else "",
                    }
                )

    title = config.friendly_name or config.system_hostname or _preferred_host(
        config, identities
    )
    manifest = {
        "version": 1,
        "title": title,
        "host": host,
        "system_type": config.system_type,
        "username": config.username,
        "services": services,
        "access": access,
        "features": {
            "t3_update": "t3code" in (config.web_interfaces or []),
            "t3_github_readiness": config.github_auth_payload,
            "t3_git_identity_readiness": config.git_identity_payload,
            "notification_ingest": config.web_panel_notification_ingest is True,
        },
    }
    if config.web_panel_port is not None:
        scheme = "https" if config.enable_ssl else "http"
        manifest["panel_url"] = _http_url(host, config.web_panel_port, scheme)
    return manifest


def render_web_panel_nginx(
    identities: list[str],
    port: int,
    *,
    cert_path: str | None = None,
    key_path: str | None = None,
    notification_ingest: bool = False,
) -> str:
    """Render the Basic-Auth reverse proxy for the Unix-socket app."""

    ssl = cert_path is not None and key_path is not None
    listen_options = " ssl" if ssl else ""
    tls = ""
    if ssl:
        tls = f"""
    ssl_certificate {cert_path};
    ssl_certificate_key {key_path};
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:infra_tools_web_panel:10m;
    ssl_session_timeout 1d;
"""
    server_names = " ".join(_url_host(identity) for identity in identities)
    ingest_zone = (
        "limit_req_zone $binary_remote_addr "
        "zone=infra_tools_web_panel_ingest:10m rate=30r/m;\n"
        if notification_ingest
        else ""
    )
    ingest_location = ""
    if notification_ingest:
        ingest_location = f"""
    location = {WEB_PANEL_NOTIFICATION_ENDPOINT} {{
        auth_basic off;
        limit_except POST {{ deny all; }}
        limit_req zone=infra_tools_web_panel_ingest burst=10 nodelay;
        limit_req_status 429;
        client_max_body_size 64k;
        proxy_pass http://unix:{WEB_PANEL_SOCKET}:{WEB_PANEL_NOTIFICATION_ENDPOINT};
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_connect_timeout 5s;
        proxy_read_timeout 10s;
        proxy_send_timeout 10s;
    }}
"""
    return f"""{_NGINX_MARKER}
limit_req_zone $binary_remote_addr zone=infra_tools_web_panel_auth:10m rate=120r/m;
{ingest_zone}

map $status $infra_tools_web_panel_auth_failure {{
    default 0;
    401 1;
}}

log_format infra_tools_web_panel_auth '$remote_addr [$time_local] infra-tools-auth-failure';

server {{
    listen {port}{listen_options};
    listen [::]:{port}{listen_options};
    server_name {server_names};
{tls}
    access_log {WEB_PANEL_AUTH_FAILURE_LOG} infra_tools_web_panel_auth
        if=$infra_tools_web_panel_auth_failure;
    auth_basic "infra-tools web panel";
    auth_basic_user_file {WEB_PANEL_AUTH_FILE};
    client_max_body_size 16k;
{ingest_location}

    location / {{
        limit_req zone=infra_tools_web_panel_auth burst=5 nodelay;
        limit_req_status 429;
        proxy_pass http://unix:{WEB_PANEL_SOCKET}:/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_connect_timeout 5s;
        proxy_read_timeout 35s;
        proxy_send_timeout 35s;
    }}
}}
"""


def _validate_htpasswd_file(
    path: str,
    expected_username: str | None = None,
) -> None:
    if os.path.islink(path) or not os.path.isfile(path):
        raise RuntimeError(f"Web panel auth must be a regular file: {path}")
    if not 0 < os.path.getsize(path) <= 64 * 1024:
        raise RuntimeError("Web panel auth file is empty or too large")
    try:
        with open(path, encoding="utf-8") as file_obj:
            records = [line.rstrip("\n") for line in file_obj if line.rstrip("\n")]
    except UnicodeDecodeError as exc:
        raise RuntimeError("Web panel auth file must be UTF-8 text") from exc
    if not records:
        raise RuntimeError("Web panel auth file has no user records")
    usernames: list[str] = []
    for record in records:
        username, separator, password_hash = record.partition(":")
        if (
            not separator
            or not validate_username(username)
            or not password_hash.startswith("$")
            or any(ord(character) < 32 or ord(character) == 127 for character in record)
        ):
            raise RuntimeError("Web panel auth file contains an invalid record")
        usernames.append(username)
    if expected_username is not None and usernames != [expected_username]:
        raise RuntimeError(
            "Web panel auth must contain exactly the setup username; "
            "supply --web-panel-password to replace it"
        )


def _replace_auth_file(payload: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".htpasswd-", dir=WEB_PANEL_CONFIG_DIR)
    try:
        with os.fdopen(descriptor, "wb") as file_obj:
            file_obj.write(payload)
        os.chmod(temporary, 0o640)
        os.replace(temporary, WEB_PANEL_AUTH_FILE)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _install_auth_file(expected_username: str) -> tuple[bool, bytes | None]:
    validate_filesystem_path(WEB_PANEL_CONFIG_DIR, must_exist=False)
    if os.path.lexists(WEB_PANEL_CONFIG_DIR) and (
        os.path.islink(WEB_PANEL_CONFIG_DIR)
        or not os.path.isdir(WEB_PANEL_CONFIG_DIR)
    ):
        raise RuntimeError(f"Refusing unsafe web panel config path: {WEB_PANEL_CONFIG_DIR}")
    os.makedirs(WEB_PANEL_CONFIG_DIR, mode=0o750, exist_ok=True)
    existing = None
    if os.path.lexists(WEB_PANEL_AUTH_FILE):
        _validate_htpasswd_file(WEB_PANEL_AUTH_FILE)
        with open(WEB_PANEL_AUTH_FILE, "rb") as file_obj:
            existing = file_obj.read()
    if os.path.lexists(WEB_PANEL_PAYLOAD_FILE):
        _validate_htpasswd_file(WEB_PANEL_PAYLOAD_FILE, expected_username)
        with open(WEB_PANEL_PAYLOAD_FILE, "rb") as file_obj:
            desired = file_obj.read()
        if existing != desired:
            _replace_auth_file(desired)
            changed = True
        else:
            changed = False
    elif existing is None:
        raise RuntimeError(
            "The web panel needs --web-panel-password on first setup"
        )
    else:
        _validate_htpasswd_file(WEB_PANEL_AUTH_FILE, expected_username)
        changed = False
    web_account = pwd.getpwnam("www-data")
    os.chown(WEB_PANEL_CONFIG_DIR, 0, web_account.pw_gid)
    os.chmod(WEB_PANEL_CONFIG_DIR, 0o750)
    os.chown(WEB_PANEL_AUTH_FILE, 0, web_account.pw_gid)
    os.chmod(WEB_PANEL_AUTH_FILE, 0o640)
    return changed, existing


def _restore_auth_file(previous: bytes | None) -> None:
    """Restore credentials after a later panel configuration failure."""

    if previous is None:
        try:
            os.unlink(WEB_PANEL_AUTH_FILE)
        except FileNotFoundError:
            return
    else:
        _replace_auth_file(previous)
        web_account = pwd.getpwnam("www-data")
        os.chown(WEB_PANEL_AUTH_FILE, 0, web_account.pw_gid)
        os.chmod(WEB_PANEL_AUTH_FILE, 0o640)
    if shutil.which("nginx"):
        run("systemctl reload nginx", check=False)


def _ensure_event_storage(service_user: str) -> None:
    """Create separate least-privilege audit and notification data stores."""

    service_account = pwd.getpwnam(service_user)
    storage_parent = os.path.dirname(os.path.abspath(WEB_PANEL_DATA_DIR))
    validate_filesystem_path(storage_parent, must_exist=False)
    if os.path.lexists(storage_parent) and (
        os.path.islink(storage_parent) or not os.path.isdir(storage_parent)
    ):
        raise RuntimeError(f"Refusing unsafe web panel storage parent: {storage_parent}")
    # The durable setup state directory is root-only by default.  Grant the
    # panel group traversal of the parent while keeping directory listing and
    # all unrelated state files private.
    os.makedirs(storage_parent, mode=0o710, exist_ok=True)
    os.chown(storage_parent, 0, service_account.pw_gid)
    os.chmod(storage_parent, 0o710)
    for path in (WEB_PANEL_DATA_DIR, WEB_PANEL_AUDIT_DIR, WEB_PANEL_NOTIFICATION_DIR):
        validate_filesystem_path(path, must_exist=False)
        if os.path.lexists(path) and (
            os.path.islink(path) or not os.path.isdir(path)
        ):
            raise RuntimeError(f"Refusing unsafe web panel data path: {path}")
        os.makedirs(path, mode=0o750, exist_ok=True)
    os.chown(WEB_PANEL_DATA_DIR, 0, service_account.pw_gid)
    os.chown(WEB_PANEL_AUDIT_DIR, 0, service_account.pw_gid)
    os.chown(
        WEB_PANEL_NOTIFICATION_DIR,
        service_account.pw_uid,
        service_account.pw_gid,
    )
    os.chmod(WEB_PANEL_DATA_DIR, 0o750)
    os.chmod(WEB_PANEL_AUDIT_DIR, 0o2750)
    os.chmod(WEB_PANEL_NOTIFICATION_DIR, 0o700)
    for path in (WEB_PANEL_AUDIT_SNAPSHOT, WEB_PANEL_NOTIFICATION_LOG):
        if os.path.lexists(path) and (
            os.path.islink(path) or not os.path.isfile(path)
        ):
            raise RuntimeError(f"Refusing unsafe web panel event file: {path}")
    if os.path.exists(WEB_PANEL_AUDIT_SNAPSHOT):
        os.chown(WEB_PANEL_AUDIT_SNAPSHOT, 0, service_account.pw_gid)
        os.chmod(WEB_PANEL_AUDIT_SNAPSHOT, 0o640)
    if os.path.exists(WEB_PANEL_NOTIFICATION_LOG):
        os.chown(
            WEB_PANEL_NOTIFICATION_LOG,
            service_account.pw_uid,
            service_account.pw_gid,
        )
        os.chmod(WEB_PANEL_NOTIFICATION_LOG, 0o600)


def _validate_web_panel_service_account(account: pwd.struct_passwd) -> None:
    try:
        primary_group = grp.getgrgid(account.pw_gid)
    except KeyError as exc:
        raise RuntimeError(
            f"Refusing incompatible {_WEB_PANEL_SYSTEM_USER} service account"
        ) from exc
    group_is_shared = bool(primary_group.gr_mem) or any(
        other.pw_name != account.pw_name and other.pw_gid == account.pw_gid
        for other in pwd.getpwall()
    )
    if (
        account.pw_name != _WEB_PANEL_SYSTEM_USER
        or account.pw_uid == 0
        or account.pw_gid == 0
        or account.pw_dir != "/nonexistent"
        or account.pw_shell not in {"/usr/sbin/nologin", "/sbin/nologin"}
        or primary_group.gr_name != _WEB_PANEL_SYSTEM_USER
        or group_is_shared
    ):
        raise RuntimeError(
            f"Refusing incompatible {_WEB_PANEL_SYSTEM_USER} service account"
        )


def _ensure_web_panel_service_account(config: SetupConfig) -> pwd.struct_passwd:
    """Return the panel identity, creating a dedicated one for root setups."""

    if config.username != "root":
        return pwd.getpwnam(config.username)
    if not validate_username(_WEB_PANEL_SYSTEM_USER):
        raise RuntimeError("The managed web panel service username is invalid")
    try:
        account = pwd.getpwnam(_WEB_PANEL_SYSTEM_USER)
    except KeyError:
        run(
            [
                "useradd",
                "--system",
                "--user-group",
                "--home-dir",
                "/nonexistent",
                "--no-create-home",
                "--shell",
                "/usr/sbin/nologin",
                _WEB_PANEL_SYSTEM_USER,
            ],
            check=True,
        )
        try:
            account = pwd.getpwnam(_WEB_PANEL_SYSTEM_USER)
        except KeyError as exc:
            raise RuntimeError(
                "The web panel service account was not available after creation"
            ) from exc
    _validate_web_panel_service_account(account)
    return account


def _validate_ingest_token_path() -> None:
    validate_filesystem_path(WEB_PANEL_INGEST_TOKEN, must_exist=False)
    if os.path.lexists(WEB_PANEL_INGEST_TOKEN) and (
        os.path.islink(WEB_PANEL_INGEST_TOKEN)
        or not os.path.isfile(WEB_PANEL_INGEST_TOKEN)
    ):
        raise RuntimeError(
            f"Refusing unsafe notification ingest token: {WEB_PANEL_INGEST_TOKEN}"
        )


def _install_ingest_token(enabled: bool, service_gid: int) -> bool:
    """Create or remove the notification API token without rotating it on setup."""

    _validate_ingest_token_path()
    if not enabled:
        try:
            os.unlink(WEB_PANEL_INGEST_TOKEN)
            return True
        except FileNotFoundError:
            return False

    existing = None
    if os.path.exists(WEB_PANEL_INGEST_TOKEN):
        existing = load_ingest_token(WEB_PANEL_INGEST_TOKEN)
    token = existing or secrets.token_urlsafe(32)
    if not _INGEST_TOKEN_PATTERN.fullmatch(token):
        raise RuntimeError("Generated an invalid notification ingest token")
    changed = existing is None
    if changed:
        write_text_atomic(WEB_PANEL_INGEST_TOKEN, token + "\n", mode=0o640)
    os.chown(WEB_PANEL_INGEST_TOKEN, 0, service_gid)
    os.chmod(WEB_PANEL_INGEST_TOKEN, 0o640)
    return changed


def _ensure_nginx() -> None:
    if not install_package("nginx", "nginx", "apt-get install -y -qq nginx"):
        raise RuntimeError("Failed to install Nginx for the web panel")
    if not is_service_active("nginx"):
        run("systemctl enable --now nginx", check=True)


def _restore_nginx_site(previous: str | None, link_created: bool) -> None:
    if previous is None:
        try:
            os.unlink(WEB_PANEL_NGINX_SITE)
        except FileNotFoundError:
            pass
    else:
        write_text_atomic(WEB_PANEL_NGINX_SITE, previous, mode=0o644)
    if link_created:
        try:
            os.unlink(WEB_PANEL_NGINX_LINK)
        except FileNotFoundError:
            pass


def _write_nginx_site(content: str) -> bool:
    previous = None
    if os.path.lexists(WEB_PANEL_NGINX_SITE):
        if os.path.islink(WEB_PANEL_NGINX_SITE) or not os.path.isfile(WEB_PANEL_NGINX_SITE):
            raise RuntimeError(f"Refusing unmanaged web panel Nginx site: {WEB_PANEL_NGINX_SITE}")
        with open(WEB_PANEL_NGINX_SITE, encoding="utf-8") as file_obj:
            previous = file_obj.read()
        if _NGINX_MARKER not in previous:
            raise RuntimeError(f"Refusing unmanaged web panel Nginx site: {WEB_PANEL_NGINX_SITE}")
    changed = previous != content
    if changed:
        write_text_atomic(WEB_PANEL_NGINX_SITE, content, mode=0o644)
    link_created = False
    if os.path.lexists(WEB_PANEL_NGINX_LINK):
        if not (
            os.path.islink(WEB_PANEL_NGINX_LINK)
            and os.path.realpath(WEB_PANEL_NGINX_LINK) == WEB_PANEL_NGINX_SITE
            and previous is not None
        ):
            raise RuntimeError(f"Refusing unmanaged web panel Nginx link: {WEB_PANEL_NGINX_LINK}")
    else:
        os.symlink(WEB_PANEL_NGINX_SITE, WEB_PANEL_NGINX_LINK)
        changed = True
        link_created = True
    try:
        validation = run("nginx -t", check=False, capture_output=True)
    except Exception:
        _restore_nginx_site(previous, link_created)
        raise
    if validation.returncode != 0:
        _restore_nginx_site(previous, link_created)
        raise RuntimeError("Nginx rejected the web panel configuration")
    if changed:
        try:
            run("systemctl reload nginx", check=True)
        except Exception:
            _restore_nginx_site(previous, link_created)
            try:
                rollback_validation = run(
                    "nginx -t", check=False, capture_output=True
                )
                if rollback_validation.returncode == 0:
                    run("systemctl reload nginx", check=False)
            except Exception:
                pass
            raise
    return changed


def _systemd_quote(value: str) -> str:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("Systemd value contains control characters")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _is_managed_service(content: str) -> bool:
    return _SERVICE_MARKER in content or (
        "Description=infra-tools web panel" in content
        and f"ExecStart=/usr/bin/python3 {WEB_PANEL_SCRIPT} " in content
    )


def _restore_service(previous: str | None) -> None:
    if previous is None:
        run(
            f"systemctl disable --now {WEB_PANEL_SERVICE_NAME}.service",
            check=False,
        )
        try:
            os.unlink(WEB_PANEL_SERVICE_FILE)
        except FileNotFoundError:
            pass
    else:
        write_text_atomic(WEB_PANEL_SERVICE_FILE, previous, mode=0o644)
    run("systemctl daemon-reload", check=False)
    if previous is not None:
        run(
            f"systemctl restart {WEB_PANEL_SERVICE_NAME}.service",
            check=False,
        )


def _configure_service(config: SetupConfig, home: str) -> bool:
    service_user = (
        config.username if config.username != "root" else _WEB_PANEL_SYSTEM_USER
    )
    service_home = home if service_user == config.username else "/nonexistent"
    service_account = pwd.getpwnam(service_user)
    web_account = pwd.getpwnam("www-data")
    content = f"""{_SERVICE_MARKER}
[Unit]
Description=infra-tools web panel
After=network-online.target nginx.service
Wants=network-online.target

[Service]
Type=simple
User={service_user}
Group={service_account.pw_gid}
SupplementaryGroups=www-data
RuntimeDirectory=infra-tools-web-panel
RuntimeDirectoryMode=0711
UMask=0007
Environment={_systemd_quote('HOME=' + service_home)}
Environment=XDG_RUNTIME_DIR=/run/user/{service_account.pw_uid}
Environment=DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{service_account.pw_uid}/bus
ExecStart=/usr/bin/python3 {WEB_PANEL_SCRIPT} --config {WEB_PANEL_MANIFEST} --socket {WEB_PANEL_SOCKET} --socket-group {web_account.pw_gid}
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateDevices=true
PrivateTmp=true
ProtectSystem=full
ProtectControlGroups=true
ProtectKernelModules=true
ProtectKernelTunables=true
LockPersonality=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
RestrictSUIDSGID=true
StandardOutput=null
StandardError=journal

[Install]
WantedBy=multi-user.target
"""
    previous = None
    if os.path.lexists(WEB_PANEL_SERVICE_FILE) and (
        os.path.islink(WEB_PANEL_SERVICE_FILE)
        or not os.path.isfile(WEB_PANEL_SERVICE_FILE)
    ):
        raise RuntimeError(
            f"Refusing unmanaged web panel service: {WEB_PANEL_SERVICE_FILE}"
        )
    try:
        with open(WEB_PANEL_SERVICE_FILE, encoding="utf-8") as file_obj:
            previous = file_obj.read()
    except OSError:
        pass
    if previous is not None and not _is_managed_service(previous):
        raise RuntimeError(
            f"Refusing unmanaged web panel service: {WEB_PANEL_SERVICE_FILE}"
        )
    changed = previous != content
    try:
        if changed:
            write_text_atomic(WEB_PANEL_SERVICE_FILE, content, mode=0o644)
            run("systemctl daemon-reload", check=True)
        run(f"systemctl enable {WEB_PANEL_SERVICE_NAME}.service", check=True)
        # The service reads its manifest only at startup, so restart even when
        # the unit itself is unchanged.
        run(f"systemctl restart {WEB_PANEL_SERVICE_NAME}.service", check=True)
        for _attempt in range(25):
            if os.path.exists(WEB_PANEL_SOCKET) and is_service_active(
                WEB_PANEL_SERVICE_NAME
            ):
                return changed
            time.sleep(0.2)
        status = run(
            f"systemctl status {WEB_PANEL_SERVICE_NAME}.service "
            "--no-pager --lines=20",
            check=False,
            capture_output=True,
        )
        detail = (
            getattr(status, "stderr", "") or getattr(status, "stdout", "")
        ).strip()
        message = "The web panel service did not create its HTTP socket"
        raise RuntimeError(message + (f":\n{detail}" if detail else ""))
    except Exception:
        if changed:
            _restore_service(previous)
        raise


def _configure_audit_exporter(service_gid: int) -> bool:
    """Install the root-only audit snapshot exporter and refresh timer."""

    service_content = f"""{_SERVICE_MARKER}
[Unit]
Description=infra-tools web panel audit snapshot
After=auditd.service

[Service]
Type=oneshot
User=root
Group={service_gid}
UMask=0027
ExecStart=/usr/bin/python3 {WEB_PANEL_AUDIT_SCRIPT} --output {WEB_PANEL_AUDIT_SNAPSHOT}
NoNewPrivileges=true
PrivateDevices=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths={WEB_PANEL_AUDIT_DIR}
ProtectControlGroups=true
ProtectKernelModules=true
ProtectKernelTunables=true
LockPersonality=true
RestrictAddressFamilies=AF_UNIX AF_NETLINK
RestrictSUIDSGID=true
StandardOutput=null
StandardError=journal
"""
    timer_content = f"""{_SERVICE_MARKER}
[Unit]
Description=Refresh the infra-tools web panel audit snapshot

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
AccuracySec=30s
RandomizedDelaySec=30s
Persistent=true

[Install]
WantedBy=timers.target
"""
    desired = {
        WEB_PANEL_AUDIT_SERVICE_FILE: service_content,
        WEB_PANEL_AUDIT_TIMER_FILE: timer_content,
    }
    previous: dict[str, str | None] = {}
    for path in desired:
        validate_filesystem_path(path, must_exist=False)
        if os.path.lexists(path) and (
            os.path.islink(path) or not os.path.isfile(path)
        ):
            raise RuntimeError(f"Refusing unmanaged web panel audit unit: {path}")
        try:
            with open(path, encoding="utf-8") as file_obj:
                previous[path] = file_obj.read()
        except OSError:
            previous[path] = None
        if previous[path] is not None and _SERVICE_MARKER not in previous[path]:
            raise RuntimeError(f"Refusing unmanaged web panel audit unit: {path}")

    changed = any(previous[path] != content for path, content in desired.items())
    try:
        for path, content in desired.items():
            if previous[path] != content:
                write_text_atomic(path, content, mode=0o644)
        if changed:
            run("systemctl daemon-reload", check=True)
        run(
            f"systemctl enable --now {WEB_PANEL_AUDIT_SERVICE_NAME}.timer",
            check=True,
        )
        run(f"systemctl start {WEB_PANEL_AUDIT_SERVICE_NAME}.service", check=True)
    except Exception:
        if previous[WEB_PANEL_AUDIT_TIMER_FILE] is None:
            run(
                f"systemctl disable --now {WEB_PANEL_AUDIT_SERVICE_NAME}.timer",
                check=False,
            )
        run(
            f"systemctl stop {WEB_PANEL_AUDIT_SERVICE_NAME}.service",
            check=False,
        )
        for path, content in previous.items():
            if content is None:
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass
            else:
                write_text_atomic(path, content, mode=0o644)
        run("systemctl daemon-reload", check=False)
        raise
    return changed


def _preflight_web_panel_removal() -> tuple[str | None, bool]:
    if os.path.lexists(WEB_PANEL_SERVICE_FILE):
        if os.path.islink(WEB_PANEL_SERVICE_FILE) or not os.path.isfile(
            WEB_PANEL_SERVICE_FILE
        ):
            raise RuntimeError(
                f"Refusing unmanaged web panel service: {WEB_PANEL_SERVICE_FILE}"
            )
        with open(WEB_PANEL_SERVICE_FILE, encoding="utf-8") as file_obj:
            service_content = file_obj.read()
        if not _is_managed_service(service_content):
            raise RuntimeError(
                f"Refusing unmanaged web panel service: {WEB_PANEL_SERVICE_FILE}"
            )

    nginx_content = None
    if os.path.lexists(WEB_PANEL_NGINX_SITE):
        if os.path.islink(WEB_PANEL_NGINX_SITE) or not os.path.isfile(
            WEB_PANEL_NGINX_SITE
        ):
            raise RuntimeError(
                f"Refusing unmanaged web panel Nginx site: {WEB_PANEL_NGINX_SITE}"
            )
        with open(WEB_PANEL_NGINX_SITE, encoding="utf-8") as file_obj:
            nginx_content = file_obj.read()
        if _NGINX_MARKER not in nginx_content:
            raise RuntimeError(
                f"Refusing unmanaged web panel Nginx site: {WEB_PANEL_NGINX_SITE}"
            )

    link_exists = os.path.lexists(WEB_PANEL_NGINX_LINK)
    if link_exists and not (
        nginx_content is not None
        and os.path.islink(WEB_PANEL_NGINX_LINK)
        and os.path.realpath(WEB_PANEL_NGINX_LINK) == WEB_PANEL_NGINX_SITE
    ):
        raise RuntimeError(
            f"Refusing unmanaged web panel Nginx link: {WEB_PANEL_NGINX_LINK}"
        )

    if os.path.lexists(WEB_PANEL_CONFIG_DIR) and (
        os.path.islink(WEB_PANEL_CONFIG_DIR)
        or not os.path.isdir(WEB_PANEL_CONFIG_DIR)
    ):
        raise RuntimeError(
            f"Refusing unsafe web panel config path: {WEB_PANEL_CONFIG_DIR}"
        )
    for path in (WEB_PANEL_MANIFEST, WEB_PANEL_AUTH_FILE):
        if os.path.lexists(path) and (
            os.path.islink(path) or not os.path.isfile(path)
        ):
            raise RuntimeError(f"Refusing unsafe web panel file: {path}")
    for path in (
        WEB_PANEL_AUDIT_SERVICE_FILE,
        WEB_PANEL_AUDIT_TIMER_FILE,
    ):
        if os.path.lexists(path):
            if os.path.islink(path) or not os.path.isfile(path):
                raise RuntimeError(f"Refusing unmanaged web panel audit unit: {path}")
            with open(path, encoding="utf-8") as file_obj:
                if _SERVICE_MARKER not in file_obj.read():
                    raise RuntimeError(f"Refusing unmanaged web panel audit unit: {path}")
    for path in (
        WEB_PANEL_DATA_DIR,
        WEB_PANEL_AUDIT_DIR,
        WEB_PANEL_NOTIFICATION_DIR,
    ):
        if os.path.lexists(path) and (
            os.path.islink(path) or not os.path.isdir(path)
        ):
            raise RuntimeError(f"Refusing unsafe web panel data path: {path}")
    for path in (
        WEB_PANEL_INGEST_TOKEN,
        WEB_PANEL_AUDIT_SNAPSHOT,
        WEB_PANEL_NOTIFICATION_LOG,
    ):
        if os.path.lexists(path) and (
            os.path.islink(path) or not os.path.isfile(path)
        ):
            raise RuntimeError(f"Refusing unsafe web panel event file: {path}")
    return nginx_content, link_exists


def _remove_nginx_site(nginx_content: str | None, link_exists: bool) -> None:
    if nginx_content is None and not link_exists:
        return
    if link_exists:
        os.unlink(WEB_PANEL_NGINX_LINK)
    if nginx_content is not None:
        os.unlink(WEB_PANEL_NGINX_SITE)
    if not shutil.which("nginx"):
        return
    try:
        validation = run("nginx -t", check=False, capture_output=True)
        if validation.returncode != 0:
            raise RuntimeError("Nginx is invalid after removing the web panel")
        run("systemctl reload nginx", check=True)
    except Exception:
        if nginx_content is not None:
            write_text_atomic(WEB_PANEL_NGINX_SITE, nginx_content, mode=0o644)
        if link_exists and not os.path.lexists(WEB_PANEL_NGINX_LINK):
            os.symlink(WEB_PANEL_NGINX_SITE, WEB_PANEL_NGINX_LINK)
        try:
            rollback_validation = run("nginx -t", check=False, capture_output=True)
            if rollback_validation.returncode == 0:
                run("systemctl reload nginx", check=False)
        except Exception:
            pass
        raise


def remove_web_panel() -> None:
    """Remove the panel, authentication data, and public Nginx listener."""

    nginx_content, link_exists = _preflight_web_panel_removal()
    _remove_nginx_site(nginx_content, link_exists)
    units_changed = False
    if os.path.exists(WEB_PANEL_SERVICE_FILE):
        run(f"systemctl disable --now {WEB_PANEL_SERVICE_NAME}.service", check=False)
        os.remove(WEB_PANEL_SERVICE_FILE)
        units_changed = True
    audit_units_exist = any(
        os.path.exists(path)
        for path in (WEB_PANEL_AUDIT_SERVICE_FILE, WEB_PANEL_AUDIT_TIMER_FILE)
    )
    if audit_units_exist:
        run(
            f"systemctl disable --now {WEB_PANEL_AUDIT_SERVICE_NAME}.timer",
            check=False,
        )
        run(
            f"systemctl stop {WEB_PANEL_AUDIT_SERVICE_NAME}.service",
            check=False,
        )
    for path in (WEB_PANEL_AUDIT_SERVICE_FILE, WEB_PANEL_AUDIT_TIMER_FILE):
        try:
            os.remove(path)
            units_changed = True
        except FileNotFoundError:
            pass
    if units_changed:
        run("systemctl daemon-reload", check=True)
    for path in (
        WEB_PANEL_MANIFEST,
        WEB_PANEL_AUTH_FILE,
        WEB_PANEL_INGEST_TOKEN,
    ):
        if os.path.lexists(path):
            os.remove(path)
    if os.path.lexists(WEB_PANEL_CONFIG_DIR):
        try:
            os.rmdir(WEB_PANEL_CONFIG_DIR)
        except OSError:
            pass
    for path in (WEB_PANEL_AUDIT_SNAPSHOT, WEB_PANEL_NOTIFICATION_LOG):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
    for path in (
        WEB_PANEL_AUDIT_DIR,
        WEB_PANEL_NOTIFICATION_DIR,
        WEB_PANEL_DATA_DIR,
    ):
        try:
            os.rmdir(path)
        except OSError:
            pass
    remove_nginx_auth_failure_ban("web-panel")
    print("  ✓ Web panel removed")


def configure_web_panel(config: SetupConfig) -> None:
    """Install, update, or remove the optional web panel."""

    if is_dry_run():
        action = "remove" if config.disable_web_panel else "configure"
        print(f"  [DRY-RUN] Would {action} the web panel")
        return
    if config.disable_web_panel:
        remove_web_panel()
        return
    if config.web_panel_port is None:
        return

    from common.godot_web_steps import (
        configure_internal_web_host,
        discover_local_web_identities,
        identities_for_config,
        validate_web_identities,
    )

    _ensure_nginx()
    discovered = discover_local_web_identities()
    identities = validate_web_identities(
        [*identities_for_config(config.host, config.system_hostname), *discovered]
    )
    cert_path = None
    key_path = None
    if config.enable_ssl:
        _base_url, _local_ca, cert_path, key_path, _changed = configure_internal_web_host(
            identities,
            [config.username],
            config.effective_access_sources(),
            configure_static_site=True,
            install_utility=True,
        )

    auth_changed, previous_auth = _install_auth_file(config.username)
    try:
        account = _ensure_web_panel_service_account(config)
        ingest_enabled = config.web_panel_notification_ingest is True
        _ensure_event_storage(account.pw_name)
        _validate_ingest_token_path()
        if ingest_enabled:
            _install_ingest_token(True, account.pw_gid)
        manifest = build_web_panel_manifest(config, identities)
        os.makedirs(WEB_PANEL_CONFIG_DIR, mode=0o750, exist_ok=True)
        write_json_atomic(WEB_PANEL_MANIFEST, manifest, mode=0o644, sort_keys=True)
        _configure_audit_exporter(account.pw_gid)
        _configure_service(config, account.pw_dir)
        nginx_changed = _write_nginx_site(
            render_web_panel_nginx(
                identities,
                config.web_panel_port,
                cert_path=cert_path,
                key_path=key_path,
                notification_ingest=ingest_enabled,
            )
        )
        if not ingest_enabled:
            # Retain an existing token until both the application and its public
            # route have stopped accepting it, so an interrupted setup does not
            # break a still-running endpoint.
            _install_ingest_token(False, account.pw_gid)
    except Exception:
        if auth_changed:
            _restore_auth_file(previous_auth)
        raise
    if auth_changed and not nginx_changed:
        run("systemctl reload nginx", check=True)
    configure_nginx_auth_failure_ban("web-panel", WEB_PANEL_AUTH_FAILURE_LOG)
    scheme = "https" if config.enable_ssl else "http"
    host = _preferred_host(config, identities)
    print(
        "  ✓ Web panel: "
        + _http_url(host, config.web_panel_port, scheme)
    )
    if not config.enable_ssl:
        print("  ⚠ Web panel Basic Auth uses plaintext HTTP; add --ssl for encrypted login")
    if config.web_panel_notification_ingest is True:
        print(
            "  ✓ Notification ingest: "
            f"{WEB_PANEL_NOTIFICATION_ENDPOINT} (token: {WEB_PANEL_INGEST_TOKEN})"
        )


__all__ = [
    "build_web_panel_manifest",
    "configure_web_panel",
    "web_panel_url",
    "remove_web_panel",
    "render_web_panel_nginx",
]
