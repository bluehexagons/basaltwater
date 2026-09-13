"""Installation and migration guards for the shared desktop runtime."""

from __future__ import annotations

import json
from pathlib import Path
import pwd
import re
import shlex
import time

from desktop.session_runtime import CONFIG_PATH, SESSION_COMMANDS
from lib.atomic_io import write_text_atomic
from lib.config import SetupConfig
from lib.machine_state import can_manage_system_services
from lib.remote_utils import install_package, is_dry_run, run
from lib.validation import validate_filesystem_path
from lib.validators import validate_username

STARTWM = "/etc/xrdp/infra-tools-startwm.sh"
DISPLAY_MANAGERS = ("gdm3", "gdm", "lightdm", "sddm", "lxdm", "xdm")
DISPLAY_MANAGER_ALIAS = Path("/etc/systemd/system/display-manager.service")
SESMAN_VENDOR_UNIT = Path("/usr/lib/systemd/system/xrdp-sesman.service")
SESMAN_UNIT = Path("/etc/systemd/system/xrdp-sesman.service")
SESMAN_MARKER = "# Managed by infra-tools shared desktop setup\n"
HANDOFF_LAUNCHER = "/usr/local/bin/infra-tools-desktop-control"
HANDOFF_ENTRY = "/usr/share/applications/infra-tools-desktop-control.desktop"


def configure_session_service() -> None:
    """Copy the vendor unit to remove its frontend lifetime dependency.

    systemd dependencies cannot be cleared by an empty drop-in assignment.
    Refresh the complete override from the packaged unit on every setup.
    """
    if SESMAN_UNIT.is_symlink() or (SESMAN_UNIT.exists()
            and not SESMAN_UNIT.read_text().startswith(SESMAN_MARKER)):
        raise RuntimeError("Custom xrdp-sesman unit requires administrator migration")
    source = SESMAN_VENDOR_UNIT.read_text()
    # Only transform the Unit section; preserve package-specific service details.
    section = re.search(r"(?ms)^\[Unit\]\s*\n(.*?)(?=^\[|\Z)", source)
    if section is None:
        raise RuntimeError("Packaged xrdp-sesman unit has no Unit section")
    lines = ["StopWhenUnneeded=false\n"]
    for line in section[1].splitlines(keepends=True):
        key, separator, value = line.partition("=")
        if separator and key.strip() == "StopWhenUnneeded":
            continue
        if separator and key.strip() == "BindsTo":
            if "\\" in value:
                raise RuntimeError("Unsupported continued sesman dependency; migrate the unit first")
            dependencies = [unit for unit in value.split() if unit != "xrdp.service"]
            if dependencies:
                lines.append("BindsTo=" + " ".join(dependencies) + "\n")
            continue
        lines.append(line)
    content = SESMAN_MARKER + source[:section.start(1)] + "".join(lines) + source[section.end(1):]
    write_text_atomic(str(SESMAN_UNIT), content, mode=0o644)
    legacy = SESMAN_UNIT.parent / "xrdp-sesman.service.d/shared-desktop.conf"
    legacy.unlink(missing_ok=True)
    run(["systemctl", "daemon-reload"])
    effective = run(["systemctl", "show", "xrdp-sesman.service", "--value",
                     "-p", "BindsTo", "-p", "Requires", "-p", "PartOf"], capture_output=True)
    if "xrdp.service" in effective.stdout.split():
        raise RuntimeError("A sesman override still ties desktop lifetime to XRDP; migrate its dependencies")


