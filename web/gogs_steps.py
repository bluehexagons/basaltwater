"""Setup steps for Gogs."""

from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import secrets
import shlex
import tempfile
from typing import Any, Mapping

from lib.atomic_io import write_json_atomic, write_text_atomic
from lib.auth_failure_bans import configure_nginx_auth_failure_ban
from lib.config import SetupConfig
from lib.credentials import get_runtime_credential
from lib.nginx_config import SSL_CIPHERS, SSL_PROTOCOLS, generate_self_signed_cert, get_ssl_cert_path
from lib.release_management import (
    detect_release_arch,
    fetch_preferred_verified_github_release_asset,
    load_json_state,
    validate_release_download_url,
    validate_release_tag,
    write_json_state,
)
from lib.remote_utils import generate_password, is_service_active, run, user_exists
from lib.unit_transaction import replace_units
from lib.validation import validate_filesystem_path
from web.cloudflare_steps import run_cloudflare_tunnel_setup
from web.ssl_steps import install_certbot, obtain_letsencrypt_certificate, setup_certificate_renewal
from web.web_steps import install_nginx


GOGS_SERVICE = "gogs"
GOGS_GIT_USER = "git"
GOGS_GIT_GROUP = "git"
GOGS_INSTALL_ROOT = "/opt/gogs"
GOGS_RELEASES_DIR = f"{GOGS_INSTALL_ROOT}/releases"
GOGS_CURRENT_DIR = f"{GOGS_INSTALL_ROOT}/current"
GOGS_EXECUTABLE = f"{GOGS_CURRENT_DIR}/gogs"
GOGS_BINARY_LINK = "/usr/local/bin/gogs"
GOGS_STATE_FILE = "/opt/basaltwater/state/gogs.json"
GOGS_SECRET_KEY_FILE = "/opt/basaltwater/state/gogs_secret_key"
GOGS_ADMIN_CREDENTIALS_FILE = "/opt/basaltwater/state/gogs_admin_credentials.json"
GOGS_GITHUB_REPO = "gogs/gogs"
GOGS_SSH_DROPIN_DIR = "/etc/ssh/sshd_config.d"
GOGS_SSH_DROPIN_FILE = f"{GOGS_SSH_DROPIN_DIR}/99-gogs-git-user.conf"
DEFAULT_GOGS_PUBLIC_PORT = 3000
DEFAULT_GOGS_PROXY_BACKEND_PORT = 13000
DEFAULT_GOGS_DATA_PATH = "/var/lib/gogs"
GOGS_AUTH_FAILURE_LOG = "/var/log/nginx/basaltwater-gogs-auth-failures.log"
_GOGS_RULE_COMMENT_PREFIX = "basaltwater Gogs"
_LEGACY_GOGS_DIRECT_COMMENT = "gogs direct HTTP"
_LEGACY_GOGS_WEB_COMMENT = "gogs web"
_UFW_NUMBERED_RULE_RE = re.compile(r"^\[\s*(\d+)\]")


def _gogs_release_dir(tag_name: str, archive_sha256: str) -> str:
    """Return the immutable release directory for one verified archive."""
    safe_tag = validate_release_tag(tag_name)
    if len(archive_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in archive_sha256
    ):
        raise ValueError("Invalid Gogs release SHA-256")
    return f"{GOGS_RELEASES_DIR}/{safe_tag}-{archive_sha256[:12]}"


def parse_gogs_spec(spec: str, *, strict: bool = False) -> tuple[str, int]:
    """Parse a Gogs 'DOMAIN[:port]' spec into (domain, port)."""
    normalized = spec.strip()
    if not normalized:
        raise ValueError("Gogs target spec must be a non-empty string")

    if normalized.isdigit():
        port = int(normalized)
        if strict and not 1 <= port <= 65535:
            raise ValueError(f"Invalid Gogs port: {normalized}")
        return "", port if 1 <= port <= 65535 else DEFAULT_GOGS_PUBLIC_PORT

    if ":" not in normalized:
        return normalized, DEFAULT_GOGS_PUBLIC_PORT

    domain, _, raw_port = normalized.rpartition(":")
    domain = domain.strip()
    raw_port = raw_port.strip()
    if not raw_port:
        return domain, DEFAULT_GOGS_PUBLIC_PORT

    try:
        port = int(raw_port)
    except ValueError as exc:
        if strict:
            raise ValueError(f"Invalid Gogs port: {raw_port}") from exc
        return domain, DEFAULT_GOGS_PUBLIC_PORT

    if strict and not 1 <= port <= 65535:
        raise ValueError(f"Invalid Gogs port: {raw_port}")
    if not 1 <= port <= 65535:
        return domain, DEFAULT_GOGS_PUBLIC_PORT
    return domain, port


def _gogs_data_path(config: SetupConfig) -> str:
    if config.gogs and len(config.gogs) >= 2:
        return str(config.gogs[1])
    return DEFAULT_GOGS_DATA_PATH


def _gogs_public_host(config: SetupConfig, domain: str) -> str:
    return domain or config.host


def effective_gogs_ipv4_sources(config: SetupConfig) -> list[str]:
    """Return the effective source policy supported by Gogs' IPv4 listener."""
    sources: list[str] = []
    for source in config.effective_gogs_sources():
        network = ipaddress.ip_network(
            source if "/" in source else f"{source}/32",
            strict=False,
        )
        if network.version == 4:
            sources.append(str(network))
    return sources


def _gogs_uses_reverse_proxy(config: SetupConfig, domain: str) -> bool:
    return bool(domain or config.enable_ssl or config.enable_cloudflare)


def _gogs_backend_port(config: SetupConfig, public_port: int) -> int:
    """Return the loopback HTTP port used behind direct TLS."""
    if config.enable_ssl:
        if public_port == DEFAULT_GOGS_PROXY_BACKEND_PORT:
            return DEFAULT_GOGS_PROXY_BACKEND_PORT + 1
        return DEFAULT_GOGS_PROXY_BACKEND_PORT
    return public_port


