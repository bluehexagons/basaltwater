"""Pure bounded serving metadata and shared release executable validation."""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Mapping
from typing import TYPE_CHECKING

from lib.build_protocol import encode_document, validate_artifact_path
from lib.validation import (
    validate_filesystem_path, validate_no_control_characters,
    validate_systemd_exec_command,
)

if TYPE_CHECKING:
    from lib.project_manifest import Component

_SYSTEMD_PATH_META = frozenset("%$'\"\\")


def _normalize_binary_path(value: object) -> str:
    """Apply the manifest's legacy normpath behavior, then validate canonically."""
    if not isinstance(value, str) or not value:
        raise ValueError("Service binary must be a non-empty relative path")
    validate_no_control_characters(value, "Service binary")
    validate_filesystem_path(value, must_exist=False)
    if os.path.isabs(value):
        raise ValueError("Service binary must be relative to the release directory")
    if "{{" in value or "}}" in value:
        raise ValueError("Service binary must be a literal relative release path")
    normalized = os.path.normpath(value)
    _validate_literal_systemd_path(normalized, "Service binary")
    try:
        return validate_artifact_path(normalized)
    except ValueError as exc:
        raise ValueError("Service binary is not a safe relative release path") from exc


def _exec_binary_path(value: object) -> str:
    """Accept only an explicit release-root executable as argv[0]."""
    if not isinstance(value, str) or not value:
        raise ValueError("Exec-only services require an explicit executable command")
    validate_systemd_exec_command(value, "Service exec")
    try:
        arguments = shlex.split(value, posix=True)
    except ValueError as exc:
        raise ValueError("Service exec must use valid command quoting") from exc
    if not arguments:
        raise ValueError("Service exec must name an executable")

    raw_first = value.split(None, 1)[0]
    if raw_first != arguments[0]:
        raise ValueError("Service exec executable must be an unquoted literal token")
    _validate_literal_systemd_path(raw_first, "Service exec executable")

    prefix = "{{release_dir}}/"
    first = raw_first
    if not first.startswith(prefix):
        raise ValueError(
            "Exec-only services must start with {{release_dir}}/relative/path"
        )
    relative = first[len(prefix):]
    if "{{" in relative or "}}" in relative:
        raise ValueError("Service executable path cannot contain additional placeholders")
    _validate_literal_systemd_path(relative, "Service exec executable path")
    try:
        return validate_artifact_path(relative)
    except ValueError as exc:
        raise ValueError("Service exec must name a safe relative release executable") from exc


def _validate_literal_systemd_path(value: str, where: str) -> None:
    """Keep the checked executable path identical to raw generated ExecStart."""
    if any(character in _SYSTEMD_PATH_META or character.isspace() for character in value):
        raise ValueError(
            f"{where} must not contain systemd specifiers, variable expansion, "
            "quoting, escapes, or whitespace"
        )


def _reject_interpreter(path: str) -> None:
    name = path.rsplit("/", 1)[-1].casefold()
    shells = {"ash", "bash", "busybox", "dash", "fish", "ksh", "sh", "zsh"}
    if name in shells or name in {"node", "nodejs", "python"}:
        raise ValueError("Interpreter and shell service commands are unsupported for prebuilt runtimes")
    if name in {"python2", "python3"} or name.startswith(("python2.", "python3.")):
        raise ValueError("Interpreter and shell service commands are unsupported for prebuilt runtimes")


def _managed_path(value: object, roots: tuple[str, ...], where: str, *, relative: bool = False) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        raise ValueError(f"{where} must be a managed path")
    if relative and not value.startswith(("/", "{{")):
        if "{" in value or "}" in value:
            raise ValueError(f"{where} requires a literal relative path")
        validate_artifact_path(value, allow_root=True)
        _validate_literal_systemd_path(value, where)
        return
    for root in roots:
        prefix = "{{" + root + "}}"
        if value == prefix or value.startswith(prefix + "/"):
            suffix = value[len(prefix):]
            if suffix:
                if "{" in suffix or "}" in suffix:
                    raise ValueError(f"{where} requires a literal path suffix")
                validate_artifact_path(suffix[1:])
            _validate_literal_systemd_path(value, where)
            return
    raise ValueError(f"{where} must stay inside a managed runtime directory")


def parse_runtime(value: object) -> dict:
    """Canonicalize administrator-approved service settings without I/O.

    Defaults are explicit in the returned mapping; absence of the mapping is
    reserved for the legacy PORT/HOST projection.
    """
    from lib.project_manifest import parse_manifest

    allowed = {"exec", "port", "runtime_env", "working_dir", "sqlite_backup",
               "backup_retention", "public_udp"}
    if not isinstance(value, Mapping) or not set(value) <= allowed:
        raise ValueError("Runtime contains unsupported fields (including secret/env_file bindings)")
    raw = dict(value)
    encode_document(raw, maximum=64 * 1024)
    if "runtime_env" in raw and not isinstance(raw["runtime_env"], dict):
        raise ValueError("Runtime environment must be an object of string values")
    command = raw.get("exec")
    if "exec" in raw and command is None:
        raise ValueError("Runtime exec must be a command")
    entry = {"name": "runtime", "type": "service", "domain": "example.invalid",
             "port": "auto", **raw}
    if command is None:
        entry["binary"] = "bin/runtime"
    component = parse_manifest({"version": 1, "components": [entry]}).components[0]
    for text in [command or "", *component.runtime_env.values()]:
        if any(name in {"binary", "env_file"} for name in re.findall(r"\{\{\s*(\w+)\s*\}\}", text)):
            raise ValueError("Runtime cannot reference optional binary or secret file bindings")
    if command is not None:
        _reject_interpreter(_exec_binary_path(command))
        if "$" in command or "%" in command:
            raise ValueError("Runtime exec cannot contain systemd expansion")
    _managed_path(component.working_dir, ("release_dir", "data_dir", "shared_dir"), "working_dir", relative=True)
    _managed_path(component.sqlite_backup, ("data_dir",), "sqlite_backup")
    for text in component.runtime_env.values():
        if "$" in text or "%" in text:
            raise ValueError("Runtime environment cannot contain systemd expansion")
    result = {
        "port": component.port if component.port is not None else "auto",
        "runtime_env": dict(component.runtime_env),
        "working_dir": component.working_dir,
        "sqlite_backup": component.sqlite_backup,
        "backup_retention": component.backup_retention,
        "public_udp": [{"port": item.port, **({"hostname": item.hostname}
                       if item.hostname is not None else {})} for item in component.public_udp],
    }
    if command is not None:
        result["exec"] = command
    encode_document(result, maximum=64 * 1024)
    return result


def runtime_from_component(component: Component) -> dict | None:
    """Project service metadata, retaining legacy defaults for simple binaries."""
    if component.env_file is not None or component.reverse_proxy is not True:
        raise ValueError("Component has unsupported execution or routing settings")
    raw = {
        "port": component.port if component.port is not None else "auto",
        "runtime_env": component.runtime_env, "working_dir": component.working_dir,
        "sqlite_backup": component.sqlite_backup, "backup_retention": component.backup_retention,
        "public_udp": [{"port": item.port, **({"hostname": item.hostname}
                       if item.hostname is not None else {})} for item in component.public_udp],
    }
    if component.exec is not None:
        raw["exec"] = component.exec
    if (component.exec is None and component.port is None and not component.runtime_env
            and component.working_dir is None and component.sqlite_backup is None
            and component.backup_retention == 10 and not component.public_udp):
        return None
    return parse_runtime(raw)
