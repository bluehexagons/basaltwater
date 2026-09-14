"""Remote deployment utilities for pushing builds to app servers."""

from __future__ import annotations

import os
import json
import subprocess
import shlex
import tempfile
import posixpath
import secrets
from typing import Optional

from lib.ssh_utils import build_scp_command, build_ssh_command, build_rsync_ssh_transport, chain_remote_commands, shell_join, ssh_batch_mode
from lib.types import JSONDict
from lib.validation import validate_filesystem_path
from lib.cicd_deploy_policy import validate_nginx_deployment
from lib.state_read import StateReadError, read_state_object
from lib.cicd_deadline import run_command


DEPLOY_ADMIN_HELPER = "/usr/local/sbin/infra-tools-deploy-admin"
DEPLOY_TARGETS_FILE = "/etc/infra_tools/cicd/deploy_targets.json"


def _validate_config_name(domain: str) -> str:
    """Return the safe nginx site name derived from a validated domain."""

    config_name = domain.replace('.', '_')
    if not config_name or len(config_name) > 254 or any(
        not (char.isascii() and (char.isalnum() or char in "_-"))
        for char in config_name
    ):
        raise ValueError(f"Invalid deployment domain: {domain}")
    return config_name


def _validate_deploy_path(deploy_path: str, base_dir: str) -> str:
    """Require a deployment path to be a child of its configured base directory."""

    validate_filesystem_path(base_dir)
    validate_filesystem_path(deploy_path)
    normalized_base = posixpath.normpath(base_dir)
    normalized_path = posixpath.normpath(deploy_path)
    if not normalized_base.startswith('/') or not normalized_path.startswith('/'):
        raise ValueError("Deployment paths must be absolute")
    if normalized_path == normalized_base or posixpath.commonpath([normalized_base, normalized_path]) != normalized_base:
        raise ValueError(f"Deployment path must be below {normalized_base}: {deploy_path}")
    return normalized_path


def load_deploy_targets() -> dict[str, JSONDict]:
    """Load deploy targets configuration."""
    targets = read_state_object(DEPLOY_TARGETS_FILE, versioned=False)
    if targets is None:
        return {}
    for target in targets.values():
        if not isinstance(target, dict) or not isinstance(target.get("host"), str):
            raise StateReadError(DEPLOY_TARGETS_FILE, "invalid deployment target structure")
    return targets


def get_deploy_target(target_host: str) -> Optional[JSONDict]:
    """Get configuration for a specific deploy target."""
    targets = load_deploy_targets()
    return targets.get(target_host)


def _build_ssh_cmd(target: JSONDict, remote_cmd: str) -> list[str]:
    """Build SSH command for a target."""
    ssh_key = target.get('ssh_key', '/var/lib/infra_tools/cicd/.ssh/deploy_key')
    ssh_port = target.get('ssh_port', 22)
    user = target.get('user', 'deploy')
    host = target['host']

    return build_ssh_command(
        host,
        user,
        ssh_key,
        port=ssh_port,
        remote_command=remote_cmd,
        batch_mode=ssh_batch_mode(),
        connect_timeout=30,
        server_alive_interval=None,
    )


def _build_ssh_stdin_script_cmd(target: JSONDict, working_dir: str) -> list[str]:
    """Build an SSH command that executes a bash script streamed over stdin."""

    remote_cmd = chain_remote_commands(
        [
            ["cd", working_dir],
            ["bash", "-s", "--"],
        ]
    )
    return _build_ssh_cmd(target, remote_cmd)


def push_artifact(
    local_path: str,
    target_host: str,
    remote_path: str,
    exclude_patterns: Optional[list[str]] = None
) -> bool:
    """Push artifact directory to remote server using rsync.
    
    Security Note: Uses the workspace known_hosts file with strict host-key
    checking. Enroll and independently verify the target key before deployment.
    """
    target = get_deploy_target(target_host)
    if not target:
        print(f"  ✗ Unknown deploy target: {target_host}")
        return False

    try:
        remote_path = _validate_deploy_path(remote_path, str(target.get('base_dir', '/var/www')))
    except ValueError as exc:
        print(f"  ✗ {exc}")
        return False

    ssh_key = target.get('ssh_key', '/var/lib/infra_tools/cicd/.ssh/deploy_key')
    ssh_port = target.get('ssh_port', 22)
    user = target.get('user', 'deploy')
    host = target['host']
    
    rsync_cmd = [
        'rsync', '-avz', '--delete',
        '-e', build_rsync_ssh_transport(ssh_key=ssh_key, port=ssh_port, batch_mode=ssh_batch_mode(), connect_timeout=30),
    ]
    
    if exclude_patterns:
        for pattern in exclude_patterns:
            rsync_cmd.extend(['--exclude', pattern])
    
    if not local_path.endswith('/'):
        local_path = local_path + '/'
    
    remote_target = f"{user}@{host}:{shlex.quote(remote_path)}"
    rsync_cmd.extend([local_path, remote_target])
    
    try:
        result = run_command(
            rsync_cmd,
            capture_output=True,
            text=True,
            timeout=300
        )
        if result.returncode != 0:
            print(f"  ✗ rsync failed: {result.stderr}")
            return False
        return True
    except subprocess.TimeoutExpired:
        print("  ✗ rsync timed out")
        return False
    except Exception as e:
        print(f"  ✗ rsync error: {e}")
        return False


