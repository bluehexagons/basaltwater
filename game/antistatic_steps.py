"""Setup steps for the antistatic lobby server.

Deploys the antistatic-server Go binary (https://github.com/bluehexagons/antistatic-server)
as a systemd service behind an nginx reverse proxy.
"""

from __future__ import annotations

import hashlib
import os
import shlex
import tempfile
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.config import SetupConfig

from lib.release_management import (
    detect_release_arch,
    fetch_latest_github_release_asset,
    install_binary_release,
    load_json_state,
    write_json_state,
)
from lib.auth_failure_bans import configure_nginx_auth_failure_ban
from lib.remote_utils import install_package, run, user_exists
from lib.unit_transaction import replace_units


ANTISTATIC_USER = "antistatic"
ANTISTATIC_BINARY = "/usr/local/bin/antistatic-server"
ANTISTATIC_SERVICE = "antistatic"
ANTISTATIC_RELEASE_STATE_FILE = "/opt/infra_tools/state/antistatic_release.json"
ANTISTATIC_DATA_DIR = "/var/lib/antistatic"
ANTISTATIC_CONFIG_DIR = "/etc/antistatic"
ANTISTATIC_ENV_FILE = f"{ANTISTATIC_CONFIG_DIR}/server.env"
ANTISTATIC_AUTH_FAILURE_LOG = (
    "/var/log/nginx/infra-tools-antistatic-auth-failures.log"
)
MIN_ANTISTATIC_RELEASE = (0, 10, 0)
GITHUB_REPO = "bluehexagons/antistatic-server"
DEFAULT_INTERNAL_PORT = 8080
DEFAULT_STUN_PORT = 3478
PROXY_LISTEN_HOST = "127.0.0.1"
TRUSTED_NGINX_PROXY_CIDRS = "127.0.0.1/32,::1/128"

ANTISTATIC_DB_USER = "antistatic-db"
ANTISTATIC_DB_BINARY = "/usr/local/bin/antistatic-db"
ANTISTATIC_DB_SERVICE = "antistatic-db"
ANTISTATIC_DB_RELEASE_STATE_FILE = "/opt/infra_tools/state/antistatic_db_release.json"
ANTISTATIC_DB_GITHUB_REPO = "bluehexagons/antistatic-db"
ANTISTATIC_DB_DATA_DIR = "/var/lib/antistatic-db"
ANTISTATIC_DB_PATH = f"{ANTISTATIC_DB_DATA_DIR}/antistatic.db"
DEFAULT_DB_INTERNAL_PORT = 8081
FirewallRule = tuple[int, str, str]


def parse_antistatic_spec(spec: str, *, strict: bool = False) -> tuple[str, int]:
    """Parse a 'DOMAIN[:port]' spec into (domain, port).

    The port defaults to DEFAULT_INTERNAL_PORT when omitted. Strict mode rejects
    malformed and out-of-range ports instead of applying the legacy fallback.
    """
    spec = spec.strip()
    if spec.isdigit():
        port = int(spec)
        if strict and not 1 <= port <= 65535:
            raise ValueError(f"Antistatic server port must be between 1 and 65535: {port}")
        return "", port
    if ":" in spec:
        domain, _, raw_port = spec.rpartition(":")
        try:
            port = int(raw_port)
        except ValueError:
            if strict:
                raise ValueError(f"Invalid Antistatic server port: {raw_port}") from None
            return domain.strip(), DEFAULT_INTERNAL_PORT
        if strict and not 1 <= port <= 65535:
            raise ValueError(f"Antistatic server port must be between 1 and 65535: {port}")
        return domain.strip(), port
    return spec.strip(), DEFAULT_INTERNAL_PORT


def parse_antistatic_db_spec(spec: str) -> tuple[str, int]:
    """Parse an antistatic-db 'DOMAIN[:port]' spec into (domain, port)."""
    spec = spec.strip()
    if spec.isdigit():
        return "", int(spec)
    if ":" in spec:
        domain, _, raw_port = spec.rpartition(":")
        try:
            return domain.strip(), int(raw_port)
        except ValueError:
            return domain.strip(), DEFAULT_DB_INTERNAL_PORT
    return spec.strip(), DEFAULT_DB_INTERNAL_PORT


