"""Controller preparation of flags-only managed build enrollment."""

from __future__ import annotations

import argparse
import copy
import os
import tempfile

from common.build_host_steps import parse_build_host_enrollment
from common.build_target_steps import parse_build_target_enrollment
from lib.build_enrollment import DEFAULT_POLICY_LIMITS, generate_enrollments
from lib.build_host_policy import _protocol_limits
from lib.build_selection import _check_private_identity, _local_path, _public_companion
from lib.build_setup import _private_directory, validate_build_setup_selection
from lib.build_setup_sources import prepare_setup_sources
from lib.config import SetupConfig
from lib.validation import validate_deploy_specs, validate_hosted_flags, validate_positive_integer
from lib.validators import validate_host
from lib.workspace import get_workspace_dir


def _principal(enrollment: dict | None, key: str) -> str:
    if enrollment is None:
        return "deploy"
    matches = [name for name, value in enrollment["keys"].items() if value == key]
    if len(matches) != 1:
        raise ValueError("Selected SSH public companion must match exactly one existing enrollment principal")
    return matches[0]


def _merge(enrollment: dict | None, generated: dict, *, builder: bool,
           principal: str, overrides: dict[str, int], args: argparse.Namespace) -> dict:
    result = copy.deepcopy(generated if enrollment is None else enrollment)
    if enrollment is not None:
        if builder:
            current = result["policy"]["principals"][principal]["projects"]
            additions = generated["policy"]["principals"][principal]["projects"]
        else:
            current = result["policy"]["applications"]
            additions = generated["policy"]["applications"]
        for name, grant in additions.items():
            previous = current.get(name)
            if previous is not None:
                if not builder and (previous["deployment_root"] != grant["deployment_root"]
                                    or previous["project"] != grant["project"]):
                    raise ValueError("Automatic target enrollment cannot change existing roots or project identities")
                if not builder:
                    grant["principals"] = sorted(set(previous["principals"]) | set(grant["principals"]))
                grant["limits"] = dict(previous["limits"])
                grant["limits"].update({key: value for key, value in overrides.items()
                                       if builder or key in grant["limits"]})
            current[name] = grant
    for field in ("max_records", "max_bytes"):
        value = vars(args).get("build_" + field)
        if value is not None:
            result[field] = value
    return (parse_build_host_enrollment if builder else parse_build_target_enrollment)(result)


def prepare_cli_build_enrollments(
    config: SetupConfig, args: argparse.Namespace, *, workspace: str | None = None,
) -> None:
    """Compile explicit CLI authority into the normal saved enrollment fields.

    No policy files are published. Temporary pinned source inputs are removed
    on every exit. Call once after patch merging and before provisioning,
    credential storage or remote mutation. Existing grants remain snapshots
    unless the operator explicitly requests generated authority again.
    """
    options = vars(args)
    projects = options.get("build_project")
    explicit_target = options.get("build_target_auto", False)
    target = explicit_target or (config.build_on is not None
                                and config.build_target_enrollment is None
                                and not config.disable_build_target)
    raw_limits = options.get("build_limit") or []
    capacity = any(options.get("build_" + field) is not None
                   for field in ("max_bytes", "max_records"))
    if not projects and not target:
        if raw_limits or capacity:
            raise ValueError("Build policy limits require --build-project or --build-target-auto")
        return
    from lib.setup_common import _apply_hosted_proxmox_defaults

    _apply_hosted_proxmox_defaults(config, workspace)
    if not validate_host(config.host):
        raise ValueError("Invalid build enrollment host")
    if config.machine_type not in {"vm", "hardware"} or config.host in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Generated build enrollment requires a remote Debian VM or hardware host")
    validate_hosted_flags(config)
    if projects:
        if not config.is_build_server or config.disable_build_host:
            raise ValueError("--build-project requires an enabled --build-server")
        if options.get("build_host_policy") or options.get("build_host_enrollment_json"):
            raise ValueError("Choose --build-project or explicit builder enrollment")
    if target:
        if options.get("build_target_policy") or options.get("build_target_enrollment_json"):
            raise ValueError("Choose --build-target-auto or explicit target enrollment")
        if explicit_target and options.get("disable_build_target"):
            raise ValueError("Choose --build-target-auto or --no-build-target")
        selection = copy.copy(config)
        if explicit_target:
            selection.disable_build_target = False
        validate_build_setup_selection(selection, require_enrollment=False)
    specs = projects if projects else config.deploy_specs
    validate_deploy_specs(specs)
    overrides: dict[str, int] = {}
    for name, raw in raw_limits:
        if name not in DEFAULT_POLICY_LIMITS or name in overrides:
            raise ValueError("--build-limit requires a unique supported resource name")
        overrides[name] = validate_positive_integer(raw, "--build-limit " + name)
    limits = _protocol_limits(dict(DEFAULT_POLICY_LIMITS, **overrides), "Generated policy limits")
    for field in ("max_records", "max_bytes"):
        value = options.get("build_" + field)
        if value is not None:
            validate_positive_integer(str(value), "--build-" + field.replace("_", "-"))
    try:
        identity = _local_path(config.ssh_key or "~/.ssh/id_ed25519", "SSH identity file")
        _check_private_identity(identity)
        key = _public_companion(identity + ".pub")
        existing = config.build_host_enrollment if projects else config.build_target_enrollment
        principal = _principal(existing, key)
        root = get_workspace_dir() if workspace is None else workspace
        _private_directory(root)
        with tempfile.TemporaryDirectory(prefix="basaltwater-build-policy-", dir=root) as temporary:
            os.chmod(temporary, 0o700)
            prepared = prepare_setup_sources(specs, temporary)
            builder, receiver = generate_enrollments(
                prepared, builder_key=key, target_key=key, principal=principal, limits=limits,
            )
            generated = builder if projects else receiver
            result = _merge(existing, generated, builder=bool(projects), principal=principal,
                            overrides=overrides, args=args)
    except (OSError, TimeoutError) as exc:
        raise ValueError(f"Build policy preparation failed: {exc}") from exc
    if projects:
        config.build_host_enrollment = result
    else:
        config.build_target_enrollment = result
        config.disable_build_target = False
    kind = "builder projects" if projects else "target applications"
    names = (result["policy"]["principals"][principal]["projects"] if projects
             else result["policy"]["applications"])
    print(f"  Generated {kind}: {', '.join(names)} (saved with setup; no policy files required)")
