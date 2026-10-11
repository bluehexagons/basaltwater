#!/usr/bin/env python3
"""Fixed host supervisor entrypoint and its non-root review worker."""

from __future__ import annotations

import argparse
import json
import os
import sys

SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
if SOURCE_ROOT not in sys.path:
    sys.path.insert(0, SOURCE_ROOT)

from lib.maintenance_agent import run_review, run_supervisor


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", action="store_true")
    args = parser.parse_args()
    if args.review:
        content = sys.stdin.buffer.read(32769)
        if len(content) > 32768:
            raise ValueError("Review input exceeds its limit")
        print(json.dumps(run_review(json.loads(content)), ensure_ascii=False))
    else:
        run_supervisor()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
