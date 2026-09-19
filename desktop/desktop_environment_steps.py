"""Desktop environment setup steps."""

from __future__ import annotations
import glob
import os
import shlex

from desktop.xfce_config import (
    LEGACY_DISPLAYS, LEGACY_XFSETTINGSD, clean_legacy_xfwm,
    remove_legacy_override, remove_legacy_pm_stub, update_channel,
)

from lib.config import SetupConfig
from lib.validation import validate_filesystem_path
from lib.remote_utils import (
    get_user_home,
    install_package,
    is_dry_run,
    is_package_installed,
    run,
)


def install_desktop(config: SetupConfig) -> None:
    """Install a desktop environment (XFCE, i3, Cinnamon, or LXQt)."""
    if is_dry_run():
        print(f"  [DRY-RUN] Would install the {config.desktop.upper()} desktop")
        return

    if config.desktop == "xfce":
        package = "xfce4"
        install_cmd = "apt-get install -y -qq --no-install-recommends xfce4 xfce4-goodies"
    elif config.desktop == "i3":
        package = "i3"
        install_cmd = "apt-get install -y -qq --no-install-recommends i3 i3status i3lock dmenu"
    elif config.desktop == "cinnamon":
        package = "cinnamon"
        install_cmd = "apt-get install -y -qq --no-install-recommends cinnamon cinnamon-core"
    elif config.desktop == "lxqt":
        package = "lxqt-core"
        install_cmd = "apt-get install -y -qq --no-install-recommends lxqt-core lxqt-config lxqt-session"
    else:
        raise ValueError(f"Unsupported shared desktop environment: {config.desktop}")
    
    if is_package_installed(package):
        print(f"  ✓ {config.desktop.upper()} desktop already installed")
        return
    
    if not install_package(f"{config.desktop.upper()} desktop", package, install_cmd):
        raise RuntimeError(
            f"{config.desktop.upper()} desktop installation failed; "
            "check APT sources and package-manager output"
        )


def configure_xfce_for_rdp(config: SetupConfig) -> None:
    """Configure XFCE to work properly in RDP sessions.
    
    Fixes common issues:
    - Disables light-locker (crashes without display manager)
    - Disables xfce4-power-manager display features (no DPMS in RDP)
    - Retires the legacy global power-management stub
    - Removes problematic autostart entries
    - Removes stale XFCE display profiles that conflict with xorgxrdp RANDR
    - Sets XRDP-specific environment indicators
    """
    if config.desktop != "xfce":
        return

    if is_dry_run():
        print("  [DRY-RUN] Would configure XFCE for RDP compatibility")
        return
    
    safe_username = shlex.quote(config.username)
    home_dir = get_user_home(config.username)
    autostart_dir = f"{home_dir}/.config/autostart"
    xfce_config_dir = f"{home_dir}/.config/xfce4/xfconf/xfce-perchannel-xml"
    
    # Create autostart directory
    os.makedirs(autostart_dir, exist_ok=True)

    # The shared desktop has a private bus/display; the global user manager
    # deliberately has neither. Start notifications directly in this session.
    notify_paths = sorted(glob.glob("/usr/lib/*/xfce4/notifyd/xfce4-notifyd")
                          + glob.glob("/usr/lib/xfce4/notifyd/xfce4-notifyd"))
    if notify_paths:
        notifyd = notify_paths[0]
        validate_filesystem_path(notifyd)
        with open(f"{autostart_dir}/xfce4-notifyd.desktop", "w") as f:
            f.write("[Desktop Entry]\nType=Application\nName=XFCE Notifications\n"
                    f"Exec={notifyd}\nOnlyShowIn=XFCE;\nTerminal=false\n")
    
    # 1. Disable light-locker (crashes in RDP sessions without display manager)
    light_locker_desktop = f"{autostart_dir}/light-locker.desktop"
    with open(light_locker_desktop, "w") as f:
        f.write("""[Desktop Entry]
Type=Application
Name=Light Locker
Comment=Screen Locker (disabled for RDP)
Hidden=true
""")
    
    # 2. Remove older basaltwater workaround that disabled xfsettingsd entirely.
    # xfsettingsd is needed for normal XFCE settings; stale display profiles are
    # the part that conflicts with xorgxrdp's RANDR-driven resize events.
    xfsettingsd_desktop = f"{autostart_dir}/xfsettingsd.desktop"
    remove_legacy_override(xfsettingsd_desktop, LEGACY_XFSETTINGSD)
    
    # Retire the global warning-suppression shim without touching user tools.
    remove_legacy_pm_stub()
    
    # 5. Configure xfce4-power-manager to not manage displays
    os.makedirs(xfce_config_dir, exist_ok=True)
    power_manager_config = f"{xfce_config_dir}/xfce4-power-manager.xml"
    
    power_manager_xml = """<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfce4-power-manager" version="1.0">
  <property name="xfce4-power-manager" type="empty">
    <property name="dpms-enabled" type="bool" value="false"/>
    <property name="dpms-on-ac-sleep" type="uint" value="0"/>
    <property name="dpms-on-ac-off" type="uint" value="0"/>
    <property name="dpms-on-battery-sleep" type="uint" value="0"/>
    <property name="dpms-on-battery-off" type="uint" value="0"/>
    <property name="brightness-switch-restore-on-exit" type="int" value="-1"/>
    <property name="brightness-switch" type="int" value="0"/>
    <property name="handle-brightness-keys" type="bool" value="false"/>
  </property>
</channel>
"""
    update_channel(power_manager_config, power_manager_xml)
    
    # 6. Remove stale fixed display profile from previous basaltwater runs.
    # A saved resolution/output profile can override xorgxrdp RANDR resize events.
    displays_config = f"{xfce_config_dir}/displays.xml"
    remove_legacy_override(displays_config, LEGACY_DISPLAYS)
    
    # Remove unsupported settings without resetting the window manager.
    clean_legacy_xfwm(f"{xfce_config_dir}/xfwm4.xml")
    
    # 8. Set ownership
    run(f"chown -R {safe_username}:{safe_username} {shlex.quote(autostart_dir)}")
    run(f"chown -R {safe_username}:{safe_username} {shlex.quote(xfce_config_dir)}")
    
    print("  ✓ XFCE configured for RDP compatibility")
    print("    - light-locker disabled (prevents crashes)")
    print("    - Known legacy display overrides retired; custom profiles preserved")
    print("    - Display power management disabled (no DPMS in RDP)")
    print("    - Unrelated window-manager and power preferences preserved")


