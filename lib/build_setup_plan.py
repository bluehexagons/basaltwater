"""Pure, bounded manifest-to-delivery planning for the first public build slice."""

from __future__ import annotations

import os
from dataclasses import replace
from typing import Any

from common.build_target_steps import parse_build_target_enrollment
from lib.build_client import _absolute_path, _validate_capabilities
from lib.build_delivery import DeliveryPlan
from lib.build_manifest import (
    DEBIAN_PLATFORM, check_tool_readiness, request_from_manifest, required_tools,
)
from lib.build_protocol import DEFAULT_LIMITS, MAX_DESCRIPTOR_BYTES, encode_document
from lib.manifest_serving import (
    _normalize_binary_path, _exec_binary_path, _reject_interpreter, runtime_from_component,
)
from lib.build_target_policy import authorize_target_deployment
from lib.build_target_service import MAX_HEADER_LINE_BYTES, _parse_header
from lib.cicd_deploy_policy import validate_nginx_path
from lib.deploy_utils import parse_deploy_spec
from lib.project_manifest import Component, Manifest, artifact_paths, render_template
from lib.validation import validate_no_control_characters
from lib.validators import validate_host


_COMPONENT_FIELDS = frozenset({
    "name", "type", "domain", "path", "build", "env", "output", "binary", "exec",
    "port", "env_file", "reverse_proxy", "health", "working_dir", "runtime_env",
    "sqlite_backup", "backup_retention", "public_udp", "spa", "artifacts",
})


