"""Configure the shared HTTPS host used by internal sites and Godot exports."""

from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import os
import pwd
import secrets
import shlex
import socket
import tempfile
from collections.abc import Sequence

from common.agent_steps import BASE_AGENT_SKILL_NAMES, install_managed_agent_skills
from lib.atomic_io import write_text_atomic
from lib.config import GODOT_WEB_HTTPS_PORT
from lib.remote_utils import install_package, is_package_installed, is_service_active, run
from lib.validation import validate_filesystem_path, validate_network_ip_or_cidr
from lib.validators import validate_host, validate_username


GODOT_WEB_ROOT = "/srv/basaltwater/web"
GODOT_WEB_GAMES_ROOT = f"{GODOT_WEB_ROOT}/games"
INTERNAL_WEB_SITES_ROOT = f"{GODOT_WEB_ROOT}/sites"
GODOT_WEB_CA_DOWNLOAD = f"{GODOT_WEB_ROOT}/basaltwater-ca.crt"
GODOT_WEB_PKI_DIR = "/var/lib/basaltwater/internal-web-pki"
GODOT_WEB_CA_CERT = f"{GODOT_WEB_PKI_DIR}/ca.crt"
GODOT_WEB_CA_KEY = f"{GODOT_WEB_PKI_DIR}/ca.key"
GODOT_WEB_CERT = f"{GODOT_WEB_PKI_DIR}/server.crt"
GODOT_WEB_KEY = f"{GODOT_WEB_PKI_DIR}/server.key"
GODOT_WEB_TRUST_CERT = (
    "/usr/local/share/ca-certificates/basaltwater-internal-web-ca.crt"
)
GODOT_WEB_NGINX_SITE = "/etc/nginx/sites-available/basaltwater-godot-web"
GODOT_WEB_NGINX_LINK = "/etc/nginx/sites-enabled/basaltwater-godot-web"
GODOT_WEB_URL_FILE = "/etc/basaltwater/internal-web/base-url"
GODOT_WEB_PUBLISHER = "/opt/basaltwater/common/service_tools/godot_web_publish.py"
GODOT_WEB_PUBLISHER_LINK = "/usr/local/bin/godot-web-publish"
GODOT_WEB_UTILITY = "/opt/basaltwater/common/service_tools/basaltwater_web.py"
GODOT_WEB_UTILITY_LINK = "/usr/local/bin/basaltwater-web"
GODOT_WEB_POLICY_FILE = "/etc/basaltwater/internal-web/policy.json"
GODOT_WEB_FORWARD_PORT_MIN = 8444
GODOT_WEB_FORWARD_PORT_MAX = 8999
GODOT_AGENT_SKILLS_ROOT = "/opt/basaltwater/common/agent_skills"
GODOT_AGENT_SKILLS = (
    *BASE_AGENT_SKILL_NAMES,
    "basaltwater-godot-web",
    "basaltwater-web-gateway",
)

_NGINX_MARKER = "# Managed by basaltwater Godot web hosting"
_LOCAL_CA_COMMON_NAME = "basaltwater VM-local Web CA"
_CHROMIUM_CA_NICKNAME = "basaltwater internal web CA"
_CHROMIUM_NSS_DB_RELATIVE = (".local", "share", "pki", "nssdb")
_CHROMIUM_NSS_LEGACY_DB_RELATIVE = (".pki", "nssdb")
_CERTIFICATE_RENEWAL_SECONDS = 30 * 24 * 60 * 60


def _ensure_managed_directory(path: str, mode: int) -> None:
    """Create one managed directory without accepting a symlink target."""

    validate_filesystem_path(path, must_exist=False)
    if os.path.lexists(path):
        if os.path.islink(path) or not os.path.isdir(path):
            raise RuntimeError(f"Refusing unsafe managed web directory: {path}")
    else:
        os.makedirs(path, mode=mode)
    os.chmod(path, mode)


def validate_web_identities(values: list[str]) -> list[str]:
    """Validate and normalize certificate DNS names and IP addresses."""

    identities: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip().rstrip(".")
        if normalized.startswith("[") and normalized.endswith("]"):
            normalized = normalized[1:-1]
        try:
            normalized = str(ipaddress.ip_address(normalized))
        except ValueError:
            normalized = normalized.lower()
            if not validate_host(normalized):
                raise ValueError(f"Invalid internal HTTPS identity: {value}")
        if normalized not in seen:
            seen.add(normalized)
            identities.append(normalized)
    if not identities:
        raise ValueError("At least one internal HTTPS identity is required")
    return identities


