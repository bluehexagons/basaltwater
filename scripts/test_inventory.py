#!/usr/bin/env python3
"""Inventory test structure and validate suite ownership without importing tests."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def selection_errors(root: Path, patterns: dict[str, tuple[str, ...]], suites: dict[str, list[str]]) -> list[str]:
    """Check every suite and pattern without parsing or importing test modules."""
    discovered = {".".join(path.relative_to(root).with_suffix("").parts)
                  for path in (root / "tests").rglob("test_*.py") if path.is_file()}
    owned = {selector for name in patterns for selector in suites.get(name, [])}
    errors = [f"Unowned test module: {selector}" for selector in sorted(discovered - owned)]
    for name, selectors in sorted(suites.items()):
        if name != "all" and not selectors:
            errors.append(f"Empty suite: {name}")
        for selector in selectors:
            if selector not in discovered:
                errors.append(f"Stale selector in {name}: {selector}")
    for name, globs in sorted(patterns.items()):
        for pattern in globs:
            if not any(path.is_file() for path in root.glob(pattern)):
                errors.append(f"Empty pattern in {name}: {pattern}")
    return errors


def inventory(
    root: Path, patterns: dict[str, tuple[str, ...]], suites: dict[str, list[str]],
) -> dict:
    """Report static definitions, candidate cost sites, and broken selections.

    Cost sites are review candidates, not proof of real I/O or redundant tests.
    Definitions include helper functions and do not equal unittest's run count.
    """
    modules = []
    errors = selection_errors(root, patterns, suites)
    for path in sorted((root / "tests").rglob("test_*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        selector = ".".join(path.relative_to(root).with_suffix("").parts)
        owners = sorted(name for name in patterns if selector in suites.get(name, []))
        sites = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute) and node.func.attr in {"sleep", "Popen", "run", "serve_forever"}:
                function = ast.unparse(node.func)
                sites.append({"line": node.lineno, "call": function})
        modules.append({
            "module": selector,
            "lines": len(source.splitlines()),
            "definitions": sum(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                               and node.name.startswith("test") for node in ast.walk(tree)),
            "domains": owners,
            "cost_candidates": sorted(sites, key=lambda site: site["line"]),
        })
    return {
        "modules": len(modules),
        "definitions": sum(module["definitions"] for module in modules),
        "lines": sum(module["lines"] for module in modules),
        "suite_modules": {name: len(selectors) for name, selectors in sorted(suites.items()) if name != "all"},
        "cross_domain": [module["module"] for module in modules if len(module["domains"]) > 1],
        "errors": errors,
        "inventory": modules,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Include per-module structure and candidate cost sites")
    args = parser.parse_args()
    from run_tests import TEST_SUITE_PATTERNS, TEST_SUITES

    report = inventory(ROOT, TEST_SUITE_PATTERNS, TEST_SUITES)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Test inventory: {report['modules']} modules, {report['definitions']} definitions, {report['lines']} lines")
        print(f"Cross-domain modules: {len(report['cross_domain'])} (legitimate overlap is retained)")
        for error in report["errors"]:
            print(error, file=sys.stderr)
        if not report["errors"]:
            print("Suite ownership, selectors and patterns are current")
    return int(bool(report["errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
