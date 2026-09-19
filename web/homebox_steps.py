"""Target-side native HomeBox installation, observation, and recovery.

The persistent service never enables registration. Bootstrap uses a bounded
transient unit; upgrades retain a stopped-service recovery archive before
allowing the new executable to run database migrations.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import grp
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import re
import secrets
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from typing import Any, Callable, Iterator
import urllib.error
import urllib.request

from lib.atomic_io import remove_file_durable, write_json_atomic, write_text_atomic
from lib.homebox_config import (
    DEFAULT_VERSION, homebox_settings, parse_homebox_spec, paths_overlap,
    validate_homebox_path, validate_homebox_settings, validate_homebox_version,
)
from lib.local_http import open_loopback
from lib.machine_state import can_manage_firewall, can_manage_system_services
from lib.release_management import detect_release_arch, validate_release_sha256_digest
from lib.remote_utils import install_package, is_dry_run, run
from lib.validation import validate_filesystem_path, validate_network_ip_or_cidr, validate_ssl_email

ROOT = Path("/opt/homebox")
CONFIG = Path("/etc/homebox")
STATE = Path("/opt/basaltwater/state/homebox.json")
UNIT = Path("/etc/systemd/system/homebox.service")
SITE = Path("/etc/nginx/sites-available/basaltwater-homebox")
LINK = Path("/etc/nginx/sites-enabled/basaltwater-homebox")
BACKUPS = Path("/var/lib/homebox-backups")
LOCK = Path("/run/lock/basaltwater-homebox.lock")
UPDATE_STATE = Path("/opt/basaltwater/state/homebox_update.json")
MARKER = "# Managed by basaltwater HomeBox"
STATUS_PATH = "/api/v1/status"
SERVICE = "homebox.service"
REPO = "sysadminsmedia/homebox"
AUTO_UPDATE_SERVICE = "auto-update-homebox.service"
AUTO_UPDATE_TIMER = "auto-update-homebox.timer"
MAX_UPDATE_AGE_SECONDS = 9 * 24 * 60 * 60
RECOVERY_ARCHIVE_RETENTION = 4
_RECOVERY_ARCHIVE_RE = re.compile(r"^[0-9]+-before-setup\.tar\.gz$")


def _safe_path(path: Path) -> None:
    validate_filesystem_path(str(path))
    for part in (path, *path.parents):
        if part.is_symlink():
            raise RuntimeError(f"Refusing symlinked HomeBox path: {part}")


def _private_dir(path: Path) -> None:
    _safe_path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.stat().st_uid != 0:
        raise RuntimeError(f"HomeBox management directory must be root-owned: {path}")
    path.chmod(0o700)


def _private_file(path: Path) -> None:
    _safe_path(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError(f"HomeBox private file must be root-owned with mode 0600: {path}")


@contextlib.contextmanager
def homebox_lock() -> Iterator[None]:
    _safe_path(LOCK)
    descriptor = os.open(LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    except BlockingIOError as exc:
        raise RuntimeError("Another HomeBox operation is running") from exc
    finally:
        os.close(descriptor)


def _command(*args: str) -> None:
    result = run(list(args), check=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(f"HomeBox command failed: {args[0]}")


def _active() -> bool:
    return run(["systemctl", "is-active", "--quiet", SERVICE], check=False, capture_output=True).returncode == 0


def _stop_service() -> None:
    if UNIT.exists() or _active():
        _command("systemctl", "stop", SERVICE)
    bootstrap = "basaltwater-homebox-bootstrap.service"
    if run(["systemctl", "is-active", "--quiet", bootstrap], check=False, capture_output=True).returncode == 0:
        _command("systemctl", "stop", bootstrap)


def _acme_rule_numbers() -> list[int]:
    """Return this service's UFW HTTP rules in deletion-safe order."""
    if not shutil.which("ufw"):
        return []
    result = run(["ufw", "status", "numbered"], check=True, capture_output=True)
    numbers = []
    for line in result.stdout.splitlines():
        match = re.match(r"^\[\s*(\d+)\].*# homebox acme\s*$", line.strip())
        if match:
            numbers.append(int(match.group(1)))
    return sorted(numbers, reverse=True)


def _ensure_acme_rule() -> bool:
    """Add the managed HTTP challenge rule only when it is not already present."""
    if not shutil.which("ufw") or _acme_rule_numbers():
        return False
    _command("ufw", "allow", "80/tcp", "comment", "homebox acme")
    return True


def _remove_acme_rule() -> None:
    """Remove only this service's commented HTTP rule, preserving shared rules."""
    for number in _acme_rule_numbers():
        _command("ufw", "--force", "delete", str(number))


def validate_state(value: Any) -> dict:
    if not isinstance(value, dict) or value.get("schema") != 1:
        raise ValueError("Invalid HomeBox state schema")
    validate_homebox_version(value.get("version"))
    for key in ("archive_sha256", "binary_sha256"):
        validate_release_sha256_digest("sha256:" + str(value.get(key, "")))
    validate_homebox_path(value.get("data_path", ""))
    domain, port = parse_homebox_spec(
        f"{value.get('domain', '')}:{value.get('public_port', '')}"
    )
    if domain != value.get("domain") or port != value.get("public_port"):
        raise ValueError("Invalid HomeBox endpoint state")
    if type(value.get("port")) is not int or not 1024 <= value["port"] <= 65535:
        raise ValueError("Invalid HomeBox backend port")
    if value.get("status") not in {"prepared", "ready", "disabled"}:
        raise ValueError("Invalid HomeBox lifecycle state")
    if type(value.get("bootstrap_pending", False)) is not bool:
        raise ValueError("Invalid HomeBox bootstrap state")
    if value.get("bootstrap_pending") and value["status"] != "disabled":
        raise ValueError("Pending disabled bootstrap has an invalid lifecycle state")
    validate_ssl_email(value.get("email"))
    if not isinstance(value.get("email"), str) or not value["email"]:
        raise ValueError("Missing HomeBox initial email")
    if not isinstance(value.get("sources"), list):
        raise ValueError("Invalid HomeBox access sources")
    for source in value["sources"]:
        validate_network_ip_or_cidr(source, "HomeBox source")
    mount = value.get("mount", {})
    if not isinstance(mount, dict) or not all(isinstance(mount.get(k), str) and mount[k] for k in ("target", "source", "fstype")):
        raise ValueError("Missing HomeBox storage identity")
    validate_filesystem_path(mount["target"])
    if not re.fullmatch(r"/[A-Za-z0-9_/.-]*", mount["target"]):
        raise ValueError("Invalid HomeBox mount path")
    return value


