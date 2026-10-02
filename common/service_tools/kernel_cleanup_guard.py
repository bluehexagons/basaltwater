#!/usr/bin/python3
"""Verify Proxmox kernel-only removals immediately before APT invokes dpkg."""

from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."))

from lib.kernel_cleanup import obsolete_kernel_packages, validate_kernel_removal_actions


def main() -> int:
    """Fail closed on unexpected package actions or incomplete hook input."""
    try:
        payload = sys.stdin.read(8 * 1024 * 1024 + 1)
        if len(payload) > 8 * 1024 * 1024:
            raise ValueError("APT removal protocol is unexpectedly large")
        validate_kernel_removal_actions(payload, sys.argv[1:])
        if not set(sys.argv[1:]).issubset(obsolete_kernel_packages()):
            raise ValueError("Kernel boot-retention policy changed; retry cleanup later")
    except (OSError, subprocess.SubprocessError, ValueError, RuntimeError) as exc:
        print(f"Kernel cleanup stopped: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
