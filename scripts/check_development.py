#!/usr/bin/env python3
"""Offline source compilation and development-environment discovery."""

from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import py_compile
import sys


ROOT = Path(__file__).resolve().parents[1]
# Managed applications and dependency trees are not maintained project sources.
EXCLUDED = {".git", ".venv", "build", "dist", "artifacts", "__pycache__",
            "node_modules", "cachyos-t3"}
DEV_MODULES = ("aiohttp", "build", "coverage", "argcomplete")


def compile_sources(root: Path) -> int:
    """Compile maintained Python, without entering dependency trees or symlinks."""
    count = 0
    try:
        for directory, dirs, files in os.walk(root, onerror=_raise_walk_error):
            parent = Path(directory)
            dirs[:] = sorted(name for name in dirs
                             if name not in EXCLUDED and not name.endswith(".egg-info")
                             and not (parent / name).is_symlink()
                             and not (parent / name / "pyvenv.cfg").exists())
            for name in sorted(files):
                path = parent / name
                if path.suffix == ".py" and not path.is_symlink():
                    py_compile.compile(str(path), doraise=True)
                    count += 1
    except (OSError, py_compile.PyCompileError) as exc:
        print(f"Python compilation failed: {exc}", file=sys.stderr)
        return 1
    print(f"Compiled {count} maintained Python files")
    return 0


def _raise_walk_error(error: OSError) -> None:
    raise error


def check_dependencies() -> int:
    """Check discoverability only; pip resolves declared versions in the venv."""
    print(f"Development Python: {sys.executable}")
    missing = [name for name in DEV_MODULES if importlib.util.find_spec(name) is None]
    if missing:
        print(f"Missing development modules: {', '.join(missing)}", file=sys.stderr)
        print("Create/activate an isolated venv and install '.[dev,completion]'; "
              "see docs/agents/contributing/README.md. No packages were installed.",
              file=sys.stderr)
        return 1
    print("Development modules available (presence only; versions not verified)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("check", choices=("compile", "dependencies"))
    args = parser.parse_args(argv)
    return compile_sources(ROOT) if args.check == "compile" else check_dependencies()


if __name__ == "__main__":
    raise SystemExit(main())
