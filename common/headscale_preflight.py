"""Read-only target admission before an optional network setup plan mutates."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat

from lib.apt_sources import get_debian_codename
from lib.config import SetupConfig
from lib.headscale_config import validate_headscale_settings
from lib.machine_state import can_manage_firewall, can_manage_system_services
from lib.remote_utils import get_os_id, is_dry_run, run


def validate_ingress(config: SetupConfig) -> None:
    """A public control endpoint cannot also obey a closed shared web port."""
    if not config.headscale:
        return
    if config.enable_cloudflare and not config.cloudflare_allow_direct:
        raise ValueError(
            "Headscale requires direct HTTPS ingress; Cloudflare's shared-port "
            "closure conflicts. Select --cloudflare-allow-direct or use a separate host."
        )
    if config.effective_access_sources() and {80, 443}.intersection(config.effective_web_ports()):
        raise ValueError(
            "Headscale direct ingress conflicts with source-restricted shared "
            "web ports; use a separate host or a compatible ingress policy."
        )


def preflight_network_services(config: SetupConfig) -> None:
    """Reject unavailable native capabilities before the profile's first step."""
    validate_headscale_settings(config, complete=True)
    validate_ingress(config)
    if config.dry_run or is_dry_run():
        return
    if (get_os_id() != "debian" or get_debian_codename() not in {"bookworm", "trixie"}
            or os.geteuid() != 0 or not shutil.which("apt-get")
            or not can_manage_system_services(config.machine_type)
            or not can_manage_firewall(config.machine_type)
            or not os.path.isdir("/run/systemd/system")):
        raise ValueError("Managed network services require Debian bookworm/trixie, root, APT, systemd and firewall capability")
    result = run(["dpkg", "--print-architecture"], capture_output=True, timeout=15)
    if result.stdout.strip() not in {"amd64", "arm64"}:
        raise ValueError("Managed network services support amd64 and arm64")
    # This checks actual process capabilities as well as the declared machine
    # profile. A privileged-looking container must not begin package changes.
    fields = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
    if not int(fields.get("CapEff", "0").strip(), 16) & (1 << 12):
        raise ValueError("Managed network services require effective CAP_NET_ADMIN")
    if config.tailscale or config.unbound:
        descriptor = os.open("/dev/net/tun", os.O_RDWR | os.O_NOFOLLOW)
        try:
            if not stat.S_ISCHR(os.fstat(descriptor).st_mode):
                raise ValueError("Native client requires a usable kernel TUN device")
        finally:
            os.close(descriptor)
