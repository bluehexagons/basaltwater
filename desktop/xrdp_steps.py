"""Desktop and workstation setup steps."""

from __future__ import annotations

import glob
import os
import shlex

from desktop.session_steps import STARTWM, assert_desktop_idle, install_session_runtime

from lib.config import SetupConfig
from lib.apt_sources import XRDP_SID_PREFERENCE, XRDP_SID_SOURCE
from lib.remote_utils import (
    get_os_id,
    get_user_home,
    is_dry_run,
    is_package_installed,
    is_service_active,
    run,
)
from lib.xrdp_certificate import (
    DEFAULT_XRDP_CERTIFICATE,
    DEFAULT_XRDP_PRIVATE_KEY,
    inspect_xrdp_certificate_pair,
)

_XRDP_XORG_LAUNCHER = "/usr/local/libexec/basaltwater-xrdp-Xorg"
_XRDP_APPARMOR_LOCAL = "/etc/apparmor.d/local/Xorg"
_XRDP_SID_SOURCES = "/etc/apt/sources.list.d/basaltwater-sid.sources"
_XRDP_SID_PREFERENCES = "/etc/apt/preferences.d/basaltwater-sid.pref"
_XRDP_SID_FORBIDDEN_UPGRADES = (
    "libc6",
    "systemd",
    "xserver-xorg-core",
    "linux-image",
    "linux-headers",
)
_XRDP_DPKG_OPTIONS = (
    "-o Dpkg::Options::=--force-confdef "
    "-o Dpkg::Options::=--force-confold"
)
# Keep this aligned with xorgxrdp's built-in glamor allowlist. In particular,
# virtio_gpu is not included: a Proxmox VirtIO recovery display is not enough
# evidence that the guest has a stable VirGL render path for xrdpdev.
_XRDP_GLAMOR_DRIVERS = frozenset({"amdgpu", "i915", "xe", "msm", "radeon"})
_XRDP_GLAMOR_DRIVER_LIST = "amdgpu i915 xe msm radeon"


def _configure_xrdp_package_source(*, refresh_metadata: bool = True) -> str:
    """Configure the narrowly pinned Debian Sid source used for XRDP."""
    if get_os_id() != "debian":
        return ""

    os.makedirs(os.path.dirname(_XRDP_SID_SOURCES), exist_ok=True)
    os.makedirs(os.path.dirname(_XRDP_SID_PREFERENCES), exist_ok=True)
    with open(_XRDP_SID_SOURCES, "w", encoding="utf-8") as source_file:
        source_file.write(XRDP_SID_SOURCE)
    with open(_XRDP_SID_PREFERENCES, "w", encoding="utf-8") as preference_file:
        preference_file.write(XRDP_SID_PREFERENCE)

    if refresh_metadata:
        update_result = run("apt-get update -qq", check=False)
        if update_result.returncode != 0:
            raise RuntimeError("could not refresh Debian Sid package metadata")
    return "sid"


def _install_xrdp_packages(packages: tuple[str, ...]) -> None:
    """Install XRDP packages without optional dependency stacks."""
    from lib.validation import validate_package_name

    for package in packages:
        validate_package_name(package)
    target_release = _configure_xrdp_package_source()
    package_list = " ".join(shlex.quote(
        f"{package}/{target_release}" if target_release and package in {"xrdp", "xorgxrdp"}
        else package) for package in packages)
    apt_prefix = "apt-get install"
    simulation = run(
        f"{apt_prefix} --simulate {_XRDP_DPKG_OPTIONS}"
        f" --no-install-recommends --no-remove {package_list}",
        check=False,
        capture_output=True,
    )
    if simulation.returncode != 0:
        raise RuntimeError(
            "xRDP package installation failed; could not simulate the transaction"
        )
    simulation_output = simulation.stdout or ""
    simulated_installs = {
        line.split()[1].split(":", 1)[0]
        for line in simulation_output.splitlines()
        if line.startswith("Inst ") and len(line.split()) > 1
    }
    forbidden = sorted(
        package
        for package in simulated_installs
        if any(
            package == forbidden_name
            or package.startswith(f"{forbidden_name}-")
            for forbidden_name in _XRDP_SID_FORBIDDEN_UPGRADES
        )
    )
    if forbidden:
        raise RuntimeError(
            "refusing xRDP installation because it would upgrade core packages: "
            + ", ".join(forbidden)
        )

    install_result = run(
        f"{apt_prefix} -y -qq {_XRDP_DPKG_OPTIONS}"
        f" --no-install-recommends --no-remove {package_list}",
        check=False,
    )
    if install_result.returncode != 0:
        raise RuntimeError(
            "xRDP package installation failed; check APT sources and package-manager output"
        )

