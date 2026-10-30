"""basaltwater - Automated setup scripts for remote Linux systems."""

from __future__ import annotations

import sys

if sys.version_info < (3, 11):
    raise RuntimeError("Basaltwater requires Python 3.11 or newer; use a supported system Python.")
