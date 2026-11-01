#!/usr/bin/env python3
"""Fixed isolated entrypoint for managed signed build events."""

from __future__ import annotations

from pathlib import Path
import sys

if not sys.flags.isolated:
    raise SystemExit("Use Python isolated mode")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lib.build_webhook_service import main

if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Use the managed webhook services")
    main(sys.argv[1])