def assert_desktop_idle(config: SetupConfig) -> None:
    """Log out the managed desktop, then require all graphical sessions idle."""
    if config.desktop not in SESSION_COMMANDS:
        raise ValueError("Unsupported shared desktop environment")
    if not validate_username(config.username) or config.username == "root":
        raise ValueError("The desktop owner must be a valid non-root account")
    if not can_manage_system_services(config.machine_type):
        raise ValueError("Shared desktops require persistent systemd services")
    if config.harden_user:
        raise ValueError("Shared desktops require user services and cannot use --harden-user")
    if is_dry_run():
        print("  [DRY-RUN] Would log out the managed desktop and verify graphical sessions are stopped")
        return
    logged_out = logout_managed_desktop(config)
    deadline = time.monotonic() + 10
    while True:
        try:
            _assert_no_graphical_sessions()
            return
        except RuntimeError:
            if not logged_out or time.monotonic() >= deadline:
                raise
            time.sleep(0.25)  # sesman/logind may briefly outlive the control socket.


def logout_managed_desktop(config: SetupConfig) -> bool:
    """Run the owner-authenticated logout client from the staged setup source."""
    if not CONFIG_PATH.exists():
        return False
    helper = Path(__file__).with_name("setup_logout.py").resolve()
    validate_filesystem_path(str(helper), must_exist=True)
    print("  Logging out the shared desktop before setup (waiting for normal logout)...", flush=True)
    result = run(["runuser", "-u", config.username, "--", "/usr/bin/python3", str(helper)],
                 capture_output=True, check=False, timeout=240)
    if result.returncode:
        raise RuntimeError(f"Desktop setup logout failed: {result.stderr.strip()}")
    return json.loads(result.stdout)["logged_out"] is True


def _assert_no_graphical_sessions() -> None:
    sessions = run(["loginctl", "list-sessions", "--no-legend"], capture_output=True)
    for line in sessions.stdout.splitlines():
        if not line.strip():
            continue
        session_id = line.split()[0]
        details = run(["loginctl", "show-session", session_id, "-p", "Type", "-p", "Class"],
                      capture_output=True, check=False)
        if details.returncode != 0:
            # Logout can remove a session between enumeration and inspection.
            # Confirm its absence instead of matching localized loginctl errors
            # or letting run() record an expected race in the final setup notes.
            remaining = run(["loginctl", "list-sessions", "--no-legend"], capture_output=True)
            if session_id not in {row.split()[0] for row in remaining.stdout.splitlines() if row.strip()}:
                continue
            raise RuntimeError(
                f"Could not inspect login session {session_id} before desktop setup: "
                f"{details.stderr.strip()}"
            )
        properties = dict(line.split("=", 1) for line in details.stdout.splitlines() if "=" in line)
        if properties.get("Type") in {"x11", "wayland"} and properties.get("Class") != "greeter":
            raise RuntimeError("Desktop setup deferred: log out existing graphical sessions and rerun; applications were not stopped")
    result = run(["pgrep", "-x", "xrdp-sesexec"], check=False, capture_output=True)
    if result.returncode == 0:
        raise RuntimeError("Desktop setup deferred: log out existing XRDP sessions and rerun")
    if result.returncode != 1:
        raise RuntimeError("Could not inspect XRDP sessions before desktop setup")


def prepare_shared_desktop(config: SetupConfig) -> None:
    """Retire console display managers so all managed GUI logins use sesman."""
    assert_desktop_idle(config)
    if is_dry_run():
        print("  [DRY-RUN] Would disable console graphical login; desktop access uses XRDP")
        return
    alias = DISPLAY_MANAGER_ALIAS
    masked = alias.is_symlink() and alias.resolve() == Path("/dev/null")
    if alias.exists() and not masked and alias.resolve().stem not in DISPLAY_MANAGERS:
        raise RuntimeError("Unrecognized console display manager; migrate its configuration before shared desktop setup")
    # Preserve the former alias for explicit administrator rollback.
    backup = alias.with_suffix(".service.infra-tools-backup")
    if alias.is_symlink() and not masked and not backup.is_symlink() and not backup.exists():
        backup.symlink_to(alias.readlink())
    if alias.is_symlink() and not masked:
        alias.unlink()
    run(["systemctl", "mask", "--now", "display-manager.service",
         *(f"{name}.service" for name in DISPLAY_MANAGERS)])
    print("  Console graphical login disabled; use the shared XRDP desktop")


