"""Infer reviewable project manifests without executing repository code."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import tempfile

from lib.atomic_io import read_json_file, write_json_atomic
from lib.project_manifest import MANIFEST_FILENAME, load_manifest, parse_manifest
from lib.validation import validate_filesystem_path


KINDS = ("auto", "node-site", "node-package", "node-service", "go-service", "full-stack", "static", "publishing")
GUIDE = "https://github.com/bluehexagons/basaltwater/blob/main/docs/PROJECT_TOOLING.md"
_SITE_TOOLS = {"vite", "astro", "react-scripts", "@angular/cli", "@sveltejs/kit"}
_SERVER_TOOLS = {"express", "fastify", "koa", "next", "nuxt", "@nestjs/core"}


class UnsupportedProject(ValueError):
    """No structure can be inferred; explicit legacy CI scripts may still work."""


def _package(directory: Path) -> dict | None:
    path = directory / "package.json"
    if not os.path.lexists(path):
        return None
    data = read_json_file(str(path))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: package metadata must be an object")
    if not isinstance(data.get("scripts", {}), dict):
        raise ValueError(f"{path}: scripts must be an object")
    for field in ("dependencies", "devDependencies"):
        if not isinstance(data.get(field, {}), dict):
            raise ValueError(f"{path}: {field} must be an object")
    return data


def _node_kind(package: dict) -> str:
    dependencies = set(package.get("dependencies", {})) | set(package.get("devDependencies", {}))
    scripts = package.get("scripts", {})
    if "electron" in dependencies:
        return "node-package"
    if dependencies & _SERVER_TOOLS and "start" in scripts:
        return "node-service"
    if dependencies & _SITE_TOOLS:
        return "node-site"
    return "node-package"


def _go_entrypoint(directory: Path) -> str | None:
    if not (directory / "go.mod").is_file():
        return None
    candidates = [directory, *(directory / "cmd").glob("*")]
    entries = []
    for candidate in candidates:
        for source in candidate.glob("*.go"):
            if source.name.endswith("_test.go") or source.is_symlink():
                continue
            with source.open("r", encoding="utf-8") as stream:
                if re.search(r"^\s*package\s+main\b", stream.read(64 * 1024), re.MULTILINE):
                    entries.append("." if candidate == directory else "./" + candidate.relative_to(directory).as_posix())
                    break
    if len(entries) > 1:
        raise ValueError("Multiple Go entry points found; declare the intended component build commands explicitly")
    return entries[0] if entries else None


def _manager(directory: Path, package: dict) -> tuple[list[str], list[str]]:
    declared = package.get("packageManager", "")
    if not isinstance(declared, str):
        raise ValueError("packageManager must be a string")
    name = declared.split("@", 1)[0] if declared else None
    locks = [manager for manager, filenames in (
        ("npm", ("package-lock.json", "npm-shrinkwrap.json")),
        ("pnpm", ("pnpm-lock.yaml",)), ("yarn", ("yarn.lock",)),
    ) if any((directory / filename).is_file() for filename in filenames)]
    if len(locks) > 1 or (name and locks and name not in locks):
        raise ValueError("Conflicting package-manager declarations or lockfiles; choose one before initialization")
    name = name or (locks[0] if locks else "npm")
    if name == "npm":
        return ["npm", "ci" if locks else "install"], ["npm", "run"]
    if name == "pnpm":
        return ["pnpm", "install", *(["--frozen-lockfile"] if locks else [])], ["pnpm", "run"]
    if name == "yarn":
        major = declared.removeprefix("yarn@").split(".", 1)[0]
        flag = "--frozen-lockfile" if major == "1" or not declared else "--immutable"
        return ["yarn", "install", *([flag] if locks else [])], ["yarn", "run"]
    raise ValueError(f"Unsupported package manager: {name!r}")


def _step(argv: list[str], directory: str = ".") -> dict:
    return {"argv": argv, **({"directory": directory} if directory != "." else {})}


def _node_workflows(directory: Path, relative: str, package: dict) -> dict:
    install, runner = _manager(directory, package)
    scripts = package.get("scripts", {})
    result = {"install": [_step(install, relative)]}
    build = next((name for name in ("build", "compile") if name in scripts), None)
    test = next((name for name in ("check", "test") if name in scripts), None)
    if build:
        result["build"] = [_step([*runner, build], relative)]
    if test:
        result["test"] = [_step([*runner, test], relative)]
    return result


def _node_component(directory: Path, relative: str, package: dict, kind: str, name: str = "site") -> dict | None:
    if kind == "node-package":
        return None
    prefix = "" if relative == "." else relative + "/"
    scripts = package.get("scripts", {})
    if kind == "node-service":
        start = scripts.get("start")
        if not isinstance(start, str) or not start.strip():
            raise ValueError("A Node service needs a start script; add one before initialization")
        return {"name": name, "type": "service", "domain": "{{domain}}", "port": "auto",
                "working_dir": relative, "exec": "/usr/bin/env " + shlex.join(_manager(directory, package)[1][:1]) + " start",
                "runtime_env": {"HOST": "127.0.0.1", "PORT": "{{port}}"}}
    output = "build" if "react-scripts" in (set(package.get("dependencies", {})) | set(package.get("devDependencies", {}))) else "dist"
    build = scripts.get("build", "")
    if isinstance(build, str):
        match = re.search(r"--outDir(?:=|\s+)([A-Za-z0-9_./-]+)", build)
        if match:
            output = match[1]
    return {"name": name, "type": "static", "domain": "{{domain}}", "output": prefix + output}


def propose_manifest(repository: str = ".", *, kind: str = "auto") -> dict:
    validate_filesystem_path(repository, must_exist=True)
    root = Path(repository).resolve()
    if not root.is_dir():
        raise ValueError("Manifest repository must be a directory")
    if kind not in KINDS:
        raise ValueError(f"Unknown project kind: {kind}")
    if kind == "publishing":
        manifest = {"version": 1, "components": [], "publishing": {"languages": {"source": "en", "supported": ["en"]}}}
        parse_manifest(manifest)
        return {"kind": kind, "manifest": manifest, "guidance": [
            "Add Spanish or other communication languages explicitly; every public revision requires human review.",
            "This manifest has no deployment components; publishing accounts, reviews and jobs stay on the VM."], "guide": GUIDE}
    package = _package(root)
    go = _go_entrypoint(root)
    frontend = next(((path, metadata) for name in ("frontend", "client", "web")
                     if (path := root / name).is_dir() and not path.is_symlink() and (metadata := _package(path))
                     and _node_kind(metadata) == "node-site"), None)
    node_kind = _node_kind(package) if package else None
    inferred = "full-stack" if frontend and (go or node_kind == "node-service") else "go-service" if go else node_kind
    static = next((name for name in (".", "public", "static", "html")
                   if (root / name / "index.html").is_file()), None)
    selected = (inferred or ("static" if static else None)) if kind == "auto" else kind
    if not selected:
        raise UnsupportedProject("No supported project structure found. Add project metadata or choose --kind; see " + GUIDE)
    guidance = ["Review the generated commands and output paths, then commit basaltwater.json.",
                "Set domains/routes, service arguments, health checks, and persistent-state/secret references before deployment.",
                "Declare .node-version or .nvmrc for a reproducible Node runtime; use basaltw node status to inspect it."]
    components = []
    ci = {}
    if package:
        ci = _node_workflows(root, ".", package)
    if selected == "go-service" or selected == "full-stack" and go:
        if not go:
            raise ValueError("A Go service needs go.mod and one package main at the root or under cmd/")
        binary = ".basaltwater/bin/app"
        components.append({"name": "app", "type": "service", "domain": "{{domain}}", "port": "auto",
                           "binary": binary, "runtime_env": {"HOST": "127.0.0.1", "PORT": "{{port}}", "LISTEN_ADDR": "127.0.0.1:{{port}}"}})
        ci.setdefault("build", []).extend([_step(["mkdir", "-p", ".basaltwater/bin"]),
                                            _step(["go", "build", "-trimpath", "-o", binary, go])])
        ci.setdefault("test", []).append(_step(["go", "test", "./..."]))
        guidance.append("Confirm the Go server honors the generated loopback host and port environment variables; adapt exec/arguments if it uses flags.")
    if selected == "full-stack":
        if not frontend:
            raise ValueError("Full-stack inference needs a Go/Node server and frontend/client/web Node site")
        if not go:
            if not package or node_kind != "node-service":
                raise ValueError("Full-stack inference needs a Go or Node server at the repository root")
            components.append(_node_component(root, ".", package, "node-service", "app"))
        directory, metadata = frontend
        relative = directory.relative_to(root).as_posix()
        for stage, steps in _node_workflows(directory, relative, metadata).items():
            ci.setdefault(stage, []).extend(steps)
        components[0]["domain"] = "api.{{domain}}"
        components.append(_node_component(directory, relative, metadata, "node-site"))
        guidance.append("The API uses api.{{domain}}; choose an API hostname or path and configure the frontend's API/base URL.")
    elif selected.startswith("node-"):
        if not package:
            raise ValueError("A Node project needs package.json")
        component = _node_component(root, ".", package, selected)
        if component:
            components.append(component)
        else:
            guidance.append("This package has CI workflows and no deployment components; do not serve its source tree as a website.")
        if selected == "node-service":
            guidance.append("Install and select Node for the service account; review its executable/PATH and loopback binding before deploying.")
    elif selected == "static":
        if static is None:
            raise ValueError("A static site needs an index.html at the root, public/, static/, or html/")
        components.append({"name": "site", "type": "static", "domain": "{{domain}}", "output": static})
    # Direct deploys also build their components. Keep the inferred commands in
    # CI and component hooks; CI with explicit stages does not run hooks twice.
    for component in components:
        commands = []
        component_directory = "."
        if component["name"] == "site" and selected == "full-stack":
            component_directory = frontend[0].relative_to(root).as_posix()
        for stage in ("install", "build"):
            for step in ci.get(stage, []):
                if step.get("directory", ".") != component_directory:
                    continue
                command = shlex.join(step["argv"])
                step_directory = root / step.get("directory", ".")
                if (step_directory / ".nvmrc").is_file() and "go" not in step["argv"][:1] and "mkdir" not in step["argv"][:1]:
                    command = 'if command -v nvm >/dev/null 2>&1; then nvm use; fi && ' + command
                if step.get("directory", ".") != ".":
                    command = "cd " + shlex.quote(step["directory"]) + " && " + command
                commands.append(command)
        if commands:
            component["build"] = commands
    manifest = {"version": 1, "components": components, **({"ci": ci} if ci else {})}
    parse_manifest(manifest)
    return {"kind": selected, "manifest": manifest, "guidance": guidance, "guide": GUIDE}


def initialize_manifest(repository: str = ".", *, kind: str = "auto", dry_run: bool = False) -> dict:
    validate_filesystem_path(repository, must_exist=True)
    root = Path(repository).resolve()
    existing = load_manifest(str(root))
    if existing is not None:
        return {"status": "existing", "path": str(root / MANIFEST_FILENAME), "guidance": ["Existing manifest preserved; edit it explicitly."], "guide": GUIDE}
    result = propose_manifest(str(root), kind=kind)
    result.update(status="preview" if dry_run else "created", path=str(root / MANIFEST_FILENAME))
    if not dry_run:
        # Publish a complete file exclusively. Existing files, including links,
        # win a concurrent initialization and are never overwritten.
        with tempfile.TemporaryDirectory(prefix=".basaltwater-init-", dir=root) as temporary:
            source = str(Path(temporary) / MANIFEST_FILENAME)
            write_json_atomic(source, result["manifest"], mode=0o644)
            os.link(source, result["path"], follow_symlinks=False)
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    return result


def add_project_manifest_subparser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("manifest", help="Initialize or validate a project manifest")
    commands = parser.add_subparsers(dest="manifest_command", required=True)
    for name in ("init", "validate"):
        command = commands.add_parser(name)
        command.add_argument("repository", nargs="?", default=".")
        command.add_argument("--json", action="store_true")
        if name == "init":
            command.add_argument("--kind", choices=KINDS, default="auto")
            command.add_argument("--dry-run", action="store_true")


def run_project_manifest_command(args: argparse.Namespace) -> int:
    try:
        if args.manifest_command == "init":
            result = initialize_manifest(args.repository, kind=args.kind, dry_run=args.dry_run)
        else:
            manifest = load_manifest(args.repository)
            if manifest is None:
                raise ValueError("No basaltwater.json found; run basaltw manifest init")
            result = {"status": "valid", "components": len(manifest.components), "ci_stages": list(manifest.ci), "publishing": manifest.publishing}
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Manifest: {result['status']}" + (f" ({result['path']})" if "path" in result else ""))
            if "manifest" in result:
                print(json.dumps(result["manifest"], indent=2))
            for instruction in result.get("guidance", []):
                print("- " + instruction)
            if "guide" in result:
                print("Guide: " + result["guide"])
        return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}) if args.json else f"Error: {exc}")
        return 1
