"""Local role-aware collection using verified build retention primitives."""

from __future__ import annotations

import os
import time

from lib.managed_storage import filesystem, storage_lock
from lib.operation_state import OperationStateStore


def _pressure(path: str, allocated: int, budget: int, target: int) -> bool:
    return allocated > budget or filesystem(path)["free_bytes"] < target


def _payload_bytes(roots: list[str]) -> int:
    """Observe flat managed payload stores; never descend into scratch trees."""
    total = 0
    for root in roots:
        with os.scandir(root) as entries:
            for index, entry in enumerate(entries):
                if index >= 20000:
                    raise ValueError("Payload inventory exceeds its entry bound")
                info = entry.stat(follow_symlinks=False)
                total += info.st_blocks * 512
    return total


def maintain_target(*, apply: bool, budget: int, target: int, min_age: int,
                    now: float | None = None, limit: int = 32,
                    retry_operation: str | None = None, expected_revision: str | None = None) -> dict:
    """Own maintenance -> admission -> artifact locks through retirement.

    Current releases, newer desired operations and all ambiguous outcomes remain
    pinned. This local root action does not expose deletion to typed SSH users.
    """
    from common.service_tools.build_target import load_config
    from lib.build_admission import DeploymentAdmissionStore
    from lib.build_target_activation import TargetReleaseActivator
    from lib.build_target_artifacts import BuildTargetArtifactStore
    from lib.build_target_maintenance import target_maintenance

    moment = time.time() if now is None else now
    if (retry_operation is None) != (expected_revision is None) or retry_operation is not None and not apply:
        raise ValueError("An exact retirement retry requires apply, operation and revision")
    with storage_lock(cleanup=True), target_maintenance():
        from lib.build_conversion import _load as conversion_snapshot

        snapshot = conversion_snapshot()
        if snapshot is not None and snapshot["state"] != "completed":
            raise RuntimeError("Unfinished conversion protects target payloads")
        config = load_config()
        roots = config["roots"]
        for path in ("/var/lib/basaltwater/setup-operation.json", os.path.join(roots["releases"], "activation.json")):
            if OperationStateStore(path).load() is not None:
                raise RuntimeError("Unfinished setup or activation protects target payloads")
        artifacts = BuildTargetArtifactStore(roots["artifacts"], max_records=config["max_records"], max_bytes=config["max_bytes"])
        activator = TargetReleaseActivator(roots["releases"], roots["verify"], open_artifact=artifacts.open_verified_bundle)
        installed = activator._load(initialize=False)["applications"]
        if not installed:
            raise ValueError("No proven installed target releases; preserve retained payloads")
        admissions = DeploymentAdmissionStore(os.path.join(roots["admission"], "admissions.json"))
        # Public store methods may initialize an empty store. Maintenance must
        # only inspect an already initialized role, including in preview mode.
        for path in (admissions.path, admissions.path + ".lock",
                     os.path.join(roots["artifacts"], ".build-target-artifacts.lock"),
                     os.path.join(roots["artifacts"], ".build-target-artifacts.initialized")):
            if not os.path.lexists(path):
                raise ValueError("Missing initialized target storage evidence")
        before = filesystem(roots["artifacts"])
        results = []
        with admissions._locked(initialize=False) as state:
            if any(item["state"] in {"uncertain", "activating"} for item in state["operations"].values()):
                raise RuntimeError("Uncertain admission protects target payloads")
            for app, current in installed.items():
                evidence = state["operations"].get(current["operation_id"])
                if (evidence is None or evidence["state"] != "completed" or evidence["application"] != app
                        or evidence["generation"] != current["generation"]
                        or evidence["artifact_sha256"] != current["artifact_sha256"]):
                    raise ValueError("Installed release lacks current completed admission proof")
            with artifacts._locked(initialize=False) as root_fd:
                records, charged, metadata = artifacts._scan(root_fd)
                for current in installed.values():
                    retained = records.get(current["operation_id"])
                    if (retained is None or retained[2] != {".intent.json", ".ready", ".bundle"}
                            or retained[0]["artifact_sha256"] != current["artifact_sha256"]):
                        raise ValueError("Current installed bundle must remain available")
                    artifacts._verify_file(root_fd, retained[0], ".bundle")
            allocated = _payload_bytes([roots["artifacts"]])
            for operation, (record, _, suffixes) in sorted(records.items(), key=lambda item: item[1][0]["generation"]):
                if retry_operation is not None and operation != retry_operation:
                    continue
                current = installed.get(record["application"])
                admission = state["operations"].get(operation)
                reason = None
                if ".retired" in suffixes and retry_operation is None:
                    continue
                if (current is None or current["generation"] <= record["generation"]
                        or operation == current["operation_id"]):
                    reason = "current_or_unproven_release"
                elif (admission is None or admission["state"] != "completed"
                      or admission["artifact_sha256"] != record["artifact_sha256"]
                      or admission["request_sha256"] != record["request_sha256"]
                      or any(admission[key] != record[key] for key in (
                          "owner", "application", "generation", "bindings_sha256", "request_id"))):
                    reason = "incomplete_admission"
                elif moment - max(admission["created_at"], state["operations"][current["operation_id"]]["created_at"]) < min_age:
                    reason = "retention_age"
                elif retry_operation is None and not _pressure(roots["artifacts"], allocated, budget, target):
                    reason = "below_limits"
                if reason:
                    results.append(dict(operation_id=operation, status="skipped", reason=reason))
                    continue
                if sum(row["status"] in {"eligible", "retired", "pending"} for row in results) >= limit:
                    results.append(dict(operation_id=operation, status="skipped", reason="pass_limit"))
                    continue
                plan = artifacts.retire_bundle(operation)
                if plan["status"] == "pending" and retry_operation is None:
                    results.append(plan | {"status": "skipped", "reason": "retirement_requires_exact_retry"})
                    continue
                if apply:
                    plan = artifacts.retire_bundle(operation, expected_revision=expected_revision or plan["revision"])
                    allocated = _payload_bytes([roots["artifacts"]])
                results.append(plan)
        after = filesystem(roots["artifacts"])
        return dict(role="target", before=before, after=after, candidates=results,
                    remaining_bytes=max(0, target - after["free_bytes"]),
                    remaining_budget_bytes=max(0, allocated - budget),
                    retained_records=len(records), record_limit=config["max_records"],
                    record_warning=len(records) * 5 >= config["max_records"] * 4,
                    logical_charge_before=charged, metadata_bytes=metadata)


