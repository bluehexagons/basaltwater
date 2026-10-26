"""Pure target authorization and deployment projection for shared builds.

This internal slice validates administrator-owned application grants and binds
a safe serving projection to an already validated build request. It does not
authenticate principals, load policy, inspect files or artifacts, validate ELF
runtime compatibility, reserve admission, or activate a deployment.
"""

from __future__ import annotations

import hashlib
import posixpath
import re
from collections.abc import Mapping
from typing import Any

from lib.build_manifest import DEBIAN_PLATFORM
from lib.build_protocol import (
    MAX_ARTIFACT_BYTES,
    MAX_FILES,
    decode_document,
    encode_document,
    parse_build_request,
    validate_artifact_path,
)
from lib.cicd_deploy_policy import validate_nginx_path
from lib.validation import validate_filesystem_path, validate_no_control_characters
from lib.validators import validate_host


POLICY_SCHEMA = 1
MAX_POLICY_BYTES = 4 * 1024**2
MAX_PRINCIPALS = 128
MAX_APPLICATIONS = 256
MAX_APPLICATIONS_PER_PRINCIPAL = 128
MAX_APPLICATION_GRANTS = 4096
MAX_PROJECTS = 128
MAX_COMPONENTS_PER_APPLICATION = 100

_APPLICATION_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_PROJECT_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_COMPONENT_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_PRINCIPAL_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}")
_SUPPORTED_COMPONENT_TYPES = frozenset({"static", "service"})
_SYSTEMD_PATH_META = frozenset("%$'\"\\{}")