def _detect_arch() -> str:
    """Return the GitHub release architecture suffix for this machine."""
    return detect_release_arch()


def _ensure_antistatic_user() -> None:
    """Create a dedicated antistatic system user if one does not already exist."""
    if user_exists(ANTISTATIC_USER):
        print(f"  ✓ System user already exists: {ANTISTATIC_USER}")
        return
    run(
        f"useradd --system --no-create-home --shell /usr/sbin/nologin "
        f"{ANTISTATIC_USER}"
    )
    print(f"  ✓ Created system user: {ANTISTATIC_USER}")


def _ensure_antistatic_db_user() -> None:
    """Create a dedicated antistatic-db system user if needed."""
    if user_exists(ANTISTATIC_DB_USER):
        print(f"  ✓ System user already exists: {ANTISTATIC_DB_USER}")
        return
    run(
        f"useradd --system --no-create-home --shell /usr/sbin/nologin "
        f"{ANTISTATIC_DB_USER}"
    )
    print(f"  ✓ Created system user: {ANTISTATIC_DB_USER}")


def _fetch_latest_antistatic_release(arch: str) -> tuple[str, str]:
    """Return the latest release tag and binary download URL for this architecture."""
    binary_name = f"antistatic-server-linux-{arch}"
    return fetch_latest_github_release_asset(
        GITHUB_REPO,
        asset_matches=lambda _tag_name, asset_name: asset_name == binary_name,
        missing_asset_description=(
            f"No binary found for '{binary_name}' in the latest releases of "
            f"https://github.com/{GITHUB_REPO}"
        ),
    )


def _fetch_latest_antistatic_db_release(arch: str) -> tuple[str, str]:
    """Return the latest antistatic-db release tag and binary URL."""
    binary_name = f"antistatic-db-linux-{arch}"
    return fetch_latest_github_release_asset(
        ANTISTATIC_DB_GITHUB_REPO,
        asset_matches=lambda _tag_name, asset_name: asset_name == binary_name,
        missing_asset_description=(
            f"No binary found for '{binary_name}' in the latest releases of "
            f"https://github.com/{ANTISTATIC_DB_GITHUB_REPO}"
        ),
    )


def _read_installed_antistatic_release() -> str | None:
    """Return the recorded antistatic release tag, if available."""
    if not os.path.exists(ANTISTATIC_RELEASE_STATE_FILE):
        return None
    release_state = load_json_state(
        ANTISTATIC_RELEASE_STATE_FILE,
        read_error_label="antistatic release metadata",
        invalid_state_message="Invalid antistatic release metadata, reinstalling latest release",
    )
    tag_name = release_state.get("tag_name")
    if not isinstance(tag_name, str) or not tag_name:
        print("  ⚠ Warning: Missing antistatic release tag in metadata, reinstalling latest release")
        return None
    return tag_name


def _write_installed_antistatic_release(tag_name: str) -> None:
    """Persist the installed antistatic release tag for future update checks."""
    write_json_state(ANTISTATIC_RELEASE_STATE_FILE, {"tag_name": tag_name})


def _read_installed_antistatic_db_release() -> str | None:
    """Return the recorded antistatic-db release tag, if available."""
    if not os.path.exists(ANTISTATIC_DB_RELEASE_STATE_FILE):
        return None
    release_state = load_json_state(
        ANTISTATIC_DB_RELEASE_STATE_FILE,
        read_error_label="antistatic-db release metadata",
        invalid_state_message="Invalid antistatic-db release metadata, reinstalling latest release",
    )
    tag_name = release_state.get("tag_name")
    if not isinstance(tag_name, str) or not tag_name:
        print("  ⚠ Warning: Missing antistatic-db release tag in metadata, reinstalling latest release")
        return None
    return tag_name