def read_state() -> dict | None:
    _safe_path(STATE)
    try:
        return validate_state(json.loads(STATE.read_text()))
    except FileNotFoundError:
        return None
    except (ValueError, TypeError) as exc:
        raise RuntimeError("HomeBox state is corrupt; restore a verified backup") from exc


def _save_state(value: dict) -> None:
    validate_state(value)
    write_json_atomic(str(STATE), value)


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def release_path(value: dict) -> Path:
    return ROOT / "releases" / f"{value['version']}-{value['archive_sha256']}" / "homebox"


def _request_json(url: str, payload: dict | None = None, *, local: bool = False) -> Any:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, headers={
        "User-Agent": "basaltwater-homebox", "Content-Type": "application/json",
    })
    if local:
        response_context = open_loopback(request, timeout=15)
    else:
        # Ignore proxy environment variables for upstream release metadata.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        response_context = opener.open(request, timeout=15)
    with response_context as response:
        body = response.read(2 * 1024 * 1024)
        return json.loads(body) if body else None


def require_amd64() -> None:
    """Reject architectures outside the intentionally narrow HomeBox support scope."""
    if detect_release_arch() != "amd64":
        raise RuntimeError("HomeBox is supported only on amd64 machines")


def stage_release(version: str) -> dict:
    """Verify the exact amd64 publisher asset and extract its regular binary."""
    validate_homebox_version(version)
    require_amd64()
    asset_name = "homebox_Linux_x86_64.tar.gz"
    release = _request_json(f"https://api.github.com/repos/{REPO}/releases/tags/{version}")
    if (
        not isinstance(release, dict)
        or release.get("tag_name") != version
        or release.get("draft")
        or release.get("prerelease")
    ):
        raise RuntimeError("HomeBox requires an exact stable upstream release")
    assets = [
        asset
        for asset in release.get("assets", [])
        if isinstance(asset, dict) and asset.get("name") == asset_name
    ]
    if len(assets) != 1:
        raise RuntimeError("HomeBox release is missing the required amd64 asset")
    asset = assets[0]
    digest = validate_release_sha256_digest(asset.get("digest"))
    url = f"https://github.com/{REPO}/releases/download/{version}/{asset_name}"
    if asset.get("browser_download_url") != url:
        raise RuntimeError("Unexpected HomeBox release download URL")
    result = {"version": version, "archive_sha256": digest}
    binary = release_path(result)
    _safe_path(binary)
    binary.parent.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".homebox-", dir=binary.parent.parent) as directory:
        archive = Path(directory) / "release.tar.gz"
        _command("curl", "--fail", "--location", "--max-time", "180", "--proto", "=https",
                 "--proto-redir", "=https", "--output", str(archive), url)
        if _digest(archive) != digest:
            raise RuntimeError("HomeBox release checksum mismatch")
        with tarfile.open(archive) as bundle:
            members = bundle.getmembers()
            _validate_members(members)
            matches = [m for m in members if m.name == "homebox" and m.isfile()]
            if len(matches) != 1 or matches[0].size > 512 * 1024 * 1024:
                raise RuntimeError("HomeBox archive has no valid native binary")
            staged = Path(directory) / "homebox"
            with bundle.extractfile(matches[0]) as source, staged.open("wb") as target:
                shutil.copyfileobj(source, target)
            staged.chmod(0o755)
            result["binary_sha256"] = _digest(staged)
            if binary.exists():
                if _digest(binary) != result["binary_sha256"]:
                    raise RuntimeError("Existing immutable HomeBox release was modified")
            else:
                binary.parent.mkdir(mode=0o755)
                shutil.move(str(staged), binary)
    return result


def latest_homebox_version() -> str:
    """Return the newest stable upstream tag accepted by this integration."""
    release = _request_json(f"https://api.github.com/repos/{REPO}/releases/latest")
    if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
        raise RuntimeError("HomeBox latest-release response is not a stable release")
    try:
        return validate_homebox_version(release.get("tag_name"))
    except ValueError as exc:
        raise RuntimeError("HomeBox latest-release response has an unsupported tag") from exc


def _validate_members(members: list[tarfile.TarInfo]) -> None:
    seen: set[str] = set()
    for member in members:
        path = PurePosixPath(member.name)
        if (path.is_absolute() or ".." in path.parts or "\\" in member.name
                or str(path) in seen or not (member.isdir() or member.isfile())):
            raise RuntimeError("Unsafe or duplicate path in HomeBox archive")
        seen.add(str(path))


def _storage(path: Path) -> dict:
    _safe_path(path)
    existing = path
    while not existing.exists():
        existing = existing.parent
    result = run(["findmnt", "--json", "--target", str(existing), "--output", "TARGET,SOURCE,FSTYPE"],
                 check=True, capture_output=True)
    try:
        mount = json.loads(result.stdout)["filesystems"][0]
        if mount["fstype"] not in {"ext4", "xfs", "btrfs", "zfs"}:
            raise ValueError("not a supported local filesystem")
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("HomeBox requires local ext4, xfs, btrfs, or zfs storage") from exc
    return {k: mount[k] for k in ("target", "source", "fstype")}


def _check_storage(value: dict) -> None:
    path = Path(value["data_path"])
    if _storage(path) != value["mount"]:
        raise RuntimeError("HomeBox backing mount changed or is missing; restore the expected mount")
    while not path.exists():
        path = path.parent
    if shutil.disk_usage(path).free < 256 * 1024 * 1024:
        raise RuntimeError("HomeBox requires at least 256 MiB free on its data filesystem")


