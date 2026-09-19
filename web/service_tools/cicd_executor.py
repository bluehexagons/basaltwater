#!/usr/bin/env python3
"""
CI/CD Executor

Processes CI/CD jobs triggered by the webhook receiver.
Clones repositories, runs build/test/deploy scripts, and reports status.
Supports both local deployment and remote deployment to app servers.

Logs to: /var/log/basaltwater/web/cicd_executor.log
"""

from __future__ import annotations

import os
import sys
import json
import shutil
import subprocess
import shlex
import time
import fcntl
import stat
import tempfile
import hashlib
import sqlite3
from pathlib import Path
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '../..'))

from lib.logging_utils import get_service_logger, log_event
from lib.notifications import load_notification_configs_from_state, send_notification_safe
from lib.cicd_deadline import JOB_TIMEOUT_SECONDS, command_timeout, enter_phase, job_budget, run_command
from lib.cicd_build import BUILD_HOME, BUILD_PATH, artifact_snapshot, receiver_state, run_build_command
from web.service_tools.cicd_config import load_config_file
from web.service_tools.cicd_deliveries import claim, pending_job_files
from web.service_tools.cicd_security import (
    DEFAULT_BRANCHES,
    MAX_JOB_FILE_BYTES,
    get_workspace_name,
    validate_branch_ref,
    validate_commit_sha,
    validate_job_data,
)

logger = get_service_logger('cicd_executor', 'web', use_syslog=True)

CONFIG_DIR = "/etc/basaltwater/cicd"
CONFIG_FILE = os.path.join(CONFIG_DIR, "webhook_config.json")
STATE_DIR = "/var/lib/basaltwater/cicd"
JOBS_DIR = os.path.join(STATE_DIR, "jobs")
WORKSPACES_DIR = os.path.join(BUILD_HOME, "workspaces")
LOGS_DIR = os.path.join(STATE_DIR, "logs")
LOCK_FILE = os.path.join(STATE_DIR, "executor.lock")
DELIVERIES_FILE = os.path.join(STATE_DIR, 'deliveries.sqlite3')


def load_config() -> dict:
    """Load webhook configuration from JSON file."""
    return load_config_file(CONFIG_FILE)


def get_repo_workspace(repo_url: str) -> str:
    """Get workspace directory for a repository."""
    return os.path.join(WORKSPACES_DIR, get_workspace_name(repo_url))


