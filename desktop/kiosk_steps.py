"""Install an optional local browser without the shared XRDP desktop flow."""

from __future__ import annotations

import hashlib
import grp
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import stat
from typing import TYPE_CHECKING

from desktop.kiosk_runtime import ACCOUNT_COMMENT as COMMENT, HOME, MANAGER, USER, cleanup_sessions
from lib.atomic_io import remove_file_durable, write_text_atomic
from lib.kiosk_config import validate_kiosk_settings, validate_kiosk_url
from lib.machine_state import can_manage_system_services, resolve_machine_type
from lib.maintenance_defaults import APT_DPKG_OPTIONS, APT_LOCK_OPTIONS
from lib.remote_utils import is_dry_run, read_os_release, run
from lib.setup_directories import check_directory_path, directory_fd
from lib.unit_transaction import inspect_unit_state, remove_units, replace_units, snapshot_unit_file
from lib.validation import validate_filesystem_path, validate_package_name

if TYPE_CHECKING:
    from lib.config import SetupConfig


SERVICE = "basaltwater-kiosk.service"
UNIT_DIR = Path("/etc/systemd/system")
CONFIG_DIR = Path("/etc/basaltwater/kiosk")
PRESET = Path("/etc/systemd/system-preset/00-basaltwater-kiosk.preset")
VENDOR_MASK = Path("/run/systemd/system/greetd.service")
DISPLAY_MANAGER = UNIT_DIR / "display-manager.service"
DRM_DIR = Path("/dev/dri")
INPUT_DIR = Path("/dev/input")
VT = Path("/dev/tty7")
MARKER = "# Managed by basaltwater local kiosk\n"
PACKAGES = ("greetd", "cage", "firefox-esr", "dbus-user-session", "libpam-systemd",
            "libgl1-mesa-dri", "fonts-dejavu-core")
GENERATED_NAME = re.compile(r"session-[a-f0-9]{64}\.(?:json|toml)\Z")


def kiosk_url(config: SetupConfig) -> str:
    """Prefer the configured host panel, then the native Proxmox interface."""
    if config.kiosk_url is not None:
        return validate_kiosk_url(config.kiosk_url)
    from common.web_panel_steps import web_panel_url

    url = None if config.disable_web_panel else web_panel_url(config)
    if url is None:
        host = config.host
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        url = f"https://{host}:8006/"
    return validate_kiosk_url(url)


def _owned(path: Path) -> dict | None:
    value = snapshot_unit_file(str(path))
    if value is not None and (value["uid"] != 0 or value["mode"] & 0o022
                              or not value["content"].startswith(MARKER)):
        raise RuntimeError(f"Unmanaged or unsafe kiosk file: {path}")
    return value


def _owned_service() -> dict | None:
    previous = _owned(UNIT_DIR / SERVICE)
    if previous is None:
        state = inspect_unit_state(SERVICE)
        if (state["LoadState"] != "not-found" or state["ActiveState"] != "inactive"
                or state["UnitFileState"] in {"enabled", "enabled-runtime"}):
            raise RuntimeError("Kiosk service is loaded, active or enabled without its managed file; "
                               "restore the unit or stop and disable it, then daemon-reload "
                               "before configuring kiosk")
    return previous


def _configuration_files() -> list[Path]:
    if not CONFIG_DIR.exists():
        return []
    files = list(CONFIG_DIR.iterdir())
    for path in files:
        if not GENERATED_NAME.fullmatch(path.name):
            raise RuntimeError(f"Unrecognized kiosk configuration: {path}")
        value = snapshot_unit_file(str(path))
        if value is None or value["uid"] != 0 or value["mode"] & 0o022:
            raise RuntimeError(f"Unsafe kiosk configuration: {path}")
        if path.suffix == ".toml":
            if not value["content"].startswith(MARKER):
                raise RuntimeError(f"Unmanaged kiosk configuration: {path}")
        else:
            from desktop.kiosk_runtime import configuration

            configuration(path)
    return files