def identities_for_config(host: str, system_hostname: str | None = None) -> list[str]:
    """Return stable certificate identities for one configured target."""

    values = [host]
    if system_hostname:
        values.append(system_hostname)
    values.extend(("localhost", "127.0.0.1", "::1"))
    return validate_web_identities(values)


def discover_local_web_identities() -> list[str]:
    """Return active interface addresses and hostnames for HTTPS access."""

    values: list[str] = []
    result = run("hostname -I", check=False, capture_output=True)
    if result.returncode == 0:
        values.extend((result.stdout or "").split())
    values.extend(
        (socket.getfqdn(), socket.gethostname(), "localhost", "127.0.0.1", "::1")
    )

    identities: list[str] = []
    for value in values:
        if not value:
            continue
        try:
            normalized = validate_web_identities([value])[0]
        except ValueError:
            continue
        if normalized not in identities:
            identities.append(normalized)
    return identities


def _certificate_name_check(cert_path: str, identity: str) -> bool:
    try:
        ipaddress.ip_address(identity)
    except ValueError:
        option = "-checkhost"
    else:
        option = "-checkip"
    result = run(
        "openssl x509 -noout "
        f"{option} {shlex.quote(identity)} -in {shlex.quote(cert_path)}",
        check=False,
        capture_output=True,
    )
    return result.returncode == 0


def _certificate_key_matches(cert_path: str, key_path: str) -> bool:
    cert_digest = run(
        f"openssl x509 -in {shlex.quote(cert_path)} -pubkey -noout | "
        "openssl pkey -pubin -outform DER | openssl sha256",
        check=False,
        capture_output=True,
    )
    key_digest = run(
        f"openssl pkey -in {shlex.quote(key_path)} -pubout -outform DER | "
        "openssl sha256",
        check=False,
        capture_output=True,
    )
    return (
        cert_digest.returncode == 0
        and key_digest.returncode == 0
        and bool(cert_digest.stdout)
        and cert_digest.stdout == key_digest.stdout
    )


def _certificate_is_usable(
    cert_path: str,
    key_path: str,
    identities: list[str],
) -> bool:
    if not os.path.isfile(cert_path) or not os.path.isfile(key_path):
        return False
    current = run(
        "openssl x509 "
        f"-checkend {_CERTIFICATE_RENEWAL_SECONDS} -noout "
        f"-in {shlex.quote(cert_path)}",
        check=False,
        capture_output=True,
    )
    return (
        current.returncode == 0
        and _certificate_key_matches(cert_path, key_path)
        and all(_certificate_name_check(cert_path, identity) for identity in identities)
    )


def _managed_pair_is_partial(cert_path: str, key_path: str) -> bool:
    return os.path.exists(cert_path) != os.path.exists(key_path)


def _openssl_extensions(identities: list[str]) -> str:
    alternative_names: list[str] = []
    dns_index = 0
    ip_index = 0
    for identity in identities:
        try:
            ipaddress.ip_address(identity)
        except ValueError:
            dns_index += 1
            alternative_names.append(f"DNS.{dns_index} = {identity}")
        else:
            ip_index += 1
            alternative_names.append(f"IP.{ip_index} = {identity}")
    return "\n".join(
        [
            "[server_cert]",
            "basicConstraints = critical,CA:FALSE",
            "keyUsage = critical,digitalSignature,keyEncipherment",
            "extendedKeyUsage = serverAuth",
            "subjectAltName = @alternative_names",
            "",
            "[alternative_names]",
            *alternative_names,
            "",
        ]
    )


