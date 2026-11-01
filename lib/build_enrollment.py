"""Shared, pure enrollment generation from pinned project manifests."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from common.build_host_steps import parse_build_host_enrollment
from common.build_target_steps import parse_build_target_enrollment
from lib.build_manifest import DEBIAN_PLATFORM, required_tools
from lib.build_setup_plan import _destination, _projection, prepare_delivery_plan
from lib.build_setup_sources import PreparedSource
from lib.deploy_utils import parse_deploy_spec
from lib.deployment import DeploymentOrchestrator


DEFAULT_POLICY_LIMITS = {
    "memory_bytes": 1024**3, "cpu_percent": 200, "scratch_bytes": 4 * 1024**3,
    "artifact_bytes": 128 * 1024**2, "artifact_entries": 65536,
    "timeout_seconds": 900, "log_bytes": 4 * 1024**2,
}


def application_slug(destination: str) -> str:
    """Return an order-independent readable slug, disambiguating lossy spelling."""
    domain = _destination(destination).lower().removesuffix(".")
    _, path = parse_deploy_spec(destination)
    identity = domain + (path.rstrip("/") or "/")
    readable = re.sub(r"[^a-z0-9]+", "-", identity).strip("-")
    digest = hashlib.sha256(identity.encode()).hexdigest()[:12]
    return readable[:51].rstrip("-") + "-" + digest


def generate_enrollments(
    prepared: list[PreparedSource], *, builder_key: str, target_key: str,
    principal: str = "deploy", limits: dict[str, int] | None = None,
    builder_max_bytes: int = 64 * 1024**3, target_max_bytes: int = 8 * 1024**3,
    builder_max_records: int = 128, target_max_records: int = 128,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Generate and validate detached enrollments without I/O or enrollment."""
    if not 1 <= len(prepared) <= 16:
        raise ValueError("Enrollments require 1..16 prepared destinations")
    ceilings = dict(DEFAULT_POLICY_LIMITS if limits is None else limits)
    projects: dict[str, Any] = {}
    applications: dict[str, Any] = {}
    local = DeploymentOrchestrator()
    for item in prepared:
        slug = application_slug(item.destination)
        if slug in applications:
            raise ValueError("Duplicate destination or application/project slug collision")
        domain, path = parse_deploy_spec(item.destination)
        rendered_domain = _destination(item.destination)
        components = [_projection(component, rendered_domain) for component in item.manifest.components]
        routes = {}
        for component in components:
            if component["name"] in routes:
                raise ValueError("Duplicate component name")
            routes[component["name"]] = {
                key: value for key, value in component.items()
                if key in {"type", "domain", "path", "runtime"}
            }
        tools = item.build_tools
        if tools is None:
            tools = tuple(required_tools(item.manifest))
        projects[slug] = {"platform": dict(DEBIAN_PLATFORM),
                          "runtimes": list(tools), "limits": dict(ceilings)}
        applications[slug] = {
            "project": slug, "principals": [principal],
            "deployment_root": local.get_deployment_path(domain, path, ""),
            "platform": dict(DEBIAN_PLATFORM),
            "limits": {key: ceilings[key] for key in ("artifact_bytes", "artifact_entries")},
            "components": routes,
        }
    builder = parse_build_host_enrollment({
        "schema": 1, "keys": {principal: builder_key},
        "policy": {"schema": 1, "principals": {principal: {"projects": projects}}},
        "max_bytes": builder_max_bytes, "max_records": builder_max_records,
    })
    target = parse_build_target_enrollment({
        "schema": 1, "keys": {principal: target_key},
        "policy": {"schema": 1, "applications": applications},
        "max_bytes": target_max_bytes, "max_records": target_max_records,
    })
    # Structural compatibility only: delivery observes actual installed tools.
    capabilities = {"schema": 1, "projects": builder["policy"]["principals"][principal]["projects"],
                    "tool_versions": {tool: "policy-validation" for tool in ("go", "node", "python")}}
    for item in prepared:
        prepare_delivery_plan(
            item.manifest, destination=item.destination, source=item.source,
            source_path=item.source_path, request_id="0" * 32, idempotency_key="0" * 32,
            target_enrollment=target, target_principal=principal,
            builder_capabilities=capabilities, build_tools=item.build_tools,
        )
    return builder, target


def generate_builder_enrollment(
    prepared: list[PreparedSource], *, builder_key: str, principal: str = "deploy",
    limits: dict[str, int] | None = None, builder_max_bytes: int = 64 * 1024**3,
    builder_max_records: int = 128,
) -> dict[str, Any]:
    """Validate each deployable project independently, then combine builder grants.

    One builder may serve several targets that reuse routes, fixed ports or
    deployment roots. Only an actual target's enrollment combines those serving
    constraints. Individual project validation still uses the shared generator
    and delivery planner, including supported manifest and runtime checks.
    """
    if not 1 <= len(prepared) <= 16:
        raise ValueError("Enrollments require 1..16 prepared destinations")
    projects: dict[str, Any] = {}
    builder: dict[str, Any] = {}
    for item in prepared:
        builder, _ = generate_enrollments(
            [item], builder_key=builder_key, target_key=builder_key, principal=principal,
            limits=limits, builder_max_bytes=builder_max_bytes,
            builder_max_records=builder_max_records,
        )
        additions = builder["policy"]["principals"][principal]["projects"]
        if projects.keys() & additions.keys():
            raise ValueError("Duplicate destination or application/project slug collision")
        projects.update(additions)
    builder["policy"]["principals"][principal]["projects"] = projects
    return parse_build_host_enrollment(builder)
