#!/usr/bin/env python3
"""Recover a confirmed previous-boot reboot drain before build services start."""

from __future__ import annotations

import os
from pathlib import Path
import sys

if not sys.flags.isolated or os.geteuid() != 0:
    raise SystemExit("Build capacity recovery requires isolated root Python")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lib.maintenance_lock import maintenance_lock, recover_reboot_drain


def main() -> int:
    with maintenance_lock(build_slot=False) as acquired:
        if acquired:
            recover_reboot_drain()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
