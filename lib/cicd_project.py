"""Interpret project metadata and run workflows only under the build identity."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

if __package__ in {None, ""}:
    # The broker invokes this trusted file with Python isolated mode. Never add
    # the checkout, its PYTHONPATH, or its current directory to module lookup.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.manifest_init import UnsupportedProject, initialize_manifest
from lib.node_toolchain import project_requirements, select_node
from lib.project_manifest import WorkflowStep, load_manifest
from lib.validation import validate_filesystem_path


def prepare_project(workspace: str) -> dict:
    """Preserve a committed manifest or infer one in the disposable checkout."""
    try:
        result = initialize_manifest(workspace)
    except UnsupportedProject as exc:
        return {"status": "unrecognized", "guidance": [str(exc), "Using explicitly configured CI scripts."]}
    manifest = load_manifest(workspace)
    return {"status": result["status"], "stages": list(manifest.ci),
            "components": len(manifest.components), "guidance": result["guidance"]}


def _environment(directory: Path, extra: dict[str, str]) -> dict[str, str]:
    environment = dict(os.environ)
    # Packages may use project-specific Node even when NVM's default is older.
    pin, _, engines = project_requirements(str(directory))
    nvm = Path(environment.get('NVM_DIR') or Path.home() / '.nvm')
    if pin or engines or (directory / "package.json").is_file() or (nvm / 'nvm.sh').is_file():
        selection = select_node(str(directory))
        environment = selection.environment(environment)
        print(f"Using Node {selection.version} ({selection.source})", flush=True)
    environment.update(extra)
    return environment


def run_step(root: Path, step: WorkflowStep) -> int:
    directory = (root / step.directory).resolve()
    if not directory.is_relative_to(root) or not directory.is_dir():
        raise ValueError(f"Workflow directory must stay inside the checkout: {step.directory}")
    environment = _environment(directory, step.env)
    print(f"Running in {step.directory}: {json.dumps(step.argv)}", flush=True)
    return subprocess.run(step.argv, cwd=directory, env=environment, check=False).returncode


def run_stage(workspace: str, stage: str) -> int:
    root = Path(workspace).resolve()
    manifest = load_manifest(str(root))
    steps = manifest.ci.get(stage, []) if manifest else []
    # Older deployment manifests expose build hooks instead of CI stages.
    if manifest and not manifest.ci and stage == "build":
        for component in manifest.components:
            steps.extend(WorkflowStep(["/bin/bash", "--noprofile", "--norc", "-c", command],
                                      env=component.env) for command in component.build)
    if not steps:
        print(f"No manifest {stage} workflow; skipping.", flush=True)
    for step in steps:
        result = run_step(root, step)
        if result:
            return result
    return 0


def run_project_script(workspace: str, script: str) -> int:
    root = Path(workspace).resolve()
    path = Path(script) if os.path.isabs(script) else root / script
    path = path.resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("Script must be a regular file inside the checkout")
    return run_step(root, WorkflowStep(["/bin/bash", str(path)]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace")
    commands = parser.add_mutually_exclusive_group(required=True)
    commands.add_argument("--prepare", action="store_true")
    commands.add_argument("--stage", choices=("install", "build", "test"))
    commands.add_argument("--script")
    args = parser.parse_args()
    try:
        validate_filesystem_path(args.workspace, must_exist=True)
        if args.prepare:
            print(json.dumps(prepare_project(args.workspace), indent=2))
            return 0
        if args.stage:
            return run_stage(args.workspace, args.stage)
        return run_project_script(args.workspace, args.script)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Project workflow failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