def _gogs_external_url(config: SetupConfig, domain: str, port: int) -> str:
    scheme = "https" if config.enable_ssl or config.enable_cloudflare else "http"
    host = domain or (
        config.host if effective_gogs_ipv4_sources(config) else "127.0.0.1"
    )
    if domain and config.enable_cloudflare:
        return f"{scheme}://{host}/"
    default_port = 443 if scheme == "https" else 80
    port_suffix = f":{port}" if port != default_port else ""
    return f"{scheme}://{host}{port_suffix}/"


def _get_git_home() -> str:
    result = run(f"getent passwd {shlex.quote(GOGS_GIT_USER)}", check=False, capture_output=True)
    if result.returncode != 0 or not result.stdout:
        return f"/home/{GOGS_GIT_USER}"

    fields = result.stdout.strip().split(":")
    if len(fields) < 6 or not fields[5]:
        return f"/home/{GOGS_GIT_USER}"
    return fields[5]


def _load_gogs_state() -> dict[str, Any]:
    return load_json_state(
        GOGS_STATE_FILE,
        read_error_label="Gogs state file",
        invalid_state_message="Invalid Gogs state file contents",
    )


def write_gogs_state(
    tag_name: str,
    data_path: str,
    config_path: str,
    archive_sha256: str,
) -> None:
    write_json_state(
        GOGS_STATE_FILE,
        {
            "tag_name": tag_name,
            "data_path": data_path,
            "config_path": config_path,
            "archive_sha256": archive_sha256,
        },
        mode=0o600,
    )


def read_installed_gogs_release() -> str | None:
    """Return the recorded installed Gogs release tag."""
    tag_name = _load_gogs_state().get("tag_name")
    return tag_name if isinstance(tag_name, str) and tag_name else None


def read_gogs_state() -> Mapping[str, Any]:
    """Return persisted Gogs install metadata."""
    return _load_gogs_state()


def fetch_preferred_gogs_release(arch: str) -> tuple[str, str, str]:
    """Return the preferred Gogs tag, URL, and publisher SHA-256."""
    binary_name_suffix = f"_linux_{arch}.tar.gz"
    return fetch_preferred_verified_github_release_asset(
        GOGS_GITHUB_REPO,
        asset_matches=lambda tag_name, asset_name: (
            asset_name.startswith(f"gogs_{tag_name}") and asset_name.endswith(binary_name_suffix)
        ),
        missing_asset_description=f"No Gogs Linux release asset found for architecture '{arch}'",
    )


def install_or_update_gogs_release() -> tuple[str, bool, str]:
    """Install the preferred Gogs release and return tag, change, and digest."""
    arch = detect_release_arch()
    tag_name, download_url, expected_sha256 = fetch_preferred_gogs_release(arch)
    tag_name = validate_release_tag(tag_name)
    download_url = validate_release_download_url(download_url)
    release_dir = _gogs_release_dir(tag_name, expected_sha256)
    installed_state = _load_gogs_state()
    installed_tag = installed_state.get("tag_name")
    installed_digest = installed_state.get("archive_sha256")
    current_binary = f"{GOGS_CURRENT_DIR}/gogs"
    current_release = os.path.realpath(GOGS_CURRENT_DIR)
    expected_release = os.path.realpath(release_dir)
    if (
        installed_tag == tag_name
        and installed_digest == expected_sha256
        and current_release == expected_release
        and os.path.exists(current_binary)
    ):
        print(f"  ✓ Gogs already up to date ({tag_name})")
        return tag_name, False, expected_sha256

    if current_release == expected_release and os.path.exists(current_binary):
        raise RuntimeError(
            "Refusing to replace the active Gogs release because its saved "
            "digest does not match; restore the managed Gogs state before retrying"
        )

    run(f"mkdir -p {shlex.quote(GOGS_RELEASES_DIR)}")
    with tempfile.TemporaryDirectory(prefix="basaltwater-gogs-release-") as temporary_dir:
        archive_path = os.path.join(temporary_dir, "gogs.tar.gz")
        extract_dir = os.path.join(temporary_dir, "extract")
        run(
            "curl -fL --proto '=https' --proto-redir '=https' "
            f"-o {shlex.quote(archive_path)} {shlex.quote(download_url)}",
            check=True,
            display_cmd=(
                "curl -fL --proto '=https' --proto-redir '=https' "
                f"-o {archive_path} <release URL>"
            ),
        )
        checksum_result = run(
            f"sha256sum {shlex.quote(archive_path)}",
            check=False,
            capture_output=True,
        )
        observed_sha256 = (checksum_result.stdout or "").split(maxsplit=1)[0].lower()
        if checksum_result.returncode != 0 or observed_sha256 != expected_sha256:
            raise RuntimeError("Gogs release archive checksum verification failed")
        run(f"mkdir -p {shlex.quote(extract_dir)}")
        run(f"rm -rf {shlex.quote(release_dir)}", check=False)
        run(
            f"tar -xzf {shlex.quote(archive_path)} -C {shlex.quote(extract_dir)}",
            check=True,
        )
        run(f"mv {shlex.quote(extract_dir)}/gogs {shlex.quote(release_dir)}", check=True)
        release_binary = f"{release_dir}/gogs"
        run(f"test -x {shlex.quote(release_binary)}", check=True)
        run(
            f"runuser -u {shlex.quote(GOGS_GIT_USER)} -- "
            f"{shlex.quote(release_binary)} --version",
            check=True,
            capture_output=True,
            cwd=release_dir,
        )
        run(
            f"ln -sfn {shlex.quote(release_dir)} {shlex.quote(GOGS_CURRENT_DIR)}",
            check=True,
        )
        run(
            f"ln -sfn {shlex.quote(GOGS_CURRENT_DIR)}/gogs "
            f"{shlex.quote(GOGS_BINARY_LINK)}",
            check=True,
        )
    print(f"  ✓ Installed Gogs {tag_name}")
    return tag_name, True, expected_sha256


