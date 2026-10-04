"""Desktop built-in capability plugin definitions and step helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Mapping

from lib.plugin_registry import PluginDefinition

if TYPE_CHECKING:
    from lib.config import SetupConfig
    from lib.types import StepFunc


PLUGIN = PluginDefinition(
    name="desktop",
    module=__name__,
    plugin_kind="capability",
    dependencies=("core",),
    custom_steps=(
        "install_desktop",
        "install_xrdp",
        "harden_xrdp",
        "configure_default_browser",
        "install_editor",
        "install_blender",
        "install_smbclient",
        "configure_dark_theme",
        "install_browser",
        "install_office_apps",
        "install_remmina",
        "configure_xfce_for_rdp",
    ),
    custom_step_provider="plugins.desktop:get_custom_step_functions",
)


def extend_desktop_steps(config: SetupConfig, steps: list[tuple[str, StepFunc]]) -> None:
    """Append the base desktop and RDP-related steps."""

    from desktop.steps import (
        configure_dark_theme,
        configure_xfce_for_rdp,
        harden_xrdp,
        install_desktop,
        install_smbclient,
        install_xrdp,
    )

    if not config.include_desktop:
        return

    from desktop.session_steps import prepare_shared_desktop

    steps.append(("Preparing single-session desktop and disabling console graphical login", prepare_shared_desktop))
    steps.append(("Installing desktop environment", install_desktop))
    steps.append(("Installing single-session XRDP desktop", install_xrdp))
    steps.append(("Configuring desktop for RDP compatibility", configure_xfce_for_rdp))
    if config.enable_smbclient:
        steps.append(("Installing SMB client packages", install_smbclient))
    if config.dark_theme:
        steps.append(("Configuring dark theme", configure_dark_theme))
    steps.append(("Hardening xRDP with TLS and group restrictions", harden_xrdp))


def extend_desktop_app_steps(config: SetupConfig, steps: list[tuple[str, StepFunc]]) -> None:
    """Append the applications declared by a workstation profile."""

    from desktop.steps import (
        configure_default_browser,
        install_browser,
        install_editor,
        install_office_apps,
        install_remmina,
    )

    if config.include_desktop_apps:
        steps.extend(
            [
                ("Installing browser", install_browser),
                ("Configuring default browser", configure_default_browser),
            ]
        )
    elif config.include_pc_dev_apps:
        steps.extend(
            [
                ("Installing Remmina", install_remmina),
                ("Installing browser", install_browser),
                ("Installing Office", install_office_apps),
                ("Configuring default browser", configure_default_browser),
            ]
        )
    elif config.include_workstation_dev_apps:
        steps.extend(
            [
                ("Installing browser", install_browser),
                ("Configuring default browser", configure_default_browser),
            ]
        )

    if config.editor:
        steps.append(("Installing workstation editor", install_editor))


def extend_desktop_browser_and_office_steps(
    config: SetupConfig,
    steps: list[tuple[str, StepFunc]],
) -> None:
    """Append optional browser and office steps for desktop systems."""

    from desktop.steps import configure_default_browser, install_browser, install_office_apps

    if config.include_desktop and (config.browser or config.browsers) and not (
        config.include_desktop_apps or config.include_pc_dev_apps or config.include_workstation_dev_apps
    ):
        steps.append(("Installing browser", install_browser))
        steps.append(("Configuring default browser", configure_default_browser))

    if config.install_office and not config.include_pc_dev_apps:
        steps.append(("Installing Office", install_office_apps))


def get_custom_step_functions() -> Mapping[str, StepFunc]:
    """Return plugin-owned custom step functions exported by the desktop capability."""

    from desktop.steps import (
        configure_dark_theme,
        configure_default_browser,
        configure_xfce_for_rdp,
        harden_xrdp,
        install_blender,
        install_browser,
        install_desktop,
        install_editor,
        install_office_apps,
        install_remmina,
        install_smbclient,
        install_xrdp,
    )

    return {
        "install_desktop": install_desktop,
        "install_xrdp": install_xrdp,
        "harden_xrdp": harden_xrdp,
        "configure_default_browser": configure_default_browser,
        "install_editor": install_editor,
        "install_blender": install_blender,
        "install_smbclient": install_smbclient,
        "configure_dark_theme": configure_dark_theme,
        "install_browser": install_browser,
        "install_office_apps": install_office_apps,
        "install_remmina": install_remmina,
        "configure_xfce_for_rdp": configure_xfce_for_rdp,
    }