def _generate_sesman_ini(config: SetupConfig) -> str:
    """Generate complete sesman.ini content.
    
    Uses Xorg+xorgxrdp backend exclusively for the most stable resize behaviour.
    Xvnc is NOT included because it doesn't emit RANDR events, causing desktop
    freezes when the RDP window is resized.

    This path is tuned for VM-first desktops with persistent user services.
    """
    
    # Only Xorg backend - Xvnc disabled due to resize issues
    # Xorg+xorgxrdp: proper RANDR events, dynamic resize works correctly
    # Xvnc: doesn't emit RRScreenChangeNotify -> desktop freezes on resize
    return f'''[Globals]
EnableUserWindowManager=false
DefaultWindowManager={STARTWM}
ReconnectScript=/bin/true

[Security]
AllowRootLogin=false
MaxLoginRetry=3
TerminalServerUsers=basaltwater-desktop
AlwaysGroupCheck=true
AllowAlternateShell=false
SessionSockdirGroup=xrdp

[Sessions]
X11DisplayOffset=10
MaxSessions=1
KillDisconnected=false
DisconnectedTimeLimit=0
IdleTimeLimit={config.rdp_idle_timeout}
Policy=UB

[Logging]
LogFile=/var/log/xrdp-sesman.log
LogLevel=INFO
EnableSyslog=true
SyslogLevel=INFO

[Xorg]
param={_XRDP_XORG_LAUNCHER}
param=-config
# Xorg rejects absolute -config paths when sesman starts it as a non-root user.
# This trusted-search-path form resolves to /etc/X11/xrdp/xorg.conf on Debian.
param=xrdp/xorg.conf
param=-noreset
param=-nolisten
param=tcp
param=-logfile
# Debian's enforced Xorg AppArmor profile permits per-user Xorg logs here.
param=.local/share/xorg/Xorg.%s.log
'''


def _find_xrdp_glamor_render_node() -> tuple[str, str] | None:
    """Find a render node using a driver supported by xorgxrdp glamor."""
    for render_node in sorted(glob.glob("/dev/dri/renderD[0-9]*")):
        node_name = os.path.basename(render_node)
        if not node_name.startswith("renderD") or not node_name[7:].isdigit():
            continue
        driver_link = os.path.join(
            "/sys/class/drm", node_name, "device", "driver"
        )
        driver = os.path.basename(os.path.realpath(driver_link))
        if driver in _XRDP_GLAMOR_DRIVERS:
            return render_node, driver
    return None


def _detect_xrdp_glamor_render_node(username: str) -> tuple[str, str] | None:
    """Enable glamor only when the desktop user can access a supported GPU."""
    render_info = _find_xrdp_glamor_render_node()
    if render_info is None:
        return None

    render_node, _driver = render_info
    _ensure_user_in_group(username, "render")
    quoted_username = shlex.quote(username)
    quoted_render_node = shlex.quote(render_node)
    access_check = run(
        f"runuser -u {quoted_username} -- test -r {quoted_render_node}"
        f" -a -w {quoted_render_node}",
        check=False,
    )
    if access_check.returncode != 0:
        print(
            f"  ⚠ Supported GPU found at {render_node}, but {username} "
            "cannot access its render node; using software rendering"
        )
        return None
    return render_info


