#!/usr/bin/env python3
"""Independent Debian T3 completion entry point; never print application data."""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from common.t3code_completion import CompletionError, complete


if __name__ == "__main__":
    try:
        if len(sys.argv) != 4:
            raise ValueError("Invalid completion arguments")
        complete(sys.argv[1], int(sys.argv[2]), sys.argv[3])
    except Exception as exc:
        issue = exc.issue if isinstance(exc, CompletionError) else "completion_failed"
        print(f"T3 completion failed ({issue}); inspect the private receipt and managed user service.", file=sys.stderr)
        sys.exit(1)
