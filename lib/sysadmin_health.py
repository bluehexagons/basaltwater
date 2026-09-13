"""Remote host health check."""

from __future__ import annotations

import sys
from typing import Optional

from lib.sysadmin_process import run_command
from lib.cache import load_setup_command
from lib.ssh_utils import build_ssh_command, ssh_batch_mode


# Remote shell script — runs as a single SSH invocation
_HEALTH_SCRIPT = r"""
echo "=== UPTIME ==="
uptime 2>/dev/null || echo "(unavailable)"

echo ""
echo "=== MEMORY ==="
free -h 2>/dev/null || echo "(unavailable)"

echo ""
echo "=== DISK ==="
df -h --output=source,size,used,avail,pcent,target 2>/dev/null || df -h 2>/dev/null || echo "(unavailable)"

echo ""
echo "=== FAILED UNITS ==="
systemctl list-units --state=failed --no-legend 2>/dev/null || echo "(systemctl unavailable)"

echo ""
echo "=== RECENT ERRORS ==="
journalctl -p err -n 10 --no-pager 2>/dev/null || echo "(journalctl unavailable)"

echo ""
echo "=== APT UPGRADES ==="
apt-get -qq --just-print upgrade 2>/dev/null | grep -c '^Inst' || echo "0"

echo ""
echo "=== REBOOT REQUIRED ==="
if [ -f /run/reboot-required ]; then
    cat /run/reboot-required
    if [ -f /run/reboot-required.pkgs ]; then
        echo "Packages:"
        cat /run/reboot-required.pkgs
    fi
else
    echo "No reboot required."
fi
"""

_DISK_WARN_THRESHOLD = 85


def _resolve_host_credentials(
    host: str,
    username: Optional[str],
    ssh_key: Optional[str],
) -> tuple[str, Optional[str]]:
    config = load_setup_command(host)
    if config:
        if not username:
            username = config.username
        if not ssh_key:
            ssh_key = config.ssh_key
    return username or "root", ssh_key


def _highlight_disk_line(line: str) -> str:
    """Prefix disk lines over the warning threshold with a marker."""
    parts = line.split()
    for part in parts:
        if part.endswith("%"):
            try:
                pct = int(part.rstrip("%"))
                if pct >= _DISK_WARN_THRESHOLD:
                    return f"  [!] {line}"
            except ValueError:
                pass
    return f"      {line}"


def _format_output(raw: str, host: str) -> None:
    """Print health output with light formatting."""
    print(f"\n{'='*60}")
    print(f"  Health: {host}")
    print(f"{'='*60}")

    in_disk = False
    for line in raw.splitlines():
        if line.startswith("=== DISK ==="):
            in_disk = True
            print(f"\n{line}")
            continue
        if line.startswith("==="):
            in_disk = False
            print(f"\n{line}")
            continue
        if in_disk and line and not line.startswith("Filesystem"):
            print(_highlight_disk_line(line))
        else:
            print(f"  {line}" if line else "")


def run_health(
    host: str,
    username: Optional[str] = None,
    ssh_key: Optional[str] = None,
) -> int:
    username, ssh_key = _resolve_host_credentials(host, username, ssh_key)

    cmd = build_ssh_command(
        host,
        username,
        ssh_key,
        batch_mode=ssh_batch_mode(),
        remote_command=_HEALTH_SCRIPT,
    )

    result = run_command(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        print(f"Error connecting to {host}:", file=sys.stderr)
        if result.stderr:
            print(result.stderr.strip(), file=sys.stderr)
        return result.returncode

    _format_output(result.stdout, host)
    return 0
