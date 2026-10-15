"""Install a verified Guacamole binary package behind the HTTPS panel."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import pwd
import re
import ssl
import stat
import tempfile
import time
from urllib.parse import quote
import urllib.request

from lib.atomic_io import read_json_file, remove_file_durable, write_json_atomic, write_text_atomic
from lib.config import SetupConfig
from lib.guacamole_config import (
    BINARY_HASHES, BUILD_REPOSITORY, BUILD_VERSION, CONFIG, PACKAGE_NAME, PACKAGE_VERSION,
    RDP_SERVICE, RELEASE, RELEASE_ROOT, SERVICE, SSH_CONFIG, SSH_HOST_KEY, SSH_SERVICE,
    SSH_SOCKET, UNIT_MARKER, VERSION, validate_guacamole_settings,
)
from lib.machine_state import can_manage_system_services
from lib.remote_utils import install_package, is_dry_run, read_os_release, run
from lib.unit_transaction import UnitRecoveryError, inspect_unit_state, remove_units, replace_units, snapshot_unit_file
from lib.validation import validate_filesystem_path
from lib.validators import validate_username

PACKAGES = ("python3-aiohttp",)
SCRIPT = "/opt/basaltwater/common/service_tools/guacamole_service.py"


def _safe_directory(path: Path) -> None:
    validate_filesystem_path(str(path), must_exist=False)
    if path.is_symlink() or (path.exists() and (not path.is_dir() or path.stat().st_uid != 0 or path.stat().st_mode & 0o022)):
        raise RuntimeError(f"Unsafe Guacamole directory: {path}")
    path.mkdir(parents=True, mode=0o755, exist_ok=True)


def binary_architecture() -> str:
    """Reject unsupported targets before installing any Guacamole packages."""
    distribution = read_os_release()
    if distribution.get("ID") != "debian" or distribution.get("VERSION_CODENAME") != "trixie":
        raise RuntimeError("--guacamole binary packages require Debian 13 (Trixie)")
    arch = run(["dpkg", "--print-architecture"], capture_output=True).stdout.strip()
    if arch not in BINARY_HASHES:
        raise RuntimeError(f"No Guacamole binary package for Debian Trixie architecture {arch!r}")
    return arch


def download_package(architecture: str, directory: Path) -> Path:
    """Bound and verify a release asset using the checksum reviewed in source."""
    name = f"{PACKAGE_NAME}_{PACKAGE_VERSION}_trixie_{architecture}.deb"
    target = directory / name
    validate_filesystem_path(str(target), must_exist=False)
    url = f"https://github.com/{BUILD_REPOSITORY}/releases/download/v{BUILD_VERSION}/{quote(name, safe='')}"
    digest = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=30) as source, target.open("xb") as output:
        total = 0
        deadline = time.monotonic() + 120
        while data := source.read1(65536):
            total += len(data)
            if time.monotonic() > deadline:
                raise RuntimeError("Guacamole binary download exceeded two minutes")
            if total > 64 * 1024 * 1024:
                raise RuntimeError("Guacamole binary exceeds 64 MiB")
            output.write(data)
            digest.update(data)
    if digest.hexdigest() != BINARY_HASHES[architecture]:
        raise RuntimeError("Guacamole binary checksum mismatch")
    return target


def validate_package(package: Path, architecture: str) -> None:
    """Confirm the verified asset is the exact package this setup selected."""
    result = run(["dpkg-deb", "--show", "--showformat=${Package}\n${Version}\n${Architecture}\n",
                  str(package)], capture_output=True)
    if result.stdout.splitlines() != [PACKAGE_NAME, PACKAGE_VERSION, architecture]:
        raise RuntimeError("Guacamole binary package metadata mismatch")


def validate_installed_release(release: Path, architecture: str) -> None:
    validate_release(release)
    metadata = read_json_file(str(release / ".basaltwater-release.json"))
    expected = dict(upstream_version=VERSION, build_version=BUILD_VERSION,
                    package_version=PACKAGE_VERSION, architecture=architecture,
                    distribution="trixie")
    if not isinstance(metadata, dict) or any(metadata.get(key) != value for key, value in expected.items()):
        raise RuntimeError("Refusing an unmanaged Guacamole release")


def install_release() -> None:
    """Install a pinned binary and its runtime dependencies; reuse unchanged installs."""
    arch = binary_architecture()
    root, release = Path(RELEASE_ROOT), Path(RELEASE)
    _safe_directory(root)
    installed = run(
        ["dpkg-query", "--show", "--showformat=${db:Status-Status}\n${Version}\n${Architecture}\n", PACKAGE_NAME],
        capture_output=True, check=False,
    )
    selected = installed.returncode == 0 and installed.stdout.splitlines() == ["installed", PACKAGE_VERSION, arch]
    if release.exists() or release.is_symlink():
        _safe_directory(release)
        validate_installed_release(release, arch)
        if not selected:
            raise RuntimeError("Guacamole release is not owned by the expected installed package")
        return
    with tempfile.TemporaryDirectory(prefix=".guacamole-package-", dir=root) as temporary:
        directory = Path(temporary)
        package = download_package(arch, directory)
        validate_package(package, arch)
        # APT's unprivileged downloader needs access to this verified local file.
        directory.chmod(0o755)
        package.chmod(0o644)
        run(["apt-get", "install", "-y", "--no-install-recommends", "--reinstall", str(package)], timeout=600)
    validate_installed_release(release, arch)


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
            or any(not list((release / "lib").glob(f"libguac-client-{protocol}.so*")) for protocol in ("rdp", "ssh"))):
        raise RuntimeError("Guacamole release is incomplete; inspect it before rerunning setup")


def render_units(username: str, uid: int, gid: int) -> dict[str, str]:
    """Confine guacd to fixed local endpoints; OpenSSH owns Unix logins."""
    if username == "root" or not validate_username(username) or uid <= 0 or gid < 0:
        raise ValueError("Guacamole requires a valid non-root account")
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
        f"{SSH_SERVICE}.socket": f"""{UNIT_MARKER}
