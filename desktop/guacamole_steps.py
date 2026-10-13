"""Install a pinned RDP-only Guacamole gateway behind the HTTPS panel."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import pwd
import shutil
import ssl
import stat
import tarfile
import tempfile
import time
import urllib.request

from lib.atomic_io import fsync_tree, read_json_file, remove_file_durable, rename_path_durable, write_json_atomic, write_text_atomic
from lib.config import SetupConfig
from lib.guacamole_config import (
    CONFIG, RDP_SERVICE, RELEASE, RELEASE_ROOT,
    SERVICE, UNIT_MARKER, VERSION, validate_guacamole_settings,
)
from lib.machine_state import can_manage_system_services
from lib.remote_utils import install_package, is_dry_run, run
from lib.unit_transaction import UnitRecoveryError, inspect_unit_state, remove_units, replace_units, snapshot_unit_file
from lib.validation import validate_filesystem_path

SOURCE_HASHES = {
    "server": "8bc45675da96d7b6f39728160181e3d4ff3c08f460f6d26de5805b642bf13f2b",
    "client": "81f9fd5a7b4377fb0ee295d0d4fec92e9667f2aafaa3d0ed8937f535deabdee4",
}
PACKAGES = (
    "build-essential", "pkg-config", "libcairo2-dev", "libjpeg62-turbo-dev",
    "libpng-dev", "uuid-dev", "libssl-dev", "freerdp3-dev", "libwebp-dev",
    "python3-aiohttp",
)
SCRIPT = "/opt/basaltwater/common/service_tools/guacamole_service.py"


def _safe_directory(path: Path) -> None:
    validate_filesystem_path(str(path), must_exist=False)
    if path.is_symlink() or (path.exists() and (not path.is_dir() or path.stat().st_uid != 0 or path.stat().st_mode & 0o022)):
        raise RuntimeError(f"Unsafe Guacamole directory: {path}")
    path.mkdir(parents=True, mode=0o755, exist_ok=True)


def download_source(kind: str, directory: Path) -> Path:
    """Bound and verify an exact Apache release before using its contents."""
    name = f"guacamole-{kind}-{VERSION}.tar.gz"
    target = directory / name
    digest = hashlib.sha256()
    # The version and hashes are reviewed in source; a remote checksum cannot
    # redefine which artifact this setup trusts. Archive is a stable fallback.
    for host in ("dlcdn.apache.org", "archive.apache.org/dist"):
        try:
            with urllib.request.urlopen(f"https://{host}/guacamole/{VERSION}/source/{name}", timeout=30) as source, target.open("wb") as output:
                total = 0
                deadline = time.monotonic() + 120
                digest = hashlib.sha256()
                # read1 returns after a single underlying read, so a trickling
                # peer cannot defer deadline checks until 64 KiB accumulate.
                while data := source.read1(65536):
                    total += len(data)
                    if time.monotonic() > deadline:
                        raise RuntimeError("Guacamole source download exceeded two minutes")
                    if total > 32 * 1024 * 1024:
                        raise RuntimeError("Guacamole source exceeds 32 MiB")
                    output.write(data)
                    digest.update(data)
            break
        except OSError:
            if host.startswith("archive"):
                raise
    if digest.hexdigest() != SOURCE_HASHES[kind]:
        raise RuntimeError("Guacamole source checksum mismatch")
    return target


def extract_source(archive: Path, directory: Path) -> None:
    """Extract only bounded regular files and directories, never archive links."""
    with tarfile.open(archive) as source:
        size = 0
        for index, member in enumerate(source):
            size += member.size
            if index >= 10000 or size > 128 * 1024 * 1024:
                raise RuntimeError("Guacamole source archive exceeds extraction limits")
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts or not (member.isdir() or member.isfile()):
                raise RuntimeError("Unsafe Guacamole source archive entry")
            target = directory.joinpath(*name.parts)
            validate_filesystem_path(str(target), must_exist=False)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as stream, target.open("xb") as output:
                    shutil.copyfileobj(stream, output)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
                # Autotools release archives contain generated files. Preserve
                # their timestamps so make never tries to regenerate them with
                # a different host autoconf/automake toolchain.
                os.utime(target, (member.mtime, member.mtime))


def install_release() -> None:
    """Publish a complete release atomically; unchanged reruns reuse it."""
    root, release = Path(RELEASE_ROOT), Path(RELEASE)
    _safe_directory(root)
    if release.exists() or release.is_symlink():
        _safe_directory(release)
        if read_json_file(str(release / "release.json")) != {"version": VERSION, "sources": SOURCE_HASHES}:
            raise RuntimeError("Refusing an unmanaged Guacamole release")
        validate_release(release)
        return
    with tempfile.TemporaryDirectory(prefix=".guacamole-build-", dir=root) as temporary:
        workspace = Path(temporary)
        for kind in SOURCE_HASHES:
            extract_source(download_source(kind, workspace), workspace)
        server = workspace / f"guacamole-server-{VERSION}"
        run([
            "./configure", f"--prefix={RELEASE}", "--with-rdp", "--without-vnc",
            "--without-ssh", "--without-telnet", "--without-terminal", "--without-pulse",
            "--without-vorbis", "--without-libavcodec", "--without-libavformat",
            "--without-libavutil", "--without-libswscale", "--disable-kubernetes",
            "--disable-guacenc", "--disable-guaclog", f"--with-freerdp-plugin-dir={RELEASE}/lib/freerdp3",
            # Debian's FreeRDP 3 headers mark legacy struct members deprecated.
            # Guacamole 1.6 supports that API; retain other compiler errors.
            "CFLAGS=-O2 -Wno-error=deprecated-declarations",
        ], cwd=str(server), timeout=300)
        run(["make", "-j2"], cwd=str(server), timeout=1800)
        staging = workspace / "staging"
        run(["make", "install", f"DESTDIR={staging}"], cwd=str(server), timeout=300)
        staged_release = staging / RELEASE.lstrip("/")
        if not list((staged_release / "lib").glob("libguac-client-rdp.so*")):
            raise RuntimeError("Guacamole was built without RDP support")
        share = staged_release / "share"
        share.mkdir(mode=0o755, exist_ok=True)
        client = workspace / f"guacamole-client-{VERSION}"
        javascript = client / "guacamole-common-js/src/main/webapp"
        modules = sorted((javascript / "modules").glob("*.js"))
        namespace = javascript / "modules/Namespace.js"
        files = [javascript / "common/license.js", namespace, *(p for p in modules if p != namespace)]
        write_text_atomic(str(share / "all.js"), "\n".join(p.read_text() for p in files), mode=0o644)
        for name in ("LICENSE", "NOTICE"):
            shutil.copyfile(client / name, share / name)
        write_json_atomic(str(staged_release / "release.json"), {"version": VERSION, "sources": SOURCE_HASHES}, mode=0o644)
        validate_release(staged_release)
        fsync_tree(str(staged_release))
        rename_path_durable(str(staged_release), RELEASE)


def validate_release(release: Path) -> None:
    """Never reuse mutable code or libraries escaping the managed release."""
    for path in (release, *release.rglob("*")):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            if info.st_uid != 0 or not path.resolve(strict=True).is_relative_to(release.resolve()):
                raise RuntimeError(f"Unsafe Guacamole release link: {path}")
        elif (not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
              or info.st_uid != 0 or info.st_mode & 0o6022):
            raise RuntimeError(f"Unsafe Guacamole release entry: {path}")
    if (not (release / "sbin/guacd").is_file()
            or not os.access(release / "sbin/guacd", os.X_OK)
            or not (release / "share/all.js").is_file()
            or not list((release / "lib").glob("libguac-client-rdp.so*"))):
        raise RuntimeError("Guacamole release is incomplete; inspect it before rerunning setup")


def render_units(username: str, uid: int, gid: int) -> dict[str, str]:
    """Confine guacd to a namespace containing only a fixed XRDP relay."""
    hardened = f"""Type=notify
