"""Narrow native Headscale runtime operations (no enrollment authority)."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import sqlite3
import stat
import tempfile
import time
from collections.abc import Iterator

from lib.atomic_io import parse_json, read_json_file, write_bytes_atomic
from lib.config import SetupConfig
from lib.headscale_config import validate_headscale_settings, validate_headscale_url
from lib.machine_state import can_manage_firewall, can_manage_system_services
from lib.operation_state import OperationStateStore
from lib.remote_utils import get_os_id, run
from lib.setup_directories import check_directory_path, directory_fd
from lib.validation import validate_filesystem_path
from lib.validators import validate_host

VERSION = "0.29.7"
SHA256 = {
    "amd64": "d230c5fc8db06cf1868f4ec0077b89e5e2ccf3b22a3259efa6d080db2d5f921c",
    "arm64": "a68f258a2a13a87321671f8cc613500986ffc40d0d2eacfdbf0b890b8b94227e",
}


@dataclass(frozen=True)
class ServerPaths:
    """Explicit native paths; alternate roots are for isolated fixtures."""

    etc: Path = Path("/etc/headscale")
    data: Path = Path("/var/lib/headscale")
    private: Path = Path("/var/lib/basaltwater/headscale")
    nginx: Path = Path("/etc/nginx/conf.d/basaltwater-headscale.conf")
    socket: Path = Path("/run/headscale/headscale.sock")

    def __post_init__(self) -> None:
        for path in (self.etc, self.data, self.private, self.nginx, self.socket):
            validate_filesystem_path(str(path))
            if not path.is_absolute() or ".." in path.parts:
                raise ValueError("Headscale paths must be absolute without traversal")

    @property
    def config(self) -> Path:
        return self.etc / "config.yaml"

    @property
    def manifest(self) -> Path:
        return self.private / "manifest.json"


@dataclass(frozen=True)
class ServerManifest:
    schema: int
    version: str
    endpoint: str
    base_domain: str

    def __post_init__(self) -> None:
        version_tuple(self.version)
        if (type(self.schema) is not int or self.schema != 1
                or validate_headscale_url(self.endpoint) != self.endpoint
                or not isinstance(self.base_domain, str) or not validate_host(self.base_domain)):
            raise ValueError("Invalid Headscale manifest binding")


@dataclass(frozen=True)
class Release:
    """An operator-reviewed official stable DEB, with out-of-band digest."""

    version: str
    architecture: str
    sha256: str

    def __post_init__(self) -> None:
        version_tuple(self.version)
        if self.architecture not in SHA256 or not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise ValueError("Unsupported architecture or invalid release digest")

    @property
    def url(self) -> str:
        return (f"https://github.com/juanfont/headscale/releases/download/v{self.version}/"
                f"headscale_{self.version}_linux_{self.architecture}.deb")


def version_tuple(version: str) -> tuple[int, int, int]:
    if not isinstance(version, str) or not re.fullmatch(r"0\.[0-9]+\.[0-9]+", version):
        raise ValueError("Expected a stable Headscale 0.x.y version")
    major, minor, patch = map(int, version.split("."))
    return major, minor, patch


def require_capabilities(*, firewall: bool = False) -> None:
    """Package/root/service capabilities are independent of kernel tuning."""
    if os.geteuid() != 0 or get_os_id() != "debian" or not shutil.which("apt-get"):
        raise RuntimeError("Native Headscale requires Debian, root and APT")
    if not can_manage_system_services():
        raise RuntimeError("Native Headscale requires systemd service capability")
    if firewall and (not can_manage_firewall() or not shutil.which("ufw")):
        raise RuntimeError("Headscale ingress requires managed UFW capability")


def private_directory(path: Path, *, uid: int = 0, gid: int = 0, mode: int = 0o700) -> None:
    with directory_fd(str(path), create=True, mode=mode) as fd:
        info = os.fstat(fd)
        if info.st_uid not in {0, os.geteuid(), uid} or info.st_mode & 0o022:
            raise ValueError("Unsafe Headscale directory ownership or permissions")
        os.fchown(fd, uid, gid)
        os.fchmod(fd, mode)


def regular_bytes(path: Path) -> bytes:
    """Read only private regular files, without following links or hard links."""
    check_directory_path(str(path.parent))
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        owners = {0, os.geteuid()}
        try:
            owners.add(pwd.getpwnam("headscale").pw_uid)
        except KeyError:
            pass
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o022
                or info.st_uid not in owners or info.st_size > 64 * 1024 * 1024):
            raise ValueError("Unsafe Headscale file")
        content = stream.read(64 * 1024 * 1024 + 1)
        if len(content) > 64 * 1024 * 1024:
            raise ValueError("Headscale file exceeds the bounded snapshot/package limit")
        return content


def load_manifest(paths: ServerPaths) -> ServerManifest | None:
    if not paths.manifest.exists() and not paths.manifest.is_symlink():
        return None
    regular_bytes(paths.manifest)
    if paths.manifest.stat().st_mode & 0o077 or paths.manifest.stat().st_uid != os.geteuid():
        raise ValueError("Headscale manifest must be private")
    raw = read_json_file(str(paths.manifest))
    if not isinstance(raw, dict) or set(raw) != {"schema", "version", "endpoint", "base_domain"}:
        raise ValueError("Invalid Headscale manifest; inspect private state")
    manifest = ServerManifest(**raw)
    version_tuple(manifest.version)
    if manifest.schema != 1:
        raise ValueError("Unsupported Headscale manifest schema")
    return manifest


def manifest_for(config: SetupConfig, version: str = VERSION) -> ServerManifest:
    validate_headscale_settings(config, complete=True)
    version_tuple(version)
    return ServerManifest(1, version, f"https://{config.headscale_domain}", str(config.headscale_base_domain))


@contextmanager
def runtime_operation(paths: ServerPaths, kind: str) -> Iterator[None]:
    """Retain a blocking marker on failure/interruption; never blindly retry."""
    private_directory(paths.private)
    store = OperationStateStore(str(paths.private / "operation.json"))
    try:
        record = store.begin(kind, "headscale", "mutating")
        yield
        store.complete(record.operation_id)
    finally:
        store.close()


def render_config(config: SetupConfig, paths: ServerPaths = ServerPaths()) -> str:
    validate_headscale_settings(config, complete=True)
    # Never advertise an unverified candidate resolver or public relay fallback.
    relay = dict(enabled=True, verify_clients=True, region_id=999,
                 region_code="owned", region_name="Owned Headscale relay",
                 stun_listen_addr="[::]:3478" if config.headscale_public_ipv6 else "0.0.0.0:3478",
                 private_key_path=str(paths.data / "derp_server_private.key"),
                 automatically_add_embedded_derp_region=True)
    for family in (4, 6):
        address = getattr(config, f"headscale_public_ipv{family}")
        if address:
            relay[f"ipv{family}"] = address
    content = dict(
        server_url=f"https://{config.headscale_domain}", listen_addr="127.0.0.1:8080",
        metrics_listen_addr="127.0.0.1:9090", grpc_listen_addr="127.0.0.1:50443",
        grpc_allow_insecure=False, trusted_proxies=["127.0.0.1/32"],
        unix_socket=str(paths.socket), unix_socket_permission="0600",
        noise={"private_key_path": str(paths.data / "noise_private.key")},
        prefixes={"v4": "100.64.0.0/10", "v6": "fd7a:115c:a1e0::/48", "allocation": "sequential"},
        derp=dict(server=relay, urls=[], paths=[], auto_update_enabled=False),
        database={"type": "sqlite", "sqlite": {"path": str(paths.data / "db.sqlite"), "write_ahead_log": True}},
        policy={"mode": "file", "path": str(paths.etc / "policy.hujson")},
        dns={"magic_dns": True, "base_domain": config.headscale_base_domain,
             "override_local_dns": False, "nameservers": {"global": [], "split": {}},
             "search_domains": [], "extra_records": []},
        disable_check_updates=True, logtail={"enabled": False}, auto_update={"enabled": False},
        log={"level": "warn", "format": "text"},
    )
    return json.dumps(content, indent=2) + "\n"


def render_nginx(domain: str, *, tls: bool = True) -> str:
    if not validate_host(domain) or not re.fullmatch(r"[a-z0-9.-]+", domain):
        raise ValueError("Invalid control hostname")
    http = f"""# Basaltwater Headscale
