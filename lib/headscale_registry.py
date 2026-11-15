"""Private, local references to Headscale servers.

The registry stores endpoint discovery only. It contains no credentials and
does not imply authority to administer a referenced server.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
import json
import os
import re
import stat
import uuid
from typing import TypeVar
from urllib.parse import urlsplit, urlunsplit

from lib.atomic_io import read_json_file, write_json_atomic
from lib.concurrency import resource_lock
from lib.validators import validate_host
from lib.workspace import normalize_workspace_dir

SCHEMA_VERSION = 1
MAX_REGISTRY_BYTES = 256 * 1024
REGISTRY_NAME = "headscale-servers.json"
_Result = TypeVar("_Result")


class RegistryStateError(ValueError):
    """The saved registry is unsafe, malformed, or uses an unknown schema."""


@dataclass(frozen=True)
class HeadscaleServer:
    id: str
    name: str
    url: str
    origin: str
    onboarding_url: str | None = None


def _registry_path(workspace: str | None) -> str:
    return os.path.join(normalize_workspace_dir(workspace), REGISTRY_NAME)


def _validate_url(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise ValueError(f"{label} must be an HTTPS URL")
    if any(ord(c) < 32 or ord(c) == 127 for c in value) or any(c.isspace() for c in value):
        raise ValueError(f"{label} contains unsupported characters")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"{label} must be a valid HTTPS URL") from exc
    if (parts.scheme.lower() != "https" or not parts.netloc or parts.username is not None
            or parts.password is not None or parts.query or parts.fragment):
        raise ValueError(f"{label} must be HTTPS without userinfo, query, or fragment")
    hostname = parts.hostname
    if not hostname or not validate_host(hostname):
        raise ValueError(f"{label} must use a valid hostname or IP address")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError(f"{label} port is invalid")
    # Paths are limited to ordinary URL path characters; no encoded separators,
    # dot segments, backslashes, or shell/control syntax are accepted.
    path = parts.path
    if ("\\" in path or "%" in path or ".." in path.split("/")
            or (path and not re.fullmatch(r"/[A-Za-z0-9._~/-]*", path))):
        raise ValueError(f"{label} path contains unsupported characters")
    return urlunsplit(("https", parts.netloc.lower(), path.rstrip("/"), "", ""))


def _validate_name(name: str) -> str:
    if (not isinstance(name, str) or not 1 <= len(name) <= 64 or name != name.strip()
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_. -]*", name)):
        raise ValueError("Server name must be 1–64 simple characters")
    return name


def _validate_record(raw: object) -> HeadscaleServer:
    if not isinstance(raw, dict) or set(raw) != {"id", "name", "url", "origin", "onboarding_url"}:
        raise RegistryStateError("Headscale registry contains an invalid record")
    try:
        if not isinstance(raw["id"], str):
            raise ValueError("invalid stable ID")
        identifier = str(uuid.UUID(raw["id"]))
        if identifier != raw["id"]:
            raise ValueError("noncanonical stable ID")
        name = _validate_name(raw["name"])
        url = _validate_url(raw["url"], "Server URL")
        origin = raw["origin"]
        onboarding = raw["onboarding_url"]
        if origin not in {"managed", "external"}:
            raise ValueError("invalid origin")
        if onboarding is not None:
            onboarding = _validate_url(onboarding, "Onboarding URL")
        return HeadscaleServer(identifier, name, url, origin, onboarding)
    except (ValueError, TypeError, KeyError) as exc:
        raise RegistryStateError("Headscale registry contains an invalid record") from exc


def _check_private_dir(path: str) -> None:
    if not os.path.lexists(path):
        return
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RegistryStateError("Headscale registry workspace has unsafe type, owner, or permissions")


def _read(workspace: str | None) -> list[HeadscaleServer]:
    path = _registry_path(workspace)
    parent = os.path.dirname(path)
    _check_private_dir(parent)
    if not os.path.lexists(path):
        return []
    try:
        info = os.lstat(path)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or info.st_mode & 0o077):
            raise RegistryStateError("Headscale registry file has unsafe type, owner, or permissions")
        value = read_json_file(path, max_bytes=MAX_REGISTRY_BYTES)
        if not isinstance(value, dict) or set(value) != {"schema_version", "servers"}:
            raise RegistryStateError("Headscale registry has an invalid structure")
        if value["schema_version"] != SCHEMA_VERSION or type(value["schema_version"]) is not int:
            raise RegistryStateError("Headscale registry uses an unsupported schema version")
        if not isinstance(value["servers"], list):
            raise RegistryStateError("Headscale registry server list is invalid")
        records = [_validate_record(item) for item in value["servers"]]
        ids = [item.id for item in records]
        names = [item.name.casefold() for item in records]
        if len(ids) != len(set(ids)) or len(names) != len(set(names)):
            raise RegistryStateError("Headscale registry contains duplicate IDs or names")
        return records
    except RegistryStateError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise RegistryStateError("Headscale registry is corrupt or unreadable") from exc


def list_servers(workspace: str | None = None) -> list[HeadscaleServer]:
    """Return saved references without creating state or acquiring a lock."""
    return _read(workspace)


def resolve_server(reference: str, workspace: str | None = None) -> HeadscaleServer:
    """Resolve one exact case-insensitive name or stable UUID."""
    _validate_name(reference)
    records = _read(workspace)
    matches = [r for r in records if r.name.casefold() == reference.casefold() or r.id == reference]
    if len(matches) != 1:
        raise ValueError("Headscale server reference is missing or ambiguous")
    return matches[0]


def mutate_servers(
    workspace: str | None,
    operation: Callable[[list[HeadscaleServer]], tuple[list[HeadscaleServer] | None, _Result]],
) -> _Result:
    """Run a validated registry mutation under the shared process lock."""
    root = normalize_workspace_dir(workspace)
    with resource_lock("headscale-registry", root):
        _check_private_dir(root)
        current = _read(root)
        updated, result = operation(current)
        if updated is not None:
            # Validate the complete candidate before publishing, including callers
            # other than the CLI. Existing state remains available on failure.
            checked = [_validate_record(vars(record)) for record in updated]
            if (len({record.id for record in checked}) != len(checked)
                    or len({record.name.casefold() for record in checked}) != len(checked)):
                raise ValueError("Headscale registry candidate contains duplicate IDs or names")
            payload = {"schema_version": SCHEMA_VERSION, "servers": [
                {"id": r.id, "name": r.name, "url": r.url, "origin": r.origin,
                 "onboarding_url": r.onboarding_url} for r in checked]}
            if len((json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")) > MAX_REGISTRY_BYTES:
                raise ValueError("Headscale registry exceeds the bounded state limit")
            os.makedirs(root, mode=0o700, exist_ok=True)
            os.chmod(root, 0o700)
            write_json_atomic(_registry_path(root), payload, mode=0o600, sort_keys=True)
        return result
