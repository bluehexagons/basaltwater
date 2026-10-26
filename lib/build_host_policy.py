"""Strict, pure policy checks for the disabled shared build-host slice.

This module validates administrator-owned grants and authorizes an already
validated build request. It does not authenticate principals, load policy,
probe tools, execute commands, or mutate queue state.
"""

from __future__ import annotations

import re
from typing import Any

from lib.build_manifest import DEBIAN_PLATFORM
from lib.build_protocol import (
    DEFAULT_LIMITS,
    decode_document,
    encode_document,
    parse_build_request,
)
from lib.validation import validate_no_control_characters


POLICY_SCHEMA = 1
MAX_POLICY_BYTES = 4 * 1024**2
MAX_PRINCIPALS = 128
MAX_PROJECTS_PER_PRINCIPAL = 128
MAX_PROJECT_GRANTS = 4096
MAX_BUILD_RUNTIMES = 3

SUPPORTED_BUILD_RUNTIMES = frozenset({"go", "node", "python"})
SUPPORTED_COMPONENT_TYPES = frozenset({"static", "service"})

# Keep principal labels compatible with the authenticated owner labels retained
# by lib.build_jobs and lib.build_admission. Project names match build requests.
_PRINCIPAL_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}")
_PROJECT_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_POLICY_REQUEST_ID = "0" * 32
_POLICY_SOURCE = {
    "revision": "0" * 40,
    "snapshot_sha256": "0" * 64,
    "manifest_sha256": "0" * 64,
}


def _record(value: object, fields: set[str], where: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"{where}: missing or unsupported fields")
    return value


def _principal_label(value: object) -> str:
    if not isinstance(value, str) or _PRINCIPAL_LABEL.fullmatch(value) is None:
        raise ValueError("Build principal must be a safe authenticated owner label")
    validate_no_control_characters(value, "Build principal")
    return value


def _project_slug(value: object) -> str:
    if not isinstance(value, str) or _PROJECT_SLUG.fullmatch(value) is None:
        raise ValueError("Build policy project must be a lowercase project slug")
    validate_no_control_characters(value, "Build policy project")
    return value


def _protocol_limits(value: object, where: str) -> dict[str, int]:
    """Validate limits through the shared version-1 request parser."""
    _record(value, set(DEFAULT_LIMITS), where)
    probe = {
        "schema": 1,
        "request_id": _POLICY_REQUEST_ID,
        "project": "policy-check",
        "source": _POLICY_SOURCE,
        "platform": DEBIAN_PLATFORM,
        "components": [{
            "name": "policy-check",
            "type": "static",
            "artifacts": ["index.html"],
            "commands": [],
            "env": {},
        }],
        "limits": dict(value),
    }
    try:
        return parse_build_request(probe)["limits"]
    except ValueError:
        raise ValueError(f"{where}: invalid protocol resource limits") from None


def _build_runtimes(value: object, where: str) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_BUILD_RUNTIMES:
        raise ValueError(f"{where}: expected at most three supported build runtimes")
    if any(not isinstance(runtime, str) or runtime not in SUPPORTED_BUILD_RUNTIMES
           for runtime in value):
        raise ValueError(f"{where}: unsupported build runtime")
    if len(set(value)) != len(value):
        raise ValueError(f"{where}: duplicate build runtime")
    return sorted(value)


def _grant_projection(grant: dict[str, Any]) -> dict[str, Any]:
    """Return only safe, detached capability fields from one project grant."""
    return {
        "platform": dict(grant["platform"]),
        "runtimes": list(grant["runtimes"]),
        "limits": dict(grant["limits"]),
    }


