#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.arg_parser import create_setup_argument_parser
from lib.config import SetupConfig
from lib.homebox_config import validate_homebox_settings
from lib.display import print_setup_summary
from lib.machine_state import (
    STATE_DIR,
    machine_type_context,
    resolve_machine_type,
    save_machine_state,
    save_setup_config,
)
from lib.notifications import send_setup_notification
from lib.operation_state import OperationRecord, OperationStateStore
from lib.remote_utils import detect_os, is_dry_run, set_dry_run
from lib.security_activity import record_setup_activity
from lib.setup_report import SetupReport, get_setup_report
from lib.validation import (
    validate_agent_repositories,
    validate_agent_git_settings,
    validate_browser_automation_settings,
    validate_backup_specs,
    validate_web_interface_settings,
    validate_gogs_settings,
    validate_network_setup_settings,
    validate_rdp_settings,
    validate_samba_share_credentials,
    validate_samba_share_specs,
    validate_smb_mount_specs,
    validate_swap_settings,
    validate_vm_storage_settings,
)
from lib.validators import validate_username
from lib.progress import progress_bar
from lib.system_types import get_steps_for_system_type
from typing import Optional
from lib.types import Deployments, StepFunc


REMOTE_AGENT_PAYLOAD_DIR = "/opt/infra_tools/agent_payload"
REMOTE_DEVICE_PAIRING_PAYLOAD_DIR = "/opt/infra_tools/device_pairing_payload"
REMOTE_WEB_PANEL_PAYLOAD_DIR = "/opt/infra_tools/web_panel_payload"
SETUP_OPERATION_FILE = os.path.join(STATE_DIR, "setup-operation.json")
_active_setup_operation: Optional[tuple[OperationStateStore, OperationRecord]] = None


def _record_security_setup_activity(record: OperationRecord, status: str) -> None:
    """Keep notification bookkeeping from changing the setup outcome."""
    try:
        record_setup_activity(record, status)
    except (OSError, ValueError) as exc:
        print(
            f"Warning: Could not record the managed setup audit window: {exc}",
            file=sys.stderr,
        )


def _begin_setup_operation(config: SetupConfig) -> None:
    global _active_setup_operation
    store = OperationStateStore(SETUP_OPERATION_FILE)
    context = {
        "machine_type": config.machine_type,
        "system_type": config.system_type,
        "username": config.username,
    }
    existing = store.load()
    matching_recovery = bool(
        existing is not None
        and existing.operation_type == "target_setup"
        and existing.resource == config.system_type
        and existing.status == "recovery_required"
        and existing.context.get("machine_type") == config.machine_type
        and existing.context.get("system_type") == config.system_type
        and existing.context.get("username") == config.username
    )
    if matching_recovery and existing is not None:
        prior_attempts = existing.context.get("recovery_attempt", 0)
        recovery_attempt = (
            prior_attempts + 1
            if isinstance(prior_attempts, int) and not isinstance(prior_attempts, bool)
            else 1
        )
        prior_step = existing.context.get("step")
        print(
            f"  Recovering failed setup operation {existing.operation_id}"
            + (f" after step {prior_step!r}" if isinstance(prior_step, str) else "")
            + "; rerunning the idempotent setup plan"
        )
        record = store.transition(
            existing.operation_id,
            "applying",
            context={
                **context,
                "recovery_attempt": recovery_attempt,
                "recovered_from": {
                    "error_type": existing.context.get("error_type"),
                    "phase": existing.phase,
                    "step": prior_step,
                },
            },
        )
    else:
        record = store.begin(
            "target_setup",
            config.system_type,
            "applying",
            context=context,
        )
    _active_setup_operation = (store, record)
    _record_security_setup_activity(record, "in_progress")


def _transition_setup_operation(phase: str, context: dict[str, object]) -> None:
    global _active_setup_operation
    if _active_setup_operation is None:
        return
    store, record = _active_setup_operation
    updated = store.transition(record.operation_id, phase, context=context)
    _active_setup_operation = (store, updated)


def _complete_setup_operation() -> None:
    global _active_setup_operation
    if _active_setup_operation is None:
        return
    store, record = _active_setup_operation
    _record_security_setup_activity(record, "succeeded")
    store.complete(record.operation_id)
    _active_setup_operation = None


