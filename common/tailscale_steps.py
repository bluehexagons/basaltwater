"""Opt-in native Debian Tailscale package and daemon setup; no implicit join."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import TYPE_CHECKING

from lib import headscale_client as client
from lib.apt_sources import get_debian_codename
from lib.atomic_io import write_bytes_atomic, write_text_atomic
from lib.headscale_config import validate_headscale_settings, validate_headscale_url
from lib.machine_state import can_manage_firewall, can_manage_system_services
from lib.remote_utils import is_dry_run, missing_packages, run
from lib.setup_directories import check_directory_path, directory_fd

if TYPE_CHECKING:
    from lib.config import SetupConfig

DROPIN = "/etc/systemd/system/tailscaled.service.d/10-basaltwater-privacy.conf"
PRIVACY = '[Service]\nEnvironment="TS_NO_LOGS_NO_SUPPORT=true"\n'
KEYRING = "/usr/share/keyrings/basaltwater-tailscale.gpg"
SOURCE = "/etc/apt/sources.list.d/basaltwater-tailscale.list"
KEY_SHA256 = "3e03dacf222698c60b8e2f990b809ca1b3e104de127767864284e6c228f1fb39"


def preflight(config: SetupConfig) -> str:
    validate_headscale_settings(config, complete=True)
    codename = get_debian_codename()
    if codename not in {"bookworm", "trixie"}:
        raise client.ClientError("Native client supports Debian bookworm/trixie only")
    if os.geteuid() != 0:
        raise client.ClientError("Native client setup requires root")
    if not can_manage_system_services(config.machine_type) or not can_manage_firewall(config.machine_type):
        raise client.ClientError("Native client needs service and firewall capabilities")
    if not os.path.isdir("/run/systemd/system"):
        raise client.ClientError("Running systemd required")
    for name in ("apt-get", "apt-cache", "dpkg-query", "dpkg", "systemctl", "curl", "iptables", "ip"):
        if not shutil.which(name):
            raise client.ClientError("Missing native client prerequisite: " + name)
    if client.native(["dpkg", "--print-architecture"]).strip() not in {"amd64", "arm64"}:
        raise client.ClientError("Only amd64/arm64 client packages are qualified for installation")
    descriptor = os.open("/dev/net/tun", os.O_RDWR | os.O_NOFOLLOW)
    try:
        if not stat.S_ISCHR(os.fstat(descriptor).st_mode):
            raise client.ClientError("Usable kernel TUN required")
    finally:
        os.close(descriptor)
    client.native(["iptables", "--wait", "5", "--list", "--numeric"])
    return codename


def _owned_file(path: str, content: str | bytes) -> None:
    check_directory_path(os.path.dirname(path))
    if os.path.lexists(path):
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise client.ClientError("Unsafe native client configuration file")
        old = Path(path).read_bytes()
        expected = content.encode() if isinstance(content, str) else content
        if old != expected:
            raise client.ClientError("Unowned native client configuration requires review")
    with directory_fd(os.path.dirname(path), create=True) as fd:
        if os.fstat(fd).st_uid != 0 or os.fstat(fd).st_mode & 0o022:
            raise client.ClientError("Unsafe native client configuration directory")
        if isinstance(content, str):
            write_text_atomic(path, content, mode=0o644)
        else:
            write_bytes_atomic(path, content, mode=0o644)


def install_client(codename: str) -> None:
    # Drop-in exists and manager is reloaded BEFORE apt's postinst can start
    # tailscaled. No policy-rc.d replacement or global service inhibition.
    _owned_file(DROPIN, PRIVACY)
    run(["systemctl", "daemon-reload"])
    with tempfile.TemporaryDirectory(prefix="basaltwater-tailscale-") as temporary:
        key = os.path.join(temporary, "key.gpg")
        run(["curl", "--fail", "--silent", "--show-error", "--proto", "=https",
             "--max-time", "30", "--max-filesize", "65536", "--output", key,
             f"https://pkgs.tailscale.com/stable/debian/{codename}.noarmor.gpg"], timeout=35)
        material = Path(key).read_bytes()
        if hashlib.sha256(material).hexdigest() != KEY_SHA256:
            raise client.ClientError("Official APT signing key changed; review upstream rotation")
        _owned_file(KEYRING, material)
    _owned_file(SOURCE, f"deb [signed-by={KEYRING}] https://pkgs.tailscale.com/stable/debian {codename} main\n")
    # Isolate source selection and enforce authentication even if host APT
    # defaults were weakened. Subsequent ordinary APT security updates remain on.
    options = ["-o", f"Dir::Etc::sourcelist={SOURCE}", "-o", "Dir::Etc::sourceparts=-",
               "-o", "APT::Get::AllowUnauthenticated=false", "-o", "Acquire::AllowInsecureRepositories=false"]
    run(["apt-get", *options, "update"], timeout=180)
    policy = client.native(["apt-cache", *options, "policy", "tailscale"])
    candidates = [line.strip().removeprefix("Candidate: ") for line in policy.splitlines()
                  if line.strip().startswith("Candidate: ")]
    if len(candidates) != 1:
        raise client.ClientError("Cannot establish authenticated client package candidate")
    candidate = candidates[0]
    client.version(candidate)
    run(["apt-get", *options, "install", "-y", "tailscale=" + candidate], timeout=300)
    if missing_packages(["tailscale"]):
        raise client.ClientError("Native client package installation not verified")
    installed = client._object(["tailscale", "version", "--json"])
    client.version(installed.get("short"))


def configure_tailscale(config: SetupConfig) -> None:
    """None is inert; enable installs/resumes; disable preserves native state."""
    if config.tailscale is None or getattr(config, "dry_run", False) or is_dry_run():
        return
    if config.tailscale is False:
        preflight(config)
        binding = client.read_binding()
        if binding is None:
            if not os.path.lexists(client.NATIVE_STATE) and not shutil.which("tailscale"):
                return
            raise client.ClientError("Unowned client requires explicit adoption")
        url = validate_headscale_url(config.tailscale_login_server or binding["url"])
        client.disable_client(url)
        return
    url = validate_headscale_url(config.tailscale_login_server)
    codename = preflight(config)
    client.reject_control_host(url)
    with client.client_lock():
        binding = client.read_binding()
        if binding is not None:
            client.owned(url)
        elif os.path.lexists(client.NATIVE_STATE) or shutil.which("tailscale"):
            raise client.ClientError("Existing native client requires explicit adoption")
        # A live identity must agree before package, service, or prefs changes.
        active = (client.native(["systemctl", "show", "--property=ActiveState", "--value", "tailscaled"]).strip()
                  if shutil.which("tailscale") else "inactive")
        if active == "active":
            status, prefs = client.snapshot()
            client.check_network(status, prefs, url, binding["phase"] if binding else "unowned")
            if status["BackendState"] in {"Running", "Stopped"}:
                client._reconcile(url)
        elif binding is not None and os.path.lexists(client.NATIVE_STATE):
            # Do not reconnect an opaque retained native profile before its
            # actual identity can be observed. Explicit recovery starts the
            # daemon under operator review, then reruns reconciliation.
            raise client.ClientError("Inactive retained native state requires reviewed daemon start and reconciliation")
        if binding is None:
            client.save_binding(url, "prepared")
        if not shutil.which("tailscale"):
            install_client(codename)
        else:
            # Existing daemons must already have the privacy setting; changing
            # it needs a reviewed restart because this may be a DNS node.
            if not Path(DROPIN).is_file() or Path(DROPIN).read_text() != PRIVACY:
                raise client.ClientError("Existing daemon privacy setting requires reviewed adoption")
        run(["systemctl", "enable", "--now", "tailscaled"])
        status, prefs = client.snapshot()
        current = client.owned(url)
        if current["phase"] == "left" and status["BackendState"] == "NeedsLogin":
            return
        client.check_network(status, prefs, url, current["phase"])
        if status["BackendState"] == "Running":
            client._reconcile(url)
            # Native set updates just DNS acceptance, preserving route settings.
            client.native(["tailscale", "set", "--accept-dns=true"])
        elif status["BackendState"] == "Stopped" and current["phase"] == "enrolled":
            client._reconcile(url)
            client.native(["tailscale", "up"], timeout=65)
            client.observe_client(url)
        # NeedsLogin/approval and interrupted joins wait for explicit enrollment
        # or reconciliation. Setup never consumes a key or prints an AuthURL.
