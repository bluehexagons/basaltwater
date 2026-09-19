#!/usr/bin/env python3
"""Inspect and remove obsolete local basaltwater configuration state."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, cast

from lib.atomic_io import write_json_atomic
from lib.config import SetupConfig
from lib.proxmox_hosts import (
    ProxmoxHost,
    _validate_host_record,
    get_proxmox_hosts_path,
)
from lib.types import JSONDict, JSONList
from lib.validation import validate_workspace_dir
from lib.validators import validate_host
from lib.workspace import get_setup_cache_dir, normalize_workspace_dir


@dataclass(frozen=True)
class CleanupFinding:
    """A local state item that is obsolete or needs manual attention."""

    category: str
    path: str
    reason: str
    action: str
    record_index: Optional[int] = None


@dataclass
class CleanupPlan:
    """The state changes identified by a cleanup inspection."""

    setup_findings: list[CleanupFinding] = field(default_factory=list)
    proxmox_findings: list[CleanupFinding] = field(default_factory=list)

    @property
    def findings(self) -> list[CleanupFinding]:
        return [*self.setup_findings, *self.proxmox_findings]

    @property
    def actionable_findings(self) -> list[CleanupFinding]:
        return [finding for finding in self.findings if finding.action != "manual"]


def _same_host(left: str, right: str) -> bool:
    return left.strip().lower().rstrip(".") == right.strip().lower().rstrip(".")


def _cache_entry_host(data: Any) -> Optional[str]:
    if not isinstance(data, dict):
        return None
    host = data.get("host")
    return host if isinstance(host, str) and host.strip() else None


def _cache_path_for_host(cache_dir: str, host: str) -> str:
    """Return the canonical cache path without creating the cache directory."""

    normalized_host = host.lower().rstrip(".")
    safe_host = re.sub(r"[^a-zA-Z0-9._-]", "_", normalized_host)
    host_hash = hashlib.sha256(normalized_host.encode()).hexdigest()[:8]
    return os.path.join(cache_dir, f"{safe_host}_{host_hash}.json")


def _setup_cache_findings(
    workspace: str,
    target_host: Optional[str],
) -> list[CleanupFinding]:
    cache_dir = get_setup_cache_dir(workspace)
    if not os.path.isdir(cache_dir):
        return []

    findings: list[CleanupFinding] = []
    try:
        entries = sorted(os.scandir(cache_dir), key=lambda entry: entry.name)
    except OSError:
        return []

    target_cache_path = (
        _cache_path_for_host(cache_dir, target_host)
        if target_host is not None
        else None
    )
    for entry in entries:
        if not entry.name.endswith(".json") or not entry.is_file(follow_symlinks=False):
            continue
        cache_path = entry.path
        try:
            with open(cache_path, "r", encoding="utf-8") as file_obj:
                data = json.load(file_obj)
        except Exception as exc:
            if target_cache_path is not None and cache_path != target_cache_path:
                continue
            findings.append(
                CleanupFinding(
                    category="setup cache",
                    path=cache_path,
                    reason=str(exc),
                    action="remove",
                )
            )
            continue

        cached_host = _cache_entry_host(data)
        if target_host is not None and not (
            (cached_host is not None and _same_host(cached_host, target_host))
            or cache_path == target_cache_path
        ):
            continue
        try:
            if not isinstance(data, dict):
                raise ValueError("cache entry must be a JSON object")
            system_type = data.get("system_type")
            args_dict = data.get("args", {})
            if not isinstance(args_dict, dict):
                raise ValueError("cache entry 'args' must be an object")
            args_dict = dict(args_dict)
            if "name" in data and "friendly_name" not in args_dict:
                args_dict["friendly_name"] = data["name"]
            if "tags" in data and "tags" not in args_dict:
                args_dict["tags"] = data["tags"]
            if cached_host is None:
                raise ValueError("cache entry is missing a valid 'host'")
            SetupConfig.from_dict(cached_host, system_type, args_dict)
        except Exception as exc:
            findings.append(
                CleanupFinding(
                    category="setup cache",
                    path=cache_path,
                    reason=str(exc),
                    action="remove",
                )
            )
    return findings


def _proxmox_findings(
    workspace: str,
    target_host: Optional[str],
) -> list[CleanupFinding]:
    registry_path = get_proxmox_hosts_path(workspace)
    if not os.path.exists(registry_path) or os.path.islink(registry_path):
        return []

    try:
        with open(registry_path, "r", encoding="utf-8") as file_obj:
            raw = json.load(file_obj)
    except (OSError, json.JSONDecodeError) as exc:
        if target_host is not None:
            return []
        return [
            CleanupFinding(
                category="Proxmox registry",
                path=registry_path,
                reason=f"registry cannot be parsed: {exc}",
                action="reset_registry",
            )
        ]

    if not isinstance(raw, list):
        if target_host is not None:
            return []
        return [
            CleanupFinding(
                category="Proxmox registry",
                path=registry_path,
                reason="registry must contain a JSON array",
                action="reset_registry",
            )
        ]

    findings: list[CleanupFinding] = []
    for index, entry in enumerate(raw):
        if target_host is not None:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            address = entry.get("address")
            if not (
                (isinstance(name, str) and _same_host(name, target_host))
                or (isinstance(address, str) and _same_host(address, target_host))
            ):
                continue
        try:
            if not isinstance(entry, dict):
                raise ValueError("record must be a JSON object")
            host = ProxmoxHost.from_dict(cast(JSONDict, entry))
            _validate_host_record(host)
        except (TypeError, ValueError) as exc:
            findings.append(
                CleanupFinding(
                    category="Proxmox registry",
                    path=registry_path,
                    reason=str(exc),
                    action="remove_record",
                    record_index=index,
                )
            )
    return findings


def inspect_cleanup(
    workspace: Optional[str] = None,
    *,
    target_host: Optional[str] = None,
    include_setup_cache: bool = True,
    include_proxmox_registry: bool = True,
) -> CleanupPlan:
    """Find obsolete state without changing the workspace."""

    workspace_path = normalize_workspace_dir(workspace)
    if target_host is not None and not validate_host(target_host):
        raise ValueError(f"Invalid host: {target_host}")
    return CleanupPlan(
        setup_findings=(
            _setup_cache_findings(workspace_path, target_host)
            if include_setup_cache
            else []
        ),
        proxmox_findings=(
            _proxmox_findings(workspace_path, target_host)
            if include_proxmox_registry
            else []
        ),
    )


def _backup_directory(workspace: str) -> str:
    parent = os.path.join(workspace, "cleanup-backups")
    os.makedirs(parent, mode=0o700, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = os.path.join(parent, timestamp)
    suffix = 0
    while True:
        try:
            os.makedirs(candidate, mode=0o700, exist_ok=False)
            return candidate
        except FileExistsError:
            suffix += 1
            candidate = os.path.join(parent, f"{timestamp}-{suffix}")


def _backup_files(findings: list[CleanupFinding], workspace: str) -> str:
    backup_dir = _backup_directory(workspace)
    backed_up: set[str] = set()
    for finding in findings:
        if finding.path in backed_up:
            continue
        destination = os.path.join(backup_dir, os.path.basename(finding.path))
        shutil.copy2(finding.path, destination, follow_symlinks=False)
        backed_up.add(finding.path)
    return backup_dir


def _remove_setup_findings(findings: list[CleanupFinding]) -> None:
    for finding in findings:
        if finding.action != "remove":
            continue
        try:
            os.unlink(finding.path)
        except FileNotFoundError:
            continue


def _remove_proxmox_findings(findings: list[CleanupFinding]) -> None:
    if not findings:
        return
    registry_path = findings[0].path
    if any(finding.action == "reset_registry" for finding in findings):
        write_json_atomic(registry_path, cast(JSONList, []), mode=0o600, sort_keys=True)
        return

    invalid_indexes = {
        finding.record_index
        for finding in findings
        if finding.action == "remove_record" and finding.record_index is not None
    }
    if not invalid_indexes:
        return
    with open(registry_path, "r", encoding="utf-8") as file_obj:
        raw = json.load(file_obj)
    if not isinstance(raw, list):
        raise ValueError("Proxmox registry changed and is no longer a JSON array")
    retained = [entry for index, entry in enumerate(raw) if index not in invalid_indexes]
    write_json_atomic(registry_path, cast(JSONList, retained), mode=0o600, sort_keys=True)


def _print_plan(plan: CleanupPlan, target_host: Optional[str]) -> None:
    scope = f" for {target_host}" if target_host else ""
    if not plan.findings:
        print(f"No obsolete local configuration found{scope}.")
        return
    print(f"Cleanup findings{scope}:")
    for finding in plan.findings:
        location = finding.path
        if finding.record_index is not None:
            location += f" record {finding.record_index}"
        print(f"  - {finding.category}: {location}")
        print(f"    {finding.reason}")


def run_cleanup(
    target_host: Optional[str] = None,
    *,
    workspace: Optional[str] = None,
    include_setup_cache: bool = True,
    include_proxmox_registry: bool = True,
    dry_run: bool = False,
    assume_yes: bool = False,
) -> int:
    """Inspect and, after confirmation, remove obsolete local state."""

    try:
        workspace_path = normalize_workspace_dir(workspace)
        validate_workspace_dir(workspace_path)
        plan = inspect_cleanup(
            workspace_path,
            target_host=target_host,
            include_setup_cache=include_setup_cache,
            include_proxmox_registry=include_proxmox_registry,
        )
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}")
        return 1

    _print_plan(plan, target_host)
    if not plan.findings or dry_run:
        if dry_run and plan.findings:
            print("Dry run: no files changed.")
        return 0

    if not assume_yes:
        if not sys.stdin.isatty():
            print("Error: cleanup requires --yes when stdin is not interactive.")
            return 1
        try:
            response = input("Remove these obsolete configuration items? [y/N] ")
        except EOFError:
            return 1
        if response.strip().lower() not in {"y", "yes"}:
            print("Cleanup cancelled.")
            return 1

    actionable = plan.actionable_findings
    try:
        backup_dir = _backup_files(actionable, workspace_path)
        _remove_setup_findings(plan.setup_findings)
        _remove_proxmox_findings(plan.proxmox_findings)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: cleanup failed after backup attempt: {exc}")
        return 1

    print(f"Removed {len(actionable)} obsolete configuration item(s).")
    print(f"Backup: {backup_dir}")
    return 0


__all__ = ["CleanupFinding", "CleanupPlan", "inspect_cleanup", "run_cleanup"]
