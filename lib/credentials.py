#!/usr/bin/env python3

from __future__ import annotations

import copy
import json
import os
import subprocess

from lib.atomic_io import write_json_atomic
from lib.concurrency import resource_lock
from lib.config import SetupConfig
from lib.git_credentials import (
    MAX_GIT_CA_BUNDLE_BYTES,
    encode_git_ca_pem,
    normalize_git_https_origin,
    parse_git_ca_ssh_source,
    validate_git_ca_pem,
)
from lib.ssh_utils import (
    build_ssh_command,
    shell_join,
    ssh_batch_mode,
    ssh_process_timeout,
)
from lib.workspace import (
    ensure_workspace_dir,
    get_credentials_path,
    get_known_hosts_path,
)


CREDENTIALS_VERSION = 1


def _normalize_credential_username(username: str) -> str:
    normalized = username.strip()
    if not normalized:
        raise ValueError("Credential username must not be empty")
    if ":" in normalized:
        raise ValueError("Credential username must not contain ':'")
    if "," in normalized:
        raise ValueError("Credential username must not contain ','")
    if any(ord(char) < 32 or ord(char) == 127 for char in normalized):
        raise ValueError("Credential username must not contain control characters")
    return normalized


def _normalize_credential_password(password: str) -> str:
    normalized = password.strip()
    if not normalized:
        raise ValueError("Credential password must not be empty")
    return normalized


def _ensure_secure_credentials_file(path: str) -> None:
    if not os.path.exists(path):
        return

    current_mode = os.stat(path).st_mode & 0o777
    if current_mode == 0o600:
        return

    try:
        os.chmod(path, 0o600)
    except OSError as exc:
        raise ValueError(f"Credential store must use 0600 permissions: {path}") from exc

    updated_mode = os.stat(path).st_mode & 0o777
    if updated_mode != 0o600:
        raise ValueError(f"Credential store must use 0600 permissions: {path}")


def _load_workspace_credentials_unlocked(workspace: str | None = None) -> dict[str, str]:
    ensure_workspace_dir(workspace)
    credentials_path = get_credentials_path(workspace)
    if not os.path.exists(credentials_path):
        return {}

    _ensure_secure_credentials_file(credentials_path)

    with open(credentials_path, "r", encoding="utf-8") as file_obj:
        payload = json.load(file_obj)

    if not isinstance(payload, dict):
        raise ValueError(
            f"Credential store must contain a JSON object, got {type(payload).__name__}"
        )

    if payload.get("version") != CREDENTIALS_VERSION:
        raise ValueError("Unsupported credential store version")

    credentials_obj = payload.get("credentials", {})
    if not isinstance(credentials_obj, dict):
        raise ValueError("Credential store credentials field must be an object")

    credentials: dict[str, str] = {}
    for username, entry in credentials_obj.items():
        normalized_username = _normalize_credential_username(str(username))
        if not isinstance(entry, dict):
            raise ValueError(f"Credential entry for {normalized_username} must be an object")
        password = entry.get("password")
        if not isinstance(password, str):
            raise ValueError(f"Credential entry for {normalized_username} must contain a password string")
        credentials[normalized_username] = password

    return credentials


def load_workspace_credentials(workspace: str | None = None) -> dict[str, str]:
    """Load saved workspace credentials."""
    path = get_credentials_path(workspace)
    with resource_lock("workspace-credentials", path, wait=True):
        return _load_workspace_credentials_unlocked(workspace)


def _save_workspace_credentials_unlocked(
    credentials: dict[str, str], workspace: str | None = None
) -> None:
    ensure_workspace_dir(workspace)
    credentials_path = get_credentials_path(workspace)
    normalized_credentials = {
        _normalize_credential_username(username): _normalize_credential_password(password)
        for username, password in credentials.items()
    }
    payload = {
        "version": CREDENTIALS_VERSION,
        "credentials": {
            username: {"password": password}
            for username, password in sorted(normalized_credentials.items())
        },
    }

    write_json_atomic(credentials_path, payload, mode=0o600, sort_keys=True)


