"""Bounded, read-only host observations shared by setup and the desktop doctor."""

from __future__ import annotations

import json
from pathlib import Path
import platform
import re
import shutil


def collect_host_health(probe, uid: int) -> list[tuple[str, str, str]]:
    observations = []

    def add(name, state, reason):
        observations.append((name, state, reason))

    for scope, options in (("system", []), ("user", ["--user"])):
        status, output = probe(["/usr/bin/systemctl", *options, "--failed", "--no-legend", "--no-pager"], uid)
        count = len(output.strip().splitlines()) if output.strip() else 0
        add(f"health.{scope}-units", "deferred" if status != "ok" else "failed" if count else "available",
            "Could not inspect failed units." if status != "ok" else f"{count} failed units; inspect systemctl locally.")
    # -Qu uses existing sync metadata. Never synchronize pacman's real database
    # or invoke checkupdates (which creates a temporary database) in a doctor.
    status, output = probe(["/usr/bin/pacman", "-Qu"], uid)
    count = len(output.strip().splitlines()) if output.strip() else 0
    add("health.updates", "deferred",
        f"{count} updates in local sync metadata; use the normal full CachyOS update workflow. Metadata may be stale."
        if status == "ok" else "Update freshness unknown; check through the normal CachyOS update workflow.")
    try:
        capacity = shutil.disk_usage("/")
        free = capacity.free // (1024 ** 3)
        add("health.capacity", "failed" if capacity.free < 5 * 1024 ** 3 else "available",
            f"Root filesystem has {free} GiB free.")
    except OSError:
        add("health.capacity", "deferred", "Root filesystem capacity unavailable.")
    add("health.firmware", "available" if Path("/sys/firmware/efi").is_dir() else "deferred",
        "Booted in UEFI mode; Secure Boot state is not verified." if Path("/sys/firmware/efi").is_dir()
        else "Legacy BIOS boot; Secure Boot is unavailable. This is not a boot failure.")
    release = platform.release()
    if re.fullmatch(r"[A-Za-z0-9._+-]{1,128}", release):
        current = Path("/usr/lib/modules") / release
        add("health.kernel", "available" if current.is_dir() else "deferred",
            "Running kernel modules are installed." if current.is_dir()
            else "Running kernel modules are absent; a full update may require a reboot.")
    status, output = probe(["/usr/bin/findmnt", "-n", "-o", "SOURCE", "/"], uid)
    # Never publish device or mount paths. Only a direct block-device root can
    # establish the absence of a dm-crypt layer; other layouts remain unknown.
    direct = status == "ok" and re.fullmatch(r"/dev/(?:nvme\d+n\d+p\d+|sd[a-z]+\d+)\s*", output)
    add("health.encryption", "deferred", "Root is on a direct block partition without a dm-crypt layer."
        if direct else "Root encryption not established by this observation.")
    return observations


def collect_network_health(probe, uid: int, port: int = 3773) -> list[tuple[str, str, str]]:
    status, output = probe(["/usr/bin/ss", "-H", "-lnt"], uid)
    results = []
    for name, ports in (("t3", {port}), ("sunshine", {47984, 47989, 47990, 48010})):
        exposed = False
        if status == "ok":
            for line in output.splitlines():
                fields = line.split()
                if len(fields) < 4:
                    continue
                host, _, number = fields[3].rpartition(":")
                if number.isdigit() and int(number) in ports and host not in ("127.0.0.1", "[::1]", "::1"):
                    exposed = True
        results.append((f"network.{name}", "deferred" if status != "ok" or exposed else "available",
                        "Non-loopback TCP listener observed; review trusted sources with sudo ufw status verbose. "
                        "Binding does not establish remote reachability or authentication."
                        if exposed else "No non-loopback listener observed on the checked ports."
                        if status == "ok" else "Listener state unavailable."))
    broad = False
    readable = True
    for path in (Path("/etc/ufw/user.rules"), Path("/etc/ufw/user6.rules")):
        try:
            content = path.read_text()
        except OSError:
            readable = False
            continue
        for line in content.splitlines():
            if (line.startswith("-A ufw") and " -j ACCEPT" in line
                    and not re.search(r"(?:^| )-s (?!0\.0\.0\.0/0 |::/0 )", line)
                    and re.search(r"--dports? (?:" + str(port) + r"|3389|47984(?::47990)?|47990|48010)(?: |$)", line)):
                broad = True
    results.append(("network.firewall", "deferred",
                    "Unrestricted saved UFW allow rules exist for remote-access ports; earlier deny rules may override them. "
                    "Verify effective rules with sudo ufw status verbose."
                    if broad else "Saved UFW rules inspected; effective firewall policy requires privileged verification."
                    if readable else "Saved firewall rules unreadable; effective firewall policy is unknown."))
    return results


def source_metadata(probe, uid: int) -> dict[str, str | None]:
    root = Path(__file__).resolve().parent.parent
    status, value = probe(["/usr/bin/git", "-C", str(root), "rev-parse", "HEAD"], uid)
    commit = value.strip() if status == "ok" and re.fullmatch(r"[0-9a-f]{40,64}\s*", value) else None
    channel = None
    try:
        value = json.loads((root / ".basaltwater/channel.json").read_text()).get("channel")
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9._/+\-]{1,128}", value):
            channel = value
    except (OSError, ValueError, AttributeError):
        pass
    return {"commit": commit, "channel": channel}