def _ensure_local_ca() -> bool:
    """Create the VM-local CA once and preserve its trust identity."""

    if _managed_pair_is_partial(GODOT_WEB_CA_CERT, GODOT_WEB_CA_KEY):
        raise RuntimeError("The managed internal-web CA is incomplete")
    if os.path.exists(GODOT_WEB_CA_CERT):
        if not _certificate_key_matches(GODOT_WEB_CA_CERT, GODOT_WEB_CA_KEY):
            raise RuntimeError("The managed internal-web CA key does not match")
        return False

    _ensure_managed_directory(GODOT_WEB_PKI_DIR, 0o700)
    with tempfile.TemporaryDirectory(
        prefix=".ca-", dir=GODOT_WEB_PKI_DIR
    ) as temporary_dir:
        cert_path = os.path.join(temporary_dir, "ca.crt")
        key_path = os.path.join(temporary_dir, "ca.key")
        run(
            "openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 3650 "
            f"-subj {shlex.quote('/CN=' + _LOCAL_CA_COMMON_NAME)} "
            "-addext 'basicConstraints=critical,CA:TRUE' "
            "-addext 'keyUsage=critical,keyCertSign,cRLSign' "
            f"-keyout {shlex.quote(key_path)} -out {shlex.quote(cert_path)}",
            check=True,
        )
        os.chmod(key_path, 0o600)
        os.chmod(cert_path, 0o644)
        os.replace(key_path, GODOT_WEB_CA_KEY)
        os.replace(cert_path, GODOT_WEB_CA_CERT)
    return True


def _ensure_local_server_certificate(identities: list[str]) -> bool:
    """Issue or renew a server certificate from the preserved local CA."""

    if _certificate_is_usable(GODOT_WEB_CERT, GODOT_WEB_KEY, identities):
        return False
    if _managed_pair_is_partial(GODOT_WEB_CERT, GODOT_WEB_KEY):
        raise RuntimeError("The managed internal-web server certificate is incomplete")

    _ensure_managed_directory(GODOT_WEB_PKI_DIR, 0o700)
    with tempfile.TemporaryDirectory(
        prefix=".server-", dir=GODOT_WEB_PKI_DIR
    ) as temporary_dir:
        key_path = os.path.join(temporary_dir, "server.key")
        request_path = os.path.join(temporary_dir, "server.csr")
        cert_path = os.path.join(temporary_dir, "server.crt")
        extension_path = os.path.join(temporary_dir, "extensions.cnf")
        write_text_atomic(
            extension_path,
            _openssl_extensions(identities),
            mode=0o600,
        )
        run(
            "openssl req -new -newkey rsa:3072 -sha256 -nodes "
            "-subj '/CN=basaltwater internal web' "
            f"-keyout {shlex.quote(key_path)} -out {shlex.quote(request_path)}",
            check=True,
        )
        serial = secrets.token_hex(16)
        run(
            "openssl x509 -req -sha256 -days 397 "
            f"-in {shlex.quote(request_path)} "
            f"-CA {shlex.quote(GODOT_WEB_CA_CERT)} "
            f"-CAkey {shlex.quote(GODOT_WEB_CA_KEY)} "
            f"-set_serial 0x{serial} "
            f"-extfile {shlex.quote(extension_path)} -extensions server_cert "
            f"-out {shlex.quote(cert_path)}",
            check=True,
        )
        os.chmod(key_path, 0o600)
        os.chmod(cert_path, 0o644)
        os.replace(key_path, GODOT_WEB_KEY)
        os.replace(cert_path, GODOT_WEB_CERT)
    return True


def _install_local_ca_trust() -> bool:
    with open(GODOT_WEB_CA_CERT, encoding="utf-8") as cert_file:
        content = cert_file.read()
    previous = None
    try:
        with open(GODOT_WEB_TRUST_CERT, encoding="utf-8") as cert_file:
            previous = cert_file.read()
    except OSError:
        pass
    if previous == content:
        return False
    write_text_atomic(GODOT_WEB_TRUST_CERT, content, mode=0o644)
    run("update-ca-certificates", check=True)
    return True


def _pem_payload(content: str) -> str:
    """Return normalized base64 content from one PEM certificate."""

    return "".join(
        line.strip()
        for line in content.splitlines()
        if line.strip() and not line.startswith("-----")
    )


