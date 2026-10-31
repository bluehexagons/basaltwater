"""Security built-in capability plugin definitions and step helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Mapping

from lib.plugin_registry import PluginDefinition

if TYPE_CHECKING:
    from lib.types import StepFunc


PLUGIN = PluginDefinition(
    name="security",
    module=__name__,
    plugin_kind="capability",
    dependencies=("core",),
    custom_steps=(
        "create_remoteusers_group",
        "configure_firewall",
        "configure_fail2ban",
        "harden_ssh",
        "harden_kernel",
        "configure_auto_updates",
        "configure_firewall_web",
        "configure_auto_restart",
        "configure_cleanup_maintenance",
        "configure_login_banners",
        "configure_apparmor",
        "configure_auditd",
        "configure_pam_lockout",
        "configure_security_monitor",
        "configure_proxmox_management_firewall",
    ),
    custom_step_provider="plugins.security:get_custom_step_functions",
)


def get_security_steps(*, lite: bool) -> list[tuple[str, StepFunc]]:
    """Return early hardening; compose authentication jails after desktop setup."""

    from security.steps import (
        configure_apparmor,
        configure_auditd,
        configure_auto_restart,
        configure_auto_updates,
        configure_cleanup_maintenance,
        configure_firewall,
        configure_login_banners,
        configure_pam_lockout,
        configure_security_monitor,
        harden_kernel,
        harden_ssh,
    )

    steps: list[tuple[str, StepFunc]] = []
    if not lite:
        steps.append(("Configuring firewall", configure_firewall))

    steps.extend(
        [
            ("Hardening SSH configuration", harden_ssh),
            ("Hardening kernel parameters", harden_kernel),
            ("Configuring login banners", configure_login_banners),
            ("Configuring AppArmor policy", configure_apparmor),
            ("Configuring auditd (kernel audit framework)", configure_auditd),
            ("Configuring PAM account lockout", configure_pam_lockout),
            ("Configuring security event monitor", configure_security_monitor),
            ("Configuring automatic security updates", configure_auto_updates),
            ("Configuring cleanup maintenance service", configure_cleanup_maintenance),
            ("Configuring automatic restart service", configure_auto_restart),
        ]
    )
    return steps


def get_fail2ban_steps() -> list[tuple[str, StepFunc]]:
    """Activate jails after their optional XRDP log sources are installed."""
    from security.steps import configure_fail2ban

    return [("Configuring fail2ban authentication jails", configure_fail2ban)]


def get_web_firewall_steps() -> list[tuple[str, StepFunc]]:
    """Return the additional firewall steps for web-facing systems."""

    from security.steps import configure_firewall_web

    return [("Configuring firewall for web server", configure_firewall_web)]


def get_custom_step_functions() -> Mapping[str, StepFunc]:
    """Return plugin-owned custom step functions exported by the security capability."""

    from security.steps import (
        configure_apparmor,
        configure_auditd,
        configure_auto_restart,
        configure_auto_updates,
        configure_cleanup_maintenance,
        configure_fail2ban,
        configure_firewall,
        configure_firewall_web,
        configure_login_banners,
        configure_pam_lockout,
        configure_proxmox_management_firewall,
        configure_security_monitor,
        create_remoteusers_group,
        harden_kernel,
        harden_ssh,
    )

    return {
        "create_remoteusers_group": create_remoteusers_group,
        "configure_firewall": configure_firewall,
        "configure_fail2ban": configure_fail2ban,
        "harden_ssh": harden_ssh,
        "harden_kernel": harden_kernel,
        "configure_auto_updates": configure_auto_updates,
        "configure_firewall_web": configure_firewall_web,
        "configure_auto_restart": configure_auto_restart,
        "configure_cleanup_maintenance": configure_cleanup_maintenance,
        "configure_login_banners": configure_login_banners,
        "configure_apparmor": configure_apparmor,
        "configure_auditd": configure_auditd,
        "configure_pam_lockout": configure_pam_lockout,
        "configure_security_monitor": configure_security_monitor,
        "configure_proxmox_management_firewall": configure_proxmox_management_firewall,
    }
