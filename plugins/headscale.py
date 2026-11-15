"""Independent native controller, client and resolver setup capabilities."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lib.plugin_registry import PluginDefinition

if TYPE_CHECKING:
    from lib.config import SetupConfig
    from lib.types import StepFunc


PLUGIN = PluginDefinition(
    name="headscale", module=__name__, plugin_kind="capability",
    dependencies=("core", "security", "web"),
)


def prepare_tailscale_client(config: SetupConfig) -> None:
    """Internal admission/firewall/client path without profile persistence."""
    from common.headscale_preflight import preflight_network_services
    from common.tailscale_steps import configure_tailscale
    from security.steps import configure_firewall
    from lib.remote_utils import run

    if not config.tailscale or config.headscale or config.unbound or config.deploy_specs:
        raise ValueError("Client preparation requires only native client intent")
    preflight_network_services(config)
    active = run("ufw status 2>/dev/null | grep -q 'Status: active'", check=False)
    if active.returncode != 0:
        configure_firewall(config)
    # An active firewall belongs to the complete desired profile. Reconciliation
    # from this minimal preparation would remove its application access rules.
    configure_tailscale(config)


def extend_headscale_steps(config: SetupConfig, steps: list[tuple[str, StepFunc]]) -> None:
    """Compose native services before the portal exports installed features."""
    onboarding = bool(getattr(config, "headscale_onboarding_domain", None))
    native = any(getattr(config, name) is not None for name in ("headscale", "tailscale", "unbound"))
    if not native and not onboarding:
        return
    from common.headscale_preflight import preflight_network_services, validate_ingress
    from common.headscale_admin_steps import configure_headscale_administration
    from common.tailscale_steps import configure_tailscale
    from common.unbound_steps import configure_unbound
    from lib.headscale_config import validate_headscale_settings
    from security.steps import configure_firewall, configure_firewall_web
    from web.headscale_steps import configure_headscale
    from web.headscale_onboarding_steps import configure_headscale_onboarding, preflight_onboarding
    from web.ssl_steps import install_certbot
    from web.web_steps import install_nginx

    if native:
        validate_headscale_settings(config, complete=True)
    validate_ingress(config)
    steps.insert(0, ("Checking native network service prerequisites", preflight_network_services)
                 if native else ("Checking onboarding prerequisites", preflight_onboarding))
    functions = {function for _, function in steps}
    if any(getattr(config, name) for name in ("headscale", "tailscale", "unbound")):
        if not functions.intersection({configure_firewall, configure_firewall_web}):
            steps.append(("Configuring firewall for native network services", configure_firewall))
    if config.headscale or onboarding:
        if install_nginx not in functions:
            steps.append(("Installing nginx for Headscale", install_nginx))
        if install_certbot not in functions:
            steps.append(("Installing certificate tools for Headscale", install_certbot))
    if config.headscale is not None:
        steps.append(("Reconciling native Headscale hosting", configure_headscale))
    if config.headscale:
        steps.append(("Checking native Headscale administration", configure_headscale_administration))
    # Withdrawal is a separate operation. Stop an unadvertised resolver before
    # stopping its client; the shared lease refuses either while advertised.
    if config.unbound is False:
        steps.append(("Disabling managed Unbound", configure_unbound))
    if config.tailscale is not None:
        steps.append(("Reconciling native Tailscale client", configure_tailscale))
    if config.unbound:
        steps.append(("Reconciling managed Unbound", configure_unbound))
    if onboarding:
        steps.append(("Publishing static Headscale onboarding", configure_headscale_onboarding))