def save_workspace_credentials(credentials: dict[str, str], workspace: str | None = None) -> None:
    """Persist workspace credentials using the versioned JSON layout."""
    path = get_credentials_path(workspace)
    with resource_lock("workspace-credentials", path, wait=True):
        _save_workspace_credentials_unlocked(credentials, workspace)


def set_workspace_credential(username: str, password: str, workspace: str | None = None) -> None:
    """Add or replace a workspace credential."""
    path = get_credentials_path(workspace)
    with resource_lock("workspace-credentials", path, wait=True):
        credentials = _load_workspace_credentials_unlocked(workspace)
        credentials[_normalize_credential_username(username)] = _normalize_credential_password(password)
        _save_workspace_credentials_unlocked(credentials, workspace)


def remove_workspace_credential(username: str, workspace: str | None = None) -> bool:
    """Remove a saved workspace credential."""
    normalized_username = _normalize_credential_username(username)
    path = get_credentials_path(workspace)
    with resource_lock("workspace-credentials", path, wait=True):
        credentials = _load_workspace_credentials_unlocked(workspace)
        removed = credentials.pop(normalized_username, None) is not None
        if removed or os.path.exists(path):
            _save_workspace_credentials_unlocked(credentials, workspace)
        return removed


def list_workspace_credentials(workspace: str | None = None) -> list[str]:
    """Return saved credential usernames."""
    return sorted(load_workspace_credentials(workspace))


def get_runtime_credential(config: SetupConfig, username: str) -> str | None:
    """Return the last runtime credential matching ``username``, if present."""
    requested_username = _normalize_credential_username(username)
    password: str | None = None
    for credential_spec in config.share_credentials or []:
        if len(credential_spec) != 2:
            raise ValueError("Credential spec requires USERNAME and PASSWORD")
        credential_username, credential_password = credential_spec
        if _normalize_credential_username(credential_username) == requested_username:
            password = _normalize_credential_password(credential_password)
    return password


def store_cli_credentials(config: SetupConfig, workspace: str | None = None) -> None:
    """Persist credentials supplied on the command line."""
    path = get_credentials_path(workspace)
    with resource_lock("workspace-credentials", path, wait=True):
        _store_cli_credentials_unlocked(config, workspace)


def _store_cli_credentials_unlocked(
    config: SetupConfig, workspace: str | None = None
) -> None:
    credentials = _load_workspace_credentials_unlocked(workspace)
    if config.share_credentials:
        for username, password in config.share_credentials:
            credentials[_normalize_credential_username(username)] = _normalize_credential_password(password)

    if config.samba_shares:
        for share_spec in config.samba_shares:
            if len(share_spec) < 4:
                continue
            for user_spec in str(share_spec[3]).split(","):
                normalized_user = user_spec.strip()
                if ":" not in normalized_user:
                    continue
                username, password = normalized_user.split(":", 1)
                credentials[_normalize_credential_username(username)] = _normalize_credential_password(password)

    if config.smb_mounts:
        for mount_spec in config.smb_mounts:
            credentials_field = str(mount_spec[2]) if len(mount_spec) >= 3 else ""
            if len(mount_spec) < 3 or ":" not in credentials_field:
                continue
            username, password = credentials_field.split(":", 1)
            credentials[_normalize_credential_username(username)] = _normalize_credential_password(password)

    if not credentials:
        return

    _save_workspace_credentials_unlocked(credentials, workspace)


def _collect_required_credential_usernames(config: SetupConfig) -> list[str]:
    usernames: list[str] = []
    seen_usernames: set[str] = set()

    if config.antistatic_admin:
        usernames.append(config.antistatic_admin)
        seen_usernames.add(config.antistatic_admin)
    if config.syncthing_admin and config.syncthing_admin not in seen_usernames:
        usernames.append(config.syncthing_admin)
        seen_usernames.add(config.syncthing_admin)

    for share_spec in config.samba_shares or []:
        if len(share_spec) < 4:
            continue
        for user_spec in str(share_spec[3]).split(","):
            normalized_user = user_spec.strip()
            if not normalized_user or ":" in normalized_user or normalized_user in seen_usernames:
                continue
            seen_usernames.add(normalized_user)
            usernames.append(normalized_user)

    return usernames


