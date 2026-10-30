"""Versioned, bounded documents shared by build controllers and workers.

These parsers perform no I/O and grant no authority. Authentication, project
policy and target admission remain responsibilities of the calling transport.
Build requests deliberately exclude target paths, runtime environment values
and deployment credentials. Returned documents own their nested collections.
"""

from __future__ import annotations

from bisect import bisect_left
import hashlib
import json
import re
import unicodedata
from typing import Any

from lib.atomic_io import parse_json
from lib.validation import (
    validate_environment_variable_name,
    validate_filesystem_path,
    validate_no_control_characters,
)


SCHEMA = 1
MAX_REQUEST_BYTES = 1024 * 1024
MAX_DESCRIPTOR_BYTES = 64 * 1024 * 1024
MAX_FILES = 100_000
MAX_ARTIFACT_BYTES = 32 * 1024**3
_SHA256 = re.compile(r"[0-9a-f]{64}")
_ID = re.compile(r"[0-9a-f]{32}")
_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_TOKEN = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
CONTROL_NAMES = frozenset({
    ".git", ".gitconfig", ".git-credentials", ".gitmodules", ".gitattributes", ".gitignore", ".hg", ".svn",
})
DEFAULT_LIMITS = {
    "timeout_seconds": 3600,
    "memory_bytes": 2 * 1024**3,
    "cpu_percent": 100,
    "scratch_bytes": 8 * 1024**3,
    "artifact_bytes": 2 * 1024**3,
    "artifact_entries": MAX_FILES,
    "log_bytes": 4 * 1024**2,
}


def _record(value: object, fields: set[str], where: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"{where}: missing or unsupported fields")
    return value


