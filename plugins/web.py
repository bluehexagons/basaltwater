"""Web built-in capability plugin definitions and step helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Mapping

from lib.plugin_registry import PluginDefinition

if TYPE_CHECKING:
    from lib.config import SetupConfig
    from lib.types import StepFunc


PLUGIN = PluginDefinition(
    name="web",
    module=__name__,
    plugin_kind="capability",
    dependencies=("core",),
    custom_steps=(
        "configure_auto_update_node",
        "install_certbot",
        "configure_cloudflare_firewall",
        "create_cloudflared_config_directory",
        "configure_nginx_for_cloudflare",
        "install_cloudflared_service_helper",
        "run_cloudflare_tunnel_setup",
        "install_nginx",
        "configure_nginx_security",
        "create_hello_world_site",
        "configure_default_site",
        "install_cicd_dependencies",
        "create_cicd_user",
        "create_cicd_directories",
        "generate_webhook_secret",
        "create_default_webhook_config",
        "create_webhook_receiver_service",
        "create_cicd_executor_service",
        "configure_nginx_for_webhook",
        "update_cloudflare_tunnel_for_webhook",
        "install_webhook_manager_helper",
        "install_app_server_dependencies",
        "create_deploy_user",
        "configure_deploy_sudoers",
        "create_app_directories",
        "configure_deploy_ssh_access",
        "configure_app_nginx",
        "generate_deploy_ssh_key",
        "configure_deploy_targets",
        "configure_deploy_known_hosts",
        "create_build_workspace_dirs",
        "install_build_node",
        "install_build_python_tools",
        "install_build_dependencies",
        "configure_build_host",
    ),
    custom_step_provider="plugins.web:get_custom_step_functions",
)


def get_web_server_steps() -> list[tuple[str, StepFunc]]:
    """Return the default web-server setup steps."""

    from web.steps import (
        configure_default_site,
        configure_nginx_security,
        create_hello_world_site,
        install_nginx,
    )

    return [
        ("Installing nginx", install_nginx),
        ("Configuring nginx security settings", configure_nginx_security),
        ("Creating Hello World website", create_hello_world_site),
        ("Configuring default site", configure_default_site),
    ]


def extend_cicd_steps(config: SetupConfig, steps: list[tuple[str, StepFunc]]) -> None:
    """Append the CI/CD service setup steps when enabled."""

    from web.steps import (
        configure_nginx_for_webhook,
        create_cicd_directories,
        create_cicd_executor_service,
        create_cicd_user,
        create_default_webhook_config,
        create_webhook_receiver_service,
        generate_webhook_secret,
        install_cicd_dependencies,
        install_webhook_manager_helper,
        update_cloudflare_tunnel_for_webhook,
    )

    # The build-server extension owns legacy receiver setup and managed
    # on-demand enrollment, including rejection of unsupported combinations.
    if not config.enable_cicd or config.is_build_server:
        return

    steps.extend(
        [
            ("Installing CI/CD dependencies", install_cicd_dependencies),
            ("Creating CI/CD user", create_cicd_user),
            ("Creating CI/CD directories", create_cicd_directories),
            ("Generating webhook secret", generate_webhook_secret),
            ("Creating default webhook configuration", create_default_webhook_config),
            ("Creating webhook receiver service", create_webhook_receiver_service),
            ("Creating CI/CD executor service", create_cicd_executor_service),
            ("Configuring nginx for webhook endpoint", configure_nginx_for_webhook),
            ("Updating Cloudflare tunnel for webhook", update_cloudflare_tunnel_for_webhook),
            ("Installing webhook manager helper", install_webhook_manager_helper),
        ]
    )


def extend_app_server_steps(config: SetupConfig, steps: list[tuple[str, StepFunc]]) -> None:
    """Append app-server deployment steps when enabled."""

    from web.steps import (
        configure_app_nginx,
        configure_deploy_ssh_access,
        configure_deploy_sudoers,
        create_app_directories,
        create_deploy_user,
        install_app_server_dependencies,
    )

    if not config.is_app_server:
        return

    steps.extend(
        [
            ("Installing app server dependencies", install_app_server_dependencies),
            ("Creating deploy user", create_deploy_user),
            ("Configuring deploy sudoers", configure_deploy_sudoers),
            ("Creating app directories", create_app_directories),
            ("Configuring deploy SSH access", configure_deploy_ssh_access),
            ("Configuring nginx for app server", configure_app_nginx),
        ]
    )


def extend_build_server_steps(config: SetupConfig, steps: list[tuple[str, StepFunc]]) -> None:
    """Append build-server deployment steps when enabled."""

    if config.disable_build_host:
        from common.build_host_steps import configure_build_host

        steps.append(("Removing managed on-demand build host access", configure_build_host))
        return
    if not config.is_build_server:
        return
    if config.build_host_enrollment is not None:
        if config.enable_cicd:
            raise ValueError(
                "Managed on-demand build hosts cannot enable the legacy webhook "
                "executor; its shared-capacity adapter is not available yet"
            )
        from common.build_host_steps import configure_build_host

        steps.append(("Configuring managed on-demand build host", configure_build_host))
        return

    from web.steps import (
        configure_deploy_known_hosts,
        configure_deploy_targets,
        configure_nginx_for_webhook,
        create_build_workspace_dirs,
        create_cicd_executor_service,
        create_cicd_user,
        create_default_webhook_config,
        create_webhook_receiver_service,
        generate_deploy_ssh_key,
        generate_webhook_secret,
        install_build_node,
        install_build_python_tools,
        install_build_dependencies,
        install_cicd_dependencies,
        install_webhook_manager_helper,
        update_cloudflare_tunnel_for_webhook,
    )

    steps.extend(
        [
            ("Installing build dependencies", install_build_dependencies),
            ("Installing CI/CD dependencies", install_cicd_dependencies),
            ("Creating CI/CD user", create_cicd_user),
            ("Creating build workspace directories", create_build_workspace_dirs),
            *(
                [("Installing build-user Node.js", install_build_node)]
                if config.install_node
                else []
            ),
            *(
                [("Installing build-user Python tooling", install_build_python_tools)]
                if config.install_python
                else []
            ),
            ("Generating webhook secret", generate_webhook_secret),
            ("Creating default webhook configuration", create_default_webhook_config),
            ("Creating webhook receiver service", create_webhook_receiver_service),
            ("Creating CI/CD executor service", create_cicd_executor_service),
            ("Generating deploy SSH key", generate_deploy_ssh_key),
            ("Configuring deploy targets", configure_deploy_targets),
            ("Configuring deploy known hosts", configure_deploy_known_hosts),
            ("Configuring nginx for webhook endpoint", configure_nginx_for_webhook),
            ("Updating Cloudflare tunnel for webhook", update_cloudflare_tunnel_for_webhook),
            ("Installing webhook manager helper", install_webhook_manager_helper),
        ]
    )


def get_custom_step_functions() -> Mapping[str, StepFunc]:
    """Return plugin-owned custom step functions exported by the web capability."""

    from common.build_host_steps import configure_build_host

    from web.steps import (
        configure_app_nginx,
        configure_auto_update_node,
        configure_cloudflare_firewall,
        configure_deploy_known_hosts,
        configure_deploy_ssh_access,
        configure_deploy_sudoers,
        configure_deploy_targets,
        configure_default_site,
        configure_nginx_for_cloudflare,
        configure_nginx_for_webhook,
        configure_nginx_security,
        create_app_directories,
        create_build_workspace_dirs,
        create_cicd_directories,
        create_cicd_executor_service,
        create_cicd_user,
        create_cloudflared_config_directory,
        create_default_webhook_config,
        create_deploy_user,
        create_hello_world_site,
        create_webhook_receiver_service,
        generate_deploy_ssh_key,
        generate_webhook_secret,
        install_app_server_dependencies,
        install_build_node,
        install_build_python_tools,
        install_build_dependencies,
        install_certbot,
        install_cicd_dependencies,
        install_cloudflared_service_helper,
        install_nginx,
        install_webhook_manager_helper,
        run_cloudflare_tunnel_setup,
        update_cloudflare_tunnel_for_webhook,
    )

    return {
        "configure_auto_update_node": configure_auto_update_node,
        "install_certbot": install_certbot,
        "configure_cloudflare_firewall": configure_cloudflare_firewall,
        "create_cloudflared_config_directory": create_cloudflared_config_directory,
        "configure_nginx_for_cloudflare": configure_nginx_for_cloudflare,
        "install_cloudflared_service_helper": install_cloudflared_service_helper,
        "run_cloudflare_tunnel_setup": run_cloudflare_tunnel_setup,
        "install_nginx": install_nginx,
        "configure_nginx_security": configure_nginx_security,
        "create_hello_world_site": create_hello_world_site,
        "configure_default_site": configure_default_site,
        "install_cicd_dependencies": install_cicd_dependencies,
        "create_cicd_user": create_cicd_user,
        "create_cicd_directories": create_cicd_directories,
        "generate_webhook_secret": generate_webhook_secret,
        "create_default_webhook_config": create_default_webhook_config,
        "create_webhook_receiver_service": create_webhook_receiver_service,
        "create_cicd_executor_service": create_cicd_executor_service,
        "configure_nginx_for_webhook": configure_nginx_for_webhook,
        "update_cloudflare_tunnel_for_webhook": update_cloudflare_tunnel_for_webhook,
        "install_webhook_manager_helper": install_webhook_manager_helper,
        "install_app_server_dependencies": install_app_server_dependencies,
        "create_deploy_user": create_deploy_user,
        "configure_deploy_sudoers": configure_deploy_sudoers,
        "create_app_directories": create_app_directories,
        "configure_deploy_ssh_access": configure_deploy_ssh_access,
        "configure_app_nginx": configure_app_nginx,
        "generate_deploy_ssh_key": generate_deploy_ssh_key,
        "configure_deploy_targets": configure_deploy_targets,
        "configure_deploy_known_hosts": configure_deploy_known_hosts,
        "create_build_workspace_dirs": create_build_workspace_dirs,
        "install_build_node": install_build_node,
        "install_build_python_tools": install_build_python_tools,
        "install_build_dependencies": install_build_dependencies,
        "configure_build_host": configure_build_host,
    }
