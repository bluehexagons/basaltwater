"""Local tooling composition for an existing CachyOS Plasma workstation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lib.plugin_registry import PluginDefinition, SystemTypeDefinition
from lib.types import StepFunc

if TYPE_CHECKING:
    from lib.config import SetupConfig


PLUGIN = PluginDefinition(
    name="cachyos",
    module=__name__,
    plugin_kind="composition",
    dependencies=("core",),
    system_types=(SystemTypeDefinition(
        name="agent_cachyos",
        description="Local coding tools for existing CachyOS + KDE",
        order=33,
        default_auto_restart=False,
        default_auto_restart_force_days=0,
        default_agent_tools=("gh", "codex"),
        step_builder="plugins.cachyos:build_cachyos_steps",
    ),),
)


def build_cachyos_steps(config: SetupConfig) -> list[tuple[str, StepFunc]]:
    from common.cachyos_cleanup import cleanup_cachyos_packages
    from common.cachyos_steps import (
        install_cachyos_packages,
        install_cachyos_agents,
        install_cachyos_skills,
        prepare_cachyos_workspace,
        install_cachyos_t3,
        install_cachyos_t3_desktop,
        report_cachyos_readiness,
        reconcile_cachyos_user_cache,
        save_cachyos_setup,
        report_cachyos_host_health,
    )
    from lib.cachyos import validate_cachyos_config
    from common.cachyos_firewall import configure_firewall, firewall_requested
    from common.cachyos_software import install_software, selected_software
    from common.cachyos_development import install_cachyos_node_versions
    from common.cachyos_sunshine import configure as configure_sunshine

    validate_cachyos_config(config)
    steps = [
        ("Observing workstation health (read-only)", report_cachyos_host_health),
        ("Installing missing workstation packages (no system upgrade)", install_cachyos_packages),
        ("Installing or updating coding agents for the current user", install_cachyos_agents),
        ("Preparing the local coding workspace", prepare_cachyos_workspace),
    ]
    if firewall_requested(config):
        steps.insert(2, ("Restricting workstation inbound access with UFW", configure_firewall))
    if config.install_node_versions:
        steps.insert(2, ("Preparing user-local NVM for project Node versions", install_cachyos_node_versions))
    if selected_software(config):
        steps.append(("Installing selected publishing and material tools (AUR review required)", install_software))
    if config.t3code_desktop:
        steps.append(("Installing T3 Code desktop and enabling KDE login startup (disables managed web service)", install_cachyos_t3_desktop))
    if config.web_interfaces:
        steps.append(("Installing or updating CachyOS T3 Code user service", install_cachyos_t3))
    if config.install_sunshine:
        steps.append(("Enabling Sunshine at KDE login and starting streaming host", configure_sunshine))
    steps.append(("Installing CachyOS workstation skills", install_cachyos_skills))
    steps.append(("Checking local coding tool readiness", report_cachyos_readiness))
    steps.append(("Pruning old CachyOS package downloads (keeps three versions and 30 days)", cleanup_cachyos_packages))
    steps.append(("Reconciling developer-tool caches", reconcile_cachyos_user_cache))
    steps.append(("Saving successful local setup for basaltw refresh", save_cachyos_setup))
    return steps
