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
    from common.cachyos_steps import (
        install_cachyos_packages,
        install_cachyos_agents,
        install_cachyos_skills,
        prepare_cachyos_workspace,
        install_cachyos_t3,
        report_cachyos_readiness,
        reconcile_cachyos_user_cache,
    )
    from lib.cachyos import validate_cachyos_config

    validate_cachyos_config(config)
    steps = [
        ("Installing missing workstation packages (no system upgrade)", install_cachyos_packages),
        ("Installing or updating coding agents for the current user", install_cachyos_agents),
        ("Preparing the local coding workspace", prepare_cachyos_workspace),
        ("Installing CachyOS workstation skills", install_cachyos_skills),
    ]
    if config.web_interfaces:
        steps.append(("Installing localhost T3 Code user service", install_cachyos_t3))
    steps.append(("Reconciling developer-tool caches", reconcile_cachyos_user_cache))
    steps.append(("Checking local coding tool readiness", report_cachyos_readiness))
    return steps
