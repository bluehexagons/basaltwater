"""Concise, read-only discovery of an agent's tools and project conventions."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import urllib.parse

from lib import agent_workspace
from lib.atomic_io import read_json_file
from lib.validation import validate_filesystem_path, validate_no_control_characters
from lib.validators import validate_host


PROJECT_FILE = "basaltwater-agent.json"
TOOLS = (
    "git", "gh", "codex", "claude", "opencode", "node", "npm", "yarn",
    "pnpm", "corepack", "python3", "uv", "go", "gcc", "g++", "make",
    "cmake", "godot", "glxinfo", "apitrace", "ffmpeg", "magick",
    "rg", "jq", "aws", "basaltwater-web",
)


def add_manifest_parser(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser("manifest", help="Show tools, workspace conventions and declared deployment mappings")
    parser.add_argument("repository", nargs="?", default=".")
    parser.add_argument("--json", action="store_true")


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError(f"{label} must be a non-empty string of at most 256 characters")
    validate_no_control_characters(value, label)
    return value


def load_project_environment(repository: str) -> dict[str, object]:
    """Load only explicit non-secret declarations; never infer deployment targets."""
    path = os.path.join(repository, PROJECT_FILE)
    try:
        data = read_json_file(path, max_bytes=64 * 1024)
    except FileNotFoundError:
        return {"source": None, "deployments": {}, "artifact_directories": []}
    if (
        not isinstance(data, dict) or type(data.get("version")) is not int
        or data["version"] != 1
        or set(data) - {"version", "deployments", "artifact_directories"}
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
    tools = {name: shutil.which(name) for name in TOOLS}
    state = agent_workspace._worktree_record(root)
    mappings = project["deployments"]
    return {
        "schema_version": 1,
        "tools": tools,
        "workspace": {
            "repository": root, "branch": state["branch"], "commit": state["head"],
            "dirty": state["dirty"], "repository_root": os.path.join(home, "repos"),
            "worktree_root": os.path.join(home, agent_workspace._DEFAULT_WORKTREE_RELATIVE),
            "task_branch_pattern": "agent/TASK",
            "browser_evidence": os.path.join(home, ".local/state/basaltwater/playwright-mcp"),
            "artifact_directories": artifacts,
        },
        "project_source": project["source"],
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