def _destination(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 4350:
        raise ValueError("Destination must be a bounded DOMAIN[/PATH] deploy spec")
    validate_no_control_characters(value, "Build destination")
    domain, path = parse_deploy_spec(value)
    if domain is None or not validate_host(domain):
        raise ValueError("Build destination requires a literal domain")
    validate_nginx_path(path)
    return domain


def _projection(component: Component, domain: str) -> dict[str, Any]:
    if (not isinstance(component, Component) or set(vars(component)) != _COMPONENT_FIELDS
            or component.type not in {"static", "service"}):
        raise ValueError("Only static and native binary service components are supported")
    # These fields cannot be represented by the typed target projection. Check
    # even static dataclasses, so a caller cannot lose meaningful runtime inputs.
    if component.type == "static" and (component.exec is not None or component.runtime_env or component.env_file is not None
            or component.working_dir is not None or component.sqlite_backup is not None
            or component.public_udp or component.backup_retention != 10
            or component.port is not None or component.reverse_proxy is not True):
        raise ValueError(f"Component {component.name!r} has unsupported execution or routing settings")
    rendered = render_template(component.domain, {"domain": domain})
    if not validate_host(rendered) or "{{" in rendered or "}}" in rendered:
        raise ValueError("Component domain must render to a literal hostname")
    # Local manifest deployment uses the component path directly, without
    # prefixing the destination path. Paths are literal, not templates.
    path = validate_nginx_path(component.path).rstrip("/") or "/"
    result: dict[str, Any] = {
        "name": component.name, "type": component.type,
        "domain": rendered.lower().removesuffix("."), "path": path,
        "artifacts": artifact_paths(component),
    }
    if component.type == "static":
        if component.binary is not None or component.health is not None:
            raise ValueError("Static components cannot declare service settings")
        if not isinstance(component.output, str) or not component.output:
            raise ValueError("Static components require an output path")
        result.update(output=os.path.normpath(component.output), spa=component.spa)
    else:
        if component.output is not None or component.spa is not False:
            raise ValueError("Services cannot declare static settings")
        binary = (_normalize_binary_path(component.binary) if component.binary is not None
                  else _exec_binary_path(component.exec))
        _reject_interpreter(binary)
        result.update(binary=binary, health=component.health)
        runtime = runtime_from_component(component)
        if runtime is not None:
            result["runtime"] = runtime
    return result


def prepare_delivery_plan(
    manifest: Manifest, *, destination: str, source: object, source_path: str,
    request_id: str, idempotency_key: str, target_enrollment: object,
    target_principal: str, builder_capabilities: object,
    build_tools: tuple[str, ...] | None = None,
) -> DeliveryPlan:
    """Plan one application from caller-pinned inputs, without I/O or ID creation.

    Enrollment is administrator-owned, principal is resolved transport metadata,
    and capabilities are the authenticated builder response. This checks their
    shape and compatibility; live authorization is repeated during delivery.
    Snapshot existence, permissions and identity remain delivery checks.
    """
    if (not isinstance(manifest, Manifest) or type(manifest.version) is not int
            or manifest.version != 1 or not isinstance(manifest.components, list)
            or not 1 <= len(manifest.components) <= 100):
        raise ValueError("Delivery planning requires a version-1 deployment manifest")
    if set(vars(manifest)) != {"version", "components", "ci", "publishing", "build_runtimes"}:
        raise ValueError("Manifest has unsupported fields")
    if manifest.ci or manifest.publishing:
        raise ValueError("CI and publishing workflows are outside delivery planning")
    domain = _destination(destination)
    if not isinstance(source_path, str):
        raise ValueError("Source snapshot must be an absolute text path")
    snapshot = _absolute_path(source_path, "Source snapshot")
    components = [_projection(component, domain) for component in manifest.components]
    effective_manifest = manifest
    if build_tools is not None:
        if (not isinstance(build_tools, tuple) or len(build_tools) > 3
                or any(not isinstance(tool, str) or tool not in {"go", "node", "python"}
                       for tool in build_tools) or len(set(build_tools)) != len(build_tools)):
            raise ValueError("Invalid pinned-source runtime selection")
        if manifest.build_runtimes is not None and list(build_tools) != required_tools(manifest):
            raise ValueError("Pinned runtime selection differs from explicit build_runtimes")
        effective_manifest = replace(manifest, build_runtimes=list(build_tools))
    tools = required_tools(effective_manifest)

    enrollment = parse_build_target_enrollment(target_enrollment)
    if not isinstance(target_principal, str) or target_principal not in enrollment["keys"]:
        raise PermissionError("Target principal is not enrolled")
    routes = {item["name"]: {key: item[key] for key in ("type", "domain", "path")}
              for item in components}
    candidates = [(name, grant) for name, grant in enrollment["policy"]["applications"].items()
                  if target_principal in grant["principals"] and {
                      name: {key: route[key] for key in ("type", "domain", "path")}
                      for name, route in grant["components"].items()
                  } == routes]
    if len(candidates) != 1:
        raise ValueError("Delivery requires exactly one matching target application")
    application, target = candidates[0]
    # Project authority comes exclusively from the selected target application.
    capabilities = _validate_capabilities(builder_capabilities)
    builder = capabilities["projects"].get(target["project"])
    if (builder is None or builder["platform"] != target["platform"]
            or builder["platform"] != DEBIAN_PLATFORM
            or not set(tools).issubset(builder["runtimes"])):
        raise PermissionError("Builder lacks the target project, platform or runtime grant")
    check_tool_readiness(effective_manifest, capabilities["tool_versions"])
    # Defaults are conservative requests, not permission to consume an entire
    # larger grant. Validated lower ceilings remain valid protocol values.
    limits = {key: min(default, builder["limits"][key], target["limits"].get(key, default))
              for key, default in DEFAULT_LIMITS.items()}
    request = request_from_manifest(
        manifest, request_id=request_id, project=target["project"], source=source,
        platform=target["platform"], limits=limits,
    )
    deployment = {
        "schema": 1, "application": application, "project": target["project"],
        "platform": dict(target["platform"]), "components": components,
    }
    authorized = authorize_target_deployment(
        enrollment["policy"], principal=target_principal, application=application,
        request=request, deployment=deployment,
    )
    health_paths = {item["name"]: item["health"] for item in components
                    if item["type"] == "service"}
    if any(item["type"] == "service" and item["health"] != health_paths[item["name"]]
           for item in authorized["bindings"]["components"]):
        raise ValueError("Service health paths must be canonical without changing their endpoint")
    # The authorization result includes private target bindings. Copy only the
    # small serving projection; deployment_root never becomes controller input.
    deployment = {key: authorized["bindings"][key]
                  for key in ("schema", "application", "project", "platform", "components")}
    plan = DeliveryPlan(request, snapshot, deployment, idempotency_key, tuple(tools))
    # Reuse wire parsers for keys, exact exports and both bounded headers,
    # without calling delivery's file-inspecting validation.
    _parse_header(encode_document({
        "schema": 1, "action": "reserve", "application": application,
        "idempotency_key": idempotency_key, "request": request,
        "deployment": deployment, "ttl_seconds": plan.ttl_seconds,
    }, maximum=MAX_HEADER_LINE_BYTES - 1) + b"\n")
    _parse_header(encode_document({
        "schema": 1, "action": "stage", "operation_id": "0" * 32, "generation": 4096,
        "request": request, "deployment": deployment, "artifact_sha256": "0" * 64,
        "artifact_size": limits["artifact_bytes"] + MAX_DESCRIPTOR_BYTES + 1024,
    }, maximum=MAX_HEADER_LINE_BYTES - 1) + b"\n")
    return plan