def _generate_xorg_conf(render_node: str | None = None) -> str:
    """Generate managed xorgxrdp configuration with optional glamor."""
    if render_node is None:
        acceleration_options = (
            '    # No supported, accessible DRM render node was detected.\n'
            '    # An empty device disables the driver default render-node probe.\n'
            '    Option "DRMDevice" ""\n'
        )
    else:
        acceleration_options = (
            '    # Enable glamor only for a supported, accessible render node.\n'
            f'    Option "DRMDevice" "{render_node}"\n'
            '    Option "DRI3" "1"\n'
            f'    Option "DRMAllowList" "{_XRDP_GLAMOR_DRIVER_LIST}"\n'
        )
    return '''Section "ServerLayout"
    Identifier "X11 Server"
    Screen "Screen (xrdpdev)"
    InputDevice "xrdpMouse" "CorePointer"
    InputDevice "xrdpKeyboard" "CoreKeyboard"
EndSection

Section "ServerFlags"
    Option "DefaultServerLayout" "X11 Server"
    Option "DontVTSwitch" "on"
    Option "AutoAddDevices" "off"
    Option "AutoAddGPU" "off"
    # Disable screen saver and DPMS to prevent display management conflicts
    Option "StandbyTime" "0"
    Option "SuspendTime" "0"
    Option "OffTime" "0"
    Option "BlankTime" "0"
EndSection

Section "Module"
    Load "fb"
    Load "glamoregl"
    Load "xorgxrdp"
EndSection

Section "InputDevice"
    Identifier "xrdpKeyboard"
    Driver "xrdpkeyb"
EndSection

Section "InputDevice"
    Identifier "xrdpMouse"
    Driver "xrdpmouse"
EndSection

Section "Monitor"
    Identifier "Monitor"
    HorizSync 30-80
    VertRefresh 50-75
EndSection

Section "Device"
    Identifier "Video Card (xrdpdev)"
    Driver "xrdpdev"
{acceleration_options}EndSection

Section "Screen"
    Identifier "Screen (xrdpdev)"
    Device "Video Card (xrdpdev)"
    Monitor "Monitor"
    DefaultDepth 24
    SubSection "Display"
        Depth 24
        # xrdpdev requires a usable mode before it can create the screen.
        Modes "640x480" "800x600" "1024x768" "1280x720" "1280x1024" "1600x900" "1920x1080"
        # Virtual screen size to support dynamic resizing (up to 4K: 3840x2160)
        # Supports 4K and common ultrawide resolutions
        Virtual 3840 2160
    EndSubSection
EndSection
'''.format(acceleration_options=acceleration_options)


def _generate_xrdp_ini(config: SetupConfig, template: str) -> str:
    """Render validated XRDP listener and channel policy into its template."""
    from lib.validation import validate_network_ip

    bind_address = validate_network_ip(
        config.rdp_bind_address if config.enable_rdp else "127.0.0.1",
        "RDP bind address",
    )
    replacements = {
        "{RDP_BIND_ADDRESS}": bind_address,
        "{RDP_CLIPBOARD}": str(config.rdp_clipboard).lower(),
        "{RDP_DRIVE_REDIRECTION}": str(config.rdp_drive_redirection).lower(),
        "{RDP_AUDIO}": str(config.rdp_audio).lower(),
    }
    rendered = template
    for placeholder, value in replacements.items():
        rendered = rendered.replace(placeholder, value)
    return rendered


def _ensure_user_in_group(username: str, group: str) -> bool:
    """Ensure the given user is a member of the given group."""
    quoted_username = shlex.quote(username)
    quoted_group = shlex.quote(group)
    membership_check_cmd = f"id -nG {quoted_username} | tr ' ' '\\n' | grep -Fxq {quoted_group}"
    membership_check = run(membership_check_cmd, check=False)
    if membership_check.returncode == 0:
        return False

    add_result = run(
        f"getent group {quoted_group} && adduser {quoted_username} {quoted_group}",
        check=False,
    )
    if add_result.returncode != 0:
        print(f"  ⚠ Could not add {username} to group {group}")
        return False

    membership_check = run(membership_check_cmd, check=False)
    if membership_check.returncode != 0:
        print(f"  ⚠ Could not verify membership after adding {username} to group {group}")
        return False

    return True


def _refresh_services(*services: str, reload_running_services: bool = True) -> None:
    """Refresh active services and start inactive ones."""
    active_services = [service for service in services if is_service_active(service)]
    inactive_services = [service for service in services if service not in active_services]

    if reload_running_services and active_services:
        active_list = " ".join(shlex.quote(service) for service in active_services)
        run(f"systemctl reload-or-restart {active_list}")

    if inactive_services:
        inactive_list = " ".join(shlex.quote(service) for service in inactive_services)
        run(f"systemctl start {inactive_list}")


def _remove_legacy_xrdp_socket_environment() -> None:
    """Remove the obsolete static socket-path overrides from older setups."""
    removed = False
    for service in ("xrdp", "xrdp-sesman"):
        dropin_path = f"/etc/systemd/system/{service}.service.d/basaltwater.conf"
        if os.path.exists(dropin_path):
            try:
                os.remove(dropin_path)
            except FileNotFoundError:
                continue
            removed = True
    if removed:
        run("systemctl daemon-reload")


