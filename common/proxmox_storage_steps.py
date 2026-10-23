"""Conservative storage defaults for Proxmox host setup."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from lib.remote_utils import is_dry_run, run
from lib.validation import validate_no_control_characters, validate_proxmox_storage_name
from lib.validators import validate_host

if TYPE_CHECKING:
    from lib.config import SetupConfig


_CONFIG_COMMAND = ["pvesh", "get", "/storage", "--output-format", "json"]


def _content_types(value: Any) -> set[str]:
    if not isinstance(value, str) or not value:
        raise ValueError("Missing storage content types")
    validate_no_control_characters(value, "storage content types")
    parts = value.split(",")
    if any(not re.fullmatch(r"[a-z][a-z0-9_-]*", part) for part in parts):
        raise ValueError("Invalid storage content types")
    return set(parts)


def _inventory(command: list[str], *, status: bool = False) -> dict[str, dict[str, Any]]:
    result = run(command, capture_output=True)
    try:
        entries = json.loads(result.stdout)
        if not isinstance(entries, list):
            raise ValueError("Expected a storage list")
        pools = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("Invalid storage entry")
            name = entry.get("storage")
            validate_proxmox_storage_name(name, "storage name")
            if name in pools:
                raise ValueError("Duplicate storage entry")
            if "content" in entry:
                _content_types(entry["content"])
            if "nodes" in entry:
                nodes = entry["nodes"]
                if not isinstance(nodes, str) or not nodes or any(
                    not validate_host(node) for node in nodes.split(",")
                ):
                    raise ValueError("Invalid storage node restriction")
            if status:
                for flag in ("enabled", "active"):
                    if type(entry.get(flag)) is not int or entry[flag] not in (0, 1):
                        raise ValueError(f"Invalid storage {flag} flag")
            pools[name] = entry
        return pools
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Unable to inspect Proxmox storage: {exc}") from exc


def configure_proxmox_snippets_storage(config: SetupConfig) -> None:
    """Enable local snippets only when this node has no configured snippets pool."""
    del config
    if is_dry_run():
        print(
            "  Would preserve configured snippets storage, or add snippets to "
            "active local directory storage while retaining existing content types"
        )
        return

    node = run(["hostname", "-s"], capture_output=True).stdout.strip()
    if not node or not validate_host(node):
        raise RuntimeError("Unable to determine the Proxmox node name")
    status_command = [
        "pvesh", "get", f"/nodes/{node}/storage", "--output-format", "json",
    ]
    configured = _inventory(_CONFIG_COMMAND)
    available = _inventory(status_command, status=True)
    snippets = [
        name for name, pool in configured.items()
        if "snippets" in _content_types(pool.get("content", "images"))
        and ("nodes" not in pool or node in pool["nodes"].split(","))
    ]
    if snippets:
        active = [
            name for name in snippets
            if available.get(name, {}).get("enabled") == 1
            and available[name]["active"] == 1
            and "snippets" in _content_types(available[name].get("content", "images"))
        ]
        if active:
            print("  ✓ Existing snippets storage available: " + ", ".join(active))
        else:
            print(
                "  ⚠ Configured snippets storage is disabled or unavailable: "
                + ", ".join(snippets) + "; restore it before provisioning VMs"
            )
        return

    local = configured.get("local", {})
    local_status = available.get("local", {})
    if (
        local.get("type") != "dir"
        or local_status.get("type") != "dir"
        or local_status.get("enabled") != 1
        or local_status.get("active") != 1
    ):
        print(
            "  ⚠ No snippets storage configured and no active local directory "
            "storage; enable snippets content on a suitable pool before provisioning VMs"
        )
        return

    try:
        content = _content_types(local.get("content", local_status.get("content")))
        digest = local.get("digest")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", digest):
            raise ValueError("Missing or invalid storage configuration digest")
    except ValueError as exc:
        raise RuntimeError(f"Unable to configure Proxmox snippets storage: {exc}") from exc
    desired = content | {"snippets"}
    run([
        "pvesm", "set", "local", "--content", ",".join(sorted(desired)),
        "--digest", digest,
    ])

    verified_config = _inventory(_CONFIG_COMMAND).get("local", {})
    verified_status = _inventory(status_command, status=True).get("local", {})
    if (
        verified_config.get("type") != "dir"
        or not desired.issubset(_content_types(verified_config.get("content", "images")))
        or verified_status.get("enabled") != 1
        or verified_status.get("active") != 1
        or "snippets" not in _content_types(verified_status.get("content", "images"))
    ):
        raise RuntimeError("Proxmox local snippets storage verification failed")
    print("  ✓ Enabled snippets on local storage, retaining existing content types")