[Unit]
Description=Private Guacamole Unix shell login socket
[Socket]
ListenStream={SSH_SOCKET}
Accept=yes
SocketUser={username}
SocketGroup={gid}
SocketMode=0600
DirectoryMode=0711
RemoveOnStop=yes
MaxConnections=2
[Install]
WantedBy=sockets.target
""",
        f"{SSH_SERVICE}@.service": f"""{UNIT_MARKER}
[Unit]
Description=Private Guacamole Unix shell login
PartOf={SSH_SERVICE}.socket
[Service]
ExecStart=/usr/sbin/sshd -i -e -f {SSH_CONFIG}
StandardInput=socket
StandardOutput=socket
StandardError=null
RuntimeDirectory=sshd
RuntimeDirectoryMode=0755
RuntimeDirectoryPreserve=yes
UMask=0077
LimitCORE=0
RuntimeMaxSec=3600
KillMode=control-group
TimeoutStopSec=15
""",
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
After={RDP_SERVICE}.service {SSH_SERVICE}.socket user@{uid}.service
Requires={RDP_SERVICE}.service {SSH_SERVICE}.socket user@{uid}.service
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
    for name in managed_units():
        previous = snapshot_unit_file(f"/etc/systemd/system/{name}")
        if previous and (previous["uid"] != 0 or previous["mode"] & 0o022 or UNIT_MARKER not in previous["content"]):
            raise RuntimeError(f"Refusing unmanaged or unsafe Guacamole service: {name}")


def preflight_configuration() -> None:
    for name in (CONFIG, SSH_CONFIG):
        path = Path(name)
        if path.is_symlink() or (path.exists() and (not path.is_file() or path.stat().st_uid != 0 or path.stat().st_mode & 0o022)):
            raise RuntimeError("Refusing unsafe Guacamole configuration")
    path = Path(SSH_CONFIG)
    if path.exists() and not path.read_text().startswith(UNIT_MARKER + "\n"):
        raise RuntimeError("Refusing unmanaged Guacamole SSH configuration")


def managed_units() -> tuple[str, ...]:
    # Stop the socket first; PartOf also stops any active login instances.
    return (f"{SSH_SERVICE}.socket", f"{SSH_SERVICE}@.service",
            f"{SERVICE}.service", f"{RDP_SERVICE}.service")


def ssh_configuration(username: str) -> str:
    """A separate private endpoint; never weaken the public SSH daemon."""
    if username == "root" or not validate_username(username):
        raise ValueError("Console requires a valid non-root account")
    return f"""{UNIT_MARKER}
