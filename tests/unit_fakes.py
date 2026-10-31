"""Small systemd state model for tests that exercise real unit transactions."""

from __future__ import annotations

from pathlib import Path
import subprocess


class FakeSystemd:
    def __init__(self, unit_dir, *, before=None):
        self.unit_dir = unit_dir
        self.before = before
        self.states: dict[str, list[str]] = {}
        self.commands: list[list[str]] = []

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        if self.before is not None:
            self.before(command, **kwargs)
        output = ""
        if command[0] == "systemd-analyze" or command == ["systemctl", "daemon-reload"]:
            return subprocess.CompletedProcess(command, 0, output, "")
        if command[0] != "systemctl":
            raise AssertionError(command)
        action, name = command[1], command[-1]
        if action == "show":
            name = command[2]
        root = Path(self.unit_dir() if callable(self.unit_dir) else self.unit_dir)
        path = root / name
        oneshot = path.is_file() and "Type=oneshot" in path.read_text()
        active, enabled = self.states.setdefault(name, ["inactive", "disabled" if path.exists() else ""])
        if action == "show":
            load = "loaded" if path.exists() else "not-found"
            output = (f"LoadState={load}\nActiveState={active}\nSubState=dead\n"
                      f"UnitFileState={enabled if path.exists() else ''}\n"
                      f"FragmentPath={path}\nNeedDaemonReload=no\nType={'oneshot' if oneshot else 'simple'}\n")
        elif action == "enable":
            self.states[name][1] = "enabled-runtime" if "--runtime" in command else "enabled"
        elif action == "disable":
            self.states[name][1] = "disabled"
        elif action in {"restart", "start"}:
            self.states[name][0] = "inactive" if oneshot else "active"
        elif action == "stop":
            self.states[name][0] = "inactive"
        else:
            raise AssertionError(command)
        return subprocess.CompletedProcess(command, 0, output, "")