def _install_chromium_ca_trust(users: list[str]) -> bool:
    """Trust the VM-local CA in each managed user's Chromium NSS database."""

    if not is_package_installed("libnss3-tools") and not install_package(
        "NSS certificate tools",
        "libnss3-tools",
        "apt-get -o DPkg::Lock::Timeout=60 install -y -qq libnss3-tools",
    ):
        raise RuntimeError("Failed to install Chromium certificate trust tools")

    with open(GODOT_WEB_TRUST_CERT, encoding="utf-8") as cert_file:
        expected_payload = _pem_payload(cert_file.read())
    changed = False
    for username in users:
        if not validate_username(username):
            raise ValueError(f"Invalid Chromium trust username: {username}")
        account = pwd.getpwnam(username)
        home = account.pw_dir
        validate_filesystem_path(home, must_exist=True)
        legacy_database = os.path.join(home, *_CHROMIUM_NSS_LEGACY_DB_RELATIVE)
        database_dir = (
            legacy_database
            if os.path.isdir(legacy_database)
            else os.path.join(home, *_CHROMIUM_NSS_DB_RELATIVE)
        )
        validate_filesystem_path(database_dir, must_exist=False)
        if os.path.lexists(database_dir) and (
            os.path.islink(database_dir) or not os.path.isdir(database_dir)
        ):
            raise RuntimeError(
                f"Refusing unsafe Chromium certificate database: {database_dir}"
            )

        safe_username = shlex.quote(username)
        safe_home = shlex.quote(home)
        safe_database_dir = shlex.quote(database_dir)
        database = shlex.quote(f"sql:{database_dir}")
        user_command = f"runuser -u {safe_username} -- env HOME={safe_home}"
        run(
            f"{user_command} mkdir -p -- {safe_database_dir}",
            check=True,
        )
        run(
            f"{user_command} chmod 700 -- {safe_database_dir}",
            check=True,
        )
        database_created = not os.path.isfile(os.path.join(database_dir, "cert9.db"))
        if database_created:
            run(
                f"{user_command} certutil -N --empty-password -d {database}",
                check=True,
            )

        safe_nickname = shlex.quote(_CHROMIUM_CA_NICKNAME)
        existing = run(
            f"{user_command} certutil -L -d {database} -n {safe_nickname} "
            "-a -f /dev/null",
            check=False,
            capture_output=True,
        )
        same_certificate = (
            existing.returncode == 0
            and _pem_payload(existing.stdout or "") == expected_payload
        )
        if same_certificate:
            run(
                f"{user_command} certutil -M -d {database} -n {safe_nickname} "
                "-t 'C,,' -f /dev/null",
                check=True,
            )
            changed = database_created or changed
            continue
        if existing.returncode == 0:
            run(
                f"{user_command} certutil -D -d {database} -n {safe_nickname} "
                "-f /dev/null",
                check=True,
            )
        run(
            f"{user_command} certutil -A -d {database} -n {safe_nickname} "
            f"-t 'C,,' -i {shlex.quote(GODOT_WEB_TRUST_CERT)} -f /dev/null",
            check=True,
        )
        changed = True
    return changed


def _letsencrypt_certificate(identities: list[str]) -> tuple[str, str] | None:
    """Reuse an already enrolled public certificate when it covers all names."""

    try:
        ipaddress.ip_address(identities[0])
    except ValueError:
        pass
    else:
        return None
    if identities[0] == "localhost":
        return None
    dns_identities: list[str] = []
    for identity in identities:
        try:
            ipaddress.ip_address(identity)
        except ValueError:
            if identity != "localhost":
                dns_identities.append(identity)
    for identity in dns_identities:
        cert_path = f"/etc/letsencrypt/live/{identity}/fullchain.pem"
        key_path = f"/etc/letsencrypt/live/{identity}/privkey.pem"
        if _certificate_is_usable(cert_path, key_path, dns_identities):
            return cert_path, key_path
    return None


def _certificate_for_identities(
    identities: list[str],
) -> tuple[str, str, bool, bool]:
    public_pair = _letsencrypt_certificate(identities)
    if public_pair:
        return public_pair[0], public_pair[1], False, False
    ca_changed = _ensure_local_ca()
    cert_changed = _ensure_local_server_certificate(identities)
    trust_changed = _install_local_ca_trust()
    return GODOT_WEB_CERT, GODOT_WEB_KEY, True, ca_changed or cert_changed or trust_changed


def _url_host(identity: str) -> str:
    return f"[{identity}]" if ":" in identity else identity


