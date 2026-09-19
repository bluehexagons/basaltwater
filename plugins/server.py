"""Server-oriented built-in plugin definitions and step builders."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lib.plugin_registry import PluginDefinition, SystemTypeDefinition

if TYPE_CHECKING:
    from lib.config import SetupConfig
    from lib.types import StepFunc


PLUGIN = PluginDefinition(
    name="server",
    module=__name__,
    plugin_kind="composition",
    dependencies=("common", "core", "desktop", "security", "sync", "web"),
    system_types=(
        SystemTypeDefinition(
            name="control_plane",
            description="Infrastructure control plane",
            order=35,
            include_cli_tools=True,
            include_control_plane_tools=True,
            step_builder="plugins.server:build_server_steps",
        ),
        SystemTypeDefinition(
            name="agent_vm",
            description="Headless agent coding VM",
            order=38,
            include_cli_tools=True,
            default_agent_tools=("gh", "codex"),
            default_agent_auth_source="login",
            step_builder="plugins.server:build_server_steps",
        ),
        SystemTypeDefinition(
            name="server_dev",
            description="Development server",
            order=40,
            include_cli_tools=True,
            step_builder="plugins.server:build_server_steps",
        ),
        SystemTypeDefinition(
            name="server_web",
            description="Web server",
            order=50,
            include_cli_tools=True,
            include_web_server=True,
            include_web_firewall=True,
            step_builder="plugins.server:build_server_steps",
        ),
        SystemTypeDefinition(
            name="server_lite",
            description="Lightweight server",
            order=60,
            step_builder="plugins.server:build_server_steps",
        ),
    ),
)


def extend_antistatic_steps(config: SetupConfig, steps: list[tuple[str, StepFunc]]) -> None:
    """Append Antistatic service setup steps when configured."""
    if config.antistatic_server:
        from game.antistatic_steps import setup_antistatic_server

        steps.append(("Setting up antistatic lobby server", setup_antistatic_server))

    if config.antistatic_db:
        from game.antistatic_steps import setup_antistatic_db

        steps.append(("Setting up antistatic-db service", setup_antistatic_db))


def extend_gogs_steps(config: SetupConfig, steps: list[tuple[str, StepFunc]]) -> None:
    """Append Gogs setup and update steps when configured."""
    if not config.gogs:
        return

    from common.steps import configure_auto_update_gogs
    from web.gogs_steps import setup_gogs

    steps.append(("Setting up Gogs service", setup_gogs))
    steps.append(("Configuring Gogs auto-update", configure_auto_update_gogs))


def extend_homebox_steps(config: SetupConfig, steps: list[tuple[str, StepFunc]]) -> None:
    """Append HomeBox setup and its recurring updater when enabled."""
    if config.homebox is None:
        return

    from web.homebox_steps import setup_homebox

    steps.append(("Configuring HomeBox inventory service", setup_homebox))
    if config.homebox:
        from common.steps import configure_auto_update_homebox

        steps.append(("Configuring HomeBox auto-update", configure_auto_update_homebox))


def build_server_steps(config: SetupConfig) -> list[tuple[str, StepFunc]]:
    """Build server-oriented setup steps from plugin-owned capability helpers."""

    from plugins.common import (
        extend_package_steps,
        extend_agent_steps,
        extend_web_panel_steps,
        extend_control_plane_steps,
        extend_runtime_steps,
        get_cli_steps,
        get_common_steps,
        get_final_steps,
    )
    from plugins.security import get_security_steps, get_web_firewall_steps
    from plugins.desktop import extend_desktop_steps, extend_desktop_browser_and_office_steps
    from plugins.sync import extend_syncthing_steps
    from plugins.web import (
        extend_app_server_steps,
        extend_build_server_steps,
        extend_cicd_steps,
        get_web_server_steps,
    )

    steps: list[tuple[str, StepFunc]] = list(get_common_steps(config))

    if config.include_web_firewall:
        steps.extend(get_web_firewall_steps())
    elif config.system_type == "server_lite" and (
        config.effective_web_ports()
        or config.homebox == []
        or config.disable_web_panel
        or config.effective_access_sources()
        or config.clear_access_sources
        or config.clear_lan_access
        or config.enable_mdns
        or config.clear_mdns
        or config.enable_rdp
    ):
        from security.steps import configure_firewall

        steps.append(
            ("Configuring firewall for requested services" if config.enable_rdp
             else "Configuring firewall for requested web ports", configure_firewall)
        )

    steps.extend(get_security_steps(lite=config.system_type in {"server_web", "server_lite"}))
    extend_desktop_steps(config, steps)
    extend_desktop_browser_and_office_steps(config, steps)

    if config.include_web_server:
        steps.extend(get_web_server_steps())

    if config.include_cli_tools:
        steps.extend(get_cli_steps())

    extend_control_plane_steps(config, steps)
    extend_runtime_steps(config, steps)
    extend_package_steps(config, steps)
    extend_agent_steps(config, steps)
    extend_cicd_steps(config, steps)
    extend_app_server_steps(config, steps)
    extend_build_server_steps(config, steps)
    extend_antistatic_steps(config, steps)
    extend_gogs_steps(config, steps)
    extend_homebox_steps(config, steps)
    extend_syncthing_steps(config, steps)
    extend_web_panel_steps(config, steps)
    steps.extend(get_final_steps(config))
    return steps
