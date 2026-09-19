from __future__ import annotations

import json
import base64
import os
import shutil
import sys
import tempfile
from typing import Any, Optional

from lib.config import SetupConfig
from lib.setup_common import copy_project_files, create_tar_from_dir
from lib.remote_utils import CommandTimeoutError, run
from lib.ssh_utils import build_ssh_command as _build_ssh_command, shell_join

REMOTE_BASALTWATER_PATH = "/opt/basaltwater/basaltwater.py"
REMOTE_RECONSTRUCT_SCRIPT = '''set -eu
umask 077
recall_stage=$(mktemp -d /tmp/basaltwater-recall.XXXXXXXX)
trap 'rm -rf -- "$recall_stage"' EXIT
trap 'exit 1' HUP INT TERM
base64 -d > "$recall_stage/source.tgz"
mkdir "$recall_stage/source"
tar xzf "$recall_stage/source.tgz" -C "$recall_stage/source"
python3 "$recall_stage/source/basaltwater.py" reconstruct --compact
'''


def build_ssh_command(host: str, username: str, ssh_key: Optional[str] = None) -> list[str]:
    """Build an SSH command for connecting to a remote host."""
    return _build_ssh_command(host, username, ssh_key, connect_timeout=30, server_alive_interval=30)


def retrieve_stored_config(host: str, username: str, ssh_key: Optional[str] = None) -> Optional[SetupConfig]:
    """Retrieve the stored configuration from the remote host."""
    remote_config_path = "/opt/basaltwater/state/setup.json"
    try:
        result = run(
            build_ssh_command(host, username, ssh_key) + [shell_join(["cat", remote_config_path])],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            config_dict = json.loads(result.stdout)
            if not isinstance(config_dict, dict):
                raise ValueError('Stored configuration must be a JSON object')
            system_type = config_dict.get("system_type", "server_dev")
            return SetupConfig.from_dict(host, system_type, config_dict)
    except CommandTimeoutError:
        print(f"Timeout retrieving stored config from {host}", file=sys.stderr)
    except ValueError as exc:
        print(f"Invalid JSON in stored config: {exc}", file=sys.stderr)
    except FileNotFoundError:
        print("SSH command not available", file=sys.stderr)
    return None


def reconstruct_remote_config(
    host: str,
    username: str,
    ssh_key: Optional[str] = None,
) -> Optional[tuple[SetupConfig, dict[str, Any]]]:
    """Run the remote reconstruction command and return the inferred config."""
    try:
        check_result = run(
            build_ssh_command(host, username, ssh_key) + [shell_join(["test", "-f", REMOTE_BASALTWATER_PATH])],
            capture_output=True,
            timeout=10,
            check=False,
        )
        if check_result.returncode not in (0, 1):
            print('Cannot inspect remote basaltwater installation; reconstruction aborted', file=sys.stderr)
            return None
        if check_result.returncode == 1:
            print("Note: remote tool missing; reconstructing from a temporary source directory...", file=sys.stderr)
            build_dir = tempfile.mkdtemp(prefix="basaltwater_recall_")
            try:
                copy_project_files(build_dir)
                tar_data = create_tar_from_dir(build_dir)
                result = run(
                    build_ssh_command(host, username, ssh_key)
                    + [shell_join(['timeout', '--kill-after=5s', '60s', 'sh', '-c', REMOTE_RECONSTRUCT_SCRIPT])],
                    input_data=base64.b64encode(tar_data).decode('ascii'),
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )
            finally:
                if os.path.exists(build_dir):
                    shutil.rmtree(build_dir)

        else:
            result = run(
                build_ssh_command(host, username, ssh_key)
                + [shell_join(['timeout', '--kill-after=5s', '60s', "python3", REMOTE_BASALTWATER_PATH, "reconstruct", "--compact"])],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        if result.returncode == 0 and result.stdout.strip():
            reconstructed = json.loads(result.stdout)
            if not isinstance(reconstructed, dict):
                raise ValueError('Reconstruction output must be a JSON object')
            config_dict: dict[str, Any] = {
                "username": username,
                "install_go": reconstructed.get("install_go", False),
                "install_node": reconstructed.get("install_node", False),
                "install_python": reconstructed.get("install_python", False),
                "enable_samba": reconstructed.get("enable_samba", False),
            }
            system_type = "server_web" if reconstructed.get("deploy") else "server_dev"
            config = SetupConfig.from_dict(host, system_type, config_dict)

            extras: dict[str, Any] = {}
            for key in ["samba_shares", "deploy", "sync", "scrub", "mount_smb"]:
                if key in reconstructed:
                    extras[key] = reconstructed[key]
            return config, extras

        if result.returncode in (124, 137):
            print('Remote reconstruction timed out or was killed; inspect /tmp/basaltwater-recall.* for leftover temporary source', file=sys.stderr)
        else:
            print(f"Error running reconstruct command: {result.stderr}", file=sys.stderr)
    except (CommandTimeoutError, OSError, ValueError) as exc:
        print(f"Error reconstructing configuration: {exc}", file=sys.stderr)
    return None


def print_config_info(config: SetupConfig, source: str) -> None:
    print("=" * 60)
    print(f"Configuration source: {source}")
    print("=" * 60)
    print()
    print(f"System type: {config.system_type}")
    print(f"Machine type: {config.machine_type}")
    print(f"Username: {config.username}")
    if config.friendly_name:
        print(f"Name: {config.friendly_name}")
    if config.tags and len(config.tags) > 0:
        print(f"Tags: {', '.join(config.tags)}")
    print()


def run_recall_command(host: str, username: str, ssh_key: Optional[str]) -> int:
    """Recall a saved or reconstructed setup command from a remote host."""
    print(f"Attempting to recall setup configuration for {username}@{host}...")
    print()

    stored_config = retrieve_stored_config(host, username, ssh_key)
    if stored_config:
        print_config_info(stored_config, "Stored configuration file")
        print("=" * 60)
        print("Suggested command:")
        print("=" * 60)
        print()
        print(" \\\n  ".join(stored_config.to_setup_command(include_username=True)))
        print()
        return 0

    print("⚠ Warning: No stored configuration found on remote host.")
    print("Attempting to reconstruct configuration by analyzing server state...")
    print()

    result = reconstruct_remote_config(host, username, ssh_key)
    if result is None:
        print("Error: Failed to retrieve or reconstruct configuration.", file=sys.stderr)
        return 1

    config, extras = result
    print_config_info(config, "Reconstructed from server analysis")
    print("=" * 60)
    print("Partial/guessed command (manual review required):")
    print("=" * 60)
    print()

    current_user = os.getenv("USER", "")
    include_username = username != current_user
    print(" \\\n  ".join(config.to_setup_command(include_username=include_username)))

    if extras:
        notes: list[str] = []
        if "samba_shares" in extras:
            shares = extras["samba_shares"]
            notes.append(f"  # Detected {len(shares)} Samba share(s): {', '.join(shares)}")
            notes.append("  # Add --share flags manually")
        if "deploy" in extras:
            deployments = extras["deploy"]
            notes.append(f"  # Detected {len(deployments)} deployment(s)")
            for name, _ in deployments:
                notes.append(f"  # Add --deploy <domain> <git_url>  # for: {name}")
        if "sync" in extras:
            notes.append(f"  # Detected {len(extras['sync'])} sync operation(s)")
            notes.append("  # Add --sync <source> <dest> <interval> flags")
        if "scrub" in extras:
            notes.append(f"  # Detected {len(extras['scrub'])} scrub operation(s)")
            notes.append("  # Add --scrub <dir> <db_path> <redundancy> <freq> flags")
        if "mount_smb" in extras:
            mounts = [str(mount) for mount in extras["mount_smb"]]
            notes.append(f"  # Detected {len(mounts)} SMB mount(s): {', '.join(mounts)}")
            notes.append("  # Add --mount-smb flags manually")
        if notes:
            print()
            print("# Additional features requiring manual configuration:")
            for note in notes:
                print(note)

    print()
    print("⚠ Note: This is a partial reconstruction. Please review and complete manually.")
    print()
    return 0