def _ensure_git_user() -> str:
    git_home = _get_git_home()
    if not user_exists(GOGS_GIT_USER):
        run(
            f"useradd --create-home --home-dir {shlex.quote(git_home)} "
            f"--shell /usr/bin/git-shell {shlex.quote(GOGS_GIT_USER)}"
        )
        print(f"  ✓ Created system user: {GOGS_GIT_USER}")
    else:
        run(f"usermod -d {shlex.quote(git_home)} {shlex.quote(GOGS_GIT_USER)}", check=False)
        run(f"usermod -s /usr/bin/git-shell {shlex.quote(GOGS_GIT_USER)}", check=False)
        print(f"  ✓ Using existing system user: {GOGS_GIT_USER}")

    ssh_dir = f"{git_home}/.ssh"
    run(f"mkdir -p {shlex.quote(ssh_dir)}")
    run(f"touch {shlex.quote(ssh_dir)}/authorized_keys")
    run(f"chmod 700 {shlex.quote(ssh_dir)}")
    run(f"chmod 600 {shlex.quote(ssh_dir)}/authorized_keys")
    run(f"chown -R {shlex.quote(GOGS_GIT_USER)}:{shlex.quote(GOGS_GIT_GROUP)} {shlex.quote(git_home)}")
    run(f"passwd -l {shlex.quote(GOGS_GIT_USER)}", check=False)
    return git_home


def _ensure_gogs_dependencies() -> None:
    os.environ["DEBIAN_FRONTEND"] = "noninteractive"
    run(
        "apt-get install -y -qq git git-lfs openssh-server sqlite3 curl ca-certificates",
        check=True,
    )


def _reject_symlinked_gogs_path(path: str) -> None:
    """Reject symlinks in a managed Gogs data path before writing."""

    current = os.path.sep
    for component in os.path.abspath(path).split(os.path.sep):
        if not component:
            continue
        current = os.path.join(current, component)
        if os.path.lexists(current) and os.path.islink(current):
            raise RuntimeError(f"Refusing symlinked Gogs data path: {current}")


def _ensure_gogs_data_dirs(data_path: str) -> str:
    config_dir = f"{data_path}/custom/conf"
    for path in (
        data_path,
        f"{data_path}/custom",
        config_dir,
        f"{data_path}/data",
        f"{data_path}/data/lfs-objects",
        f"{data_path}/data/tmp/lfs-objects",
        f"{data_path}/data/tmp/uploads",
        f"{data_path}/data/attachments",
        f"{data_path}/data/avatars",
        f"{data_path}/data/repo-avatars",
        f"{data_path}/data/sessions",
        f"{data_path}/repositories",
        f"{data_path}/log",
    ):
        _reject_symlinked_gogs_path(path)
        run(f"mkdir -p {shlex.quote(path)}")
    run(f"chown -R {shlex.quote(GOGS_GIT_USER)}:{shlex.quote(GOGS_GIT_GROUP)} {shlex.quote(data_path)}")
    return f"{config_dir}/app.ini"


def _load_or_create_gogs_secret_key() -> str:
    try:
        with open(GOGS_SECRET_KEY_FILE, "r", encoding="utf-8") as file_obj:
            value = file_obj.read().strip()
    except FileNotFoundError:
        value = ""
    if value:
        return value

    value = secrets.token_hex(32)
    write_text_atomic(GOGS_SECRET_KEY_FILE, f"{value}\n")
    return value


def generate_gogs_app_ini(
    config: SetupConfig,
    *,
    git_home: str,
    data_path: str,
    domain: str,
    port: int,
    backend_port: int | None = None,
) -> str:
    """Return app.ini contents for a minimal self-hosted Gogs service."""
    public_host = _gogs_public_host(config, domain)
    external_url = _gogs_external_url(config, domain, port)
    resolved_backend_port = backend_port if backend_port is not None else port
    http_addr = (
        "0.0.0.0"
        if effective_gogs_ipv4_sources(config)
        and not _gogs_uses_reverse_proxy(config, domain)
        else "127.0.0.1"
    )
    secret_key = _load_or_create_gogs_secret_key()
    return f"""BRAND_NAME = Gogs
RUN_USER = {GOGS_GIT_USER}
RUN_MODE = prod

[server]
EXTERNAL_URL = {external_url}
DOMAIN = {public_host}
PROTOCOL = http
HTTP_ADDR = {http_addr}
HTTP_PORT = {resolved_backend_port}
LOCAL_ROOT_URL = http://127.0.0.1:{resolved_backend_port}/
APP_DATA_PATH = {data_path}/data
DISABLE_SSH = false
SSH_DOMAIN = {public_host}
SSH_PORT = 22
SSH_ROOT_PATH = {git_home}/.ssh
REWRITE_AUTHORIZED_KEYS_AT_START = true
START_SSH_SERVER = false
SSH_KEYGEN_PATH = /usr/bin/ssh-keygen
OFFLINE_MODE = false
DISABLE_ROUTER_LOG = true

[repository]
ROOT = {data_path}/repositories
DISABLE_HTTP_GIT = false

[repository.upload]
TEMP_PATH = {data_path}/data/tmp/uploads

[database]
TYPE = sqlite3
PATH = {data_path}/data/gogs.db

[lfs]
STORAGE = local
OBJECTS_PATH = {data_path}/data/lfs-objects
OBJECTS_TEMP_PATH = {data_path}/data/tmp/lfs-objects

[attachment]
PATH = {data_path}/data/attachments

[picture]
AVATAR_UPLOAD_PATH = {data_path}/data/avatars
REPOSITORY_AVATAR_UPLOAD_PATH = {data_path}/data/repo-avatars

[session]
PROVIDER_CONFIG = {data_path}/data/sessions
COOKIE_SECURE = {str(external_url.startswith("https://")).lower()}

[log]
ROOT_PATH = {data_path}/log

[security]
INSTALL_LOCK = true
SECRET_KEY = {secret_key}

[auth]
DISABLE_REGISTRATION = true
ENABLE_REGISTRATION_CAPTCHA = false
"""