def _record_setup_failure(error: Exception) -> None:
    global _active_setup_operation
    if _active_setup_operation is None:
        return
    store, record = _active_setup_operation
    updated = store.transition(
        record.operation_id,
        "recovery",
        status="recovery_required",
        context={**record.context, "error_type": type(error).__name__},
    )
    _record_security_setup_activity(updated, "failed")
    _active_setup_operation = (store, updated)


def _remove_secret_payloads() -> None:
    """Remove uploaded credentials after any setup outcome."""
    from lib.cachyos import is_cachyos

    if is_cachyos():
        return  # The local CachyOS profile never stages controller credentials.
    if is_dry_run():
        return
    for payload_dir in (
        REMOTE_AGENT_PAYLOAD_DIR,
        REMOTE_DEVICE_PAIRING_PAYLOAD_DIR,
        REMOTE_WEB_PANEL_PAYLOAD_DIR,
    ):
        if not os.path.isdir(payload_dir):
            continue
        try:
            if os.path.islink(payload_dir):
                os.unlink(payload_dir)
            else:
                shutil.rmtree(payload_dir)
        except OSError as exc:
            print(
                f"Warning: Failed to remove secret payload {payload_dir}: {exc}",
                file=sys.stderr,
            )


def _send_setup_notification(
    *,
    notify_specs: Optional[list[list[str]]],
    system_type: str,
    host: str,
    success: bool,
    errors: Optional[list[str]] = None,
    friendly_name: Optional[str] = None,
    notification_level: object = None,
    strict_https: object = None,
) -> bool:
    """Send the setup result and make best-effort delivery failures visible."""

    delivered = send_setup_notification(
        notify_specs=notify_specs,
        system_type=system_type,
        host=host,
        success=success,
        errors=errors,
        friendly_name=friendly_name,
        notification_level=notification_level,
        strict_https=strict_https,
    )
    if not delivered:
        print(
            "  ⚠ Setup notification delivery was incomplete; check the saved "
            "webhook URL, token, notification level, and receiver connectivity.",
            file=sys.stderr,
        )
    return delivered


def extract_repo_name(git_url: str) -> str:
    repo_name = git_url.rstrip('/').split('/')[-1]
    if repo_name.endswith('.git'):
        repo_name = repo_name[:-4]
    return repo_name


def get_repository_source_path(
    git_url: str,
    deployment_mode: str,
    dry_run: bool = False
) -> tuple[str, str]:
    """
    Get the uploaded source path for a repository.

    Repositories are cloned by the local launcher and uploaded with infra_tools,
    so private repositories only require local access. The remote never fetches
    from Git directly; deployment modes control rebuild behavior, not source
    acquisition.

    Returns: (source_path, commit_hash). Missing uploads are fatal because
    continuing would make Nginx reconciliation remove otherwise-live routes.
    """
    repo_name = extract_repo_name(git_url)
    cache_path = f'/opt/infra_tools/deployments/{repo_name}'

    if not os.path.exists(cache_path):
        raise RuntimeError(
            f"Uploaded repository files not found at {cache_path} for {git_url}"
        )

    commit_hash = ""
    commit_file = f'{cache_path}.commit'
    if os.path.exists(commit_file):
        try:
            with open(commit_file, 'r') as f:
                content = f.read().strip()
                if content:
                    commit_hash = content
        except OSError:
            pass

    mode_label = deployment_mode if deployment_mode in {"default", "lite", "full"} else "default"
    print(f"\nUsing uploaded {repo_name} ({mode_label} mode)")
    return (cache_path, commit_hash)


