#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import io
import json
import os
import pwd
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from typing import Optional, Callable

try:
    import argcomplete
except ImportError:
    argcomplete = None

from lib.atomic_io import write_json_atomic
from lib.streamed_process import run_streamed
from lib.setup_payloads import PAYLOAD_NAMES, link_payloads, move_payloads, payload_workspace
from lib.agent_credentials import codex_auth_warning, inspect_codex_auth_file
from lib.config import DEFAULT_MACHINE_TYPE, SetupConfig, _normalize_nested_specs
from lib.credentials import prepare_runtime_config, store_cli_credentials
from lib.network_transition import finish_network_transition
from lib.proxmox_hosts import ProxmoxHost, find_proxmox_host, sync_proxmox_host
from lib.validators import validate_host, validate_username
from lib.validation import (
    validate_apt_packages,
    validate_agent_repositories,
    validate_agent_git_settings,
    validate_git_author_email,
    validate_git_author_name,
    validate_browser_automation_settings,
    validate_antistatic_settings,
    validate_deploy_specs,
    validate_deploy_targets,
    validate_gogs_settings,
    validate_hosted_flags,
    validate_network_setup_settings,
    validate_rdp_settings,
    validate_samba_settings,
    validate_samba_share_credentials,
    validate_samba_share_specs,
    validate_smb_mount_specs,
    validate_scrub_specs,
    validate_backup_specs,
    validate_web_interface_settings,
    validate_ssl_email,
    validate_sync_specs,
    validate_memory_string,
    validate_filesystem_path,
    validate_timezone_name,
    validate_workspace_dir,
    validate_positive_integer,
)
from lib.cache import get_cache_path_for_host, save_setup_command
from lib.concurrency import ResourceBusyError, resource_lock
from lib.arg_parser import create_setup_argument_parser
from lib.display import print_setup_summary
from lib.interactive_setup import prompt_for_missing_passwords, run_interactive_setup
from lib.installation_info import write_setup_snapshot_metadata
from lib.notifications import validate_notification_args
from lib.proxmox_guest import resolve_guest_ssh_key
from lib.ssh_utils import (
    build_ssh_command,
    ensure_remote_sudo,
    get_ssh_control_path,
    ssh_batch_mode,
)
from lib.workspace import set_workspace_dir
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REMOTE_SCRIPT_PATH = os.path.join(SCRIPT_DIR, "..", "remote_setup.py")
LIB_DIR = SCRIPT_DIR
CONFIG_DIR = os.path.join(SCRIPT_DIR, "..", "config")
SERVICE_TOOLS_DIR = os.path.join(SCRIPT_DIR, "..", "service_tools")
REMOTE_INSTALL_DIR = "/opt/basaltwater"
PERSISTENT_STATE_DIR = "/var/lib/basaltwater"
GIT_CACHE_DIR = os.path.expanduser("~/.cache/basaltwater/git_repos")
REMOTE_ARGS_FILENAME = ".remote_setup_args.json"
AGENT_PAYLOAD_DIRNAME = "agent_payload"
DEVICE_PAIRING_PAYLOAD_DIRNAME = "device_pairing_payload"
WEB_PANEL_PAYLOAD_DIRNAME = "web_panel_payload"
LEGACY_SETUP_OPERATION_FILENAME = "setup-operation.pre-persistence.json"
MAX_AGENT_CREDENTIAL_BYTES = 4 * 1024 * 1024
MAX_DEVICE_PAIRING_AUTH_BYTES = 64 * 1024
_GIT_IDENTITY_PAYLOAD_PATH = os.path.join("config", "git", "identity.json")
_LAST_REMOTE_ACCESS_DETAILS: dict[str, str] = {}
_REMOTE_ACCESS_PROGRESS_PREFIX = re.compile(r"^[✓ℹ]\s+")
_SYNCTHING_DEVICE_ID_PATTERN = re.compile(
    r"^[A-Z2-7]{7}(?:-[A-Z2-7]{7}){7}$"
)


def get_last_remote_access_details() -> dict[str, str]:
    """Return access details announced by the most recent remote setup."""

    return dict(_LAST_REMOTE_ACCESS_DETAILS)


def _record_remote_access_output(line: str) -> None:
    """Remember generated access details while preserving live setup output."""

    text = _REMOTE_ACCESS_PROGRESS_PREFIX.sub("", line.strip())
    url_prefixes = {
        "T3 Code HTTPS endpoint:": "t3code",
        "T3 Code pairing HTTPS endpoint:": "t3code-pairing",
        "Syncthing HTTPS admin:": "syncthing-admin",
    }
    for prefix, name in url_prefixes.items():
        if not text.startswith(prefix):
            continue
        url = text[len(prefix) :].strip()
        if re.fullmatch(r"https://[^\s]+", url):
            _LAST_REMOTE_ACCESS_DETAILS[name] = url
        return

    device_id_prefix = "Syncthing device ID:"
    if text.startswith(device_id_prefix):
        device_id = text[len(device_id_prefix) :].strip()
        if _SYNCTHING_DEVICE_ID_PATTERN.fullmatch(device_id):
            _LAST_REMOTE_ACCESS_DETAILS["syncthing-device-id"] = device_id


def _repository_cache_path(cache_dir: str, git_url: str, repo_name: str) -> str:
    """Return a cache path unique to the complete repository URL."""
    url_digest = hashlib.sha256(git_url.encode("utf-8")).hexdigest()[:16]
    return os.path.join(cache_dir, f"{repo_name}-{url_digest}")


