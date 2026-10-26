"""Trusted local setup replay of installed artifacts, under target exclusion."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from common.service_tools.build_target import load_config, require_platform
from lib.build_target_activation import TargetReleaseActivator
from lib.build_target_artifacts import BuildTargetArtifactStore
from lib.build_target_maintenance import target_maintenance
from lib.machine_state import can_manage_system_services
from lib.remote_utils import is_dry_run

if TYPE_CHECKING:
    from lib.config import SetupConfig


def validate_artifact_refresh(config: SetupConfig) -> None:
    """Reject setup route policies outside the retained activation contract."""
    if config.enable_cloudflare or config.enable_ssl:
        raise ValueError(
            "Retained artifact reconciliation does not support Cloudflare or SSL setup; "
            "use the supported target routing policy from the controller"
        )


def reconcile_artifact_deployments(config: SetupConfig) -> list[dict[str, Any]]:
    """Reapply retained releases without source, builds or new admission intent.

    The fixed maintenance lease protects the first configuration read through
    the complete reconciliation and local response. Enrollment and initial
    artifact delivery belong to the controller, never this setup step.
    """
    validate_artifact_refresh(config)
    if config.dry_run or is_dry_run():
        print("  [DRY-RUN] Would reconcile retained target artifacts; missing inputs require controller deployment")
        return []
    if os.geteuid() != 0:
        raise PermissionError("Retained artifact reconciliation requires root")
    if (config.machine_type not in {"vm", "hardware"}
            or not can_manage_system_services(config.machine_type)):
        raise RuntimeError("Retained artifact reconciliation requires VM or physical system services")
    require_platform()
    try:
        with target_maintenance():
            installed = load_config()
            roots = installed["roots"]
            artifacts = BuildTargetArtifactStore(
                roots["artifacts"], max_records=installed["max_records"],
                max_bytes=installed["max_bytes"],
            )
            activator = TargetReleaseActivator(
                roots["releases"], roots["verify"],
                open_artifact=artifacts.open_verified_bundle,
            )
            receipts = activator.reconcile_installed()
            print(f"  Reconciled {len(receipts)} retained target artifact release(s)")
            return receipts
    except (OSError, ValueError, RuntimeError) as exc:
        raise RuntimeError(
            "Retained artifact reconciliation refused. Inspect protected target evidence "
            "and enrollment; retry after any active target operation finishes. "
            "Use deployment from the controller to bootstrap or deliver missing retained "
            "artifacts/descriptors; preserve recovery guards for operator reconciliation."
        ) from exc
