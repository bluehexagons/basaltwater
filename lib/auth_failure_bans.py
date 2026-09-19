"""Fail2ban integration for privacy-preserving Nginx authentication logs."""

from __future__ import annotations

import os
import re

from lib.atomic_io import write_text_atomic
from lib.remote_utils import install_package, run
from lib.validation import validate_filesystem_path


FAIL2BAN_FILTER_PATH = "/etc/fail2ban/filter.d/basaltwater-nginx-auth.conf"
FAIL2BAN_JAIL_DIR = "/etc/fail2ban/jail.d"
_JAIL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def configure_nginx_auth_failure_ban(jail_name: str, log_path: str) -> None:
    """Ban clients that repeatedly reach an Nginx-managed auth failure log."""

    if not _JAIL_NAME_RE.fullmatch(jail_name):
        raise ValueError(f"Invalid authentication jail name: {jail_name}")
    validate_filesystem_path(log_path, must_exist=False)
    if os.path.dirname(log_path) != "/var/log/nginx":
        raise ValueError("Authentication failure logs must be under /var/log/nginx")
    if not install_package(
        "Fail2ban",
        "fail2ban",
        "apt-get install -y -qq fail2ban",
    ):
        raise RuntimeError("Fail2ban is required for authentication failure bans")

    write_text_atomic(
        FAIL2BAN_FILTER_PATH,
        r"""# Managed by basaltwater
[Definition]
datepattern = {NONE}
failregex = ^<HOST> \[[^]]+\] basaltwater-auth-failure$
ignoreregex =
""",
        mode=0o644,
    )
    jail_path = os.path.join(FAIL2BAN_JAIL_DIR, f"basaltwater-{jail_name}.local")
    write_text_atomic(
        jail_path,
        f"""# Managed by basaltwater
[basaltwater-{jail_name}]
enabled = true
filter = basaltwater-nginx-auth
logpath = {log_path}
maxretry = 5
findtime = 10m
bantime = 1h
""",
        mode=0o644,
    )
    run("systemctl enable fail2ban")
    restart = run("systemctl restart fail2ban", check=False, capture_output=True)
    if restart.returncode != 0:
        detail = (restart.stderr or restart.stdout or "").strip()
        raise RuntimeError(
            "Fail2ban rejected the authentication protection configuration"
            + (f": {detail}" if detail else "")
        )
    print(f"  ✓ Fail2ban protects {jail_name} authentication (5 failures per 10 minutes)")


def remove_nginx_auth_failure_ban(jail_name: str) -> None:
    """Remove one managed jail and reload Fail2ban when it was installed."""

    if not _JAIL_NAME_RE.fullmatch(jail_name):
        raise ValueError(f"Invalid authentication jail name: {jail_name}")
    jail_path = os.path.join(FAIL2BAN_JAIL_DIR, f"basaltwater-{jail_name}.local")
    try:
        os.remove(jail_path)
    except FileNotFoundError:
        return
    reload_result = run(
        "fail2ban-client reload",
        check=False,
        capture_output=True,
    )
    if reload_result.returncode != 0:
        raise RuntimeError(f"Could not remove the {jail_name} authentication jail")


__all__ = [
    "configure_nginx_auth_failure_ban",
    "remove_nginx_auth_failure_ban",
]
