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
from lib.cachyos import is_cachyos
from lib.validation import validate_filesystem_path, validate_no_control_characters
from lib.validators import validate_host


PROJECT_FILE = "basaltwater-agent.json"
MUSESCORE_EXECUTABLES = ("musescore4", "mscore4", "musescore3", "mscore3", "musescore", "mscore")
DESKTOP_APPLICATIONS = {
    "blender": {
        "workflows": ["3D model touch-ups", "3D scene editing", "background rendering", "Python scene automation"],
        "instructions": [
            "Use the native desktop for model touch-ups, material/UV edits and scene work: {launch_prefix}blender /absolute/project/model.blend. Follow the desktop skill and the project's add-on/script policy.",
            "Complete model edits autonomously: combine viewport editing with bpy, verify a saved task copy by reopening it, and report saved/exported artifacts. Human handoff is optional when requested.",
            "Render without a desktop: blender --background --disable-autoexec scene.blend --render-output /absolute/artifact/render- --render-format PNG --render-frame 1",
            "Check a small isolated Cycles CPU render: basaltw agent blender smoke --json. It retains a blend scene, PNG, settings and logs; UI and GPU readiness remain unverified.",
            "Load the blend file before output overrides; put the render action last. Record camera, frame, resolution, render engine and device.",
            "For Python scene automation, put --python-exit-code 1 before --python SCRIPT so script errors fail the command. Query Blender's Python environment; Debian builds use system libraries and upstream builds may bundle Python. Project virtual environments are not automatically used.",
            "Use a native desktop session to validate interactive editing; background rendering does not verify UI or GPU readiness.",
            "For glTF/GLB and OBJ/MTL, preserve materials, UVs and companion textures; reimport from a relocated export directory. Query Blender's NumPy dependency and qualify Draco separately when compression is required.",
        ],
    },
    "krita": {
        "workflows": ["raster painting", "texture editing", "sprite touch-ups"],
        "instructions": [
            "Open project assets with {launch_prefix}krita /absolute/project/asset.kra. Preserve a layered KRA source and export the required game or web image separately.",
            "Follow the desktop skill's media reference. Verify alpha, dimensions, sprite frame boundaries and texture seams in the saved export and consuming project.",
        ],
    },
    "gimp": {
        "workflows": ["raster image editing", "image touch-ups"],
        "instructions": [
            "Open project assets with {launch_prefix}gimp /absolute/project/asset.xcf. Save editable layers as XCF and export delivery images separately; check the installed version before scripting.",
            "Follow the desktop skill's media reference. Reopen the export and check crop, alpha edges, dimensions and color appearance in the game or website.",
        ],
    },
    "inkscape": {
        "workflows": ["SVG editing", "vector asset export"],
        "instructions": [
            "Edit SVG assets with {launch_prefix}inkscape /absolute/project/asset.svg. Preserve an editable SVG and resolve linked images and fonts before delivery.",
            "If a panel forces the window beyond the desktop, hide it before input or capture. Inspect the actual window bounds and current display geometry.",
            "For repeatable object edits, discover inkscape --action-list, select existing IDs and export to a task copy with --actions/--batch-process. Verify saved SVG geometry and a fresh PNG export; CLI success alone does not verify the requested edit.",
            "Export reproducibly: inkscape /absolute/project/asset.svg --export-area-page --export-type=png --export-filename=/absolute/artifact/asset.png. Check inkscape --help for version-specific options and verify viewBox, dimensions and alpha in the consuming project.",
        ],
    },
    "freecad": {
        "workflows": ["parametric CAD", "3D model inspection"],
        "instructions": ["Preserve an FCStd task copy, constraints and linked parts. Recompute and reopen before exporting STEP/STL; check units and geometry in the consuming application."],
    },
    "kicad": {
        "workflows": ["schematic editing", "PCB inspection"],
        "instructions": ["Keep the KiCad project, schematic, board and library references together. Use the installed kicad-cli help for repeatable ERC/DRC and fabrication exports; inspect reported violations and exported layers."],
    },
    "kdenlive": {
        "workflows": ["video editing"],
        "instructions": ["Keep the editable .kdenlive project and linked media. Check profile, frame rate and render range; reopen the project and verify the completed export with ffprobe and representative frames/audio."],
    },
    "shotcut": {
        "workflows": ["video editing", "short clip touch-ups"],
        "instructions": [
            "Open a timeline with {launch_prefix}shotcut /absolute/project/clip.mlt. Keep the MLT project and its linked media; export the requested clip separately.",
            "Shotcut needs a Qt display even for --version/--help; use QT_QPA_PLATFORM=offscreen for terminal-only queries, not UI validation. For isolated tests use --appdata /absolute/private/profile --noupgrade. Check Export's From selection (Source versus Timeline) and wait for the job to finish.",
            "Follow the desktop skill's media reference. Use ffprobe to check export duration, dimensions, frame rate and codecs, then inspect representative frames and audio in the consuming project.",
        ],
    },
    "audacity": {
        "workflows": ["audio editing", "sound effect touch-ups"],
        "instructions": [
            "Edit sound effects with {launch_prefix}audacity /absolute/project/sound.wav. Save an AUP3 project and export the game's or website's required audio format separately.",
            "A title wait can match the startup splash. Inspect the document and dismiss the Welcome dialog through observed controls; prefer accessible Effect menus and Export Audio controls. Scripting is optional and disabled by default.",
            "Follow the desktop skill's media reference. Verify trim, fades, clipping, loop boundaries, sample rate and channels; unavailable desktop playback does not establish that the export lacks audio.",
        ],
    },
    "musescore": {
        "workflows": ["score inspection", "MIDI notation comparison", "sheet music export"],
        "instructions": [
            "Inspect a licensed score or MIDI task copy with {launch_prefix}{executable} /absolute/task/score.mid. Save an editable MSCZ and export review PDF/MusicXML separately; retain the exact original MIDI bytes.",
            "Check the installed version and --help before scripting. Debian's package is MuseScore 3; native CachyOS uses its repository version. QT_QPA_PLATFORM=offscreen permits terminal-only queries and exports, but does not verify desktop readiness.",
            "The tested Debian MuseScore 3.2.3 GUI crashed with --no-synthesizer; omit that flag from interactive launches. It worked for isolated offscreen exports. A title wait can match the startup splash; use --exclude-title Startup and inspect the actual document.",
            "Follow the desktop skill's media reference and MUSIC_DEVELOPMENT.md guide. MIDI import derives notation: inspect quantization, voices, tempo, meter, ties and rests. A successful PDF export does not establish musical correctness or preserve original events.",
            "Keep exported artifacts and isolated preferences outside source assets. Do not bundle MuseScore's fonts, SoundFonts or samples without a separate provenance and license review.",
        ],
    },
    "ardour": {
        "workflows": ["audio production"],
        "instructions": ["Preserve the session directory, audio and plugin references. Export the intended range with explicit sample rate/channels; verify signal and clipping. Audio-device and plugin readiness need separate checks."],
    },
    "lmms": {
        "workflows": ["music production"],
        "instructions": ["Keep MMP/MMPZ sources, samples and plugin references. Export with explicit loop/range, sample rate and quality; check duration, peaks and representative playback."],
    },
    "scribus": {
        "workflows": ["page layout", "PDF production"],
        "instructions": ["Keep the editable SLA and linked images/fonts. Run document preflight, export the required PDF profile and inspect page size, bleed, fonts and rendered pages."],
    },
    "obs": {
        "workflows": ["desktop recording"],
        "instructions": ["Use a task-specific scene/profile and explicitly selected capture source. Verify a short recording's video and audio; Wayland screen selection can require portal consent. Preserve existing recordings and streaming configuration."],
    },
    "remmina": {
        "workflows": ["remote desktop connections"],
        "instructions": ["Use the project's intended connection and native protocol plugin. Test connectivity and Secret Service integration without copying credentials; installation does not create a remote desktop server."],
    },
}
TOOLS = (
    "git", "gh", "codex", "claude", "opencode", "node", "npm", "yarn",
    "pnpm", "corepack", "python3", "uv", "go", "gcc", "g++", "make",
    "cmake", "godot", "glxinfo", "apitrace", "ffmpeg", "ffprobe",
    "magick", "convert", "identify", "exiftool",
    "pdfinfo", "pdftoppm", "pdftotext",
    "sox", "soxi", "arecord", "aplay", "amidi", "aconnect", "pactl", "paplay", "parecord",
    *MUSESCORE_EXECUTABLES,
    "rg", "jq", "aws", "basaltwater-web", "basaltwater-playwright-mcp", "butler", "steamcmd", *DESKTOP_APPLICATIONS,
)
DESKTOP_GUIDE = "https://github.com/bluehexagons/basaltwater/blob/main/docs/DESKTOP_DEVELOPMENT.md"
PUBLISHING_GUIDE = "https://github.com/bluehexagons/basaltwater/blob/main/docs/GAME_PUBLISHING.md"
PUBLISHING_INSTRUCTIONS = [
    "Never request, read or copy publishing credentials into prompts, repositories or the controller. Tool presence does not verify provider authentication.",
    "After a completed export, use basaltw publish complete REPOSITORY ARTIFACT_SUBDIRECTORY INTERNAL_BUILD_ID, then prepare PROJECT_ID and upload ARTIFACT_ID. Upload only under the user's explicit request or standing unattended/scheduled authority.",
    "Agents may draft/translate and import unreviewed text with basaltw publish draft. Every public destination/language revision, including bundled release notes, requires human review. Do not alter approval records or click human review/confirmation controls.",
    "Steam default/public release and rollback remain manual on Steamworks. Steam announcements and itch.io posts currently use reviewed exports and human editor handoffs; do not claim an export published a post.",
    "Butler/SteamCMD storefront uploads and HTTPS game previews are separate publishing workflows. Use the returned destination and receipt for the selected workflow.",
]


