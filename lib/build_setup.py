"""Controller orchestration of explicitly selected remote setup builds."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack
import copy
from dataclasses import asdict
import hashlib
import json
import os
import shlex
import subprocess
import tempfile
from typing import Any
import uuid

from lib.atomic_io import write_json_atomic
from lib.build_client import BuildHostClient, _open_private_directory
from lib.build_delivery import BuildDeliveryController, MAX_PLANS, _check_workspace_capacity
from lib.build_protocol import encode_document
from lib.build_selection import SSHEndpoint, resolve_saved_builder, resolve_target_endpoint
from lib.build_setup_plan import prepare_delivery_plan
from lib.build_target_client import BuildTargetClient
from lib.config import SetupConfig
from lib.operation_state import OperationStateStore
from lib.ssh_utils import build_ssh_command
from lib.validation import validate_deploy_specs
from lib.workspace import get_workspace_dir


MAX_RETAINED_ATTEMPTS = 32
MAX_PREPARED_BYTES = 32 * 1024**2


class BuildSetupRecoveryRequired(RuntimeError):
    """An unfinished controller setup must be inspected before another attempt."""

    def __init__(self, journal_path: str) -> None:
        self.journal_path = journal_path
        super().__init__(
            f"Remote-build setup needs reconciliation; preserve {journal_path} and its "
            "attempt directory, pinned sources, prepared inputs and delivery receipts. "
            "Inspect the recorded request/operation IDs and target setup/enrollment "
            "markers before explicit recovery. Do not retry with replacement IDs."
        )


def _prepare_sources(specs: list[list[str]], destination: str) -> list[Any]:
    from lib.build_setup_sources import prepare_setup_sources

    return prepare_setup_sources(specs, destination)


def _private_directory(path: str) -> None:
    """Create one controller-owned directory and validate its entire ancestry."""
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    descriptor = _open_private_directory(path)
    try:
        # Also repair a prior failed publication sync before reusing a path.
        os.fsync(descriptor)
        parent = os.open(os.path.dirname(path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        os.close(descriptor)


def _validate_selection(config: SetupConfig) -> None:
    from common.build_target_refresh import validate_artifact_refresh

    validate_artifact_refresh(config)
    validate_deploy_specs(config.deploy_specs)
    if (not config.build_on or not config.deploy_specs or config.disable_build_target
            or config.build_target_enrollment is None):
        raise ValueError("Build-on setup requires deployments and explicit target enrollment")
    if config.machine_type not in {"vm", "hardware"} or config.host in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Build-on setup requires a remote Debian VM or physical target")
    if config.system_type not in {"server_lite", "server_web"}:
        raise ValueError("First-slice build-on targets require server_lite or server_web")
    if (config.custom_steps or config.is_build_server or config.install_go or config.install_node
            or config.install_node_versions or config.install_godot):
        raise ValueError("Build-on target setup cannot request builder roles, custom steps or build toolchains")


def _preflight_target(config: SetupConfig, endpoint: SSHEndpoint) -> None:
    """Read platform and unfinished-operation state without bootstrapping code."""
    script = (
        "import json,os,platform; "
        "v=platform.freedesktop_os_release(); "
        "m='/etc/basaltwater/build-target/.managed.json'; "
        "print(json.dumps({'uid':os.geteuid(),'os':v.get('ID'),'version':v.get('VERSION_ID'),"
        "'architecture':platform.machine(),'setup_pending':os.path.lexists("
        "'/var/lib/basaltwater/setup-operation.json'),'legacy_ssh':os.path.lexists("
        "'/var/lib/basaltwater/build-target-ssh'),'enrollment_schema':"
        "json.load(open(m)).get('schema') if os.path.lexists(m) else None}))"
    )
    command = build_ssh_command(
        config.host, "root", endpoint.identity_file, port=endpoint.port,
        remote_command=shlex.join(["python3", "-I", "-c", script]), batch_mode=True,
        known_hosts_path=endpoint.known_hosts_file, allow_host_key_prompt=False,
    )
    command[1:1] = ["-F", "/dev/null", "-o", "IdentitiesOnly=yes", "-o", "IdentityAgent=none",
                    "-o", "ForwardAgent=no", "-o", "ClearAllForwardings=yes"]
    result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, timeout=60, check=True)
    if len(result.stdout) > 4096:
        raise ValueError("Target platform response is oversized")
    value = json.loads(result.stdout)
    if (not isinstance(value, dict) or value.get("uid") != 0 or value.get("os") != "debian"
            or value.get("version") != "13" or value.get("architecture") != "x86_64"
            or value.get("setup_pending") is not False or value.get("legacy_ssh") is not False
            or value.get("enrollment_schema") not in {None, 2}):
        raise ValueError("Target must be Debian 13 x86-64 with no unfinished setup or legacy enrollment")


def _plans(config: SetupConfig, destination: str, capabilities: object,
           principal: str) -> list[Any]:
    sources = _prepare_sources(config.deploy_specs, destination)
    if not 1 <= len(sources) <= MAX_PLANS:
        raise ValueError(f"Build-on setup requires 1 to {MAX_PLANS} application destinations")
    plans = [prepare_delivery_plan(
        item.manifest, destination=item.destination, source=item.source,
        source_path=item.source_path, request_id=uuid.uuid4().hex,
        idempotency_key=uuid.uuid4().hex, target_enrollment=config.build_target_enrollment,
        target_principal=principal, builder_capabilities=capabilities,
    ) for item in sources]
    if len({item.deployment["application"] for item in plans}) != len(plans):
        raise ValueError("Build-on setup selects the same target application more than once")
    return plans


def _bootstrap_config(config: SetupConfig) -> SetupConfig:
    return SetupConfig(
        host=config.host, username=config.username, system_type="custom_steps",
        machine_type=config.machine_type, custom_steps="bootstrap_build_target",
        build_target_enrollment=config.build_target_enrollment, ssh_key=config.ssh_key,
    )


def _profile_config(config: SetupConfig) -> SetupConfig:
    profile = copy.deepcopy(config)
    profile.build_on = None
    profile.clear_build_on = False
    profile.artifact_deployments = True
    # Bootstrap already applied the explicit enrollment. The final profile owns
    # target maintenance throughout execution, so it must not reenroll inside it.
    profile.build_target_enrollment = None
    profile.disable_build_target = False
    return profile


def run_build_setup(config: SetupConfig, *, apply_setup: Callable[..., int]) -> int:
    """Run under the caller's setup-target lock; selected builders never fall back.

    Preparation and read-only probes precede bootstrap. Any uncertain mutating
    outcome retains a host-scoped durable guard, sources and all exact inputs.
    """
    _validate_selection(config)
    selected = resolve_saved_builder(config.build_on)
    endpoint = resolve_target_endpoint(config)
    with ExitStack() as stack:
        builder = stack.enter_context(BuildHostClient(**selected.endpoint.connection_kwargs(), timeout=900))
        target = stack.enter_context(BuildTargetClient(**endpoint.connection_kwargs(), timeout=900))
        capabilities = builder.capabilities()
        _preflight_target(config, endpoint)
        if config.dry_run:
            with tempfile.TemporaryDirectory(prefix="basaltwater-build-preflight-") as temporary:
                plans = _plans(config, temporary, capabilities, endpoint.principal)
                descriptor = _open_private_directory(temporary)
                try:
                    _check_workspace_capacity(descriptor, plans)
                finally:
                    os.close(descriptor)
                for plan in plans:
                    print(f"  [DRY-RUN] Build {plan.request['source']['revision']} on {selected.config.host} "
                          f"and deliver {plan.deployment['application']} to {config.host}")
                print("  [DRY-RUN] Would bootstrap enrollment, reserve all, build/stage all, then apply saved profile")
            return 0

        workspace = get_workspace_dir()
        _private_directory(workspace)
        root = os.path.join(workspace, "build-setups")
        _private_directory(root)
        name = hashlib.sha256(config.host.lower().rstrip(".").encode()).hexdigest()
        host_root = os.path.join(root, name)
        _private_directory(host_root)
        journal = os.path.join(host_root, "setup.json")
        with OperationStateStore(journal).ownership() as store:
            if store.load() is not None:
                raise BuildSetupRecoveryRequired(journal)
            with os.scandir(host_root) as entries:
                attempts = sum(entry.is_dir(follow_symlinks=False) for entry in entries)
            if attempts >= MAX_RETAINED_ATTEMPTS:
                raise ValueError("Build-on setup retention is full; reconcile retained attempts before continuing")
            attempt = os.path.join(host_root, uuid.uuid4().hex)
            _private_directory(attempt)
            source_root = os.path.join(attempt, "sources")
            _private_directory(source_root)
            plans = _plans(config, source_root, capabilities, endpoint.principal)
            descriptor = _open_private_directory(attempt)
            try:
                _check_workspace_capacity(descriptor, plans)
            finally:
                os.close(descriptor)
            prepared = {"schema": 1, "plans": [asdict(plan) for plan in plans]}
            encode_document(prepared, maximum=MAX_PREPARED_BYTES)
            write_json_atomic(os.path.join(attempt, "prepared.json"), prepared, mode=0o600)
            context: dict[str, Any] = {
                "schema": 1, "attempt": attempt, "host": config.host,
                "builder": selected.config.host,
                "profile_sha256": hashlib.sha256(json.dumps(
                    config.to_remote_args(), separators=(",", ":"), ensure_ascii=True,
                ).encode()).hexdigest(),
                "prepared_sha256": hashlib.sha256(encode_document(prepared, maximum=MAX_PREPARED_BYTES)).hexdigest(),
            }
            operation = store.begin("build_setup", config.host, "bootstrapping", context=context)
            try:
                if apply_setup(_bootstrap_config(config)) != 0:
                    raise RuntimeError("Target enrollment bootstrap did not finish")
                store.transition(operation.operation_id, "delivering")
                delivery_root = os.path.join(attempt, "delivery")
                _private_directory(delivery_root)
                delivery = BuildDeliveryController(builder=builder, target=target, workspace=delivery_root).deliver(plans)
                expected = [{key: getattr(item, key) for key in (
                    "application", "operation_id", "generation", "artifact_sha256",
                )} for item in delivery.applications]
                context.update(delivery_receipt=delivery.receipt_path, expected_releases=expected)
                store.transition(operation.operation_id, "profile_applying", context=context)
                if apply_setup(_profile_config(config), expected_releases=expected) != 0:
                    raise RuntimeError("Final artifact profile setup did not finish")
                store.complete(operation.operation_id)
                print(f"  Remote-build setup complete; retained evidence: {attempt}")
                return 0
            except BaseException:
                try:
                    store.transition(operation.operation_id, "recovery_required", status="recovery_required", context=context)
                except BaseException:
                    pass
                raise BuildSetupRecoveryRequired(journal) from None
