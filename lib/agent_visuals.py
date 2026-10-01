"""Private visual comparisons and repeatable captures in managed Git worktrees."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import stat
import struct
import subprocess
import time
import uuid

from lib import agent_workspace
from lib.atomic_io import read_json_file, write_json_atomic
from lib.remote_utils import run
from lib.validation import validate_filesystem_path, validate_no_control_characters, validate_positive_integer
from lib.visual_compare_viewer import render_viewer


MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_IMAGE_PIXELS = 16 * 1024 * 1024


def add_visuals_parser(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser("visuals", help="Compare PNGs or capture two Git revisions")
    actions = parser.add_subparsers(dest="visuals_command", required=True)
    compare = actions.add_parser("compare", help="Build a self-contained before/after viewer")
    compare.add_argument("before")
    compare.add_argument("after")
    compare.add_argument("--before-settings")
    compare.add_argument("--after-settings")
    capture = actions.add_parser("capture", help="Run the same capture command in two managed worktrees")
    capture.add_argument("--repository", default=".")
    capture.add_argument("--before", required=True, metavar="REVISION")
    capture.add_argument("--after", required=True, metavar="REVISION")
    capture.add_argument("--settings", required=True, metavar="JSON")
    capture.add_argument("--timeout", type=validate_positive_integer, default=600)
    capture.add_argument("capture_command", nargs=argparse.REMAINDER, metavar="COMMAND")
    for action in (compare, capture):
        action.add_argument("--output", help="New artifact directory; default: private managed state")
        action.add_argument("--json", action="store_true")


def _settings(path: str | None) -> dict:
    if path is None:
        return {}
    value = read_json_file(os.path.abspath(os.path.expanduser(path)), max_bytes=64 * 1024)
    if not isinstance(value, dict):
        raise ValueError("Capture settings must be a JSON object")
    # Python's JSON reader accepts NaN/Infinity; the browser's JSON parser does
    # not. Reject them before creating evidence or worktrees.
    json.dumps(value, allow_nan=False)
    return value


def _image(path: str, metadata: dict) -> dict:
    resolved = os.path.abspath(os.path.expanduser(path))
    validate_filesystem_path(resolved, must_exist=True)
    descriptor = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("Capture must be a regular PNG file")
        with os.fdopen(descriptor, "rb") as file:
            descriptor = -1
            content = file.read(MAX_IMAGE_BYTES + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(content) > MAX_IMAGE_BYTES:
        raise ValueError("PNG capture exceeds 16 MiB")
    if len(content) < 33 or content[:8] != b"\x89PNG\r\n\x1a\n" or content[12:16] != b"IHDR":
        raise ValueError("Capture must have a PNG header")
    width, height = struct.unpack(">II", content[16:24])
    if not width or not height or max(width, height) > 16384 or width * height > MAX_IMAGE_PIXELS:
        raise ValueError("PNG dimensions must be positive, at most 16384 per side and 16 megapixels")
    return {
        "image": "data:image/png;base64," + base64.b64encode(content).decode("ascii"),
        "label": str(metadata.get("revision") or os.path.basename(resolved)),
        "metadata": {**metadata, "image_path": resolved, "width": width, "height": height,
                     "sha256": hashlib.sha256(content).hexdigest()},
    }


def _directory(output: str | None, run_id: str) -> str:
    if output:
        destination = os.path.abspath(os.path.expanduser(output))
        validate_filesystem_path(destination)
        os.makedirs(os.path.dirname(destination), exist_ok=True)
    else:
        home = agent_workspace._effective_home()
        parent = agent_workspace._managed_root(
            home, os.path.join(home, ".local/state/basaltwater/visuals"), create=True,
        )
        destination = os.path.join(parent, run_id)
    os.mkdir(destination, mode=0o700)  # Never overwrite another run's evidence.
    return destination


def _write_viewer(directory: str, before: dict, after: dict) -> dict:
    path = os.path.join(directory, "index.html")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        file.write(render_viewer(before, after))
    return {"viewer": path, "before": before["metadata"], "after": after["metadata"]}


def compare_captures(args: argparse.Namespace) -> dict:
    before = _image(args.before, _settings(args.before_settings))
    after = _image(args.after, _settings(args.after_settings))
    directory = _directory(args.output, "compare-" + uuid.uuid4().hex[:12])
    result = _write_viewer(directory, before, after)
    write_json_atomic(os.path.join(directory, "comparison.json"), result)
    return result


def capture_revisions(args: argparse.Namespace) -> dict:
    command = list(args.capture_command)
    if command and command[0] == "--":
        command.pop(0)
    if not command or not any("{output}" in arg for arg in command):
        raise ValueError("Supply a capture argv after -- that writes a PNG to {output}")
    for argument in command:
        validate_no_control_characters(argument, "Capture argument")
    if not command[0]:
        raise ValueError("Capture executable cannot be empty")
    if not 0 < args.timeout <= 3600:
        raise ValueError("Capture timeout must be from 1 through 3600 seconds")
    settings = _settings(args.settings)
    repository = agent_workspace._repository_root(args.repository)
    # Resolve both refs before creating anything. Branch changes during a build
    # cannot change which commits the two captures use.
    commits = {
        side: agent_workspace._validate_base(repository, getattr(args, side))
        for side in ("before", "after")
    }
    run_id = "visual-" + uuid.uuid4().hex[:12]
    directory = _directory(args.output, run_id)
    settings_path = os.path.join(directory, "settings.json")
    # Compact serialization bounds whitespace expansion. Escaped Unicode can
    # still expand the caller's bounded UTF-8 input, so allow that internally.
    write_json_atomic(settings_path, settings, mode=0o400, indent=None)
    result = {"ok": False, "directory": directory, "captures": {}, "command": command}
    manifest_path = os.path.join(directory, "capture.json")
    images = {}
    try:
        for side in ("before", "after"):
            worktree = agent_workspace.create_agent_worktree(
                repository, f"{run_id}-{side}", base=commits[side],
            )
            record = {"revision": commits[side], "requested_revision": getattr(args, side),
                      "worktree": worktree["path"], "settings": settings, "status": "running"}
            result["captures"][side] = record
            write_json_atomic(manifest_path, result)
            output = os.path.join(directory, side + ".png")
            argv = [arg.replace("{output}", output).replace("{settings}", settings_path) for arg in command]
            started = time.monotonic()
            log_path = os.path.join(directory, side + ".log")
            record["log"] = log_path
            try:
                descriptor = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as log:
                    completed = run(
                        argv, cwd=str(worktree["path"]), timeout=args.timeout, check=False,
                        stdout=log, stderr=subprocess.STDOUT, input_data="",
                    )
                record["exit_status"] = completed.returncode
                if completed.returncode != 0:
                    raise RuntimeError(f"{side} capture failed; inspect {log_path}")
                if read_json_file(settings_path, max_bytes=512 * 1024) != settings:
                    raise RuntimeError(f"{side} capture changed the shared settings; refusing an inconsistent comparison")
                images[side] = _image(output, record)
                record.update(status="captured", duration_seconds=round(time.monotonic() - started, 3))
                images[side]["metadata"].update(record)
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
                record["status"] = "failed"
                raise
        result.update(_write_viewer(directory, images["before"], images["after"]))
        result["ok"] = True
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        result["error"] = str(exc)
    finally:
        write_json_atomic(manifest_path, result)
    return result


def run_visuals_command(args: argparse.Namespace) -> int:
    try:
        result = capture_revisions(args) if args.visuals_command == "capture" else compare_captures(args)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        result = {"ok": False, "error": str(exc)}
    if args.json:
        print(json.dumps(result, indent=2))
    elif result.get("viewer"):
        print(result["viewer"])
    else:
        print(f"Error: {result['error']}")
        if result.get("directory"):
            print(f"Capture evidence retained: {result['directory']}")
    return 1 if result.get("ok") is False else 0
