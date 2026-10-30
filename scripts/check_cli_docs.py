#!/usr/bin/env python3
"""Check that the documented CLI entry points match the parser surface."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import NoReturn

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "reference" / "command-line.md"


class RegistrationError(ValueError):
    """Parser registration cannot be established without executing source."""


def root_commands(repository: Path) -> set[str]:
    """Read literal root registrations and their builders, without importing CLI code.

    Deliberately support straight-line registration only. Any other use of the
    root subparsers must fail rather than produce an incomplete command list.
    """
    commands: set[str] = set()
    active: set[tuple[Path, str]] = set()

    def fail(path: Path, node: ast.AST, reason: str) -> NoReturn:
        raise RegistrationError(
            f"{path}:{getattr(node, 'lineno', 1)}: {reason}; "
            "use literal, straight-line root registrations or extend scripts/check_cli_docs.py"
        )

    def scan(path: Path, function: str, parameter: str | None = None) -> None:
        key = (path, function)
        if key in active:
            raise RegistrationError(f"{path}: recursive parser builder {function}")
        active.add(key)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        definitions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
        fn = definitions.get(function)
        if fn is None:
            fail(path, tree, f"cannot resolve parser builder {function}")
        if fn.decorator_list:
            fail(path, fn, "decorated parser builders are unsupported")
        declaration = None
        if parameter is None:
            # This is the root collection, not credentials or other nested commands.
            declarations = [
                node for node in fn.body
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
                and ast.unparse(node.value.func) == "parser.add_subparsers"
            ]
            if len(declarations) != 1:
                fail(path, fn, "cannot identify a unique root parser.add_subparsers assignment")
            declaration = declarations[0]
            if len(declaration.targets) != 1 or not isinstance(declaration.targets[0], ast.Name):
                fail(path, declaration, "unsupported root subparsers assignment")
            parameter = declaration.targets[0].id

        parents = {child: node for node in ast.walk(fn) for child in ast.iter_child_nodes(node)}
        calls: set[ast.Call] = set()
        for node in ast.walk(fn):
            if not isinstance(node, ast.Name) or node.id != parameter:
                continue
            parent = parents[node]
            if declaration is parent:
                continue
            if not isinstance(node.ctx, ast.Load):
                fail(path, node, "root subparsers reassignment is unsupported")
            if isinstance(parent, ast.Attribute) and isinstance(parents[parent], ast.Call):
                call = parents[parent]
                if call.func is not parent or parent.attr != "add_parser":
                    fail(path, node, "unsupported root subparsers method")
            elif isinstance(parent, ast.Call) and node in parent.args:
                call = parent
            else:
                fail(path, node, "unsupported root subparsers reference")
            # Reject loops, conditionals, closures and embedded expressions.
            statement = parents[call]
            if not isinstance(statement, (ast.Assign, ast.Expr)) or statement not in fn.body:
                fail(path, call, "dynamic root registration is unsupported")
            calls.add(call)

        imports = {
            alias.asname or alias.name: (node.module, alias.name)
            for node in tree.body if isinstance(node, ast.ImportFrom) and not node.level
            for alias in node.names
        }
        for call in sorted(calls, key=lambda node: node.lineno):
            if isinstance(call.func, ast.Attribute):
                keywords = {item.arg: item.value for item in call.keywords}
                if None in keywords or len(call.args) > 1 or (call.args and "name" in keywords):
                    fail(path, call, "unsupported add_parser arguments")
                name = call.args[0] if call.args else keywords.get("name")
                aliases = keywords.get("aliases", ast.List(elts=[], ctx=ast.Load()))
                if not isinstance(name, ast.Constant) or not isinstance(name.value, str):
                    fail(path, call, "root command name must be a literal string")
                if not isinstance(aliases, (ast.List, ast.Tuple)) or any(
                    not isinstance(item, ast.Constant) or not isinstance(item.value, str)
                    for item in aliases.elts
                ):
                    fail(path, call, "root aliases must be a literal list or tuple of strings")
                commands.add(name.value)
                commands.update(item.value for item in aliases.elts)
            else:
                if not isinstance(call.func, ast.Name) or len(call.args) != 1 or call.keywords:
                    fail(path, call, "unsupported parser builder call")
                builder = call.func.id
                target = path
                if builder not in definitions:
                    module, builder = imports.get(builder, (None, builder))
                    if module is None:
                        fail(path, call, f"cannot resolve parser builder {builder}")
                    target = repository.joinpath(*module.split(".")).with_suffix(".py")
                target_tree = ast.parse(target.read_text(encoding="utf-8"), filename=str(target))
                target_fn = next((node for node in target_tree.body
                                  if isinstance(node, ast.FunctionDef) and node.name == builder), None)
                if target_fn is None or not target_fn.args.args:
                    fail(path, call, f"cannot resolve parser builder parameter for {builder}")
                scan(target, builder, target_fn.args.args[0].arg)
        active.remove(key)

    scan(repository / "basaltwater.py", "create_basaltwater_parser")
    if not commands:
        raise RegistrationError("No root CLI registrations found; extend scripts/check_cli_docs.py")
    return commands


def main() -> int:
    try:
        parsed = root_commands(ROOT)
    except (RegistrationError, OSError, SyntaxError) as exc:
        print(f"CLI documentation check failed: {exc}", file=sys.stderr)
        return 1
    documented = set(
        re.findall(
            r"^basaltw ([a-z][a-z0-9-]*)",
            DOC.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    )
    ignored_aliases = {
        "ls", "command", "remove", "admin-python", "self-setup",
        "mount", "umount", "health", "ssh", "push", "pull", "key",
        "df", "fan", "svc", "logs", "reachable",
    }
    missing = sorted((parsed - ignored_aliases) - documented)
    if missing:
        print("CLI commands missing from docs/reference/command-line.md: " + ", ".join(missing))
        return 1
    print("CLI documentation check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