server {{
    listen 80;
    listen [::]:80;
    server_name {domain};
    access_log off;
    error_log /dev/null;
    location /.well-known/acme-challenge/ {{ root /var/www/letsencrypt; }}
    location / {{ return 301 https://{domain}$request_uri; }}
}}
"""
    if not tls:
        return http
    return http + f"""map $http_upgrade $basaltwater_headscale_connection {{ default upgrade; '' close; }}
server {{
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name {domain};
    ssl_certificate /etc/letsencrypt/live/{domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{domain}/privkey.pem;
    access_log off;
    error_log /dev/null;
    location / {{
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $basaltwater_headscale_connection;
        proxy_set_header Host {domain};
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-Host {domain};
        proxy_set_header X-Forwarded-Port 443;
        proxy_set_header X-Forwarded-Server {domain};
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header True-Client-IP "";
        proxy_set_header Forwarded "";
        proxy_buffering off;
        proxy_request_buffering off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }}
}}
"""


def configtest(path: Path, *, expected_database_schema: Path | None = None) -> None:
    """Validate with scratch state: upstream configtest initializes its database.

    Never let validation create keys, migrate the live database, or change its
    ownership. Existing keys are copied privately so their format and distinct
    identities are still validated. The daemon owns production initialization.
    """
    content = parse_json(regular_bytes(path))
    if not isinstance(content, dict):
        raise ValueError("Managed Headscale validation requires an object")
    database, noise, derp = (content.get(name) for name in ("database", "noise", "derp"))
    if (not isinstance(database, dict) or database.get("type") != "sqlite"
            or not isinstance(database.get("sqlite"), dict) or not isinstance(noise, dict)
            or not isinstance(derp, dict) or not isinstance(derp.get("server"), dict)):
        raise ValueError("Managed Headscale validation requires its SQLite configuration")
    with tempfile.TemporaryDirectory(prefix="headscale-configtest-") as temporary:
        scratch = Path(temporary)
        for settings, key, filename in (
            (content["database"]["sqlite"], "path", "db.sqlite"),
            (content["noise"], "private_key_path", "noise.key"),
            (content["derp"]["server"], "private_key_path", "derp.key"),
        ):
            original = settings.get(key)
            validate_filesystem_path(original)
            source = Path(original)
            if not source.is_absolute() or ".." in source.parts:
                raise ValueError("Managed Headscale state paths must be absolute")
            destination = scratch / filename
            if filename != "db.sqlite" and (source.exists() or source.is_symlink()):
                write_bytes_atomic(str(destination), regular_bytes(source), mode=0o600)
            settings[key] = str(destination)
        content["unix_socket"] = str(scratch / "headscale.sock")
        candidate = scratch / "config.yaml"
        write_bytes_atomic(str(candidate), (json.dumps(content) + "\n").encode(), mode=0o600)
        # Environment overrides must not redirect validation back to live state.
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith("HEADSCALE_")}
        result = run(["headscale", "--config", str(candidate), "configtest"], env=environment,
                     check=False, capture_output=True, timeout=30)
        if result.returncode:
            raise RuntimeError("Headscale configtest failed; candidate not activated")
        if expected_database_schema is not None:
            def schema(database: Path, *, immutable: bool) -> list:
                connection = sqlite3.connect(database.as_uri() + "?mode=ro" +
                                             ("&immutable=1" if immutable else ""), uri=True)
                try:
                    return connection.execute(
                        "SELECT type, name, tbl_name, sql FROM sqlite_master "
                        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
                    ).fetchall()
                finally:
                    connection.close()
            regular_bytes(expected_database_schema)
            # configtest exits without checkpointing its fresh WAL. Only this
            # private scratch database may consult/reconstruct WAL state; the
            # reviewed stopped production database remains immutable.
            native_schema = schema(scratch / "db.sqlite", immutable=False)
            if not native_schema or schema(expected_database_schema, immutable=True) != native_schema:
                raise ValueError("Headscale database schema differs from pinned native schema; migrate manually")


def health(paths: ServerPaths) -> None:
    run(["systemctl", "is-active", "--quiet", "headscale"], capture_output=True, timeout=30)
    for attempt in range(5):
        result = run(["headscale", "--config", str(paths.config), "health"],
                     check=False, capture_output=True, timeout=5)
        if result.returncode == 0:
            return
        if attempt < 4:
            time.sleep(0.5)
    raise RuntimeError("Native Headscale health check failed")


def installed_version() -> str:
    output = run(["headscale", "version"], capture_output=True, timeout=30).stdout
    match = re.search(r"\bheadscale version v?(0\.[0-9]+\.[0-9]+)(?:\s|$)", output)
    if not match:
        raise ValueError("Cannot determine stable installed Headscale version")
    return match[1]


def stopped() -> None:
    result = run(["systemctl", "show", "headscale", "--property=ActiveState", "--value"],
                 capture_output=True, timeout=30)
    if result.stdout.strip() not in {"inactive", "failed"}:
        raise RuntimeError("Headscale must be stopped before this operation")


def verify_package(package: Path, release: Release) -> None:
    if hashlib.sha256(regular_bytes(package)).hexdigest() != release.sha256:
        raise ValueError("Headscale package checksum mismatch")
    result = run(["dpkg-deb", "-f", str(package), "Package", "Version", "Architecture"], capture_output=True)
    fields = dict(line.split(": ", 1) for line in result.stdout.splitlines() if ": " in line)
    if fields != {"Package": "headscale", "Version": release.version, "Architecture": release.architecture}:
        raise ValueError("Headscale package metadata mismatch")


def install_deb(package: Path, release: Release) -> None:
    """Install held package, leaving a persistent safety mask until activation."""
    verify_package(package, release)
    stopped()
    # Package maintainer scripts cannot expose the upstream default listeners.
    run(["systemctl", "mask", "headscale.service"])
    run(["apt-get", "install", "-y", "--allow-downgrades", "--allow-change-held-packages",
         "-o", "Dpkg::Options::=--force-confold", "-o", "Dpkg::Options::=--force-confdef", str(package)])
    run(["apt-mark", "hold", "headscale"])
    if installed_version() != release.version:
        raise ValueError("Installed Headscale version does not match verified package")


def preflight(config: SetupConfig, paths: ServerPaths) -> ServerManifest | None:
    """Read actual listeners/files before any package, config or service changes."""
    candidate = manifest_for(config)
    render_config(config, paths)
    for path in (paths.etc, paths.data, paths.private, paths.nginx.parent):
        check_directory_path(str(path))
    previous = load_manifest(paths)
    if previous and previous != candidate:
        raise ValueError("Headscale endpoint/base/version change requires explicit migration or controlled upgrade")
    if previous:
        for path in (paths.config, paths.etc / "policy.hujson", paths.data / "noise_private.key",
                     paths.data / "derp_server_private.key", paths.data / "db.sqlite", paths.nginx):
            regular_bytes(path)
        native = read_json_file(str(paths.config))
        if (not isinstance(native, dict) or native.get("server_url") != previous.endpoint
                or native.get("dns", {}).get("base_domain") != previous.base_domain):
            raise ValueError("Managed native endpoint binding changed; explicit migration required")
        if installed_version() != previous.version:
            raise ValueError("Installed Headscale version differs from managed manifest")
    else:
        if any(p.exists() or p.is_symlink() for p in (paths.etc, paths.data, paths.nginx)) or shutil.which("headscale"):
            raise ValueError("Unmanaged Headscale installation: stop and back up; explicit adoption/migration required")
        state = run(["systemctl", "show", "headscale", "--property=LoadState", "--value"],
                    capture_output=True).stdout.strip()
        if state != "not-found":
            raise ValueError("Unmanaged Headscale unit requires explicit adoption/migration")
    listeners = run(["ss", "-H", "-lntup"], capture_output=True, timeout=30).stdout
    for line in listeners.splitlines():
        ports = {int(p) for p in re.findall(r":(\d+)\b", line.split("users:")[0])}
        if ports & {8080, 9090, 50443, 3478} and not (previous and '"headscale"' in line):
            raise ValueError("Headscale listener conflicts with an existing process")
        if ports & {80, 443} and '"nginx"' not in line:
            raise ValueError("Headscale HTTPS ingress conflicts with an existing process")
    # Effective Nginx includes catch included snippets and tunnel routes too.
    effective = run(["nginx", "-T"], capture_output=True, timeout=30).stdout
    for section in effective.split("# configuration file "):
        if str(paths.nginx) + ":" in section.split("\n", 1)[0]:
            continue
        if re.search(r"\bserver_name\s+[^;]*\b" + re.escape(str(config.headscale_domain)) + r"\s*[; ]", section):
            raise ValueError("Headscale hostname is already bound in Nginx; explicit migration required")
    cloudflare = Path("/etc/cloudflared")
    if cloudflare.exists():
        for path in cloudflare.glob("*.y*ml"):
            # Only inspect routing text; never print credentials or inventories.
            text = regular_bytes(path).decode()
            if re.search(r"hostname:\s*['\"]?" + re.escape(str(config.headscale_domain)) + r"(?:['\"]?\s|$)", text):
                raise ValueError("Headscale control hostname is routed through Cloudflare; remove that route explicitly")
    return previous


def publish_config(content: str, paths: ServerPaths, *, gid: int) -> None:
    """Validate before replace; native activation failure restores prior config."""
    with tempfile.TemporaryDirectory(prefix=".candidate-", dir=paths.etc) as staging:
        candidate = Path(staging) / "config.yaml"
        write_bytes_atomic(str(candidate), content.encode())
        configtest(candidate)
    old = regular_bytes(paths.config) if paths.config.exists() else None
    try:
        write_bytes_atomic(str(paths.config), content.encode(), mode=0o640, uid=0, gid=gid)
        run(["systemctl", "unmask", "headscale.service"])
        run(["systemctl", "enable", "headscale"])
        run(["systemctl", "restart", "headscale"])
        health(paths)
    except BaseException:
        run(["systemctl", "stop", "headscale"])
        stopped()
        if old is not None:
            write_bytes_atomic(str(paths.config), old, mode=0o640, uid=0, gid=gid)
            run(["systemctl", "restart", "headscale"])
            health(paths)
        else:
            run(["systemctl", "disable", "headscale"])
        raise
