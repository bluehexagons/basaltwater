"""Run a declared check against two pinned revisions with one settings snapshot."""

from __future__ import annotations

import argparse
import os
import subprocess
import uuid

from lib import agent_workspace
from lib.agent_reservations import termination_cleanup
from lib.agent_visual_check import check_recipe
from lib.agent_visuals import _directory, _settings, _settings_unchanged, resolve_revision_pair
from lib.atomic_io import write_json_atomic
from lib.godot_qa_prepare import bundle_path
from lib.remote_utils import is_dry_run


def _case_differences(before: dict, after: dict, *, comparable: bool) -> list[dict]:
    outcomes = {
        ("passed", "passed"): "passed",
        ("failed", "failed"): "persisted",
        ("passed", "failed"): "regressed",
        ("failed", "passed"): "fixed",
        ("skipped", "skipped"): "skipped",
    } if comparable else {}
    baseline = {case["id"]: case["status"] for case in before.get("cases", [])}
    candidate = {case["id"]: case["status"] for case in after.get("cases", [])}
    return [{"id": case, "before": baseline.get(case), "after": candidate.get(case),
             "outcome": outcomes.get((baseline.get(case), candidate.get(case)), "unverified")}
            for case in dict.fromkeys([*baseline, *candidate])]


def _comparable(result: dict, *, assertions_only: bool) -> bool:
    cases = result.get("case_results", {})
    if cases.get("status") != "complete" or "renderer_error" in result:
        return False
    # A complete assertion failure is valid comparison evidence. With extra
    # gates, only accept a check that actually passed those gates.
    return result["ok"] or (
        assertions_only and isinstance(result["returncode"], int) and result["returncode"] > 0
        and result.get("failure_stage") == "execution"
        and any(case["status"] == "failed" for case in cases["cases"])
    )


@termination_cleanup()
def check_revisions(args: argparse.Namespace) -> dict:
    if is_dry_run():
        raise ValueError("A revision check requires execution; use desktop develop compare --dry-run to inspect its plan")
    if type(args.timeout) is not int or not 1 <= args.timeout <= 3600:
        raise ValueError("Visual check timeout must be from 1 through 3600 seconds")
    bundle = bundle_path(getattr(args, "asset_bundle", None), prepare=getattr(args, "prepare", False))
    repository, commits = resolve_revision_pair(args.repository, args.before, args.after)
    settings = _settings(args.settings)
    run_id = "visual-check-" + uuid.uuid4().hex[:12]
    directory = _directory(args.output, run_id)
    settings_path = os.path.join(directory, "settings.json")
    write_json_atomic(settings_path, settings, mode=0o400, indent=None)
    report_path = os.path.join(directory, "comparison.json")
    report = {"schema_version": 1, "ok": False, "status": "running", "recipe": args.recipe,
              "comparison_report": report_path, "settings": settings_path, "directory": directory,
              "checks": {}, "cases": []}
    checkpoints = {}
    baseline_recipe = None
    assertions_only = not (args.verify_evidence or args.require_artifact_kind or args.review_manifest)
    try:
        for side, commit in commits.items():
            output = os.path.join(directory, side)
            record = {"revision": commit, "requested_revision": getattr(args, side), "status": "creating-worktree",
                      "check_report": os.path.join(output, "check.json"), "log": os.path.join(output, "check.log")}
            report["checks"][side] = record
            if side == "after" and baseline_recipe is None:
                record.update(status="not-run", comparable=False, error="Baseline recipe declaration was unavailable")
                continue
            write_json_atomic(report_path, report)
            try:
                worktree = agent_workspace.create_agent_worktree(repository, f"{run_id}-{side}", base=commit)
                record.update(worktree=worktree["path"], status="running")
                write_json_atomic(report_path, report)
                result = check_recipe(argparse.Namespace(
                    **{**vars(args), "repository": worktree["path"], "settings": settings_path,
                       "output": output, "asset_bundle": str(bundle) if bundle is not None else None},
                ), expected_revision=commit, settings_snapshot=settings, baseline_recipe=baseline_recipe)
                if side == "before":
                    baseline_recipe = result["recipe_definition"]
                checkpoints[side] = result.get("case_results", {})
                record.update(status=result["status"], returncode=result["returncode"],
                              comparable=_comparable(result, assertions_only=assertions_only))
                for field in ("error", "renderer_error", "preparation", "recipe_differences"):
                    if field in result:
                        record[field] = result[field]
                if result["status"] == "interrupted":
                    raise KeyboardInterrupt
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
                record.update(status="failed", comparable=False, error=str(exc))
            finally:
                write_json_atomic(report_path, report)
            if not _settings_unchanged(settings_path, settings):
                raise RuntimeError("The recipe changed the shared settings; comparison is inconsistent")
        comparable = all(record["comparable"] for record in report["checks"].values())
        report["cases"] = _case_differences(
            checkpoints.get("before", {}), checkpoints.get("after", {}), comparable=comparable,
        )
        outcomes = {case["outcome"] for case in report["cases"]}
        if not comparable or "unverified" in outcomes or not outcomes - {"skipped"}:
            report.update(status="incomplete", error="Case comparison is incomplete; inspect each check report")
        elif "regressed" in outcomes:
            report.update(status="regressed", error="One or more cases regressed")
        else:
            report.update(ok=True, status="passed")
    except KeyboardInterrupt:
        report.update(status="interrupted", error="Revision check interrupted")
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        report.update(status="incomplete", error=str(exc))
    finally:
        if not report["cases"]:
            report["cases"] = _case_differences(checkpoints.get("before", {}), checkpoints.get("after", {}), comparable=False)
        write_json_atomic(report_path, report)
    return report