def install_smbclient(config: SetupConfig) -> None:
    """Install SMB/CIFS client packages for accessing network shares.
    
    Installs packages needed for file managers (like Thunar for XFCE) to 
    connect to SMB/Samba shares. Includes:
    - cifs-utils: Core SMB/CIFS mounting utilities
    - smbclient: Command-line SMB client
    - gvfs-backends: GNOME VFS backends for file manager integration
    """
    packages = ["cifs-utils", "smbclient", "gvfs-backends"]
    
    all_installed = all(is_package_installed(pkg) for pkg in packages)
    if all_installed:
        print("  ✓ SMB client packages already installed")
        return
    
    packages_str = " ".join(packages)
    os.environ["DEBIAN_FRONTEND"] = "noninteractive"
    run(f"apt-get install -y -qq {packages_str}")
    
    print("  ✓ SMB client packages installed (cifs-utils, smbclient, gvfs-backends)")
    print("    File managers can now browse and mount SMB/Samba shares")


def configure_dark_theme(config: SetupConfig) -> None:
    """Configure desktop environment to use dark theme.
    
    Configures dark theme settings for supported desktop environments:
    - XFCE: Sets GTK appearance while preserving window-manager preferences
    - LXQt: Sets Qt theme to dark
    - Cinnamon: Sets GTK and window manager themes
    - i3: Informational message (requires manual configuration)
    """
    if not config.dark_theme:
        return

    if is_dry_run():
        print(f"  [DRY-RUN] Would configure {config.desktop.upper()} dark theme")
        return
    
    safe_username = shlex.quote(config.username)
    home_dir = get_user_home(config.username)
    
    if config.desktop == "xfce":
        xfce_config_dir = f"{home_dir}/.config/xfce4/xfconf/xfce-perchannel-xml"
        os.makedirs(xfce_config_dir, exist_ok=True)
        
        # Configure XFCE appearance settings
        xsettings_config = f"{xfce_config_dir}/xsettings.xml"
        xsettings_xml = """<?xml version="1.0" encoding="UTF-8"?>
<channel name="xsettings" version="1.0">
  <property name="Net" type="empty">
    <property name="ThemeName" type="string" value="Adwaita-dark"/>
    <property name="IconThemeName" type="string" value="Adwaita"/>
  </property>
</channel>
"""
        update_channel(xsettings_config, xsettings_xml)
        clean_legacy_xfwm(f"{xfce_config_dir}/xfwm4.xml")
        # Leave WM theme and HiDPI selection to XFCE/user settings.

        run(f"chown -R {safe_username}:{safe_username} {shlex.quote(xfce_config_dir)}")
        print("  ✓ XFCE configured with dark theme (Adwaita-dark)")
        
    elif config.desktop == "lxqt":
        lxqt_config_dir = f"{home_dir}/.config/lxqt"
        os.makedirs(lxqt_config_dir, exist_ok=True)
        
        # Configure LXQt to use dark theme
        lxqt_config_file = f"{lxqt_config_dir}/lxqt.conf"
        lxqt_config = """[General]
theme=kvantum-dark
icon_theme=breeze-dark

[Qt]
style=kvantum-dark
"""
        with open(lxqt_config_file, "w") as f:
            f.write(lxqt_config)
        
        run(f"chown -R {safe_username}:{safe_username} {shlex.quote(lxqt_config_dir)}")
        print("  ✓ LXQt configured with dark theme")
        print("    Note: Install kvantum theme packages for best results")
        
    elif config.desktop == "cinnamon":
        # Configure Cinnamon dark theme using gsettings in a single dbus session
        gsettings_script = f"""
sudo -u {safe_username} sh -c 'eval $(dbus-launch --sh-syntax) && \\
gsettings set org.cinnamon.desktop.interface gtk-theme "Adwaita-dark" && \\
gsettings set org.cinnamon.desktop.wm.preferences theme "Adwaita-dark" && \\
gsettings set org.cinnamon.theme name "Adwaita-dark"'
"""
        run(gsettings_script, check=False)
        print("  ✓ Cinnamon configured with dark theme (Adwaita-dark)")
        
    elif config.desktop == "i3":
        print("  ℹ i3 window manager detected")
        print("    Dark theme configuration requires manual i3 config file editing")
        print("    Edit ~/.config/i3/config to customize colors")
    
    else:
        print(f"  ℹ Dark theme configuration not implemented for {config.desktop}")
