"""Select build inputs from manifests without carrying deployment authority."""

from __future__ import annotations

from typing import Any, Mapping

from lib.build_protocol import DEFAULT_LIMITS, parse_build_request
from lib.project_manifest import Manifest, artifact_paths


DEBIAN_PLATFORM = {"os": "linux", "architecture": "x86_64", "baseline": "debian-13"}


def manifest_components(manifest: Manifest) -> list[dict[str, Any]]:
    """Select commands, build environment and exports, excluding runtime inputs."""
    if not isinstance(manifest, Manifest) or not manifest.components:
        raise ValueError("Remote builds require a manifest with deployment components")
    return [{"name": item.name, "type": item.type, "artifacts": artifact_paths(item),
             "commands": list(item.build), "env": dict(item.env)}
            for item in manifest.components]


def request_from_manifest(manifest: Manifest, *, request_id: str, project: str,
                          source: object, platform: object,
                          limits: Mapping[str, int] | None = None) -> dict[str, Any]:
    """Create the exact bounded build document from a separately pinned source."""
    return parse_build_request({
        "schema": 1, "request_id": request_id, "project": project,
        "source": source, "platform": platform, "components": manifest_components(manifest),
        "limits": dict(DEFAULT_LIMITS if limits is None else limits),
    })


def validate_manifest_request(manifest: Manifest, request: object) -> dict[str, Any]:
    """Check the pinned manifest again before executing the admitted commands."""
    job = parse_build_request(request)
    if job["components"] != manifest_components(manifest):
        raise ValueError("Build commands, environment or exports differ from the source manifest")
    return job


def required_tools(manifest: Manifest) -> list[str]:
    """Require explicit tools for command-bearing remote builds, without guessing."""
    manifest_components(manifest)
    if manifest.build_runtimes is None:
        if any(item.build for item in manifest.components):
            raise ValueError("Remote builds with commands require explicit build_runtimes")
        return []
    tools = manifest.build_runtimes
    if (not isinstance(tools, list) or len(tools) > 3
            or any(not isinstance(item, str) or item not in {"go", "node", "python"} for item in tools)
            or len(set(tools)) != len(tools)):
        raise ValueError("Remote build runtimes must be distinct supported tool names")
    return list(tools)


def check_tool_readiness(manifest: Manifest, versions: Mapping[str, str]) -> dict[str, str]:
    """Select observed tools after a trusted worker has probed installed versions."""
    tools = required_tools(manifest)
    if any(not isinstance(versions.get(name), str) or not versions[name].strip() for name in tools):
        raise ValueError("A declared remote build tool is unavailable")
    return {name: versions[name] for name in tools}
