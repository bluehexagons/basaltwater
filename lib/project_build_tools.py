"""Shared source-marker discovery and installed Node command construction."""

from __future__ import annotations

from collections.abc import Iterator
import os
from pathlib import Path
import shlex
import tempfile

from lib.atomic_io import read_json_file
from lib.agent_probe import run_probe
from lib.node_project import select_package_manager
from lib.node_toolchain import inspect_project_node

from lib.validation import validate_filesystem_path


BUILD_NODE_HOME = "/opt/basaltwater-build-tools"
BUILD_NVM_DIR = BUILD_NODE_HOME + "/.nvm"
RUNTIME_MARKERS = {
    "go": {"go.mod"},
    "node": {"package.json", ".node-version", ".nvmrc"},
    "python": {"pyproject.toml", "requirements.txt", "uv.lock"},
}


def _source_markers(source_path: str, markers: set[str]) -> Iterator[str]:
    validate_filesystem_path(source_path, must_exist=True)
    if not os.path.isdir(source_path) or os.path.islink(source_path):
        raise ValueError("Build source must be a directory without links")
    ignored = {".git", ".basaltwater", ".venv", "node_modules", "vendor"}

    def failed(error: OSError) -> None:
        raise error

    for current, directories, filenames in os.walk(source_path, onerror=failed):
        directories[:] = [name for name in directories if name not in ignored
                          and not os.path.islink(os.path.join(current, name))]
        for name in markers.intersection(filenames):
            path = os.path.join(current, name)
            if os.path.isfile(path) and not os.path.islink(path):
                yield name


def source_has_marker(source_path: str, markers: set[str]) -> bool:
    return next(_source_markers(source_path, markers), None) is not None


def source_runtimes(source_path: str) -> list[str]:
    """Infer all supported runtimes in one fresh traversal of the source tree."""
    runtime_by_marker = {marker: name for name, markers in RUNTIME_MARKERS.items() for marker in markers}
    found: set[str] = set()
    for marker in _source_markers(source_path, set(runtime_by_marker)):
        found.add(runtime_by_marker[marker])
        if len(found) == len(RUNTIME_MARKERS):
            break
    return [name for name in RUNTIME_MARKERS if name in found]


def node_cli() -> list[str]:
    return ["/usr/bin/python3", "-I", str(Path(__file__).resolve().parents[1] / "basaltwater.py"), "node"]


def check_node_projects(source_path: str) -> None:
    """Check installed Node/npm and package-manager policy without provisioning.

    Called only in the unprivileged supervised helper. Conventional nested
    frontend projects share the ordinary deployment provisioning boundary.
    """
    projects = [Path(source_path)]
    projects.extend(Path(source_path) / name for name in ("frontend", "client", "web")
                    if (Path(source_path) / name / "package.json").is_file())
    for project in projects:
        report = inspect_project_node(str(project))
        if not report["healthy"]:
            raise ValueError("Installed Node/npm does not satisfy project policy")
        package_path = project / "package.json"
        if not package_path.is_file():
            continue
        package = read_json_file(str(package_path))
        manager = select_package_manager(project, package)
        executable = Path(report["selection"]["executable"]).with_name(manager.name)
        with tempfile.TemporaryDirectory(prefix="basaltwater-build-manager-") as probe:
            user_config = Path(probe) / "user.npmrc"
            global_config = Path(probe) / "global.npmrc"
            user_config.write_text("")
            global_config.write_text("")
            result = run_probe([str(executable), "--version"], cwd=probe, timeout=10,
                               max_bytes=4096, env={
                                   "PATH": str(executable.parent) + ":" + os.defpath,
                                   "HOME": probe, "LANG": "C.UTF-8",
                                   "COREPACK_ENABLE_NETWORK": "0",
                                   "COREPACK_ENABLE_PROJECT_SPEC": "0",
                                   "NPM_CONFIG_USERCONFIG": str(user_config),
                                   "NPM_CONFIG_GLOBALCONFIG": str(global_config),
                               })
        declared = package.get("packageManager", "")
        expected = declared.partition("@")[2].partition("+")[0]
        if result.issue or (expected and result.output.strip() != expected):
            raise ValueError("Install the project's exact package manager during builder setup")


def node_build_command(command: str, source_path: str, cli: list[str]) -> str:
    """Select installed Node and preserve generated nested `nvm use` hooks."""
    node_env = shlex.join([*cli, "env"])
    node_command = (
        'nvm() {\n'
        '  [ "${1-}" = use ] || return 2\n'
        '  shift\n'
        '  if [ "$#" -eq 0 ]; then\n'
        f'    node_exports=$({node_env} --project "$PWD") || return\n'
        '  elif [ "$#" -eq 1 ]; then\n'
        f'    node_exports=$({node_env} --project "$PWD" --version "$1") || return\n'
        '  else return 2; fi\n'
        '  eval "$node_exports"\n'
        '}\n' + command
    )
    return shlex.join([*cli, "exec", "--project", source_path, "--",
                       "/bin/bash", "--noprofile", "--norc", "-c", node_command])