def _refresh_repository_cache(git_url: str, cache_path: str, repo_name: str) -> bool:
    """Create or refresh one cache while its caller owns the cache lock."""
    if not os.path.exists(cache_path):
        print(f"  Caching {git_url}...")
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        result = subprocess.run(
            ["git", "clone", git_url, cache_path],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            print(f"  Error cloning repository: {result.stderr}")
            return False
        print(f"  ✓ Cached to {cache_path}")
        return True

    print(f"  Updating cached repository {repo_name}...")
    result = subprocess.run(
        ["git", "-C", cache_path, "fetch", "--all"],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        print(f"  Error fetching updates: {result.stderr}")
        return False

    result = subprocess.run(
        ["git", "-C", cache_path, "symbolic-ref", "refs/remotes/origin/HEAD", "--short"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode == 0:
        default_branch = result.stdout.strip()
    else:
        for branch in ["origin/main", "origin/master"]:
            result = subprocess.run(
                ["git", "-C", cache_path, "rev-parse", "--verify", branch],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                default_branch = branch
                break
        else:
            print("  Error: Could not determine default branch")
            return False

    for command, label in (
        (["git", "-C", cache_path, "reset", "--hard", default_branch], "resetting"),
        (["git", "-C", cache_path, "clean", "-fdx"], "cleaning"),
    ):
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            print(f"  Error {label} repository: {result.stderr}")
            return False
    print("  ✓ Updated cached repository")
    return True


def clone_repository(git_url: str, temp_dir: str, cache_dir: Optional[str] = None, dry_run: bool = False) -> Optional[tuple[str, Optional[str]]]:
    repo_name = git_url.rstrip('/').split('/')[-1]
    if repo_name.endswith('.git'):
        repo_name = repo_name[:-4]

    if repo_name in {"", ".", ".."}:
        print(f"  Error: unsafe repository name derived from {git_url}")
        return None
    
    clone_path = os.path.join(temp_dir, repo_name)

    # A deployment dry run still needs real source files to validate basaltwater.json
    # and project support. Clone only into the disposable setup staging tree;
    # do not update or populate the persistent cache.
    if dry_run:
        print(f"  [DRY RUN] Cloning {git_url} for deployment preflight...")
        try:
            result = subprocess.run(
                ["git", "clone", "--depth", "1", git_url, clone_path],
                capture_output=True,
                text=True,
                timeout=300,
            )
            if result.returncode != 0:
                print(f"  Error cloning repository for preflight: {result.stderr}")
                return None
            from lib.deploy_utils import get_git_commit_hash

            return (clone_path, get_git_commit_hash(clone_path))
        except Exception as exc:
            print(f"  Error cloning repository for preflight: {exc}")
            return None
    
    if cache_dir:
        cache_path = _repository_cache_path(cache_dir, git_url, repo_name)
        try:
            with resource_lock("git-cache", cache_path, wait=True):
                if not _refresh_repository_cache(git_url, cache_path, repo_name):
                    return None
                if os.path.exists(clone_path):
                    shutil.rmtree(clone_path)
                shutil.copytree(cache_path, clone_path, symlinks=True)
            print(f"  ✓ Copied to {clone_path}")
            from lib.deploy_utils import get_git_commit_hash
            return (clone_path, get_git_commit_hash(clone_path))
        except Exception as exc:
            print(f"  Error preparing repository cache: {exc}")
            return None
    else:
        print(f"  Cloning {git_url}...")
        if dry_run:
            print(f"  [DRY RUN] Would clone to {clone_path}")
            return (clone_path, None)
        
        try:
            result = subprocess.run(
                ["git", "clone", git_url, clone_path],
                capture_output=True,
                text=True,
                timeout=300
            )
            if result.returncode != 0:
                print(f"  Error cloning repository: {result.stderr}")
                return None
            print(f"  ✓ Cloned to {clone_path}")
            
            from lib.deploy_utils import get_git_commit_hash
            commit_hash = get_git_commit_hash(clone_path)
            
            return (clone_path, commit_hash)
        except Exception as e:
            print(f"  Error cloning repository: {e}")
            return None


def copy_project_files(dest_dir: str) -> None:
    """Stage runtime code with permissions suitable for root-owned services."""
    validate_filesystem_path(dest_dir)
    project_root = os.path.normpath(os.path.join(SCRIPT_DIR, ".."))
    items_to_copy = [
        "basaltwater.py",
        "remote_setup.py",
        "lib",
        "plugins",
        "game",
        "desktop",
        "web",
        "smb",
        "security",
        "sync",
        "common",
        "deploy",
    ]
    
    for item in items_to_copy:
        src = os.path.join(project_root, item)
        dst = os.path.join(dest_dir, item)
        if os.path.exists(src):
            if os.path.isdir(src):
                shutil.copytree(src, dst, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.git'))
            else:
                shutil.copy2(src, dst)
    # A shared controller checkout can be group-writable. Those modes must not
    # cross into the privileged runtime. Payloads are staged separately later.
    for directory, directories, files in os.walk(dest_dir):
        for name in directories:
            os.chmod(os.path.join(directory, name), 0o755)
        for name in files:
            path = os.path.join(directory, name)
            os.chmod(path, 0o755 if os.stat(path).st_mode & 0o111 else 0o644)
    write_setup_snapshot_metadata(project_root, dest_dir)


def _is_managed_local_install(install_dir: str) -> bool:
    """Return whether local setup would otherwise overwrite its managed source tree."""
    project_root = os.path.normpath(os.path.join(SCRIPT_DIR, ".."))
    try:
        return os.path.samefile(project_root, install_dir) and os.path.isdir(
            os.path.join(install_dir, ".git")
        )
    except FileNotFoundError:
        return False


def _runtime_state_path() -> str:
    """Return the compatibility path exposed inside the runtime tree."""
    return os.path.join(REMOTE_INSTALL_DIR, "state")


def _migrate_local_runtime_state() -> None:
    """Move legacy local runtime state into its durable host directory."""
    legacy_state_dir = _runtime_state_path()
    if os.path.islink(PERSISTENT_STATE_DIR):
        raise RuntimeError(
            f"Refusing symlinked basaltwater state directory: {PERSISTENT_STATE_DIR}"
        )
    os.makedirs(PERSISTENT_STATE_DIR, mode=0o700, exist_ok=True)

    migrated_legacy_state = False
    if os.path.lexists(legacy_state_dir):
        if os.path.islink(legacy_state_dir):
            if os.path.realpath(legacy_state_dir) != os.path.realpath(
                PERSISTENT_STATE_DIR
            ):
                raise RuntimeError(
                    f"Refusing unexpected basaltwater state link: {legacy_state_dir}"
                )
        elif os.path.isdir(legacy_state_dir):
            _copy_existing_path(legacy_state_dir, PERSISTENT_STATE_DIR)
            migrated_legacy_state = True
        else:
            raise RuntimeError(
                f"basaltwater state path is not a directory: {legacy_state_dir}"
            )
    if migrated_legacy_state:
        operation_marker = os.path.join(
            PERSISTENT_STATE_DIR,
            "setup-operation.json",
        )
        legacy_marker = os.path.join(
            PERSISTENT_STATE_DIR,
            LEGACY_SETUP_OPERATION_FILENAME,
        )
        if os.path.isfile(operation_marker) and not os.path.lexists(legacy_marker):
            os.replace(operation_marker, legacy_marker)
    os.chmod(PERSISTENT_STATE_DIR, 0o700)


def _install_local_runtime_state_link() -> None:
    """Expose durable state at the historical runtime-relative path."""
    legacy_state_dir = _runtime_state_path()
    if os.path.lexists(legacy_state_dir):
        if (
            os.path.islink(legacy_state_dir)
            and os.path.realpath(legacy_state_dir)
            == os.path.realpath(PERSISTENT_STATE_DIR)
        ):
            return
        if os.path.isdir(legacy_state_dir) and not os.path.islink(legacy_state_dir):
            shutil.rmtree(legacy_state_dir)
        else:
            os.unlink(legacy_state_dir)
    os.symlink(PERSISTENT_STATE_DIR, legacy_state_dir, target_is_directory=True)


def _activate_local_runtime(build_dir: str) -> None:
    """Stage local setup payloads without destroying a managed Git worktree."""
    if os.path.lexists(os.path.join(os.path.dirname(REMOTE_INSTALL_DIR), "infra_tools")):
        raise RuntimeError("Run basaltw migrate --system --apply before replacing a recent infra-tools installation")
    _migrate_local_runtime_state()
    if not _is_managed_local_install(REMOTE_INSTALL_DIR):
        if os.path.exists(REMOTE_INSTALL_DIR):
            shutil.rmtree(REMOTE_INSTALL_DIR)
        shutil.copytree(build_dir, REMOTE_INSTALL_DIR, symlinks=True)
        _install_local_runtime_state_link()
        os.chmod(REMOTE_INSTALL_DIR, 0o755)
        return

    for item in (
        "deployments",
        AGENT_PAYLOAD_DIRNAME,
        DEVICE_PAIRING_PAYLOAD_DIRNAME,
        WEB_PANEL_PAYLOAD_DIRNAME,
        REMOTE_ARGS_FILENAME,
    ):
        destination = os.path.join(REMOTE_INSTALL_DIR, item)
        if os.path.isdir(destination) and not os.path.islink(destination):
            shutil.rmtree(destination)
        elif os.path.lexists(destination):
            os.unlink(destination)

        source = os.path.join(build_dir, item)
        if os.path.isdir(source):
            shutil.copytree(source, destination, symlinks=True)
        elif os.path.exists(source):
            shutil.copy2(source, destination)

    _install_local_runtime_state_link()
    os.chmod(REMOTE_INSTALL_DIR, 0o755)


def prepare_deployments(config: SetupConfig, target_dir: str) -> None:
    if not config.deploy_specs:
        return
        
    print(f"\n{'='*60}")
    print("Cloning repositories locally...")
    print(f"{'='*60}")
    
    for _deploy_spec, git_url in config.deploy_specs:
        result = clone_repository(git_url, target_dir, cache_dir=GIT_CACHE_DIR, dry_run=config.dry_run)
        if result is None:
            raise RuntimeError(
                f"Failed to stage {git_url}; no target changes were started"
            )
        clone_path, commit_hash = result
        from lib.deploy_utils import is_ruby_project
        from lib.project_manifest import load_manifest

        if is_ruby_project(clone_path):
            raise RuntimeError(
                f"Ruby/Rails repository {git_url} is unsupported by this "
                "basaltwater version; use its pinned legacy release"
            )
        manifest = load_manifest(clone_path)
        if manifest is not None:
            print(
                f"  ✓ Validated basaltwater.json ({len(manifest.components)} component(s))"
            )
        if commit_hash and not config.dry_run:
            repo_name = os.path.basename(clone_path)
            commit_file = os.path.join(target_dir, f"{repo_name}.commit")
            with open(commit_file, 'w') as f:
                f.write(commit_hash)


def _local_user_home() -> str:
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and sudo_user != "root":
        try:
            return pwd.getpwnam(sudo_user).pw_dir
        except KeyError:
            pass
    return os.path.expanduser("~")


def _copy_existing_path(source: str, destination: str) -> bool:
    if not os.path.lexists(source):
        return False
    if os.path.islink(source):
        raise ValueError(f"Refusing to stage symlinked source path: {source}")

    os.makedirs(os.path.dirname(destination), exist_ok=True)
    if os.path.isdir(source):
        def reject_symlinks(directory: str, names: list[str]) -> list[str]:
            for name in names:
                path = os.path.join(directory, name)
                if os.path.islink(path):
                    raise ValueError(
                        f"Refusing to stage symlinked source path: {path}"
                    )
            return []

        shutil.copytree(
            source,
            destination,
            symlinks=False,
            ignore=reject_symlinks,
            dirs_exist_ok=True,
        )
    elif os.path.isfile(source):
        shutil.copy2(source, destination)
    else:
        raise ValueError(f"Source path must be a regular file or directory: {source}")
    return True


_AGENT_AUTH_FILENAMES = {
    "codex": "auth.json",
    "claude": ".credentials.json",
    "opencode": "auth.json",
}


def _credential_source_path(path: str, label: str) -> str:
    expanded = os.path.abspath(os.path.expanduser(path))
    validate_filesystem_path(expanded, must_exist=True)
    if os.path.islink(expanded) or not os.path.isfile(expanded):
        raise ValueError(f"{label} must be a regular, non-symlink file: {path}")
    mode = stat.S_IMODE(os.stat(expanded, follow_symlinks=False).st_mode)
    if mode & 0o022:
        raise ValueError(f"{label} must not be group- or world-writable: {path}")
    if os.path.getsize(expanded) > MAX_AGENT_CREDENTIAL_BYTES:
        raise ValueError(f"{label} exceeds the size limit: {path}")
    return expanded


def _stage_secret_file(source: str, destination: str, label: str) -> None:
    source_path = _credential_source_path(source, label)
    if not _copy_existing_path(source_path, destination):
        raise ValueError(f"{label} could not be read: {source}")
    os.chmod(destination, 0o600)
    print(f"  Staged {label}")


def _warn_for_codex_auth_source(source: str) -> None:
    metadata = inspect_codex_auth_file(source)
    if metadata.get("auth_mode") == "chatgpt":
        print(
            "  Note: Codex ChatGPT auth is renewable per-machine state; "
            "use a dedicated source for each VM"
        )
    warning = codex_auth_warning(metadata)
    if warning:
        print(f"  Warning: staged source: {warning}")


def _validate_htpasswd_content(content: bytes, label: str) -> None:
    """Validate the narrow Nginx password-file format accepted by portals."""

    if not content or len(content) > MAX_DEVICE_PAIRING_AUTH_BYTES:
        raise ValueError(f"{label} htpasswd file is empty or too large")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} htpasswd file must be UTF-8 text") from exc
    records = [line for line in text.splitlines() if line]
    if not records:
        raise ValueError(f"{label} htpasswd file has no user records")
    for line in records:
        if any(ord(character) < 32 or ord(character) == 127 for character in line):
            raise ValueError(f"{label} htpasswd records contain control characters")
        username, separator, password_hash = line.partition(":")
        if not separator or not validate_username(username):
            raise ValueError(f"Invalid {label.lower()} htpasswd username: {username}")
        if not password_hash.startswith("$"):
            raise ValueError(
                f"{label} htpasswd entries must use crypt-style hashes"
            )


def _generated_htpasswd(username: str | None, password: str | None, label: str) -> bytes:
    if not username or not password:
        raise ValueError(f"{label} credentials are incomplete")
    try:
        result = subprocess.run(
            ["openssl", "passwd", "-6", "-stdin"],
            input=f"{password}\n",
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(
            f"OpenSSL is required to generate the {label.lower()} password file"
        ) from exc
    password_hash = result.stdout.strip()
    if result.returncode != 0 or not password_hash.startswith("$6$"):
        raise ValueError(f"OpenSSL could not generate the {label.lower()} password hash")
    payload = f"{username}:{password_hash}\n".encode("utf-8")
    _validate_htpasswd_content(payload, label)
    return payload


def prepare_device_pairing_payload(config: SetupConfig, payload_dir: str) -> None:
    """Stage a controller-local Basic Auth file without persisting its secret."""

    if not config.device_pairing_providers:
        return
    if not (
        config.device_pairing_auth_file
        or config.device_pairing_auth_username
        or config.device_pairing_auth_password
    ):
        return
    if config.dry_run:
        print("  [DRY RUN] Would stage device-pairing Basic Auth credentials")
        return

    if config.device_pairing_auth_file:
        source = _credential_source_path(
            config.device_pairing_auth_file,
            "Device-pairing htpasswd file",
        )
        if os.path.getsize(source) > MAX_DEVICE_PAIRING_AUTH_BYTES:
            raise ValueError("Device-pairing htpasswd file exceeds the size limit")
        with open(source, "rb") as file_obj:
            payload = file_obj.read()
    else:
        payload = _generated_htpasswd(
            config.device_pairing_auth_username,
            config.device_pairing_auth_password,
            "Device-pairing",
        )
    _validate_htpasswd_content(payload, "Device-pairing")
    destination = os.path.join(payload_dir, "htpasswd")
    _copy_existing_path_value(payload, destination)
    print("  Staged device-pairing Basic Auth credentials")


def prepare_web_panel_payload(config: SetupConfig, payload_dir: str) -> None:
    """Hash and stage the transient web panel password."""

    if config.web_panel_port is None or config.web_panel_auth_password is None:
        return
    if config.dry_run:
        print("  [DRY RUN] Would stage web panel Basic Auth credentials")
        return
    payload = _generated_htpasswd(
        config.username,
        config.web_panel_auth_password,
        "Web panel",
    )
    destination = os.path.join(payload_dir, "htpasswd")
    _copy_existing_path_value(payload, destination)
    print("  Staged web panel Basic Auth credentials")


def _github_host_entry(hosts_path: str, host: str) -> str:
    source = _credential_source_path(hosts_path, "GitHub CLI hosts file")
    with open(source, encoding="utf-8") as file_obj:
        lines = file_obj.readlines()
    entry = _github_host_entry_from_lines(lines, host)
    if entry is None:
        raise ValueError(f"GitHub CLI hosts file has no entry for {host}: {hosts_path}")
    return entry


def _github_host_entry_from_lines(lines: list[str], host: str) -> Optional[str]:
    start = None
    for index, line in enumerate(lines):
        if line.startswith(f"{host}:") or line.startswith(f"'{host}':") or line.startswith(f'"{host}":'):
            start = index
            break
    if start is None:
        return None
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].strip() and not lines[index][0].isspace() and not lines[index].lstrip().startswith("#"):
            end = index
            break
    return "".join(lines[start:end])


def _github_entry_has_token(entry: str) -> bool:
    """Return whether a selected hosts.yml entry contains a token value."""
    for line in entry.splitlines():
        stripped = line.strip()
        if not stripped.startswith("oauth_token:"):
            continue
        value = stripped.partition(":")[2].strip()
        return value not in {"", "null", "~", "''", '""'}
    return False


def _github_token_entry(token: str, host: str) -> str:
    normalized = token.strip()
    if not normalized or any(character.isspace() for character in normalized):
        raise ValueError("GitHub token must be a non-empty single-line value")
    return f"{host}:\n    oauth_token: {json.dumps(normalized)}\n    git_protocol: https\n"


def _github_entry_with_token(entry: Optional[str], token: str, host: str) -> str:
    """Add a retrieved token without discarding host identity metadata."""
    if entry is None:
        return _github_token_entry(token, host)

    normalized = token.strip()
    if not normalized or any(character.isspace() for character in normalized):
        raise ValueError("GitHub token must be a non-empty single-line value")
    lines = entry.splitlines(keepends=True)
    if not lines:
        return _github_token_entry(normalized, host)

    if not lines[0].endswith(("\n", "\r")):
        lines[0] += "\n"
    indentation = "    "
    token_index = None
    for index, line in enumerate(lines[1:], start=1):
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        candidate = line[: len(line) - len(stripped)]
        if candidate:
            indentation = candidate
        if stripped.startswith("oauth_token:"):
            token_index = index
            break
    token_line = f"{indentation}oauth_token: {json.dumps(normalized)}\n"
    if token_index is None:
        lines.insert(1, token_line)
    else:
        lines[token_index] = token_line
    return "".join(lines)


def _github_auth_payload_from_file(source: str, host: str) -> bytes:
    """Read a selected GitHub hosts entry or a one-line token file."""
    try:
        entry = _github_host_entry(source, host)
    except ValueError as hosts_error:
        source_path = _credential_source_path(source, "GitHub token file")
        with open(source_path, encoding="utf-8") as file_obj:
            token = file_obj.read().strip()
        if not token or any(character.isspace() for character in token):
            raise hosts_error
        entry = _github_token_entry(token, host)
    else:
        if not _github_entry_has_token(entry):
            raise ValueError(
                f"GitHub CLI hosts entry for {host} has no oauth_token: {source}"
            )
    return entry.encode("utf-8")


def _github_auth_payload_from_active(hosts_path: str, host: str) -> bytes:
    """Read active GitHub auth, including tokens held by gh's keyring."""
    entry: Optional[str] = None
    if os.path.lexists(os.path.abspath(os.path.expanduser(hosts_path))):
        entry = _github_host_entry_from_validated_path(hosts_path, host)
    if entry and _github_entry_has_token(entry):
        return entry.encode("utf-8")

    gh_path = shutil.which("gh")
    if not gh_path:
        raise ValueError(
            "Active GitHub credentials are not available in hosts.yml and gh is "
            "not installed; use --git-auth-file PATH or --interactive to provide "
            "a token file"
        )
    try:
        result = subprocess.run(
            [gh_path, "auth", "token", "--hostname", host],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(
            "gh could not read the active GitHub token; use --git-auth-file PATH "
            "or --interactive to provide a token file"
        ) from exc
    if result.returncode != 0:
        raise ValueError(
            "gh could not read the active GitHub token; authenticate gh or use "
            "--git-auth-file PATH"
        )
    return _github_entry_with_token(entry, result.stdout, host).encode("utf-8")


def _github_host_entry_from_validated_path(hosts_path: str, host: str) -> Optional[str]:
    source = _credential_source_path(hosts_path, "GitHub CLI hosts file")
    with open(source, encoding="utf-8") as file_obj:
        return _github_host_entry_from_lines(file_obj.readlines(), host)


def _stage_github_auth(config: SetupConfig, payload_dir: str, local_home: str) -> None:
    if config.git_auth_token:
        payload = _github_token_entry(config.git_auth_token, config.git_host).encode("utf-8")
    elif config.git_auth_file:
        payload = _github_auth_payload_from_file(config.git_auth_file, config.git_host)
    else:
        payload = _github_auth_payload_from_active(
            os.path.join(local_home, ".config", "gh", "hosts.yml"),
            config.git_host,
        )
    destination = os.path.join(payload_dir, "secrets", "gh", "hosts.yml")
    _copy_existing_path_value(payload, destination)
    print("  Staged GitHub CLI credentials")


def _active_git_identity(local_home: str) -> dict[str, str]:
    """Read only the controller user's global Git author identity."""

    git_path = shutil.which("git")
    if not git_path:
        return {}
    environment = os.environ.copy()
    environment["HOME"] = local_home
    if os.environ.get("SUDO_USER"):
        environment["XDG_CONFIG_HOME"] = os.path.join(local_home, ".config")

    identity: dict[str, str] = {}
    for field, key, validator in (
        ("name", "user.name", validate_git_author_name),
        ("email", "user.email", validate_git_author_email),
    ):
        try:
            result = subprocess.run(
                [git_path, "config", "--global", "--get", key],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
                env=environment,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        value = (result.stdout or "").rstrip("\r\n")
        if result.returncode == 0 and value:
            identity[field] = validator(value)
    return identity


def _stage_git_identity(payload_dir: str, local_home: str) -> None:
    """Stage the minimal non-secret Git identity paired with GitHub auth."""

    identity = _active_git_identity(local_home)
    if not identity:
        return
    write_json_atomic(
        os.path.join(payload_dir, _GIT_IDENTITY_PAYLOAD_PATH),
        identity,
        mode=0o600,
        sort_keys=True,
    )
    print("  Staged Git author identity")


def _copy_existing_path_value(value: bytes, destination: str) -> None:
    os.makedirs(os.path.dirname(destination), mode=0o700, exist_ok=True)
    with open(destination, "wb") as file_obj:
        file_obj.write(value)
    os.chmod(destination, 0o600)


def _stage_active_agent_config(config: SetupConfig, payload_dir: str, local_home: str) -> None:
    config_sources = {
        "codex": (os.path.join(local_home, ".codex"), ("config.toml", "AGENTS.md", "skills", "rules")),
        "claude": (os.path.join(local_home, ".claude"), ("settings.json", "CLAUDE.md", "commands", "agents", "skills", "plugins")),
        "opencode": (os.path.join(local_home, ".config", "opencode"), None),
        "gh": (os.path.join(local_home, ".config", "gh"), ("config.yml", "aliases.yml", "extensions")),
    }
    for tool, (source_dir, names) in config_sources.items():
        if tool not in config.selected_agent_tools():
            continue
        destination = os.path.join(payload_dir, "config", tool)
        staged = False
        if names is None:
            staged = _copy_existing_path(source_dir, destination)
        else:
            for name in names:
                staged = _copy_existing_path(
                    os.path.join(source_dir, name),
                    os.path.join(destination, name),
                ) or staged
        if staged:
            print(f"  Staged {tool} config")


def prepare_agent_payload(config: SetupConfig, payload_dir: str) -> None:
    config.github_auth_payload = False
    config.git_identity_payload = False
    if not (config.copy_agent_config or config.copy_agent_keys):
        return
    if not config.selected_agent_tools():
        raise ValueError("Agent credentials or config require at least one --agent-tool")

    print(f"\n{'='*60}")
    print("Staging selected agent config and credentials...")
    print(f"{'='*60}")
    if config.dry_run:
        print("  [DRY RUN] Would stage selected agent config and credentials")
        return

    local_home = _local_user_home()
    os.makedirs(payload_dir, mode=0o700, exist_ok=True)
    if config.copy_agent_config:
        _stage_active_agent_config(config, payload_dir, local_home)

    if config.git_auth_source or config.git_auth_file or config.git_auth_token:
        if config.git_host != "github.com" or "gh" not in config.selected_agent_tools():
            raise ValueError("GitHub auth requires --agent-tool gh and --git-host github.com")
        _stage_github_auth(config, payload_dir, local_home)

    for tool, source in config.agent_auth_files or []:
        if tool == "gh":
            _copy_existing_path_value(
                _github_auth_payload_from_file(source, config.git_host),
                os.path.join(payload_dir, "secrets", "gh", "hosts.yml"),
            )
        else:
            if tool == "codex":
                _warn_for_codex_auth_source(source)
            _stage_secret_file(
                source,
                os.path.join(payload_dir, "secrets", tool, _AGENT_AUTH_FILENAMES[tool]),
                f"{tool} credentials",
            )

    github_auth = os.path.isfile(
        os.path.join(payload_dir, "secrets", "gh", "hosts.yml")
    )
    if github_auth:
        _stage_git_identity(payload_dir, local_home)
    config.github_auth_payload = github_auth
    config.git_identity_payload = os.path.isfile(
        os.path.join(payload_dir, _GIT_IDENTITY_PAYLOAD_PATH)
    )


def create_tar_from_dir(source_dir: str, *, exclude_payloads: bool = False) -> bytes:
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode='w:gz') as tar:
        def include(member):
            parts = member.name.removeprefix("./").split("/")
            return None if exclude_payloads and parts[0] in PAYLOAD_NAMES else member
        tar.add(source_dir, arcname=".", filter=include)
    return tar_buffer.getvalue()


def _create_payload_archive(source_dir: str) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name in PAYLOAD_NAMES:
            path = os.path.join(source_dir, name)
            if os.path.lexists(path):
                archive.add(path, arcname=name)
    return buffer.getvalue()


def create_argument_parser(description: str, allow_steps: bool = False) -> argparse.ArgumentParser:
    return create_setup_argument_parser(description, for_remote=False, allow_steps=allow_steps)


def _expand_remote_args(remote_args: list[str]) -> list[str]:
    """Split quoted remote arg fragments back into argv tokens for subprocess usage."""
    expanded_args: list[str] = []
    for arg in remote_args:
        expanded_args.extend(shlex.split(arg))
    return expanded_args


def _write_remote_args_file(build_dir: str, remote_arg_tokens: list[str]) -> str:
    """Persist runtime argv tokens outside the process table."""
    args_path = os.path.join(build_dir, REMOTE_ARGS_FILENAME)
    write_json_atomic(args_path, remote_arg_tokens, mode=0o600, indent=None)
    return args_path


def _default_root_storage_for_host(host) -> Optional[str]:
    if host.default_storage:
        return host.default_storage
    if host.facts and host.facts.default_root_storage:
        return host.facts.default_root_storage
    return None


def _default_template_storage_for_host(host) -> Optional[str]:
    if host.default_template_storage:
        return host.default_template_storage
    if host.facts and host.facts.default_template_storage:
        return host.facts.default_template_storage
    return None


def _is_storage_amount(value: str) -> bool:
    try:
        validate_memory_string(value, "--storage AMOUNT")
    except ValueError:
        return False
    return True


def _normalize_provisioned_target(config: SetupConfig) -> None:
    """Make the setup target the single source of truth for a guest IPv4."""
    if not isinstance(config.hosted_node, str) or not config.hosted_node:
        return

    raw_target = str(config.host).strip()
    configured_interface: Optional[ipaddress.IPv4Interface] = None
    if config.static_ipv4:
        try:
            parsed_configured = ipaddress.ip_interface(config.static_ipv4)
        except ValueError as exc:
            raise ValueError(f"Invalid --ip address: {config.static_ipv4}") from exc
        if not isinstance(parsed_configured, ipaddress.IPv4Interface):
            raise ValueError("Proxmox provisioning requires an IPv4 setup target")
        configured_interface = parsed_configured

    if "/" in raw_target:
        try:
            target_interface = ipaddress.ip_interface(raw_target)
        except ValueError as exc:
            raise ValueError(f"Invalid provisioned target address: {raw_target}") from exc
        if not isinstance(target_interface, ipaddress.IPv4Interface):
            raise ValueError("Proxmox provisioning requires an IPv4 setup target")
        if configured_interface and configured_interface != target_interface:
            raise ValueError(
                "The provisioned target and --ip describe different IPv4 interfaces; "
                "specify the address once as HOST[/PREFIX]"
            )
        config.host = str(target_interface.ip)
        config.static_ipv4 = str(target_interface)
        return

    try:
        target_address = ipaddress.ip_address(raw_target)
    except ValueError:
        if configured_interface is None:
            raise ValueError(
                "Proxmox provisioning requires an IPv4 target; use HOST or "
                "HOST/PREFIX after the setup type"
            )
        config.host = str(configured_interface.ip)
        config.static_ipv4 = str(configured_interface)
        return

    if not isinstance(target_address, ipaddress.IPv4Address):
        raise ValueError("Proxmox provisioning requires an IPv4 setup target")
    if configured_interface and configured_interface.ip != target_address:
        raise ValueError(
            "The provisioned target and --ip use different IPv4 addresses; "
            "specify the address once as HOST[/PREFIX]"
        )

    config.host = str(target_address)
    config.static_ipv4 = str(
        configured_interface or ipaddress.ip_interface(f"{target_address}/24")
    )


def _apply_hosted_proxmox_defaults(
    config: SetupConfig,
    workspace: Optional[str],
) -> None:
    """Resolve saved Proxmox host details and expand shorthand storage specs."""
    if not isinstance(config.hosted_node, str) or not config.hosted_node:
        return

    if config.machine_type == DEFAULT_MACHINE_TYPE:
        config.machine_type = "vm"

    guest_key_was_provided = bool(config.ssh_key)
    host = find_proxmox_host(str(config.hosted_node), workspace)
    registered_host_key = host.ssh_key if host else None
    if host:
        if config.hosted_node == host.name:
            config.hosted_node = host.address
        if not config.hosted_key and registered_host_key:
            config.hosted_key = registered_host_key
        if not config.hosted_bridge:
            config.hosted_bridge = host.default_bridge or (
                host.facts.default_bridge if host.facts else None
            )

    if not config.ssh_key:
        config.ssh_key = resolve_guest_ssh_key(
            registered_host_key,
            home=_local_user_home(),
        )
        # Keep the saved-host fallback visible to validation when no local
        # public key can be inspected.  The resulting error identifies the
        # missing guest identity instead of silently provisioning keyless VMs.
        if not config.ssh_key and registered_host_key:
            config.ssh_key = registered_host_key

    if not config.hosted_key and config.ssh_key and guest_key_was_provided:
        config.hosted_key = config.ssh_key

    if not validate_host(str(config.hosted_node)):
        return
    _normalize_provisioned_target(config)

    storage_specs = _normalize_nested_specs(config.container_storage)
    if not storage_specs:
        return

    root_pool = _default_root_storage_for_host(host) if host else None
    template_pool = _default_template_storage_for_host(host) if host else None
    root_pool = root_pool or "auto"
    template_pool = template_pool or "auto"
    updated_specs: list[list[str]] = []
    changed = False

    for spec in storage_specs:
        normalized = list(spec)
        if normalized and normalized[0] == "root":
            if len(normalized) == 2 and _is_storage_amount(normalized[1]):
                normalized = ["root", root_pool, normalized[1]]
                changed = True
            elif len(normalized) == 3 and normalized[1] in {"default", "host"}:
                normalized = ["root", root_pool, normalized[2]]
                changed = True
        elif normalized and normalized[0] == "template":
            if len(normalized) == 1:
                normalized = ["template", template_pool]
                changed = True
        elif normalized:
            if len(normalized) == 2 and _is_storage_amount(normalized[1]):
                normalized = [normalized[0], root_pool, normalized[1]]
                changed = True
            elif len(normalized) == 3 and normalized[1] in {"default", "host"}:
                normalized = [normalized[0], root_pool, normalized[2]]
                changed = True
        updated_specs.append(normalized)

    if changed:
        config.container_storage = updated_specs


def prepare_validated_runtime_config(
    config: SetupConfig,
    workspace: Optional[str],
) -> SetupConfig:
    """Apply saved-host defaults, resolve credentials, and validate a setup."""
    _apply_hosted_proxmox_defaults(config, workspace)
    runtime_config = prepare_runtime_config(config, workspace)
    validate_timezone_name(runtime_config.timezone)
    validate_apt_packages(runtime_config.apt_packages)
    validate_agent_repositories(runtime_config.agent_repos)
    validate_agent_git_settings(runtime_config)
    validate_browser_automation_settings(runtime_config)
    validate_notification_args(runtime_config.notify_specs)
    validate_ssl_email(runtime_config.ssl_email)
    validate_deploy_specs(runtime_config.deploy_specs)
    validate_deploy_targets(runtime_config.deploy_targets)
    validate_sync_specs(runtime_config.sync_specs)
    validate_backup_specs(runtime_config.backup_specs)
    validate_scrub_specs(runtime_config.scrub_specs)
    validate_web_interface_settings(runtime_config)
    validate_smb_mount_specs(runtime_config.smb_mounts)
    validate_samba_settings(runtime_config)
    validate_samba_share_specs(
        runtime_config.samba_shares,
        runtime_config.share_credentials,
    )
    validate_gogs_settings(runtime_config)
    validate_antistatic_settings(runtime_config)
    validate_hosted_flags(runtime_config)
    validate_network_setup_settings(runtime_config)
    validate_rdp_settings(runtime_config)
    validate_samba_share_credentials(runtime_config)
    return runtime_config


def register_proxmox_setup_host(
    config: SetupConfig,
    workspace: Optional[str] = None,
) -> None:
    """Register a successfully configured ``server_proxmox`` host."""
    if config.system_type != "server_proxmox" or config.dry_run:
        return

    host = ProxmoxHost(
        name=config.friendly_name or config.host,
        address=config.host,
        user="root",
        ssh_key=config.ssh_key,
    )
    registered = sync_proxmox_host(host, workspace)
    print(f"Registered Proxmox host '{registered.name}' ({registered.address}).")


def adopt_verified_network_host(
    config: SetupConfig,
    runtime_config: SetupConfig,
    previous_host: str,
) -> Optional[str]:
    """Move saved setup identity to the controller-verified network address."""

    if runtime_config.activate_network and not runtime_config.dry_run:
        config.activate_network = False
    if runtime_config.host == previous_host:
        return None
    config.host = runtime_config.host
    return previous_host


def remove_replaced_setup_cache(previous_host: Optional[str], current_host: str) -> None:
    """Remove the obsolete cache key after the replacement host was saved."""

    if not previous_host:
        return
    previous_path = get_cache_path_for_host(previous_host)
    current_path = get_cache_path_for_host(current_host)
    if previous_path == current_path:
        return
    try:
        os.unlink(previous_path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        print(
            f"Warning: new host configuration was saved, but the old cache "
            f"could not be removed: {exc}"
        )


def run_remote_setup(config: SetupConfig) -> int:
    """Run one setup while preventing overlapping mutation of the same target."""
    if config.dry_run:
        return _run_remote_setup_locked(config)
    lock_target = (
        "local"
        if config.host in {"localhost", "127.0.0.1", "::1"}
        else config.host.lower().rstrip(".")
    )
    try:
        with resource_lock("setup-target", lock_target):
            return _run_remote_setup_locked(config)
    except ResourceBusyError as exc:
        print(f"Error: {exc}")
        return 1


def _run_remote_setup_locked(config: SetupConfig) -> int:
    setup_timeout = validate_positive_integer(
        os.environ.get("BASALTWATER_SETUP_TIMEOUT", "14400"), "Setup timeout",
    )
    _LAST_REMOTE_ACCESS_DETAILS.clear()
    is_local = config.host in {"localhost", "127.0.0.1", "::1"}
    
    if is_local and not config.dry_run and os.geteuid() != 0:
        print("Error: Local setup requires root privileges. Please run with sudo.")
        return 1

    remote_user = "root"
    control_path: Optional[str] = None
    if not is_local:
        control_path = get_ssh_control_path(
            config.host,
            remote_user,
            config.ssh_key,
        )
        if not config.dry_run and not ensure_remote_sudo(
            config.host,
            remote_user,
            config.ssh_key,
            control_path=control_path,
        ):
            return 1

    build_dir = tempfile.mkdtemp(prefix="basaltwater_setup_build_")
    try:
        copy_project_files(build_dir)
        
        if config.deploy_specs:
            deploy_dir = os.path.join(build_dir, "deployments")
            os.makedirs(deploy_dir, exist_ok=True)
            prepare_deployments(config, deploy_dir)

        if config.copy_agent_config or config.copy_agent_keys:
            agent_payload_dir = os.path.join(build_dir, AGENT_PAYLOAD_DIRNAME)
            prepare_agent_payload(config, agent_payload_dir)
            config.agent_payload = True

        if (
            config.device_pairing_auth_file
            or config.device_pairing_auth_username
            or config.device_pairing_auth_password
        ):
            pairing_payload_dir = os.path.join(
                build_dir, DEVICE_PAIRING_PAYLOAD_DIRNAME
            )
            prepare_device_pairing_payload(config, pairing_payload_dir)
            config.device_pairing_payload = True

        if config.web_panel_auth_password is not None:
            web_panel_payload_dir = os.path.join(
                build_dir, WEB_PANEL_PAYLOAD_DIRNAME
            )
            prepare_web_panel_payload(config, web_panel_payload_dir)
            config.web_panel_payload = True

        remote_arg_tokens = _expand_remote_args(config.to_remote_args())
        if config.agent_auth_source == "login" and config.install_codex:
            interactive = sys.stdin.isatty() and sys.stdout.isatty()
            remote_arg_tokens.extend(("--agent-auth-mode", "login" if interactive else "check"))
        _write_remote_args_file(build_dir, remote_arg_tokens)
        remote_args_path = os.path.join(REMOTE_INSTALL_DIR, REMOTE_ARGS_FILENAME)
        command_tokens = [
            sys.executable,
            os.path.join(REMOTE_INSTALL_DIR, "remote_setup.py"),
            "--args-file",
            remote_args_path,
        ]
        
        if config.dry_run:
            print("\n" + "=" * 60)
            print("[DRY RUN] Would execute:")
            if is_local:
                print(f"  Copy files to {REMOTE_INSTALL_DIR}")
                print(f"  Run: {shlex.join(command_tokens)}")
            else:
                print(f"  Upload files to {config.host}:{REMOTE_INSTALL_DIR}")
                print(f"  Run: {shlex.join(command_tokens)}")
            print("  Automatically migrate any recent infra-tools installation before setup")
            if config.activate_network:
                print("  Verify SSH on every requested address, then persist the network change")
            print("=" * 60)
            return 0

        if is_local:
            print(f"\n{'='*60}")
            print("Running setup locally...")
            print(f"{'='*60}")
            
            env = os.environ.copy()
            env["LC_ALL"] = "C"
            # The child writes through a pipe so setup progress is relayed by
            # this process. Disable Python buffering to keep APT and setup
            # status visible during long-running local installs.
            env["PYTHONUNBUFFERED"] = "1"
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            
            try:
                with payload_workspace(setup_timeout) as payload:
                    move_payloads(build_dir, payload)
                    from lib.setup_upgrade import prepare_target_runtime
                    prepare_target_runtime(build_dir, config.username)
                    link_payloads(payload, REMOTE_INSTALL_DIR)
                    command_tokens[-1] = os.path.join(payload, REMOTE_ARGS_FILENAME)
                    try:
                        returncode = run_streamed(
                            command_tokens, timeout=setup_timeout,
                            on_output=_relay_setup_output, env=env, cwd=REMOTE_INSTALL_DIR,
                        )
                    finally:
                        for name in PAYLOAD_NAMES:
                            path = os.path.join(REMOTE_INSTALL_DIR, name)
                            if os.path.islink(path):
                                os.unlink(path)
                return finish_network_transition(config, returncode)
            except Exception as e:
                print(f"Error running local setup: {e}")
                return finish_network_transition(config, 1)
        else:
            runtime_data = create_tar_from_dir(build_dir, exclude_payloads=True)
            tar_data = runtime_data + _create_payload_archive(build_dir)
            
            # Receive code into a private staging tree before touching either
            # installation. Only the bounded first archive is consumed here;
            # the private payload archive remains on stdin for setup_payloads.
            remote_shell_cmd = (
                'set -eu; '
                f'basaltwater_stage=$(mktemp -d {shlex.quote(os.path.dirname(REMOTE_INSTALL_DIR))}/.basaltwater-stage.XXXXXX); '
                "trap 'rm -rf \"$basaltwater_stage\"' EXIT; "
                f'head -c {len(runtime_data)} | tar xzf - -C "$basaltwater_stage" --no-same-owner; '
                'cd "$basaltwater_stage"; '
                'export PYTHONDONTWRITEBYTECODE=1; '
                'basaltwater_runtime_locked=0; '
                f'if [ ! -e {shlex.quote(os.path.join(os.path.dirname(REMOTE_INSTALL_DIR), "infra_tools"))} ]; then '
                'exec 9>/run/lock/basaltwater-setup.lock; flock --exclusive --nonblock 9; '
                'basaltwater_runtime_locked=1; fi; '
                f'python3 -u -m lib.setup_upgrade --username {shlex.quote(config.username)}; '
                f'cd {shlex.quote(REMOTE_INSTALL_DIR)}; '
                'if [ "$basaltwater_runtime_locked" = 0 ]; then '
                'exec 9>/run/lock/basaltwater-setup.lock; flock --exclusive --nonblock 9; fi; '
                f'python3 -u -m lib.setup_payloads --timeout {setup_timeout}'
            )
            locked_remote_shell_cmd = shlex.join(
                [
                    "flock",
                    "--exclusive",
                    "--nonblock",
                    "/run/lock/basaltwater-setup-bootstrap.lock",
                    "/bin/bash",
                    "-o",
                    "pipefail",
                    "-c",
                    remote_shell_cmd,
                ]
            )
            ssh_cmd = build_ssh_command(
                config.host,
                remote_user,
                config.ssh_key,
                remote_command=shlex.join([
                    "timeout", "--signal=TERM", "--kill-after=10s", str(setup_timeout),
                    "/bin/sh", "-c", locked_remote_shell_cmd,
                ]),
                batch_mode=ssh_batch_mode(),
                connect_timeout=30,
                server_alive_interval=30,
                control_path=control_path,
            )
            
            ssh_env = os.environ.copy()
            ssh_env["LC_ALL"] = "C"
            
            try:
                returncode = run_streamed(
                    ssh_cmd, timeout=setup_timeout, on_output=_relay_setup_output,
                    input_data=tar_data, env=ssh_env,
                )
                return finish_network_transition(config, returncode)
            except Exception as e:
                print(f"Error running remote setup: {e}")
                return finish_network_transition(config, 1)

    finally:
        if os.path.exists(build_dir):
            shutil.rmtree(build_dir)


def _relay_setup_output(line: str) -> None:
    _record_remote_access_output(line)
    print(line, end="", flush=True)


def setup_main(system_type: str, description: str, success_msg_fn: Callable[[SetupConfig], None]) -> int:
    allow_steps = (system_type == "custom_steps")
    parser = create_argument_parser(description, allow_steps)
    
    if argcomplete:
        argcomplete.autocomplete(parser)
    
    args = parser.parse_args()
    if getattr(args, 'workspace', None):
        try:
            validate_workspace_dir(args.workspace)
        except ValueError as e:
            print(f"Error: {e}")
            return 1
        set_workspace_dir(args.workspace)

    if getattr(args, "interactive", False) is True:
        try:
            run_interactive_setup(args)
        except (EOFError, KeyboardInterrupt, ValueError) as exc:
            print(f"Error: {exc}")
            return 1

    try:
        prompt_for_missing_passwords(args, system_type)
    except (EOFError, KeyboardInterrupt, ValueError) as exc:
        print(f"Error: {exc}")
        return 1

    explicit_ipv4 = getattr(args, "static_ipv4", None)
    if getattr(args, "hosted_node", None) and isinstance(explicit_ipv4, str) and explicit_ipv4:
        print(
            "Error: --ip is redundant with --provision-on; put the guest address "
            "and optional prefix in the positional HOST[/PREFIX] target"
        )
        return 1
    
    config = SetupConfig.from_args(args, system_type)

    if not validate_username(config.username):
        print(f"Error: Invalid username: {config.username}")
        return 1

    if config.hosted_node and config.activate_network is True:
        print(
            "Error: --activate-network is for patching an existing Proxmox "
            "guest; provisioned guests boot directly on their requested address"
        )
        return 1

    try:
        runtime_config = prepare_validated_runtime_config(
            config,
            getattr(args, "workspace", None),
        )
    except ValueError as e:
        print(f"Error: {e}")
        return 1

    if not validate_host(config.host):
        print(f"Error: Invalid IP address or hostname: {config.host}")
        return 1

    # Provision a Proxmox guest as the first phase of regular setup.
    if config.hosted_node:
        if config.machine_type == "vm":
            from lib.proxmox_vm import provision_vm, VMAlreadyExists

            print(f"\n{'='*60}")
            print(f"Provisioning VM on {config.hosted_node}...")
            print(f"{'='*60}")

            try:
                provision_vm(config, image=config.vm_image)
            except VMAlreadyExists:
                if config.storage_mounts or config.storage_caches:
                    print(
                        "Error: named VM data disks and caches are provisioning-only; "
                        "refusing to adopt disks on an existing unsaved VM"
                    )
                    return 1
                print("  ✓ VM already provisioned, skipping creation")
            except Exception as e:
                print(f"\n✗ Failed to provision VM: {e}")
                return 1
        else:
            from lib.proxmox_node import provision_container, ContainerAlreadyExists

            print(f"\n{'='*60}")
            print(f"Provisioning LXC container on {config.hosted_node}...")
            print(f"{'='*60}")

            try:
                provision_container(config)
            except ContainerAlreadyExists:
                print("  ✓ Container already provisioned, skipping creation")
            except Exception as e:
                print(f"\n✗ Failed to provision container: {e}")
                return 1

        try:
            runtime_config = prepare_validated_runtime_config(
                config,
                getattr(args, "workspace", None),
            )
        except ValueError as e:
            print(f"Error: {e}")
            return 1

    print_setup_summary(config, description)
    
    if not config.dry_run:
        store_cli_credentials(config)
        save_setup_command(config, operation="setup")
    
    previous_host = config.host
    replaced_cache_host: Optional[str] = None
    start_time = time.time()
    returncode = 1
    try:
        returncode = run_remote_setup(runtime_config)
        if returncode == 0:
            replaced_cache_host = adopt_verified_network_host(
                config,
                runtime_config,
                previous_host,
            )
    finally:
        end_time = time.time()
        success = (returncode == 0)
        if not config.dry_run:
            save_setup_command(config, start_time, end_time, success, operation="setup")

    if replaced_cache_host:
        remove_replaced_setup_cache(replaced_cache_host, config.host)
    
    if returncode != 0:
        print(f"\n✗ Setup failed (exit code: {returncode})")
        return 1

    try:
        register_proxmox_setup_host(config, getattr(args, "workspace", None))
    except ValueError as exc:
        print(f"\n✗ Setup completed, but Proxmox host registration failed: {exc}")
        return 1
    
    print()
    print("=" * 60)
    print("Setup Complete!")
    print("=" * 60)
    success_msg_fn(config)
    if "t3code" in (config.web_interfaces or []):
        print()
        if "t3code" in (config.device_pairing_providers or []):
            print("Protected T3 Code device enrollment:")
            print(
                "  Use the T3 Code pairing HTTPS endpoint shown in setup output."
            )
            print("  Sign in with the configured Basic Auth account, then pair this browser.")
        else:
            print("T3 Code pairing (one-time):")
            print(f"  basaltw agent web pair {config.host} {config.username}")
            if config.ssh_key:
                print(
                    f"  Add --key {config.ssh_key} if the SSH key is not your "
                    "default identity"
                )
    print("=" * 60)
    
    return 0
