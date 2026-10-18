#!/usr/bin/env python3
"""Fixed entry point; installed root-owned, outside the runner workspace."""

from __future__ import annotations

import sys

sys.path.insert(0, "/opt/basaltwater")

from lib.github_runner_gate import main


if __name__ == "__main__":
    raise SystemExit(main(check=sys.argv[1:] == ["--check"]))