def _account() -> pwd.struct_passwd | None:
    try:
        account = pwd.getpwnam(USER)
    except KeyError:
        return None
    if (account.pw_uid == 0 or account.pw_gecos != COMMENT or account.pw_dir != str(HOME)
            or account.pw_shell != "/usr/sbin/nologin"
            or grp.getgrgid(account.pw_gid).gr_name != USER):
        raise RuntimeError("Existing kiosk account requires administrator migration")
    groups = run(["id", "-G", USER], capture_output=True).stdout.split()
    if groups != [str(account.pw_gid)]:
        raise RuntimeError("Kiosk account must have only its dedicated primary group")
    password = run(["passwd", "--status", USER], capture_output=True).stdout.split()
    if len(password) < 2 or password[:2] != [USER, "L"]:
        raise RuntimeError("Kiosk account must retain its locked password")
    return account


def _vendor_state() -> dict[str, str]:
    result = run(["systemctl", "show", "greetd.service",
                  "--property=LoadState,ActiveState,UnitFileState", "--no-pager"], capture_output=True)
    state = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if (state.get("LoadState") not in {"loaded", "not-found", "masked"}
            or state.get("ActiveState") not in {"inactive", "failed"}
            or state.get("UnitFileState", "") not in {"", "disabled", "not-found", "masked", "masked-runtime"}):
        raise RuntimeError("Disable the existing greetd display manager before configuring kiosk")
    return state


def preflight_kiosk(config: SetupConfig) -> None:
    """Reject incompatible seats and unowned resources before profile mutations."""
    validate_kiosk_settings(config)
    if config.kiosk_mode is None:
        return
    if not can_manage_system_services(config.machine_type):
        raise ValueError("Kiosk setup requires persistent systemd services")
    if is_dry_run():
        print("  [DRY-RUN] Would check kiosk ownership, console seat and display-manager conflicts")
        return
    for path in (CONFIG_DIR.parent, CONFIG_DIR, PRESET.parent, UNIT_DIR, HOME.parent):
        check_directory_path(str(path))
        try:
            with directory_fd(str(path)) as descriptor:
                info = os.fstat(descriptor)
                if info.st_uid != 0 or info.st_mode & 0o022:
                    raise RuntimeError(f"Unsafe kiosk parent directory: {path}")
        except FileNotFoundError:
            pass
    _owned_service()
    _owned(PRESET)
    _configuration_files()
    if config.kiosk_mode == "off":
        return
    release = read_os_release()
    if release.get("ID") != "debian" or release.get("VERSION_ID") != "13":
        raise ValueError("Local kiosk requires Debian 13 / Proxmox VE 9")
    if resolve_machine_type(config.machine_type) not in {"hardware", "vm"}:
        raise ValueError("Local kiosk requires a hardware or VM console, not a container")
    if (not VT.exists() or not any(DRM_DIR.glob("card[0-9]*"))
            or not any(INPUT_DIR.glob("event*"))):
        raise RuntimeError("Kiosk requires tty7, a DRM graphics card, and local input devices")
    if os.path.lexists(DISPLAY_MANAGER) and not (
            DISPLAY_MANAGER.is_symlink() and DISPLAY_MANAGER.resolve() == Path("/dev/null")):
        raise RuntimeError("Existing console display manager requires administrator migration")
    for override in (UNIT_DIR / "greetd.service", VENDOR_MASK):
        if os.path.lexists(override) and not (
                override.is_symlink() and override.resolve() == Path("/dev/null")):
            raise RuntimeError("Existing greetd override requires administrator migration")
    _vendor_state()
    account = _account()
    if os.path.lexists(HOME):
        with directory_fd(str(HOME)) as descriptor:
            info = os.fstat(descriptor)
            if account is None or info.st_uid != account.pw_uid or stat.S_IMODE(info.st_mode) != 0o700:
                raise RuntimeError("Existing kiosk home requires administrator migration")
    sessions = run(["loginctl", "list-sessions", "--no-legend", "--no-pager"], capture_output=True)
    for row in sessions.stdout.splitlines():
        if not row.strip():
            continue
        session = row.split()[0]
        details = run(["loginctl", "show-session", session, "-p", "Name", "-p", "TTY", "-p", "Type"],
                      capture_output=True)
        fields = dict(line.split("=", 1) for line in details.stdout.splitlines() if "=" in line)
        if fields.get("Name") != USER and (fields.get("TTY") == "tty7"
                or fields.get("Type") in {"wayland", "x11"}):
            raise RuntimeError("Log out other graphical/tty7 sessions before configuring kiosk")
    kiosk_url(config)