def _configure_xrdp_apparmor_socket_access(render_node: str | None = None) -> None:
    """Permit confined Xorg to use xorgxrdp transport and capture buffers."""
    render_rule = ""
    if render_node is not None:
        render_rule = (
            "# xorgxrdp glamor render node selected during setup\n"
            f"{render_node} rw,\n"
        )
    os.makedirs(os.path.dirname(_XRDP_APPARMOR_LOCAL), exist_ok=True)
    with open(_XRDP_APPARMOR_LOCAL, "w", encoding="utf-8") as rules:
        rules.write(
            "# basaltwater: xorgxrdp per-user transport sockets\n"
            "owner /run/xrdp/sockdir/** rw,\n"
            "# xorgxrdp uses POSIX shared memory for RDP frame capture\n"
            "owner /dev/shm/ rw,\n"
            "owner /dev/shm/** rw,\n"
            + render_rule
            + "unix (create, bind, listen, accept, receive, send) type=stream,\n"
        )
    run("apparmor_parser -r /etc/apparmor.d/Xorg", check=False)


def _validate_xrdp_tls_certificate() -> None:
    """Refuse to activate XRDP with an unusable TLS certificate/key pair."""
    health = inspect_xrdp_certificate_pair(
        DEFAULT_XRDP_CERTIFICATE,
        DEFAULT_XRDP_PRIVATE_KEY,
    )
    for detail in health.details:
        prefix = "⚠" if health.status == "warning" else "✗"
        print(f"  {prefix} {detail}")
    if health.status == "error":
        run("systemctl stop xrdp", check=False)
        raise RuntimeError("XRDP TLS certificate validation failed")
    print("  ✓ XRDP TLS certificate and private key validated")


def _require_peer_authenticated_sesrun() -> None:
    """Older XRDP releases cannot perform the passwordless local start flow."""
    version = run(["dpkg-query", "-W", "-f=${Version}", "xrdp"], capture_output=True)
    value = version.stdout.strip()
    if not value or run(["dpkg", "--compare-versions", value, "ge", "0.10.0"], check=False).returncode:
        raise RuntimeError("Shared desktops require XRDP 0.10 or newer; rerun setup with --refresh-packages")
    run(["test", "-x", "/usr/bin/xrdp-sesrun"])


