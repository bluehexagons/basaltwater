"""Small host capability context for fresh agent threads, independent of Git."""

from __future__ import annotations

import argparse
import json
import platform
import shutil

from lib import agent_workspace
from lib.agent_skill_catalog import inspect_skill_catalogs
from lib.agent_environment import DESKTOP_APPLICATIONS, MUSESCORE_EXECUTABLES
from lib.cachyos import is_cachyos


GUIDE = "https://github.com/bluehexagons/basaltwater/blob/main/docs/agents/agent-environment.md"
TOOL_GROUPS = {
    "agents": ("codex", "claude", "opencode", "gh", "t3code", "t3"),
    "development": ("git", "node", "npm", "pnpm", "python3", "uv", "go", "gcc", "clang", "godot", "dotnet"),
    "containers": ("podman", "docker"),
    "assets": tuple(dict.fromkeys((*DESKTOP_APPLICATIONS, *MUSESCORE_EXECUTABLES,
                                  "ffmpeg", "ffprobe", "magick", "convert", "exiftool"))),
    "browser": ("basaltwater-playwright-mcp", "firefox", "firefox-esr", "chromium"),
}


def add_context_parser(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser("context", help="Concise host features and help routes for new agent threads (no Git required)")
    parser.add_argument("--json", action="store_true", help="Output structured discovery observations")


def inspect_context() -> dict[str, object]:
    """Observe PATH and known skill bundles without executing installed tools."""
    system = platform.system().lower()
    wsl = system == "linux" and any(part in platform.release().lower() for part in ("microsoft", "wsl"))
    cachyos = system == "linux" and not wsl and is_cachyos()
    try:
        distribution = platform.freedesktop_os_release().get("ID", "unknown") if system == "linux" else system
    except OSError:
        distribution = "unknown"
    host = f"{distribution}-wsl" if wsl else "cachyos-workstation" if cachyos else distribution
    inventory = inspect_skill_catalogs(agent_workspace._effective_home())
    skills: dict[str, list[dict[str, str]]] = {}
    issues = 0
    for catalog in inventory["catalogs"]:
        for entry in catalog["skills"]:
            if entry["status"] in {"unsafe", "incomplete"}:
                issues += 1
                continue
            skills.setdefault(entry["name"], []).append({"path": entry["path"], "status": entry["status"]})
    routes = {
        "full feature index": "basaltw --help",
        "agent operations index": "basaltw agent --help",
        "project tools, recipes, deployment declarations": "basaltw agent manifest --help",
        "private project notes and todos": "basaltw agent memory --help",
        "owned Git worktrees": "basaltw agent workspace --help",
        "independent persistent tasks": "basaltw agent tasks --help",
        "model recommendations and outcome records": "basaltw agent models --help",
        "resource limits and pressure": "basaltw agent resources --help",
        "container capacity and storage": "basaltw agent containers --help",
        "generated artifact cleanup": "basaltw agent storage --help",
        "render comparisons and project checks": "basaltw agent visuals --help",
        "graphics prerequisites": "basaltw agent graphics --help",
        "Node runtime selection": "basaltw node --help",
    }
    if cachyos:
        routes["native KDE applications and development tasks"] = "basaltw desktop --native --help"
        routes["host and selected T3 diagnostics"] = "basaltw local cachyos-doctor --help"
    elif distribution == "debian" and not wsl:
        routes["managed desktop"] = "basaltw desktop --help"
        routes["host and managed T3 diagnostics"] = "basaltw agent doctor --help"
    return {
        "schema_version": 1,
        "host_profile": host,
        "tools_on_path": {group: [name for name in names if shutil.which(name)]
                          for group, names in TOOL_GROUPS.items()},
        "installed_guidance": dict(sorted(skills.items())),
        "unusable_guidance_count": issues,
        "help_routes": routes,
        "readiness": "unverified",
        "instructions": "Use relevant features under the active task and session policy. PATH presence and installed guidance do not verify authentication, services, loaded skills, browser tools, or desktop consent. CLI routes describe commands, not provisioned services. In T3, use its live tools for delegation, schedules and browser work as session policy directs.",
        "details_command": "basaltw agent manifest --help",
        "guide": GUIDE,
    }


def run_context_command(args: argparse.Namespace) -> int:
    try:
        result = inspect_context()
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}) if args.json else f"Error: {exc}")
        return 1
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    print(f"Basaltwater agent context: {result['host_profile']} (readiness unverified)")
    for group, names in result["tools_on_path"].items():
        print(f"PATH {group}: {', '.join(names) or 'none observed'}")
    guidance = []
    for name, copies in result["installed_guidance"].items():
        statuses = sorted({copy["status"] for copy in copies})
        qualifier = " (" + "/".join(statuses) + ")" if statuses != ["current"] else ""
        guidance.append(name + qualifier)
    print("Installed guidance: " + (", ".join(guidance) or "none observed"))
    if any(copy["status"] != "current" for copies in result["installed_guidance"].values() for copy in copies):
        print("Guidance differs from source or is personal; inspect paths/status with basaltw agent context --json.")
    if result["unusable_guidance_count"]:
        print(f"Unsafe/incomplete guidance omitted: {result['unusable_guidance_count']}")
    print("CLI routes (check relevant help before use):")
    for purpose, command in result["help_routes"].items():
        print(f"  {purpose}: {command}")
    print(result["instructions"])
    print(f"Details: {result['details_command']}; {result['guide']}")
    return 0
