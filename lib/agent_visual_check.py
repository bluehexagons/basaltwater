"""Explicit project rendering checks with private, repeatable environment evidence."""

from __future__ import annotations

import argparse
from contextlib import nullcontext
from datetime import datetime, timezone
import os
from pathlib import Path
import platform
import shutil
import subprocess
import time
import uuid

from lib.agent_environment import inspect_environment
from lib.agent_graphics import graphics_environment
from lib.agent_probe import run_probe
from lib.agent_reservations import reserve, resource_keys, termination_cleanup
from lib.agent_visuals import _directory, _settings, _settings_unchanged, validate_review_manifest
from lib.atomic_io import read_json_file, write_json_atomic
from lib.godot_qa_prepare import bundle_path, prepare_godot
from lib.remote_utils import is_dry_run, run
from lib.streamed_process import run_streamed
from lib.validation import validate_filesystem_path, validate_no_control_characters
from lib.visual_case_results import DEVICE_LOST, read_case_checkpoint, saved_captures


ARCH_GRAPHICS_PACKAGES = (
    "mesa", "libglvnd", "vulkan-intel", "vulkan-radeon", "nvidia-utils",
    "sdl3", "sdl3_image", "mesa-utils", "xorg-server-xvfb", "xorg-xauth",
)
DEBIAN_GRAPHICS_PACKAGES = (
    "libgl1-mesa-dri", "libegl-mesa0", "libglx-mesa0", "libglvnd0",
    "libgl1", "libegl1", "mesa-vulkan-drivers", "libvulkan1",
    "nvidia-driver", "libnvidia-glcore", "libsdl2-2.0-0", "libsdl2-image-2.0-0",
    "libsdl3-0", "libsdl3-image0", "mesa-utils", "xvfb", "xauth",
)
LOG_LIMIT = 16 * 1024 * 1024


def host_context(environment: dict[str, str]) -> dict:
    """Record bounded host facts without launching renderers or tool shims."""
    context = {
        "system": platform.system(), "kernel": platform.release(),
        "architecture": platform.machine(),
        "graphics_environment": graphics_environment(environment),
        "graphics_packages": {}, "package_observation": "unavailable",
    }
    try:
        release = platform.freedesktop_os_release()
        context["distribution"] = {key: release[key] for key in ("ID", "VERSION_ID") if key in release}
    except OSError:
        release = {}
        context["distribution"] = {}
    families = {release.get("ID", ""), *release.get("ID_LIKE", "").split()}
    command = None
    if families & {"debian", "ubuntu"}:
        manager = shutil.which("dpkg-query")
        packages = DEBIAN_GRAPHICS_PACKAGES
        if manager:
            command = [manager, "--show", "--showformat=${binary:Package}\t${Version}\t${db:Status-Status}\n", *packages]
    elif families & {"arch", "cachyos"}:
        manager = shutil.which("pacman")
        packages = ARCH_GRAPHICS_PACKAGES
        if manager:
            command = [manager, "-Q", *packages]
    if command:
        try:
            result = run(
                command, check=False, capture_output=True,
                timeout=10, input_data="",
                env={**os.environ, "LC_ALL": "C"},
            )
            if result.returncode not in (0, 1):
                return context
            # Missing optional packages return 1. Dpkg also knows removed or
            # unpacked packages, so require installed status. Preserve multiarch
            # qualifiers so two installed architectures do not overwrite evidence.
            for line in (result.stdout or "").splitlines():
                if families & {"debian", "ubuntu"}:
                    fields = line.split("\t")
                    if len(fields) != 3 or fields[2] != "installed":
                        continue
                    name, version = fields[:2]
                else:
                    name, separator, version = line.partition(" ")
                    if not separator:
                        continue
                if name.partition(":")[0] in packages and 0 < len(version) <= 256:
                    context["graphics_packages"][name] = version
            context["package_observation"] = "observed" if result.returncode == 0 else "partial"
        except (OSError, RuntimeError, subprocess.SubprocessError):
            pass  # Package metadata is optional; the project check owns its gate.
    return context


