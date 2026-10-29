#!/usr/bin/env python3
"""Draft managed build enrollments from pinned HTTPS manifests; enroll nothing."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Any

# Support direct invocation from any working directory without installation.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.build_host_steps import parse_build_host_enrollment
from common.build_target_steps import parse_build_target_enrollment
from lib.build_manifest import DEBIAN_PLATFORM, required_tools
from lib.build_selection import _public_companion
from lib.build_setup_plan import _destination, _projection, prepare_delivery_plan
from lib.build_setup_sources import PreparedSource, prepare_setup_sources
from lib.deploy_utils import parse_deploy_spec
from lib.deployment import DeploymentOrchestrator
from lib.setup_directories import directory_fd
from lib.validation import validate_filesystem_path


DRAFT_LIMITS = {
    "memory_bytes": 1024**3, "cpu_percent": 200, "scratch_bytes": 4 * 1024**3,
    "artifact_bytes": 128 * 1024**2, "artifact_entries": 65536,
    "timeout_seconds": 900, "log_bytes": 4 * 1024**2,
}
BUILDER_FILENAME = "builder-enrollment.json"
TARGET_FILENAME = "target-enrollment.json"


def application_slug(destination: str) -> str:
    """Return an order-independent readable slug, disambiguating lossy spelling."""
    domain = _destination(destination).lower().removesuffix(".")
    _, path = parse_deploy_spec(destination)
    identity = domain + (path.rstrip("/") or "/")
    readable = re.sub(r"[^a-z0-9]+", "-", identity).strip("-")
    digest = hashlib.sha256(identity.encode()).hexdigest()[:12]
    return readable[:51].rstrip("-") + "-" + digest


def draft_enrollments(
    prepared: list[PreparedSource], *, builder_key: str, target_key: str,
    principal: str = "deploy", limits: dict[str, int] | None = None,
    builder_max_bytes: int = 64 * 1024**3, target_max_bytes: int = 8 * 1024**3,
    builder_max_records: int = 128, target_max_records: int = 128,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build and validate detached drafts from already pinned sources, without I/O."""
    if not 1 <= len(prepared) <= 16:
        raise ValueError("Drafts require 1..16 prepared destinations")
    ceilings = dict(DRAFT_LIMITS if limits is None else limits)
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
    # Structural compatibility only: these placeholders are never represented as
    # observed versions or written to the drafts. Delivery probes real readiness.
    capabilities = {"schema": 1, "projects": builder["policy"]["principals"][principal]["projects"],
                    "tool_versions": {tool: "draft-validation" for tool in ("go", "node", "python")}}
    for item in prepared:
        prepare_delivery_plan(
            item.manifest, destination=item.destination, source=item.source,
            source_path=item.source_path, request_id="0" * 32, idempotency_key="0" * 32,
            target_enrollment=target, target_principal=principal,
            builder_capabilities=capabilities, build_tools=item.build_tools,
        )
    return builder, target


def write_drafts(output_dir: str, builder: dict[str, Any], target: dict[str, Any]) -> None:
    """Exclusively create both validated drafts under an owned mode-0700 directory."""
    builder = parse_build_host_enrollment(builder)
    target = parse_build_target_enrollment(target)
    documents = [(BUILDER_FILENAME, builder), (TARGET_FILENAME, target)]
    payloads = [(name, (json.dumps(document, indent=2, sort_keys=True) + "\n").encode())
                for name, document in documents]
    validate_filesystem_path(output_dir)
    with directory_fd(os.path.abspath(output_dir), create=True, mode=0o700) as parent:
        info = os.fstat(parent)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError("Output directory must be caller-owned mode 0700")
        created: list[tuple[str, int]] = []
        try:
            # Reserve both names before writing either; an existing name is never replaced.
            for name, _ in payloads:
                descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                     0o600, dir_fd=parent)
                created.append((name, descriptor))
                os.fchmod(descriptor, 0o600)
            for (_, descriptor), (_, payload) in zip(created, payloads):
                with os.fdopen(os.dup(descriptor), "wb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
            os.fsync(parent)
        except BaseException:
            for name, _ in created:
                os.unlink(name, dir_fd=parent)
            raise
        finally:
            for _, descriptor in created:
                os.close(descriptor)


def main(argv: list[str] | None = None) -> int:
    """Fetch bounded HTTPS inputs and write drafts for operator review."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deploy", nargs=2, action="append", required=True,
                        metavar=("DOMAIN[/PATH]", "HTTPS_URL"))
    parser.add_argument("--builder-key", required=True, help="ssh-ed25519 PUBLIC companion file")
    parser.add_argument("--target-key", required=True, help="ssh-ed25519 PUBLIC companion file")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--principal", default="deploy")
    for name, default in DRAFT_LIMITS.items():
        parser.add_argument("--" + name.replace("_", "-"), type=int, default=default)
    for role, default_bytes in (("builder", 64 * 1024**3), ("target", 8 * 1024**3)):
        parser.add_argument(f"--{role}-max-bytes", type=int, default=default_bytes)
        parser.add_argument(f"--{role}-max-records", type=int, default=128)
    args = parser.parse_args(argv)
    try:
        for path in (args.builder_key, args.target_key, args.output_dir):
            validate_filesystem_path(path)
        builder_key = _public_companion(os.path.abspath(os.path.expanduser(args.builder_key)))
        target_key = _public_companion(os.path.abspath(os.path.expanduser(args.target_key)))
        with tempfile.TemporaryDirectory(prefix="basaltwater-policy-drafts-") as root:
            os.chmod(root, 0o700)
            prepared = prepare_setup_sources(args.deploy, root)
            builder, target = draft_enrollments(
                prepared, builder_key=builder_key, target_key=target_key, principal=args.principal,
                limits={name: getattr(args, name) for name in DRAFT_LIMITS},
                builder_max_bytes=args.builder_max_bytes, target_max_bytes=args.target_max_bytes,
                builder_max_records=args.builder_max_records, target_max_records=args.target_max_records,
            )
        output = os.path.abspath(os.path.expanduser(args.output_dir))
        write_drafts(output, builder, target)
    except (OSError, ValueError, PermissionError, TimeoutError) as exc:
        parser.exit(1, f"Draft preparation failed: {exc}\n")
    print(f"Drafts written to {output}; operator review required before separate enrollment.")
    for slug, grant in target["policy"]["applications"].items():
        tools = builder["policy"]["principals"][args.principal]["projects"][slug]["runtimes"]
        print(f"  {slug}: {grant['deployment_root']}; runtimes={','.join(tools) or 'none'}")
        for name, route in grant["components"].items():
            print(f"    {name}: {route['type']} {route['domain']}{route['path']}")
    print("Review keys, complete routes/runtime grants, roots and ceilings in both JSON files.")
    print("Retained reservations consume finite capacity; no automatic cleanup is performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
