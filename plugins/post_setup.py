"""Compose optional target-side work after the base system profile."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Optional

from lib.plugin_registry import PluginDefinition
from lib.github_runner_config import PROFILE as GITHUB_RUNNER_PROFILE

if TYPE_CHECKING:
    from lib.config import SetupConfig
    from lib.types import Deployments, StepFunc


PLUGIN = PluginDefinition(
    name="post_setup",
    module=__name__,
    plugin_kind="capability",
    dependencies=("core",),
)


RepositorySource = Callable[[str, str, bool], tuple[str, str]]


def build_post_setup_steps(
    config: SetupConfig,
    repository_source: RepositorySource,
) -> list[tuple[str, StepFunc]]:
    """Return the exact optional plan, sharing deploy results between its steps."""
    steps: list[tuple[str, StepFunc]] = []
    deployments: Deployments = []

    if config.enable_cloudflare:
        def prepare_cloudflare(_config: SetupConfig) -> None:
            from web.cloudflare_steps import (
                create_cloudflared_config_directory,
                configure_nginx_for_cloudflare,
                install_cloudflared_service_helper,
            )

            create_cloudflared_config_directory(_config)
            configure_nginx_for_cloudflare(_config)
            install_cloudflared_service_helper(_config)
            print("  Direct web access policy will be applied after cloudflared is verified active")

        steps.append(("Preparing Cloudflare tunnel support", prepare_cloudflare))

    if config.deploy_specs:
        public_udp = []

        def deploy_repositories(_config: SetupConfig) -> None:
            from deploy.deploy_steps import deploy_repository
            from lib.project_manifest import collect_public_udp, load_manifest

            prepared = []
            declared = []
            for deploy_specs_str, git_url in _config.deploy_specs or []:
                source_path, commit_hash = repository_source(
                    git_url, _config.deployment_mode, _config.dry_run,
                )
                manifest = load_manifest(source_path)
                for deploy_spec in deploy_specs_str.split(","):
                    deploy_spec = deploy_spec.strip()
                    if not deploy_spec:
                        continue
                    declared.append((deploy_spec, manifest))
                    prepared.append((source_path, deploy_spec, git_url, commit_hash))
            public_udp[:] = collect_public_udp(declared)
            for source_path, deploy_spec, git_url, commit_hash in prepared:
                infos = deploy_repository(
                    source_path=source_path,
                    deploy_spec=deploy_spec,
                    git_url=git_url,
                    commit_hash=commit_hash,
                    full_deploy=_config.full_deploy,
                    keep_source=True,
                )
                if infos:
                    deployments.extend(infos)

        def configure_udp(_config: SetupConfig) -> None:
            from deploy.network_steps import configure_public_udp

            configure_public_udp(public_udp)

        def configure_nginx(_config: SetupConfig) -> None:
            if not deployments:
                return
            from lib.nginx_config import create_nginx_sites_for_groups

            grouped: dict[Optional[str], Deployments] = {}
            for dep in deployments:
                grouped.setdefault(dep.get("domain"), []).append(dep)
            create_nginx_sites_for_groups(
                grouped,
                enable_https_redirect=not _config.enable_cloudflare,
            )

        steps.extend((
            ("Deploying uploaded repositories", deploy_repositories),
            ("Configuring Nginx for deployed routes, if any", configure_nginx),
            ("Configuring manifest public UDP access, if any", configure_udp),
        ))

        # Cloudflare supplies public HTTPS; this origin is reached over local
        # HTTP. Do not block tunnel activation on unnecessary ACME validation.
        if config.enable_ssl and not config.enable_cloudflare:
            def configure_ssl(_config: SetupConfig) -> None:
                if not deployments:
                    return
                from web.ssl_steps import install_certbot, setup_ssl_for_deployments

                install_certbot(_config)
                setup_ssl_for_deployments(
                    deployments,
                    _config.ssl_email,
                    enable_https_redirect=not _config.enable_cloudflare,
                )

            steps.append(("Configuring certificates for deployed routes, if any", configure_ssl))

    if config.enable_cloudflare:
        def activate_cloudflare(_config: SetupConfig) -> None:
            from web.cloudflare_steps import (
                configure_cloudflare_firewall,
                run_cloudflare_tunnel_setup,
            )

            if run_cloudflare_tunnel_setup(_config, allow_create=True):
                configure_cloudflare_firewall(_config)
            else:
                print("  ℹ Direct HTTP/HTTPS access retained until a tunnel is configured")

        steps.append(("Verifying Cloudflare tunnel activation", activate_cloudflare))

    if config.enable_samba:
        def configure_samba(_config: SetupConfig) -> None:
            from smb.samba_steps import (
                install_samba,
                configure_samba_firewall,
                configure_samba_global_settings,
                configure_samba_fail2ban,
                reconcile_samba_shares,
            )

            install_samba(_config)
            configure_samba_global_settings(_config)
            configure_samba_firewall(_config)
            configure_samba_fail2ban(_config)
            reconcile_samba_shares(_config)

        steps.append(("Configuring Samba and reconciling shares", configure_samba))

    if config.smb_mounts:
        def configure_smb_mounts(_config: SetupConfig) -> None:
            from smb.smb_mount_steps import configure_smb_mount

            for mount_spec in _config.smb_mounts or []:
                configure_smb_mount(_config, mount_spec=mount_spec)

        steps.append(("Configuring SMB mounts", configure_smb_mounts))

    if config.sync_specs or config.backup_specs or config.scrub_specs:
        def configure_storage_operations(_config: SetupConfig) -> None:
            from sync.sync_steps import install_rsync
            from sync.scrub_steps import install_par2
            from sync.storage_ops_steps import (
                create_storage_ops_service,
                schedule_storage_ops_update,
            )

            if _config.sync_specs or _config.backup_specs:
                install_rsync(_config)
            if _config.scrub_specs:
                install_par2(_config)
            create_storage_ops_service(_config)
            schedule_storage_ops_update()

        steps.append(("Configuring storage operations service", configure_storage_operations))

    maintenance_requested = config.maintenance_agent is not None or config.web_panel_port is not None or any(
        getattr(config, name) is not None for name in config.__dataclass_fields__ if name.startswith("maintenance_agent_")
    )
    if config.system_type != GITHUB_RUNNER_PROFILE and maintenance_requested:
        from common.maintenance_agent_steps import configure_maintenance_agent

        steps.append(("Configuring tailored maintenance agent", configure_maintenance_agent))

    return steps
