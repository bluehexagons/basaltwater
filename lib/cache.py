#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import asdict
from typing import Optional, Any

from lib.state_read import StateReadError, read_state_object
from lib.config import SetupConfig
from lib.atomic_io import write_json_atomic
from lib.validators import validate_username
from lib.workspace import get_history_dir, get_setup_cache_dir


def _get_entrypoint_metadata(operation: str) -> dict[str, str]:
    """Return user-facing CLI metadata stored with workspace state."""

    return {
        "script": "infra-tools",
        "command": f"infra-tools {operation}",
    }


def get_cache_path_for_host(host: str) -> str:
    cache_dir = get_setup_cache_dir()
    os.makedirs(cache_dir, exist_ok=True)
    normalized_host = host.lower().rstrip('.')
    safe_host = re.sub(r'[^a-zA-Z0-9._-]', '_', normalized_host)
    host_hash = hashlib.sha256(normalized_host.encode()).hexdigest()[:8]
    return os.path.join(cache_dir, f"{safe_host}_{host_hash}.json")


def _get_history_path_for_run(host: str, operation: str, end_time: float) -> str:
    """Return a unique history filename for a completed setup or patch run."""

    history_dir = get_history_dir()
    os.makedirs(history_dir, mode=0o700, exist_ok=True)

    normalized_host = host.lower().rstrip(".")
    safe_host = re.sub(r"[^a-zA-Z0-9._-]", "_", normalized_host)
    host_hash = hashlib.sha256(normalized_host.encode()).hexdigest()[:8]
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(end_time))
    return os.path.join(history_dir, f"{timestamp}_{operation}_{safe_host}_{host_hash}.json")


def _write_history_entry(
    config: SetupConfig,
    *,
    operation: str,
    start_time: float,
    end_time: float,
    success: bool,
) -> None:
    """Persist a completed run entry in the workspace history directory."""

    history_path = _get_history_path_for_run(config.host, operation, end_time)
    history_data: dict[str, Any] = {
        "host": config.host,
        "system_type": config.system_type,
        "operation": operation,
        "args": config.to_dict(),
        "start_time": start_time,
        "end_time": end_time,
        "duration_seconds": max(0.0, end_time - start_time),
        "success": success,
    }
    history_data.update(_get_entrypoint_metadata(operation))

    if config.friendly_name:
        history_data["name"] = config.friendly_name
    if config.tags:
        history_data["tags"] = config.tags

    write_json_atomic(history_path, history_data)


def save_setup_command(
    config: SetupConfig,
    start_time: Optional[float] = None,
    end_time: Optional[float] = None,
    success: Optional[bool] = None,
    *,
    operation: str = "setup",
) -> None:
    cache_path = get_cache_path_for_host(config.host)
    _load_cache_file(cache_path, config.host)
    
    cache_data: dict[str, Any] = {
        "host": config.host,
        "system_type": config.system_type,
        "args": config.to_dict(),
    }
    cache_data.update(_get_entrypoint_metadata(operation))
    
    if config.friendly_name:
        cache_data["name"] = config.friendly_name
    if config.tags:
        cache_data["tags"] = config.tags
        
    # Add metadata if provided
    if start_time is not None:
        cache_data["last_start_time"] = start_time
    if end_time is not None:
        cache_data["last_end_time"] = end_time
    if success is not None:
        cache_data["last_success"] = success
    
    write_json_atomic(cache_path, cache_data)

    if start_time is not None and end_time is not None and success is not None:
        _write_history_entry(
            config,
            operation=operation,
            start_time=start_time,
            end_time=end_time,
            success=success,
        )


def _rewrite_cached_home_path(value: Any, old_home: str, new_home: str) -> Any:
    """Rewrite one schema-approved path value."""

    if isinstance(value, str) and old_home and (
        value == old_home or value.startswith(old_home + "/")
    ):
        return new_home + value[len(old_home):]
    return value


