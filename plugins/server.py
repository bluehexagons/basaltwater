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
            name="server_github_runner",
            description="Dedicated Debian GitHub Actions runner",
            order=55,
            step_builder="plugins.server:build_github_runner_steps",
        ),
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


def build_github_runner_steps(config: SetupConfig) -> list[tuple[str, StepFunc]]:
    """Keep the build account separate from operator panel and approval access."""
    from web.github_runner_steps import check_runner_platform, setup_github_runner
    from common.steps import setup_user
    from plugins.common import extend_web_panel_steps

    steps: list[tuple[str, StepFunc]] = [
        ("Checking Debian GitHub runner prerequisites", check_runner_platform),
        ("Setting up operator account", setup_user),
    ]
    if config.privilege_broker_port is not None or config.disable_privilege_broker:
        from common.agent_security_steps import configure_agent_user_security

        steps.append(("Configuring operator account security", configure_agent_user_security))
    steps.append(("Installing GitHub runner and author gate", setup_github_runner))
    extend_web_panel_steps(config, steps)
    return steps


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
    from plugins.security import get_fail2ban_steps, get_security_steps, get_web_firewall_steps
    from lib.machine_state import can_manage_firewall
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
        if config.enable_rdp:
            from security.steps import configure_firewall

            steps.append(("Configuring firewall for web and RDP services", configure_firewall))
        else:
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
        or (config.enable_samba and can_manage_firewall(config.machine_type))
    ):
        from security.steps import configure_firewall

        steps.append(
            ("Configuring firewall for requested services" if config.enable_rdp or config.enable_samba
             else "Configuring firewall for requested web ports", configure_firewall)
        )

    steps.extend(get_security_steps(lite=config.system_type in {"server_web", "server_lite"}))
    extend_desktop_steps(config, steps)
    steps.extend(get_fail2ban_steps())
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
