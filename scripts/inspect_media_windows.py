"""Optional native Windows file inspection entrypoint, independent of installation."""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.media_cli import main

if __name__ == "__main__":
    sys.exit(main())
