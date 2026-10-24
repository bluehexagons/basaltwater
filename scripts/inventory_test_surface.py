#!/usr/bin/env python3
"""Print the declared CLI/profile surface as JSON without running commands.

This is an inventory, not a measurement of behavioral test coverage. Count
command aliases once, long-option spellings globally, and include command
groups as well as leaf commands. The root parser is excluded from path counts.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from basaltwater import create_basaltwater_parser
from lib.config import MACHINE_TYPES
from lib.plugin_registry import get_plugin_registry


def inventory(parser: argparse.ArgumentParser) -> dict:
    """Describe reachable parser paths, collapsing aliases at each branch."""
    commands = []
    long_options: set[str] = set()

    def visit(current: argparse.ArgumentParser, path: tuple[str, ...], aliases: list[str]) -> None:
        options = sorted({option for action in current._actions
                          for option in action.option_strings if option.startswith("--")})
        long_options.update(options)
        if path:
            commands.append({"path": list(path), "aliases": aliases, "options": options})
        for action in current._actions:
            if not isinstance(action, argparse._SubParsersAction):
                continue
            seen: set[int] = set()
            for name, child in action.choices.items():
                if id(child) in seen:
                    continue
                seen.add(id(child))
                child_aliases = [alias for alias, target in action.choices.items()
                                 if target is child and alias != name]
                visit(child, (*path, name), child_aliases)

    visit(parser, (), [])
    commands.sort(key=lambda item: item["path"])
    return {
        "counts": {
            "top_level_commands": sum(len(item["path"]) == 1 for item in commands),
            "command_paths": len(commands),
            "long_option_spellings": len(long_options),
        },
        "root_options": sorted(option for action in parser._actions
                               for option in action.option_strings if option.startswith("--")),
        "long_options": sorted(long_options),
        "commands": commands,
    }


def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()
    result = inventory(create_basaltwater_parser()[0])
    profiles = sorted(item.name for item in get_plugin_registry().system_types)
    result["counts"]["setup_profiles"] = len(profiles)
    result["setup_profiles"] = profiles
    result["machine_types"] = sorted(MACHINE_TYPES)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