NotifyAccess=main
TimeoutStartSec=30
User={username}
Group={gid}
UMask=0077
NoNewPrivileges=true
PrivateDevices=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ProtectControlGroups=true
ProtectKernelModules=true
ProtectKernelTunables=true
RestrictSUIDSGID=true
LockPersonality=true
RestrictAddressFamilies=AF_UNIX AF_INET
LimitCORE=0
Restart=on-failure
RestartSec=5
KillMode=mixed
TimeoutStopSec=15
StandardOutput=null
StandardError=journal
"""
    return {
        f"{RDP_SERVICE}.service": f"""{UNIT_MARKER}
[Unit]
Description=Private Guacamole connection to local XRDP
After=xrdp.service
Requires=xrdp.service
[Service]
{hardened}RuntimeDirectory={RDP_SERVICE}
RuntimeDirectoryMode=0700
ExecStart=/usr/bin/python3 {SCRIPT} --rdp-relay
[Install]
WantedBy=multi-user.target
""",
        f"{SERVICE}.service": f"""{UNIT_MARKER}
[Unit]
Description=Embedded Guacamole browser desktop
After={RDP_SERVICE}.service user@{uid}.service
Requires={RDP_SERVICE}.service user@{uid}.service
[Service]
{hardened}PrivateNetwork=true
SupplementaryGroups=www-data
RuntimeDirectory={SERVICE}
RuntimeDirectoryMode=0711
ReadWritePaths=/run/user/{uid}
Environment=HOME=/nonexistent
ExecStart=/usr/bin/python3 {SCRIPT} --config {CONFIG}
[Install]
WantedBy=multi-user.target
""",
    }


def preflight_units() -> None:
    for name in (SERVICE, RDP_SERVICE):
        previous = snapshot_unit_file(f"/etc/systemd/system/{name}.service")
        if previous and (previous["uid"] != 0 or previous["mode"] & 0o022 or UNIT_MARKER not in previous["content"]):
            raise RuntimeError(f"Refusing unmanaged or unsafe Guacamole service: {name}")


def preflight_configuration() -> None:
    path = Path(CONFIG)
    if path.is_symlink() or (path.exists() and (not path.is_file() or path.stat().st_uid != 0 or path.stat().st_mode & 0o022)):
        raise RuntimeError("Refusing unsafe Guacamole configuration")


def remove_guacamole() -> None:
    """Remove only managed access services; retain the desktop and release."""
    preflight_units()
    preflight_configuration()
    path = Path(CONFIG)
    if any(Path(f"/etc/systemd/system/{name}.service").exists() for name in (SERVICE, RDP_SERVICE)):
        remove_units((f"{SERVICE}.service", f"{RDP_SERVICE}.service"))
    path.unlink(missing_ok=True)


def configure_services(settings: dict, units: dict[str, str]) -> None:
    """Restore the previous non-secret configuration on activation failure."""
    preflight_configuration()
    previous = snapshot_unit_file(CONFIG)
    state = inspect_unit_state(f"{SERVICE}.service")
    try:
        write_json_atomic(CONFIG, settings, mode=0o644)
        replace_units(units, activate=(f"{RDP_SERVICE}.service", f"{SERVICE}.service"))
    except BaseException as error:
        if previous is None:
            remove_file_durable(CONFIG)
        else:
            write_text_atomic(CONFIG, **previous)
        # Unit rollback restarts old definitions before returning. Reload their
        # restored configuration too; leave unresolved unit recovery quiescent.
        if state["ActiveState"] == "active" and not isinstance(error, UnitRecoveryError):
            run(["systemctl", "restart", f"{SERVICE}.service"])
            if inspect_unit_state(f"{SERVICE}.service")["ActiveState"] != "active":
                raise RuntimeError("Guacamole configuration rollback needs service recovery") from error
        raise


def configure_guacamole(config: SetupConfig, identities: list[str]) -> None:
    """Called by the owning panel setup before publishing its desktop route."""
    validate_guacamole_settings(config)
    if is_dry_run():
        print("  [DRY-RUN] Would configure the private Guacamole desktop")
        return
    if not can_manage_system_services():
        raise RuntimeError("Guacamole requires a machine that can manage system services")
    preflight_units()
    preflight_configuration()
    for package in PACKAGES:
        if not install_package(package, package, ["apt-get", "install", "-y", "-qq", "--no-install-recommends", package]):
            raise RuntimeError(f"Failed to install Guacamole dependency: {package}")
    install_release()
    account = pwd.getpwnam(config.username)
    if account.pw_uid == 0:
        raise RuntimeError("Guacamole cannot run as root")
    # Pin the configured local XRDP certificate instead of disabling checks.
    cert = Path("/etc/xrdp/cert.pem")
    pem = cert.read_text()
    fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()
    origins = ["https://" + (f"[{host}]" if ":" in host else host) + (f":{config.web_panel_port}" if config.web_panel_port != 443 else "") for host in identities]
    validate_filesystem_path(CONFIG, must_exist=False)
    _safe_directory(Path(CONFIG).parent)
    configure_services({"version": 1, "username": config.username, "uid": account.pw_uid,
                        "origins": origins, "certificate": fingerprint,
                        "socket_gid": pwd.getpwnam("www-data").pw_gid},
                       render_units(config.username, account.pw_uid, account.pw_gid))
    print("  ✓ Guacamole browser desktop configured (HTTPS panel only)")