def _rewrite_cached_home_paths(value: Any, old_home: str, new_home: str) -> Any:
    """Rewrite only path-valued setup fields, leaving metadata and credentials intact."""

    if not isinstance(value, dict):
        return value
    updated = dict(value)
    if "agent_workspace" in updated:
        updated["agent_workspace"] = _rewrite_cached_home_path(
            updated["agent_workspace"], old_home, new_home
        )
    gogs = updated.get("gogs")
    if isinstance(gogs, list) and len(gogs) > 1:
        gogs = list(gogs)
        gogs[1] = _rewrite_cached_home_path(gogs[1], old_home, new_home)
        updated["gogs"] = gogs
    path_indexes = {
        "samba_shares": (2,),
        "smb_mounts": (0,),
        "sync_specs": (0, 1),
        "backup_specs": (0, 1),
        "scrub_specs": (0, 1),
        "deploy_specs": (0,),
        "storage_mounts": (1,),
    }
    for field, indexes in path_indexes.items():
        records = updated.get(field)
        if not isinstance(records, list):
            continue
        rewritten_records = []
        for record in records:
            if not isinstance(record, list):
                rewritten_records.append(record)
                continue
            rewritten = list(record)
            for index in indexes:
                if index < len(rewritten):
                    rewritten[index] = _rewrite_cached_home_path(
                        rewritten[index], old_home, new_home
                    )
            rewritten_records.append(rewritten)
        updated[field] = rewritten_records
    return updated


def rename_setup_command(
    host: str,
    *,
    old_username: str,
    new_username: str,
    old_home: str = "",
    new_home: str = "",
) -> None:
    """Update the current workspace cache after a verified remote rename.

    The cache metadata and historical records are preserved. Only the current
    setup arguments are changed, including path values below the old home.
    """

    if not validate_username(old_username) or not validate_username(new_username):
        raise ValueError("Invalid username in setup-cache rename")
    if old_username == new_username:
        raise ValueError("Setup-cache rename requires different usernames")

    cached = load_setup_command(host)
    if cached is None:
        raise ValueError(f"No cached setup found for {host}")
    cache_path = get_cache_path_for_host(cached.host)
    try:
        with open(cache_path, "r", encoding="utf-8") as file_obj:
            cache_data = json.load(file_obj)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not load setup cache for {cached.host}: {exc}") from exc
    if not isinstance(cache_data, dict) or not isinstance(cache_data.get("args"), dict):
        raise ValueError(f"Invalid setup cache for {cached.host}")

    args = cache_data["args"]
    if args.get("username") != old_username:
        raise ValueError(
            f"Setup cache username mismatch: expected {old_username!r}, "
            f"found {args.get('username')!r}"
        )
    updated_args = _rewrite_cached_home_paths(args, old_home, new_home)
    if not isinstance(updated_args, dict):
        raise ValueError("Setup cache arguments are not an object")
    updated_args["username"] = new_username
    cache_data["args"] = updated_args
    write_json_atomic(cache_path, cache_data, mode=0o600)


def _cache_record(path: str) -> dict[str, Any] | None:
    data = read_state_object(path)
    if data is None:
        return None
    if (
        not isinstance(data.get("host"), str) or not data["host"]
        or not isinstance(data.get("system_type"), str)
        or not isinstance(data.get("args"), dict)
        or ("name" in data and not isinstance(data["name"], str))
        or ("tags" in data and (not isinstance(data["tags"], list) or not all(isinstance(tag, str) for tag in data["tags"])))
    ):
        raise StateReadError(path, "invalid setup cache structure")
    return data


def _load_cache_file(cache_path: str, host: str) -> Optional[SetupConfig]:
    data = _cache_record(cache_path)
    if data is None:
        return None
    args_dict = dict(data["args"])
    if "name" in data and "friendly_name" not in args_dict:
        args_dict["friendly_name"] = data["name"]
    if "tags" in data and "tags" not in args_dict:
        args_dict["tags"] = data["tags"]
    try:
        config = SetupConfig.from_dict(host, data["system_type"], args_dict)
        if config.friendly_name is not None and not isinstance(config.friendly_name, str):
            raise ValueError("Invalid friendly name")
        if config.tags is not None and (not isinstance(config.tags, list) or not all(isinstance(tag, str) for tag in config.tags)):
            raise ValueError("Invalid tags")
        return config
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise StateReadError(cache_path, "invalid setup arguments") from exc