def install_xrdp(config: SetupConfig) -> None:
    if is_dry_run():
        print("  [DRY-RUN] Would install and configure xRDP")
        return

    assert_desktop_idle(config)

    safe_username = shlex.quote(config.username)
    user_home = get_user_home(config.username)
    xorg_log_dir = os.path.join(user_home, ".local", "share", "xorg")
    sesman_config = "/etc/xrdp/sesman.ini"
    xrdp_config = "/etc/xrdp/xrdp.ini"
    
    os.environ["DEBIAN_FRONTEND"] = "noninteractive"
    required_packages = (
        "xrdp",
        "xorgxrdp",
        "dbus-x11",
        "x11-xserver-utils",
        "x11-utils",
        "xdotool",
        "scrot",
    )
    missing_packages = [
        package for package in required_packages if not is_package_installed(package)
    ]
    if missing_packages or config.refresh_packages:
        packages_to_install = (
            tuple(required_packages)
            if config.refresh_packages
            else tuple(missing_packages)
        )
        _install_xrdp_packages(packages_to_install)
    else:
        _configure_xrdp_package_source(refresh_metadata=False)
        print("  ✓ xRDP packages already installed; source reconciled without refreshing metadata")

    missing_packages = [
        package for package in required_packages if not is_package_installed(package)
    ]
    if missing_packages:
        raise RuntimeError(
            "xRDP package installation was incomplete: "
            + ", ".join(missing_packages)
        )
    print("  ✓ xRDP packages installed (Xorg+xorgxrdp backend for dynamic resolution)")
    _require_peer_authenticated_sesrun()

    # Debian's Xorg AppArmor profile denies XRDP's traditional
    # ~/.xorgxrdp.<display>.log path. Create the profile-approved log directory
    # as the desktop user so rootless Xorg can start and write its diagnostics.
    log_dir_result = run(
        f"runuser -u {safe_username} -- mkdir -p {shlex.quote(xorg_log_dir)}",
        check=False,
    )
    if log_dir_result.returncode != 0:
        raise RuntimeError("could not create the per-user Xorg log directory")
    run(f"chmod 700 {shlex.quote(xorg_log_dir)}")
    _remove_legacy_xrdp_socket_environment()
    launcher_dir = os.path.dirname(_XRDP_XORG_LAUNCHER)
    os.makedirs(launcher_dir, exist_ok=True)
    with open(_XRDP_XORG_LAUNCHER, "w", encoding="utf-8") as launcher:
        launcher.write(
            "#!/bin/sh\n"
            'export XRDP_SOCKET_PATH="/run/xrdp/sockdir/$(id -u)"\n'
            'exec /usr/lib/xorg/Xorg "$@"\n'
        )
    run(f"chmod 755 {shlex.quote(_XRDP_XORG_LAUNCHER)}")
    render_info = _detect_xrdp_glamor_render_node(config.username)
    render_node = render_info[0] if render_info is not None else None
    _configure_xrdp_apparmor_socket_access(render_node)
    if render_info is None:
        print("  ✓ xRDP software rendering selected (no supported accessible GPU)")
    else:
        print(
            f"  ✓ xRDP glamor acceleration enabled ({render_info[1]}, {render_node})"
        )
    # Configure Xwrapper to allow XRDP sessions to start X server
    # This is critical for preventing session freezes and startup issues
    xwrapper_config = "/etc/X11/Xwrapper.config"
    xwrapper_content = """# Xwrapper configuration for XRDP
# Allow any user to start X server (required for remote desktop sessions)
allowed_users=anybody
# Don't require root privileges
needs_root_rights=no
"""
    
    os.makedirs(os.path.dirname(xwrapper_config), exist_ok=True)
    if os.path.exists(xwrapper_config) and not os.path.exists(f"{xwrapper_config}.bak"):
        run(f"cp {xwrapper_config} {xwrapper_config}.bak")
    with open(xwrapper_config, "w") as f:
        f.write(xwrapper_content)

    # Ensure xrdp can create its runtime dirs/sockets
    run("systemctl enable xrdp-sesman", check=False)
    
    _ensure_user_in_group("xrdp", "ssl-cert")
    _validate_xrdp_tls_certificate()
    
    # Generate sesman.ini with Xorg backend only
    if os.path.exists(sesman_config) and not os.path.exists(f"{sesman_config}.bak"):
        run(f"cp {sesman_config} {sesman_config}.bak")
    
    with open(sesman_config, "w") as f:
        f.write(_generate_sesman_ini(config))
    print("  ✓ Session manager configuration deployed")

    if os.path.exists(xrdp_config) and not os.path.exists(f"{xrdp_config}.bak"):
        run(f"cp {xrdp_config} {xrdp_config}.bak")
    template_path = os.path.join(os.path.dirname(__file__), "config", "xrdp.ini.template")
    with open(template_path, encoding="utf-8") as f:
        xrdp_content = _generate_xrdp_ini(config, f.read())
    with open(xrdp_config, "w") as f:
        f.write(xrdp_content)
    print("  ✓ xRDP configuration deployed")

    # Configure xorgxrdp - required for all XRDP environments
    # Fix for Debian Trixie/X.Org 21.1.16: glamoregl must be loaded before xorgxrdp
    # to resolve undefined glamor_xv_init symbol errors.
    # Glamor is enabled only when setup found a supported, accessible render
    # node; otherwise the software-rendered path remains the safe fallback.
    # Large virtual screen supports up to 4K+ resolutions for dynamic resizing.
    xorg_conf_path = "/etc/X11/xrdp/xorg.conf"
    xorg_conf_dir = os.path.dirname(xorg_conf_path)
    os.makedirs(xorg_conf_dir, exist_ok=True)
    if os.path.exists(xorg_conf_path) and not os.path.exists(f"{xorg_conf_path}.bak"):
        run(f"cp {xorg_conf_path} {xorg_conf_path}.bak")
    with open(xorg_conf_path, "w") as f:
        f.write(_generate_xorg_conf(render_node))
    print("  ✓ xorgxrdp configuration deployed")
    
    install_session_runtime(config)
    run("systemctl enable xrdp")
    _refresh_services("xrdp-sesman", "xrdp")

    print("  ✓ xRDP configured")


def harden_xrdp(config: SetupConfig) -> None:
    """Harden xRDP with TLS encryption and group restrictions.
    
    Note: Security settings are already configured in xrdp.ini and sesman.ini templates.
    This function ensures the xrdp user has proper permissions and only refreshes
    services when that access changes or the services are not running.
    """
    if is_dry_run():
        print("  [DRY-RUN] Would verify shared desktop TLS and owner access")
        return
    xrdp_config = "/etc/xrdp/xrdp.ini"
    
    if not os.path.exists(xrdp_config):
        print("  ⚠ xRDP not installed, skipping hardening")
        return
    
    # Ensure xrdp user has access to SSL certificates
    ssl_cert_changed = _ensure_user_in_group("xrdp", "ssl-cert")
    _refresh_services("xrdp", reload_running_services=ssl_cert_changed)
    
    print("  ✓ xRDP hardened (TLS encryption, strong ciphers, group restrictions)")