def _text(value: object, where: str, maximum: int = 4096, *, multiline: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(f"{where}: expected a bounded string")
    checked = value.translate({ord(c): None for c in "\n\r\t"}) if multiline else value
    validate_no_control_characters(checked, where)
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise ValueError(f"{where}: invalid Unicode") from exc
    return value


def _pattern(value: object, pattern: re.Pattern[str], where: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"{where}: invalid identity")
    return value


def _integer(value: object, minimum: int, maximum: int, where: str) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{where}: integer outside supported limits")
    return value


def _schema(value: object) -> int:
    if type(value) is not int or value != SCHEMA:
        raise ValueError("Unsupported build document schema")
    return value


def validate_artifact_path(value: object, *, allow_root: bool = False) -> str:
    """Validate a canonical portable relative name, without filesystem probes."""
    path = _text(value, "artifact path")
    validate_filesystem_path(path, must_exist=False)
    if allow_root and path == ".":
        return path
    parts = path.split("/")
    if (not path or "\\" in path or ":" in path
            or any(part in {"", ".", ".."} for part in parts)
            or any(part.casefold() in CONTROL_NAMES for part in parts)
            or any(unicodedata.category(char) == "Cc" for char in path)):
        raise ValueError("Artifact path is not a supported relative export")
    return path


def _exports(value: object) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= 128:
        raise ValueError("artifacts: expected 1 to 128 relative exports")
    paths = [validate_artifact_path(path, allow_root=True) for path in value]
    if len(set(paths)) != len(paths):
        raise ValueError("artifacts: duplicate exports")
    return paths


def _source(value: object) -> dict[str, str]:
    data = _record(value, {"revision", "snapshot_sha256", "manifest_sha256"}, "source")
    revision = data["revision"]
    if not isinstance(revision, str) or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", revision) is None:
        raise ValueError("source revision: expected a complete Git object identity")
    return {
        "revision": revision,
        "snapshot_sha256": _pattern(data["snapshot_sha256"], _SHA256, "source snapshot"),
        "manifest_sha256": _pattern(data["manifest_sha256"], _SHA256, "source manifest"),
    }


def _platform(value: object) -> dict[str, str]:
    data = _record(value, {"os", "architecture", "baseline"}, "platform")
    return {key: _pattern(data[key], _TOKEN, "platform " + key) for key in sorted(data)}


def _limits(value: object) -> dict[str, int]:
    data = _record(value, set(DEFAULT_LIMITS), "limits")
    bounds = {
        "timeout_seconds": (1, 4 * 3600),
        "memory_bytes": (64 * 1024**2, 64 * 1024**3),
        "cpu_percent": (1, 6400),
        "scratch_bytes": (256 * 1024**2, 128 * 1024**3),
        "artifact_bytes": (1, MAX_ARTIFACT_BYTES),
        "artifact_entries": (1, MAX_FILES),
        "log_bytes": (1, 16 * 1024**2),
    }
    result = {key: _integer(data[key], *bounds[key], "limits " + key) for key in sorted(data)}
    if result["scratch_bytes"] % (1024**2):
        raise ValueError("limits scratch_bytes: expected a whole number of MiB")
    return result


def _component(value: object, *, build: bool) -> dict[str, Any]:
    fields = {"name", "type", "artifacts"}
    if build:
        fields |= {"commands", "env"}
    data = _record(value, fields, "component")
    if data["type"] not in ("static", "godot-web", "service"):
        raise ValueError("component: unsupported type")
    result: dict[str, Any] = {
        "name": _pattern(data["name"], _NAME, "component name"),
        "type": data["type"], "artifacts": _exports(data["artifacts"]),
    }
    if "." in result["artifacts"] and (data["type"] == "service" or len(result["artifacts"]) != 1):
        raise ValueError("artifacts: root selection is limited to a single static export")
    if build:
        commands = data["commands"]
        if not isinstance(commands, list) or len(commands) > 100:
            raise ValueError("commands: expected at most 100 commands")
        result["commands"] = [_text(command, "build command", 65536, multiline=True) for command in commands]
        if any(not command.strip() for command in result["commands"]):
            raise ValueError("commands: empty build command")
        environment = data["env"]
        if not isinstance(environment, dict) or len(environment) > 128:
            raise ValueError("env: expected a bounded environment object")
        result["env"] = {}
        for key, val in environment.items():
            validate_environment_variable_name(key, name="build environment")
            result["env"][key] = _text(val, "build environment value", 8192, multiline=True)
    return result


def _components(value: object, *, build: bool) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not 1 <= len(value) <= 100:
        raise ValueError("components: expected 1 to 100 components")
    components = [_component(item, build=build) for item in value]
    if len({item["name"] for item in components}) != len(components):
        raise ValueError("components: duplicate names")
    return components


def _build_request_document(value: object) -> tuple[dict[str, Any], bytes]:
    """Validate once and retain the bounded canonical encoding for hashing."""
    data = _record(value, {
        "schema", "request_id", "project", "source", "platform", "components", "limits",
    }, "build request")
    result = {
        "schema": _schema(data["schema"]),
        "request_id": _pattern(data["request_id"], _ID, "request ID"),
        "project": _pattern(data["project"], _NAME, "project"),
        "source": _source(data["source"]), "platform": _platform(data["platform"]),
        "components": _components(data["components"], build=True),
        "limits": _limits(data["limits"]),
    }
    return result, encode_document(result, maximum=MAX_REQUEST_BYTES)


def parse_build_request(value: object) -> dict[str, Any]:
    """Validate the build-only v1 payload; caller policy may impose lower limits."""
    return _build_request_document(value)[0]


def request_digest(value: object) -> str:
    """Bind all build inputs and the attempt identity to a canonical digest."""
    return hashlib.sha256(_build_request_document(value)[1]).hexdigest()


def _entry(value: object) -> dict[str, Any]:
    data = _record(value, {"path", "kind", "size", "sha256", "executable"}, "artifact entry")
    if data["kind"] not in ("file", "directory") or type(data["executable"]) is not bool:
        raise ValueError("artifact entry: unsupported type or mode")
    size = _integer(data["size"], 0, MAX_ARTIFACT_BYTES, "artifact entry size")
    digest = data["sha256"]
    if data["kind"] == "directory":
        if size != 0 or digest != "" or data["executable"]:
            raise ValueError("artifact directory: invalid metadata")
    else:
        _pattern(digest, _SHA256, "artifact file digest")
    return {"path": validate_artifact_path(data["path"]), "kind": data["kind"],
            "size": size, "sha256": digest, "executable": data["executable"]}


def parse_artifact_descriptor(value: object) -> dict[str, Any]:
    """Validate retained metadata, excluding commands and environment values."""
    data = _record(value, {
        "schema", "request_id", "project", "request_sha256", "source", "platform",
        "components", "toolchains", "entries",
    }, "artifact descriptor")
    entries = data["entries"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_FILES:
        raise ValueError("artifact inventory: empty or oversized")
    entries = [_entry(item) for item in entries]
    paths = {item["path"]: item for item in entries}
    if len(paths) != len(entries) or sum(item["size"] for item in entries) > MAX_ARTIFACT_BYTES:
        raise ValueError("artifact inventory: duplicate paths or excessive size")
    components = _components(data["components"], build=False)
    exports = {path for component in components for path in component["artifacts"]}
    ordered_exports = sorted(exports)
    entries.sort(key=lambda item: item["path"])
    whole_root = "." in exports
    covered_directories: set[str] = set()
    for item in entries:
        path = item["path"]
        parent = path.rpartition("/")[0]
        # Every entry checks its immediate parent, so the complete inventory
        # proves the ancestor chain without expanding every prefix per entry.
        if parent and paths.get(parent, {}).get("kind") != "directory":
            raise ValueError("artifact inventory: missing or invalid parent directory")
        # Sorting puts parents before descendants. Track directories inside an
        # export separately from directories that merely lead to an export.
        declared = whole_root or path in exports or parent in covered_directories
        if declared and not whole_root and item["kind"] == "directory":
            covered_directories.add(path)
        if not declared and item["kind"] == "directory":
            # The first export at/after this prefix proves whether this directory
            # leads to a declared output, without scanning every export per entry.
            prefix = path + "/"
            index = bisect_left(ordered_exports, prefix)
            declared = index < len(ordered_exports) and ordered_exports[index].startswith(prefix)
        if not declared:
            raise ValueError("artifact inventory: undeclared output")
    for root in exports:
        if root != "." and root not in paths:
            raise ValueError("artifact inventory: declared output is missing")
    tools = data["toolchains"]
    if not isinstance(tools, dict) or len(tools) > 128:
        raise ValueError("toolchains: expected a bounded version object")
    tools = {_pattern(key, _TOKEN, "toolchain name"): _text(val, "toolchain version", 1024)
             for key, val in tools.items()}
    result = {
        "schema": _schema(data["schema"]),
        "request_id": _pattern(data["request_id"], _ID, "request ID"),
        "project": _pattern(data["project"], _NAME, "project"),
        "request_sha256": _pattern(data["request_sha256"], _SHA256, "request digest"),
        "source": _source(data["source"]), "platform": _platform(data["platform"]),
        "components": components, "toolchains": tools,
        "entries": entries,
    }
    encode_document(result, maximum=MAX_DESCRIPTOR_BYTES)
    return result


def validate_artifact_for_request(descriptor: object, request: object) -> dict[str, Any]:
    """Require exact request/source/platform/export identity before deployment."""
    artifact = parse_artifact_descriptor(descriptor)
    job = parse_build_request(request)
    for key in ("request_id", "project", "source", "platform"):
        if artifact[key] != job[key]:
            raise ValueError("Artifact identity does not match the admitted build")
    components = [{key: item[key] for key in ("name", "type", "artifacts")}
                  for item in job["components"]]
    if artifact["request_sha256"] != request_digest(job) or artifact["components"] != components:
        raise ValueError("Artifact inputs do not match the admitted build")
    if (len(artifact["entries"]) > job["limits"]["artifact_entries"]
            or sum(item["size"] for item in artifact["entries"]) > job["limits"]["artifact_bytes"]):
        raise ValueError("Artifact exceeds the admitted build limits")
    return artifact


def encode_document(value: object, *, maximum: int = MAX_REQUEST_BYTES) -> bytes:
    """Produce deterministic UTF-8 JSON; no credential values are logged here."""
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError("Invalid build document encoding") from exc
    if len(encoded) > maximum:
        raise ValueError("Build document exceeds its byte limit")
    return encoded


def decode_document(raw: bytes, *, maximum: int = MAX_REQUEST_BYTES) -> object:
    """Reject duplicate keys, non-JSON numbers and oversized transport input."""
    if not isinstance(raw, bytes) or len(raw) > maximum:
        raise ValueError("Build document exceeds its byte limit")

    try:
        return parse_json(raw)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError("Invalid build document") from exc