def generate_gogs_service(config_path: str) -> str:
    """Return a hardened systemd unit file for Gogs."""
    validate_filesystem_path(config_path)
    custom_dir = os.path.dirname(os.path.dirname(config_path))
    mount_path = custom_dir.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return f"""[Unit]
Description=Gogs
After=network.target ssh.service sshd.service
Wants=network.target
RequiresMountsFor="{mount_path}"

[Service]
Type=simple
User={GOGS_GIT_USER}
Group={GOGS_GIT_GROUP}
WorkingDirectory={GOGS_CURRENT_DIR}
ExecStart={GOGS_EXECUTABLE} web --config {config_path}
Restart=always
RestartSec=2s
Environment=USER={GOGS_GIT_USER}
Environment=HOME={_get_git_home()}
Environment=GOGS_WORK_DIR={GOGS_CURRENT_DIR}
Environment=GOGS_CUSTOM={custom_dir}
ProtectSystem=full
PrivateDevices=yes
PrivateTmp=yes
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
"""


def generate_gogs_nginx_config(
    server_name: str,
    backend_port: int,
    *,
    public_tls_port: int | None,
    forwarded_proto: str,
    client_ip: str = "$remote_addr",
    cloudflare_origin: bool = False,
    redirect_plain_http: bool = False,
    loopback_only: bool = False,
    enable_ipv6: bool = True,
) -> str:
    """Return an nginx site config that proxies to Gogs."""
    cert_file, key_file = get_ssl_cert_path(server_name)
    zone_suffix = hashlib.sha256(server_name.encode("utf-8")).hexdigest()[:12]
    login_zone = f"basaltwater_gogs_login_{zone_suffix}"
    login_failure = f"basaltwater_gogs_login_failure_{zone_suffix}"
    basic_failure = f"basaltwater_gogs_basic_failure_{zone_suffix}"
    auth_failure = f"basaltwater_gogs_auth_failure_{zone_suffix}"
    log_format = f"basaltwater_gogs_auth_{zone_suffix}"
    if cloudflare_origin:
        http_server = f"""server {{
    listen 80;
    listen [::]:80;
    server_name {server_name};
    client_max_body_size 512m;
    access_log {GOGS_AUTH_FAILURE_LOG} {log_format} if=${auth_failure};

    location /.well-known/acme-challenge/ {{
        root /var/www/letsencrypt;
    }}

    location ~ ^/(?:api/web/user/(?:sign-in|mfa(?:/recovery)?)|user/login(?:/two_factor(?:_recovery_code)?)?)$ {{
        limit_req zone={login_zone} burst=5 nodelay;
        limit_req_status 429;
        proxy_pass http://127.0.0.1:{backend_port};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP {client_ip};
        proxy_set_header X-Forwarded-For {client_ip};
        proxy_set_header X-Forwarded-Proto {forwarded_proto};
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_request_buffering off;
        proxy_read_timeout 300s;
    }}

    location / {{
        proxy_pass http://127.0.0.1:{backend_port};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP {client_ip};
        proxy_set_header X-Forwarded-For {client_ip};
        proxy_set_header X-Forwarded-Proto {forwarded_proto};
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_request_buffering off;
        proxy_read_timeout 300s;
    }}
}}

"""
    elif redirect_plain_http and public_tls_port is not None:
        port_suffix = "" if public_tls_port == 443 else f":{public_tls_port}"
        http_server = f"""server {{
    listen 80;
    listen [::]:80;
    server_name {server_name};

    location /.well-known/acme-challenge/ {{
        root /var/www/letsencrypt;
    }}

    location / {{
        return 301 https://$host{port_suffix}$request_uri;
    }}
}}

"""
    else:
        http_server = ""
    proxy_settings = f"""        proxy_pass http://127.0.0.1:{backend_port};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP {client_ip};
        proxy_set_header X-Forwarded-For {client_ip};
        proxy_set_header X-Forwarded-Proto {forwarded_proto};
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_request_buffering off;
        proxy_read_timeout 300s;
"""
    if public_tls_port is None:
        tls_server = ""
    else:
        if loopback_only:
            listeners = f"    listen 127.0.0.1:{public_tls_port} ssl;"
        elif enable_ipv6:
            listeners = (
                f"    listen {public_tls_port} ssl;\n"
                f"    listen [::]:{public_tls_port} ssl;"
            )
        else:
            listeners = f"    listen 0.0.0.0:{public_tls_port} ssl;"
        tls_server = f"""server {{
{listeners}
    http2 on;
    server_name {server_name};

    ssl_certificate {cert_file};
    ssl_certificate_key {key_file};
    ssl_protocols {SSL_PROTOCOLS};
    ssl_prefer_server_ciphers on;
    ssl_ciphers {SSL_CIPHERS};
    client_max_body_size 512m;
    access_log {GOGS_AUTH_FAILURE_LOG} {log_format} if=${auth_failure};

    location /.well-known/acme-challenge/ {{
        root /var/www/letsencrypt;
    }}

    location ~ ^/(?:api/web/user/(?:sign-in|mfa(?:/recovery)?)|user/login(?:/two_factor(?:_recovery_code)?)?)$ {{
        limit_req zone={login_zone} burst=5 nodelay;
        limit_req_status 429;
{proxy_settings}    }}

    location / {{
{proxy_settings}    }}
}}
"""
    return f"""limit_req_zone {client_ip} zone={login_zone}:10m rate=5r/m;

map "$request_method:$status:$uri" ${login_failure} {{
    default 0;
    ~^POST:401:/api/web/user/(?:sign-in|mfa|mfa/recovery)$ 1;
}}

map "$http_authorization:$status" ${basic_failure} {{
    default 0;
    ~^.+:401$ 1;
}}

map "${login_failure}:${basic_failure}" ${auth_failure} {{
    default 0;
    ~1 1;
}}

log_format {log_format} '{client_ip} [$time_local] basaltwater-auth-failure';

{http_server}{tls_server}
"""


def _gogs_nginx_config_name(server_name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_]+", "_", server_name).strip("_")
    return f"gogs_{slug or 'default'}"


def _remove_stale_gogs_nginx_sites(desired_name: str | None = None) -> bool:
    removed = False
    for directory in ("/etc/nginx/sites-enabled", "/etc/nginx/sites-available"):
        try:
            names = os.listdir(directory)
        except FileNotFoundError:
            continue
        for name in names:
            if not name.startswith("gogs_") or name == desired_name:
                continue
            path = os.path.join(directory, name)
            if os.path.isdir(path) and not os.path.islink(path):
                raise RuntimeError(f"Refusing unexpected Gogs nginx directory: {path}")
            os.remove(path)
            removed = True
            print(f"  ✓ Removed stale Gogs nginx config: {path}")
    return removed


