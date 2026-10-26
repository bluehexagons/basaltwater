"""Trusted local setup replay of installed artifacts, under target exclusion."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import os
from typing import TYPE_CHECKING, Any

from common.service_tools.build_target import load_config, require_platform
from lib.build_admission import DeploymentAdmissionStore
from lib.build_target_activation import TargetReleaseActivator, parse_expected_releases
from lib.build_target_artifacts import BuildTargetArtifactStore
from lib.build_target_maintenance import target_maintenance
from lib.machine_state import can_manage_system_services
from lib.remote_utils import is_dry_run

if TYPE_CHECKING:
    from lib.config import SetupConfig


_REFUSAL_GUIDANCE = (
    "Retained artifact reconciliation refused. Inspect protected target evidence "
    "and enrollment; retry after any active target operation finishes. "
    "Use deployment from the controller to bootstrap, deliver missing retained "
    "artifacts/descriptors, or replace superseded final-setup expectations; "
    "preserve recovery guards for operator reconciliation."
)


@dataclass
class _RefreshContext:
    config: SetupConfig
    expected_releases: list[dict[str, Any]] | None
    activator: TargetReleaseActivator
    admissions: DeploymentAdmissionStore | None
    active: bool = True


_ACTIVE_SETUP: ContextVar[_RefreshContext | None] = ContextVar("expected_artifact_setup", default=None)


def validate_artifact_refresh(config: SetupConfig) -> None:
    """Reject setup route policies outside the retained activation contract."""
    if config.enable_cloudflare or config.enable_ssl:
        raise ValueError(
            "Retained artifact reconciliation does not support Cloudflare or SSL setup; "
            "use the supported target routing policy from the controller"
        )


def _require_refresh_platform(config: SetupConfig) -> None:
    if os.geteuid() != 0:
        raise PermissionError("Retained artifact reconciliation requires root")
    if (config.machine_type not in {"vm", "hardware"}
            or not can_manage_system_services(config.machine_type)):
        raise RuntimeError("Retained artifact reconciliation requires VM or physical system services")
    require_platform()


def _compose_refresh(config: SetupConfig, installed: dict, expected: list[dict[str, Any]] | None) -> _RefreshContext:
    roots = installed["roots"]
    artifacts = BuildTargetArtifactStore(
        roots["artifacts"], max_records=installed["max_records"],
        max_bytes=installed["max_bytes"],
    )
    activator = TargetReleaseActivator(
        roots["releases"], roots["verify"], open_artifact=artifacts.open_verified_bundle,
    )
    admissions = (DeploymentAdmissionStore(os.path.join(roots["admission"], "admissions.json"))
                  if expected is not None else None)
    return _RefreshContext(config, expected, activator, admissions)


@contextmanager
def expected_artifact_setup(config: SetupConfig, expected_releases: object) -> Iterator[None]:
    """Guard final setup before its first profile mutation, through response.

    Enter before the setup operation marker. This checks installed identities
    and current completed admissions without artifact preflight or writes, then
    holds exclusive maintenance across the caller's steps. Enrollment must be
    omitted inside this scope. Reconciliation inherits these expectations when
    called with this same config object; neither config nor registry stores them.
    """
    validate_artifact_refresh(config)
    if config.dry_run or is_dry_run():
        yield
        return
    _require_refresh_platform(config)
    with ExitStack() as ownership:
        try:
            if _ACTIVE_SETUP.get() is not None:
                raise ValueError("Expected artifact setup scopes cannot be nested")
            ownership.enter_context(target_maintenance())
            expected = parse_expected_releases(expected_releases)
            if expected is None:
                raise ValueError("Expected controller releases are required for guarded setup")
            context = _compose_refresh(config, load_config(), expected)
            context.activator.check_expected_releases(expected_releases=expected, admissions=context.admissions)
        except (OSError, ValueError, RuntimeError) as exc:
            raise RuntimeError(_REFUSAL_GUIDANCE) from exc
        token = _ACTIVE_SETUP.set(context)
        try:
            yield
        finally:
            context.active = False
            _ACTIVE_SETUP.reset(token)


def _reconcile(context: _RefreshContext) -> list[dict[str, Any]]:
    if context.expected_releases is None:
        receipts = context.activator.reconcile_installed()
    else:
        receipts = context.activator.reconcile_installed(
            expected_releases=context.expected_releases, admissions=context.admissions,
        )
    print(f"  Reconciled {len(receipts)} retained target artifact release(s)")
    return receipts


def reconcile_artifact_deployments(config: SetupConfig, *, expected_releases: object = None) -> list[dict[str, Any]]:
    """Reapply retained releases without source, builds or new admission intent.

    The maintenance lease protects the first configuration read through the
    complete reconciliation and local response. Reuse only a live guarded setup
    scope for this config object; otherwise acquire independent maintenance.
    """
    validate_artifact_refresh(config)
    if config.dry_run or is_dry_run():
        print("  [DRY-RUN] Would reconcile retained target artifacts; missing inputs require controller deployment")
        return []
    _require_refresh_platform(config)
    try:
        active = _ACTIVE_SETUP.get()
        if active is not None:
            if (not active.active or active.config is not config
                    or (expected_releases is not None
                        and parse_expected_releases(expected_releases) != active.expected_releases)):
                raise ValueError("Reconciliation does not match the active expected artifact setup scope")
            return _reconcile(active)
        with target_maintenance():
            expected = parse_expected_releases(expected_releases)
            return _reconcile(_compose_refresh(config, load_config(), expected))
    except (OSError, ValueError, RuntimeError) as exc:
        raise RuntimeError(_REFUSAL_GUIDANCE) from exc
