"""Bounded workstation metadata, without opening graphics contexts or project code."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import platform
import re
import shutil
import time

from lib.agent_probe import run_probe


# Only terminal-only system executables are queried. In particular, do not run
# package-manager shims or GUI apps just to discover a version.
VERSION_TOOLS = ("git", "python3", "cc", "clang", "cmake", "ninja", "make", "pkg-config", "ffmpeg")
TOOLS = (*VERSION_TOOLS, "node", "npm", "pnpm", "go", "godot", "blender",
         "glxinfo", "eglinfo", "vulkaninfo", "xvfb-run", "Xvfb", "xauth")


def _tool(name: str) -> dict:
    executable = shutil.which(name)
    result = {"executable": executable, "installed": executable is not None,
              "version": None, "version_probe": "not-run", "usable_in_session": None,
              "verified_by_project": None}
    if not executable or name not in VERSION_TOOLS:
        return result
    # A local wrapper may have side effects even for --version. Observe PATH
    # presence but only query the distro's system executable from a neutral cwd.
    if str(Path(executable).resolve().parent) not in ("/usr/bin", "/bin"):
        return result
    probe = run_probe([executable, "-version" if name == "ffmpeg" else "--version"], timeout=2, max_bytes=8192,
                      env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
    result["version_probe"] = probe.issue or "passed"
    if probe.issue is None:
        # Retain only a version token, never arbitrary banners or terminal escapes.
        first_line = next(iter(probe.output.splitlines()), "")
        if name == "ffmpeg":
            # Arch uses n-prefixed release tags; later lines describe linked
            # libraries and must never be mistaken for the executable version.
            match = re.fullmatch(r"ffmpeg version ([A-Za-z0-9][A-Za-z0-9._+~:-]{0,79})(?:\s.*)?", first_line)
            result["version"] = match[1] if match else None
        else:
            match = re.search(r"(?<![\w.])\d+\.\d+(?:\.[0-9]+)*(?:[-+][A-Za-z0-9.]+)?", first_line)
            result["version"] = match[0][:80] if match else None
    # A terminal version query does not establish application/session usability.
    return result


def _gpus() -> dict:
    """Read kernel PCI identities; these do not establish GPU accessibility."""
    root = Path("/sys/class/drm")
    devices = []
    try:
        cards = sorted(path for path in root.iterdir() if re.fullmatch(r"card[0-9]+", path.name))[:16]
        for card in cards:
            device = card / "device"
            try:
                vendor = (device / "vendor").read_text(encoding="ascii").strip()
                identity = (device / "device").read_text(encoding="ascii").strip()
                if not all(re.fullmatch(r"0x[0-9a-fA-F]{4}", value) for value in (vendor, identity)):
                    continue
                driver = (device / "driver").resolve().name if (device / "driver").is_symlink() else None
                devices.append({"card": card.name, "pci_id": vendor[2:] + ":" + identity[2:],
                                "driver": driver, "usable_in_session": None})
            except (OSError, UnicodeError):
                continue
    except OSError:
        return {"source": "unavailable", "devices": []}
    return {"source": "linux-drm-sysfs", "devices": devices}


def inspect_workstation(repository: str, project: dict) -> dict:
    """Summarize installed prerequisites separately from runtime/project evidence."""
    started = time.monotonic()
    tools = {name: _tool(name) for name in dict.fromkeys((*TOOLS, *project["required_tools"]))}
    # Recipe commands remain declarations, never readiness claims. Keep large
    # argv/instruction bodies in the normal development manifest.
    recipes = {name: {"description": recipe["description"],
                      "missing_tools": [tool for tool in recipe["requires"] if shutil.which(tool) is None],
                      "usable_in_session": None, "verified_by_project": None}
               for name, recipe in project["recipes"].items()}
    backends = {
        "wayland": {"session_advertised": bool(os.environ.get("WAYLAND_DISPLAY")),
                    "usable_in_session": None},
        "x11": {"session_advertised": bool(os.environ.get("DISPLAY")),
                "usable_in_session": None},
        "xvfb_glx": {"tools_installed": all(tools[name]["installed"] for name in ("xvfb-run", "Xvfb", "xauth", "glxinfo")),
                     "usable_in_session": None},
        "egl": {"probe_installed": tools["eglinfo"]["installed"], "usable_in_session": None},
        "vulkan": {"probe_installed": tools["vulkaninfo"]["installed"], "usable_in_session": None},
    }
    gpu = _gpus()
    return {
        "schema_version": 1, "observed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "observed", "elapsed_seconds": round(time.monotonic() - started, 3),
        "system": platform.system(), "kernel": platform.release(), "tools": tools,
        "rendering_backends": backends, "gpu": gpu, "project_source": project["source"],
        "recipes": recipes,
        "commands": {
            "recipes": ["basaltw", "agent", "manifest", repository, "--capability", "development", "--json"],
            "project_check": ["basaltw", "agent", "visuals", "check", "RECIPE", "--repository", repository, "--json"],
            "glx_context": ["basaltw", "agent", "graphics", "doctor", "--context", "display", "--json"],
            "software_context": ["basaltw", "agent", "graphics", "doctor", "--context", "xvfb", "--require-software", "--json"],
            "blender_cpu": ["basaltw", "agent", "blender", "smoke", "--json"],
        },
        "qualification": "Installed means PATH presence. Version probes test only terminal startup. "
        "Session advertisements and kernel GPU/driver identities do not prove a usable renderer. "
        "null means unverified; project verification requires a separate recipe report at the tested revision.",
    }


def format_workstation(report: dict) -> str:
    """A short terminal report; JSON retains individual unknowns and probe issues."""
    installed = [name + ("=" + tool["version"] if tool["version"] else "")
                 for name, tool in report["tools"].items() if tool["installed"]]
    gpu = ", ".join(device["pci_id"] + " (" + (device["driver"] or "driver unknown") + ")"
                    for device in report["gpu"]["devices"]) or "unobserved"
    return "\n".join([
        f"Workstation: {report['system']} {report['kernel']} ({report['elapsed_seconds']}s)",
        "Installed: " + (", ".join(installed) or "none observed"),
        "GPU/driver: " + gpu,
        "Backend prerequisites: " + ", ".join(name + "=" + (
            "advertised" if backend.get("session_advertised") else
            "tools present" if backend.get("tools_installed", backend.get("probe_installed")) else "unobserved")
            for name, backend in report["rendering_backends"].items()),
        "Usable in session: unverified; verified by project: unverified",
        "Project recipes: " + (", ".join(report["recipes"]) or "none declared"),
        "Checks: agent graphics doctor --context display|xvfb; agent blender smoke; agent visuals check RECIPE",
    ])