def enable_detected_build_runtimes(config: SetupConfig) -> None:
    """Enable target runtimes required by uploaded deployment sources.

    The controller already uploads repositories before remote setup starts, so
    the target can select a build runtime without project-specific CLI flags.
    Explicit runtime choices remain supported; detection only adds missing
    requirements.
    """
    if not config.deploy_specs:
        return

    required_versions: list[tuple[int, int, int]] = []
    for _deploy_spec, git_url in config.deploy_specs:
        repo_name = extract_repo_name(git_url)
        repo_path = os.path.join("/opt/infra_tools/deployments", repo_name)
        project_files = _find_project_runtime_files(repo_path)
        if project_files["node"] and not config.install_node:
            config.install_node = True
            print(f"Detected Node.js project in {repo_name}; enabling target Node.js runtime")
        if project_files["python"] and not config.install_python:
            config.install_python = True
            print(f"Detected Python project in {repo_name}; enabling target Python runtime")
        if project_files["go"]:
            if not config.install_go:
                config.install_go = True
                print(f"Detected Go module in {repo_name}; enabling target Go runtime")
            for go_mod in project_files["go"]:
                try:
                    with open(go_mod, "r", encoding="utf-8") as handle:
                        match = re.search(
                            r"^go\s+(\d+)\.(\d+)(?:\.(\d+))?\s*$",
                            handle.read(),
                            re.MULTILINE,
                        )
                except OSError:
                    match = None
                if match:
                    required_versions.append(tuple(int(part or 0) for part in match.groups()))

    if required_versions:
        version = max(required_versions)
        os.environ["INFRA_TOOLS_GO_VERSION"] = ".".join(str(part) for part in version)


def _find_project_runtime_files(repo_path: str) -> dict[str, list[str]]:
    """Find runtime marker files in a repository, including monorepo children."""
    markers = {
        "go": {"go.mod"},
        "node": {"package.json"},
        "python": {"pyproject.toml", "uv.lock", "requirements.txt"},
    }
    found: dict[str, list[str]] = {runtime: [] for runtime in markers}
    if not os.path.isdir(repo_path):
        for runtime, filenames in markers.items():
            found[runtime] = [
                os.path.join(repo_path, filename)
                for filename in filenames
                if os.path.isfile(os.path.join(repo_path, filename))
            ]
        return found

    ignored = {".git", ".infra_tools", ".venv", "node_modules", "vendor"}
    for current_dir, directories, filenames in os.walk(repo_path):
        directories[:] = [name for name in directories if name not in ignored]
        names = set(filenames)
        for runtime, runtime_markers in markers.items():
            found[runtime].extend(
                os.path.join(current_dir, filename)
                for filename in sorted(names & runtime_markers)
            )
    return found


def _load_args_file(args_file: str) -> list[str]:
    try:
        with open(args_file, "r", encoding="utf-8") as file_obj:
            payload = json.load(file_obj)
    finally:
        try:
            os.unlink(args_file)
        except FileNotFoundError:
            pass
        except OSError as exc:
            print(f"Warning: Failed to remove args file {args_file}: {exc}", file=sys.stderr)

    if not isinstance(payload, list):
        raise ValueError("Args file must contain a JSON list")
    if not all(isinstance(arg, str) for arg in payload):
        raise ValueError("Args file entries must all be strings")
    return list(payload)


def _resolve_cli_args(argv: list[str]) -> list[str]:
    args_file_parser = argparse.ArgumentParser(add_help=False)
    args_file_parser.add_argument("--args-file")
    parsed, remaining = args_file_parser.parse_known_args(argv)
    if not parsed.args_file:
        return argv
    return _load_args_file(parsed.args_file) + remaining


def _print_dry_run_plan(steps: list[tuple[str, StepFunc]]) -> None:
    """Print the setup plan without invoking mutating step functions."""
    print("\nSetup plan:")
    for index, (name, _function) in enumerate(steps, 1):
        print(f"  {index:02d}. {name}")
    print("\n[DRY-RUN] No setup steps were executed and no target files were changed.")


def config_from_remote_args(args: argparse.Namespace) -> SetupConfig:
    if args.custom_steps:
        system_type = "custom_steps"
    elif args.system_type:
        system_type = args.system_type
    else:
        raise ValueError("Either --system-type or --steps must be specified")
    
    args.host = "localhost"
    
    config = SetupConfig.from_args(args, system_type)
    config.machine_type = resolve_machine_type(config.machine_type)
    validate_agent_repositories(config.agent_repos)
    validate_agent_git_settings(config)
    validate_browser_automation_settings(config)
    validate_backup_specs(config.backup_specs)
    validate_web_interface_settings(config)
    validate_samba_share_specs(config.samba_shares, config.share_credentials)
    validate_samba_share_credentials(config)
    validate_smb_mount_specs(config.smb_mounts)
    validate_gogs_settings(config)
    validate_homebox_settings(config)
    validate_swap_settings(config)
    validate_vm_storage_settings(config, require_provisioning=False)
    validate_network_setup_settings(config)
    validate_rdp_settings(config)
    
    if system_type == "server_proxmox":
        config.username = "root"
    
    return config