def _write_installed_antistatic_db_release(tag_name: str) -> None:
    """Persist the installed antistatic-db release tag for future update checks."""
    write_json_state(ANTISTATIC_DB_RELEASE_STATE_FILE, {"tag_name": tag_name})


def _download_antistatic_binary(arch: str) -> str:
    """Install the latest antistatic-server release when needed.

    Returns the release tag that should be running after setup completes.
    """
    binary_name = f"antistatic-server-linux-{arch}"
    latest_tag, download_url = _fetch_latest_antistatic_release(arch)
    _require_compatible_antistatic_release(latest_tag)
    return install_binary_release(
        binary_name=binary_name,
        binary_path=ANTISTATIC_BINARY,
        tag_name=latest_tag,
        download_url=download_url,
        installed_tag=_read_installed_antistatic_release(),
        persist_installed_tag=_write_installed_antistatic_release,
    )


def _require_compatible_antistatic_release(tag_name: str) -> None:
    """Reject releases that predate persistent privacy reports and administration."""
    version_text = tag_name.removeprefix("v")
    version_parts = version_text.split(".")
    if len(version_parts) != 3 or not all(part.isdigit() for part in version_parts):
        raise RuntimeError(f"Invalid antistatic-server release tag: {tag_name}")
    version = tuple(int(part) for part in version_parts)
    if version < MIN_ANTISTATIC_RELEASE:
        minimum = ".".join(str(part) for part in MIN_ANTISTATIC_RELEASE)
        raise RuntimeError(
            f"antistatic-server {tag_name} is incompatible; v{minimum} or newer is required"
        )


def _download_antistatic_db_binary(arch: str) -> str:
    """Install the latest antistatic-db release when needed."""
    binary_name = f"antistatic-db-linux-{arch}"
    latest_tag, download_url = _fetch_latest_antistatic_db_release(arch)
    return install_binary_release(
        binary_name=binary_name,
        binary_path=ANTISTATIC_DB_BINARY,
        tag_name=latest_tag,
        download_url=download_url,
        installed_tag=_read_installed_antistatic_db_release(),
        persist_installed_tag=_write_installed_antistatic_db_release,
    )


def _antistatic_service_listen_options(domain: str) -> tuple[str, bool]:
    """Return (host, trust_proxy) for antistatic-server systemd execution."""
    if domain:
        return PROXY_LISTEN_HOST, True
    return "", False


def generate_antistatic_service(
    port: int,
    host: str = "",
    trust_proxy: bool = True,
    stun_port: int = DEFAULT_STUN_PORT,
) -> str:
    """Return systemd unit file content for the antistatic server."""
    host_args = f" -host {host}" if host else ""
    stun_args = f" -stun-port {stun_port}" if stun_port > 0 else ""
    proxy_args = (
        f" -trust-proxy -trusted-proxy-cidrs {TRUSTED_NGINX_PROXY_CIDRS}"
        if trust_proxy
        else ""
    )
    return f"""\
[Unit]
Description=Antistatic lobby server
Documentation=https://github.com/bluehexagons/antistatic-server
After=network.target
Wants=network.target
StartLimitIntervalSec=60
StartLimitBurst=3

[Service]
Type=simple
User={ANTISTATIC_USER}
Group={ANTISTATIC_USER}
StateDirectory=antistatic
WorkingDirectory={ANTISTATIC_DATA_DIR}
Environment=ANTISTATIC_DATA_DIR={ANTISTATIC_DATA_DIR}
EnvironmentFile=-{ANTISTATIC_ENV_FILE}
ExecStart={ANTISTATIC_BINARY}{host_args} -port {port}{stun_args}{proxy_args}
ExecStartPost=/usr/bin/curl --fail --silent --show-error --retry 5 --retry-connrefused --retry-delay 1 --max-time 2 http://127.0.0.1:{port}/health
Restart=on-failure
RestartSec=5
TimeoutStopSec=40s
UMask=0077

NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes

[Install]
WantedBy=multi-user.target
"""


