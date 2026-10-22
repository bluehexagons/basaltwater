#!/usr/bin/env python3
"""Run Debian checks with bounded image-registry fallback on a hosted CI runner."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.validation import validate_filesystem_path
from lib.agent_probe import run_probe
from lib.node_toolchain import SUPPORTED_NODE_RANGE, is_supported_node

IMAGES = (
    "public.ecr.aws/docker/library/debian:trixie",
    "mirror.gcr.io/library/debian:trixie",
    "docker.io/library/debian:trixie",
)
CONTAINER_SCRIPT = """
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install --no-install-recommends -y \\
  ca-certificates fish git make python3-venv libsqlite3-0 libffi8 libstdc++6 libatomic1
git config --global --add safe.directory /workspace
"$BASALTWATER_CI_PYTHON" -m venv /opt/basaltwater-ci-python
export PATH=/opt/basaltwater-ci-python/bin:"$BASALTWATER_CI_NODE_DIR":/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
python3 --version
node --version
python3 -m pip install --upgrade pip
python3 -m pip install '.[dev,completion]'
"""


def _path(value: str) -> Path:
    validate_filesystem_path(value, must_exist=True)
    path = Path(value).resolve()
    validate_filesystem_path(str(path), must_exist=True)
    if not os.path.isabs(value) or any(char in str(path) for char in (",", ":")):
        raise ValueError("CI paths must be absolute and safe for Docker mounts")
    return path


def run_debian_ci(python: str, tool_cache: str, *, node: str, coverage: bool = False) -> int:
    """Try alternate pulls only; never repeat failed checks or setup commands."""
    executable, cache, workspace = _path(python), _path(tool_cache), _path(str(ROOT))
    node_executable = _path(node)
    if not cache.is_dir() or not executable.is_file() or not executable.is_relative_to(cache):
        raise ValueError("CI Python must be a file inside the runner tool cache")
    if not node_executable.is_file() or not node_executable.is_relative_to(cache):
        raise ValueError("CI Node must be a file inside the runner tool cache")
    probe = run_probe([str(node_executable), "--version"], cwd="/tmp",
                      env={"PATH": os.defpath, "LC_ALL": "C"}, timeout=10, max_bytes=4096)
    if probe.issue is not None or not is_supported_node(probe.output.strip()):
        raise ValueError(f"CI Node requires {SUPPORTED_NODE_RANGE}")
    image = None
    for candidate in IMAGES:
        print(f"Pulling Debian CI image: {candidate}", flush=True)
        try:
            result = subprocess.run(["docker", "pull", candidate], check=False, timeout=120)
        except subprocess.TimeoutExpired:
            continue
        if result.returncode == 0:
            image = candidate
            break
    if image is None:
        raise RuntimeError("All Debian image registries failed; checks were not started")
    name = f"basaltwater-ci-{os.getpid()}"
    script = CONTAINER_SCRIPT + ("make check-ci\n" if coverage else "make check\n")
    try:
        result = subprocess.run([
            "docker", "run", "--rm", "--name", name,
            "--mount", f"type=bind,source={workspace},target=/workspace",
            "--mount", f"type=bind,source={cache},target={cache},readonly",
            "--env", f"BASALTWATER_CI_PYTHON={executable}",
            "--env", f"BASALTWATER_CI_NODE_DIR={node_executable.parent}",
            "--env", f"LD_LIBRARY_PATH={executable.parent.parent / 'lib'}",
            "--workdir", "/workspace", image, "bash", "-euc", script,
        ], check=False, timeout=900)
        return result.returncode
    finally:
        # A timed-out Docker client can leave its container alive. Only remove
        # this invocation's named container; --rm normally already removed it.
        try:
            subprocess.run(["docker", "rm", "--force", name], check=False, timeout=30,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired):
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True, help="actions/setup-python executable")
    parser.add_argument("--tool-cache", required=True, help="Hosted runner's tool cache")
    parser.add_argument("--node", required=True, help="actions/setup-node executable inside the tool cache")
    parser.add_argument("--coverage", action="store_true")
    args = parser.parse_args()
    try:
        return run_debian_ci(args.python, args.tool_cache, node=args.node, coverage=args.coverage)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"Debian CI failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