def _resolve_share_credentials(config: SetupConfig, credential_map: dict[str, str]) -> list[list[str]]:
    resolved_credentials: list[list[str]] = []
    seen_usernames: set[str] = set()

    if config.share_credentials:
        for username, password in config.share_credentials:
            normalized_username = _normalize_credential_username(username)
            normalized_password = _normalize_credential_password(password)
            credential_map[normalized_username] = normalized_password
            if normalized_username not in seen_usernames:
                seen_usernames.add(normalized_username)
                resolved_credentials.append([normalized_username, normalized_password])

    for username in _collect_required_credential_usernames(config):
        password = credential_map.get(username)
        if password is None:
            if username == config.antistatic_admin:
                raise ValueError(
                    f"Missing credential for Antistatic admin: {username}. "
                    "Run infra-tools credentials set USERNAME to enter it securely"
                )
            if username == config.syncthing_admin:
                raise ValueError(
                    f"Missing credential for Syncthing admin: {username}. "
                    "Run infra-tools credentials set USERNAME to enter it securely"
                )
            raise ValueError(
                f"Missing credential for share user: {username}. "
                "Run infra-tools credentials set USERNAME or use --credential USERNAME PASSWORD"
            )
        if username not in seen_usernames:
            seen_usernames.add(username)
            resolved_credentials.append([username, password])

    if config.gogs:
        gogs_admin = _normalize_credential_username(config.username)
        password = credential_map.get(gogs_admin)
        if password is not None and gogs_admin not in seen_usernames:
            seen_usernames.add(gogs_admin)
            resolved_credentials.append(
                [gogs_admin, _normalize_credential_password(password)]
            )

    for credential_spec in config.git_credentials or []:
        if len(credential_spec) != 2:
            raise ValueError(
                "--git-credential requires HTTPS_ORIGIN and USERNAME"
            )
        origin, username = credential_spec
        normalized_origin = normalize_git_https_origin(origin)
        normalized_username = _normalize_credential_username(username)
        password = credential_map.get(normalized_username)
        if password is None:
            raise ValueError(
                f"Missing credential for Git user {normalized_username} at "
                f"{normalized_origin}. Run infra-tools credentials set USERNAME "
                "or use --credential USERNAME PASSWORD"
            )
        if normalized_username not in seen_usernames:
            seen_usernames.add(normalized_username)
            resolved_credentials.append(
                [normalized_username, _normalize_credential_password(password)]
            )

    return resolved_credentials


def _read_git_ca_bundle_from_ssh(
    source_path: str,
    workspace: str | None,
) -> str:
    """Read a bounded public certificate bundle over host-key-verified SSH."""
    ssh_source = parse_git_ca_ssh_source(source_path)
    if ssh_source is None:
        raise ValueError("Git CA SSH source expected")

    known_hosts_path = get_known_hosts_path(workspace)
    if os.path.islink(known_hosts_path) or not os.path.isfile(known_hosts_path):
        raise ValueError(
            f"SSH host key for Git CA source {ssh_source.host} is not enrolled. "
            f"Run infra-tools ssh-key enroll {ssh_source.host}"
        )
    read_command = [
        "head",
        "-c",
        str(MAX_GIT_CA_BUNDLE_BYTES + 1),
        "--",
        ssh_source.path,
    ]
    if ssh_source.username != "root":
        read_command = ["sudo", "-n", *read_command]
    batch_mode = ssh_batch_mode()
    command = build_ssh_command(
        ssh_source.host,
        ssh_source.username,
        port=ssh_source.port,
        remote_command=shell_join(read_command),
        batch_mode=batch_mode,
        known_hosts_path=known_hosts_path,
    )
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=ssh_process_timeout(60, batch_mode=batch_mode),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(
            f"Could not retrieve Git CA certificate over SSH from "
            f"{ssh_source.username}@{ssh_source.host}: {exc}"
        ) from exc
    if result.returncode != 0:
        detail = (result.stderr or "SSH certificate retrieval failed").strip()
        raise ValueError(
            f"Could not retrieve Git CA certificate over SSH from "
            f"{ssh_source.username}@{ssh_source.host}: {detail[:240]}"
        )
    try:
        return validate_git_ca_pem(result.stdout)
    except ValueError as exc:
        raise ValueError(
            f"Invalid Git CA certificate source {source_path}: {exc}"
        ) from exc