def generate_antistatic_db_service(port: int, host: str = "127.0.0.1") -> str:
    """Return systemd unit file content for antistatic-db."""
    host_args = f" -host {host}" if host else ""
    return f"""\
[Unit]
Description=Antistatic DB service
Documentation=https://github.com/bluehexagons/antistatic-db
After=network.target
Wants=network.target
StartLimitIntervalSec=60
StartLimitBurst=3

[Service]
Type=simple
User={ANTISTATIC_DB_USER}
Group={ANTISTATIC_DB_USER}
StateDirectory=antistatic-db
WorkingDirectory={ANTISTATIC_DB_DATA_DIR}
ExecStart={ANTISTATIC_DB_BINARY}{host_args} -port {port} -db {ANTISTATIC_DB_PATH} -trust-proxy
Restart=on-failure
RestartSec=5

NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes

[Install]
WantedBy=multi-user.target
"""


def generate_antistatic_nginx_config(
    domain: str,
    port: int,
    *,
    enable_https_redirect: bool = True,
    forwarded_proto: str = "$scheme",
    forwarded_client_ip: str = "$remote_addr",
    private_origin: bool = False,
) -> str:
    """Return an nginx site config that proxies all traffic to the antistatic server."""
    if not domain:
        raise ValueError("Antistatic nginx config requires a non-empty domain")

    from lib.nginx_config import SSL_PROTOCOLS, SSL_CIPHERS, get_ssl_cert_path

    cert_file, key_file = get_ssl_cert_path(domain)
    zone_suffix = hashlib.sha256(domain.encode("utf-8")).hexdigest()[:12]
    admin_zone = f"infra_tools_antistatic_admin_{zone_suffix}"
    auth_failure = f"infra_tools_antistatic_auth_failure_{zone_suffix}"
    log_format = f"infra_tools_antistatic_auth_{zone_suffix}"
    http_listeners = (
        "    listen 127.0.0.1:80;\n    listen [::1]:80;"
        if private_origin
        else "    listen 80;\n    listen [::]:80;"
    )
    https_listeners = (
        "    listen 127.0.0.1:443 ssl;\n    listen [::1]:443 ssl;"
        if private_origin
        else "    listen 443 ssl;\n    listen [::]:443 ssl;"
    )

    proxy_settings = f"""        proxy_pass http://127.0.0.1:{port};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP {forwarded_client_ip};
        proxy_set_header X-Forwarded-For {forwarded_client_ip};
        proxy_set_header X-Forwarded-Proto {forwarded_proto};
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_read_timeout 30s;
        proxy_buffering on;
        proxy_intercept_errors off;
"""
    admin_proxy_settings = f"""        limit_req zone={admin_zone} burst=10 nodelay;
        limit_req_status 429;
{proxy_settings}"""
    admin_location = f"""\
    location = /admin {{
{admin_proxy_settings}    }}

    location ^~ /admin/ {{
{admin_proxy_settings}    }}
"""
    http_location = """\
    location / {
        return 301 https://$host$request_uri;
    }
""" if enable_https_redirect else f"""\
{admin_location}
    location / {{
{proxy_settings}    }}
"""

    return f"""\
limit_req_zone {forwarded_client_ip} zone={admin_zone}:10m rate=10r/m;

map "$http_authorization:$status:$uri" ${auth_failure} {{
    default 0;
    ~^.+:401:/admin(?:/|$) 1;
}}

log_format {log_format} '{forwarded_client_ip} [$time_local] infra-tools-auth-failure';

server {{
{http_listeners}
    server_name {domain};
    access_log {ANTISTATIC_AUTH_FAILURE_LOG} {log_format} if=${auth_failure};

    location /.well-known/acme-challenge/ {{
        root /var/www/letsencrypt;
    }}
{http_location}}}

server {{
{https_listeners}
    http2 on;

    server_name {domain};

    ssl_certificate {cert_file};
    ssl_certificate_key {key_file};
    ssl_protocols {SSL_PROTOCOLS};
    ssl_prefer_server_ciphers on;
    ssl_ciphers {SSL_CIPHERS};

    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;
    access_log {ANTISTATIC_AUTH_FAILURE_LOG} {log_format} if=${auth_failure};

{admin_location}
    location / {{
{proxy_settings}    }}
}}
"""


