"""Concise, read-only discovery of an agent's tools and project conventions."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import urllib.parse

from lib import agent_workspace
from lib.atomic_io import read_json_file
from lib.validation import validate_filesystem_path, validate_no_control_characters
from lib.validators import validate_host


PROJECT_FILE = "basaltwater-agent.json"
DESKTOP_APPLICATIONS = {
    "blender": {
        "workflows": ["3D model touch-ups", "3D scene editing", "background rendering", "Python scene automation"],
        "instructions": [
            "Use the native desktop for model touch-ups, material/UV edits and scene work: basaltw desktop exec -- blender /absolute/project/model.blend. Follow the desktop skill and the project's add-on/script policy.",
            "Complete model edits autonomously: combine viewport editing with bpy, verify a saved task copy by reopening it, and report saved/exported artifacts. Human handoff is optional when requested.",
            "Render without a desktop: blender --background --disable-autoexec scene.blend --render-output /absolute/artifact/render- --render-format PNG --render-frame 1",
            "Check a small isolated Cycles CPU render: basaltw agent blender smoke --json. It retains a blend scene, PNG, settings and logs; UI and GPU readiness remain unverified.",
            "Load the blend file before output overrides; put the render action last. Record camera, frame, resolution, render engine and device.",
            "For Python scene automation, put --python-exit-code 1 before --python SCRIPT so script errors fail the command. Query Blender's Python environment; Debian builds use system libraries and upstream builds may bundle Python. Project virtual environments are not automatically used.",
            "Use a native desktop session to validate interactive editing; background rendering does not verify UI or GPU readiness.",
        ],
    },
    "krita": {
        "workflows": ["raster painting", "texture editing", "sprite touch-ups"],
        "instructions": [
            "Open project assets with basaltw desktop exec -- krita /absolute/project/asset.kra. Preserve a layered KRA source and export the required game or web image separately.",
            "Follow the desktop skill's media reference. Verify alpha, dimensions, sprite frame boundaries and texture seams in the saved export and consuming project.",
        ],
    },
    "gimp": {
        "workflows": ["raster image editing", "image touch-ups"],
        "instructions": [
            "Open project assets with basaltw desktop exec -- gimp /absolute/project/asset.xcf. Save editable layers as XCF and export delivery images separately; check the installed version before scripting.",
            "Follow the desktop skill's media reference. Reopen the export and check crop, alpha edges, dimensions and color appearance in the game or website.",
        ],
    },
    "inkscape": {
        "workflows": ["SVG editing", "vector asset export"],
        "instructions": [
            "Edit SVG assets with basaltw desktop exec -- inkscape /absolute/project/asset.svg. Preserve an editable SVG and resolve linked images and fonts before delivery.",
            "If a panel forces the window beyond a small desktop, hide it before input or capture. Inspect the actual window bounds; agent-created desktops default to 1600x900 after updating Basaltwater.",
            "Export reproducibly: inkscape /absolute/project/asset.svg --export-area-page --export-type=png --export-filename=/absolute/artifact/asset.png. Check inkscape --help for version-specific options and verify viewBox, dimensions and alpha in the consuming project.",
        ],
    },
    "freecad": {"workflows": ["parametric CAD", "3D model inspection"]},
    "kicad": {"workflows": ["schematic editing", "PCB inspection"]},
    "kdenlive": {"workflows": ["video editing"]},
    "shotcut": {
        "workflows": ["video editing", "short clip touch-ups"],
        "instructions": [
            "Open a timeline with basaltw desktop exec -- shotcut /absolute/project/clip.mlt. Keep the MLT project and its linked media; export the requested clip separately.",
            "Shotcut needs a Qt display even for --version/--help; use QT_QPA_PLATFORM=offscreen for terminal-only queries, not UI validation. For isolated tests use --appdata /absolute/private/profile --noupgrade. Check Export's From selection (Source versus Timeline) and wait for the job to finish.",
            "Follow the desktop skill's media reference. Use ffprobe to check export duration, dimensions, frame rate and codecs, then inspect representative frames and audio in the consuming project.",
        ],
    },
    "audacity": {
        "workflows": ["audio editing", "sound effect touch-ups"],
        "instructions": [
            "Edit sound effects with basaltw desktop exec -- audacity /absolute/project/sound.wav. Save an AUP3 project and export the game's or website's required audio format separately.",
            "A title wait can match the startup splash. Inspect the document and dismiss the Welcome dialog through observed controls; prefer accessible Effect menus and Export Audio controls. Scripting is optional and disabled by default.",
            "Follow the desktop skill's media reference. Verify trim, fades, clipping, loop boundaries, sample rate and channels; a silent RDP session does not establish that the export lacks audio.",
        ],
    },
    "ardour": {"workflows": ["audio production"]},
    "lmms": {"workflows": ["music production"]},
    "scribus": {"workflows": ["page layout", "PDF production"]},
    "obs": {"workflows": ["desktop recording"]},
}
TOOLS = (
    "git", "gh", "codex", "claude", "opencode", "node", "npm", "yarn",
    "pnpm", "corepack", "python3", "uv", "go", "gcc", "g++", "make",
    "cmake", "godot", "glxinfo", "apitrace", "ffmpeg", "ffprobe",
    "magick", "convert", "identify", "exiftool",
    "rg", "jq", "aws", "basaltwater-web", *DESKTOP_APPLICATIONS,
)
DESKTOP_GUIDE = "https://github.com/bluehexagons/basaltwater/blob/main/docs/DESKTOP_DEVELOPMENT.md"


def add_manifest_parser(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser("manifest", help="Show tools, workspace conventions and declared deployment mappings")
    parser.add_argument("repository", nargs="?", default=".")
    parser.add_argument("--json", action="store_true")


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError(f"{label} must be a non-empty string of at most 256 characters")
    validate_no_control_characters(value, label)
    return value


def _required_tools(value: object) -> list[str]:
    if not isinstance(value, list) or len(value) > 100:
        raise ValueError("required tools must be an array of at most 100 executable names")
    for name in value:
        _text(name, "required tool")
        validate_filesystem_path(name)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_+.-]*", name):
            raise ValueError("Required tools must be executable names without paths or whitespace")
    return list(dict.fromkeys(value))


def _recipes(value: object, repository: str) -> dict[str, object]:
    if not isinstance(value, dict) or len(value) > 100:
        raise ValueError("recipes must be an object of at most 100 named workflows")
    recipes = {}
    for name, recipe in value.items():
        _text(name, "recipe name")
        if not isinstance(recipe, dict) or set(recipe) - {"description", "argv", "directory", "requires"}:
            raise ValueError(f"Invalid recipe: {name}")
        description = _text(recipe.get("description"), "recipe description")
        argv = recipe.get("argv")
        if not isinstance(argv, list) or not 1 <= len(argv) <= 100:
            raise ValueError("Recipe argv must contain between 1 and 100 arguments")
        for argument in argv:
            _text(argument, "recipe argument")
        directory = _text(recipe.get("directory", "."), "recipe directory")
        validate_filesystem_path(directory)
        directory = os.path.normpath(directory)
        if (
            os.path.isabs(directory) or directory == ".." or directory.startswith("../")
            or os.path.commonpath([
                os.path.realpath(repository), os.path.realpath(os.path.join(repository, directory)),
            ]) != os.path.realpath(repository)
        ):
            raise ValueError("Recipe directory must remain below the repository")
        recipes[name] = {
            "description": description, "argv": argv, "directory": directory,
            "requires": _required_tools(recipe.get("requires", [])),
        }
    return recipes


def load_project_environment(repository: str) -> dict[str, object]:
    """Load only explicit non-secret declarations; never infer deployment targets."""
    path = os.path.join(repository, PROJECT_FILE)
    try:
        data = read_json_file(path, max_bytes=64 * 1024)
    except FileNotFoundError:
        return {"source": None, "deployments": {}, "artifact_directories": [], "required_tools": [], "recipes": {}}
    if (
        not isinstance(data, dict) or type(data.get("version")) is not int
        or data["version"] != 1
        or set(data) - {"version", "deployments", "artifact_directories", "required_tools", "recipes"}
    ):
        raise ValueError(f"{PROJECT_FILE}: expected version 1 and known fields")
    deployments = data.get("deployments", {})
    if not isinstance(deployments, dict) or len(deployments) > 100:
        raise ValueError("deployments must be an object of at most 100 branch mappings")
    for branch, mapping in deployments.items():
        _text(branch, "deployment branch")
        if branch.startswith("-") or agent_workspace._git(
            repository, ["check-ref-format", "--branch", branch], check=False,
        ).returncode != 0:
            raise ValueError(f"Invalid deployment branch: {branch}")
        if not isinstance(mapping, dict) or set(mapping) - {"environment", "provider", "region", "url"}:
            raise ValueError(f"Invalid deployment mapping for {branch}")
        _text(mapping.get("environment"), "deployment environment")
        for field in ("provider", "region"):
            if field in mapping:
                _text(mapping[field], field)
        if "url" in mapping:
            url = _text(mapping["url"], "deployment URL")
            parsed = urllib.parse.urlsplit(url)
            if (
                parsed.scheme != "https" or not validate_host(parsed.hostname or "")
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment
            ):
                raise ValueError("Deployment URL must use HTTPS without credentials, query or fragment")
            _ = parsed.port
    artifacts = data.get("artifact_directories", [])
    if not isinstance(artifacts, list) or len(artifacts) > 100:
        raise ValueError("artifact_directories must be an array of at most 100 paths")
    normalized = []
    for value in artifacts:
        path_value = _text(value, "artifact directory")
        validate_filesystem_path(path_value)
        path_value = os.path.normpath(path_value)
        if os.path.isabs(path_value) or path_value in (".", "..") or path_value.startswith("../"):
            raise ValueError("Artifact directories must remain below the repository")
        normalized.append(path_value)
    return {
        "source": path, "deployments": deployments,
        "artifact_directories": list(dict.fromkeys(normalized)),
        "required_tools": _required_tools(data.get("required_tools", [])),
        "recipes": _recipes(data.get("recipes", {}), repository),
    }


def inspect_environment(repository: str) -> dict[str, object]:
    root = agent_workspace._repository_root(repository)
    home = agent_workspace._effective_home()
    project = load_project_environment(root)
    artifacts = [
        {"path": path, "ignored": agent_workspace._git(
            root, ["check-ignore", "-q", "--", path + "/"], check=False,
        ).returncode == 0}
        for path in project["artifact_directories"]
    ]
    # Resolve the active session PATH. Do not run package-manager shims, which
    # can download tools even for --version, or inspect authentication files.
    declared_tools = dict.fromkeys([
        *TOOLS, *project["required_tools"],
        *(name for recipe in project["recipes"].values() for name in recipe["requires"]),
    ])
    tools = {name: shutil.which(name) for name in declared_tools}
    requirements = {
        name: {"executable": tools[name], "status": "available" if tools[name] else "missing"}
        for name in project["required_tools"]
    }
    recipes = {
        name: {**recipe, "missing_tools": [tool for tool in recipe["requires"] if not tools[tool]]}
        for name, recipe in project["recipes"].items()
    }
    desktop = {
        name: {**guidance, "executable": tools[name], "readiness": "unverified", "guide": DESKTOP_GUIDE}
        for name, guidance in DESKTOP_APPLICATIONS.items() if tools[name]
    }
    desktop_skills = [
        path for name in ("basaltwater-desktop", "basaltwater-cachyos-workstation")
        if os.path.isfile(path := os.path.join(home, ".agents", "skills", name, "SKILL.md"))
    ]
    state = agent_workspace._worktree_record(root)
    mappings = project["deployments"]
    return {
        "schema_version": 1,
        "tools": tools,
        "desktop_applications": desktop,
        "desktop_skills": desktop_skills if desktop else [],
        "workspace": {
            "repository": root, "branch": state["branch"], "commit": state["head"],
            "dirty": state["dirty"], "repository_root": os.path.join(home, "repos"),
            "worktree_root": os.path.join(home, agent_workspace._DEFAULT_WORKTREE_RELATIVE),
            "task_branch_pattern": "agent/TASK",
            "browser_evidence": os.path.join(home, ".local/state/basaltwater/playwright-mcp"),
            "artifact_directories": artifacts,
        },
        "project_source": project["source"],
        "required_tools": requirements,
        "recipes": recipes,
        "deployments": mappings,
        "current_deployment": mappings.get(state["branch"]),
        "undeclared_branches": [branch for branch in ("dev", "staging") if branch not in mappings],
        "health_command": "basaltw agent doctor --all-capabilities --json",
    }


def run_manifest_command(args: argparse.Namespace) -> int:
    try:
        result = inspect_environment(args.repository)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"Error: {exc}")
        return 1
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    workspace = result["workspace"]
    print(f"Repository: {workspace['repository']} ({workspace['branch'] or 'detached'}, {workspace['commit'][:12]})")
    print("Available tools: " + ", ".join(name for name, path in result["tools"].items() if path))
    for name, requirement in result["required_tools"].items():
        print(f"Required tool: {name} ({requirement['status']})")
    for name, recipe in result["recipes"].items():
        print(f"Recipe: {name} — {recipe['description']}")
        print(f"  Directory: {recipe['directory']}; command: {shlex.join(recipe['argv'])}")
        if recipe["missing_tools"]:
            print("  Missing tools: " + ", ".join(recipe["missing_tools"]))
    for name, application in result["desktop_applications"].items():
        print(f"Desktop: {name} — {', '.join(application['workflows'])} (readiness unverified)")
        for instruction in application.get("instructions", []):
            print(f"  {instruction}")
    if result["desktop_applications"]:
        print(f"Desktop guide: {DESKTOP_GUIDE}")
        for path in result["desktop_skills"]:
            print(f"Desktop skill: {path}")
    print(f"Worktrees: {workspace['worktree_root']} (agent/TASK)")
    print(f"Browser evidence: {workspace['browser_evidence']}")
    for artifact in workspace["artifact_directories"]:
        print(f"Artifact directory: {artifact['path']} ({'ignored' if artifact['ignored'] else 'not ignored'})")
    for branch, mapping in result["deployments"].items():
        details = ", ".join(str(mapping[field]) for field in ("provider", "region", "url") if field in mapping)
        print(f"Deploy: {branch} -> {mapping['environment']}" + (f" ({details})" if details else ""))
    if result["undeclared_branches"]:
        print("Deployment mapping unknown: " + ", ".join(result["undeclared_branches"]) + f"; declare in {PROJECT_FILE}")
    print(f"Health: {result['health_command']} (tool presence is not a health or authentication check)")
    return 0
