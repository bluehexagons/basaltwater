"""Regression tests for shell-hardening behavior."""

from __future__ import annotations

import ast
from collections.abc import Iterator
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _python_sources(repo_root: Path) -> Iterator[Path]:
    """Prune generated/private trees before walking repository source."""
    for directory, children, files in os.walk(repo_root):
        children[:] = [
            name for name in children
            if not name.startswith(".") and name != "__pycache__"
            and not (Path(directory) == repo_root and name in {"build", "dist"})
        ]
        for name in files:
            if name.endswith(".py") and not name.startswith("."):
                yield Path(directory) / name


class TestShellSafety(unittest.TestCase):
    def test_generated_and_private_trees_are_pruned_but_nested_source_is_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            included = {"lib/module.py", "lib/build/generator.py"}
            excluded = {"build/stale.py", "dist/packed.py", ".venv/library.py",
                        ".private/secret.py", "lib/__pycache__/cached.py", ".hidden.py"}
            for relative in included | excluded:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")
            self.assertEqual({path.relative_to(root).as_posix() for path in _python_sources(root)}, included)

    def test_repo_python_sources_do_not_use_shell_true(self):
        repo_root = Path(__file__).resolve().parent.parent
        offenders: list[str] = []

        for path in _python_sources(repo_root):
            if path.name == "test_shell_safety.py":
                continue
            source = path.read_text(encoding="utf-8")
            # Non-ASCII identifiers can normalize to "shell" during parsing.
            if "shell" not in source and source.isascii():
                continue
            try:
                tree = ast.parse(source, filename=str(path))
            except SyntaxError as exc:
                self.fail(f"Failed to parse {path.relative_to(repo_root)}: {exc}")

            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if any(
                    keyword.arg == "shell"
                    and isinstance(keyword.value, ast.Constant)
                    and keyword.value.value is True
                    for keyword in node.keywords
                ):
                    offenders.append(f"{path.relative_to(repo_root)}:{node.lineno}")

        self.assertEqual(offenders, [], msg=f"Found shell=True call sites: {offenders}")


if __name__ == "__main__":
    unittest.main()
