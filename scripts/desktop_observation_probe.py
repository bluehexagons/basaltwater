#!/usr/bin/env python3
"""Disposable native Tk target for observation recipes; no global input hooks."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.validation import validate_filesystem_path


def new_path(value: str) -> Path:
    validate_filesystem_path(value, check_writable=True)
    path = Path(value)
    if not path.is_absolute() or path.exists() or path.is_symlink() or not path.parent.is_dir():
        raise ValueError("Use a new absolute path under an existing private parent")
    return path


def create(path: Path):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                         | getattr(os, "O_BINARY", 0), 0o600)
    return os.fdopen(descriptor, "w", encoding="utf-8", buffering=1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", required=True, help="New private JSONL file; logs only synthetic probe events")
    parser.add_argument("--block-output", help="Deliberately create this new PNG path after input to exercise capture failure")
    parser.add_argument("--small-controls", action="store_true",
                        help="Show adjacent small Advance/Decoy controls for targeting qualification")
    args = parser.parse_args(argv)
    try:
        log_path = new_path(args.log)
        blocked = new_path(args.block_output) if args.block_output else None
        if blocked == log_path or blocked is not None and blocked.suffix.lower() != ".png":
            raise ValueError("--block-output must be a different new .png path")
        import tkinter as tk
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            raise RuntimeError("Run the probe in the intended graphical session with Tk available") from exc
        root.title("Basaltwater observation probe")
        root.geometry("640x320+60+60")
        with create(log_path) as log:
            def record(event: str, **fields):
                log.write(json.dumps({"event": event, "at": datetime.now(timezone.utc).isoformat(),
                                      "monotonic_ns": time.monotonic_ns(), **fields}) + "\n")

            phase = tk.Label(root, text="Ready: inspect, then advance once", bg="#dddddd", font=("sans", 18), pady=40)
            phase.pack(fill="both", expand=True)

            def show(name, color):
                phase.config(text=name, bg=color)
                record("phase", phase=name)

            def advance():
                button.config(state="disabled")
                record("button")
                if blocked:
                    try:
                        with create(blocked) as fixture:
                            fixture.write("Synthetic occupied capture path; intentionally not a PNG.\n")
                        record("blocked-output-created")
                    except OSError as exc:
                        record("blocked-output-failed", error=type(exc).__name__)
                show("Immediate phase", "#f3ba50")
                root.after(300, lambda: show("Transition phase", "#91c5f6"))
                root.after(1200, lambda: show("Settled phase", "#96d99e"))

            def decoy():
                record("decoy")
                phase.config(text="Decoy selected: reinspect before recovery", bg="#ed9e9e")

            def close():
                record("closed")
                root.destroy()

            if args.small_controls:
                controls = tk.Frame(root)
                controls.pack(pady=12)
                button = tk.Button(controls, text="Advance", command=advance, font=("sans", 10))
                button.pack(side="left", padx=4)
                tk.Button(controls, text="Decoy", command=decoy, font=("sans", 10)).pack(side="left", padx=4)
            else:
                button = tk.Button(root, text="Advance once", command=advance, font=("sans", 16))
                button.pack(pady=12)
            tk.Button(root, text="Close probe", command=close).pack(pady=(0, 12))
            root.protocol("WM_DELETE_WINDOW", close)
            root.after(120000, close)
            record("opened")
            root.mainloop()
        return 0
    except (OSError, ValueError, ImportError, RuntimeError) as exc:
        print(f"Probe unavailable: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
