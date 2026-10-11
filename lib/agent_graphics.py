"""Explicit GLX context checks, separate from rendering and display qualification."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tempfile

from lib.agent_probe import run_probe


def inspect_graphics_context(context: str) -> dict:
    """Launch glxinfo only on request; an Xvfb context never tests the desktop."""
    if context not in ("xvfb", "display"):
        raise ValueError("Choose xvfb or display")
    result = {"context": context, "backend": "GLX", "healthy": False, "process_exit_success": None,
              "context_created": False, "renderer": None, "opengl_version": None,
              "accelerated": None, "rendering_verified": False, "hardware_profiling_qualified": False,
              "physical_display_qualified": False, "hdr_qualified": False, "input_latency_qualified": False,
              "issues": []}
    glxinfo = shutil.which("glxinfo", path="/usr/local/bin:/usr/bin:/bin")
    if not glxinfo:
        result["issues"].append("glxinfo_missing")
        return result
    if context == "display" and not os.environ.get("DISPLAY"):
        result["issues"].append("display_missing")
        return result
    wrapper = shutil.which("xvfb-run", path="/usr/local/bin:/usr/bin:/bin") if context == "xvfb" else None
    if context == "xvfb" and (not wrapper or not shutil.which("Xvfb", path="/usr/local/bin:/usr/bin:/bin")
                              or not shutil.which("xauth", path="/usr/local/bin:/usr/bin:/bin")):
        result["issues"].append("xvfb_tools_missing")
        return result
    try:
        configuration = tempfile.TemporaryDirectory(prefix="basaltwater-glx-probe-")
    except OSError:
        result["issues"].append("probe_unavailable")
        return result
    with configuration as private:
        command = [glxinfo, "-B"]
        if wrapper:
            command = [wrapper, "-a", "--auth-file", os.path.join(private, "authority"),
                       "--server-args=-screen 0 640x480x24 -nolisten tcp", *command]
        probe = run_probe(command, timeout=15, max_bytes=16384, env=dict(os.environ, LC_ALL="C"))
    result["process_exit_success"] = probe.issue is None if probe.issue in (None, "probe_failed") else None
    if probe.issue:
        result["issues"].append(probe.issue)
        return result
    fields = {}
    for line in probe.output.splitlines():
        if ":" in line:
            key, value = line.strip().split(":", 1)
            value = value.strip()
            if 0 < len(value) <= 256 and not any(ord(character) < 32 for character in value):
                fields[key] = value
    result["renderer"] = fields.get("OpenGL renderer string")
    result["opengl_version"] = fields.get("OpenGL core profile version string") or fields.get("OpenGL version string")
    result["context_created"] = bool(result["renderer"] and result["opengl_version"])
    renderer = (result["renderer"] or "").lower()
    software = bool(re.search(r"llvmpipe|softpipe|swrast|software rasterizer|swiftshader", renderer))
    accelerated = fields.get("Accelerated", "").lower()
    result["accelerated"] = False if software or accelerated == "no" else True if accelerated == "yes" else None
    if not result["context_created"]:
        result["issues"].append("context_creation_unverified")
    result["healthy"] = result["context_created"]
    # glxinfo creates a context but does not verify a project frame or timings.
    # Its llvmpipe video-memory counters are deliberately omitted entirely.
    return result


def add_graphics_parser(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser("graphics", help="Opt-in native graphics context diagnostics")
    actions = parser.add_subparsers(dest="agent_graphics_command", required=True)
    doctor = actions.add_parser("doctor")
    doctor.add_argument("--context", choices=("xvfb", "display"), required=True)
    doctor.add_argument("--json", action="store_true")


def run_graphics_command(args: argparse.Namespace) -> int:
    try:
        result = inspect_graphics_context(args.context)
    except OSError:
        print(json.dumps({"ok": False, "issue": "probe_unavailable",
                          "remediation": "Check local temporary-directory capacity and permissions."}))
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result["healthy"] else 1
