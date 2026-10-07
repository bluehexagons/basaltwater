"""Select an installed Node runtime from project pins and engine requirements."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile

from lib.atomic_io import read_json_file
from lib.node_runtime_ownership import mark_runtime, runtime_owner
from lib.validation import validate_filesystem_path, validate_no_control_characters


Version = tuple[int, int, int]
_VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")
_PARTIAL = re.compile(r"v?(\d+)(?:\.(\d+|[xX*]))?(?:\.(\d+|[xX*]))?")
_TOKEN = re.compile(r"(>=|<=|>|<|=|\^|~)?\s*(v?\d+(?:\.(?:\d+|[xX*])){0,2}|[xX*])")


def _version(text: str) -> Version:
    match = _VERSION.fullmatch(text.strip())
    if not match:
        raise ValueError(f"Expected a stable Node version, got {text!r}")
    return tuple(int(part) for part in match.groups())


def _bounds(text: str) -> tuple[Version, Version | None, int]:
    if text in {"*", "x", "X"}:
        return (0, 0, 0), None, 0
    match = _PARTIAL.fullmatch(text)
    if not match:
        raise ValueError(f"Unsupported Node version requirement: {text!r}")
    parts = []
    wildcard = False
    for part in match.groups():
        if part is None or part in {"x", "X", "*"}:
            wildcard = True
        elif wildcard:
            raise ValueError(f"Invalid Node version requirement: {text!r}")
        else:
            parts.append(int(part))
    lower = tuple(parts + [0] * (3 - len(parts)))
    upper = None
    if len(parts) == 1:
        upper = (parts[0] + 1, 0, 0)
    elif len(parts) == 2:
        upper = (parts[0], parts[1] + 1, 0)
    return lower, upper, len(parts)


def satisfies(version: Version, requirement: str) -> bool:
    """Support stable npm ranges without running a package manager or downloading."""
    validate_no_control_characters(requirement, "Node requirement")
    if not requirement.strip() or len(requirement) > 512:
        raise ValueError("Node requirement must contain 1–512 characters")
    alternatives = []
    for clause in requirement.split("||"):
        clause = clause.strip()
        hyphen = re.fullmatch(r"(\S+)\s+-\s+(\S+)", clause)
        if hyphen:
            lower, _, _ = _bounds(hyphen[1])
            end, upper, count = _bounds(hyphen[2])
            alternatives.append(version >= lower and (True if count == 0 else version < upper if count < 3 and upper else version <= end))
            continue
        tokens = list(_TOKEN.finditer(clause))
        if not tokens or _TOKEN.sub("", clause).strip():
            raise ValueError(f"Unsupported Node range: {requirement!r}; use a stable version or npm range")
        if any(not clause[left.end():right.start()] and not right.group(0)[0].isspace()
               for left, right in zip(tokens, tokens[1:])):
            raise ValueError(f"Separate Node range comparators with spaces: {requirement!r}")
        accepted = True
        for token in tokens:
            operator, value = token.groups()
            lower, upper, count = _bounds(value)
            if count == 0:
                matches = operator not in {'>', '<'}
            elif operator in {None, "="}:
                matches = version >= lower and (upper is None if count == 0 else version < upper if upper else version == lower)
            elif operator == ">=":
                matches = version >= lower
            elif operator == ">":
                matches = version >= upper if upper else version > lower
            elif operator == "<":
                matches = version < lower
            elif operator == "<=":
                matches = version < upper if upper else version <= lower
            elif operator == "~":
                ceiling = (lower[0] + 1, 0, 0) if count == 1 else (lower[0], lower[1] + 1, 0)
                matches = count == 0 or lower <= version < ceiling
            else:
                if lower[0] or count == 1:
                    ceiling = (lower[0] + 1, 0, 0)
                elif lower[1] or count == 2:
                    ceiling = (0, lower[1] + 1, 0)
                else:
                    ceiling = (0, 0, lower[2] + 1)
                matches = count == 0 or lower <= version < ceiling
            accepted = accepted and matches
        alternatives.append(accepted)
    return any(alternatives)


def _read_pin(path: Path) -> str | None:
    if not os.path.lexists(path):
        return None
    # Reuse the bounded regular-file reader for version files as well.
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError(f"Node pin must be a regular file: {path}")
        data = stream.read(4097)
    if len(data) > 4096:
        raise ValueError(f"Node pin is too large: {path}")
    lines = [line.split("#", 1)[0].strip() for line in data.decode().splitlines()]
    values = [line for line in lines if line]
    if len(values) != 1:
        raise ValueError(f"Node pin must contain one version: {path}")
    validate_no_control_characters(values[0], "Node pin")
    return values[0]


def project_requirements(project: str) -> tuple[str | None, str | None, str | None]:
    """Use the nearest pin and package engines, stopping at the repository root."""
    validate_filesystem_path(project, must_exist=True)
    current = Path(project).resolve()
    if not current.is_dir():
        raise ValueError("Node project must be a directory")
    pin = source = engines = None
    for directory in (current, *current.parents):
        if pin is None:
            for name in (".node-version", ".nvmrc"):
                value = _read_pin(directory / name)
                if value is not None:
                    pin, source = value, str(directory / name)
                    break
        package_path = directory / "package.json"
        if engines is None and os.path.lexists(package_path):
            package = read_json_file(str(package_path))
            if not isinstance(package, dict):
                raise ValueError(f"Expected an object in {package_path}")
            requirements = package.get("engines", {})
            if not isinstance(requirements, dict):
                raise ValueError(f"Invalid engines in {package_path}")
            engines = requirements.get("node")
            if engines is not None and not isinstance(engines, str):
                raise ValueError(f"Invalid Node engine in {package_path}")
        if os.path.lexists(directory / ".git"):
            break
    return pin, source, engines


def _resolve_alias(specification: str, nvm_dir: Path) -> str:
    for _ in range(8):
        if specification in {"node", "stable"}:
            return "*"
        if specification.startswith("lts/") or specification == "default":
            if specification != 'default' and not re.fullmatch(r'lts/(?:[a-z][a-z0-9-]*|\*)', specification):
                raise ValueError('Invalid NVM LTS alias')
            alias = _read_pin(nvm_dir / "alias" / specification)
            if alias is None:
                raise ValueError(f"NVM alias {specification!r} is unavailable; specify an installed Node version")
            specification = alias
        else:
            return specification
    raise ValueError("Node alias cycle")


@dataclass(frozen=True)
class NodeSelection:
    executable: str
    version: str
    source: str
    requirement: str
    engines: str | None
    nvm_dir: str
    runtime_source: str

    def environment(self, original: dict[str, str] | None = None) -> dict[str, str]:
        environment = dict(os.environ if original is None else original)
        versions = Path(self.nvm_dir) / "versions" / "node"
        paths = [p for p in environment.get("PATH", os.defpath).split(os.pathsep)
                 if not Path(p).is_relative_to(versions)]
        environment["PATH"] = os.pathsep.join([str(Path(self.executable).parent), *paths])
        return environment


def _probe_environment(directory: str) -> dict[str, str]:
    # npm rejects loading one path as both user and global configuration.
    user_config = Path(directory) / "user.npmrc"
    global_config = Path(directory) / "global.npmrc"
    user_config.write_text("")
    global_config.write_text("")
    return {
        "PATH": os.defpath, "HOME": directory, "LC_ALL": "C",
        "COREPACK_ENABLE_NETWORK": "0", "COREPACK_ENABLE_PROJECT_SPEC": "0",
        "NPM_CONFIG_USERCONFIG": str(user_config), "NPM_CONFIG_GLOBALCONFIG": str(global_config),
        "NPM_CONFIG_UPDATE_NOTIFIER": "false",
    }


def select_node(project: str = ".", *, version: str | None = None,
                nvm_dir: str | None = None) -> NodeSelection:
    pin, source, engines = project_requirements(project)
    nvm_root = Path(nvm_dir or os.environ.get("NVM_DIR") or Path.home() / ".nvm").resolve()
    specification = version or pin or engines or "default"
    path_default = specification == "default" and not os.path.lexists(nvm_root / "alias" / "default")
    requirement = "*" if path_default else _resolve_alias(specification, nvm_root) if specification != engines else specification
    # Parse every clause even if no runtime is installed.
    satisfies((0, 0, 0), requirement)
    if engines:
        satisfies((0, 0, 0), engines)
    candidates = []
    for path in (() if path_default else (nvm_root / "versions" / "node").glob("v*/bin/node")):
        if not path.is_file() or not os.access(path, os.X_OK):
            continue
        try:
            candidate = _version(path.parent.parent.name)
        except ValueError:
            continue
        if satisfies(candidate, requirement) and (not engines or satisfies(candidate, engines)):
            candidates.append((candidate, str(path)))
    if candidates:
        actual, executable = max(candidates)
    else:
        executable = shutil.which("node")
        actual = None
        if executable:
            with tempfile.TemporaryDirectory(prefix="basaltwater-node-selection-", dir="/tmp") as probe:
                result = subprocess.run([executable, "--version"], cwd=probe, env=_probe_environment(probe),
                                        capture_output=True, text=True, timeout=10, check=False)
            if result.returncode == 0:
                actual = _version(result.stdout)
        if actual is None or not satisfies(actual, requirement) or (engines and not satisfies(actual, engines)):
            raise ValueError(
                f"No installed Node matches {specification!r}"
                + (f" and engines {engines!r}" if engines else "")
                + f". Run basaltw node install --version {shlex.quote(version or pin or 'VERSION')} "
                "or install a compatible runtime through your host's package manager."
            )
    selected_by = "--version" if version else source or ("package.json engines" if engines else "PATH" if path_default else "NVM default")
    return NodeSelection(executable, ".".join(map(str, actual)), selected_by,
                         requirement, engines, str(nvm_root),
                         "nvm" if Path(executable).is_relative_to(nvm_root / "versions/node") else "path")


def inspect_project_node(project: str, *, version: str | None = None) -> dict[str, object]:
    """Check the selected interpreter/npm without running project scripts."""
    selection = select_node(project, version=version)
    npm_requirement = None
    current = Path(project).resolve()
    for directory in (current, *current.parents):
        package_path = directory / "package.json"
        if os.path.lexists(package_path):
            package = read_json_file(str(package_path))
            engines = package.get("engines", {}) if isinstance(package, dict) else None
            if not isinstance(engines, dict):
                raise ValueError("Invalid package engines for project Node diagnosis")
            npm_requirement = engines.get("npm")
            if npm_requirement is not None:
                if not isinstance(npm_requirement, str):
                    raise ValueError("Invalid npm engine requirement")
                satisfies((0, 0, 0), npm_requirement)
            break
        if os.path.lexists(directory / ".git"):
            break
    tools: dict[str, str | None] = {"node": None, "npm": None}
    issues: list[str] = []
    with tempfile.TemporaryDirectory(prefix="basaltwater-project-node-", dir="/tmp") as probe:
        environment = selection.environment(_probe_environment(probe))
        for name in tools:
            try:
                command = Path(selection.executable).with_name(name)
                result = subprocess.run([str(command), "--version"], cwd=probe, env=environment,
                                        capture_output=True, text=True, timeout=10, check=False)
                if result.returncode == 0:
                    tools[name] = ".".join(map(str, _version(result.stdout)))
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
            if tools[name] is None:
                issues.append("project_" + name + "_unusable")
    if tools["node"] and tools["node"] != selection.version:
        issues.append("project_node_version_mismatch")
    if tools["npm"] and npm_requirement and not satisfies(_version(tools["npm"]), npm_requirement):
        issues.append("project_npm_engine_mismatch")
    return {
        "healthy": not issues, "selection": asdict(selection), "tools": tools, "issues": issues,
        "maintenance_owner": (runtime_owner(selection.nvm_dir, selection.version)
                              if selection.runtime_source == "nvm" else None),
        "npm_requirement": npm_requirement,
    }


def add_node_subparser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("node", help="Select and run project-specific Node runtimes")
    commands = parser.add_subparsers(dest="node_command", required=True)
    for name in ("status", "doctor", "env", "exec", "install"):
        command = commands.add_parser(name, help={
            "status": "Inspect the runtime selected for a project",
            "doctor": "Probe the project's selected Node/npm outside repository configuration",
            "env": "Print shell exports for the selected runtime",
            "exec": "Run a command with the selected runtime",
            "install": "Install an explicit project runtime through managed NVM",
        }[name])
        command.add_argument("--project", default=".")
        command.add_argument("--version", help=(
            "Install a stable version, major/minor pin, node, or lts/*" if name == "install"
            else "Override the project pin with a version or range"
        ))
        if name in {"status", "doctor"}:
            command.add_argument("--json", action="store_true")
        if name == "exec":
            command.add_argument("argv", nargs=argparse.REMAINDER)
        if name == "install":
            command.add_argument("--package-manager", action="append", default=[], metavar="NAME@VERSION",
                                 help="Install an exact npm, pnpm, or yarn version for this runtime (repeatable)")


def run_node_command(args: argparse.Namespace) -> int:
    try:
        if args.node_command == "doctor":
            report = inspect_project_node(args.project, version=args.version)
            print(json.dumps(report, indent=2) if args.json else
                  f"Project Node {report['selection']['version']}: "
                  + ("healthy" if report["healthy"] else ", ".join(report["issues"])))
            return 0 if report["healthy"] else 1
        if args.node_command == "install":
            pin, _, engines = project_requirements(args.project)
            version = args.version or pin
            if not version or not re.fullmatch(r"v?\d+(?:\.\d+){0,2}|node|lts/[a-z*]+", version):
                raise ValueError("Install requires --version with a stable version, major/minor pin, node, or lts/*")
            if engines:
                satisfies((0, 0, 0), engines)
                if _VERSION.fullmatch(version) and not satisfies(_version(version), engines):
                    raise ValueError(f"Requested Node {version!r} does not satisfy project engines {engines!r}")
            managers = args.package_manager
            for manager in managers:
                if not re.fullmatch(r"(?:npm|pnpm|yarn)@\d+\.\d+\.\d+", manager):
                    raise ValueError("Package managers require an exact npm@VERSION, pnpm@VERSION, or yarn@VERSION")
            nvm = Path(os.environ.get("NVM_DIR") or Path.home() / ".nvm") / "nvm.sh"
            validate_filesystem_path(str(nvm))
            if not nvm.is_file():
                from lib.cachyos import is_cachyos

                if not is_cachyos():
                    raise ValueError("Managed NVM is unavailable; reconcile the host's saved Node setup")
                from common.cachyos_development import prepare_project_node_versions

                nvm = prepare_project_node_versions()
            # An existing maintenance install becomes a project runtime before
            # NVM or package-manager work can race with automatic cleanup.
            try:
                existing = select_node(args.project, version=version, nvm_dir=str(nvm.parent))
            except ValueError:
                existing = None
            if existing and Path(existing.executable).is_relative_to(nvm.parent.absolute() / "versions/node"):
                mark_runtime(nvm.parent, existing.version, owner="project")
            result = subprocess.run([
                "/bin/bash", "--noprofile", "--norc", "-c",
                '. "$1" --no-use && nvm install "$2"', "basaltwater-node-install", str(nvm), version,
            ], check=False)
            if result.returncode:
                return result.returncode
            selection = select_node(args.project, version=version, nvm_dir=str(nvm.parent))
            mark_runtime(nvm.parent, selection.version, owner="project")
            if managers:
                environment = selection.environment()
                environment['npm_config_engine_strict'] = 'true'
                npm = Path(selection.executable).with_name('npm')
                # Avoid reading a project's npm configuration or Corepack
                # package-manager policy during host tool installation.
                with tempfile.TemporaryDirectory(prefix='basaltwater-node-tools-') as temporary:
                    probe = subprocess.run([str(npm), '--version'], cwd=temporary, env=environment,
                                           capture_output=True, text=True, timeout=10, check=True)
                    npm_version = _version(probe.stdout)
                    # npm 12 blocks lifecycle scripts by default. Explicitly
                    # installing these exact tools also authorizes their own
                    # setup scripts, without allowing arbitrary dependencies.
                    script_policy = (['--allow-scripts=' + ','.join(manager.split('@')[0] for manager in managers)]
                                     if npm_version[0] >= 12 else [])
                    result = subprocess.run([str(npm), 'install', '--global', '--prefix',
                                             str(Path(selection.executable).parent.parent),
                                             '--ignore-scripts=false', *script_policy, *managers],
                                            cwd=temporary, env=environment, check=False)
                    if result.returncode:
                        return result.returncode
                    for manager in managers:
                        name, expected = manager.split('@')
                        command = Path(selection.executable).with_name(name)
                        probe = subprocess.run([str(command), '--version'], cwd=temporary, env=environment,
                                               capture_output=True, text=True, timeout=30, check=True)
                        if _version(probe.stdout) != _version(expected):
                            raise ValueError(f'{name} did not activate the requested version {expected}')
                return 0
            return 0
        selection = select_node(args.project, version=args.version)
        if args.node_command == "status":
            print(json.dumps(asdict(selection), indent=2) if args.json else
                  f"Node {selection.version}: {selection.executable}\n"
                  f"Runtime source: {selection.runtime_source.upper()}\n"
                  f"Selected by {selection.source} ({selection.requirement})")
        elif args.node_command == "env":
            print("export PATH=" + shlex.quote(selection.environment()["PATH"]))
        else:
            argv = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
            if not argv:
                raise ValueError("node exec requires a command after --")
            result = subprocess.run(argv, env=selection.environment(), check=False)
            return result.returncode
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 1