def _base_url(identities: list[str]) -> str:
    preferred = next(
        (identity for identity in identities if identity not in {"localhost", "127.0.0.1", "::1"}),
        identities[0],
    )
    return f"https://{_url_host(preferred)}:{GODOT_WEB_HTTPS_PORT}"


def render_nginx_config(cert_path: str, key_path: str) -> str:
    """Render the isolated HTTPS static host for web games and tools."""

    return f"""{_NGINX_MARKER}
server {{
    listen {GODOT_WEB_HTTPS_PORT} ssl;
    listen [::]:{GODOT_WEB_HTTPS_PORT} ssl;
    http2 on;
    server_name _;

    ssl_certificate {cert_path};
    ssl_certificate_key {key_path};
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:basaltwater_internal_web:10m;
    ssl_session_timeout 1d;

    root {GODOT_WEB_ROOT};
    index index.html;
    charset utf-8;
    autoindex off;
    gzip on;
    gzip_static on;
    gzip_vary on;
    gzip_types application/wasm application/octet-stream;

    location = / {{
        try_files /index.html =404;
    }}

    location /games/ {{
        autoindex on;
        try_files $uri $uri/ =404;
        add_header Cross-Origin-Opener-Policy "same-origin" always;
        add_header Cross-Origin-Embedder-Policy "require-corp" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header Cache-Control "no-cache" always;
    }}

    location /sites/ {{
        autoindex on;
        try_files $uri $uri/ =404;
        add_header X-Content-Type-Options "nosniff" always;
        add_header Cache-Control "no-cache" always;
    }}

    location = /basaltwater-ca.crt {{
        default_type application/x-x509-ca-cert;
        try_files /basaltwater-ca.crt =404;
    }}

    location ~ /\\. {{
        deny all;
        access_log off;
        log_not_found off;
    }}
}}
"""


def _landing_page(base_url: str, local_ca: bool, users: Sequence[str]) -> str:
    trust_note = (
        '<p>This VM uses its own certificate authority. It is already trusted by '
        'software on the VM. For another computer, install '
        '<a href="/basaltwater-ca.crt">the VM CA certificate</a> once.</p>'
        if local_ca
        else "<p>This endpoint is using an existing publicly trusted certificate.</p>"
    )
    user_items = "".join(
        f'<li><a href="/sites/{html.escape(username)}/">'
        f"{html.escape(username)} sites</a> · "
        f'<a href="/games/{html.escape(username)}/">games</a></li>'
        for username in users
    )
    catalogs = f"<ul>{user_items}</ul>" if user_items else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>basaltwater internal web host</title>
