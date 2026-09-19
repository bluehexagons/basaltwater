#!/usr/bin/env python3
"""Check the installable package's launcher metadata and public command name."""

from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_RELEASE_VERSION = "2.0.0"


def main() -> int:
    with (ROOT / "pyproject.toml").open("rb") as file_obj:
        project = tomllib.load(file_obj)["project"]
    if project.get("version") != EXPECTED_RELEASE_VERSION:
        print(
            "pyproject.toml must declare the upcoming stable release version "
            f"{EXPECTED_RELEASE_VERSION}"
        )
        return 1
    scripts = project.get("scripts", {})
    if project.get("name") != "basaltwater":
        print("pyproject.toml must declare the basaltwater distribution")
        return 1
    if scripts.get("infra-tools") != "infra_tools:main":
        print("The v2 transition launcher infra-tools must remain available")
        return 1
    if scripts.get("basaltw") != "infra_tools:main":
        print("pyproject.toml must expose infra_tools:main as the basaltw entry point")
        return 1
    if {"infra_tools", "basalt", "bw", "b6"}.intersection(scripts):
        print("pyproject.toml must not add unapproved executable aliases")
        return 1
    command_reference = (ROOT / "docs" / "COMMAND_LINE.md").read_text(encoding="utf-8")
    if "basaltw setup" not in command_reference:
        print("docs/COMMAND_LINE.md must document the basaltw launcher")
        return 1
    print("Package metadata check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
