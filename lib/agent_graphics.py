"""Explicit GLX/EGL checks, separate from rendering and display qualification."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile

from lib.agent_probe import run_probe
from lib.validation import validate_no_control_characters


GRAPHICS_ENVIRONMENT = (
    "XDG_SESSION_TYPE", "DISPLAY", "WAYLAND_DISPLAY", "SDL_VIDEODRIVER",
    "SDL_RENDER_DRIVER", "LIBGL_ALWAYS_SOFTWARE", "GALLIUM_DRIVER",
    "MESA_LOADER_DRIVER_OVERRIDE", "DRI_PRIME", "QT_QPA_PLATFORM", "EGL_PLATFORM",
    "__EGL_VENDOR_LIBRARY_FILENAMES", "__EGL_VENDOR_LIBRARY_DIRS",
    "__GLX_VENDOR_LIBRARY_NAME", "__NV_PRIME_RENDER_OFFLOAD",
    "__NV_PRIME_RENDER_OFFLOAD_PROVIDER", "__VK_LAYER_NV_optimus", "GBM_BACKEND",
    "VK_ICD_FILENAMES", "VK_DRIVER_FILES", "MESA_VK_DEVICE_SELECT",
    "CUDA_VISIBLE_DEVICES", "NVIDIA_VISIBLE_DEVICES",
)


def graphics_environment(environment: dict[str, str]) -> dict[str, str]:
    """Retain only bounded selector values, never the entire environment."""
    return {key: value[:1024] if not any(ord(c) < 32 or ord(c) == 127 for c in value) else "<invalid>"
            for key in GRAPHICS_ENVIRONMENT if (value := environment.get(key)) is not None}


def inspect_graphics_context(context: str, *, require_software: bool = False,
                             backend: str = "glx", expect_renderer: str | None = None) -> dict:
    """Launch an isolated context only on request; never qualify a project frame."""
    if backend not in ("glx", "egl") or context not in (("xvfb", "display") if backend == "glx" else ("offscreen", "surfaceless")):
        raise ValueError("GLX needs xvfb/display; EGL needs offscreen/surfaceless")
    if expect_renderer is not None:
        validate_no_control_characters(expect_renderer, "Expected renderer")
        if not expect_renderer.strip() or len(expect_renderer) > 256:
            raise ValueError("Expected renderer must contain 1–256 characters")
    result = {"context": context, "backend": backend.upper(), "healthy": False, "process_exit_success": None,
              "context_created": False, "renderer": None, "opengl_version": None,
              "accelerated": None, "software_renderer": None, "software_required": require_software,
              "rendering_verified": False, "hardware_profiling_qualified": False,
              "physical_display_qualified": False, "hdr_qualified": False, "input_latency_qualified": False,
              "xvfb_allocation": None, "issues": [], "inherited_selectors": graphics_environment(dict(os.environ)),
              "expected_renderer": expect_renderer, "renderer_matches": None}
    glxinfo = shutil.which("glxinfo", path="/usr/local/bin:/usr/bin:/bin") if backend == "glx" else None
    if backend == "glx" and not glxinfo:
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
        configuration = tempfile.TemporaryDirectory(prefix="basaltwater-graphics-probe-")
    except OSError:
        result["issues"].append("probe_unavailable")
        return result
    with configuration as private:
        command = ([glxinfo, "-B"] if backend == "glx" else
                   [sys.executable, "-I", os.path.join(os.path.dirname(__file__), "agent_egl_probe.py"), context])
        if wrapper:
            help_probe = run_probe([wrapper, "--help"], timeout=3, max_bytes=8192,
                                   env=dict(os.environ, LC_ALL="C"))
            automatic = help_probe.issue is None and re.search(
                r"(?m)^\s*-d[ \t,]+--auto-display(?:[ \t]|$)", help_probe.output,
            ) is not None
            allocation = "-d" if automatic else "-a"
            result["xvfb_allocation"] = "auto-display" if automatic else "auto-servernum"
            command = [wrapper, allocation, "--auth-file", os.path.join(private, "authority"),
                       "--server-args=-screen 0 640x480x24 -nolisten tcp", *command]
        probe = run_probe(command, timeout=15, max_bytes=16384, env=dict(os.environ, LC_ALL="C"))
    result["process_exit_success"] = probe.issue is None if probe.issue in (None, "probe_failed") else None
    if probe.issue:
        result["issues"].append(probe.issue)
        return result
    fields = {}
    if backend == "egl":
        try:
            observed = json.loads(probe.output)
        except ValueError:
            observed = None
        if not isinstance(observed, dict):
            result["issues"].append("egl_probe_invalid")
            return result
        issue = observed.get("issue")
        if issue:
            result["issues"].append(issue if isinstance(issue, str) and re.fullmatch(r"egl_[a-z_]{1,64}", issue) else "egl_probe_invalid")
            return result
        for source, destination in (("renderer", "OpenGL renderer string"), ("opengl_version", "OpenGL version string"),
                                    ("egl_vendor", "egl_vendor"), ("egl_version", "egl_version"), ("client_api", "client_api")):
            value = observed.get(source)
            if isinstance(value, str) and 0 < len(value) <= 256 and not any(ord(c) < 32 for c in value):
                fields[destination] = value
        for key in ("egl_vendor", "egl_version", "client_api"):
            result[key] = fields.get(key)
    else:
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
    software = bool(re.search(r"\b(llvmpipe|softpipe|swrast|software rasterizer|swiftshader)\b", renderer))
    accelerated = fields.get("Accelerated", "").lower()
    result["accelerated"] = False if software or accelerated == "no" else True if accelerated == "yes" else None
    if result["context_created"]:
        result["software_renderer"] = True if software else False if accelerated == "yes" else None
    if not result["context_created"]:
        result["issues"].append("context_creation_unverified")
    elif require_software and result["software_renderer"] is not True:
        result["issues"].append("software_renderer_unverified")
    if expect_renderer is not None and result["context_created"]:
        result["renderer_matches"] = expect_renderer.casefold() in result["renderer"].casefold()
        if not result["renderer_matches"]:
            result["issues"].append("renderer_mismatch")
    result["healthy"] = not result["issues"]
    # Context probes do not verify a project frame or timings. glxinfo's
    # llvmpipe video-memory counters are deliberately omitted entirely.
    return result


def add_graphics_parser(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser("graphics", help="Opt-in native graphics context diagnostics")
    actions = parser.add_subparsers(dest="agent_graphics_command", required=True)
    doctor = actions.add_parser("doctor")
    doctor.add_argument("--backend", choices=("glx", "egl"), default="glx")
    doctor.add_argument("--context", choices=("xvfb", "display", "offscreen", "surfaceless"), required=True)
    doctor.add_argument("--require-software", action="store_true",
                        help="Fail unless this context reports a recognized software renderer")
    doctor.add_argument("--expect-renderer", help="Require this case-insensitive substring in the observed renderer")
    doctor.add_argument("--json", action="store_true")


def run_graphics_command(args: argparse.Namespace) -> int:
    try:
        result = inspect_graphics_context(args.context, require_software=args.require_software,
                                          backend=args.backend, expect_renderer=args.expect_renderer)
    except ValueError as exc:
        print(json.dumps({"ok": False, "issue": "invalid_graphics_request", "error": str(exc)}))
        return 1
    except OSError:
        print(json.dumps({"ok": False, "issue": "probe_unavailable",
                          "remediation": "Check local temporary-directory capacity and permissions."}))
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result["healthy"] else 1