def parse_build_host_policy(value: object) -> dict[str, Any]:
    """Validate and normalize a strict version-1 administrator policy.

    ``value`` may be an already-decoded mapping or raw UTF-8 JSON bytes. Pass
    raw bytes when duplicate JSON keys must be detected at the parse boundary.
    The returned mapping is detached and sorted by principal and project.
    """
    if isinstance(value, bytes):
        value = decode_document(value, maximum=MAX_POLICY_BYTES)
    data = _record(value, {"schema", "principals"}, "build host policy")
    if type(data["schema"]) is not int or data["schema"] != POLICY_SCHEMA:
        raise ValueError("Unsupported build host policy schema")

    principals_value = data["principals"]
    if (not isinstance(principals_value, dict) or not principals_value
            or len(principals_value) > MAX_PRINCIPALS):
        raise ValueError("Build host policy must contain 1 to 128 principals")

    principal_names = [_principal_label(raw) for raw in principals_value]
    principals: dict[str, Any] = {}
    total_projects = 0
    for principal in sorted(principal_names):
        principal_data = _record(
            principals_value[principal], {"projects"}, f"principal {principal!r}"
        )
        projects_value = principal_data["projects"]
        if (not isinstance(projects_value, dict) or not projects_value
                or len(projects_value) > MAX_PROJECTS_PER_PRINCIPAL):
            raise ValueError(f"Principal {principal!r} must have 1 to 128 project grants")
        total_projects += len(projects_value)
        if total_projects > MAX_PROJECT_GRANTS:
            raise ValueError("Build host policy exceeds its project grant limit")

        project_names = [_project_slug(raw) for raw in projects_value]
        projects: dict[str, Any] = {}
        for project in sorted(project_names):
            grant_data = _record(
                projects_value[project],
                {"platform", "runtimes", "limits"},
                f"project {project!r}",
            )
            platform = grant_data["platform"]
            if not isinstance(platform, dict) or platform != DEBIAN_PLATFORM:
                raise ValueError(f"Project {project!r} uses an unsupported build platform")
            projects[project] = {
                "platform": dict(DEBIAN_PLATFORM),
                "runtimes": _build_runtimes(
                    grant_data["runtimes"], f"Project {project!r} runtimes"
                ),
                "limits": _protocol_limits(
                    grant_data["limits"], f"Project {project!r} limits"
                ),
            }
        principals[principal] = {"projects": projects}

    result = {"schema": POLICY_SCHEMA, "principals": principals}
    encode_document(result, maximum=MAX_POLICY_BYTES)
    return result


def authorize_build_request(
    policy: object,
    *,
    principal: str,
    request: object,
    requested_runtimes: list[str],
) -> dict[str, Any]:
    """Authorize a request for the trusted principal's current project grant.

    ``principal`` must come from authenticated transport state, never request
    data. ``requested_runtimes`` must be derived from the pinned manifest with
    ``lib.build_manifest.required_tools()``. The returned object contains only
    the granted project, platform, allowed runtimes, and resource ceilings.
    Invalid schema/input raises ``ValueError``; a missing or insufficient grant
    raises ``PermissionError``.
    """
    validated_policy = parse_build_host_policy(policy)
    owner = _principal_label(principal)
    job = parse_build_request(request)
    runtimes = _build_runtimes(requested_runtimes, "Requested build runtimes")

    principal_grants = validated_policy["principals"].get(owner)
    grant = None if principal_grants is None else principal_grants["projects"].get(job["project"])
    if grant is None:
        raise PermissionError("Principal is not authorized for this build project")
    if (job["platform"] != DEBIAN_PLATFORM or job["platform"] != grant["platform"]
            or any(component["type"] not in SUPPORTED_COMPONENT_TYPES
                   for component in job["components"])
            or not set(runtimes).issubset(grant["runtimes"])
            or any(job["limits"][key] > grant["limits"][key] for key in DEFAULT_LIMITS)):
        raise PermissionError("Build request exceeds the current project grant")

    return {"project": job["project"], **_grant_projection(grant)}


def capabilities_for_principal(policy: object, *, principal: str) -> dict[str, Any]:
    """Project only this authenticated principal's safe build capabilities.

    Unknown principals receive an empty project map, which avoids revealing
    whether another principal or project exists in the policy.
    """
    validated_policy = parse_build_host_policy(policy)
    owner = _principal_label(principal)
    principal_grants = validated_policy["principals"].get(owner)
    projects = {} if principal_grants is None else principal_grants["projects"]
    return {
        "schema": POLICY_SCHEMA,
        "projects": {
            project: _grant_projection(grant)
            for project, grant in projects.items()
        },
    }


__all__ = [
    "MAX_BUILD_RUNTIMES",
    "MAX_POLICY_BYTES",
    "MAX_PRINCIPALS",
    "MAX_PROJECT_GRANTS",
    "MAX_PROJECTS_PER_PRINCIPAL",
    "POLICY_SCHEMA",
    "SUPPORTED_BUILD_RUNTIMES",
    "SUPPORTED_COMPONENT_TYPES",
    "authorize_build_request",
    "capabilities_for_principal",
    "parse_build_host_policy",
]
