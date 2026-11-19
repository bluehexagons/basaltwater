#!/usr/bin/env python3
"""Check the installable package's launcher metadata and public command name."""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_VERSION_PATTERN = re.compile(r"\d+\.\d+\.\d+(?:rc[1-9]\d*)?")
RELEASE_TAG_PATTERN = re.compile(
    r"v(?P<stable>\d+\.\d+\.\d+)(?:-rc\.(?P<candidate>[1-9]\d*))?"
)


def expected_release_version() -> str | None:
    """Return the package version required by a release tag."""
    tag = os.environ.get("GITHUB_REF_NAME", "")
    match = RELEASE_TAG_PATTERN.fullmatch(tag)
    if match is None:
        return None
    candidate = match.group("candidate")
    stable = match.group("stable")
    return f"{stable}rc{candidate}" if candidate else stable


def main() -> int:
    with (ROOT / "pyproject.toml").open("rb") as file_obj:
        project = tomllib.load(file_obj)["project"]
    version = project.get("version")
    if not isinstance(version, str) or PACKAGE_VERSION_PATTERN.fullmatch(version) is None:
        print("pyproject.toml must declare a package version in X.Y.Z or X.Y.ZrcN form")
        return 1
    if os.environ.get("GITHUB_REF_TYPE") == "tag":
        expected_version = expected_release_version()
        if version != expected_version:
            print(f"pyproject.toml version must match the exact release tag "
                  f"(expected {expected_version or 'a vX.Y.Z or vX.Y.Z-rc.N tag'})")
            return 1
    scripts = project.get("scripts", {})
    if project.get("name") != "basaltwater":
        print("pyproject.toml must declare the basaltwater distribution")
        return 1
    if scripts.get("basaltw") != "basaltwater:main":
        print("pyproject.toml must expose basaltwater:main as the basaltw entry point")
        return 1
    if {"infra-tools", "infra_tools", "basaltwater", "basalt", "bw", "b6"}.intersection(scripts):
        print("pyproject.toml must not add unapproved executable aliases")
        return 1
    command_reference = (ROOT / "docs" / "reference" / "command-line.md").read_text(encoding="utf-8")
    if "basaltw setup" not in command_reference:
        print("docs/reference/command-line.md must document the basaltw launcher")
        return 1
    print("Package metadata check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
