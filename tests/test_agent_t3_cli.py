"""The managed headless setup must expose a usable host CLI."""

from __future__ import annotations

from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import t3code_steps
from lib.config import SetupConfig


class T3HostCliTests(unittest.TestCase):
    def assert_entry_preserved(self, path: Path, original: os.stat_result) -> None:
        # Executing or inspecting a symlink may update atime without changing it.
        current = path.lstat()
        for field in ('st_dev', 'st_ino', 'st_mode', 'st_nlink', 'st_uid', 'st_gid',
                      'st_size', 'st_mtime_ns', 'st_ctime_ns'):
            self.assertEqual(getattr(current, field), getattr(original, field), field)

    def test_setup_exposes_cli_that_follows_runtime_updates(self):
        with tempfile.TemporaryDirectory(prefix="t3 home ") as directory, ExitStack() as stack:
            home = Path(directory)
            account = SimpleNamespace(pw_dir=directory, pw_uid=1000, pw_gid=1000)
            admin = home / "admin-pair.py"
            admin.write_text("# fixture\n")
            stack.enter_context(patch.object(t3code_steps, "is_dry_run", return_value=False))
            stack.enter_context(patch.object(t3code_steps, "_user_home", return_value=directory))
            stack.enter_context(patch.object(t3code_steps.pwd, "getpwnam", return_value=account))
            stack.enter_context(patch.object(t3code_steps.os, "chown"))
            link_owner = stack.enter_context(patch.object(t3code_steps.os, "lchown"))
            stack.enter_context(patch.object(t3code_steps, "T3_ADMIN_PAIR_SCRIPT", str(admin)))
            for name in (
                "_configure_firewall", "_install_t3_service", "_ensure_t3_agent_skill",
                "_remove_connect_restart_units", "_remove_device_pairing", "_remove_t3_https",
            ):
                stack.enter_context(patch.object(t3code_steps, name))
            stack.enter_context(patch.object(t3code_steps, "_configure_t3_https", return_value={}))
            stack.enter_context(patch.object(
                t3code_steps, "run", side_effect=AssertionError("Unexpected host command"),
            ))
            config = SetupConfig(
                host="target", username="agent", system_type="server_dev",
                agent_tools=["codex"], web_interfaces=["t3code"],
                web_interface_host="127.0.0.1", disable_device_pairing=True,
            )
            t3code_steps.install_t3code_web(config)
            launcher = home / ".local/bin/t3"
            self.assertTrue(launcher.is_symlink())
            link_owner.assert_called_once_with(str(launcher), 1000, 1000)
            os.utime(launcher, ns=(0, launcher.lstat().st_mtime_ns), follow_symlinks=False)
            original = launcher.lstat()
            runtime = home / ".t3/runtime"
            for version in ("0.1.3", "0.1.4"):
                binary = runtime / "versions" / version / "t3"
                binary.parent.mkdir(parents=True)
                binary.write_text(f"#!/bin/sh\nprintf '%s\\n' '{version}' \"$@\"\n")
                binary.chmod(0o755)
                (runtime / "service-state.json").write_text(json.dumps({
                    "protocol": 3, "activeVersion": version,
                }))
                result = subprocess.run(
                    [str(launcher), "thread", "argument with spaces"],
                    check=True, capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(result.stdout.splitlines(), [
                    version, "thread", "argument with spaces",
                ])
            t3code_steps.install_t3code_web(config)
            self.assert_entry_preserved(launcher, original)
            link_owner.assert_called_once()

    def test_existing_commands_and_other_entries_are_preserved(self):
        for kind in ("file", "link", "dangling-link", "directory"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                launcher = home / ".local/bin/t3"
                launcher.parent.mkdir(parents=True)
                other = home / "other-t3"
                if kind == "file":
                    launcher.write_text("user command\n")
                    launcher.chmod(0o700)
                elif kind == "directory":
                    launcher.mkdir()
                else:
                    if kind == "link":
                        other.write_text("user command\n")
                    launcher.symlink_to(other)
                original = launcher.lstat()
                with patch.object(t3code_steps.os, "lchown") as owner:
                    self.assertFalse(t3code_steps._install_t3_cli_launcher(directory, 1000, 1000))
                self.assert_entry_preserved(launcher, original)
                owner.assert_not_called()
                if kind == "file":
                    self.assertEqual(launcher.read_text(), "user command\n")
                elif kind.endswith("link"):
                    self.assertEqual(launcher.readlink(), other)


if __name__ == "__main__":
    unittest.main()
