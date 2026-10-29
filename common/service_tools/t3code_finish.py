#!/usr/bin/env python3
"""Independent Debian T3 completion entry point; never print application data."""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from common.t3code_completion import complete


if __name__ == "__main__":
    try:
        if len(sys.argv) != 4:
            raise ValueError("Invalid completion arguments")
        complete(sys.argv[1], int(sys.argv[2]), sys.argv[3])
    except Exception as exc:
        print(f"T3 completion failed ({type(exc).__name__}); inspect the managed user service.", file=sys.stderr)
        sys.exit(1)
