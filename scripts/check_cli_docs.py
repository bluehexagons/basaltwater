#!/usr/bin/env python3
"""Check that the documented CLI entry points match the parser surface."""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import basaltwater


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "COMMAND_LINE.md"


def main() -> int:
    parser, _, _ = basaltwater.create_basaltwater_parser()
    documented = set(
        re.findall(
            r"^basaltw ([a-z][a-z0-9-]*)",
            DOC.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    )
    actions = parser._subparsers._group_actions[0]
    parsed = set(actions.choices)
    ignored_aliases = {
        "ls", "command", "remove", "admin-python", "self-setup",
        "mount", "umount", "health", "ssh", "push", "pull", "key",
        "df", "fan", "svc", "logs", "reachable",
    }
    missing = sorted((parsed - ignored_aliases) - documented)
    if missing:
        print("CLI commands missing from docs/COMMAND_LINE.md: " + ", ".join(missing))
        return 1
    print("CLI documentation check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
