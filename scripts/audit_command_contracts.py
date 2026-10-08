#!/usr/bin/env python3
"""Inventory direct remote_utils.run contracts without executing target code."""

from __future__ import annotations

import argparse
import ast
from collections import Counter
import json
from pathlib import Path


SOURCE_DIRECTORIES = ("common", "deploy", "desktop", "game", "lib", "plugins", "security", "smb", "sync", "web")


def inventory_source(source: str, path: str) -> list[dict]:
    """Report imports, literal check policies and result consumption per call.

    This is a structural inventory, not a proof that a consumed result is
    checked correctly. Dynamic check policies and wrappers require review.
    """
    tree = ast.parse(source, filename=path)
    names: set[str] = {"run"} if path == "lib/remote_utils.py" else set()
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "lib.remote_utils":
                names.update(alias.asname or alias.name for alias in node.names if alias.name == "run")
            elif node.module == "lib":
                modules.update(alias.asname or alias.name for alias in node.names if alias.name == "remote_utils")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "lib.remote_utils":
                    modules.add(alias.asname or alias.name)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    records = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = ast.unparse(node.func)
        if function not in names and not any(function == module + ".run" for module in modules):
            continue
        policy = node.args[1] if len(node.args) > 1 else ast.Constant(True)
        for keyword in node.keywords:
            if keyword.arg == "check":
                policy = keyword.value
        consumed = not isinstance(parents.get(node), ast.Expr)
        if isinstance(policy, ast.Constant) and policy.value is True:
            contract = "required"
        elif isinstance(policy, ast.Constant) and policy.value is False:
            contract = "caller-managed" if consumed else "best-effort"
        else:
            contract = "delegated"
        if any(keyword.arg is None for keyword in node.keywords) and not any(
            keyword.arg == "check" for keyword in node.keywords
        ) and len(node.args) < 2:
            contract = "delegated"
        scope = []
        current = parents.get(node)
        while current is not None:
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                scope.append(current.name)
            current = parents.get(current)
        records.append({
            "path": path, "line": node.lineno,
            "scope": ".".join(reversed(scope)) or "<module>",
            "contract": contract, "result": "consumed" if consumed else "discarded",
            "command": ast.unparse(node.args[0])[:240] if node.args else "<keyword or expanded arguments>",
        })
    return sorted(records, key=lambda item: item["line"])


def inventory_repository(root: Path) -> list[dict]:
    paths = list(root.glob("*.py"))
    for directory in SOURCE_DIRECTORIES:
        paths.extend((root / directory).rglob("*.py"))
    return [
        record for path in sorted(paths)
        if "__pycache__" not in path.parts
        for record in inventory_source(path.read_text(encoding="utf-8"), path.relative_to(root).as_posix())
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit every call with its structural contract")
    parser.add_argument("--unchecked", action="store_true", help="Show only discarded best-effort results")
    args = parser.parse_args()
    records = inventory_repository(Path(__file__).resolve().parents[1])
    if args.unchecked:
        records = [record for record in records if record["contract"] == "best-effort"]
    if args.json:
        print(json.dumps(records, indent=2))
        return
    for record in records:
        print(f'{record["path"]}:{record["line"]} {record["scope"]}: '
              f'{record["contract"]} ({record["result"]}) {record["command"]}')
    print("Totals: " + ", ".join(f"{kind}={count}" for kind, count in sorted(Counter(
        record["contract"] for record in records
    ).items())))


if __name__ == "__main__":
    main()