def maintain_builder(*, apply: bool, budget: int, target: int, min_age: int,
                     release_bundles: bool = False, now: float | None = None, limit: int = 32) -> dict:
    """Select old terminal payloads; existing primitives revalidate every action."""
    from common.service_tools.build_host import load_config, retain_terminal
    from lib.build_jobs import BuildJobStore
    from lib.build_capacity import inspect_build_capacity

    config = load_config()
    roots = config["roots"]
    capacity = inspect_build_capacity(config)
    jobs, _ = BuildJobStore(os.path.join(roots["state"], "jobs.json")).capacity_snapshot()
    moment = time.time() if now is None else now
    paths = [roots[name] for name in ("inputs", "logs", "artifacts")]
    allocated = _payload_bytes(paths)
    before = [filesystem(path) for path in paths]
    results = []
    selected = sorted(jobs["jobs"].values(), key=lambda row: row["finished_at"] or moment)
    for job in selected[:4096]:
        identifier = job["request_id"]
        if (job["state"] not in {"succeeded", "failed", "cancelled", "expired"}
                or moment - job["finished_at"] < min_age):
            results.append(dict(request_id=identifier, status="skipped", reason="active_uncertain_or_retention_age"))
            continue
        if not any(_pressure(path, allocated, budget, target) for path in paths):
            results.append(dict(request_id=identifier, status="skipped", reason="below_limits"))
            continue
        if sum(row["status"] != "skipped" for row in results) >= limit:
            break
        try:
            plan = retain_terminal(config, request_id=identifier, expected_revision=None)
            if any(op["state"] == "pending" for op in plan["pruning"]):
                results.append(dict(request_id=identifier, status="skipped", reason="pending_retention_requires_exact_retry"))
                continue
            kinds = tuple(kind for kind in ("source", "log") if plan["availability"][kind] == "present")
            bundle = release_bundles and job["state"] == "succeeded" and plan["availability"]["bundle"] == "present"
            if not kinds and not bundle:
                results.append(dict(request_id=identifier, status="skipped", reason="no_eligible_payloads"))
                continue
            payloads = []
            for kind, root, suffix in (("source", "inputs", ".source"), ("log", "logs", ".log"),
                                       ("bundle", "artifacts", ".bundle")):
                if kind not in kinds and not (kind == "bundle" and bundle):
                    continue
                path = os.path.join(roots[root], identifier + suffix)
                info = os.stat(path, follow_symlinks=False)
                payloads.append(dict(path=path, category=kind, owner_uid=info.st_uid,
                                     logical_bytes=info.st_size, allocated_bytes=info.st_blocks * 512))
            if apply:
                if not plan["settled"]:
                    plan = retain_terminal(config, request_id=identifier, expected_revision=plan["revision"])
                if kinds:
                    plan = retain_terminal(config, request_id=identifier, expected_revision=plan["revision"], prune_kinds=kinds)
                if bundle:
                    plan = retain_terminal(config, request_id=identifier, expected_revision=plan["revision"], release_bundle=True)
                allocated = _payload_bytes(paths)
            results.append(dict(request_id=identifier, status="maintained" if apply else "eligible", retention=plan,
                                payloads=payloads, rebuild_cost="New request/build required; old retrieval ends for selected payloads"))
        except (OSError, ValueError, RuntimeError):
            results.append(dict(request_id=identifier, status="skipped", reason="busy_or_unsafe_retention"))
    after = [filesystem(path) for path in paths]
    return dict(role="builder", before=before, after=after, candidates=results,
                remaining_bytes=max(max(0, target - row["free_bytes"]) for row in after),
                remaining_budget_bytes=max(0, allocated - budget), capacity=capacity,
                record_warning=capacity["inputs"]["slot_headroom"] <= max(1, config["max_records"] // 5))


def maintain_roles(**policy) -> list[dict]:
    """Discover installed roles only; omitted roles do not need their tooling."""
    if os.geteuid() != 0 or "SSH_ORIGINAL_COMMAND" in os.environ:
        raise PermissionError("Payload maintenance requires a local administrative session")
    if any(type(policy[key]) is not int or policy[key] < 0 for key in ("budget", "target", "min_age")):
        raise ValueError("Invalid payload policy")
    results = []
    for role, function in (("target", maintain_target), ("builder", maintain_builder)):
        if not os.path.lexists(f"/etc/basaltwater/build-{role if role == 'target' else 'host'}/config.json"):
            continue
        options = dict(policy)
        if role == "target":
            options.pop("release_bundles", None)
        try:
            results.append(function(**options))
        except (OSError, ValueError, RuntimeError):
            results.append(dict(role=role, status="deferred_or_failed", reason="busy_unfinished_or_unsafe_evidence"))
    return results


def main() -> int:
    """Explicit local recovery of a recorded target bundle retirement."""
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retry-target", required=True)
    parser.add_argument("--expected-revision", required=True)
    args = parser.parse_args()
    if os.geteuid() != 0 or "SSH_ORIGINAL_COMMAND" in os.environ:
        parser.error("Requires a local administrative session")
    try:
        result = maintain_target(apply=True, budget=0, target=0, min_age=0,
                                 retry_operation=args.retry_target, expected_revision=args.expected_revision)
        print(json.dumps(result))
        return 0 if any(row["status"] == "retired" for row in result["candidates"]) else 2
    except (OSError, ValueError, RuntimeError):
        print(json.dumps(dict(status="refused", reason="busy_unfinished_changed_or_unsafe_evidence")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