HostKey {SSH_HOST_KEY}
UsePAM yes
AuthenticationMethods password
PasswordAuthentication yes
KbdInteractiveAuthentication no
PubkeyAuthentication no
PermitRootLogin no
PermitEmptyPasswords no
AllowUsers {username}
AuthorizedKeysFile none
AllowAgentForwarding no
AllowTcpForwarding no
AllowStreamLocalForwarding no
X11Forwarding no
PermitTunnel no
PermitUserRC no
PermitTTY yes
MaxSessions 1
MaxAuthTries 3
LoginGraceTime 30
LogLevel QUIET
"""


def prepare_ssh_host_key() -> str:
    """Create a private endpoint key, returning only its public known-hosts pin."""
    path = Path(SSH_HOST_KEY)
    _safe_directory(path.parent)
    path.parent.chmod(0o700)
    if path.is_symlink() or (path.exists() and (not path.is_file() or path.stat().st_uid != 0
                                              or path.stat().st_nlink != 1 or path.stat().st_mode & 0o077)):
        raise RuntimeError("Unsafe Guacamole SSH host key")
    if not path.exists():
        run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(path)])
    # Derive from the private key; do not trust a stale adjacent .pub file.
    public = run(["ssh-keygen", "-y", "-f", str(path)], capture_output=True).stdout.strip()
    public = " ".join(public.split()[:2])
    if not re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/]{68}", public):
        raise RuntimeError("Invalid Guacamole SSH host public key")
    return "[127.0.0.1]:2222 " + public


def remove_guacamole() -> None:
    """Remove only managed access services; retain the desktop and release."""
    preflight_units()
    preflight_configuration()
    path = Path(CONFIG)
    if any(Path(f"/etc/systemd/system/{name}").exists() for name in managed_units()):
        remove_units(managed_units())
    path.unlink(missing_ok=True)
    Path(SSH_CONFIG).unlink(missing_ok=True)


def configure_services(settings: dict, units: dict[str, str]) -> None:
    """Restore the previous non-secret configuration on activation failure."""
    preflight_configuration()
    previous = {path: snapshot_unit_file(path) for path in (CONFIG, SSH_CONFIG)}
    state = inspect_unit_state(f"{SERVICE}.service")
    try:
        write_json_atomic(CONFIG, settings, mode=0o644)
        write_text_atomic(SSH_CONFIG, ssh_configuration(settings["username"]), mode=0o644)
        run(["/usr/sbin/sshd", "-t", "-f", SSH_CONFIG])
        replace_units(units, activate=(f"{SSH_SERVICE}.socket", f"{RDP_SERVICE}.service", f"{SERVICE}.service"))
    except BaseException as error:
        for path, snapshot in previous.items():
            if snapshot is None:
                remove_file_durable(path)
            else:
                write_text_atomic(path, **snapshot)
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
    install_release()
    for package in PACKAGES:
        if not install_package(package, package, ["apt-get", "install", "-y", "-qq", "--no-install-recommends", package]):
            raise RuntimeError(f"Failed to install Guacamole dependency: {package}")
    # Server profiles already provide OpenSSH; this is not a second public
    # listener and its password policy is scoped to the private Unix socket.
    if not Path("/usr/sbin/sshd").is_file():
        raise RuntimeError("Guacamole console requires the profile's OpenSSH server")
    account = pwd.getpwnam(config.username)
    if account.pw_uid == 0:
        raise RuntimeError("Guacamole cannot run as root")
    # OpenSSH's compiled-in privilege separation directory is also needed
    # when the public SSH daemon has been stopped. The login unit recreates it
    # after boot; create it now for the configuration preflight below.
    _safe_directory(Path("/run/sshd"))
    # Pin the configured local XRDP certificate instead of disabling checks.
    cert = Path("/etc/xrdp/cert.pem")
    pem = cert.read_text()
    fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()
    origins = ["https://" + (f"[{host}]" if ":" in host else host) + (f":{config.web_panel_port}" if config.web_panel_port != 443 else "") for host in identities]
    validate_filesystem_path(CONFIG, must_exist=False)
    _safe_directory(Path(CONFIG).parent)
    configure_services({"version": 2, "username": config.username, "uid": account.pw_uid,
                        "origins": origins, "certificate": fingerprint,
                        "ssh_host_key": prepare_ssh_host_key(),
                        "socket_gid": pwd.getpwnam("www-data").pw_gid},
                       render_units(config.username, account.pw_uid, account.pw_gid))
    print("  ✓ Guacamole browser desktop configured (HTTPS panel only)")
