"""Collect live TLS trust in a process bounded by the panel's deadline."""

from __future__ import annotations

import argparse
import json
import os
import sys


SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if SOURCE_ROOT not in sys.path:
    sys.path.insert(0, SOURCE_ROOT)

from common.service_tools.basaltwater_web import _certificate_trust


def main(argv: list[str] | None = None) -> int:
    """Inspect the validated policy passed by the local panel process."""
    parser = argparse.ArgumentParser(description="Collect web panel TLS trust")
    parser.add_argument("policy", type=json.loads)
    args = parser.parse_args(argv)
    print(json.dumps(_certificate_trust(args.policy)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