def _record(value: object, fields: set[str], where: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{where}: expected an object")
    record = dict(value)
    if any(not isinstance(key, str) for key in record) or set(record) != fields:
        raise ValueError(f"{where}: missing or unsupported fields")
    return record


def _slug(value: object, pattern: re.Pattern[str], where: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"{where}: invalid lowercase slug")
    validate_no_control_characters(value, where)
    return value


def _principal(value: object) -> str:
    if not isinstance(value, str) or _PRINCIPAL_LABEL.fullmatch(value) is None:
        raise ValueError("Target principal must be a safe authenticated owner label")
    validate_no_control_characters(value, "Target principal")
    return value


def _integer(value: object, minimum: int, maximum: int, where: str) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{where}: integer outside supported limits")
    return value


def _platform(value: object, where: str) -> dict[str, str]:
    data = _record(value, set(DEBIAN_PLATFORM), where)
    if any(not isinstance(item, str) for item in data.values()):
        raise ValueError(f"{where}: unsupported platform")
    if data != DEBIAN_PLATFORM:
        raise ValueError(f"{where}: only Debian 13 x86_64 is supported")
    return dict(DEBIAN_PLATFORM)


def _deployment_root(value: object, where: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ValueError(f"{where}: expected a bounded absolute deployment root")
    validate_no_control_characters(value, where)
    validate_filesystem_path(value, must_exist=False)
    if (not re.fullmatch(r"/[A-Za-z0-9_./-]+", value)
            or value.startswith("//") or value == "/"
            or posixpath.normpath(value) != value
            or any(part in {"", ".", ".."} for part in value[1:].split("/"))):
        raise ValueError(f"{where}: deployment root must be a canonical absolute path")
    if ".basaltwater_shared" in value.split("/"):
        raise ValueError(f"{where}: deployment root must not overlap managed target state")
    return value


def _overlap(left: str, right: str) -> bool:
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def _route_path(value: object, where: str) -> str:
    path = validate_nginx_path(value)
    # The deployment renderer treats a trailing slash as the same route.
    return path.rstrip("/") or "/"


def _domain(value: object, where: str) -> str:
    if (not isinstance(value, str) or len(value) > 253
            or not validate_host(value) or "{{" in value or "}}" in value):
        raise ValueError(f"{where}: expected a literal hostname")
    normalized = value.lower().removesuffix(".")
    if not normalized or not validate_host(normalized):
        raise ValueError(f"{where}: expected a literal hostname")
    validate_no_control_characters(normalized, where)
    return normalized


def _route(value: object, where: str) -> dict[str, str]:
    data = _record(value, {"type", "domain", "path"}, where)
    component_type = data["type"]
    if not isinstance(component_type, str) or component_type not in _SUPPORTED_COMPONENT_TYPES:
        raise ValueError(f"{where}: target component type must be static or service")
    return {
        "type": component_type,
        "domain": _domain(data["domain"], where + " domain"),
        "path": _route_path(data["path"], where + " path"),
    }


def _target_limits(value: object, where: str) -> dict[str, int]:
    data = _record(value, {"artifact_bytes", "artifact_entries"}, where)
    return {
        "artifact_bytes": _integer(data["artifact_bytes"], 1, MAX_ARTIFACT_BYTES,
                                   where + " artifact_bytes"),
        "artifact_entries": _integer(data["artifact_entries"], 1, MAX_FILES,
                                     where + " artifact_entries"),
    }


def parse_target_policy(value: object) -> dict[str, Any]:
    """Parse a bounded strict schema-1 administrator target policy.

    Pass raw UTF-8 JSON bytes to detect duplicate keys at the parse boundary.
    The normalized result is detached, sorted, and contains only explicitly
    granted applications, principals, project IDs, routes and target roots.
    """
    if isinstance(value, bytes):
        value = decode_document(value, maximum=MAX_POLICY_BYTES)
    data = _record(value, {"schema", "applications"}, "target policy")
    if type(data["schema"]) is not int or data["schema"] != POLICY_SCHEMA:
        raise ValueError("Unsupported target policy schema")

    applications_value = data["applications"]
    if (not isinstance(applications_value, Mapping) or not applications_value
            or len(applications_value) > MAX_APPLICATIONS):
        raise ValueError(f"Target policy must contain 1 to {MAX_APPLICATIONS} applications")

    application_names = [
        _slug(raw, _APPLICATION_SLUG, "Target application") for raw in applications_value
    ]
    applications: dict[str, Any] = {}
    all_principals: set[str] = set()
    all_projects: set[str] = set()
    principal_applications: dict[str, int] = {}
    grant_count = 0
    roots: list[tuple[str, str]] = []
    identity_fragments: dict[str, str] = {}
    routes: dict[tuple[str, str], tuple[str, str]] = {}

    for application in sorted(application_names):
        grant = _record(
            applications_value[application],
            {"project", "principals", "deployment_root", "platform", "limits", "components"},
            f"application {application!r}",
        )
        project = _slug(grant["project"], _PROJECT_SLUG,
                        f"application {application!r} project")
        all_projects.add(project)
        if len(all_projects) > MAX_PROJECTS:
            raise ValueError(f"Target policy exceeds its {MAX_PROJECTS} project limit")

        principals_value = grant["principals"]
        if (not isinstance(principals_value, list) or not principals_value
                or len(principals_value) > MAX_PRINCIPALS):
            raise ValueError(
                f"Application {application!r} must allow 1 to {MAX_PRINCIPALS} principals"
            )
        principals = [_principal(raw) for raw in principals_value]
        if len(set(principals)) != len(principals):
            raise ValueError(f"Application {application!r} contains duplicate principals")
        all_principals.update(principals)
        if len(all_principals) > MAX_PRINCIPALS:
            raise ValueError(f"Target policy exceeds its {MAX_PRINCIPALS} principal limit")
        grant_count += len(principals)
        if grant_count > MAX_APPLICATION_GRANTS:
            raise ValueError("Target policy exceeds its application grant limit")
        for principal in principals:
            count = principal_applications.get(principal, 0) + 1
            if count > MAX_APPLICATIONS_PER_PRINCIPAL:
                raise ValueError(
                    f"Principal {principal!r} exceeds its application grant limit"
                )
            principal_applications[principal] = count

        deployment_root = _deployment_root(
            grant["deployment_root"], f"Application {application!r} deployment_root"
        )
        for previous_root, previous_application in roots:
            if _overlap(deployment_root, previous_root):
                raise ValueError(
                    f"Application roots for {previous_application!r} and {application!r} overlap"
                )
        # Deployment derives service units and persistent state from the root
        # basename after this same sanitization. Distinct roots can still collide.
        identity_fragment = re.sub(
            r"[^a-z0-9_-]", "-", posixpath.basename(deployment_root).lower()
        ).strip("-_") or "x"
        previous_application = identity_fragments.get(identity_fragment)
        if previous_application is not None:
            raise ValueError(
                f"Application roots for {previous_application!r} and {application!r} "
                "collide on generated service identity"
            )
        identity_fragments[identity_fragment] = application
        roots.append((deployment_root, application))

        components_value = grant["components"]
        if (not isinstance(components_value, Mapping) or not components_value
                or len(components_value) > MAX_COMPONENTS_PER_APPLICATION):
            raise ValueError(
                f"Application {application!r} must define 1 to "
                f"{MAX_COMPONENTS_PER_APPLICATION} component routes"
            )
        component_names = [
            _slug(raw, _COMPONENT_NAME, f"Application {application!r} component name")
            for raw in components_value
        ]
        components: dict[str, Any] = {}
        for name in sorted(component_names):
            route = _route(components_value[name], f"Component {name!r}")
            route_key = (route["domain"], route["path"])
            previous = routes.get(route_key)
            if previous is not None:
                raise ValueError(
                    f"Target route {route['domain']}{route['path']} is shared by "
                    f"{previous[0]!r}/{previous[1]!r} and {application!r}/{name!r}"
                )
            routes[route_key] = (application, name)
            components[name] = route

        applications[application] = {
            "project": project,
            "principals": sorted(principals),
            "deployment_root": deployment_root,
            "platform": _platform(grant["platform"],
                                  f"Application {application!r} platform"),
            "limits": _target_limits(grant["limits"],
                                      f"Application {application!r} limits"),
            "components": components,
        }

    result = {"schema": POLICY_SCHEMA, "applications": applications}
    encode_document(result, maximum=MAX_POLICY_BYTES)
    return result


def _exports(value: object, component_type: str, where: str) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= 128:
        raise ValueError(f"{where}: artifacts must contain 1 to 128 exports")
    paths = [validate_artifact_path(item, allow_root=True) for item in value]
    if len(paths) != len(set(paths)):
        raise ValueError(f"{where}: duplicate artifact exports")
    if "." in paths and (component_type == "service" or len(paths) != 1):
        raise ValueError(f"{where}: root export is limited to one static component export")
    return paths


def _covered_by_export(path: str, exports: list[str]) -> bool:
    return any(root == "." or path == root or path.startswith(root + "/")
               for root in exports)


def _literal_binary(value: object, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{where}: expected a literal relative executable path")
    validate_no_control_characters(value, where)
    if any(character in _SYSTEMD_PATH_META or character.isspace() for character in value):
        raise ValueError(f"{where}: executable path must be literal and systemd-safe")
    if "{{" in value or "}}" in value:
        raise ValueError(f"{where}: executable path cannot contain templates")
    try:
        return validate_artifact_path(value)
    except ValueError as exc:
        raise ValueError(f"{where}: executable path is not a safe relative export") from exc


def _component(value: object, where: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{where}: expected an object")
    raw = dict(value)
    base = {"name", "type", "domain", "path", "artifacts"}
    component_type = raw.get("type")
    if component_type == "static":
        allowed = base | {"output", "spa"}
        required = allowed
    elif component_type == "service":
        allowed = base | {"binary", "health"}
        required = base | {"binary"}
    else:
        raise ValueError(f"{where}: type must be static or service")
    if (any(not isinstance(key, str) for key in raw)
            or not required <= set(raw) or not set(raw) <= allowed):
        raise ValueError(f"{where}: missing or unsupported fields")

    name = _slug(raw["name"], _COMPONENT_NAME, where + " name")
    normalized: dict[str, Any] = {
        "name": name,
        "type": component_type,
        "domain": _domain(raw["domain"], where + " domain"),
        "path": _route_path(raw["path"], where + " path"),
        "artifacts": _exports(raw["artifacts"], component_type, where),
    }
    if component_type == "static":
        output = validate_artifact_path(raw["output"], allow_root=True)
        if type(raw["spa"]) is not bool:
            raise ValueError(f"{where}: spa must be a boolean")
        if not _covered_by_export(output, normalized["artifacts"]):
            raise ValueError(f"{where}: output is not covered by the declared exports")
        normalized.update(output=output, spa=raw["spa"])
        return normalized

    binary = _literal_binary(raw["binary"], where + " binary")
    if not _covered_by_export(binary, normalized["artifacts"]):
        raise ValueError(f"{where}: binary is not covered by the declared exports")
    health = raw.get("health")
    if health is not None:
        health = _route_path(health, where + " health")
    normalized.update(binary=binary, health=health)
    return normalized


def parse_target_deployment(value: object, *, request: object) -> dict[str, Any]:
    """Validate the safe serving projection and bind it to exact build exports.

    Service ports and generated service identities are implicit defaults. The
    schema intentionally has no command, build environment, runtime environment,
    secret, arbitrary execution, persistent-data, or identity fields.
    """
    data = _record(value, {"schema", "application", "project", "platform", "components"},
                   "target deployment")
    if type(data["schema"]) is not int or data["schema"] != POLICY_SCHEMA:
        raise ValueError("Unsupported target deployment schema")
    application = _slug(data["application"], _APPLICATION_SLUG, "Target application")
    project = _slug(data["project"], _PROJECT_SLUG, "Target deployment project")
    platform = _platform(data["platform"], "Target deployment platform")
    components_value = data["components"]
    if (not isinstance(components_value, list) or not components_value
            or len(components_value) > MAX_COMPONENTS_PER_APPLICATION):
        raise ValueError("Target deployment must contain 1 to 100 components")
    components = [_component(item, f"Target component {index}")
                  for index, item in enumerate(components_value)]
    names = [item["name"] for item in components]
    if len(names) != len(set(names)):
        raise ValueError("Target deployment contains duplicate component names")
    components.sort(key=lambda item: item["name"])

    job = parse_build_request(request)
    if project != job["project"]:
        raise ValueError("Target deployment project does not match the build request")
    if platform != job["platform"]:
        raise ValueError("Target deployment platform does not match the build request")
    request_components = {item["name"]: item for item in job["components"]}
    if set(names) != set(request_components):
        raise ValueError("Target deployment components do not match the build request")
    for item in components:
        requested = request_components[item["name"]]
        if (item["type"] != requested["type"]
                or item["artifacts"] != requested["artifacts"]):
            raise ValueError(
                f"Target component {item['name']!r} does not match the build exports"
            )

    return {
        "schema": POLICY_SCHEMA,
        "application": application,
        "project": project,
        "platform": platform,
        "components": components,
    }


def authorize_target_deployment(
    policy: object,
    *,
    principal: str,
    application: str,
    request: object,
    deployment: object,
) -> dict[str, Any]:
    """Authorize and return private target bindings plus their canonical digest.

    ``principal`` must be supplied by authenticated transport state. The
    returned ``bindings`` are detached private data and may include the
    administrator-owned deployment root. Persist only ``bindings_sha256`` in
    an admission ledger; never copy request commands or build environment into
    target bindings.
    """
    configured = parse_target_policy(policy)
    owner = _principal(principal)
    app_id = _slug(application, _APPLICATION_SLUG, "Target application")
    job = parse_build_request(request)
    target = parse_target_deployment(deployment, request=job)
    if target["application"] != app_id:
        raise ValueError("Target deployment application does not match the authorized application")

    grant = configured["applications"].get(app_id)
    if grant is None or owner not in (grant or {}).get("principals", []):
        raise PermissionError("Principal is not authorized for this target application")
    if grant["project"] != job["project"]:
        raise PermissionError("Build project does not match the target application grant")
    if (job["platform"] != grant["platform"] or target["platform"] != grant["platform"]):
        raise PermissionError("Build platform does not match the target application grant")
    if any(job["limits"][key] > grant["limits"][key] for key in grant["limits"]):
        raise PermissionError("Build artifact ceilings exceed the target application grant")

    approved = grant["components"]
    if {item["name"] for item in target["components"]} != set(approved):
        raise PermissionError("Target components do not match the approved application routes")
    for item in target["components"]:
        route = approved[item["name"]]
        if any(item[key] != route[key] for key in ("type", "domain", "path")):
            raise PermissionError(
                f"Target component {item['name']!r} does not match its approved route"
            )

    bindings = {
        "schema": POLICY_SCHEMA,
        "principal": owner,
        "application": app_id,
        "project": job["project"],
        "deployment_root": grant["deployment_root"],
        "platform": dict(grant["platform"]),
        "limits": dict(grant["limits"]),
        "components": [dict(item, artifacts=list(item["artifacts"]))
                       for item in target["components"]],
    }
    digest = hashlib.sha256(encode_document(bindings, maximum=MAX_POLICY_BYTES)).hexdigest()
    return {"bindings": bindings, "bindings_sha256": digest}


def capabilities_for_principal(policy: object, *, principal: str) -> dict[str, Any]:
    """Project authorized application IDs, routes, platform and ceilings only.

    No deployment roots, artifact paths, binary paths, or other filesystem
    locations are returned. An unknown principal receives an empty map.
    """
    configured = parse_target_policy(policy)
    owner = _principal(principal)
    applications: dict[str, Any] = {}
    for app_id, grant in configured["applications"].items():
        if owner not in grant["principals"]:
            continue
        applications[app_id] = {
            "project": grant["project"],
            "platform": dict(grant["platform"]),
            "limits": dict(grant["limits"]),
            "components": [
                {"name": name, **dict(route)}
                for name, route in grant["components"].items()
            ],
        }
    return {"schema": POLICY_SCHEMA, "applications": applications}


__all__ = [
    "MAX_APPLICATIONS",
    "MAX_APPLICATIONS_PER_PRINCIPAL",
    "MAX_APPLICATION_GRANTS",
    "MAX_COMPONENTS_PER_APPLICATION",
    "MAX_POLICY_BYTES",
    "MAX_PRINCIPALS",
    "MAX_PROJECTS",
    "POLICY_SCHEMA",
    "authorize_target_deployment",
    "capabilities_for_principal",
    "parse_target_deployment",
    "parse_target_policy",
]
