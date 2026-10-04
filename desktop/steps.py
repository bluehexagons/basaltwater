"""Desktop and workstation setup steps."""

from __future__ import annotations

from .desktop_environment_steps import (
    install_desktop,
    install_smbclient,
    configure_xfce_for_rdp,
    configure_dark_theme,
)

from .xrdp_steps import (
    install_xrdp,
    harden_xrdp,
)

from .browser_steps import (
    install_browser,
    configure_default_browser,
)

from .apps_steps import (
    is_flatpak_installed,
    install_flatpak_if_needed,
    is_flatpak_app_installed,
    install_remmina,
    install_office_apps,
    install_editor,
    install_blender,
)

__all__ = [
    'install_desktop',
    'install_smbclient',
    'configure_xfce_for_rdp',
    'configure_dark_theme',
    'install_xrdp',
    'harden_xrdp',
    'install_browser',
    'configure_default_browser',
    'is_flatpak_installed',
    'install_flatpak_if_needed',
    'is_flatpak_app_installed',
    'install_remmina',
    'install_office_apps',
    'install_editor',
    'install_blender',
]
