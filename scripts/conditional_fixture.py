#!/usr/bin/env python3
"""Disposable Qt scenes and app-local input counts for manual native qualification."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from desktop.qa_artifacts import new_bundle, stamp
from desktop.record_bundles import _read
from lib.atomic_io import parse_json, write_json_atomic

COLORS = {"red": "#ef3030", "blue": "#3060ef", "gray": "#888888"}


def read_control(folder: Path) -> dict:
    """Accept only bounded private scene setup or cooperative quit commands."""
    path = folder / "control.json"
    try:
        path.lstat()
    except FileNotFoundError:
        return {}
    value = parse_json(_read(path, 4096, private=True))
    if value == {"quit": True} and type(value.get("quit")) is bool:
        return value
    if (not isinstance(value, dict) or set(value) != {"scene", "serial"}
            or not isinstance(value["scene"], str) or value["scene"] not in COLORS
            or type(value["serial"]) is not int or not 0 <= value["serial"] <= 1000000):
        raise ValueError("Expected scene/serial or quit:true fixture control")
    return value


def run(folder: Path, seconds: int, scene: str) -> int:
    # Help and imports need no Qt installation. Native qualification uses the
    # existing workstation's Qt; this script installs nothing and injects no input.
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtGui import QColor, QPainter
    from PySide6.QtWidgets import QApplication, QWidget

    class Fixture(QWidget):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Basaltwater disposable conditional fixture")
            self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.FramelessWindowHint)
            self.setFixedSize(640, 360)
            self.scene, self.serial, self.ticks = scene, -1, 0
            self.presses = self.releases = self.keys = 0
            self.started, self.reason = time.monotonic(), "normal-close"
            self.events, self.truncated, self.closed = [], False, False
            self.timer = QTimer(self)
            self.timer.timeout.connect(self.tick)
            self.timer.start(100)
            self.save()

        def save(self):
            write_json_atomic(str(folder / "state.json"), {
                "schema_version": 1, "kind": "conditional-fixture-state", "pid": os.getpid(),
                "state": "closed" if self.closed else "running", "scene": self.scene, "serial": self.serial,
                "presses": self.presses, "releases": self.releases, "keys": self.keys,
                "elapsed_seconds": time.monotonic() - self.started, "reason": self.reason,
                "events": self.events, "events_truncated": self.truncated}, mode=0o600)

        def record(self, kind, **details):
            if len(self.events) < 128:
                self.events.append({"kind": kind, **stamp(), **details})
            else:
                self.truncated = True
            self.save()

        def tick(self):
            self.ticks += 1
            if time.monotonic() - self.started >= seconds:
                self.reason = "bounded-lifetime"
                self.close()
                return
            try:
                command = read_control(folder)
            except (OSError, ValueError) as exc:
                self.reason = "control-error"
                self.record("control-error", detail=str(exc)[:300])
                self.close()
                return
            if command.get("quit") is True:
                self.reason = "cooperative-command"
                self.close()
                return
            if command and command["serial"] != self.serial:
                self.scene, self.serial = command["scene"], command["serial"]
                self.record("setup-scene", scene=self.scene, serial=self.serial)
            self.update()

        def paintEvent(self, event):
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor("#202833"))
            painter.setPen(QColor("white"))
            painter.drawText(30, 35, "Basaltwater disposable conditional fixture")
            painter.drawText(30, 65, f"Presses {self.presses}   Releases {self.releases}   Keys {self.keys}")
            painter.fillRect(40, 100, 180, 180, QColor(COLORS[self.scene]))
            painter.fillRect(400, 100, 120, 120, QColor("#20e060"))
            painter.fillRect(600, 320, 20, 20, QColor("#ffffff" if self.ticks % 2 else "#303030"))
            painter.drawText(30, 325, "Scene setup is separate from input events")

        def mousePressEvent(self, event):
            self.presses += 1
            self.record("mouse-press")

        def mouseReleaseEvent(self, event):
            self.releases += 1
            self.record("mouse-release")

        def keyPressEvent(self, event):
            self.keys += 1
            self.record("key-press")

        def closeEvent(self, event):
            self.timer.stop()
            self.closed = True
            self.save()
            event.accept()

    app = QApplication([])
    window = Fixture()
    window.show()
    print(str(folder / "state.json"), flush=True)
    result = app.exec()
    return result or int(window.reason == "control-error")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="New directory outside Git beneath a private owned parent")
    parser.add_argument("--seconds", type=int, default=600, help="Cooperative lifetime, 1–600 seconds")
    parser.add_argument("--scene", choices=tuple(COLORS), default="red")
    args = parser.parse_args()
    if not 1 <= args.seconds <= 600:
        parser.error("--seconds must be in 1–600")
    return run(new_bundle(args.output), args.seconds, args.scene)


if __name__ == "__main__":
    raise SystemExit(main())
