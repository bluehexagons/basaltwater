"""Proxmox-oriented built-in plugin definitions and step builders."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lib.plugin_registry import PluginDefinition, SystemTypeDefinition

if TYPE_CHECKING:
    from lib.config import SetupConfig
    from lib.types import StepFunc


PLUGIN = PluginDefinition(
    name="proxmox",
    module=__name__,
    plugin_kind="composition",
    dependencies=("common", "core", "security"),
    system_types=(
        SystemTypeDefinition(
            name="server_proxmox",
            description="Proxmox host server",
            order=70,
            default_auto_restart=False,
            # A hypervisor restart affects every guest. Operators must opt in
            # to a forced deadline with --auto-restart-force-days.
            default_auto_restart_force_days=0,
            step_builder="plugins.proxmox:build_server_proxmox_steps",
        ),
    ),
)


def build_server_proxmox_steps(config: SetupConfig) -> list[tuple[str, StepFunc]]:
    """Build the dedicated Proxmox hardening flow."""

    from common.proxmox_steps import (
        configure_proxmox_balloon_target,
        configure_proxmox_host_memory_safety,
    )
    from common.steps import check_restart_required, configure_swap
    from common.setup_maintenance import run_setup_maintenance
    from security.steps import (
        configure_auto_restart,
        configure_auto_updates,
        configure_cleanup_maintenance,
        configure_fail2ban,
        configure_security_monitor,
        configure_proxmox_management_firewall,
        create_remoteusers_group,
        harden_kernel,
        harden_ssh,
    )

    steps = [
        ("Creating remoteusers group", create_remoteusers_group),
        ("Configuring swap", configure_swap),
        (
            "Configuring Proxmox host memory safety",
            configure_proxmox_host_memory_safety,
        ),
        (
            "Configuring Proxmox memory balloon target",
            configure_proxmox_balloon_target,
        ),
        ("Hardening SSH configuration", harden_ssh),
        ("Hardening kernel parameters", harden_kernel),
        ("Configuring fail2ban (sshd jail)", configure_fail2ban),
        ("Configuring security event monitor", configure_security_monitor),
        ("Configuring automatic security updates", configure_auto_updates),
        ("Configuring cleanup maintenance service", configure_cleanup_maintenance),
        ("Configuring automatic restart service", configure_auto_restart),
        ("Running setup maintenance", run_setup_maintenance),
        ("Checking if restart required", check_restart_required),
    ]
    if (
        config.effective_access_sources()
        or config.clear_access_sources
        or config.clear_lan_access
    ):
        steps.insert(
            2,
            (
                "Configuring Proxmox management access filter",
                configure_proxmox_management_firewall,
            ),
        )
    return steps