def _ensure_account() -> pwd.struct_passwd:
    try:
        account = pwd.getpwnam("homebox")
    except KeyError:
        _command("useradd", "--system", "--user-group", "--home-dir", "/nonexistent",
                 "--no-create-home", "--shell", "/usr/sbin/nologin", "homebox")
        account = pwd.getpwnam("homebox")
    if account.pw_uid == 0 or account.pw_gid == 0 or grp.getgrgid(account.pw_gid).gr_name != "homebox" or account.pw_dir != "/nonexistent" or account.pw_shell not in {"/usr/sbin/nologin", "/sbin/nologin"}:
        raise RuntimeError("Refusing an incompatible existing homebox account")
    if os.getgrouplist("homebox", account.pw_gid) != [account.pw_gid]:
        raise RuntimeError("HomeBox service account must have no supplementary groups")
    if (any(user.pw_name != "homebox" and user.pw_gid == account.pw_gid for user in pwd.getpwall())
            or any(name != "homebox" for name in grp.getgrgid(account.pw_gid).gr_mem)):
        raise RuntimeError("HomeBox service group must not be shared with other accounts")
    return account


def _secrets(create: bool = False) -> dict:
    path = CONFIG / "secrets.json"
    _safe_path(path)
    if not path.exists() and create:
        write_json_atomic(str(path), {"pepper": secrets.token_urlsafe(48), "password": secrets.token_urlsafe(24)})
    try:
        value = json.loads(path.read_text())
        if not all(isinstance(value.get(k), str) and re.fullmatch(r"[A-Za-z0-9_-]{32,128}", value[k]) for k in ("pepper", "password")):
            raise ValueError("invalid secret")
        _private_file(path)
        return value
    except (OSError, ValueError, AttributeError) as exc:
        raise RuntimeError("HomeBox secrets are missing or invalid; restore them without rotating the pepper") from exc


def render_environment(value: dict, secret: dict) -> str:
    data = value["data_path"]
    settings = {
        "HBOX_MODE": "production", "HBOX_WEB_HOST": "127.0.0.1", "HBOX_WEB_PORT": str(value["port"]),
        "HBOX_DATABASE_DRIVER": "sqlite3",
        "HBOX_DATABASE_SQLITE_PATH": f"{data}/homebox.db?_pragma=busy_timeout=999&_pragma=journal_mode=WAL&_fk=1&_time_format=sqlite",
        "HBOX_STORAGE_CONN_STRING": f"file://{data}/", "HBOX_STORAGE_PREFIX_PATH": "attachments",
        "HBOX_OPTIONS_ALLOW_REGISTRATION": "false", "HBOX_OPTIONS_ALLOW_LOCAL_LOGIN": "true",
        "HBOX_OPTIONS_ALLOW_ANALYTICS": "false", "HBOX_DEBUG_ENABLED": "false", "HBOX_DEMO": "false",
        "HBOX_OTEL_ENABLED": "false", "HBOX_OPTIONS_TRUST_PROXY": "true" if value["domain"] else "false",
        "HBOX_OPTIONS_HOSTNAME": public_url(value).rstrip("/"), "HBOX_LOG_LEVEL": "warn",
        "HBOX_AUTH_API_KEY_PEPPER": secret["pepper"],
    }
    return MARKER + "\n" + "\n".join(f'{k}="{v}"' for k, v in settings.items()) + "\n"


def render_unit(value: dict) -> str:
    mount = value["mount"]["target"]
    mount_condition = f"ConditionPathIsMountPoint={mount}\n" if mount != "/" else ""
    return f"""{MARKER}
[Unit]
Description=HomeBox inventory
After=network.target
RequiresMountsFor={value['data_path']}
{mount_condition}
[Service]
Type=simple
User=homebox
Group=homebox
WorkingDirectory={value['data_path']}
EnvironmentFile={CONFIG}/homebox.env
ExecStart={ROOT}/current/homebox
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths={value['data_path']}
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6

[Install]
WantedBy=multi-user.target
"""


def public_url(value: dict) -> str:
    if not value["domain"]:
        return f"http://127.0.0.1:{value['port']}/"
    suffix = "" if value["public_port"] == 443 else f":{value['public_port']}"
    return f"https://{value['domain']}{suffix}/"


def render_nginx(value: dict, *, challenge: bool = False) -> str:
    domain, port = value["domain"], value["public_port"]
    access = ""
    if value["sources"]:
        access = "allow 127.0.0.1;\n        allow ::1;\n        " + "\n        ".join(f"allow {source};" for source in value["sources"]) + "\n        deny all;"
    http = f"""{MARKER}
server {{
    listen 80;
    listen [::]:80;
    server_name {domain};
    location /.well-known/acme-challenge/ {{ root /var/www/letsencrypt; }}
    location / {{ {'return 503;' if challenge else 'return 301 ' + public_url(value).rstrip('/') + '$request_uri;'} }}
}}
"""
    if challenge:
        return http
    return http + f"""
server {{
    listen {port} ssl;
    listen [::]:{port} ssl;
    server_name {domain};
    ssl_certificate /etc/letsencrypt/live/{domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{domain}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    client_max_body_size 10m;
    location = /basaltwater-homebox-readiness {{
        allow 127.0.0.1;
        allow ::1;
        deny all;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_pass http://127.0.0.1:{value['port']}{STATUS_PATH};
    }}
    location / {{
        if (-f {CONFIG}/maintenance) {{ return 503; }}
        {access}
        proxy_pass http://127.0.0.1:{value['port']};
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600s;
    }}
}}
"""


def _owned_files() -> None:
    for path in (UNIT, SITE):
        _safe_path(path)
        if path.exists() and (not path.is_file() or not path.read_text().startswith(MARKER)):
            raise RuntimeError(f"Refusing unmanaged HomeBox file: {path}")
    if LINK.exists() or LINK.is_symlink():
        if not LINK.is_symlink() or LINK.resolve() != SITE or not SITE.is_file():
            raise RuntimeError("Refusing unmanaged HomeBox Nginx link")
    current = ROOT / "current"
    if current.exists() or current.is_symlink():
        if not current.is_symlink() or not current.resolve().is_relative_to(ROOT / "releases"):
            raise RuntimeError("Refusing unmanaged HomeBox current release")


def _write_site(content: str | None) -> None:
    if content is None:
        LINK.unlink(missing_ok=True)
        SITE.unlink(missing_ok=True)
    else:
        write_text_atomic(str(SITE), content, mode=0o644)
        if not LINK.is_symlink():
            LINK.symlink_to(SITE)
    if content is not None or shutil.which("nginx"):
        result = run(["nginx", "-t"], check=True, capture_output=True)
        if "conflicting server name" in (result.stderr or ""):
            raise RuntimeError("HomeBox hostname conflicts with an existing Nginx site")
        _command("systemctl", "reload", "nginx")


