"""Isolated, deliberate Blender asset inspection with retained failure evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import math
import os
from pathlib import Path
import shutil
import stat
import subprocess
import time

from desktop import blender_inspect_scene
from lib.agent_blender import _directory, _environment
from lib.agent_visuals import _image
from lib.atomic_io import read_json_file, write_json_atomic
from lib.blender_inputs import assembly_inputs
from lib.remote_utils import is_dry_run, run
from lib.validation import validate_filesystem_path, validate_no_control_characters


def _source_hash(path: str) -> str:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as file:
        if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
            raise ValueError("Inspection inputs must be regular files")
        return hashlib.file_digest(file, "sha256").hexdigest()


def inspect_blender(args: argparse.Namespace) -> dict:
    if type(args.timeout) is not int or not 1 <= args.timeout <= 3600:
        raise ValueError("Inspection timeout must be 1–3600 seconds")
    if type(args.size) is not int or not 64 <= args.size <= 2048:
        raise ValueError("Diagnostic size must be 64–2048 pixels")
    if is_dry_run():
        raise ValueError("Blender inspection requires execution")
    source = os.path.abspath(os.path.expanduser(args.source))
    validate_filesystem_path(source, must_exist=True)
    digest = _source_hash(source)
    assembly = assembly_inputs(source)
    dependencies = [os.path.abspath(os.path.expanduser(path)) for path in args.dependency]
    if len(dependencies) > 256:
        raise ValueError("Inspect at most 256 declared companion files")
    for path in dependencies:
        validate_filesystem_path(path, must_exist=True)
    inputs = {path: _source_hash(path) for path in {source, *dependencies, *(part["source"] for part in assembly)}}
    if not math.isfinite(args.support_tolerance) or args.support_tolerance <= 0:
        raise ValueError("Support tolerance must be finite and positive")
    objects = args.objects
    pairs, allowed = args.intersect, args.allow_intersection
    if len(objects) > 256 or len(pairs) > 32 or len(allowed) > 32 or len(args.support) > 32:
        raise ValueError("Inspect at most 256 objects and 32 intersection pairs")
    for name in [*objects, *(name for pair in pairs + allowed + args.support for name in pair)]:
        if not isinstance(name, str) or not name or len(name) > 200:
            raise ValueError("Object names must be nonempty strings of at most 200 characters")
        validate_no_control_characters(name, "Object name")
    if any(a == b for a, b in pairs + allowed + args.support):
        raise ValueError("Intersection checks require two distinct objects")
    if any(set(pair) not in [set(item) for item in pairs] for pair in allowed):
        raise ValueError("Allowed intersections must also be designated with --intersect")
    executable = shutil.which("blender")
    if not executable:
        raise RuntimeError("Blender is not on PATH; select --blender in setup")
    directory = _directory(args.output)
    request = {"source": source, "objects": objects, "intersections": pairs,
               "allowed_intersections": allowed, "render_views": args.render_views, "size": args.size,
               "assembly": assembly, "supports": args.support, "support_tolerance": args.support_tolerance}
    write_json_atomic(os.path.join(directory, "request.json"), request, mode=0o600)
    command = [executable, "--background", "--factory-startup", "--disable-autoexec", "--threads", "2",
               "--python-exit-code", "1", "--python", str(Path(blender_inspect_scene.__file__).resolve()), "--", directory]
    report = {"schema_version": 1, "ok": False, "directory": directory, "source": source,
              "source_sha256": digest, "input_sha256": inputs, "command": command, "timeout": args.timeout,
              "started_at": datetime.now(timezone.utc).isoformat(), "returncode": None,
              "visual_correctness": "unverified", "consumer_appearance": "unverified"}
    log_path = os.path.join(directory, "blender.log")
    started = time.monotonic()
    try:
        environment = _environment(directory)
        with os.fdopen(os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as log:
            completed = run(command, cwd=directory, env=environment, timeout=args.timeout, check=False,
                            stdout=log, stderr=subprocess.STDOUT, input_data="")
        report["returncode"] = completed.returncode
        if completed.returncode:
            raise RuntimeError(f"Blender exited with status {completed.returncode}; inspect {log_path}")
        inspection = read_json_file(os.path.join(directory, "inspection.json"), max_bytes=16 * 1024 * 1024)
        if not isinstance(inspection, dict) or inspection.get("schema_version") != 1 or not isinstance(inspection.get("objects"), list):
            raise ValueError("Blender did not produce a supported inspection report")
        report["inspection"] = inspection
        if args.render_views:
            report["images"] = {f"{view}-{mode}": _image(os.path.join(directory, f"{view}-{mode}.png"), {})["metadata"]
                                for view in ("front", "rear", "underside") for mode in ("solid", "wire", "orientation", "islands")}
            if any((item["width"], item["height"]) != (args.size, args.size) for item in report["images"].values()):
                raise ValueError("Diagnostic render dimensions differ from the request")
        if _source_hash(source) != digest:
            raise ValueError("Source changed during inspection; recapture from a stable checkpoint")
        if any(_source_hash(path) != expected for path, expected in inputs.items()):
            raise ValueError("Inspection input changed; recapture from stable geometry and companions")
        report["ok"] = True
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        report["error"] = str(exc)
    except KeyboardInterrupt:
        report["error"] = "Blender inspection interrupted"
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    report["report"] = os.path.join(directory, "report.json")
    write_json_atomic(report["report"], report, mode=0o600)
    return report