def _root_directory(path: Path) -> None:
    with directory_fd(str(path), create=True) as descriptor:
        info = os.fstat(descriptor)
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise RuntimeError(f"Unsafe kiosk configuration directory: {path}")
        os.fchmod(descriptor, 0o755)


def _install_packages() -> None:
    """Prevent the packaged login manager from acquiring the console on install."""
    _root_directory(PRESET.parent)
    write_text_atomic(str(PRESET), MARKER + "disable greetd.service\n", mode=0o644)
    # The preset prevents first-install enablement. A temporary mask also guards
    # package hooks that try to start greetd; preserve an existing administrator mask.
    own_mask = not os.path.lexists(VENDOR_MASK)
    if own_mask:
        run(["systemctl", "mask", "--runtime", "greetd.service"])
    try:
        for package in PACKAGES:
            validate_package_name(package)
        run(["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "-y", "-q",
             "--no-remove", "--no-install-recommends", *APT_DPKG_OPTIONS, *APT_LOCK_OPTIONS, *PACKAGES])
    finally:
        if own_mask:
            run(["systemctl", "unmask", "--runtime", "greetd.service"])
    _vendor_state()
    for command in ("/usr/sbin/greetd", "/usr/bin/cage", "/usr/bin/firefox-esr", "/usr/bin/dbus-run-session"):
        if not os.access(command, os.X_OK):
            raise RuntimeError(f"Kiosk package installation did not provide {command}")


def _install_account() -> None:
    account = _account()
    if account is None:
        run(["useradd", "--system", "--user-group", "--no-create-home", "--home-dir", str(HOME),
             "--shell", "/usr/sbin/nologin", "--comment", COMMENT, USER])
        account = _account()
        if account is None:
            raise RuntimeError("Kiosk account creation failed")
    with directory_fd(str(HOME), create=True, mode=0o700) as descriptor:
        info = os.fstat(descriptor)
        if info.st_uid not in {0, account.pw_uid}:
            raise RuntimeError("Unsafe kiosk home ownership")
        os.fchown(descriptor, account.pw_uid, account.pw_gid)
        os.fchmod(descriptor, 0o700)


def _word(value: str) -> str:
    validate_filesystem_path(value)
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$") + '"'