def _read_git_ca_bundle(
    source_path: str,
    workspace: str | None = None,
) -> str:
    """Read and validate one local or authenticated SSH PEM bundle."""
    if parse_git_ca_ssh_source(source_path) is not None:
        return _read_git_ca_bundle_from_ssh(source_path, workspace)

    from lib.validation import validate_filesystem_path

    validate_filesystem_path(source_path, must_exist=True)
    if os.path.islink(source_path) or not os.path.isfile(source_path):
        raise ValueError(
            f"Git CA certificate source must be a regular non-symlink file: {source_path}"
        )
    source_mode = os.stat(source_path).st_mode & 0o777
    if source_mode & 0o022:
        raise ValueError(
            f"Git CA certificate source must not be group/world-writable: {source_path}"
        )
    if os.path.getsize(source_path) > MAX_GIT_CA_BUNDLE_BYTES:
        raise ValueError(f"Git CA certificate source exceeds 1 MiB: {source_path}")
    with open(source_path, "r", encoding="utf-8") as file_obj:
        content = file_obj.read()

    try:
        return validate_git_ca_pem(content)
    except ValueError as exc:
        raise ValueError(f"Invalid Git CA certificate source {source_path}: {exc}") from exc


def _prepare_git_ca_pems(
    config: SetupConfig,
    workspace: str | None = None,
) -> list[list[str]] | None:
    prepared: list[list[str]] = []
    for ca_spec in config.git_ca_certificates or []:
        if len(ca_spec) != 2:
            raise ValueError(
                "--git-ca-certificate requires HTTPS_ORIGIN and SOURCE"
            )
        origin, source_path = ca_spec
        normalized_origin = normalize_git_https_origin(origin)
        content = _read_git_ca_bundle(source_path, workspace)
        prepared.append([normalized_origin, encode_git_ca_pem(content)])
    return prepared or None


def _resolve_named_smb_mounts(
    smb_mounts: list[list[str]] | None,
    credential_map: dict[str, str],
) -> list[list[str]] | None:
    if not smb_mounts:
        return smb_mounts

    resolved_mounts: list[list[str]] = []
    for mount_spec in smb_mounts:
        resolved_spec = list(mount_spec)
        if len(resolved_spec) >= 3 and ":" not in resolved_spec[2]:
            username = _normalize_credential_username(resolved_spec[2])
            password = credential_map.get(username)
            if password is None:
                raise ValueError(
                    f"Missing credential for SMB mount user: {username}. "
                    "Run infra-tools credentials set USERNAME or use --credential USERNAME PASSWORD"
                )
            resolved_spec[2] = f"{username}:{password}"
        resolved_mounts.append(resolved_spec)
    return resolved_mounts


def prepare_runtime_config(config: SetupConfig, workspace: str | None = None) -> SetupConfig:
    """Return a runtime config with workspace credentials resolved."""
    runtime_config = copy.deepcopy(config)
    credential_map = load_workspace_credentials(workspace)
    runtime_config.share_credentials = _resolve_share_credentials(runtime_config, credential_map) or None
    runtime_config.smb_mounts = _resolve_named_smb_mounts(runtime_config.smb_mounts, credential_map)
    runtime_config.git_ca_pems = _prepare_git_ca_pems(runtime_config, workspace)
    runtime_config.git_ca_certificates = None
    return runtime_config