def clone_or_update_repo(repo_url: str, workspace: str, ref: str, commit_sha: str) -> bool:
    """Create a fresh clone and check out the authenticated commit."""
    try:
        if os.path.lexists(workspace):
            if os.path.islink(workspace) or not os.path.isdir(workspace):
                os.unlink(workspace)
            else:
                shutil.rmtree(workspace)

        log_event(logger, "Creating fresh repository clone", repo_url=repo_url)
        result = run_build_command(
            [
                'git',
                'clone',
                '--no-checkout',
                '--origin',
                'origin',
                '--config',
                'core.hooksPath=/dev/null',
                '--',
                repo_url,
                workspace,
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            log_event(logger, "Failed to clone repository", level=40, repo_url=repo_url, stderr=result.stderr.strip())
            return False

        _validated_ref, branch, validated_sha = _validate_checkout_fields(ref, commit_sha)
        remote_ref = f"refs/remotes/origin/{branch}"
        refspec = f"+{ref}:{remote_ref}"

        log_event(logger, "Fetching authenticated branch", repo_url=repo_url, branch=branch)
        result = run_build_command(
            ['git', 'fetch', '--force', '--prune', 'origin', refspec],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            log_event(logger, "Failed to fetch repository changes", level=40, repo_url=repo_url, stderr=result.stderr.strip())
            return False

        result = run_build_command(
            ['git', 'cat-file', '-e', f'{validated_sha}^{{commit}}'],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            log_event(logger, "Authenticated commit was not fetched", level=40, repo_url=repo_url, commit_sha=validated_sha[:8])
            return False

        result = run_build_command(
            ['git', 'merge-base', '--is-ancestor', validated_sha, remote_ref],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            log_event(
                logger,
                "Authenticated commit is not reachable from configured branch",
                level=40,
                repo_url=repo_url,
                branch=branch,
                commit_sha=validated_sha[:8],
            )
            return False

        log_event(logger, "Checking out authenticated commit", repo_url=repo_url, branch=branch, commit_sha=validated_sha[:8])
        result = run_build_command(
            ['git', 'checkout', '--detach', '--force', validated_sha],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            log_event(logger, "Failed to checkout authenticated commit", level=40, repo_url=repo_url, commit_sha=validated_sha[:8], stderr=result.stderr.strip())
            return False

        result = run_build_command(
            ['git', 'clean', '-ffdx'],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            log_event(logger, "Failed to clean repository workspace", level=40, repo_url=repo_url, stderr=result.stderr.strip())
            return False

        log_event(logger, "Repository checkout prepared", repo_url=repo_url, branch=branch, commit_sha=validated_sha[:8])
        return True
        
    except subprocess.TimeoutExpired:
        log_event(logger, "Git operation timed out", level=40, repo_url=repo_url)
        return False
    except Exception as e:
        log_event(logger, "Failed to clone/update repository", level=40, repo_url=repo_url, error=str(e))
        return False


def _validate_checkout_fields(ref: str, commit_sha: str) -> tuple[str, str, str]:
    """Validate checkout fields through the shared job validator."""

    safe_ref, branch = validate_branch_ref(ref)
    safe_sha = validate_commit_sha(commit_sha)
    return safe_ref, branch, safe_sha


def _load_job_file(job_file: str) -> object:
    """Load a small, regular job file without following symlinks."""

    # Opening a FIFO without O_NONBLOCK hangs before fstat can reject it.
    flags = os.O_RDONLY | os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(job_file, flags)
    try:
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError("job path must be a regular file")
        if file_stat.st_size > MAX_JOB_FILE_BYTES:
            raise ValueError("job file is too large")
        with os.fdopen(fd, 'rb') as file_obj:
            fd = -1
            content = file_obj.read(MAX_JOB_FILE_BYTES + 1)
        # The writer can grow a file after fstat. Bound the actual read too.
        if len(content) > MAX_JOB_FILE_BYTES:
            raise ValueError("job file is too large")
        return json.loads(content)
    finally:
        if fd >= 0:
            os.close(fd)


def _consume_job_path(job_file: str) -> None:
    """Remove one terminal queue entry without following symlinks."""

    try:
        path_stat = os.lstat(job_file)
    except FileNotFoundError:
        return

    if stat.S_ISDIR(path_stat.st_mode):
        shutil.rmtree(job_file)
    else:
        os.unlink(job_file)


def run_script(script_path: str, workspace: str, log_file: str) -> bool:
    """Run a CI/CD script and log output."""
    # Resolve relative paths against workspace
    if not os.path.isabs(script_path):
        script_path = os.path.join(workspace, script_path)
    
    if not os.path.exists(script_path):
        log_event(logger, "Script not found", level=40, script_path=script_path)
        return False
    if not Path(script_path).resolve().is_relative_to(Path(workspace).resolve()):
        log_event(logger, 'Script escapes repository checkout', level=40)
        return False

    build_home = BUILD_HOME
    nvm_dir = os.path.join(build_home, ".nvm")
    script_path_env = BUILD_PATH
    script_command = (
        f"export HOME={shlex.quote(build_home)} && "
        f"export NVM_DIR={shlex.quote(nvm_dir)} && "
        f"export PATH={shlex.quote(script_path_env)} && "
        '[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"; '
        f"exec /bin/bash {shlex.quote(script_path)}"
    )
    try:
        log_event(logger, "Running script", script_path=script_path)
        
        with open(log_file, 'a') as log:
            log.write(f"\n{'='*80}\n")
            log.write(f"Running: {script_path}\n")
            log.write(f"{'='*80}\n\n")
            
            result = run_build_command(
                ['/bin/bash', '-lc', script_command],
                cwd=workspace,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=3600  # 1 hour timeout
            )
            
            log.write(f"\n{'='*80}\n")
            log.write(f"Exit code: {result.returncode}\n")
            log.write(f"{'='*80}\n")
        
        if result.returncode == 0:
            log_event(logger, "Script completed successfully", script_path=script_path)
            return True
        else:
            log_event(logger, "Script failed", level=40, script_path=script_path, exit_code=result.returncode)
            return False
            
    except subprocess.TimeoutExpired:
        log_event(logger, "Script timed out", level=40, script_path=script_path)
        return False
    except Exception as e:
        log_event(logger, "Failed to run script", level=40, script_path=script_path, error=str(e))
        return False


def process_job(job_file: str) -> bool:
    with job_budget() as budget:
        result = _process_job(job_file)
        if budget.timed_out:
            log_event(
                logger, "CI/CD job timed out", level=40, job_file=job_file,
                stage=budget.phase, deployment_started=budget.deployment_started,
            )
            return False
        return result


def _process_job(job_file: str) -> bool:
    """Process a single CI/CD job."""
    log_event(logger, "Processing job", job_file=job_file)
    try:
        config = load_config()
    except (OSError, ValueError) as exc:
        log_event(logger, 'CI/CD configuration unavailable; retaining job', level=40, error=str(exc))
        return False
    
    consume = True
    try:
        job_data = _load_job_file(job_file)
        repo_url, ref, branch, commit_sha, pusher = validate_job_data(job_data)

        if 'delivery_key' in job_data:
            try:
                with receiver_state():
                    claimed = claim(DELIVERIES_FILE, job_file, job_data)
            except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
                consume = False
                log_event(logger, 'Delivery receipt unavailable; retaining job', level=40, error=str(exc))
                return False
            if not claimed:
                log_event(logger, 'Skipping expired or previously claimed delivery', level=30, job_file=job_file)
                return False
        
        repos = config.get('repositories', [])
        
        repo_config = None
        for repo in repos:
            if repo.get('url') == repo_url:
                repo_config = repo
                break
        
        if not repo_config:
            log_event(logger, "Repository not configured", level=40, repo_url=repo_url)
            return False

        configured_branches = repo_config.get('branches', DEFAULT_BRANCHES)
        if branch not in configured_branches:
            log_event(logger, "Job branch is not configured", level=40, repo_url=repo_url, branch=branch)
            return False
        
        workspace = get_repo_workspace(repo_url)
        os.makedirs(LOGS_DIR, exist_ok=True)

        timestamp = job_data.get('timestamp', 'unknown')
        if not isinstance(timestamp, str) or len(timestamp) > 64 or any(
            ord(char) < 32 or ord(char) == 127 for char in timestamp
        ):
            timestamp = 'unknown'
        
        job_id = hashlib.sha256(os.path.basename(job_file).encode()).hexdigest()[:12]
        descriptor, log_file = tempfile.mkstemp(
            prefix=f'{get_workspace_name(repo_url)}-{commit_sha[:12]}-{job_id}-',
            suffix='.log', dir=LOGS_DIR,
        )
        log_event(logger, 'Created build log', job_file=job_file, log_file=log_file)
        with os.fdopen(descriptor, 'w') as log:
            log.write(f"CI/CD Build Log\n")
            log.write(f"Job: {os.path.basename(job_file)}\n")
            log.write(f"{'='*80}\n")
            log.write(f"Repository: {repo_url}\n")
            log.write(f"Branch: {ref}\n")
            log.write(f"Commit: {commit_sha}\n")
            log.write(f"Pusher: {pusher}\n")
            log.write(f"Timestamp: {timestamp}\n")
            deploy_target = repo_config.get('deploy_target')
            if deploy_target:
                log.write(f"Deploy Target: {deploy_target}\n")
            log.write(f"{'='*80}\n\n")
        
        if not clone_or_update_repo(repo_url, workspace, ref, commit_sha):
            log_event(logger, "Failed to clone/update repository", level=40, repo_url=repo_url, job_file=job_file)
            notify_failure(repo_url, commit_sha, "Failed to clone/update repository")
            return False
        
        scripts = repo_config.get('scripts', {})
        success = True
        
        for script_name in ['install', 'build', 'test']:
            script_path = scripts.get(script_name)
            if script_path:
                enter_phase(script_name)
                if not run_script(script_path, workspace, log_file):
                    log_event(logger, "Failed at stage", level=40, stage=script_name, repo_url=repo_url, commit_sha=commit_sha[:8])
                    success = False
                    break
        
        if success:
            deploy_target = repo_config.get('deploy_target')
            deploy_spec = repo_config.get('deploy_spec')
            
            if deploy_target:
                enter_phase("snapshot")
                with artifact_snapshot(workspace) as snapshot:
                    enter_phase("deploy", deployment=True)
                    success = perform_remote_deployment(
                        snapshot, deploy_target, deploy_spec, repo_url,
                        commit_sha, log_file, repo_config
                    )
            else:
                deploy_script = scripts.get('deploy')
                if deploy_script:
                    enter_phase("deploy", deployment=True)
                    if not run_script(deploy_script, workspace, log_file):
                        log_event(logger, "Failed at stage", level=40, stage="deploy", repo_url=repo_url, commit_sha=commit_sha[:8])
                        success = False
        
        command_timeout(JOB_TIMEOUT_SECONDS)
        notification_configs = load_notification_configs_from_state(logger)
        if notification_configs:
            if success:
                notify_success(repo_url, commit_sha, log_file, notification_configs)
            else:
                notify_failure(repo_url, commit_sha, "Build failed", notification_configs)
        
        log_event(
            logger,
            "Job completed",
            job_file=job_file,
            result="success" if success else "failed",
            repo_url=repo_url,
            commit_sha=commit_sha[:8],
        )
        return success
        
    except Exception as e:
        log_event(logger, "Error processing job", level=40, job_file=job_file, error=str(e))
        return False
    finally:
        try:
            if consume:
                _consume_job_path(job_file)
        except OSError as exc:
            log_event(logger, "Failed to remove consumed job", level=30, job_file=job_file, error=str(exc))


def perform_remote_deployment(
    workspace: str,
    deploy_target: str,
    deploy_spec: Optional[str],
    repo_url: str,
    commit_sha: str,
    log_file: str,
    repo_config: dict
) -> bool:
    """Deploy built artifacts to a remote app server."""
    from lib.remote_deploy import (
        _build_ssh_stdin_script_cmd,
        _validate_deploy_path,
        get_deploy_target,
        push_artifact,
        push_nginx_config,
        reload_nginx,
    )
    from lib.deploy_utils import (
        detect_project_type,
        get_project_root,
        is_ruby_project,
        parse_deploy_spec,
    )
    from lib.cicd_deploy_policy import validate_nginx_deployment
    
    target = get_deploy_target(deploy_target)
    if not target:
        log_event(logger, "Unknown deploy target", level=40, deploy_target=deploy_target)
        with open(log_file, 'a') as log:
            log.write(f"\n✗ Unknown deploy target: {deploy_target}\n")
        return False
    
    domain = None
    path = '/'
    if deploy_spec:
        domain, path = parse_deploy_spec(deploy_spec)
    
    if is_ruby_project(workspace):
        log_event(
            logger,
            "Ruby/Rails remote deployment is unsupported",
            level=40,
            deploy_target=deploy_target,
        )
        with open(log_file, 'a') as log:
            log.write("\n✗ Ruby/Rails deployment requires a pinned legacy basaltwater release\n")
        return False

    project_type = detect_project_type(workspace)
    log_event(logger, "Detected project type", deploy_target=deploy_target, project_type=project_type)
    
    serve_path = get_project_root(workspace, project_type)
    base_dir = target.get('base_dir', '/var/www')
    
    from lib.deploy_utils import create_safe_directory_name
    dir_name = create_safe_directory_name(domain, path)
    try:
        remote_path = _validate_deploy_path(f"{base_dir}/{dir_name}", base_dir)
    except ValueError as exc:
        log_event(logger, "Invalid deployment path", level=40, error=str(exc))
        return False

    deployment = None
    if domain:
        try:
            deployment = validate_nginx_deployment({
                'path': path, 'serve_path': remote_path,
                'project_type': project_type, 'domain': domain,
            })
        except ValueError as exc:
            log_event(logger, "Invalid nginx deployment", level=40, error=str(exc))
            return False

    # Read required deployment steps before transferring or activating artifacts.
    # Retain the content so a disappearing script cannot become a silent skip.
    deploy_script = repo_config.get('scripts', {}).get('deploy')
    script_content = None
    if deploy_script:
        script_path = deploy_script if os.path.isabs(deploy_script) else os.path.join(workspace, deploy_script)
        try:
            if not Path(script_path).resolve().is_relative_to(Path(workspace).resolve()):
                raise ValueError('Deploy script escapes repository checkout')
            if not os.path.isfile(script_path):
                raise ValueError(f"Deploy script is not a regular file: {script_path}")
            with open(script_path, 'r', encoding='utf-8') as script:
                script_content = script.read(1024 * 1024 + 1)
            if len(script_content.encode('utf-8')) > 1024 * 1024:
                raise ValueError('Deploy script exceeds 1 MiB')
        except (OSError, ValueError) as exc:
            log_event(logger, "Cannot read required deploy script", level=40, error=str(exc))
            with open(log_file, 'a') as log:
                log.write(f"\n✗ Cannot read required deploy script: {exc}\n")
            return False
    
    with open(log_file, 'a') as log:
        log.write(f"\n{'='*80}\n")
        log.write(f"Deploying to remote server: {deploy_target}\n")
        log.write(f"Remote path: {remote_path}\n")
        log.write(f"{'='*80}\n\n")
    
    exclude_patterns = ['.git', 'node_modules', '__pycache__', '*.log']
    
    if not push_artifact(serve_path, deploy_target, remote_path, exclude_patterns):
        log_event(logger, "Failed to push artifact to remote server", level=40, deploy_target=deploy_target, remote_path=remote_path)
        with open(log_file, 'a') as log:
            log.write("✗ Failed to push artifact\n")
        return False
    
    with open(log_file, 'a') as log:
        log.write(f"✓ Artifact pushed to {deploy_target}:{remote_path}\n")
    
    if domain:
        assert deployment is not None
        if not push_nginx_config(deployment, deploy_target, domain):
            log_event(logger, "Failed to push nginx config", level=40, deploy_target=deploy_target, domain=domain)
            with open(log_file, 'a') as log:
                log.write("✗ Failed to push nginx config\n")
            return False
        
        with open(log_file, 'a') as log:
            log.write(f"✓ Nginx config pushed for {domain}\n")
        
        if not reload_nginx(deploy_target):
            log_event(logger, "Failed to reload nginx on remote server", level=40, deploy_target=deploy_target, domain=domain)
            with open(log_file, 'a') as log:
                log.write("✗ Failed to reload nginx\n")
            return False
        
        with open(log_file, 'a') as log:
            log.write("✓ Nginx reloaded\n")
    
    if script_content is not None:
        ssh_cmd = _build_ssh_stdin_script_cmd(target, remote_path)
        try:
            result = run_command(
                ssh_cmd,
                input=script_content,
                capture_output=True,
                text=True,
                timeout=300,
            )
            with open(log_file, 'a') as log:
                log.write(f"\nDeploy script output:\n{result.stdout}\n")
                if result.stderr:
                    log.write(f"Errors:\n{result.stderr}\n")
            if result.returncode != 0:
                log_event(logger, "Deploy script failed", level=40, deploy_target=deploy_target, stderr=result.stderr.strip())
                return False
        except Exception as e:
            log_event(logger, "Failed to run deploy script", level=40, deploy_target=deploy_target, error=str(e))
            return False
    
    log_event(logger, "Remote deployment completed", deploy_target=deploy_target, remote_path=remote_path)
    return True


def notify_success(repo_url: str, commit_sha: str, log_file: str, notification_configs: list) -> None:
    """Send success notification."""
    try:
        send_notification_safe(
            notification_configs,
            subject=f"CI/CD Success: {repo_url}",
            job="cicd_executor",
            status="good",
            message=f"Build succeeded for commit {commit_sha[:8]}\nLog: {log_file}",
            logger=logger,
            event_type="cicd.completed",
            state="success",
            dedup_key=f"cicd:{repo_url}:{commit_sha}",
            delivery_policy="signal",
        )
    except Exception as e:
        log_event(
            logger,
            "Failed to send success notification",
            level=30,
            repo_url=repo_url,
            commit_sha=commit_sha[:8],
            error=str(e),
        )


def notify_failure(repo_url: str, commit_sha: str, reason: str, notification_configs: Optional[list] = None) -> None:
    """Send failure notification."""
    if not notification_configs:
        notification_configs = load_notification_configs_from_state(logger)
    
    if notification_configs:
        try:
            send_notification_safe(
                notification_configs,
                subject=f"CI/CD Failed: {repo_url}",
                job="cicd_executor",
                status="error",
                message=f"Build failed for commit {commit_sha[:8]}\nReason: {reason}",
                logger=logger,
                event_type="cicd.failed",
                state="firing",
                dedup_key=f"cicd:{repo_url}:{commit_sha}",
            )
        except Exception as e:
            log_event(
                logger,
                "Failed to send failure notification",
                level=30,
                repo_url=repo_url,
                commit_sha=commit_sha[:8],
                error=str(e),
            )


def cleanup_old_build_logs(days_to_keep: int = 30) -> int:
    """Remove build log files older than days_to_keep days.

    Args:
        days_to_keep: Number of days to keep build logs

    Returns:
        Number of files removed
    """
    if not os.path.exists(LOGS_DIR):
        return 0

    cutoff_time = time.time() - (days_to_keep * 24 * 60 * 60)
    removed_count = 0

    for filename in os.listdir(LOGS_DIR):
        if not filename.endswith('.log'):
            continue
        log_path = os.path.join(LOGS_DIR, filename)
        try:
            if os.path.getmtime(log_path) < cutoff_time:
                os.remove(log_path)
                removed_count += 1
        except OSError as e:
            log_event(logger, "Failed to remove old build log", level=30, log_file=filename, error=str(e))

    if removed_count > 0:
        log_event(
            logger,
            "Cleaned up old build logs",
            removed_count=removed_count,
            days_to_keep=days_to_keep,
        )

    return removed_count


def cleanup_stale_workspaces(config: dict) -> int:
    """Remove workspace directories for repos no longer in config.

    Skips cleanup if config appears invalid (empty or missing repositories),
    to avoid destroying workspaces due to a transient config read error.

    Args:
        config: Loaded webhook configuration

    Returns:
        Number of directories removed
    """
    if not os.path.exists(WORKSPACES_DIR):
        return 0

    repos = config.get('repositories', [])
    if not repos:
        logger.debug("Skipping stale workspace cleanup: no repositories in config")
        return 0

    configured_workspaces = set()
    for repo in repos:
        url = repo.get('url', '')
        if url:
            configured_workspaces.add(get_workspace_name(url))

    removed_count = 0
    for name in os.listdir(WORKSPACES_DIR):
        if name not in configured_workspaces:
            workspace_path = os.path.join(WORKSPACES_DIR, name)
            if os.path.isdir(workspace_path):
                try:
                    shutil.rmtree(workspace_path)
                    removed_count += 1
                    log_event(logger, "Removed stale workspace", workspace=name)
                except OSError as e:
                    log_event(logger, "Failed to remove stale workspace", level=30, workspace=name, error=str(e))

    return removed_count


def main():
    """Main function to process CI/CD jobs."""
    log_event(logger, "Starting CI/CD executor")
    
    os.makedirs(STATE_DIR, exist_ok=True)
    os.makedirs(JOBS_DIR, exist_ok=True)
    os.makedirs(WORKSPACES_DIR, exist_ok=True)
    os.makedirs(LOGS_DIR, exist_ok=True)
    
    lock_fd = None
    try:
        lock_fd = open(LOCK_FILE, 'w')
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (IOError, OSError):
            log_event(logger, "Another executor instance is running, exiting")
            return 0
        
        with receiver_state():
            job_files = pending_job_files(DELIVERIES_FILE, JOBS_DIR)
        config = load_config()
        
        if not job_files:
            log_event(logger, "No pending jobs")
            cleanup_old_build_logs()
            cleanup_stale_workspaces(config)
            return 0
        
        log_event(logger, "Found pending jobs", pending_jobs=len(job_files))
        
        success_count = 0
        failure_count = 0
        
        for job_file in job_files:
            if process_job(str(job_file)):
                success_count += 1
            else:
                failure_count += 1
        
        log_event(
            logger,
            "CI/CD executor finished",
            successful_jobs=success_count,
            failed_jobs=failure_count,
        )
        
        cleanup_old_build_logs()
        cleanup_stale_workspaces(config)
        
        return 0 if failure_count == 0 else 1
    finally:
        if lock_fd is not None:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()


if __name__ == "__main__":
    sys.exit(main())