<style>body{{font:16px system-ui,sans-serif;max-width:48rem;margin:3rem auto;padding:0 1rem;line-height:1.5}}code{{background:#eef1f4;padding:.15rem .3rem;border-radius:.2rem}}</style>
</head><body><main><h1>Internal web host</h1>
<p>Static sites are available under <a href="/sites/">/sites/</a>.</p>
<p>Godot exports are available under <a href="/games/">/games/</a>.</p>
{catalogs}
<p>Publish a static project with <code>basaltwater-web publish site</code>.</p>
<p>Publish the current project with <code>basaltwater-web publish godot</code>.</p>
{trust_note}
<p>Base URL: <code>{html.escape(base_url)}</code></p>
</main></body></html>
"""


def _internal_landing_page(base_url: str, local_ca: bool) -> str:
    trust_note = (
        '<p>Install the <a href="/basaltwater-ca.crt">VM CA certificate</a> '
        "on LAN clients before opening internal HTTPS services.</p>"
        if local_ca
        else "<p>This endpoint uses a publicly trusted certificate.</p>"
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>basaltwater internal web host</title>
</head><body><main><h1>basaltwater internal web host</h1>
<p>Static sites are available under <a href="/sites/">/sites/</a>.</p>
<p>Publish the current project with <code>basaltwater-web publish site</code>.</p>
{trust_note}<p>Base URL: <code>{html.escape(base_url)}</code></p>
</main></body></html>
"""


def _existing_internal_web_policy_values() -> tuple[list[str], list[str]]:
    """Read existing managed gateway owners and source policy for merging."""

    if not os.path.exists(GODOT_WEB_POLICY_FILE):
        return [], []
    if os.path.islink(GODOT_WEB_POLICY_FILE):
        raise RuntimeError(
            f"Refusing symlinked internal-web policy: {GODOT_WEB_POLICY_FILE}"
        )
    try:
        with open(GODOT_WEB_POLICY_FILE, encoding="utf-8") as file_obj:
            payload = json.load(file_obj)
    except (OSError, ValueError) as exc:
        raise RuntimeError("Could not read the existing internal-web policy") from exc
    if not isinstance(payload, dict):
        return [], []
    raw_users = payload.get("users", [])
    raw_sources = payload.get("access_sources", [])
    users = []
    if isinstance(raw_users, list):
        users = [
            value
            for value in raw_users
            if isinstance(value, str) and validate_username(value)
        ]
    sources = []
    if isinstance(raw_sources, list):
        sources = [value for value in raw_sources if isinstance(value, str)]
    return users, sources


def _write_if_changed(path: str, content: str, mode: int) -> bool:
    if os.path.islink(path):
        raise RuntimeError(f"Refusing symlinked managed web file: {path}")
    previous = None
    try:
        with open(path, encoding="utf-8") as file_obj:
            previous = file_obj.read()
    except OSError:
        pass
    if previous == content:
        os.chmod(path, mode)
        return False
    write_text_atomic(path, content, mode=mode)
    return True


def _ensure_nginx() -> None:
    if not is_package_installed("nginx") and not install_package(
        "nginx",
        "nginx",
        "apt-get -o DPkg::Lock::Timeout=60 install -y -qq nginx",
    ):
        raise RuntimeError("Failed to install nginx for internal HTTPS hosting")
    if not is_package_installed("openssl") and not install_package(
        "OpenSSL",
        "openssl",
        "apt-get -o DPkg::Lock::Timeout=60 install -y -qq openssl",
    ):
        raise RuntimeError("Failed to install OpenSSL for internal HTTPS hosting")
    if not is_service_active("nginx"):
        run("systemctl enable --now nginx", check=True)


def _configure_user_roots(users: list[str]) -> bool:
    changed = False
    _ensure_managed_directory(GODOT_WEB_GAMES_ROOT, 0o755)
    _ensure_managed_directory(INTERNAL_WEB_SITES_ROOT, 0o755)
    for username in users:
        if not validate_username(username):
            raise ValueError(f"Invalid internal web-host username: {username}")
        account = pwd.getpwnam(username)
        for root in (GODOT_WEB_GAMES_ROOT, INTERNAL_WEB_SITES_ROOT):
            user_root = os.path.join(root, username)
            validate_filesystem_path(user_root, must_exist=False)
            if os.path.lexists(user_root) and (
                os.path.islink(user_root) or not os.path.isdir(user_root)
            ):
                raise RuntimeError(f"Refusing unsafe internal web root: {user_root}")
            if not os.path.isdir(user_root):
                os.mkdir(user_root, mode=0o755)
                changed = True
            os.chown(user_root, account.pw_uid, account.pw_gid)
            os.chmod(user_root, 0o755)
    return changed


def _install_managed_link(source: str, destination: str, label: str) -> bool:
    if not os.path.isfile(source):
        raise RuntimeError(f"{label} is missing: {source}")
    os.chmod(source, 0o755)
    if os.path.lexists(destination):
        if (
            os.path.islink(destination)
            and os.path.realpath(destination) == source
        ):
            return False
        raise RuntimeError(f"Refusing to replace unmanaged {label}: {destination}")
    os.symlink(source, destination)
    return True


def _install_publisher_links() -> bool:
    changed = _install_managed_link(
        GODOT_WEB_PUBLISHER,
        GODOT_WEB_PUBLISHER_LINK,
        "Godot web publisher",
    )
    return _install_managed_link(
        GODOT_WEB_UTILITY,
        GODOT_WEB_UTILITY_LINK,
        "basaltwater-web utility",
    ) or changed


def _install_basaltwater_web_link() -> bool:
    return _install_managed_link(
        GODOT_WEB_UTILITY,
        GODOT_WEB_UTILITY_LINK,
        "basaltwater-web utility",
    )


def _configure_web_policy(
    base_url: str,
    cert_path: str,
    key_path: str,
    local_ca: bool,
    users: list[str],
    access_sources: Sequence[str],
) -> bool:
    sources = [
        validate_network_ip_or_cidr(source, "internal HTTPS access source")
        for source in access_sources
    ]
    content = json.dumps(
        {
            "access_sources": list(dict.fromkeys(sources)),
            "base_url": base_url,
            "ca_certificate": GODOT_WEB_CA_DOWNLOAD if local_ca else None,
            "certificate": cert_path,
            "certificate_key": key_path,
            "forward_port_max": GODOT_WEB_FORWARD_PORT_MAX,
            "forward_port_min": GODOT_WEB_FORWARD_PORT_MIN,
            "users": list(dict.fromkeys(users)),
            "version": 1,
        },
        indent=2,
        sort_keys=True,
    ) + "\n"
    changed = _write_if_changed(GODOT_WEB_POLICY_FILE, content, 0o644)
    result = run(
        f"{shlex.quote(GODOT_WEB_UTILITY_LINK)} forward reconcile",
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "forward reconciliation failed").strip()
        raise RuntimeError(f"Could not reconcile HTTPS forwards: {detail}")
    return changed


def configure_godot_agent_skills(username: str, agent_tools: Sequence[str]) -> bool:
    """Install shared Godot workflow skills for selected Codex/OpenCode users."""

    return install_managed_agent_skills(
        username,
        list(agent_tools),
        GODOT_AGENT_SKILLS,
        source_root=GODOT_AGENT_SKILLS_ROOT,
    )


def _configure_nginx_site(content: str, *, reload_required: bool = False) -> bool:
    previous_content = None
    if os.path.exists(GODOT_WEB_NGINX_SITE):
        if os.path.islink(GODOT_WEB_NGINX_SITE):
            raise RuntimeError(f"Refusing symlinked Nginx site: {GODOT_WEB_NGINX_SITE}")
        with open(GODOT_WEB_NGINX_SITE, encoding="utf-8") as site_file:
            previous_content = site_file.read()
        if _NGINX_MARKER not in previous_content:
            raise RuntimeError(f"Refusing unmanaged Nginx site: {GODOT_WEB_NGINX_SITE}")
    changed = previous_content != content
    if changed:
        write_text_atomic(GODOT_WEB_NGINX_SITE, content, mode=0o644)

    link_created = False
    if os.path.lexists(GODOT_WEB_NGINX_LINK):
        if not (
            os.path.islink(GODOT_WEB_NGINX_LINK)
            and os.path.realpath(GODOT_WEB_NGINX_LINK) == GODOT_WEB_NGINX_SITE
        ):
            raise RuntimeError(f"Refusing unmanaged Nginx link: {GODOT_WEB_NGINX_LINK}")
    else:
        os.symlink(GODOT_WEB_NGINX_SITE, GODOT_WEB_NGINX_LINK)
        changed = True
        link_created = True

    validation = run("nginx -t", check=False, capture_output=True)
    if validation.returncode != 0:
        if previous_content is None:
            try:
                os.unlink(GODOT_WEB_NGINX_SITE)
            except FileNotFoundError:
                pass
        else:
            write_text_atomic(GODOT_WEB_NGINX_SITE, previous_content, mode=0o644)
        if link_created:
            try:
                os.unlink(GODOT_WEB_NGINX_LINK)
            except FileNotFoundError:
                pass
        raise RuntimeError("Nginx rejected the Godot HTTPS host configuration")
    if changed or reload_required:
        run("systemctl reload nginx", check=True)
    return changed


def configure_godot_web_host(
    identities: list[str],
    users: list[str],
    access_sources: Sequence[str] = (),
) -> bool:
    """Reconcile a ready-to-publish HTTPS origin for registered Godot users."""

    normalized_identities = validate_web_identities(
        [*identities, *discover_local_web_identities()]
    )
    existing_users, existing_sources = _existing_internal_web_policy_values()
    normalized_users = list(dict.fromkeys([*existing_users, *users]))
    normalized_sources = list(dict.fromkeys([*existing_sources, *access_sources]))
    # The policy reconciliation below invokes basaltwater-web, so install its
    # managed launcher before reconciling the first policy on a fresh host.
    changed = _install_publisher_links()
    base_url, local_ca, cert_path, key_path, certificate_changed = configure_internal_web_host(
        normalized_identities,
        normalized_users,
        normalized_sources,
    )
    changed = _configure_user_roots(normalized_users) or changed
    changed = certificate_changed or changed
    changed = _write_if_changed(
        os.path.join(GODOT_WEB_ROOT, "index.html"),
        _landing_page(base_url, local_ca, normalized_users),
        0o644,
    ) or changed
    if local_ca:
        with open(GODOT_WEB_CA_CERT, encoding="utf-8") as cert_file:
            changed = _write_if_changed(
                GODOT_WEB_CA_DOWNLOAD,
                cert_file.read(),
                0o644,
            ) or changed
    else:
        if os.path.exists(GODOT_WEB_CA_DOWNLOAD):
            os.unlink(GODOT_WEB_CA_DOWNLOAD)
            changed = True
    changed = _write_if_changed(GODOT_WEB_URL_FILE, base_url + "\n", 0o644) or changed
    changed = _configure_nginx_site(
        render_nginx_config(cert_path, key_path),
        reload_required=certificate_changed or not local_ca,
    ) or changed
    changed = _configure_web_policy(
        base_url,
        cert_path,
        key_path,
        local_ca,
        normalized_users,
        normalized_sources,
    ) or changed
    fingerprint = hashlib.sha256()
    if local_ca:
        with open(GODOT_WEB_CA_CERT, "rb") as cert_file:
            fingerprint.update(cert_file.read())
    print(f"  ✓ Godot web exports: {base_url}/games/")
    if local_ca:
        print(
            "  ✓ VM-local CA installed on the VM; remote clients can enroll "
            f"{base_url}/basaltwater-ca.crt (SHA-256 {fingerprint.hexdigest()})"
        )
    return changed


def configure_internal_web_host(
    identities: list[str],
    users: list[str],
    access_sources: Sequence[str] = (),
    *,
    configure_static_site: bool = False,
    install_utility: bool = False,
) -> tuple[str, bool, str, str, bool]:
    """Ensure the shared internal HTTPS origin and forwarding policy exist."""

    normalized_identities = validate_web_identities(
        [*identities, *discover_local_web_identities()]
    )
    existing_users, existing_sources = _existing_internal_web_policy_values()
    normalized_users = list(dict.fromkeys([*existing_users, *users]))
    normalized_sources = list(dict.fromkeys([*existing_sources, *access_sources]))
    _ensure_nginx()
    _ensure_managed_directory(GODOT_WEB_ROOT, 0o755)
    cert_path, key_path, local_ca, certificate_changed = _certificate_for_identities(
        normalized_identities
    )
    if local_ca:
        _install_chromium_ca_trust(normalized_users)
    base_url = _base_url(normalized_identities)
    _write_if_changed(GODOT_WEB_URL_FILE, base_url + "\n", 0o644)
    if install_utility:
        _install_basaltwater_web_link()
    if configure_static_site:
        _configure_user_roots(normalized_users)
        index_path = os.path.join(GODOT_WEB_ROOT, "index.html")
        if os.path.lexists(index_path):
            if os.path.islink(index_path) or not os.path.isfile(index_path):
                raise RuntimeError(
                    f"Refusing unsafe internal web landing page: {index_path}"
                )
        else:
            _write_if_changed(
                index_path,
                _internal_landing_page(base_url, local_ca),
                0o644,
            )
        _configure_nginx_site(
            render_nginx_config(cert_path, key_path),
            reload_required=certificate_changed or not local_ca,
        )
    _configure_web_policy(
        base_url,
        cert_path,
        key_path,
        local_ca,
        normalized_users,
        normalized_sources,
    )
    return base_url, local_ca, cert_path, key_path, certificate_changed


__all__ = [
    "GODOT_WEB_CA_DOWNLOAD",
    "GODOT_WEB_GAMES_ROOT",
    "INTERNAL_WEB_SITES_ROOT",
    "configure_godot_agent_skills",
    "configure_internal_web_host",
    "configure_godot_web_host",
    "discover_local_web_identities",
    "identities_for_config",
    "render_nginx_config",
    "validate_web_identities",
]