def _publishing_instructions(native_desktop: bool) -> list[str]:
    if native_desktop:
        authentication = (
            "This is a native CachyOS workstation. Publishing management is qualified on Debian; "
            "no VM web panel is assumed here. When authentication is needed, the owner uses "
            "the installed provider's interactive terminal login (butler login or steamcmd +login BUILD_ACCOUNT). "
            "Do not put passwords or tokens in command arguments."
        )
    else:
        authentication = (
            "On a Debian publishing host, use Publishing in a configured HTTPS web panel for human sign-in, "
            "or basaltw publish auth login butler / steamcmd --username BUILD_ACCOUNT in the owner's interactive terminal. "
            "Panel availability is unverified; use basaltw publish status --json for saved management state."
        )
    return [authentication, *PUBLISHING_INSTRUCTIONS]


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
    # Canonical project requirements resolve distro aliases without launching Qt.
    tools["musescore"] = next((tools[name] for name in MUSESCORE_EXECUTABLES if tools[name]), None)
    requirements = {
        name: {"executable": tools[name], "status": "available" if tools[name] else "missing"}
        for name in project["required_tools"]
    }
    recipes = {
        name: {**recipe, "missing_tools": [tool for tool in recipe["requires"] if not tools[tool]]}
        for name, recipe in project["recipes"].items()
    }
    native_desktop = is_cachyos()
    launch_prefix = "" if native_desktop else "basaltw desktop exec -- "
    desktop = {
        name: {**guidance,
               "instructions": [instruction.format(launch_prefix=launch_prefix, executable=tools[name])
                                for instruction in guidance.get("instructions", [])],
               "launch_argv": ([tools[name]] if native_desktop else ["basaltw", "desktop", "exec", "--", tools[name]]),
               "desktop_backend": "native-session" if native_desktop else "shared-xrdp",
               "automation_command": "basaltw desktop --native" if native_desktop else "basaltw desktop",
               "executable": tools[name], "readiness": "unverified", "guide": DESKTOP_GUIDE}
        for name, guidance in DESKTOP_APPLICATIONS.items() if tools[name]
    }
    desktop_skills = [
        path for name in (("basaltwater-cachyos-desktop", "basaltwater-cachyos-workstation")
                         if native_desktop else ("basaltwater-desktop",))
        if os.path.isfile(path := os.path.join(home, ".agents", "skills", name, "SKILL.md"))
    ]
    state = agent_workspace._worktree_record(root)
    mappings = project["deployments"]
    return {
        "schema_version": 1,
        "host_profile": "cachyos-workstation" if native_desktop else "debian-managed",
        "tools": tools,
        "browser": {
            "preferred_provider": "active-session",
            "readiness": "session-dependent",
            "instructions": "Prefer browser tools exposed by the active agent session; in T3 Code use preview_status and preview_open before concluding the browser is unavailable.",
            "managed_playwright": {
                "executable": tools["basaltwater-playwright-mcp"],
                "readiness": "unverified" if tools["basaltwater-playwright-mcp"] else "not-on-path",
            },
        },
        "publishing": {
            "tools": {name: tools[name] for name in ("butler", "steamcmd")},
            "readiness": "unverified", "management_command": "basaltw publish status --json",
            "guide": PUBLISHING_GUIDE, "instructions": _publishing_instructions(native_desktop),
        },
        "desktop_applications": desktop,
        "desktop_skills": desktop_skills if desktop else [],
        "workspace": {
            "repository": root, "branch": state["branch"], "commit": state["head"],
            "dirty": state["dirty"], "repository_root": os.path.join(home, "repos"),
            "worktree_root": os.path.join(home, agent_workspace._DEFAULT_WORKTREE_RELATIVE),
            "task_branch_pattern": "agent/TASK",
            "legacy_browser_artifacts": os.path.join(home, ".local/state/basaltwater/playwright-mcp"),
            "artifact_directories": artifacts,
        },
        "project_source": project["source"],
        "required_tools": requirements,
        "recipes": recipes,
        "deployments": mappings,
        "current_deployment": mappings.get(state["branch"]),
        "undeclared_branches": [branch for branch in ("dev", "staging") if branch not in mappings],
        "health_command": "basaltw local cachyos-doctor --json" if native_desktop else "basaltw agent doctor --all-capabilities --json",
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
    publishing = result["publishing"]
    installed = [name for name, path in publishing["tools"].items() if path]
    print("Publishing tools: " + (", ".join(installed) if installed else "none on PATH") + " (readiness and authentication unverified)")
    if installed:
        print(f"Publishing guide: {publishing['guide']}")
        for instruction in publishing["instructions"]:
            print(f"  {instruction}")
    print(f"Worktrees: {workspace['worktree_root']} (agent/TASK)")
    browser = result["browser"]
    print(f"Browser: {browser['instructions']}")
    print(f"Managed Playwright: {browser['managed_playwright']['readiness']}")
    print(f"Legacy browser artifacts: {workspace['legacy_browser_artifacts']} (directory convention; not a browser capability)")
    for artifact in workspace["artifact_directories"]:
        print(f"Artifact directory: {artifact['path']} ({'ignored' if artifact['ignored'] else 'not ignored'})")
    for branch, mapping in result["deployments"].items():
        details = ", ".join(str(mapping[field]) for field in ("provider", "region", "url") if field in mapping)
        print(f"Deploy: {branch} -> {mapping['environment']}" + (f" ({details})" if details else ""))
    if result["undeclared_branches"]:
        print("Deployment mapping unknown: " + ", ".join(result["undeclared_branches"]) + f"; declare in {PROJECT_FILE}")
    print(f"Health: {result['health_command']} (tool presence is not a health or authentication check)")
    return 0