def _configure_nginx_proxy(config: SetupConfig, domain: str, port: int) -> None:
    """Write an nginx site config and reload nginx."""
    from lib.nginx_config import generate_self_signed_cert

    generate_self_signed_cert(domain)

    domain_slug = domain.replace(".", "_").replace("-", "_")
    config_name = f"antistatic_{domain_slug}"
    config_file = f"/etc/nginx/sites-available/{config_name}"
    enabled_link = f"/etc/nginx/sites-enabled/{config_name}"

    run("mkdir -p /var/www/letsencrypt/.well-known/acme-challenge")

    forwarded_proto = "https" if config.enable_cloudflare else "$scheme"
    forwarded_client_ip = "$http_cf_connecting_ip" if config.enable_cloudflare else "$remote_addr"
    config_content = generate_antistatic_nginx_config(
        domain,
        port,
        enable_https_redirect=config.enable_ssl and not config.enable_cloudflare,
        forwarded_proto=forwarded_proto,
        forwarded_client_ip=forwarded_client_ip,
        private_origin=config.enable_cloudflare,
    )
    with open(config_file, "w", encoding="utf-8") as fh:
        fh.write(config_content)
    print(f"  ✓ Created nginx config: {config_file}")

    if not os.path.exists(enabled_link):
        run(f"ln -s {shlex.quote(config_file)} {shlex.quote(enabled_link)}")
        print(f"  ✓ Enabled nginx site: {config_name}")

    result = run("nginx -t", check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Invalid nginx configuration for {domain}")
    reload_result = run("systemctl reload nginx", check=False)
    if reload_result.returncode != 0:
        raise RuntimeError(f"Failed to reload nginx for {domain}")
    print("  ✓ nginx reloaded")

    if config.enable_ssl and not config.enable_cloudflare:
        from web.ssl_steps import (
            install_certbot,
            obtain_letsencrypt_certificate,
            setup_certificate_renewal,
        )

        install_certbot(config)
        if obtain_letsencrypt_certificate([domain], config.ssl_email, domain):
            with open(config_file, "w", encoding="utf-8") as fh:
                fh.write(
                    generate_antistatic_nginx_config(
                        domain,
                        port,
                        enable_https_redirect=True,
                        forwarded_proto=forwarded_proto,
                        forwarded_client_ip=forwarded_client_ip,
                        private_origin=False,
                    )
                )
            setup_certificate_renewal()
            result = run("nginx -t", check=False)
            if result.returncode == 0:
                reload_result = run("systemctl reload nginx", check=False)
                if reload_result.returncode != 0:
                    raise RuntimeError(f"Failed to reload nginx for {domain}")
                print("  ✓ Let's Encrypt enabled for antistatic-server")
            else:
                raise RuntimeError(f"Invalid Let's Encrypt nginx configuration for {domain}")
        else:
            raise RuntimeError(f"Failed to obtain a trusted certificate for {domain}")

    if config.enable_cloudflare:
        from web.cloudflare_steps import run_cloudflare_tunnel_setup

        run_cloudflare_tunnel_setup(config)
    configure_nginx_auth_failure_ban("antistatic", ANTISTATIC_AUTH_FAILURE_LOG)


def _remove_empty_domain_nginx_proxy() -> None:
    """Remove stale configs generated by the old ':PORT' hostless parser bug."""
    for path in (
        "/etc/nginx/sites-enabled/antistatic_",
        "/etc/nginx/sites-available/antistatic_",
    ):
        if os.path.lexists(path):
            os.remove(path)
            print(f"  ✓ Removed invalid nginx config: {path}")


def _maybe_configure_nginx_proxy(
    config: SetupConfig,
    domain: str,
    port: int,
    service_name: str,
) -> None:
    if domain:
        _configure_nginx_proxy(config, domain, port)
        return

    _remove_empty_domain_nginx_proxy()
    print(
        f"  ✓ No hostname configured; skipping nginx proxy for {service_name} "
        f"(service listens directly on :{port})"
    )


def get_antistatic_public_firewall_rules(domain: str, port: int) -> tuple[FirewallRule, ...]:
    """Return required public firewall rules for antistatic-server."""
    rules: list[FirewallRule] = []
    if not domain:
        rules.append((port, "tcp", f"{ANTISTATIC_SERVICE} direct port"))
    rules.append((DEFAULT_STUN_PORT, "udp", f"{ANTISTATIC_SERVICE} STUN"))
    return tuple(rules)


def _maybe_configure_firewall_rules(rules: tuple[FirewallRule, ...], service_name: str) -> None:
    """Allow service firewall rules when UFW is already enforcing rules."""
    if not rules:
        return

    result = run("ufw status 2>/dev/null | grep -q 'Status: active'", check=False)
    if result.returncode != 0:
        allowed_ports = ", ".join(f"{port}/{protocol}" for port, protocol, _ in rules)
        print(
            f"  ✓ Firewall inactive; no port rules needed for {service_name} "
            f"({allowed_ports})"
        )
        return

    for rule_port, protocol, comment in rules:
        result = run(
            f"ufw allow {rule_port}/{protocol} comment {shlex.quote(comment)}",
            check=False,
        )
        if result.returncode == 0:
            print(f"  ✓ Firewall allows {comment}: {rule_port}/{protocol}")
        else:
            print(f"  ⚠ Warning: Failed to allow {comment}: {rule_port}/{protocol}")


def _maybe_configure_direct_port_firewall(domain: str, port: int, service_name: str) -> None:
    """Allow the direct hostless service port when UFW is already enforcing rules."""
    if domain:
        return

    _maybe_configure_firewall_rules(
        ((port, "tcp", f"{service_name} direct port"),),
        service_name,
    )


def _maybe_configure_antistatic_firewall(domain: str, port: int) -> None:
    """Allow antistatic-server public ports when UFW is already enforcing rules."""
    _maybe_configure_firewall_rules(
        get_antistatic_public_firewall_rules(domain, port),
        ANTISTATIC_SERVICE,
    )


def _quote_systemd_environment_value(value: str) -> str:
    """Quote a validated value for a systemd EnvironmentFile assignment."""
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Antistatic environment values must not contain control characters")
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _antistatic_admin_password(config: SetupConfig) -> str | None:
    if not config.antistatic_admin:
        return None
    for credential in config.share_credentials or []:
        if len(credential) == 2 and credential[0] == config.antistatic_admin:
            return credential[1]
    raise ValueError(f"Missing credential for Antistatic admin: {config.antistatic_admin}")


def _configure_antistatic_environment(config: SetupConfig) -> None:
    """Install or remove the root-only optional admin environment file."""
    password = _antistatic_admin_password(config)
    if password is None:
        if os.path.exists(ANTISTATIC_ENV_FILE):
            os.remove(ANTISTATIC_ENV_FILE)
            print("  ✓ Disabled antistatic-server admin credentials")
        return

    if os.path.lexists(ANTISTATIC_CONFIG_DIR) and (
        os.path.islink(ANTISTATIC_CONFIG_DIR) or not os.path.isdir(ANTISTATIC_CONFIG_DIR)
    ):
        raise ValueError(f"Antistatic config path must be a real directory: {ANTISTATIC_CONFIG_DIR}")
    os.makedirs(ANTISTATIC_CONFIG_DIR, mode=0o700, exist_ok=True)
    os.chown(ANTISTATIC_CONFIG_DIR, 0, 0)
    os.chmod(ANTISTATIC_CONFIG_DIR, 0o700)
    content = (
        f"ANTISTATIC_ADMIN_USERNAME={_quote_systemd_environment_value(config.antistatic_admin or '')}\n"
        f"ANTISTATIC_ADMIN_PASSWORD={_quote_systemd_environment_value(password)}\n"
    )
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=ANTISTATIC_CONFIG_DIR,
        prefix=".server.env-",
        delete=False,
    ) as file_obj:
        temp_path = file_obj.name
        file_obj.write(content)
    try:
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, ANTISTATIC_ENV_FILE)
        os.chmod(ANTISTATIC_ENV_FILE, 0o600)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
    print("  ✓ Configured antistatic-server admin credentials")