def install_session_runtime(config: SetupConfig) -> None:
    """Install a root-owned startup path and the single authorized account."""
    if config.desktop not in SESSION_COMMANDS:
        raise ValueError("Unsupported shared desktop environment")
    if is_dry_run():
        print("  [DRY-RUN] Would install single-owner desktop runtime and local control tools")
        return
    assert_desktop_idle(config)
    account = pwd.getpwnam(config.username)
    if account.pw_uid == 0:
        raise ValueError("The desktop cannot run as root")
    for package in ("wmctrl", "python3-tk", "python3-gi", "gir1.2-atspi-2.0", "at-spi2-core"):
        if not install_package(package, package, ["apt-get", "install", "-y", "-qq", "--no-install-recommends", package]):
            raise RuntimeError(f"Desktop productivity tools require {package}")
    configure_session_service()
    source = str(Path(__file__).resolve().parents[1])
    validate_filesystem_path(source, must_exist=True)
    content = json.dumps({"version": 1, "username": config.username, "desktop": config.desktop}, indent=2) + "\n"
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if CONFIG_PATH.parent.is_symlink() or CONFIG_PATH.is_symlink():
        raise RuntimeError("Refusing symlinked desktop configuration")
    if CONFIG_PATH.exists():
        previous = CONFIG_PATH.read_text()
        old = json.loads(previous)
        if not isinstance(old, dict) or old.get("version") != 1:
            raise RuntimeError("Unrecognized desktop configuration; refusing to replace it")
        backup = CONFIG_PATH.with_suffix(".json.bak")
        if not backup.exists():
            write_text_atomic(str(backup), previous, mode=0o600)
    write_text_atomic(str(CONFIG_PATH), content, mode=0o644)
    run(["groupadd", "-f", "infra-desktop"])
    run(["gpasswd", "-M", config.username, "infra-desktop"])
    desktop_names = {"xfce": "XFCE", "i3": "i3", "cinnamon": "X-Cinnamon", "lxqt": "LXQt"}
    desktop_environment = (
        f"export XDG_CURRENT_DESKTOP={shlex.quote(desktop_names[config.desktop])}\n"
        f"export XDG_SESSION_DESKTOP={shlex.quote(config.desktop)}\n"
        f"export DESKTOP_SESSION={shlex.quote(config.desktop)}\n"
    )
    if config.desktop == "xfce":
        desktop_environment += "export XDG_MENU_PREFIX=xfce-\n"
    script = (
        "#!/bin/sh\n# Managed by infra_tools shared desktop setup\n"
        "umask 077\nexport XRDP_SESSION=1\n"
        'export PATH="$HOME/.local/bin:$PATH"\n'
        'export XDG_SESSION_TYPE=x11\n'
        f"{desktop_environment}"
        "unset DBUS_SESSION_BUS_ADDRESS SESSION_MANAGER\n"
        f"cd {shlex.quote(source)} || exit 1\n"
        "exec dbus-run-session -- /usr/bin/python3 -m desktop.session_runtime\n"
    )
    write_text_atomic(STARTWM, script, mode=0o755)
    write_text_atomic(HANDOFF_LAUNCHER,
                      "#!/bin/sh\nexec /usr/bin/python3 " + shlex.quote(source + "/desktop/handoff.py") + "\n", mode=0o755)
    write_text_atomic(HANDOFF_ENTRY,
                      "[Desktop Entry]\nType=Application\nName=Shared Desktop Control\n"
                      "Comment=Pause or resume agent input in the shared desktop\n"
                      f"Exec={HANDOFF_LAUNCHER}\nIcon=input-mouse\nTerminal=false\nCategories=System;\n", mode=0o644)
    run(["loginctl", "enable-linger", config.username])
    run(["systemctl", "start", f"user@{account.pw_uid}.service"])
    from common.agent_steps import install_agent_cli_launcher, install_managed_agent_skills

    install_agent_cli_launcher(config)
    install_managed_agent_skills(config.username, config.selected_agent_tools(), ("infra-tools-desktop",))