def _write_gogs_nginx_config(
    config: SetupConfig,
    domain: str,
    public_port: int,
    backend_port: int,
) -> None:
    server_name = domain or config.host
    public_tls_port = public_port if config.enable_ssl else None
    loopback_only = not domain and not effective_gogs_ipv4_sources(config)
    if public_tls_port is not None:
        additional_identities = ["127.0.0.1"] if loopback_only else []
        generate_self_signed_cert(server_name, additional_identities)
    config_name = _gogs_nginx_config_name(server_name)
    config_file = f"/etc/nginx/sites-available/{config_name}"
    enabled_link = f"/etc/nginx/sites-enabled/{config_name}"
    forwarded_proto = "https" if config.enable_cloudflare else "$scheme"
    client_ip = "$http_cf_connecting_ip" if config.enable_cloudflare else "$remote_addr"
    run("mkdir -p /var/www/letsencrypt/.well-known/acme-challenge")
    _remove_stale_gogs_nginx_sites(config_name)
    with open(config_file, "w", encoding="utf-8") as file_obj:
        file_obj.write(
            generate_gogs_nginx_config(
                server_name,
                backend_port,
                public_tls_port=public_tls_port,
                forwarded_proto=forwarded_proto,
                client_ip=client_ip,
                cloudflare_origin=config.enable_cloudflare,
                redirect_plain_http=bool(domain and config.enable_ssl),
                loopback_only=loopback_only,
                enable_ipv6=bool(domain),
            )
        )
    if not os.path.exists(enabled_link):
        run(f"ln -s {shlex.quote(config_file)} {shlex.quote(enabled_link)}")
    result = run("nginx -t", check=False)
    if result.returncode != 0:
        raise RuntimeError("nginx configuration test failed for Gogs")
    run("systemctl reload nginx")
    print("  ✓ nginx configured for Gogs")

    if config.enable_ssl and domain:
        install_certbot(config)
        if not obtain_letsencrypt_certificate([domain], config.ssl_email, domain):
            raise RuntimeError("Could not obtain the requested Gogs TLS certificate")
        with open(config_file, "w", encoding="utf-8") as file_obj:
            file_obj.write(
                generate_gogs_nginx_config(
                    server_name,
                    backend_port,
                    public_tls_port=public_tls_port,
                    forwarded_proto=forwarded_proto,
                    client_ip=client_ip,
                    cloudflare_origin=config.enable_cloudflare,
                    redirect_plain_http=True,
                    loopback_only=loopback_only,
                    enable_ipv6=True,
                )
            )
        setup_certificate_renewal()
        result = run("nginx -t", check=False)
        if result.returncode != 0:
            raise RuntimeError(
                "nginx configuration test failed after enabling Gogs TLS"
            )
        run("systemctl reload nginx")
        print("  ✓ Let's Encrypt enabled for Gogs")
    elif config.enable_ssl:
        print(
            f"  ✓ Self-signed TLS enabled for Gogs on {server_name}:{public_port}"
        )

    if config.enable_cloudflare:
        run_cloudflare_tunnel_setup(config)
    configure_nginx_auth_failure_ban("gogs", GOGS_AUTH_FAILURE_LOG)


def _configure_git_ssh_access() -> None:
    os.makedirs(GOGS_SSH_DROPIN_DIR, exist_ok=True)
    with open(GOGS_SSH_DROPIN_FILE, "w", encoding="utf-8") as file_obj:
        file_obj.write(
            f"""# Managed by basaltwater for Gogs Git-over-SSH
Match User {GOGS_GIT_USER}
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    PubkeyAuthentication yes
    PermitTTY no
    X11Forwarding no
    AllowTcpForwarding no
    PermitTunnel no
    GatewayPorts no
"""
        )

    reload_result = run("systemctl reload ssh", check=False)
    if reload_result.returncode != 0:
        reload_result = run("systemctl reload sshd", check=False)
    if reload_result.returncode != 0:
        raise RuntimeError("Could not reload SSH after configuring Gogs access")
    print("  ✓ SSH configured for Git-over-SSH access")


def _ufw_numbered_rules() -> list[tuple[int, str, str]]:
    """Return numbered UFW rules as ``(number, comment, line)`` records."""

    result = run("ufw status numbered", check=False, capture_output=True)
    if result.returncode != 0 or not isinstance(result.stdout, str):
        raise RuntimeError("Could not inspect UFW rules for Gogs")
    rules: list[tuple[int, str, str]] = []
    for line in result.stdout.splitlines():
        match = _UFW_NUMBERED_RULE_RE.match(line.strip())
        if not match:
            continue
        comment = line.split("#", 1)[1].strip() if "#" in line else ""
        rules.append((int(match.group(1)), comment, line))
    return rules


def _remove_managed_gogs_rules(
    rules: list[tuple[int, str, str]],
    desired_comments: set[str],
) -> None:
    stale_numbers = [
        number
        for number, comment, _line in rules
        if (
            comment.startswith(_GOGS_RULE_COMMENT_PREFIX)
            or comment in {
                _LEGACY_GOGS_DIRECT_COMMENT,
                _LEGACY_GOGS_WEB_COMMENT,
            }
        )
        and comment not in desired_comments
    ]
    for number in sorted(stale_numbers, reverse=True):
        result = run(f"ufw --force delete {number}", check=False)
        if result.returncode != 0:
            raise RuntimeError("Could not remove a stale managed Gogs firewall rule")


