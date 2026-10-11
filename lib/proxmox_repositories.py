"""Select accessible Proxmox repositories on nodes without a subscription key."""

from __future__ import annotations

import json
import re
import socket
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from lib.proxmox_preflight import SUPPORTED_DEBIAN_CODENAME
from lib.remote_utils import is_dry_run, run
from lib.validation import validate_filesystem_path
from lib.validators import validate_host


def _get_json(endpoint: str) -> dict[str, Any]:
    result = run(["pvesh", "get", endpoint, "--output-format", "json"],
                 check=False, capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError(f"Unable to read Proxmox API {endpoint}; inspect node repositories and retry setup")
    try:
        data = json.loads(result.stdout or "")
        if not isinstance(data, dict):
            raise ValueError("Expected an object")
        return data
    except ValueError as exc:
        # Subscription responses can contain a key: never include their content.
        raise RuntimeError(f"Invalid Proxmox API response for {endpoint}") from exc


def _inventory(endpoint: str) -> dict[str, Any]:
    data = _get_json(endpoint)
    if (data.get("errors") != [] or not isinstance(data.get("files"), list)
            or not isinstance(data.get("standard-repos"), list)
            or not isinstance(data.get("digest"), str)
            or not re.fullmatch(r"[a-fA-F0-9]{64}", data["digest"])):
        raise RuntimeError("Unable to safely inspect Proxmox APT repositories; fix repository parsing errors first")
    return data


def _standard_status(data: dict[str, Any], handle: str) -> bool:
    for entry in data["standard-repos"]:
        if isinstance(entry, dict) and entry.get("handle") == handle:
            return entry.get("status") in (True, 1)
    raise RuntimeError(f"Proxmox does not offer repository {handle}; configure its channel manually")


def _enterprise_entries(data: dict[str, Any]) -> list[tuple[str, int, str]]:
    entries = []
    for source in data["files"]:
        if not isinstance(source, dict) or not isinstance(source.get("repositories"), list):
            raise RuntimeError("Invalid Proxmox repository file inventory")
        for index, repo in enumerate(source["repositories"]):
            if not isinstance(repo, dict) or repo.get("Enabled") not in (True, False, 0, 1):
                raise RuntimeError("Invalid Proxmox repository enabled state")
            if not repo["Enabled"]:
                continue
            uris = repo.get("URIs")
            if not isinstance(uris, list) or not uris or not all(isinstance(uri, str) for uri in uris):
                raise RuntimeError("Invalid Proxmox repository URI inventory")
            urls = [urlsplit(uri) for uri in uris]
            if not any(url.hostname == "enterprise.proxmox.com" for url in urls):
                continue
            product = urls[0].path.rstrip("/").removeprefix("/debian/")
            component = "pve-enterprise" if product == "pve" else "enterprise"
            if (repo.get("Types") != ["deb"]
                    or not re.fullmatch(r"pve|ceph-[a-z][a-z0-9]*", product)
                    or any(url.scheme not in ("http", "https")
                           or url.netloc != "enterprise.proxmox.com"
                           or url.path.rstrip("/") != f"/debian/{product}"
                           or url.query or url.fragment for url in urls)
                    or repo.get("Suites") != [SUPPORTED_DEBIAN_CODENAME]
                    or repo.get("Components") != [component]):
                raise RuntimeError("Custom or mixed enterprise repository requires manual channel selection")
            path = source.get("path")
            if not isinstance(path, str):
                raise RuntimeError("Invalid Proxmox repository path")
            validate_filesystem_path(path)
            parsed_path = PurePosixPath(path)
            if (str(parsed_path) != path or ".." in parsed_path.parts
                    or not (path == "/etc/apt/sources.list"
                            or parsed_path.parent == PurePosixPath("/etc/apt/sources.list.d")
                            and parsed_path.suffix in (".list", ".sources"))):
                raise RuntimeError("Proxmox repository path is outside the APT source files")
            handle = "no-subscription" if product == "pve" else f"{product}-no-subscription"
            _standard_status(data, handle)  # Validate every replacement before any changes.
            entries.append((path, index, handle))
    return entries


def _change(endpoint: str, method: str, data: dict[str, Any], options: list[str]) -> dict[str, Any]:
    result = run(["pvesh", method, endpoint, *options, "--digest", data["digest"]],
                 check=False, capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError("Proxmox repository selection failed; inspect node repositories and retry setup")
    return _inventory(endpoint)


def configure_unsubscribed_proxmox_repositories() -> bool:
    """Replace enabled standard enterprise sources only with an explicit absent key.

    Use Proxmox's own source-file parser and writer, preserving unrelated entries
    and the Ceph release. A fresh digest guards every write against concurrent
    edits. Existing keys, including expired or invalid keys, retain their policy.
    """
    if is_dry_run():
        return False
    node = socket.gethostname().split(".", 1)[0]
    if not validate_host(node):
        raise ValueError("Invalid local Proxmox node name")
    subscription = _get_json(f"/nodes/{node}/subscription")
    if subscription.get("status") != "notfound" or subscription.get("key"):
        return False
    endpoint = f"/nodes/{node}/apt/repositories"
    data = _inventory(endpoint)
    pending = _enterprise_entries(data)
    if not pending:
        return False
    handles = {handle for _, _, handle in pending}
    # Enable each replacement first so a failed write cannot leave PVE with no
    # channel. Proxmox re-enables an existing entry instead of adding duplicates.
    for handle in sorted(handles):
        if not _standard_status(data, handle):
            data = _change(endpoint, "set", data, ["--handle", handle])
        if not _standard_status(data, handle):
            raise RuntimeError(f"Proxmox repository {handle} did not become enabled")
    for _ in range(len(pending)):
        pending = _enterprise_entries(data)
        if not pending:
            break
        path, index, handle = pending[0]
        if handle not in handles or not _standard_status(data, handle):
            raise RuntimeError("Proxmox repository selection changed concurrently; retry setup")
        previous_count = len(pending)
        data = _change(endpoint, "create", data,
                       ["--path", path, "--index", str(index), "--enabled", "0"])
        if len(_enterprise_entries(data)) >= previous_count:
            raise RuntimeError("Proxmox enterprise repository did not become disabled")
    if _enterprise_entries(data) or not all(_standard_status(data, handle) for handle in handles):
        raise RuntimeError("Unable to verify Proxmox no-subscription repository selection")
    print("  ✓ No subscription key installed; enabled matching PVE/Ceph no-subscription repositories")
    return True
