"""Read-only CachyOS qualification prerequisites; never activate desktop control."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import pwd
import re
import stat
import subprocess
import shutil

from lib.cachyos import is_cachyos
from lib.streamed_process import run_streamed
from lib.validation import validate_package_name
from lib.validators import validate_username


SCHEMA_VERSION = 1
PROBE_TIMEOUT = 3.0
OUTPUT_LIMIT = 16384
BROWSER_PACKAGES = ("chromium", "firefox", "brave-bin", "cachy-browser", "librewolf-bin")
PACKAGES = (
    "plasma-workspace", "kwin", "wayland", "pipewire", "wireplumber",
    "xdg-desktop-portal", "xdg-desktop-portal-kde", "at-spi2-core",
    "python-gobject", "t3code-bin", *BROWSER_PACKAGES,
    "gstreamer", "gst-plugins-base", "gst-plugin-pipewire", "gtk3", "libxkbcommon",
)
_NAME = re.compile(r"[a-z][a-z0-9_.-]{0,63}", re.ASCII)
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9.:+_~\-]{0,127}", re.ASCII)


@dataclass(frozen=True)
class CapabilityResult:
    """Versioned metadata contract, independent of package/setup success.

    None means unknown, not false. An observation is not live qualification.
    Consumers must reject unknown schemas rather than guessing field meaning.
    """

    name: str
    state: str
    reason: str
    owner: str
    session: str
    observed_at: str
    selected: bool | None = None
    origin: str = "user-session"
    interactive_required: bool | None = None
    sensitivity: str = "metadata"
    last_verified: str | None = None
    version: str | None = None
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != SCHEMA_VERSION:
            raise ValueError("Unsupported CachyOS capability schema")
        if not isinstance(self.name, str) or not _NAME.fullmatch(self.name):
            raise ValueError("Invalid capability name")
        if self.state not in ("available", "deferred", "failed"):
            raise ValueError("Invalid capability state")
        if not isinstance(self.owner, str) or not validate_username(self.owner):
            raise ValueError("Invalid capability owner")
        if self.session not in ("wayland", "x11", "unknown", "absent"):
            raise ValueError("Invalid session type")
        if self.origin not in ("user-session", "portal", "t3-preview"):
            raise ValueError("Invalid capability origin")
        if self.sensitivity != "metadata":
            raise ValueError("Doctor records may contain metadata only")
        if (not isinstance(self.reason, str) or not 1 <= len(self.reason) <= 512
                or any(ord(char) < 32 or ord(char) == 127 for char in self.reason)):
            raise ValueError("Invalid capability reason")
        for value in (self.selected, self.interactive_required):
            if value is not None and type(value) is not bool:
                raise ValueError("Expected a boolean or unknown capability field")
        for index, value in enumerate((self.observed_at, self.last_verified)):
            if index == 1 and value is None:
                continue
            if not isinstance(value, str) or len(value) > 40:
                raise ValueError("Invalid capability timestamp")
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError as exc:
                raise ValueError("Invalid capability timestamp") from exc
            if parsed.tzinfo is None:
                raise ValueError("Capability timestamps require a timezone")
        if self.version is not None and (
            not isinstance(self.version, str) or not _VERSION.fullmatch(self.version)
        ):
            raise ValueError("Invalid package version")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> CapabilityResult:
        """Require a complete record; reject additional fields and older schemas."""
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise ValueError("Invalid capability record fields")
        return cls(**value)


class _OutputLimitError(RuntimeError):
    pass


def _probe(command: list[str], uid: int) -> tuple[str, str]:
    """Bound elapsed time and memory; never include raw errors in reports."""
    output: list[str] = []
    size = 0

    def collect(chunk: str) -> None:
        nonlocal size
        size += len(chunk.encode("utf-8"))
        if size > OUTPUT_LIMIT:
            raise _OutputLimitError
        output.append(chunk)

    # Do not inherit proxies, remote bus addresses, loaders, or personal PATH.
    runtime = f"/run/user/{uid}"
    environment = {
        "PATH": "/usr/bin:/bin", "LC_ALL": "C", "SYSTEMD_PAGER": "cat",
        "SYSTEMD_COLORS": "0", "XDG_RUNTIME_DIR": runtime,
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime}/bus",
    }
    try:
        code = run_streamed(command, timeout=PROBE_TIMEOUT, on_output=collect,
                            env=environment, cwd="/")
    except FileNotFoundError:
        return "missing", ""
    except _OutputLimitError:
        return "output-limit", ""
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return "error", ""
    return ("ok" if code == 0 else "error"), "".join(output) if code == 0 else ""


def _owned_socket(path: Path, uid: int) -> bool:
    try:
        info = path.lstat()
        parent = path.parent.lstat()
        return (stat.S_ISSOCK(info.st_mode) and info.st_uid == uid
                and stat.S_ISDIR(parent.st_mode) and parent.st_uid == uid
                and not parent.st_mode & 0o022)
    except OSError:
        return False


def saved_selection():
    from lib.cachyos_refresh import load_saved_setup

    try:
        return load_saved_setup()[1]
    except (OSError, KeyError, ValueError):
        return None


def collect_cachyos_doctor(*, config=None) -> dict[str, object]:
    """Observe this account only, without reading UI contents or starting services."""
    uid = os.getuid()
    owner = pwd.getpwuid(uid).pw_name
    observed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    session = os.environ.get("XDG_SESSION_TYPE", "absent")
    if session not in ("wayland", "x11", "absent"):
        session = "unknown"
    records: list[CapabilityResult] = []

    def record(name: str, state: str, reason: str, **kwargs: object) -> None:
        records.append(CapabilityResult(name, state, reason, owner, session,
                                        observed_at, **kwargs))

    supported = is_cachyos() and platform.machine() == "x86_64"
    if not supported or uid == 0 or os.geteuid() != uid:
        record("host", "deferred",
               "Run as the existing desktop user on CachyOS x86_64; no desktop probes ran.")
    else:
        config = config if config is not None else saved_selection()
        selected_packages = {"t3code-bin": config.t3code_desktop} if config is not None else {}
        selected_packages.update({package: False for package in BROWSER_PACKAGES})
        if config is not None:
            from common.cachyos_steps import CACHYOS_DESKTOP_PACKAGES, CACHYOS_SYSADMIN_PACKAGES
            from common.cachyos_software import selected_software

            for command, packages in selected_software(config):
                version = None
                for package in packages:
                    validate_package_name(package)
                    status, output = _probe(["/usr/bin/pacman", "-Q", "--", package], uid)
                    parts = output.split()
                    if status == "ok" and len(parts) == 2 and parts[0] == package and _VERSION.fullmatch(parts[1]):
                        version = parts[1]
                        break
                record("software." + command, "available" if version else "failed",
                       "Selected package installed; application and publishing behavior not tested."
                       if version else "Selected package missing or query inconclusive; inspect its original package manager.",
                       selected=True, version=version)

            for field, _command, package in CACHYOS_DESKTOP_PACKAGES:
                if getattr(config, field):
                    selected_packages[package] = True
            if config.install_sysadmin_tools:
                selected_packages.update({package: True for _, package in CACHYOS_SYSADMIN_PACKAGES})
            for enabled, packages in (
                (config.install_sunshine, ("sunshine",)),
                (config.install_moonlight, ("moonlight-qt",)),
                (config.install_gaming, ("cachyos-gaming-meta", "cachyos-gaming-applications")),
                (config.install_blender, ("libdecor",)),
                (config.install_steamcmd, ("lib32-glibc", "lib32-gcc-libs")),
            ):
                if enabled:
                    selected_packages.update({package: True for package in packages})
            commands = config.selected_agent_tools() + ["git", "rg"]
            if config.install_node:
                commands += ["node", "npm", "pnpm"]
            if config.install_python:
                commands += ["python", "uv"]
            if config.install_git_lfs:
                commands.append("git-lfs")
            if config.install_go:
                commands.append("go")
            if config.install_godot:
                commands.append("godot")
            for command in commands:
                executable = shutil.which(command)
                status, output = _probe([executable, "version" if command == "go" else "--version"], uid) if executable else ("missing", "")
                match = re.search(r"(?<![0-9.])\d+\.\d+(?:\.\d+)?[a-zA-Z0-9.+_-]*", output[:512])
                version = match[0] if match and _VERSION.fullmatch(match[0]) else None
                record("tool." + command, "available" if status == "ok" else "failed",
                       "Selected executable passed its version check." if status == "ok"
                       else "Selected executable missing or version check failed; rerun setup or inspect its original manager.",
                       selected=True, version=version)
        record("host", "available", "CachyOS x86_64 detected; live qualification is recorded separately.")
        runtime = Path(f"/run/user/{uid}")
        bus_ready = _owned_socket(runtime / "bus", uid)
        record("session.bus", "available" if bus_ready else "deferred",
               "Owned user bus socket exists; service health is checked separately." if bus_ready else
               "User bus socket unavailable or unsafe; log into KDE as this user.")
        display = os.environ.get("WAYLAND_DISPLAY", "")
        # Accept only a socket in the canonical user runtime directory. Never
        # inspect a path supplied by another session or an arbitrary environment.
        display_valid = bool(re.fullmatch(r"wayland-[0-9]{1,6}", display))
        wayland_ready = session == "wayland" and display_valid and _owned_socket(runtime / display, uid)
        record("session.wayland", "available" if wayland_ready else "deferred",
               "Owned Wayland socket exists; active compositor and rendering are not verified." if wayland_ready else
               "Run from a KDE Wayland terminal with its owned Wayland socket.")

        installed_browsers = []
        for package in dict.fromkeys((*PACKAGES, *selected_packages)):
            validate_package_name(package)
            status, output = _probe(["/usr/bin/pacman", "-Q", "--", package], uid)
            parts = output.split()
            version = (parts[1] if status == "ok" and len(parts) == 2
                       and parts[0] == package and _VERSION.fullmatch(parts[1]) else None)
            selected = selected_packages.get(package)
            record(f"package.{package}", "available" if version else "failed" if selected else "deferred",
                   "Native package installed; this does not verify application behavior." if version else
                   "Not selected by setup; package absent or query inconclusive." if selected is False else
                   "Selected package missing or query inconclusive; inspect pacman locally." if selected else
                   "Package unavailable or query inconclusive; inspect pacman locally if needed.",
                   version=version, selected=selected)
            if version and package in BROWSER_PACKAGES:
                installed_browsers.append(package)

        record("browser.native", "available" if installed_browsers else "deferred",
               "Native browser package detected; launch, default selection, and automation are not verified."
               if installed_browsers else
               "No recognized native browser package observed; custom installations may still be usable.")

        for name, bus_name in (
            ("session.portal", "org.freedesktop.portal.Desktop"),
            ("session.accessibility", "org.a11y.Bus"),
            ("session.kwin", "org.kde.KWin"),
        ):
            status, output = ("missing", "")
            if bus_ready:
                status, output = _probe([
                    "/usr/bin/busctl", "--user", "--timeout=2", "call",
                    "org.freedesktop.DBus", "/org/freedesktop/DBus",
                    "org.freedesktop.DBus", "NameHasOwner", "s", bus_name,
                ], uid)
            active = status == "ok" and output.strip() == "b true"
            record(name, "available" if active else "deferred",
                   "Bus name already owned; interfaces and permissions are not verified." if active else
                   "Bus owner not observed; log into KDE and inspect the service locally. Doctor will not activate it.")

        for name, unit in (
            ("session.systemd", "default.target"),
            ("session.pipewire", "pipewire.service"),
            ("session.wireplumber", "wireplumber.service"),
            ("service.t3code", "basaltwater-cachyos-t3.service"),
            ("service.t3code-upstream", "t3code.service"),
        ):
            status, output = ("missing", "")
            if bus_ready:
                status, output = _probe([
                    "/usr/bin/systemctl", "--user", "--no-pager", "is-active", unit,
                ], uid)
            active = status == "ok" and output.strip() == "active"
            selected = bool(config.web_interfaces) if config is not None and name == "service.t3code" else None
            record(name, "available" if active else "failed" if selected else "deferred",
                   "User unit active; functional readiness is not verified." if active else
                   ("Selected web service inactive; inspect systemctl --user status basaltwater-cachyos-t3."
                    if selected else "Managed web service inactive; expected for --t3code-desktop. Desktop health is checked in the app."
                    if config is not None and config.t3code_desktop and name == "service.t3code" else
                    "User unit not observed active; inspect with systemctl --user. Selection may be unknown."), selected=selected)

        from lib.cachyos_health import collect_host_health, collect_network_health

        for name, state, reason in collect_host_health(_probe, uid) + collect_network_health(
            _probe, uid, config.web_interface_port if config is not None else 3773,
        ):
            record(name, state, reason)

    record("browser.playwright", "deferred", "Managed Playwright is not installed on CachyOS.", selected=False)
    for name, origin in (("desktop.accessibility", "user-session"),
                         ("desktop.portal", "portal")):
        record(name, "deferred", "Task-scoped automation requires basaltw desktop --native doctor/start from KDE and portal consent; this inventory does not start or verify it.",
               origin=origin)
    return {"schema_version": SCHEMA_VERSION, "observed_at": observed_at,
            "capabilities": [record.to_dict() for record in records]}


def run_cachyos_doctor(args: argparse.Namespace) -> int:
    """Emit shareable metadata. Deferred prerequisites are not setup failures."""
    try:
        report = collect_cachyos_doctor()
    except (OSError, KeyError, ValueError):
        print("Error: could not resolve the invoking desktop account or diagnostic contract.")
        return 1
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for item in report["capabilities"]:
            print(f"{item['name']}: {item['state']} — {item['reason']}")
    return 0
