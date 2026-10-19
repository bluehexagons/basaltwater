#!/usr/bin/env python3
"""Fixed entry point; installed root-owned, outside the runner workspace."""

from __future__ import annotations

import sys
import argparse

sys.path.insert(0, "/opt/basaltwater")

from lib.github_runner_gate import main
from lib.github_runner_queue import hold_job_slot


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--instance", default="")
    parser.add_argument("--worker", type=int)
    parser.add_argument("--hold-queue", type=int, nargs=3)
    args = parser.parse_args()
    if args.hold_queue:
        raise SystemExit(hold_job_slot(*args.hold_queue))
    raise SystemExit(main(check=args.check, instance=args.instance, worker=args.worker))
