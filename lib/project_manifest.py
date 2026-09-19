"""Repo-side project manifest (``basaltwater.json``) loader and validation.

A repository MAY ship an ``basaltwater.json`` at its root describing how it should be
built and served. When present, the ``--deploy`` / ``DeploymentOrchestrator``
path uses it as the source of truth, overriding ``detect_project_type()``. When
absent, behavior is unchanged (fully backward compatible).

This module provides repo-side manifest parsing, dataclasses, and strict
validation for ``basaltwater.json`` deploys.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Optional

from lib.types import StrDict, StrList
from lib.validation import (
    validate_environment_variable_name,
    validate_filesystem_path,
    validate_no_control_characters,
    validate_systemd_exec_command,
)
from lib.validators import validate_host

MANIFEST_FILENAME = "basaltwater.json"
SUPPORTED_VERSION = 1
MIN_PORT = 1024
MAX_PORT = 65535

_NAME_PATTERN = re.compile(r"^[a-z0-9-]+$")
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")

# Deploy-time template variables that may appear as ``{{name}}`` in a service
# component's env_file / working_dir / exec and backup settings. The
# orchestrator supplies the concrete values at deploy time.
TEMPLATE_VARS = frozenset({
    "release_dir",   # absolute path of the deployed release
    "base_dir",      # deployment root (e.g. /var/www)
    "name",          # component name
    "service_name",  # systemd unit base name (app-<name>)
    "domain",        # component domain
    "path",          # component URL path
    "web_user",      # service user
    "web_group",     # service group
    "port",          # loopback port
    "binary",        # resolved absolute binary path
    "working_dir",   # resolved working directory
    "env_file",      # resolved EnvironmentFile path
    "shared_dir",    # basaltwater-managed persistent dir for this component
    "data_dir",      # writable data dir under shared_dir (e.g. for SQLite)
})


def has_placeholder(text: str) -> bool:
    """True if text contains any ``{{...}}`` template placeholder."""
    return "{{" in text


def render_template(text: str, context: dict[str, str]) -> str:
    """Substitute ``{{name}}`` placeholders from context.

    Raises ValueError on an unknown variable so typos fail fast instead of
    silently producing a broken path or unit file.
    """
    def replace(match: "re.Match[str]") -> str:
        key = match.group(1)
        if key not in context:
            raise ValueError(f"unknown template variable: {{{{{key}}}}}")
        return context[key]

    return _PLACEHOLDER_RE.sub(replace, text)


def _validate_placeholders(text: str, field: str, where: str) -> None:
    """Reject any ``{{name}}`` referencing a variable not in TEMPLATE_VARS."""
    for match in _PLACEHOLDER_RE.finditer(text):
        if match.group(1) not in TEMPLATE_VARS:
            raise ValueError(
                f"{where}: {field} references unknown template variable "
                f"{{{{{match.group(1)}}}}}; known: {', '.join(sorted(TEMPLATE_VARS))}"
            )
    unmatched = _PLACEHOLDER_RE.sub("", text)
    if "{{" in unmatched or "}}" in unmatched:
        raise ValueError(f"{where}: {field} contains a malformed template placeholder")

_COMMON_FIELDS = {"name", "type", "domain", "path", "build", "env"}
_STATIC_FIELDS = {"output"}
_SERVICE_FIELDS = {
    "binary",
    "exec",
    "port",
    "env_file",
    "runtime_env",
    "reverse_proxy",
    "health",
    "working_dir",
    "sqlite_backup",
    "backup_retention",
}


@dataclass
class Component:
    """A single deployable component declared in a manifest."""

    name: str
    type: str
    domain: str
    path: str = "/"
    build: StrList = field(default_factory=list)
    env: StrDict = field(default_factory=dict)
    # static
    output: Optional[str] = None
    # service
    binary: Optional[str] = None
    exec: Optional[str] = None
    port: Optional[int] = None
    env_file: Optional[str] = None
    reverse_proxy: bool = True
    health: Optional[str] = None
    working_dir: Optional[str] = None
    runtime_env: StrDict = field(default_factory=dict)
    sqlite_backup: Optional[str] = None
    backup_retention: int = 10

    @property
    def is_static(self) -> bool:
        return self.type in {"static", "godot-web"}

    @property
    def is_service(self) -> bool:
        return self.type == "service"


@dataclass
class Manifest:
    """A parsed, validated ``basaltwater.json``."""

    version: int
    components: list[Component]


def load_manifest(repo_path: str) -> Optional[Manifest]:
    """Load and validate the manifest at ``repo_path``, or None if absent.

    Raises ValueError on malformed JSON or any validation failure so callers
    fail fast instead of deploying a half-understood repo.
    """
    manifest_path = os.path.join(repo_path, MANIFEST_FILENAME)
    if not os.path.lexists(manifest_path):
        if os.path.lexists(os.path.join(repo_path, "infra.json")):
            raise ValueError(
                f"Rename infra.json to {MANIFEST_FILENAME} before deployment; "
                "the retired manifest cannot be ignored safely"
            )
        return None

    try:
        with open(manifest_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{MANIFEST_FILENAME}: invalid JSON: {exc}") from exc
    except OSError as exc:
        raise ValueError(f"{MANIFEST_FILENAME}: cannot read: {exc}") from exc

    return parse_manifest(data)


def infer_manifest(repo_path: str) -> Optional[Manifest]:
    """Infer a minimal service manifest from a conventional Go repository.

    Repositories with an explicit ``basaltwater.json`` still take precedence. The
    convention deliberately stays narrow: a Go module with either
    ``cmd/server/main.go`` or a root ``main.go`` gets a buildable service on
    an automatically assigned loopback port. Projects with non-standard entry
    points can provide an explicit manifest without changing basaltwater.
    """
    if not os.path.isfile(os.path.join(repo_path, "go.mod")):
        return None

    entrypoint = ""
    for candidate in ("cmd/server/main.go", "main.go"):
        candidate_path = os.path.join(repo_path, candidate)
        if not os.path.isfile(candidate_path):
            continue
        try:
            with open(candidate_path, "r", encoding="utf-8") as handle:
                if re.search(r"^\s*package\s+main\b", handle.read(), re.MULTILINE):
                    entrypoint = "./" + os.path.dirname(candidate) if os.path.dirname(candidate) else "."
                    break
        except OSError:
            return None

    if not entrypoint:
        return None

    return parse_manifest({
        "version": SUPPORTED_VERSION,
        "components": [{
            "name": "app",
            "type": "service",
            "domain": "{{domain}}",
            "build": (
                "mkdir -p .basaltwater/bin && "
                f"go build -trimpath -ldflags='-s -w' "
                f"-o .basaltwater/bin/app {entrypoint}"
            ),
            "binary": ".basaltwater/bin/app",
            "port": "auto",
            "runtime_env": {
                "HOST": "127.0.0.1",
                "PORT": "{{port}}",
                "LISTEN_ADDR": "127.0.0.1:{{port}}",
            },
        }],
    })


def parse_manifest(data: object) -> Manifest:
    """Validate an already-decoded manifest object into a Manifest."""
    if not isinstance(data, dict):
        raise ValueError(f"{MANIFEST_FILENAME} must be a JSON object")

    _reject_unknown_keys(data, {"version", "components"}, MANIFEST_FILENAME)

    version = data.get("version")
    if version != SUPPORTED_VERSION:
        raise ValueError(
            f"unsupported manifest version: {version!r} (expected {SUPPORTED_VERSION})"
        )

    components_raw = data.get("components")
    if not isinstance(components_raw, list) or not components_raw:
        raise ValueError("'components' must be a non-empty array")

    components: list[Component] = []
    seen: set[str] = set()
    for index, entry in enumerate(components_raw):
        component = _parse_component(entry, index)
        if component.name in seen:
            raise ValueError(f"duplicate component name: {component.name!r}")
        seen.add(component.name)
        components.append(component)

    return Manifest(version=version, components=components)


def _parse_component(entry: object, index: int) -> Component:
    where = f"components[{index}]"
    if not isinstance(entry, dict):
        raise ValueError(f"{where} must be an object")

    comp_type = entry.get("type")
    if comp_type in ("static", "godot-web"):
        allowed = _COMMON_FIELDS | _STATIC_FIELDS
    elif comp_type == "service":
        allowed = _COMMON_FIELDS | _SERVICE_FIELDS
    else:
        raise ValueError(f"{where}: type must be 'static', 'godot-web' or 'service', got {comp_type!r}")

    _reject_unknown_keys(entry, allowed, where)

    name = _require_str(entry, "name", where)
    if not _NAME_PATTERN.match(name):
        raise ValueError(f"{where}: name {name!r} must match {_NAME_PATTERN.pattern}")
    where = f"component {name!r}"

    domain = _require_str(entry, "domain", where)
    if has_placeholder(domain):
        _validate_placeholders(domain, "domain", where)
    elif not validate_host(domain):
        raise ValueError(f"{where}: invalid domain: {domain}")

    path = entry.get("path", "/")
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError(f"{where}: path must be a string starting with '/'")
    validate_no_control_characters(path, f"{where} path")
    if comp_type == "godot-web":
        from lib.cicd_deploy_policy import validate_nginx_path
        validate_nginx_path(path)

    build = _parse_build(entry.get("build"), where)
    env = _parse_env(entry.get("env"), where)

    common = dict(
        name=name,
        type=comp_type,
        domain=domain,
        path=path,
        build=build,
        env=env,
    )

    if comp_type in ("static", "godot-web"):
        output = _require_str(entry, "output", where)
        _require_repo_relative(output, "output", where)
        if comp_type == "godot-web" and not re.fullmatch(r"[A-Za-z0-9_./-]+", output):
            raise ValueError(f"{where}: output must use safe Nginx path characters")
        return Component(output=output, **common)

    return _parse_service(entry, where, common)


def _parse_service(entry: dict, where: str, common: dict) -> Component:
    binary = entry.get("binary")
    exec_cmd = entry.get("exec")
    if (binary is None) == (exec_cmd is None):
        raise ValueError(f"{where}: exactly one of 'binary' or 'exec' is required")
    if binary is not None:
        if not isinstance(binary, str) or not binary:
            raise ValueError(f"{where}: binary must be a non-empty string")
        _require_repo_relative(binary, "binary", where)
    if exec_cmd is not None:
        if not isinstance(exec_cmd, str) or not exec_cmd:
            raise ValueError(f"{where}: exec must be a non-empty string")
        validate_systemd_exec_command(exec_cmd, f"{where} exec")
        _validate_placeholders(exec_cmd, "exec", where)

    raw_port = entry.get("port")
    if raw_port == "auto":
        port = None
    elif isinstance(raw_port, int) and not isinstance(raw_port, bool):
        port = raw_port
        if not MIN_PORT <= port <= MAX_PORT:
            raise ValueError(f"{where}: port must be between {MIN_PORT} and {MAX_PORT}, got {port}")
    else:
        raise ValueError(f"{where}: port must be an integer or 'auto'")

    env_file = entry.get("env_file")
    if env_file is not None:
        if not isinstance(env_file, str) or not env_file:
            raise ValueError(f"{where}: env_file must be a non-empty string")
        validate_filesystem_path(env_file, must_exist=False)
        _validate_placeholders(env_file, "env_file", where)
        # Must resolve to an absolute server-side path: either literally absolute
        # or built from placeholders (e.g. {{base_dir}}/{{name}}/.env).
        if not has_placeholder(env_file) and not os.path.isabs(env_file):
            raise ValueError(
                f"{where}: env_file must be an absolute server-side path or use "
                f"{{{{...}}}} placeholders"
            )

    runtime_env = _parse_env(
        entry.get("runtime_env"), where, field="runtime_env", validate_templates=True
    )

    reverse_proxy = entry.get("reverse_proxy", True)
    if not isinstance(reverse_proxy, bool):
        raise ValueError(f"{where}: reverse_proxy must be a boolean")

    health = entry.get("health")
    if health is not None and (not isinstance(health, str) or not health.startswith("/")):
        raise ValueError(f"{where}: health must be a string starting with '/'")
    if health is not None:
        validate_no_control_characters(health, f"{where} health")

    working_dir = entry.get("working_dir")
    if working_dir is not None:
        if not isinstance(working_dir, str) or not working_dir:
            raise ValueError(f"{where}: working_dir must be a non-empty string")
        validate_filesystem_path(working_dir, must_exist=False)
        _validate_placeholders(working_dir, "working_dir", where)

    sqlite_backup = entry.get("sqlite_backup")
    if sqlite_backup is not None:
        if not isinstance(sqlite_backup, str) or not sqlite_backup:
            raise ValueError(f"{where}: sqlite_backup must be a non-empty string")
        validate_filesystem_path(sqlite_backup, must_exist=False)
        _validate_placeholders(sqlite_backup, "sqlite_backup", where)
        if not has_placeholder(sqlite_backup) and not os.path.isabs(sqlite_backup):
            raise ValueError(
                f"{where}: sqlite_backup must be an absolute server-side path or use "
                f"{{{{...}}}} placeholders"
            )

    backup_retention = entry.get("backup_retention", 10)
    if (
        not isinstance(backup_retention, int)
        or isinstance(backup_retention, bool)
        or not 1 <= backup_retention <= 100
    ):
        raise ValueError(f"{where}: backup_retention must be an integer from 1 through 100")

    return Component(
        binary=binary,
        exec=exec_cmd,
        port=port,
        env_file=env_file,
        runtime_env=runtime_env,
        reverse_proxy=reverse_proxy,
        health=health,
        working_dir=working_dir,
        sqlite_backup=sqlite_backup,
        backup_retention=backup_retention,
        **common,
    )


def _parse_build(value: object, where: str) -> StrList:
    if value is None:
        return []
    if isinstance(value, str):
        if not value.strip():
            raise ValueError(f"{where}: build command must be non-empty")
        return [value]
    if isinstance(value, list):
        commands: StrList = []
        for command in value:
            if not isinstance(command, str) or not command.strip():
                raise ValueError(f"{where}: each build command must be a non-empty string")
            commands.append(command)
        if not commands:
            raise ValueError(f"{where}: build array must not be empty")
        return commands
    raise ValueError(f"{where}: build must be a string or array of strings")


def _parse_env(
    value: object,
    where: str,
    *,
    field: str = "env",
    validate_templates: bool = False,
) -> StrDict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{where}: {field} must be an object of string values")
    env: StrDict = {}
    for key, val in value.items():
        validate_environment_variable_name(key, name=f"{where} {field} variable")
        if not isinstance(val, str):
            raise ValueError(f"{where}: {field} value for {key!r} must be a string")
        if validate_templates:
            validate_no_control_characters(val, f"{where} {field} value for {key!r}")
            _validate_placeholders(val, field, where)
        env[key] = val
    return env


def _require_str(entry: dict, key: str, where: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{where}: {key} is required and must be a non-empty string")
    validate_no_control_characters(value, f"{where} {key}")
    return value


def _require_repo_relative(value: str, key: str, where: str) -> None:
    """Reject absolute paths and any that escape the repo root via '..'."""
    validate_filesystem_path(value, must_exist=False)
    if os.path.isabs(value):
        raise ValueError(f"{where}: {key} must be relative to the repo root: {value}")
    normalized = os.path.normpath(value)
    if normalized == ".." or normalized.startswith(".." + os.sep):
        raise ValueError(f"{where}: {key} must not escape the repo root: {value}")


def _reject_unknown_keys(data: dict, allowed: set[str], where: str) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f"{where}: unknown field(s): {', '.join(sorted(unknown))}")