def render_configuration(url: str, source: Path) -> tuple[dict[Path, str], str]:
    """Use immutable configuration generations so unit rollback uses old data."""
    validate_kiosk_url(url)
    script = source / "desktop/kiosk_runtime.py"
    validate_filesystem_path(str(script), must_exist=True)
    content = json.dumps({"manager": MANAGER, "version": 1, "url": url}, sort_keys=True) + "\n"
    generation = hashlib.sha256((str(script) + "\n" + content).encode()).hexdigest()
    session = CONFIG_DIR / f"session-{generation}.json"
    greetd = session.with_suffix(".toml")
    session_command = shlex.join(["/usr/bin/dbus-run-session", "--", "/usr/bin/systemd-cat",
                                  "--identifier=basaltwater-kiosk", "/usr/bin/python3", "-I",
                                  str(script), "session", "--config", str(session)])
    idle_command = shlex.join(["/usr/bin/python3", "-I", str(script), "idle"])
    # JSON strings are valid TOML basic strings for these validated paths.
    greeting = (MARKER + '[terminal]\nvt = 7\nswitch = true\n\n[general]\n'
                'source_profile = false\nrunfile = "/run/basaltwater-kiosk/initial-session"\n\n'
                f'[default_session]\ncommand = {json.dumps(idle_command)}\nuser = "{USER}"\n\n'
                f'[initial_session]\ncommand = {json.dumps(session_command)}\nuser = "{USER}"\n')
    unit = (MARKER + '[Unit]\nDescription=Basaltwater local browser kiosk\n'
            'After=systemd-user-sessions.service systemd-logind.service getty@tty7.service\n'
            'After=network.target pveproxy.service nginx.service basaltwater-web-panel.service\n'
            'Wants=systemd-logind.service\nConflicts=getty@tty7.service greetd.service\n'
            'StartLimitIntervalSec=600\nStartLimitBurst=3\n\n[Service]\nType=exec\n'
            f'ExecStartPre=/usr/bin/python3 -I {_word(str(script))} cleanup\n'
            f'ExecStart=/usr/sbin/greetd --config {_word(str(greetd))}\n'
            f'ExecStopPost=/usr/bin/python3 -I {_word(str(script))} cleanup\n'
            'RuntimeDirectory=basaltwater-kiosk\nRuntimeDirectoryMode=0755\n'
            'Restart=always\nRestartSec=5\nTimeoutStartSec=60\nTimeoutStopSec=60\n'
            'KillMode=control-group\nSendSIGHUP=yes\nKeyringMode=shared\n'
            'UMask=0077\n'
            '\n[Install]\nWantedBy=multi-user.target\n')
    return {session: content, greetd: greeting}, unit


def configure_kiosk(config: SetupConfig) -> None:
    """Reconcile the opted-in runtime, retaining normal tty1 and VM networking."""
    preflight_kiosk(config)
    if config.kiosk_mode is None:
        return
    if is_dry_run():
        action = "remove managed kiosk configuration" if config.kiosk_mode == "off" else (
            f"install Firefox ESR/Cage/greetd; boot into {config.kiosk_mode}; open {kiosk_url(config)}")
        print(f"  [DRY-RUN] Would {action}")
        return
    previous = _owned_service()
    if config.kiosk_mode == "off":
        # Missing unit files must not hide a retained, still-running PAM scope.
        if previous is None and _account() is not None:
            cleanup_sessions()
        remove_units((SERVICE,), unit_dir=str(UNIT_DIR), prepare=cleanup_sessions)
        for path in _configuration_files():
            remove_file_durable(str(path))
        if _owned(PRESET) is not None:
            remove_file_durable(str(PRESET))
        print("  ✓ Kiosk disabled; browser data, account and packages retained")
        return
    _install_packages()
    _install_account()
    files, unit = render_configuration(kiosk_url(config), Path(__file__).resolve().parents[1])
    _root_directory(CONFIG_DIR)
    for path, content in files.items():
        existing = snapshot_unit_file(str(path))
        if existing is not None and existing["content"] != content:
            raise RuntimeError(f"Kiosk configuration generation was modified: {path}")
        if existing is None:
            write_text_atomic(str(path), content, mode=0o644)
    state = inspect_unit_state(SERVICE)
    changed = previous is None or previous["content"] != unit
    web = config.kiosk_mode == "web"
    activate = web and (changed or state["ActiveState"] != "active" or state["UnitFileState"] != "enabled")
    deactivate = not web and (state["ActiveState"] != "inactive"
                             or state["UnitFileState"] in {"enabled", "enabled-runtime"})
    if changed or activate or deactivate:
        replace_units({SERVICE: unit}, activate=(SERVICE,) if activate else (),
                      deactivate=(SERVICE,) if deactivate else (), unit_dir=str(UNIT_DIR),
                      prepare=cleanup_sessions if not web else None)
    elif not web:
        # An inactive unit can still have a failed ExecStopPost or a PAM scope
        # left by an interrupted older runtime. Check it before claiming CLI mode.
        cleanup_sessions()
    print(f"  ✓ Local kiosk configured ({config.kiosk_mode} boot); Ctrl+Alt+F1 returns to host login")