def _check_tool_version(selection: dict[str, str], cwd: str, environment: dict[str, str]) -> dict:
    executable = selection["executable"]
    expected = selection["expected"]
    if "/" in executable:
        path = shutil.which(str(Path(cwd) / executable))
    else:
        # Match lookup in the recipe's directory even when PATH has relative entries.
        search_path = os.pathsep.join(os.path.join(cwd, entry) for entry in os.get_exec_path(environment))
        path = shutil.which(executable, path=search_path)
    evidence = {**selection, "path": path, "observed": None, "status": "missing"}
    if path is None:
        return evidence
    result = run_probe([path, "--version"], cwd=cwd, env=environment, timeout=5, max_bytes=4096)
    if result.issue:
        return {**evidence, "status": "probe-failed", "issue": result.issue}
    observed = result.output.partition("\n")[0].strip()
    try:
        validate_no_control_characters(observed, "Observed version line")
    except ValueError:
        observed = ""
    if not 1 <= len(observed) <= 256:
        return {**evidence, "status": "probe-failed", "issue": "invalid_version_line"}
    return {**evidence, "observed": observed, "status": "verified" if observed == expected else "unexpected-version"}


@termination_cleanup()
def check_recipe(args: argparse.Namespace, *, expected_revision: str | None = None,
                 settings_snapshot: dict | None = None) -> dict:
    """Execute one reviewed declaration and retain the requested evidence."""
    if type(args.timeout) is not int or not 1 <= args.timeout <= 3600:
        raise ValueError("Visual check timeout must be from 1 through 3600 seconds")
    if is_dry_run():
        raise ValueError("A visual check requires execution; dry-run cannot verify rendering")
    if args.review_manifest is not None:
        validate_review_manifest(args.review_manifest)
    prepare = getattr(args, "prepare", False)
    bundle = bundle_path(getattr(args, "asset_bundle", None), prepare=prepare)
    settings = _settings(args.settings) if settings_snapshot is None else settings_snapshot
    manifest = inspect_environment(args.repository)
    if expected_revision is not None and (manifest["workspace"].get("commit") != expected_revision
                                          or manifest["workspace"].get("dirty") is not False):
        raise RuntimeError("Revision check requires the clean, pinned worktree")
    recipe = manifest["recipes"].get(args.recipe)
    if recipe is None:
        raise ValueError(f"Declare recipe {args.recipe!r} in basaltwater-agent.json before running it")
    if recipe["missing_tools"]:
        raise ValueError("Missing recipe tools: " + ", ".join(recipe["missing_tools"]))
    cwd = os.path.normpath(os.path.join(manifest["workspace"]["repository"], recipe["directory"]))
    validate_filesystem_path(cwd, must_exist=True)
    if not os.path.isdir(cwd):
        raise ValueError("Recipe working directory must be a directory")
    directory = _directory(args.output, "check-" + uuid.uuid4().hex[:12])
    report_path = os.path.join(directory, "check.json")
    log_path = os.path.join(directory, "check.log")
    environment_path = os.path.join(directory, "environment.json")
    settings_path = os.path.join(directory, "settings.json")
    environment = os.environ.copy()
    # Project scripts may use these paths; literal recipe argv is never expanded.
    environment["BASALTWATER_VISUAL_EVIDENCE"] = directory
    environment["BASALTWATER_VISUAL_SETTINGS"] = settings_path
    receipt_path = os.path.join(directory, "receipt.json")
    revision = manifest["workspace"].get("commit")
    environment["BASALTWATER_VISUAL_REVISION"] = revision or ""
    environment["BASALTWATER_VISUAL_RECEIPT"] = receipt_path
    environment["BASALTWATER_VISUAL_VERIFY"] = "1" if args.verify_evidence or args.require_artifact_kind else "0"
    case_path = Path(directory) / "basaltwater-case-checkpoint.json"
    environment["BASALTWATER_VISUAL_CASES"] = str(case_path)
    write_json_atomic(settings_path, settings, mode=0o400, indent=None)
    report = {
        "schema_version": 1, "ok": False, "status": "running", "recipe": args.recipe,
        "description": recipe["description"], "command": recipe["argv"], "cwd": cwd,
        "directory": directory, "report": report_path, "log": log_path,
        "environment": environment_path, "settings": settings_path,
        "started_at": datetime.now(timezone.utc).isoformat(), "timeout": args.timeout,
        "returncode": None, "revision": revision, "verification_scope": "project-recipe-exit-status",
        "gpu_readiness": "unverified", "ui_readiness": "unverified",
        "verification": {"status": "unverified"},
        "log_limit_bytes": LOG_LIMIT, "log_truncated": False,
    }
    if args.review_manifest is not None:
        report["review"] = {"manifest": os.path.join(directory, args.review_manifest)}
    if prepare:
        report["preparation"] = {"report": os.path.join(directory, "prepare.json"), "log": log_path}
    performance = None
    profile = getattr(args, "power_profile", None)
    write_json_atomic(report_path, report)
    started = time.monotonic()
    phase = "preflight"
    device_lost = False
    try:
        snapshot = {"manifest": manifest, "host": host_context(environment)}
        write_json_atomic(environment_path, snapshot)
        if selection := recipe.get("version_check"):
            environment.pop("BASALTWATER_VISUAL_TOOL", None)
            version = _check_tool_version(selection, cwd, environment)
            report["tool_version"] = version
            if version["status"] != "verified":
                detail = version["observed"] or version.get("issue") or "executable not found"
                raise RuntimeError(f"Recipe tool {version['executable']!r} preflight {version['status']}: "
                                   f"expected {version['expected']!r}, found {detail!r}")
            environment["BASALTWATER_VISUAL_TOOL"] = version["path"]
            write_json_atomic(report_path, report)
        argv = recipe["argv"]
        if getattr(args, "performance_session", False) or profile:
            from lib.agent_performance import performance_argv, performance_snapshot

            performance = {"schema_version": 1, "requested_profile": profile, "before": None, "after": None,
                           "active_observation": os.path.join(directory, "performance-active.json"),
                           "scope": "point-in-time-snapshots; no peak or exclusivity assertion"}
            report["performance"] = os.path.join(directory, "performance.json")
            performance["before"] = performance_snapshot()
            write_json_atomic(report["performance"], performance)
            argv = performance_argv(argv, directory, args.timeout, profile)
            report["execution_command"] = argv
        descriptor = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb", buffering=0) as log:
            written = 0

            def record_output(chunk: str) -> None:
                nonlocal written, device_lost
                if not device_lost and DEVICE_LOST.search(chunk):
                    device_lost = True
                encoded = chunk.encode("utf-8", errors="replace")
                retained = encoded[:max(0, LOG_LIMIT - written)]
                log.write(retained)
                written += len(retained)
                if len(retained) < len(encoded):
                    report["log_truncated"] = True

            repository = Path(manifest["workspace"]["repository"])
            reservation = nullcontext(({}, ()))
            if prepare:
                phase = "preparation"
                reservation = reserve(resource_keys([], [str(repository)]), owner="Godot QA check", run_id=uuid.uuid4().hex)
            with reservation as (_, descriptors):
                if prepare:
                    preparation = prepare_godot(
                        repository, recipe.get("godot_prepare"), bundle,
                        report_path=report["preparation"]["report"], timeout=args.timeout,
                        environment=environment, on_output=record_output, descriptors=descriptors,
                    )
                    report["preparation"]["status"] = preparation["status"]
                    write_json_atomic(report_path, report)
                phase = "execution"
                returncode = run_streamed(
                    argv, cwd=cwd, env=environment, timeout=args.timeout, on_output=record_output, pass_fds=descriptors,
                    cleanup_group_on_exit=prepare or expected_revision is not None or performance is not None,
                )
        report["returncode"] = returncode
        phase = "verification"
        if not _settings_unchanged(settings_path, settings):
            raise RuntimeError("Project recipe changed the recorded settings; refusing inconsistent evidence")
        if returncode != 0:
            phase = "execution"
            raise RuntimeError(f"Project recipe exited with status {returncode}; inspect {log_path}")
        if performance is not None:
            active = read_json_file(performance["active_observation"], max_bytes=2 * 1024 * 1024)
            if (not isinstance(active, dict) or active.get("schema_version") != 1
                    or (profile and active.get("power_profile") != profile)):
                raise ValueError("Performance session did not retain the expected active observation")
        if args.verify_evidence or args.require_artifact_kind:
            from lib.visual_evidence import verify_receipt

            report["verification"] = verify_receipt(receipt_path, manifest["workspace"]["repository"], revision, settings, args.require_artifact_kind)
            report["verification_scope"] = "project-recipe-and-checked-receipt"
        report.update(ok=True, status="passed")
    except TimeoutError:
        operation = "Headless Godot import" if phase == "preparation" else "Project recipe"
        report.update(status="timed-out", error=f"{operation} timed out after {args.timeout} seconds; inspect {log_path}")
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        report.update(status="failed", error=str(exc))
    except KeyboardInterrupt:
        report.update(status="interrupted", error="Project visual check interrupted")
    if not report["ok"]:
        report["failure_stage"] = phase
    if performance is not None:
        from lib.agent_performance import performance_snapshot

        try:
            performance["after"] = performance_snapshot()
            write_json_atomic(report["performance"], performance)
        except (OSError, ValueError, RuntimeError) as exc:
            report.update(ok=False, status="failed", failure_stage="verification", error=f"Performance evidence failed: {exc}")
        except KeyboardInterrupt:
            report.update(ok=False, status="interrupted", failure_stage="verification", error="Performance evidence collection interrupted")
    if device_lost:
        report["renderer_error"] = {"code": "vulkan-device-lost", "source": "log",
                                    "message": "Vulkan reported device loss; inspect the check log"}
    try:
        cases = read_case_checkpoint(case_path)
    except FileNotFoundError as exc:
        # A missing checkpoint is normal for recipes that only report an exit status.
        if case_path.exists() or case_path.is_symlink():
            report["case_results"] = {"status": "invalid", "path": str(case_path), "error": str(exc)}
    except (OSError, ValueError) as exc:
        report["case_results"] = {"status": "invalid", "path": str(case_path), "error": str(exc)}
    else:
        if renderer_error := cases.pop("renderer_error", None):
            report["renderer_error"] = renderer_error
        report["case_results"] = {"status": "complete" if cases["complete"] else "partial", "path": str(case_path), **cases}
    if report["ok"] and "case_results" in report:
        cases = report["case_results"]
        if (cases["status"] != "complete" or any(case["status"] == "failed" for case in cases.get("cases", []))
                or report.get("renderer_error", {}).get("source") == "project"):
            report.update(ok=False, status="failed", failure_stage="case-results",
                          error=f"Recipe reported unsuccessful or invalid case results; inspect {case_path}")
    if "renderer_error" in report:
        try:
            captures, truncated = saved_captures(Path(directory))
            report.update(saved_captures=captures, saved_captures_truncated=truncated)
        except OSError as exc:
            report["saved_captures_error"] = str(exc)
    if report["ok"] and (review := report.get("review")):
        from lib.agent_visual_batch import review_batch

        try:
            gallery = review_batch(
                argparse.Namespace(manifest=review["manifest"], output=os.path.join(directory, "review-gallery"),
                                   allow_incompatible=False),
                evidence_root=Path(directory),
            )
            review.update(viewer=gallery["viewer"], batch_id=gallery["batch_id"],
                          batch_report=os.path.join(gallery["directory"], "batch.json"))
        except (OSError, RuntimeError, ValueError) as exc:
            report.update(ok=False, status="failed", failure_stage="review", error=f"Review gallery failed: {exc}")
        except KeyboardInterrupt:
            report.update(ok=False, status="interrupted", failure_stage="review", error="Review gallery generation interrupted")
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    write_json_atomic(report_path, report)
    return report