def _run_main() -> int:
    parser = create_setup_argument_parser(
        description="Remote system setup",
        for_remote=True,
        allow_steps=True
    )
    
    args = parser.parse_args(_resolve_cli_args(sys.argv[1:]))

    set_dry_run(bool(args.dry_run))

    if args.system_type == "agent_cachyos":
        from lib.cachyos import cachyos_config_from_args

        return run_cachyos_setup(cachyos_config_from_args(args, for_remote=True))
    from lib.cachyos import is_cachyos

    if is_cachyos():
        raise ValueError("CachyOS supports only the local agent_cachyos setup profile")

    if args.deploy_latest:
        os.environ["INFRA_TOOLS_DEPENDENCY_MIN_AGE_DAYS"] = "0"

    if args.dry_run:
        print("=" * 60)
        print("DRY-RUN MODE ENABLED")
        print("=" * 60)
    
    try:
        config = config_from_remote_args(args)
    except ValueError as e:
        print(f"Error: {e}")
        return 1
    
    # When using --deploy-latest with lite mode, upgrade to default mode for fresh clone
    if args.deploy_latest and config.deployment_mode == "lite":
        config.deployment_mode = "default"

    enable_detected_build_runtimes(config)
    
    if not validate_username(config.username):
        print(f"Error: Invalid username: {config.username}")
        return 1

    if config.custom_steps == "reconcile_samba_shares":
        from smb.samba_steps import reconcile_samba_shares

        detect_os()
        print("Configuring Samba shares only...")
        if args.dry_run:
            print("[DRY-RUN] Would reconcile Samba shares")
            return 0
        reconcile_samba_shares(config)
        print("✓ Samba share update complete")
        return 0
    
    print_setup_summary(config, f"Remote Setup ({config.system_type})")
    sys.stdout.flush()

    detected_os = detect_os()
    print(f"OS: {detected_os}")
    sys.stdout.flush()

    if config.antistatic_server or config.antistatic_db:
        from game.antistatic_steps import preflight_antistatic_releases

        print("Preflighting Antistatic releases...")
        preflight_antistatic_releases(config)
        sys.stdout.flush()
    
    print(f"Machine type: {config.machine_type}")
    sys.stdout.flush()
    
    with machine_type_context(config.machine_type):
        steps = get_steps_for_system_type(config)

    if args.dry_run:
        _print_dry_run_plan(steps)
        print("\n" + "=" * 60)
        print("✓ Remote setup dry-run complete!")
        print("=" * 60)
        return 0

    _begin_setup_operation(config)

    if config.include_desktop:
        from desktop.session_steps import assert_desktop_idle

        # Check before common package upgrades can disturb a graphical session.
        assert_desktop_idle(config)
    
    setup_errors: list[str] = []
    report = get_setup_report()
    
    total_steps = len(steps)
    for i, (name, func) in enumerate(steps, 1):
        if report is not None:
            report.set_step(name)
        _transition_setup_operation(
            "applying",
            {
                "machine_type": config.machine_type,
                "system_type": config.system_type,
                "username": config.username,
                "step": name,
                "step_index": i,
                "step_count": total_steps,
            },
        )
        bar = progress_bar(i, total_steps)
        print(f"\n{bar} [{i}/{total_steps}] {name}")
        sys.stdout.flush()
        step_started = time.monotonic()
        try:
            with machine_type_context(config.machine_type):
                func(config)
        except Exception as e:
            error_msg = f"Step '{name}' failed: {e}"
            elapsed = time.monotonic() - step_started
            # SetupReport observes this progress line. Keep the summary on one
            # line so a multiline exception does not become several run notes;
            # the original exception is still retained for the traceback and
            # setup notification below.
            summary_error = " ".join(error_msg.split())
            print(f"  ✗ {summary_error} ({elapsed:.1f}s)")
            setup_errors.append(error_msg)
            if config.notify_specs:
                _send_setup_notification(
                    notify_specs=config.notify_specs,
                    system_type=config.system_type,
                    host=config.host,
                    success=False,
                    errors=setup_errors,
                    friendly_name=config.friendly_name,
                    notification_level=config.notification_level,
                    strict_https=config.notification_strict_https,
                )
            raise
        elapsed = time.monotonic() - step_started
        print(f"  ✓ Step completed in {elapsed:.1f}s")
        if report is not None:
            report.set_step(None)
    
    bar = progress_bar(total_steps, total_steps)
    print(f"\n{bar} Complete!")
    
    if config.enable_cloudflare:
        from web.cloudflare_steps import (
            create_cloudflared_config_directory,
            configure_nginx_for_cloudflare,
            install_cloudflared_service_helper
        )
        
        print("\n" + "=" * 60)
        print("Configuring Cloudflare tunnel support...")
        print("=" * 60)
        
        print("\n[1/3] Creating cloudflared configuration directory")
        create_cloudflared_config_directory(config)
        
        print("\n[2/3] Configuring nginx for Cloudflare")
        configure_nginx_for_cloudflare(config)
        
        print("\n[3/3] Installing cloudflared setup helper")
        install_cloudflared_service_helper(config)
        
        print("\n✓ Cloudflare tunnel preconfiguration complete")
        print("  Public HTTP/HTTPS remain open until cloudflared is verified active")
        print("  Run 'sudo setup-cloudflare-tunnel' to create a tunnel")
    
    if config.deploy_specs:
        from deploy.deploy_steps import deploy_repository
        
        print("\n" + "=" * 60)
        print("Deploying repositories...")
        print("=" * 60)
        
        deployments: Deployments = []
        
        for deploy_specs_str, git_url in config.deploy_specs:
            repo_result = get_repository_source_path(git_url, config.deployment_mode, config.dry_run)
            source_path, commit_hash = repo_result
            
            for deploy_spec in deploy_specs_str.split(','):
                deploy_spec = deploy_spec.strip()
                if not deploy_spec:
                    continue

                infos = deploy_repository(
                    source_path=source_path,
                    deploy_spec=deploy_spec,
                    git_url=git_url,
                    commit_hash=commit_hash,
                    full_deploy=config.full_deploy,
                    keep_source=True,
                )
                if infos:
                    deployments.extend(infos)
        
        if deployments:
            print("\n" + "=" * 60)
            print("Configuring Nginx...")
            print("=" * 60)
            
            from lib.nginx_config import create_nginx_sites_for_groups
            
            grouped_deployments: dict[Optional[str], Deployments] = {}
            for dep in deployments:
                key = dep.get('domain')
                grouped_deployments.setdefault(key, []).append(dep)
            
            create_nginx_sites_for_groups(
                grouped_deployments,
                enable_https_redirect=not config.enable_cloudflare,
            )
            
            if config.enable_ssl:
                from web.ssl_steps import install_certbot, setup_ssl_for_deployments
                
                print("\n" + "=" * 60)
                print("Installing certbot...")
                print("=" * 60)
                install_certbot(config)
                
                setup_ssl_for_deployments(
                    deployments,
                    config.ssl_email,
                    enable_https_redirect=not config.enable_cloudflare,
                )
            
    if config.enable_cloudflare:
        from web.cloudflare_steps import (
            configure_cloudflare_firewall,
            run_cloudflare_tunnel_setup,
        )

        print("\n" + "=" * 60)
        print("Verifying Cloudflare tunnel activation...")
        print("=" * 60)
        if run_cloudflare_tunnel_setup(config):
            configure_cloudflare_firewall(config)
        else:
            print("  ℹ Direct HTTP/HTTPS access retained until a tunnel is configured")
    
    if config.enable_samba:
        from smb.samba_steps import (
            install_samba,
            configure_samba_firewall,
            configure_samba_global_settings,
            configure_samba_fail2ban,
            reconcile_samba_shares
        )
        
        print("\n" + "=" * 60)
        print("Configuring Samba...")
        print("=" * 60)
        
        print("\n[1/4] Installing Samba")
        install_samba(config)
        
        print("\n[2/4] Configuring global Samba settings with security hardening")
        configure_samba_global_settings(config)
        
        print("\n[3/4] Configuring firewall for Samba")
        configure_samba_firewall(config)
        
        print("\n[4/4] Configuring fail2ban for Samba brute-force protection")
        configure_samba_fail2ban(config)
        
        print("\n" + "=" * 60)
        print(f"Reconciling {len(config.samba_shares or [])} Samba share(s)...")
        print("=" * 60)
        reconcile_samba_shares(config)
        
        print("\n✓ Samba configuration complete")
    
    if config.smb_mounts:
        from smb.smb_mount_steps import configure_smb_mount
        
        print("\n" + "=" * 60)
        print("Configuring SMB mounts...")
        print("=" * 60)
        
        for i, mount_spec in enumerate(config.smb_mounts, 1):
            print(f"\n[{i}/{len(config.smb_mounts)}] Mounting {mount_spec[0]}")
            configure_smb_mount(config, mount_spec=mount_spec)
        
        print("\n✓ SMB mount configuration complete")
    
    if config.sync_specs or config.backup_specs or config.scrub_specs:
        from sync.sync_steps import install_rsync
        from sync.scrub_steps import install_par2
        from sync.storage_ops_steps import (
            create_storage_ops_service,
            schedule_storage_ops_update,
        )

        print("\n" + "=" * 60)
        print("Configuring storage operations service...")
        print("=" * 60)

        if config.sync_specs or config.backup_specs:
            storage_job_count = len(config.sync_specs or []) + len(config.backup_specs or [])
            print(
                f"\nPreparing {storage_job_count} "
                "sync/backup job(s)..."
            )
            install_rsync(config)

        if config.scrub_specs:
            print(f"\nPreparing {len(config.scrub_specs)} scrub job(s)...")
            install_par2(config)
        
        # Create unified storage operations service and timer
        if args.dry_run:
            print("  [DRY-RUN] Skipping storage-ops systemd service/timer creation")
        else:
            create_storage_ops_service(config)
            schedule_storage_ops_update()

    _transition_setup_operation(
        "finalizing",
        {
            "machine_type": config.machine_type,
            "system_type": config.system_type,
            "username": config.username,
        },
    )
    save_machine_state(
        machine_type=config.machine_type,
        system_type=config.system_type,
        username=config.username,
    )
    config_dict = config.to_dict()
    config_dict["system_type"] = config.system_type
    save_setup_config(config_dict)
    _complete_setup_operation()
    
    print("\n" + "=" * 60)
    print("✓ Remote setup complete!")
    print("=" * 60)
    
    if config.notify_specs:
        _send_setup_notification(
            notify_specs=config.notify_specs,
            system_type=config.system_type,
            host=config.host,
            success=True,
            friendly_name=config.friendly_name,
            notification_level=config.notification_level,
            strict_https=config.notification_strict_https,
        )
    
    return 0


def run_cachyos_setup(config: SetupConfig) -> int:
    """Apply only the local workstation plugin, as the existing desktop user."""
    from lib.cachyos import preflight_cachyos

    previous_dry_run = is_dry_run()
    set_dry_run(config.dry_run)
    try:
        preflight_cachyos(config)
        steps = get_steps_for_system_type(config)
        if config.dry_run:
            print("CachyOS local workstation plan (hardware checks deferred until apply)")
            _print_dry_run_plan(steps)
            return 0
        for name, function in steps:
            print(f"\n{name}", flush=True)
            function(config)
        print("\nCachyOS coding setup complete. Authenticate providers locally before starting work.")
        return 0
    finally:
        set_dry_run(previous_dry_run)


def main() -> int:
    report = SetupReport()
    try:
        with report.activate(), report.capture():
            try:
                result = _run_main()
            except Exception as exc:
                _record_setup_failure(exc)
                raise
            finally:
                _remove_secret_payloads()
    finally:
        report.render()
    return result


if __name__ == "__main__":
    def terminate_setup(_signum, _frame):
        raise KeyboardInterrupt("Setup terminated")
    signal.signal(signal.SIGTERM, terminate_setup)
    sys.exit(main())