def _find_cache_by_name(name: str) -> Optional[SetupConfig]:
    needle = name.lower()
    for config in load_all_setup_commands():
        if needle == (config.friendly_name or "").lower() or any(
            needle == tag.lower() for tag in config.tags or []
        ):
            return config
    return None


def load_setup_command(host: str) -> Optional[SetupConfig]:
    cache_path = get_cache_path_for_host(host)
    if os.path.lexists(cache_path):
        return _load_cache_file(cache_path, host)
    return _find_cache_by_name(host)


def load_all_setup_commands(workspace: Optional[str] = None) -> list[SetupConfig]:
    """Load all saved configurations; incomplete inventories fail explicitly."""
    cache_dir = get_setup_cache_dir(workspace)
    if not os.path.exists(cache_dir):
        return []
    configs = []
    for filename in sorted(os.listdir(cache_dir)):
        if not filename.endswith(".json"):
            continue
        path = os.path.join(cache_dir, filename)
        data = _cache_record(path)
        if data is None:
            raise StateReadError(path, "cache disappeared during inventory")
        config = _load_cache_file(path, data["host"])
        if config is None:
            raise StateReadError(path, "cache disappeared during inventory")
        configs.append(config)
    return sorted(configs, key=lambda config: (config.friendly_name or config.host).lower())


def merge_setup_configs(
    cached_config: SetupConfig,
    new_config: SetupConfig,
    *,
    preserve_keys: set[str] | None = None,
) -> SetupConfig:
    merged_dict = asdict(cached_config)
    new_dict = asdict(new_config)
    preserve_keys = preserve_keys or set()

    # Selecting passwordless sudo is an explicit request for the capable,
    # non-hardened posture. SetupConfig still rejects callers that explicitly
    # combine this option with either hardened mode.
    if new_config.nopasswd:
        merged_dict["harden_agent"] = False
        merged_dict["harden_user"] = False
    
    for key, value in new_dict.items():
        if key in ('host', 'system_type'):
            continue

        if key in preserve_keys:
            continue

        if key == 'deploy_specs' and key in merged_dict:
            if merged_dict[key] is None:
                merged_dict[key] = value
            elif value is not None:
                existing_deploys = {(spec[0], spec[1]) for spec in merged_dict[key]}
                for deploy_spec in value:
                    deploy_tuple = (deploy_spec[0], deploy_spec[1])
                    if deploy_tuple not in existing_deploys:
                        merged_dict[key].append(deploy_spec)
                        existing_deploys.add(deploy_tuple)
        elif key == 'samba_shares' and key in merged_dict:
            if merged_dict[key] is None:
                merged_dict[key] = value
            elif value is not None:
                existing_shares = {tuple(share) for share in merged_dict[key]}
                for share_spec in value:
                    share_tuple = tuple(share_spec)
                    if share_tuple not in existing_shares:
                        merged_dict[key].append(share_spec)
                        existing_shares.add(share_tuple)
        elif key == 'share_credentials' and key in merged_dict:
            if merged_dict[key] is None:
                merged_dict[key] = value
            elif value is not None:
                merged_credentials = {
                    credential_spec[0]: credential_spec
                    for credential_spec in merged_dict[key]
                    if credential_spec and len(credential_spec) >= 2
                }
                for credential_spec in value:
                    if credential_spec and len(credential_spec) >= 2:
                        merged_credentials[credential_spec[0]] = credential_spec
                merged_dict[key] = list(merged_credentials.values())
        elif key in {'git_credentials', 'git_ca_certificates'}:
            if new_config.clear_git_credentials:
                merged_dict[key] = None
            elif value is not None:
                merged_specs = {
                    spec[0]: list(spec)
                    for spec in merged_dict.get(key) or []
                    if spec and len(spec) == 2
                }
                for spec in value:
                    if spec and len(spec) == 2:
                        merged_specs[spec[0]] = list(spec)
                merged_dict[key] = list(merged_specs.values()) or None
        elif key == 'tags':
            if value is not None:
                merged_dict[key] = value
        elif value is not None:
            merged_dict[key] = value

    if new_config.disable_syncthing:
        merged_dict["enable_syncthing"] = False
        merged_dict["disable_syncthing"] = True
        merged_dict["syncthing_admin"] = None
    
    return SetupConfig(**merged_dict)
