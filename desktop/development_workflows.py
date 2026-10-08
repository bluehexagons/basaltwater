"""Read-only project declarations and explicit native workflow command plans."""

from __future__ import annotations

from pathlib import Path
import re
import sys

from lib.atomic_io import read_json_file
from lib.validation import validate_filesystem_path, validate_no_control_characters


MANAGERS = ("npm", "pnpm", "yarn")


def cli_prefix() -> list[str]:
    # Use this installation, even when the project contains basaltwater.py or
    # the invoking PATH resolves an older CLI. Do not import from project cwd.
    return [sys.executable, str(Path(__file__).resolve().parents[1] / "basaltwater.py")]


def package_info(project: Path) -> dict:
    path = project / "package.json"
    validate_filesystem_path(str(path), must_exist=True)
    package = read_json_file(str(path), max_bytes=1024 * 1024)
    if not isinstance(package, dict):
        raise ValueError("package.json must contain an object")
    scripts = package.get("scripts", {})
    if not isinstance(scripts, dict) or len(scripts) > 200:
        raise ValueError("package.json scripts must contain at most 200 entries")
    for name, command in scripts.items():
        if not isinstance(name, str) or not 1 <= len(name) <= 128 or not isinstance(command, str):
            raise ValueError("Package scripts require bounded names and string commands")
        validate_no_control_characters(name, "Package script name")
    declaration = package.get("packageManager")
    if declaration is not None:
        if not isinstance(declaration, str) or not 1 <= len(declaration) <= 256:
            raise ValueError("packageManager must be a string of at most 256 characters")
        validate_no_control_characters(declaration, "Package manager")
    engines = package.get("engines", {})
    if not isinstance(engines, dict):
        raise ValueError("Package engines must be an object")
    requirement = engines.get("node")
    if requirement is not None:
        if not isinstance(requirement, str) or not 1 <= len(requirement) <= 512:
            raise ValueError("Node requirement must contain 1–512 characters")
        validate_no_control_characters(requirement, "Node requirement")
    return {"scripts": sorted(scripts), "package_manager": declaration,
            "node_requirement": requirement, "runtime_readiness": "unverified"}


def node_command(project: Path, script: str, manager: str | None, arguments: list[str]) -> list[str]:
    if not isinstance(script, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:_.-]{0,127}", script):
        raise ValueError("Choose one literal package script name using letters, digits, colon, dot, underscore or hyphen")
    info = package_info(project)
    if script not in info["scripts"]:
        raise ValueError(f"Package script is not declared: {script}")
    if manager is None:
        declaration = info["package_manager"]
        if declaration is None:
            manager = "npm"
        else:
            match = re.fullmatch(r"(npm|pnpm|yarn)@[^\s@]+", declaration)
            if not match:
                raise ValueError("Unsupported packageManager; choose --manager npm, pnpm or yarn explicitly")
            manager = match[1]
    if manager not in MANAGERS:
        raise ValueError("Package manager must be npm, pnpm or yarn")
    command = [*cli_prefix(), "node", "exec", "--project", str(project), "--", manager, "run", script]
    if arguments:
        command.extend((["--"] if manager == "npm" else []) + arguments)
    return command


def check_command(project: Path, recipe: str, settings: str | None, timeout: int) -> list[str]:
    from lib.agent_environment import inspect_environment
    from lib.agent_visuals import _settings

    if not isinstance(recipe, str) or not 1 <= len(recipe) <= 256 or recipe.startswith("-"):
        raise ValueError("Recipe name must contain 1–256 characters and must not start with an option")
    validate_no_control_characters(recipe, "Recipe name")
    if type(timeout) is not int or not 1 <= timeout <= 3600:
        raise ValueError("Check timeout must be from 1 through 3600 seconds")
    if settings is not None:
        validate_filesystem_path(settings)
        settings = str(Path(settings).expanduser().resolve())
    _settings(settings)  # Validate before allocating a task or running project code.
    manifest = inspect_environment(str(project))
    declared = manifest["recipes"].get(recipe)
    if declared is None:
        raise ValueError(f"Declare recipe {recipe!r} in basaltwater-agent.json before running it")
    if declared["missing_tools"]:
        raise ValueError("Missing recipe tools: " + ", ".join(declared["missing_tools"]))
    cwd = (Path(manifest["workspace"]["repository"]) / declared["directory"]).resolve()
    if not cwd.is_relative_to(Path(manifest["workspace"]["repository"]).resolve()) or not cwd.is_dir():
        raise ValueError("Recipe working directory must be an existing directory within the repository")
    command = [*cli_prefix(), "agent", "visuals", "check", recipe,
               "--repository", str(project), "--timeout", str(timeout), "--json"]
    if settings is not None:
        command.extend(["--settings", settings])
    return command
