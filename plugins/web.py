"""Web capability composition, sharing the managed build services."""

from __future__ import annotations

from typing import TYPE_CHECKING, Mapping

from lib.plugin_registry import PluginDefinition

if TYPE_CHECKING:
    from lib.config import SetupConfig
    from lib.types import StepFunc

_WEB_STEPS = (
    "configure_auto_update_node", "install_certbot", "configure_cloudflare_firewall",
    "create_cloudflared_config_directory", "configure_nginx_for_cloudflare",
    "install_cloudflared_service_helper", "run_cloudflare_tunnel_setup",
    "install_nginx", "configure_nginx_security", "create_hello_world_site", "configure_default_site",
)
PLUGIN = PluginDefinition(
    name="web", module=__name__, plugin_kind="capability", dependencies=("core",),
    custom_steps=(*_WEB_STEPS, "configure_build_host", "configure_build_target", "configure_webhook_builds"),
    custom_step_provider="plugins.web:get_custom_step_functions",
)


def get_web_server_steps() -> list[tuple[str, StepFunc]]:
    from web.steps import configure_default_site, configure_nginx_security, create_hello_world_site, install_nginx

    return [
        ("Installing nginx", install_nginx),
        ("Configuring nginx security settings", configure_nginx_security),
        ("Creating Hello World website", create_hello_world_site),
        ("Configuring default site", configure_default_site),
    ]


def extend_build_target_steps(config: SetupConfig, steps: list[tuple[str, StepFunc]]) -> None:
    if config.build_target_enrollment is not None or config.disable_build_target:
        from common.build_target_steps import configure_build_target

        steps.append(("Configuring managed target deployment access", configure_build_target))


def extend_build_server_steps(config: SetupConfig, steps: list[tuple[str, StepFunc]]) -> None:
    from common.build_host_steps import configure_build_host
    from web.webhook_build_steps import configure_webhook_builds

    if config.is_build_server or config.disable_build_host:
        if config.is_build_server and config.build_host_enrollment is None:
            raise ValueError("Managed builder enrollment must be prepared by the setup controller")
        steps.append(("Configuring managed build host", configure_build_host))
        # Reconcile removal too: an old receiver must not survive --no-webhook-builds.
        steps.append(("Configuring signed build webhooks", configure_webhook_builds))
    if config.enable_github_runner or config.disable_github_runner:
        from web.github_runner_steps import check_runner_platform, setup_github_runner

        steps.extend([
            ("Checking GitHub runner prerequisites", check_runner_platform),
            ("Configuring GitHub runner", setup_github_runner),
        ])


def get_custom_step_functions() -> Mapping[str, StepFunc]:
    from common.build_host_steps import configure_build_host
    from common.build_target_steps import configure_build_target
    from web.webhook_build_steps import configure_webhook_builds
    from web import steps

    return dict(
        {name: getattr(steps, name) for name in _WEB_STEPS},
        configure_build_host=configure_build_host,
        configure_build_target=configure_build_target,
        configure_webhook_builds=configure_webhook_builds,
    )