def _reconcile_gogs_direct_firewall(config: SetupConfig, port: int) -> None:
    """Reconcile direct Gogs rules before changing the service listener."""

    active = run(
        "ufw status 2>/dev/null | grep -q 'Status: active'",
        check=False,
    ).returncode == 0
    domain = ""
    if config.gogs:
        domain, _configured_port = parse_gogs_spec(str(config.gogs[0]))
    sources = (
        effective_gogs_ipv4_sources(config)
        if not domain
        else (
            config.effective_access_sources()
            if not config.enable_cloudflare
            else []
        )
    )
    if not active:
        if sources:
            raise RuntimeError(
                "Source-restricted Gogs exposure requires an active UFW firewall"
            )
        available = run("command -v ufw", check=False, capture_output=True)
        if available.returncode != 0:
            return
        existing_rules = _ufw_numbered_rules()
        _remove_managed_gogs_rules(existing_rules, set())
        return

    existing_rules = _ufw_numbered_rules()
    if sources:
        managed_comments = {
            comment
            for _number, comment, _line in existing_rules
            if comment.startswith(_GOGS_RULE_COMMENT_PREFIX)
            or comment in {
                _LEGACY_GOGS_DIRECT_COMMENT,
                _LEGACY_GOGS_WEB_COMMENT,
            }
        }
        conflicting = [
            line
            for _number, comment, line in existing_rules
            if f"{port}/tcp" in line
            and "ALLOW IN" in line
            and comment not in managed_comments
        ]
        if conflicting:
            raise RuntimeError(
                f"Unmanaged UFW allow rules already expose Gogs port {port}; "
                "remove them before using source-restricted Gogs access"
            )

    desired_comments: set[str] = set()
    for source in sources:
        comment = f"{_GOGS_RULE_COMMENT_PREFIX} {port}/tcp source {source}"
        desired_comments.add(comment)
        result = run(
            "ufw allow from "
            f"{shlex.quote(source)} to any port {port} proto tcp "
            f"comment {shlex.quote(comment)}",
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError("Could not install a requested Gogs source rule")

    updated_rules = _ufw_numbered_rules()
    observed_comments = {comment for _number, comment, _line in updated_rules}
    missing = desired_comments - observed_comments
    if missing:
        raise RuntimeError(
            "UFW did not retain all requested Gogs source rules: "
            + ", ".join(sorted(missing))
        )
    _remove_managed_gogs_rules(updated_rules, desired_comments)


def _maybe_configure_firewall(config: SetupConfig, domain: str, port: int) -> None:
    result = run("ufw status 2>/dev/null | grep -q 'Status: active'", check=False)
    if result.returncode != 0:
        return

    if domain:
        if config.enable_cloudflare:
            print("  ✓ Cloudflare tunnel enabled; not exposing public HTTP/HTTPS ports for Gogs")
            return
        if config.effective_access_sources():
            print(
                f"  ✓ Firewall restricts Gogs {port}/tcp to "
                f"{', '.join(config.effective_access_sources())}"
            )
            return
        for rule_port in sorted({80, port}):
            rule = run(
                f"ufw allow {rule_port}/tcp comment 'gogs web'",
                check=False,
            )
            if rule.returncode != 0:
                raise RuntimeError(
                    f"Could not install the Gogs web firewall rule for {rule_port}/tcp"
                )
        print(
            "  ✓ Firewall allows Gogs web access on "
            + " and ".join(f"{rule_port}/tcp" for rule_port in sorted({80, port}))
        )
        return

    sources = effective_gogs_ipv4_sources(config)
    if sources:
        print(
            f"  ✓ Firewall restricts Gogs {port}/tcp to "
            f"{', '.join(sources)}"
        )


def build_gogs_admin_command(args: list[str], config_path: str) -> str:
    """Return a shell-safe command string for running the Gogs CLI as the git user."""
    if len(args) < 2 or args[0] != "admin":
        raise ValueError("Gogs admin command requires 'admin' and a subcommand")
    git_home = _get_git_home()
    custom_dir = os.path.dirname(os.path.dirname(config_path))
    command = [
        "runuser",
        "-u",
        GOGS_GIT_USER,
        "--",
        "env",
        f"HOME={git_home}",
        f"GOGS_WORK_DIR={GOGS_CURRENT_DIR}",
        f"GOGS_CUSTOM={custom_dir}",
        GOGS_EXECUTABLE,
        *args[:2],
        "--config",
        config_path,
        *args[2:],
    ]
    # runuser preserves cwd. Macaron inspects it during process initialization,
    # before GOGS_WORK_DIR is applied, so a private setup home causes a panic.
    return f"cd {shlex.quote(GOGS_CURRENT_DIR)} && {shlex.join(command)}"


def _redacted_admin_create_user_command(config_path: str, username: str, email: str) -> str:
    return build_gogs_admin_command(
        [
            "admin",
            "create-user",
            "--name",
            username,
            "--password",
            "[REDACTED]",
            "--email",
            email,
            "--admin",
        ],
        config_path,
    )


def _gogs_admin_user_exists(config_path: str, username: str, data_path: str) -> bool:
    database_path = f"{data_path}/data/gogs.db"
    if not os.path.exists(database_path):
        return False
    escaped_username = username.replace("'", "''")
    query = (
        "SELECT COUNT(*) FROM user "
        f"WHERE lower(name) = lower('{escaped_username}');"
    )
    result = run(
        f"sqlite3 {shlex.quote(database_path)} {shlex.quote(query)}",
        check=False,
        capture_output=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "1"


def _write_admin_credentials(username: str, password: str) -> None:
    write_json_atomic(
        GOGS_ADMIN_CREDENTIALS_FILE,
        {"username": username, "password": password},
        mode=0o600,
        sort_keys=True,
    )


def _ensure_gogs_admin_account(config: SetupConfig, config_path: str, data_path: str) -> None:
    admin_username = config.username
    if _gogs_admin_user_exists(config_path, admin_username, data_path):
        print(f"  ✓ Gogs admin user already exists: {admin_username}")
        return

    password = get_runtime_credential(config, admin_username)
    if password is None:
        password = generate_password(24)
    email = f"{admin_username}@localhost"
    result = run(
        build_gogs_admin_command(
            [
                "admin",
                "create-user",
                "--name",
                admin_username,
                "--password",
                password,
                "--email",
                email,
                "--admin",
            ],
            config_path,
        ),
        check=False,
        display_cmd=_redacted_admin_create_user_command(config_path, admin_username, email),
    )
    if result.returncode != 0:
        raise RuntimeError("Failed to create initial Gogs admin account")

    _write_admin_credentials(admin_username, password)
    print(f"  ✓ Created initial Gogs admin user: {admin_username}")
    print(f"  ✓ Admin credentials saved to {GOGS_ADMIN_CREDENTIALS_FILE}")


def _run_gogs_post_setup_commands(config_path: str) -> None:
    for args, label in (
        (["admin", "rewrite-authorized-keys"], "authorized_keys"),
        (["admin", "resync-hooks"], "repository hooks"),
    ):
        result = run(build_gogs_admin_command(args, config_path), check=False)
        if result.returncode == 0:
            print(f"  ✓ Refreshed Gogs {label}")
        else:
            raise RuntimeError(f"Failed to refresh Gogs {label}")


def _wait_for_gogs_ready(port: int) -> None:
    result = run(
        "curl --fail --silent --output /dev/null "
        "--connect-timeout 2 --max-time 2 --retry 15 --retry-delay 1 "
        "--retry-max-time 30 --retry-all-errors "
        f"http://127.0.0.1:{port}/",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("Gogs did not become HTTP-ready after service startup")


def _wait_for_gogs_frontend(
    config: SetupConfig,
    domain: str,
    public_port: int,
) -> None:
    """Verify the managed nginx route, including TLS identity when enabled."""
    if not _gogs_uses_reverse_proxy(config, domain):
        return

    retry_options = (
        "--noproxy '*' --connect-timeout 2 --max-time 3 --retry 10 --retry-delay 1 "
        "--retry-max-time 20 --retry-all-errors"
    )
    if config.enable_cloudflare:
        command = (
            "curl --fail --silent --output /dev/null "
            f"{retry_options} -H {shlex.quote(f'Host: {domain}')} "
            "http://127.0.0.1/"
        )
    elif config.enable_ssl:
        client_host = domain or (
            config.host if effective_gogs_ipv4_sources(config) else "127.0.0.1"
        )
        cert_file, _key_file = get_ssl_cert_path(domain or config.host)
        resolve = f"{client_host}:{public_port}:127.0.0.1"
        url = f"https://{client_host}:{public_port}/"
        command = (
            "curl --fail --silent --output /dev/null "
            f"{retry_options} --cacert {shlex.quote(cert_file)} "
            f"--resolve {shlex.quote(resolve)} {shlex.quote(url)}"
        )
    else:
        return

    result = run(command, check=False)
    if result.returncode != 0:
        raise RuntimeError("Gogs public web endpoint failed its nginx/TLS health check")
    print("  ✓ Gogs public web endpoint is healthy")


def _gogs_directory_usage(path: str) -> int:
    if not os.path.exists(path):
        return 0
    result = run(
        f"du -sx -B1 -- {shlex.quote(path)}",
        check=False,
        capture_output=True,
    )
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError(f"Could not measure Gogs storage usage: {path}")
    try:
        return int(result.stdout.split()[0])
    except (IndexError, ValueError) as exc:
        raise RuntimeError(f"Invalid storage usage result for {path}") from exc


def _gogs_backing_filesystem(data_path: str) -> tuple[str, str, str]:
    probe_path = os.path.abspath(data_path)
    while not os.path.exists(probe_path):
        parent = os.path.dirname(probe_path)
        if parent == probe_path:
            break
        probe_path = parent
    mount_result = run(
        f"findmnt -n -o SOURCE,FSTYPE,TARGET -T {shlex.quote(probe_path)}",
        check=False,
        capture_output=True,
    )
    mount_fields = (mount_result.stdout or "").strip().split()
    if mount_result.returncode != 0 or len(mount_fields) < 3:
        raise RuntimeError("Could not identify the filesystem backing Gogs data")
    source, filesystem, mount_target = mount_fields[:3]
    if filesystem.lower() in {"cifs", "smb3"}:
        raise RuntimeError(
            "Gogs live repositories, SQLite, and LFS objects cannot use CIFS storage"
        )
    return source, filesystem, mount_target


def check_gogs_storage_health(data_path: str) -> Mapping[str, Any]:
    """Validate and report the local filesystem backing Gogs live data."""
    source, filesystem, mount_target = _gogs_backing_filesystem(data_path)

    managed_paths = (
        data_path,
        f"{data_path}/data",
        f"{data_path}/data/lfs-objects",
        f"{data_path}/data/attachments",
        f"{data_path}/data/avatars",
        f"{data_path}/data/repo-avatars",
        f"{data_path}/data/sessions",
        f"{data_path}/repositories",
        f"{data_path}/log",
    )
    for path in managed_paths:
        result = run(
            f"runuser -u {shlex.quote(GOGS_GIT_USER)} -- "
            f"test -r {shlex.quote(path)} -a -w {shlex.quote(path)} "
            f"-a -x {shlex.quote(path)}",
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Gogs data path is not accessible and writable by git: {path}"
            )

    database_path = f"{data_path}/data/gogs.db"
    database_result = run(
        f"sqlite3 {shlex.quote(database_path)} 'PRAGMA quick_check;'",
        check=False,
        capture_output=True,
    )
    if database_result.returncode != 0 or (database_result.stdout or "").strip() != "ok":
        raise RuntimeError("Gogs SQLite quick check failed")

    capacity_result = run(
        f"df -B1 --output=avail,iavail {shlex.quote(data_path)}",
        check=False,
        capture_output=True,
    )
    capacity_lines = [line.split() for line in (capacity_result.stdout or "").splitlines()]
    if capacity_result.returncode != 0 or len(capacity_lines) < 2 or len(capacity_lines[-1]) < 2:
        raise RuntimeError("Could not inspect Gogs free bytes and inodes")
    try:
        free_bytes, free_inodes = (int(value) for value in capacity_lines[-1][:2])
    except ValueError as exc:
        raise RuntimeError("Invalid Gogs filesystem capacity result") from exc

    usage = {
        "repositories": _gogs_directory_usage(f"{data_path}/repositories"),
        "lfs_objects": _gogs_directory_usage(f"{data_path}/data/lfs-objects"),
        "attachments": _gogs_directory_usage(f"{data_path}/data/attachments"),
        "logs": _gogs_directory_usage(f"{data_path}/log"),
    }
    print(
        "  ✓ Gogs storage healthy: "
        f"{source} ({filesystem}) mounted at {mount_target}; "
        f"{free_bytes} bytes and {free_inodes} inodes free"
    )
    print(
        "  ✓ Gogs usage: "
        + ", ".join(f"{name}={size} bytes" for name, size in usage.items())
    )
    return {
        "source": source,
        "filesystem": filesystem,
        "mount_target": mount_target,
        "free_bytes": free_bytes,
        "free_inodes": free_inodes,
        "usage": usage,
    }


def _complete_gogs_setup(
    config: SetupConfig,
    *,
    domain: str,
    port: int,
    backend_port: int,
    data_path: str,
    git_home: str,
    config_path: str,
    tag_name: str,
    archive_sha256: str,
) -> None:
    write_text_atomic(
        config_path,
        generate_gogs_app_ini(
            config,
            git_home=git_home,
            data_path=data_path,
            domain=domain,
            port=port,
            backend_port=backend_port,
        ),
        mode=0o600,
    )
    run(f"chown {shlex.quote(GOGS_GIT_USER)}:{shlex.quote(GOGS_GIT_GROUP)} {shlex.quote(config_path)}")
    write_gogs_state(tag_name, data_path, config_path, archive_sha256)
    _configure_git_ssh_access()

    if _gogs_uses_reverse_proxy(config, domain):
        install_nginx(config)
        _write_gogs_nginx_config(config, domain, port, backend_port)
    elif _remove_stale_gogs_nginx_sites():
        result = run("nginx -t", check=False)
        if result.returncode != 0:
            raise RuntimeError(
                "nginx configuration test failed after removing the Gogs proxy"
            )
        run("systemctl reload nginx")

    unit = f"{GOGS_SERVICE}.service"
    replace_units({unit: generate_gogs_service(config_path)}, activate=(unit,))
    print(f"  ✓ Created and started systemd service: {GOGS_SERVICE}")

    if is_service_active(GOGS_SERVICE):
        print(f"  ✓ {GOGS_SERVICE} is running")
    else:
        raise RuntimeError("Gogs service failed to start")

    _wait_for_gogs_ready(backend_port)
    _wait_for_gogs_frontend(config, domain, port)
    _ensure_gogs_admin_account(config, config_path, data_path)
    _run_gogs_post_setup_commands(config_path)
    check_gogs_storage_health(data_path)
    _maybe_configure_firewall(config, domain, port)
    if not domain and not effective_gogs_ipv4_sources(config):
        print(
            f"  Connect with: ssh -L {port}:127.0.0.1:{port} "
            f"{config.username}@{config.host}"
        )


def _rollback_failed_gogs_setup(
    previous_tag: str | None,
    previous_archive_sha256: str | None,
    data_path: str,
    config_path: str,
) -> None:
    if previous_tag:
        try:
            safe_previous_tag = validate_release_tag(previous_tag)
        except ValueError:
            safe_previous_tag = ""
        previous_release = (
            _gogs_release_dir(safe_previous_tag, previous_archive_sha256)
            if safe_previous_tag and previous_archive_sha256
            else ""
        )
        if (
            safe_previous_tag
            and previous_archive_sha256
            and os.path.exists(f"{previous_release}/gogs")
        ):
            run(
                f"ln -sfn {shlex.quote(previous_release)} "
                f"{shlex.quote(GOGS_CURRENT_DIR)}",
                check=False,
            )
            write_gogs_state(
                safe_previous_tag,
                data_path,
                config_path,
                previous_archive_sha256,
            )
            run(f"systemctl restart {GOGS_SERVICE}", check=False)
            print(f"  ⚠ Restored Gogs {safe_previous_tag} after setup failure")
            return
    run(f"systemctl stop {GOGS_SERVICE}", check=False)


def setup_gogs(config: SetupConfig) -> None:
    """Install and run Gogs with HTTP(S) and Git-over-SSH enabled."""
    if not config.gogs:
        return

    domain, port = parse_gogs_spec(str(config.gogs[0]), strict=True)
    backend_port = _gogs_backend_port(config, port)
    data_path = _gogs_data_path(config)
    if config.enable_cloudflare:
        listen_label = (
            f"https://{domain or config.host} via Cloudflare "
            f"-> 127.0.0.1:{backend_port}"
        )
    elif config.enable_ssl:
        public_host = domain or config.host
        listen_label = f"https://{public_host}:{port} -> 127.0.0.1:{backend_port}"
    elif domain:
        listen_label = f"{domain}:{port} -> 127.0.0.1:{backend_port}"
    elif effective_gogs_ipv4_sources(config):
        listen_label = f"private sources -> :{port}"
    else:
        listen_label = f"127.0.0.1:{port} (SSH tunnel only)"
    print(f"  Setting up Gogs: {listen_label}")

    from common.storage_steps import assert_declared_storage_mount

    assert_declared_storage_mount(config, data_path)
    _gogs_backing_filesystem(data_path)

    _reconcile_gogs_direct_firewall(config, port)

    _ensure_gogs_dependencies()
    git_home = _ensure_git_user()
    config_path = _ensure_gogs_data_dirs(data_path)
    previous_state = _load_gogs_state()
    previous_tag = previous_state.get("tag_name")
    previous_archive_sha256 = previous_state.get("archive_sha256")
    tag_name, changed, archive_sha256 = install_or_update_gogs_release()
    try:
        _complete_gogs_setup(
            config,
            domain=domain,
            port=port,
            backend_port=backend_port,
            data_path=data_path,
            git_home=git_home,
            config_path=config_path,
            tag_name=tag_name,
            archive_sha256=archive_sha256,
        )
    except Exception:
        if changed:
            _rollback_failed_gogs_setup(
                previous_tag if isinstance(previous_tag, str) else None,
                (
                    previous_archive_sha256
                    if isinstance(previous_archive_sha256, str)
                    else None
                ),
                data_path,
                config_path,
            )
        raise