def _activate_files(value: dict) -> None:
    binary = release_path(value)
    if _digest(binary) != value["binary_sha256"]:
        raise RuntimeError("HomeBox installed binary digest mismatch")
    current = ROOT / "current"
    temporary = ROOT / ".current-new"
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(binary.parent)
    os.replace(temporary, current)
    write_text_atomic(str(CONFIG / "homebox.env"), render_environment(value, _secrets()))
    write_text_atomic(str(UNIT), render_unit(value), mode=0o644)
    _command("systemctl", "daemon-reload")


def _status(port: int) -> dict:
    value = _request_json(f"http://127.0.0.1:{port}{STATUS_PATH}", local=True)
    if not isinstance(value, dict) or value.get("health") is not True:
        raise RuntimeError("HomeBox did not return a healthy API status")
    return value


def wait_ready(port: int, version: str, *, registration: bool = False) -> None:
    for _ in range(40):
        try:
            value = _status(port)
            if (value.get("allowRegistration") is registration
                    and value.get("build", {}).get("version", "").lstrip("v") == version.lstrip("v")):
                return
        except (OSError, ValueError, RuntimeError, urllib.error.URLError):
            pass
        time.sleep(0.5)
    raise RuntimeError("HomeBox readiness, version, or registration policy check failed")


def _database(value: dict) -> dict:
    database = Path(value["data_path"]) / "homebox.db"
    _safe_path(database)
    owner = database.stat()
    if owner.st_uid != os.geteuid():
        # SQLite mode=ro can still create WAL/SHM files. Probe live inventory
        # as its owner so observing a stopped service cannot break its restart.
        result = subprocess.run([
            sys.executable, "-I", "-c",
            "import json,sqlite3,sys; from pathlib import Path; "
            "c=sqlite3.connect(Path(sys.argv[1]).as_uri()+'?mode=ro',uri=True,timeout=5); "
            "integrity=c.execute('PRAGMA quick_check').fetchone()[0]; "
            "users=c.execute('SELECT count(*) FROM users').fetchone()[0]; c.close(); "
            "print(json.dumps({'integrity':integrity,'users':users}))",
            str(database),
        ], user=owner.st_uid, group=owner.st_gid, extra_groups=[], umask=0o077, cwd="/",
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
            capture_output=True, text=True, check=False, timeout=15)
        if result.returncode:
            raise RuntimeError("HomeBox database could not be checked as its owner")
        observed = json.loads(result.stdout)
        if observed.get("integrity") != "ok":
            raise RuntimeError("HomeBox database integrity check failed")
        return observed
    with contextlib.closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5)) as connection:
        if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise RuntimeError("HomeBox database integrity check failed")
        users = connection.execute("SELECT count(*) FROM users").fetchone()[0]
    return {"integrity": "ok", "users": users}


