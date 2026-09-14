"""Common built-in capability plugin definitions and step helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Mapping

from lib.plugin_registry import PluginDefinition

if TYPE_CHECKING:
    from lib.config import SetupConfig
    from lib.types import StepFunc


PLUGIN = PluginDefinition(
    name="common",
    module=__name__,
    plugin_kind="capability",
    dependencies=("core",),
    custom_steps=(
        "install_go",
        "install_node",
        "install_python",
        "ensure_python_alias",
        "install_godot",
        "install_godot_bundles",
        "install_github_cli",
        "install_codex",
        "configure_codex_auth_maintenance",
        "install_claude",
        "install_opencode",
        "update_managed_agent_tools",
        "install_agent_cli_launcher",
        "install_agent_workflow_skills",
        "install_t3code_web",
        "configure_web_panel",
        "reconcile_agent_storage",
        "copy_agent_tooling_payload",
        "configure_codex_security_policy",
        "install_browser_automation",
        "install_git_for_agent_repositories",
        "configure_git_https_credentials",
        "install_git_lfs_for_agent_repositories",
        "clone_agent_repositories",
        "configure_auto_update_uv",
        "configure_auto_update_godot",
        "update_and_upgrade_packages",
        "check_debian_package_sources",
        "ensure_sudo_installed",
        "configure_locale",
        "configure_ipv4_preference",
        "configure_system_hostname",
        "configure_mdns",
        "configure_static_network",
        "setup_user",
        "configure_agent_user_security",
        "copy_ssh_keys_to_user",
        "generate_ssh_key",
        "configure_time_sync",
        "install_cli_tools",
        "install_control_plane_tools",
        "check_restart_required",
        "configure_auto_update_gogs",
        "install_mail_utils",
        "configure_swap",
        "install_apt_packages",
        "install_flatpak_packages",
    ),
    custom_step_provider="plugins.common:get_custom_step_functions",
)


def get_common_steps(config: SetupConfig) -> list[tuple[str, StepFunc]]:
    """Return the shared foundation steps for built-in setup flows."""

    from common.steps import (
        configure_ipv4_preference,
        configure_locale,
        configure_system_hostname,
        configure_swap,
        configure_time_sync,
        copy_ssh_keys_to_user,
        ensure_sudo_installed,
        generate_ssh_key,
        setup_user,
        update_and_upgrade_packages,
    )
    from security.steps import create_remoteusers_group
    from common.storage_steps import setup_vm_storage
    from common.agent_security_steps import configure_agent_user_security
    from lib.vm_storage import has_home_mount

    storage_step = (
        [("Preparing VM data storage", setup_vm_storage)]
        if config.storage_mounts
        else []
    )
    steps: list[tuple[str, StepFunc]] = [
        ("Updating and upgrading packages", update_and_upgrade_packages),
        ("Ensuring sudo is installed", ensure_sudo_installed),
        ("Configuring UTF-8 locale", configure_locale),
        ("Configuring IPv4 preference", configure_ipv4_preference),
        ("Creating remoteusers group", create_remoteusers_group),
        *(storage_step if has_home_mount(config) else []),
        ("Setting up user", setup_user),
        *(storage_step if not has_home_mount(config) else []),
        ("Configuring agent user security", configure_agent_user_security),
        ("Copying SSH keys to user", copy_ssh_keys_to_user),
        ("Generating SSH key for user", generate_ssh_key),
        ("Configuring time synchronization", configure_time_sync),
        ("Configuring swap", configure_swap),
    ]
    if config.system_hostname:
        steps.insert(4, ("Configuring system hostname", configure_system_hostname))
    return steps


def get_cli_steps() -> list[tuple[str, StepFunc]]:
    """Return the shared CLI-tool steps for built-in setup flows."""

    from common.steps import install_cli_tools

    return [("Installing CLI tools", install_cli_tools)]


def extend_control_plane_steps(config: SetupConfig, steps: list[tuple[str, StepFunc]]) -> None:
    """Append the administrator-tool bundle for infrastructure control hosts."""

    if not config.include_control_plane_tools:
        return

    from common.steps import install_control_plane_tools

    steps.append(
        ("Installing control-plane administrator tools", install_control_plane_tools)
    )


def get_final_steps(config: SetupConfig) -> list[tuple[str, StepFunc]]:
    """Return the standard final verification steps for built-in setup flows."""

    from common.steps import (
        check_restart_required,
        configure_static_network,
        ensure_python_alias,
    )

    steps: list[tuple[str, StepFunc]] = []
    if config.static_ipv4 or config.static_ipv6:
        steps.append(("Staging static network configuration", configure_static_network))
    steps.append(("Ensuring python command alias", ensure_python_alias))
    steps.append(("Checking if restart required", check_restart_required))
    return steps


def extend_runtime_steps(config: SetupConfig, steps: list[tuple[str, StepFunc]]) -> None:
    """Append optional language-runtime steps in the established order."""

    from common.steps import (
        configure_auto_update_uv,
        install_av_tools,
        install_data_analysis_tools,
        install_gl_tools,
        install_go,
        install_node,
        install_python,
    )
    from web.steps import configure_auto_update_node

    if config.install_go:
        steps.append(("Installing Go (latest version)", install_go))
    if config.install_node:
        steps.append(("Installing Node.js (nvm + latest LTS + PNPM)", install_node))
        steps.append(("Configuring Node.js auto-update", configure_auto_update_node))
    if config.install_python:
        steps.append(("Installing Python tooling (aliases + uv)", install_python))
        steps.append(("Configuring uv auto-update", configure_auto_update_uv))
    if config.install_data_analysis_tools:
        steps.append(("Installing data-analysis tools", install_data_analysis_tools))
    if config.install_av_tools:
        steps.append(("Installing image, audio, and video tools", install_av_tools))
    if config.install_gl_tools:
        steps.append(("Installing OpenGL tools", install_gl_tools))
    if config.install_godot:
        from common.godot_steps import (
            configure_auto_update_godot,
            install_godot,
            install_godot_bundles,
        )

        steps.append(("Installing Godot Engine (latest stable)", install_godot))
        if config.godot_bundles:
            bundle_names = ", ".join(config.godot_bundles)
            steps.append(
                (f"Installing Godot bundles ({bundle_names})", install_godot_bundles)
            )
        steps.append(("Configuring Godot auto-update", configure_auto_update_godot))


def extend_package_steps(config: SetupConfig, steps: list[tuple[str, StepFunc]]) -> None:
    """Append optional package-install and notification steps."""

    from common.steps import (
        configure_mdns,
        install_apt_packages,
        install_flatpak_packages,
        install_mail_utils,
    )

    if config.apt_packages:
        steps.append(("Installing custom apt packages", install_apt_packages))

    if config.enable_mdns or config.clear_mdns:
        steps.append(("Configuring mDNS hostname discovery", configure_mdns))

    if config.flatpak_packages:
        steps.append(("Installing custom flatpak packages", install_flatpak_packages))

    if config.notify_specs:
        steps.append(("Installing mail utilities for notifications", install_mail_utils))


def extend_agent_steps(config: SetupConfig, steps: list[tuple[str, StepFunc]]) -> None:
    """Append optional agent tooling and target-side workspace steps."""

    from common.agent_steps import (
        copy_agent_tooling_payload,
        clone_agent_repositories,
        configure_codex_auth_maintenance,
        install_agent_cli_launcher,
        install_agent_workflow_skills,
        install_git_for_agent_repositories,
        install_git_lfs_for_agent_repositories,
        install_claude,
        install_codex,
        install_github_cli,
        install_opencode,
        reconcile_agent_storage,
        update_managed_agent_tools,
    )
    from common.agent_security_steps import configure_codex_security_policy
    from common.t3code_steps import install_t3code_web

    if config.has_agent_features():
        steps.append(
            ("Installing agent VM management command", install_agent_cli_launcher)
        )
        if {"codex", "opencode"}.intersection(config.selected_agent_tools()):
            steps.append(
                (
                    "Installing managed agent workflow skills",
                    install_agent_workflow_skills,
                )
            )

    if config.install_gh:
        steps.append(("Installing GitHub CLI", install_github_cli))

    if config.install_codex:
        steps.append(("Installing Codex CLI", install_codex))

    if config.install_claude:
        steps.append(("Installing Claude Code", install_claude))

    if config.install_opencode:
        steps.append(("Installing OpenCode", install_opencode))

    # Install provider configuration and credentials before starting an
    # interface that probes those providers during its initial discovery.
    if config.agent_payload:
        steps.append(("Copying agent tool configuration", copy_agent_tooling_payload))

    if config.install_codex:
        steps.append(
            ("Configuring Codex security policy", configure_codex_security_policy)
        )
        steps.append(
            (
                "Configuring Codex authentication maintenance",
                configure_codex_auth_maintenance,
            )
        )

    git_needed = bool(
        config.agent_repos
        or config.install_git_lfs
        or config.git_credentials
        or config.git_ca_pems
        or config.clear_git_credentials
        or (config.web_interfaces and config.git_access != "none")
    )
    if git_needed:
        steps.append(("Installing Git for agent repositories", install_git_for_agent_repositories))

    if config.git_credentials or config.git_ca_pems or config.clear_git_credentials:
        from common.git_credential_steps import configure_git_https_credentials

        steps.append(
            (
                "Configuring managed Git HTTPS credentials",
                configure_git_https_credentials,
            )
        )

    if "t3code" in (config.web_interfaces or []):
        steps.append(("Installing T3 Code web interface", install_t3code_web))

    if config.has_agent_features():
        steps.append(("Reconciling agent storage", reconcile_agent_storage))

    # Run the agent updater after T3 and storage reconciliation so its
    # post-update readiness record observes the complete setup state.
    if {"codex", "claude", "opencode"}.intersection(
        config.selected_agent_tools()
    ):
        steps.append(("Updating managed agent tools", update_managed_agent_tools))

    if config.browser_automation:
        from common.browser_automation_steps import install_browser_automation

        steps.append(("Installing agent browser automation", install_browser_automation))
    elif config.has_agent_features() and not config.disable_browser_automation:
        from common.browser_automation_steps import (
            reconcile_existing_browser_automation,
        )

        steps.append(
            (
                "Reconciling existing agent browser automation",
                reconcile_existing_browser_automation,
            )
        )

    if config.install_git_lfs:
        steps.append(
            (
                "Installing Git LFS for agent repositories",
                install_git_lfs_for_agent_repositories,
            )
        )

    if config.agent_repos:
        steps.append(("Cloning agent repositories on target", clone_agent_repositories))


def extend_web_panel_steps(
    config: SetupConfig,
    steps: list[tuple[str, StepFunc]],
) -> None:
    """Append the optional authenticated web panel."""

    if config.web_panel_port is None and not config.disable_web_panel:
        return

    from common.web_panel_steps import configure_web_panel

    action = "Removing" if config.disable_web_panel else "Configuring"
    steps.append((f"{action} infra-tools web panel", configure_web_panel))


def get_custom_step_functions() -> Mapping[str, StepFunc]:
    """Return plugin-owned custom step functions exported by the common capability."""

    from common.steps import (
        check_restart_required,
        configure_auto_update_gogs,
        configure_auto_update_uv,
        configure_ipv4_preference,
        configure_locale,
        configure_mdns,
        configure_static_network,
        configure_system_hostname,
        configure_swap,
        configure_time_sync,
        copy_ssh_keys_to_user,
        check_debian_package_sources,
        ensure_sudo_installed,
        generate_ssh_key,
        install_apt_packages,
        install_cli_tools,
        install_control_plane_tools,
        install_flatpak_packages,
        install_go,
        install_mail_utils,
        install_node,
        install_python,
        ensure_python_alias,
        setup_user,
        update_and_upgrade_packages,
    )
    from common.agent_steps import (
        copy_agent_tooling_payload,
        clone_agent_repositories,
        configure_codex_auth_maintenance,
        install_agent_cli_launcher,
        install_agent_workflow_skills,
        install_git_for_agent_repositories,
        install_git_lfs_for_agent_repositories,
        install_claude,
        install_codex,
        install_github_cli,
        install_opencode,
        update_managed_agent_tools,
        reconcile_agent_storage,
    )
    from common.agent_security_steps import (
        configure_agent_user_security,
        configure_codex_security_policy,
    )
    from common.t3code_steps import install_t3code_web
    from common.web_panel_steps import configure_web_panel
    from common.git_credential_steps import configure_git_https_credentials
    from common.browser_automation_steps import install_browser_automation
    from common.godot_steps import (
        configure_auto_update_godot,
        install_godot,
        install_godot_bundles,
    )

    return {
        "install_go": install_go,
        "install_node": install_node,
        "install_python": install_python,
        "ensure_python_alias": ensure_python_alias,
        "install_godot": install_godot,
        "install_godot_bundles": install_godot_bundles,
        "install_github_cli": install_github_cli,
        "install_codex": install_codex,
        "configure_codex_auth_maintenance": configure_codex_auth_maintenance,
        "install_claude": install_claude,
        "install_opencode": install_opencode,
        "update_managed_agent_tools": update_managed_agent_tools,
        "install_agent_cli_launcher": install_agent_cli_launcher,
        "install_agent_workflow_skills": install_agent_workflow_skills,
        "install_t3code_web": install_t3code_web,
        "configure_web_panel": configure_web_panel,
        "reconcile_agent_storage": reconcile_agent_storage,
        "copy_agent_tooling_payload": copy_agent_tooling_payload,
        "configure_codex_security_policy": configure_codex_security_policy,
        "install_browser_automation": install_browser_automation,
        "clone_agent_repositories": clone_agent_repositories,
        "install_git_for_agent_repositories": install_git_for_agent_repositories,
        "configure_git_https_credentials": configure_git_https_credentials,
        "install_git_lfs_for_agent_repositories": install_git_lfs_for_agent_repositories,
        "configure_auto_update_uv": configure_auto_update_uv,
        "configure_auto_update_godot": configure_auto_update_godot,
        "update_and_upgrade_packages": update_and_upgrade_packages,
        "check_debian_package_sources": check_debian_package_sources,
        "ensure_sudo_installed": ensure_sudo_installed,
        "configure_locale": configure_locale,
        "configure_ipv4_preference": configure_ipv4_preference,
        "configure_system_hostname": configure_system_hostname,
        "configure_mdns": configure_mdns,
        "configure_static_network": configure_static_network,
        "setup_user": setup_user,
        "configure_agent_user_security": configure_agent_user_security,
        "copy_ssh_keys_to_user": copy_ssh_keys_to_user,
        "generate_ssh_key": generate_ssh_key,
        "configure_time_sync": configure_time_sync,
        "install_cli_tools": install_cli_tools,
        "install_control_plane_tools": install_control_plane_tools,
        "check_restart_required": check_restart_required,
        "configure_auto_update_gogs": configure_auto_update_gogs,
        "install_mail_utils": install_mail_utils,
        "configure_swap": configure_swap,
        "install_apt_packages": install_apt_packages,
        "install_flatpak_packages": install_flatpak_packages,
    }