def push_nginx_config(deployment: JSONDict, target_host: str, domain: str) -> bool:
    """Send structured site parameters for rendering by the app server."""
    target = get_deploy_target(target_host)
    if not target:
        print(f"  ✗ Unknown deploy target: {target_host}")
        return False
    
    try:
        config_name = _validate_config_name(domain)
        request = validate_nginx_deployment(deployment)
        if request['domain'] != domain:
            raise ValueError("deployment domain does not match nginx site name")
    except ValueError as exc:
        print(f"  ✗ {exc}")
        return False
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(request, f)
        temp_path = f.name
    
    try:
        ssh_key = target.get('ssh_key', '/var/lib/infra_tools/cicd/.ssh/deploy_key')
        ssh_port = target.get('ssh_port', 22)
        user = target.get('user', 'deploy')
        host = target['host']
        
        operation_id = secrets.token_hex(16)
        remote_temp_path = (
            f"/tmp/infra-tools-nginx-{config_name}-{operation_id}.json"
        )
        scp_cmd = build_scp_command(
            host,
            user,
            temp_path,
            remote_temp_path,
            ssh_key,
            port=ssh_port,
            batch_mode=ssh_batch_mode(),
            connect_timeout=30,
        )
        
        result = run_command(scp_cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            print(f"  ✗ Failed to upload nginx config: {result.stderr}")
            return False
        
        remote_cmd = shell_join(
            [
                "sudo",
                DEPLOY_ADMIN_HELPER,
                "install-site",
                config_name,
                operation_id,
            ]
        )
        
        ssh_cmd = _build_ssh_cmd(target, remote_cmd)
        result = run_command(ssh_cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            print(f"  ✗ Failed to install nginx config: {result.stderr}")
            return False
        
        return True
    finally:
        os.unlink(temp_path)


def reload_nginx(target_host: str) -> bool:
    """Reload nginx on remote server."""
    target = get_deploy_target(target_host)
    if not target:
        return False
    
    remote_cmd = shell_join(["sudo", DEPLOY_ADMIN_HELPER, "reload-nginx"])
    ssh_cmd = _build_ssh_cmd(target, remote_cmd)
    
    try:
        result = run_command(ssh_cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            print(f"  ✗ Failed to reload nginx: {result.stderr}")
            return False
        return True
    except subprocess.TimeoutExpired:
        print("  ✗ SSH timed out")
        return False


def restart_service(target_host: str, service_name: str) -> bool:
    """Restart a systemd service on remote server."""
    target = get_deploy_target(target_host)
    if not target:
        return False
    
    remote_cmd = shell_join(
        ["sudo", DEPLOY_ADMIN_HELPER, "restart-service", service_name]
    )
    ssh_cmd = _build_ssh_cmd(target, remote_cmd)
    
    try:
        result = run_command(ssh_cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            print(f"  ✗ Failed to restart {service_name}: {result.stderr}")
            return False
        return True
    except subprocess.TimeoutExpired:
        print("  ✗ SSH timed out")
        return False


def remove_deployment(target_host: str, deploy_path: str, domain: Optional[str] = None) -> bool:
    """Remove a deployment from remote server."""
    target = get_deploy_target(target_host)
    if not target:
        return False
    
    try:
        safe_deploy_path = _validate_deploy_path(
            deploy_path,
            str(target.get("base_dir", "/var/www")),
        )
    except ValueError as exc:
        print(f"  ✗ {exc}")
        return False

    cmds: list[list[str]] = [["rm", "-rf", "--", safe_deploy_path]]
    
    if domain:
        try:
            config_name = _validate_config_name(domain)
        except ValueError as exc:
            print(f"  ✗ {exc}")
            return False
        cmds.append(["sudo", DEPLOY_ADMIN_HELPER, "remove-nginx", config_name])
        cmds.append(["sudo", DEPLOY_ADMIN_HELPER, "reload-nginx"])

    remote_cmd = chain_remote_commands(cmds)
    ssh_cmd = _build_ssh_cmd(target, remote_cmd)
    
    try:
        result = run_command(ssh_cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            print(f"  ✗ Failed to remove deployment: {result.stderr}")
            return False
        return True
    except subprocess.TimeoutExpired:
        print("  ✗ SSH timed out")
        return False


def test_deploy_connection(target_host: str) -> bool:
    """Test SSH connection to deploy target."""
    target = get_deploy_target(target_host)
    if not target:
        print(f"  ✗ Unknown deploy target: {target_host}")
        return False
    
    ssh_cmd = _build_ssh_cmd(target, shell_join(["echo", "connection ok"]))
    
    try:
        result = run_command(ssh_cmd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            print(f"  ✗ Connection failed: {result.stderr}")
            return False
        print(f"  ✓ Connection to {target_host} successful")
        return True
    except subprocess.TimeoutExpired:
        print("  ✗ Connection timed out")
        return False