def _ensure_antistatic_dependencies() -> None:
    """Install commands needed for release setup and systemd health checks."""
    if not install_package(
        "CA certificates",
        "ca-certificates",
        "apt-get install -y -qq ca-certificates",
    ):
        raise RuntimeError("CA certificates are required to install antistatic-server")
    if not install_package("curl", "curl", "apt-get install -y -qq curl ca-certificates"):
        raise RuntimeError("curl is required to install and monitor antistatic-server")


def preflight_antistatic_releases(config: SetupConfig) -> None:
    """Validate release availability before existing services are removed."""
    if not (config.antistatic_server or config.antistatic_db) or config.dry_run:
        return
    _ensure_antistatic_dependencies()
    arch = _detect_arch()
    if config.antistatic_server:
        tag_name, _download_url = _fetch_latest_antistatic_release(arch)
        _require_compatible_antistatic_release(tag_name)
    if config.antistatic_db:
        _fetch_latest_antistatic_db_release(arch)


def setup_antistatic_server(config: SetupConfig) -> None:
    """Install and run the antistatic lobby server behind nginx."""
    if not config.antistatic_server:
        return

    domain, port = parse_antistatic_spec(config.antistatic_server)
    service_host, trust_proxy = _antistatic_service_listen_options(domain)
    listen_label = f"{domain} → 127.0.0.1:{port}" if domain else f":{port}"
    print(f"  Setting up antistatic server: {listen_label}")

    from web.web_steps import install_nginx
    install_nginx(config)
    _ensure_antistatic_dependencies()

    _ensure_antistatic_user()
    _download_antistatic_binary(_detect_arch())
    _configure_antistatic_environment(config)

    unit = f"{ANTISTATIC_SERVICE}.service"
    replace_units({unit: generate_antistatic_service(
        port, host=service_host, trust_proxy=trust_proxy,
    )}, activate=(unit,))
    print(f"  ✓ Created and started systemd service: {ANTISTATIC_SERVICE}")

    _maybe_configure_nginx_proxy(config, domain, port, ANTISTATIC_SERVICE)
    _maybe_configure_antistatic_firewall(domain, port)
    if config.enable_cloudflare:
        print(
            "  ℹ Cloudflare tunnels do not proxy UDP; antistatic still needs "
            f"direct IP reachability on {DEFAULT_STUN_PORT}/udp"
        )


def setup_antistatic_db(config: SetupConfig) -> None:
    """Install and run antistatic-db behind nginx from GitHub releases."""
    if not config.antistatic_db:
        return

    domain, port = parse_antistatic_db_spec(config.antistatic_db)
    db_host = "127.0.0.1" if domain else ""
    listen_label = f"{domain} → 127.0.0.1:{port}" if domain else f":{port}"
    print(f"  Setting up antistatic-db: {listen_label}")

    from web.web_steps import install_nginx
    install_nginx(config)
    _ensure_antistatic_dependencies()

    _ensure_antistatic_db_user()
    _download_antistatic_db_binary(_detect_arch())

    unit = f"{ANTISTATIC_DB_SERVICE}.service"
    replace_units({unit: generate_antistatic_db_service(port, host=db_host)}, activate=(unit,))
    print(f"  ✓ Created and started systemd service: {ANTISTATIC_DB_SERVICE}")

    _maybe_configure_nginx_proxy(config, domain, port, ANTISTATIC_DB_SERVICE)
    _maybe_configure_direct_port_firewall(domain, port, ANTISTATIC_DB_SERVICE)