def _bootstrap(value: dict) -> None:
    """Create the initial user through a short-lived private service."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    name = "basaltwater-homebox-bootstrap"
    bootstrap_env = CONFIG / "bootstrap.env"
    content = render_environment(value, _secrets()).replace(
        'HBOX_OPTIONS_ALLOW_REGISTRATION="false"', 'HBOX_OPTIONS_ALLOW_REGISTRATION="true"'
    ).replace(f'HBOX_WEB_PORT="{value["port"]}"', f'HBOX_WEB_PORT="{port}"')
    write_text_atomic(str(bootstrap_env), content)
    try:
        _command("systemd-run", "--unit", name, "--collect", "--quiet",
                 "--property=User=homebox", "--property=Group=homebox",
                 "--property=UMask=0077", "--property=PrivateDevices=true", "--property=ProtectHome=true",
                 "--property=RuntimeMaxSec=90", "--property=NoNewPrivileges=true",
                 "--property=PrivateTmp=true", "--property=ProtectSystem=strict",
                 f"--property=ReadWritePaths={value['data_path']}",
                 f"--property=WorkingDirectory={value['data_path']}",
                 f"--property=EnvironmentFile={bootstrap_env}", str(release_path(value)))
        wait_ready(port, value["version"], registration=True)
        if _database(value)["users"] != 0:
            raise RuntimeError("Refusing bootstrap into a nonempty HomeBox user database")
        _request_json(f"http://127.0.0.1:{port}/api/v1/users/register", {
            "name": "HomeBox owner", "email": value["email"], "password": _secrets()["password"],
        }, local=True)
        token = _request_json(f"http://127.0.0.1:{port}/api/v1/users/login", {
            "username": value["email"], "password": _secrets()["password"],
        }, local=True)
        if not isinstance(token, dict) or not token.get("token"):
            raise RuntimeError("HomeBox initial login verification failed")
        if _database(value)["users"] != 1:
            raise RuntimeError("HomeBox bootstrap did not create exactly one user")
    finally:
        try:
            _command("systemctl", "stop", name + ".service")
        finally:
            bootstrap_env.unlink(missing_ok=True)


def _archive_add(bundle: tarfile.TarFile, name: str, value: dict) -> None:
    body = json.dumps(value).encode()
    member = tarfile.TarInfo(name)
    member.size = len(body)
    member.mode = 0o600
    bundle.addfile(member, io.BytesIO(body))


def _needs_bootstrap(value: dict) -> bool:
    return value["status"] == "prepared" or value.get("bootstrap_pending", False)


def create_backup(value: dict, destination: Path) -> None:
    """Archive stopped state; callers hold the lock and stop the service."""
    _safe_path(destination)
    _safe_path(destination.parent)
    validate_filesystem_path(str(destination))
    if not destination.is_absolute() or paths_overlap(str(destination), value["data_path"]):
        raise ValueError("HomeBox backup must be an absolute path outside live data")
    validate_state(value)
    secret = _secrets()
    if _digest(release_path(value)) != value["binary_sha256"]:
        raise RuntimeError("Cannot back up a damaged HomeBox executable")
    database = Path(value["data_path"]) / "homebox.db"
    _safe_path(database)
    if database.exists():
        owner = database.stat()
        for suffix in ("-wal", "-shm"):
            sidecar = database.with_name(database.name + suffix)
            _safe_path(sidecar)
            if sidecar.exists() and sidecar.stat().st_uid == 0 and owner.st_uid != 0:
                # Repair sidecars left by older root-run health probes, only
                # while the service is stopped under the operation lock.
                if not sidecar.is_file():
                    raise RuntimeError("Invalid HomeBox SQLite sidecar")
                os.chown(sidecar, owner.st_uid, owner.st_gid)
                sidecar.chmod(0o600)
    if not _needs_bootstrap(value) and _database(value)["users"] < 1:
        raise RuntimeError("Cannot back up HomeBox without an initialized user database")
    data = Path(value["data_path"])
    paths = [data, *data.rglob("*")]
    if any(p.is_symlink() or not (p.is_file() or p.is_dir()) for p in paths):
        raise RuntimeError("HomeBox data contains unsupported links or special files")
    size = sum(p.stat().st_size for p in paths if p.is_file()) + release_path(value).stat().st_size
    if shutil.disk_usage(destination.parent).free < 2 * size + 256 * 1024 * 1024:
        raise RuntimeError("Insufficient free space for a complete HomeBox recovery archive")
    if destination.exists():
        raise FileExistsError(destination)
    descriptor, staging = tempfile.mkstemp(prefix=".homebox-backup-", dir=destination.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            with tarfile.open(fileobj=stream, mode="w:gz", dereference=True) as bundle:
                _archive_add(bundle, "state.json", value)
                _archive_add(bundle, "secrets.json", secret)
                bundle.add(release_path(value), arcname="homebox", recursive=False)
                for path in paths:
                    bundle.add(path, arcname=str(Path("data") / path.relative_to(data)), recursive=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(staging, destination)
        directory = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(staging).unlink(missing_ok=True)


def unpack_backup(archive: Path, destination: Path) -> dict:
    """Validate and unpack a trusted root-owned archive without tar extraction."""
    _private_file(archive)
    with tarfile.open(archive) as bundle:
        members = bundle.getmembers()
        _validate_members(members)
        if sum(m.size for m in members) + 256 * 1024 * 1024 > shutil.disk_usage(destination).free:
            raise RuntimeError("Insufficient space to unpack HomeBox backup")
        for member in members:
            name = PurePosixPath(member.name)
            if member.name not in {"state.json", "secrets.json", "homebox", "data"} and (not name.parts or name.parts[0] != "data"):
                raise ValueError("Unexpected file in HomeBox backup")
            target = destination / str(name)
            if member.isdir():
                target.mkdir(mode=0o700, parents=True, exist_ok=True)
            else:
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                with bundle.extractfile(member) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
                target.chmod(0o600)
    value = validate_state(json.loads((destination / "state.json").read_text()))
    if _digest(destination / "homebox") != value["binary_sha256"]:
        raise RuntimeError("Backup HomeBox binary digest mismatch")
    secret = json.loads((destination / "secrets.json").read_text())
    if not all(isinstance(secret.get(k), str) and re.fullmatch(r"[A-Za-z0-9_-]{32,128}", secret[k]) for k in ("pepper", "password")):
        raise RuntimeError("Backup HomeBox secrets are invalid")
    if not (destination / "data").is_dir():
        raise RuntimeError("Backup is missing HomeBox data")
    if not _needs_bootstrap(value):
        _database({**value, "data_path": str(destination / "data")})
    return value


def _restore_unpacked(directory: Path, value: dict) -> None:
    data = Path(value["data_path"])
    _check_storage(value)
    account = _ensure_account()
    _safe_path(data)
    data.mkdir(mode=0o700, parents=True, exist_ok=True)
    required = sum(p.stat().st_size for p in (directory / "data").rglob("*") if p.is_file())
    if shutil.disk_usage(data).free < required + 256 * 1024 * 1024:
        raise RuntimeError("Insufficient space to restore HomeBox data")
    # Repair a damaged executable from the verified archive before touching data.
    binary = release_path(value)
    _safe_path(binary)
    binary.parent.mkdir(parents=True, exist_ok=True)
    staged = binary.parent / ".restore-binary"
    _safe_path(staged)
    shutil.copyfile(directory / "homebox", staged)
    staged.chmod(0o755)
    os.replace(staged, binary)
    for path in data.iterdir():
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    shutil.copytree(directory / "data", data, dirs_exist_ok=True)
    for path in [data, *data.rglob("*")]:
        os.chown(path, account.pw_uid, account.pw_gid)
        path.chmod(0o700 if path.is_dir() else 0o600)
    secret = json.loads((directory / "secrets.json").read_text())
    write_json_atomic(str(CONFIG / "secrets.json"), secret)
    _activate_files(value)
    _save_state(value)
    if value["status"] == "ready":
        _command("systemctl", "enable", "--now", SERVICE)
        wait_ready(value["port"], value["version"])
        _write_site(render_nginx(value) if value["domain"] else None)
        _frontend_ready(value, maintenance=True)
    else:
        _command("systemctl", "disable", "--now", SERVICE)
        _write_site(None)


def _recover(archive: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="restore-", dir=BACKUPS) as temporary:
        value = unpack_backup(archive, Path(temporary))
        _stop_service()
        _restore_unpacked(Path(temporary), value)


def _preserve_damaged(value: dict, destination: Path) -> None:
    """Keep raw evidence before an explicit repair, including damaged secrets."""
    paths = [(Path(value["data_path"]), "data"), (CONFIG, "config"), (STATE, "state.json"),
             (release_path(value), "homebox")]
    for path, _ in paths:
        _safe_path(path)
        if path.is_dir():
            for child in path.rglob("*"):
                if child.is_symlink() or not (child.is_dir() or child.is_file()):
                    raise RuntimeError("Cannot preserve damaged HomeBox with links or special files")
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            with tarfile.open(fileobj=stream, mode="w:gz") as bundle:
                for path, name in paths:
                    if path.exists():
                        bundle.add(path, arcname=name)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def _maintenance(archive: Path | None) -> None:
    # Nginx needs directory traversal to stat this flag, but cannot read secrets.
    CONFIG.chmod(0o711)
    write_json_atomic(str(CONFIG / "maintenance"), {"backup": str(archive) if archive else None}, mode=0o644)


def _require_idle() -> None:
    if (CONFIG / "maintenance").exists():
        try:
            marker = json.loads((CONFIG / "maintenance").read_text())
        except (ValueError, OSError) as exc:
            raise RuntimeError("Invalid HomeBox maintenance state; inspect it before recovery") from exc
        if isinstance(marker, dict) and "backup" in marker and marker["backup"] is None:
            # No application mutation begins until the recovery archive path
            # has been durably recorded. Replaying this preparation is safe.
            remove_file_durable(str(CONFIG / "maintenance"))
            return
        raise RuntimeError("HomeBox operation was interrupted; inspect /etc/homebox/maintenance and restore its backup before retrying")


def _frontend_ready(value: dict, *, maintenance: bool = False) -> None:
    if not value["domain"]:
        return
    result = run(["curl", "--fail", "--silent", "--show-error", "--noproxy", "*", "--max-time", "15",
                  "--resolve", f"{value['domain']}:{value['public_port']}:127.0.0.1",
                  public_url(value).rstrip("/") + ("/basaltwater-homebox-readiness" if maintenance else STATUS_PATH)],
                 check=True, capture_output=True)
    status = json.loads(result.stdout)
    if status.get("health") is not True or status.get("allowRegistration") is not False:
        raise RuntimeError("HomeBox HTTPS readiness failed")


def _version_parts(version: str) -> tuple[int, int, int]:
    """Return validated HomeBox version components for release ordering."""
    return tuple(map(int, validate_homebox_version(version)[1:].split(".")))


def _reserve_backend_port(previous: dict, desired: dict) -> None:
    """Fail before stopping HomeBox when a changed private listener is unavailable."""
    if _active() and previous["port"] == desired["port"]:
        return
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", desired["port"]))


def prune_recovery_archives() -> None:
    """Retain recent automatic pre-update archives without touching manual backups."""
    _safe_path(BACKUPS)
    archives: list[Path] = []
    for path in BACKUPS.iterdir():
        if not _RECOVERY_ARCHIVE_RE.fullmatch(path.name):
            continue
        _safe_path(path)
        info = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode):
            raise RuntimeError(f"Invalid HomeBox recovery archive: {path}")
        archives.append(path)
    archives.sort(key=lambda path: path.stat().st_mtime_ns, reverse=True)
    for path in archives[RECOVERY_ARCHIVE_RETENTION:]:
        remove_file_durable(str(path))


def _transition_to_ready(
    previous: dict,
    desired: dict,
    *,
    bootstrap: bool,
    before_activate: Callable[[], None] | None = None,
    after_recovery: Callable[[], None] | None = None,
) -> Path:
    """Activate one verified release with the same recovery path for all updates."""
    _reserve_backend_port(previous, desired)
    archive = BACKUPS / f"{time.time_ns()}-before-setup.tar.gz"
    _maintenance(None)
    was_active = _active()
    _stop_service()
    try:
        create_backup(previous, archive)
    except BaseException:
        if was_active:
            _command("systemctl", "start", SERVICE)
        remove_file_durable(str(CONFIG / "maintenance"))
        raise
    _maintenance(archive)
    activated = False
    try:
        if before_activate is not None:
            before_activate()
        _activate_files(desired)
        if bootstrap:
            _bootstrap(desired)
        _command("systemctl", "enable", "--now", SERVICE)
        wait_ready(desired["port"], desired["version"])
        if _database(desired)["users"] < 1:
            raise RuntimeError("HomeBox has no user; refusing to expose an incomplete installation")
        _write_site(render_nginx(desired) if desired["domain"] else None)
        _frontend_ready(desired, maintenance=True)
        _save_state(desired)
        # A later probe cannot safely roll back writes accepted after this point.
        activated = True
        remove_file_durable(str(CONFIG / "maintenance"))
        _frontend_ready(desired)
    except BaseException:
        if not activated:
            try:
                _recover(archive)
                if after_recovery is not None:
                    after_recovery()
                remove_file_durable(str(CONFIG / "maintenance"))
            except Exception as recovery_error:
                raise RuntimeError(
                    f"HomeBox recovery failed; service remains in maintenance. Restore {archive}"
                ) from recovery_error
        raise
    try:
        prune_recovery_archives()
    except (OSError, RuntimeError) as exc:
        print(f"  ⚠ Could not prune old HomeBox recovery archives: {exc}")
    return archive


def update_homebox(version: str) -> tuple[str, bool]:
    """Upgrade a ready managed service through the setup transaction."""
    target = validate_homebox_version(version)
    require_amd64()
    with homebox_lock():
        _owned_files()
        _require_idle()
        previous = read_state()
        if previous is None or previous["status"] == "disabled":
            return "", False
        if previous["status"] != "ready" or _needs_bootstrap(previous):
            raise RuntimeError("HomeBox updates require a completed, ready installation")
        _check_storage(previous)
        _secrets()
        _ensure_account()
        if _digest(release_path(previous)) != previous["binary_sha256"]:
            raise RuntimeError("Installed HomeBox binary has changed")
        if _version_parts(target) <= _version_parts(previous["version"]):
            return previous["version"], False
        release = stage_release(target)
        desired = {**previous, **release, "status": "ready", "bootstrap_pending": False}
        _transition_to_ready(previous, desired, bootstrap=False)
        return desired["version"], True


def update_homebox_to_latest() -> tuple[str, bool] | None:
    """Resolve and apply the latest stable release for a ready managed service."""
    current = read_state()
    if current is None or current["status"] != "ready" or _needs_bootstrap(current):
        return None
    return update_homebox(latest_homebox_version())


def _unit_properties(unit: str, properties: str) -> dict[str, str | bool]:
    result = run(
        ["systemctl", "show", unit, f"--property={properties},LoadState", "--no-pager"],
        check=False,
        capture_output=True,
    )
    value: dict[str, str | bool] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, item = line.split("=", 1)
            value[key] = item
    value["available"] = result.returncode == 0 and value.get("LoadState") != "not-found"
    return value


def _automatic_update_health() -> dict:
    """Observe the optional recurring updater without exposing service secrets."""
    job = _unit_properties(AUTO_UPDATE_SERVICE, "ActiveState,Result,ExecMainStatus")
    timer = _unit_properties(AUTO_UPDATE_TIMER, "ActiveState,NextElapseUSecRealtime")
    job_failed = job.get("ActiveState") == "failed" or job.get("Result") not in (None, "", "success")
    timer_active = timer.get("ActiveState") == "active"
    timer_scheduled = timer.get("NextElapseUSecRealtime") not in (None, "", "n/a")
    source = UPDATE_STATE if UPDATE_STATE.exists() else None
    check: dict[str, Any] = {}
    age_seconds: int | None = None
    if source is not None:
        try:
            age_seconds = max(0, int(time.time() - source.stat().st_mtime))
            _private_file(UPDATE_STATE)
            loaded = json.loads(UPDATE_STATE.read_text())
            if isinstance(loaded, dict):
                check = loaded
        except (OSError, ValueError, RuntimeError):
            pass
    check_successful = (
        check.get("schema_version") == 1 and check.get("successful") is True
        if source is not None
        else None
    )
    stale = source is not None and (age_seconds is None or age_seconds > MAX_UPDATE_AGE_SECONDS)
    if source is None:
        check["status"] = "pending"
    configured = timer.get("available") is True
    return {
        "configured": configured,
        "healthy": (
            not configured or (
                not job_failed
                and timer_active
                and timer_scheduled
                and (source is None or (check_successful and not stale))
            )
        ),
        "job": {"failed": job_failed, **job},
        "timer": {"active": timer_active, "scheduled": timer_scheduled, **timer},
        "check": {
            **check,
            "age_seconds": age_seconds,
            "max_age_seconds": MAX_UPDATE_AGE_SECONDS,
            "stale": stale,
            "successful": check_successful,
        },
    }


def _files_match(value: dict) -> bool:
    """A healthy process is insufficient if managed configuration has drifted."""
    try:
        _private_file(CONFIG / "homebox.env")
    except (OSError, ValueError, RuntimeError):
        return False
    expected = {UNIT: render_unit(value), CONFIG / "homebox.env": render_environment(value, _secrets())}
    if value["domain"]:
        expected[SITE] = render_nginx(value)
        if not LINK.is_symlink() or LINK.resolve() != SITE:
            return False
    elif SITE.exists() or LINK.is_symlink():
        return False
    return ((ROOT / "current").resolve() == release_path(value).parent
            and all(path.is_file() and path.read_text() == content for path, content in expected.items()))


def setup_homebox(config) -> None:
    validate_homebox_settings(config)
    if config.homebox is None:
        return
    if config.dry_run or is_dry_run():
        print("  [DRY-RUN] Would configure HomeBox with verified release, private bootstrap, and recovery backup")
        return
    if not can_manage_system_services(config.machine_type):
        raise RuntimeError("HomeBox requires systemd service management")
    require_amd64()
    with homebox_lock():
        _owned_files()
        _require_idle()
        previous = read_state()
        if config.homebox == []:
            if previous is None:
                return
            run(["systemctl", "disable", "--now", AUTO_UPDATE_TIMER], check=False, capture_output=True)
            _maintenance(None)
            _stop_service()
            if UNIT.exists():
                _command("systemctl", "disable", SERVICE)
            _write_site(None)
            if can_manage_firewall(config.machine_type):
                _remove_acme_rule()
            UNIT.unlink(missing_ok=True)
            _command("systemctl", "daemon-reload")
            _save_state({**previous, "status": "disabled", "bootstrap_pending": _needs_bootstrap(previous)})
            remove_file_durable(str(CONFIG / "maintenance"))
            print("  HomeBox disabled; inventory, secrets, backups, and releases retained")
            return
        desired = homebox_settings(config)
        if previous and (previous["data_path"] != desired["data_path"] or previous["email"] != desired["email"]):
            raise ValueError("HomeBox data path and initial account are immutable; use an explicit migration or the application account settings")
        from common.storage_steps import assert_declared_storage_mount
        assert_declared_storage_mount(config, desired["data_path"])
        data = Path(desired["data_path"])
        mount = _storage(data)
        if previous:
            _check_storage(previous)
            _secrets()
        elif (data.exists() and any(data.iterdir())) or UNIT.exists() or STATE.exists() or (ROOT / "current").exists():
            raise RuntimeError("Refusing to adopt an unmanaged HomeBox installation")
        for package in ("ca-certificates", "curl"):
            if not install_package(package, package, f"apt-get install -y -qq {package}"):
                raise RuntimeError(f"Failed to install HomeBox dependency {package}")
        account = _ensure_account()
        _private_dir(CONFIG)
        CONFIG.chmod(0o711)
        _private_dir(BACKUPS)
        _safe_path(ROOT)
        ROOT.mkdir(mode=0o755, exist_ok=True)
        version = config.homebox_version or (previous["version"] if previous else DEFAULT_VERSION)
        if previous and tuple(map(int, version[1:].split("."))) < tuple(map(int, previous["version"][1:].split("."))):
            raise ValueError("HomeBox downgrades require restoring a complete backup from that version")
        if previous and version == previous["version"]:
            release = {k: previous[k] for k in ("version", "archive_sha256", "binary_sha256")}
            if _digest(release_path(previous)) != previous["binary_sha256"]:
                raise RuntimeError("Installed HomeBox binary has changed")
        else:
            release = stage_release(version)
        desired = {**desired, **release, "schema": 1, "mount": mount, "status": "ready"}
        _safe_path(data)
        data.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chown(data, account.pw_uid, account.pw_gid)
        data.chmod(0o700)
        _check_storage(desired)
        _secrets(create=previous is None)
        # Seed recoverable management state before any application startup.
        if previous is None:
            previous = {**desired, "status": "prepared"}
            _save_state(previous)
        if previous == desired and _active() and _files_match(desired):
            wait_ready(desired["port"], version)
            _database(desired)
            _frontend_ready(desired)
            print(f"  HomeBox already healthy: {public_url(desired)}")
            return
        acme_rule_added = False

        def prepare_ingress() -> None:
            nonlocal acme_rule_added
            if not desired["domain"]:
                return
            from web.ssl_steps import (
                install_certbot,
                obtain_letsencrypt_certificate,
                setup_certificate_renewal,
            )

            if not install_package("nginx", "nginx", "apt-get install -y -qq nginx"):
                raise RuntimeError("Failed to install Nginx")
            _command("systemctl", "enable", "--now", "nginx")
            if can_manage_firewall(config.machine_type):
                # Public HTTP is needed for ACME even when HTTPS access is
                # source-restricted. Its site serves only challenges/redirects.
                acme_rule_added = _ensure_acme_rule()
            # Challenge-only site cannot route to an unverified application.
            _write_site(render_nginx(desired, challenge=True))
            install_certbot(config)
            if not obtain_letsencrypt_certificate([desired["domain"]], config.ssl_email, desired["domain"]):
                raise RuntimeError("HomeBox TLS certificate issuance failed")
            setup_certificate_renewal()

        def cleanup_failed_initial_ingress() -> None:
            if acme_rule_added and previous["status"] == "prepared":
                _remove_acme_rule()

        archive = _transition_to_ready(
            previous,
            desired,
            bootstrap=_needs_bootstrap(previous),
            before_activate=prepare_ingress,
            after_recovery=cleanup_failed_initial_ingress,
        )
        if not desired["domain"] and can_manage_firewall(config.machine_type):
            _remove_acme_rule()
        print(f"  HomeBox ready: {public_url(desired)}")
        print(f"  Initial login: {desired['email']}; generated password in {CONFIG}/secrets.json (root only)")
        print(f"  Recovery archive: {archive}")


def health_homebox() -> dict:
    result: dict[str, Any] = {"healthy": False, "service_active": _active()}
    try:
        value = read_state()
        if value is None:
            result["error"] = "HomeBox is not managed"
            return result
        result.update({k: value[k] for k in ("version", "status", "data_path", "port")})
        result["url"] = public_url(value)
        result["maintenance"] = (CONFIG / "maintenance").exists()
        _check_storage(value)
        result["free_bytes"] = shutil.disk_usage(value["data_path"]).free
        result["database"] = _database(value)
        _secrets()
        _owned_files()
        _private_file(CONFIG / "homebox.env")
        result["configuration_matches"] = _files_match(value)
        if not result["configuration_matches"]:
            raise RuntimeError("HomeBox managed configuration has drifted; rerun setup")
        if _digest(release_path(value)) != value["binary_sha256"]:
            raise RuntimeError("HomeBox binary digest mismatch")
        status = _status(value["port"])
        result["registration_closed"] = status.get("allowRegistration") is False
        result["version_matches"] = status.get("build", {}).get("version", "").lstrip("v") == value["version"].lstrip("v")
        _frontend_ready(value)
        result["automatic_update"] = _automatic_update_health()
        result["healthy"] = bool(result["service_active"] and not result["maintenance"] and result["registration_closed"]
                                 and result["version_matches"] and result["database"]["users"] > 0 and value["status"] == "ready"
                                 and result["automatic_update"]["healthy"])
    except (OSError, ValueError, RuntimeError, sqlite3.Error, urllib.error.URLError, subprocess.SubprocessError) as exc:
        result["error"] = str(exc)
    return result


def operate_homebox(action: str, path: str, *, yes: bool = False, dry_run: bool = False) -> dict:
    """Run a deliberate target backup or restore using the installation lock."""
    if action not in {"backup", "restore"}:
        raise ValueError("Unknown HomeBox operation")
    destination = Path(path)
    validate_filesystem_path(path)
    if not destination.is_absolute():
        raise ValueError("HomeBox backup path must be absolute")
    if action == "restore" and not yes:
        raise ValueError("Restore replaces current inventory; pass --yes")
    if dry_run:
        return {"action": action, "path": path, "dry_run": True}
    with homebox_lock():
        _owned_files()
        try:
            value = read_state()
        except RuntimeError:
            if action != "restore":
                raise
            value = None
        if value is None and (action != "restore" or not (STATE.exists() or UNIT.exists())):
            raise RuntimeError("Set up HomeBox at the original data path before restoring")
        if value is not None:
            _check_storage(value)
        _private_dir(BACKUPS)
        if action == "backup":
            _require_idle()
            was_active = _active()
            _maintenance(None)
            _stop_service()
            try:
                create_backup(value, destination)
            finally:
                if was_active:
                    _command("systemctl", "start", SERVICE)
                    wait_ready(value["port"], value["version"])
                remove_file_durable(str(CONFIG / "maintenance"))
        else:
            with tempfile.TemporaryDirectory(prefix="restore-", dir=BACKUPS) as temporary:
                restored = unpack_backup(destination, Path(temporary))
                if paths_overlap(str(destination), restored["data_path"]):
                    raise ValueError("Restore archive must be outside HomeBox live data")
                if value is None:
                    value = {**restored, "mount": _storage(Path(restored["data_path"]))}
                    _check_storage(value)
                if restored["data_path"] != value["data_path"]:
                    raise ValueError("Restore data path differs from the installed HomeBox path")
                # A replacement VM has a different filesystem identity; explicitly
                # retain its already-validated managed mount instead of the old UUID.
                restored["mount"] = value["mount"]
                _maintenance(destination)
                _stop_service()
                safeguard = BACKUPS / f"{time.time_ns()}-before-restore.tar.gz"
                try:
                    if _digest(release_path(value)) != value["binary_sha256"]:
                        raise RuntimeError("Damaged binary")
                    create_backup(value, safeguard)
                except (OSError, ValueError, RuntimeError, sqlite3.Error, subprocess.SubprocessError):
                    safeguard = BACKUPS / f"{time.time_ns()}-damaged-before-restore.tar.gz"
                    _preserve_damaged(value, safeguard)
                _restore_unpacked(Path(temporary), restored)
                remove_file_durable(str(CONFIG / "maintenance"))
                if restored["status"] == "ready":
                    _frontend_ready(restored)
                return {"action": action, "path": path, "success": True, "previous_state_archive": str(safeguard)}
        return {"action": action, "path": path, "success": True}


def main() -> int:
    parser = argparse.ArgumentParser(description="Target-side managed HomeBox operations")
    parser.add_argument("action", choices=("health", "backup", "restore"))
    parser.add_argument("path", nargs="?", default="")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        with contextlib.redirect_stdout(sys.stderr):
            value = health_homebox() if args.action == "health" else operate_homebox(args.action, args.path, yes=args.yes, dry_run=args.dry_run)
        print(json.dumps(value, sort_keys=True))
        return 0 if args.action != "health" or value["healthy"] else 1
    except Exception as exc:
        print(json.dumps({"success": False, "error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
