"""Focused tests for Ubuntu WSL setup boundaries and source preparation."""

from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import wsl_steps
from lib.config import SetupConfig
from plugins.wsl import build_wsl_steps
from windows import wsl_prepare


class WslStepTests(unittest.TestCase):
    def _config(self, **overrides):
        values = dict(system_type="server_wsl", machine_type="wsl", username="agent",
                      host="localhost", web_interfaces=(), t3code_desktop=False,
                      enable_rdp=False, storage_mounts=(), swap_files=(), swap_devices=(),
                      is_app_server=False, enable_ssl=False, enable_cloudflare=False,
                      is_build_server=False, enable_cicd=False)
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_preflight_rejects_nonlocal_and_linux_webhook_configuration(self):
        with patch.object(wsl_steps, "is_dry_run", return_value=True):
            for config in (self._config(host="example.com"),
                           self._config(enable_cicd=True),
                           self._config(machine_type="vm")):
                with self.subTest(config=config), self.assertRaises(ValueError):
                    wsl_steps.preflight_wsl(config)

    def test_preflight_requires_ubuntu_systemd_and_wsl(self):
        config = self._config()
        with patch.object(wsl_steps, "is_dry_run", return_value=False), \
             patch.object(wsl_steps.platform, "freedesktop_os_release", return_value={"ID": "ubuntu"}), \
             patch.object(wsl_steps, "detect_machine_type", return_value="wsl"), \
             patch.object(wsl_steps.os, "geteuid", return_value=0), \
             patch.object(wsl_steps.pwd, "getpwnam"), \
             patch.object(wsl_steps.Path, "read_text", return_value="systemd\n"):
            wsl_steps.preflight_wsl(config)
        with patch.object(wsl_steps, "is_dry_run", return_value=False), \
             patch.object(wsl_steps.platform, "freedesktop_os_release", return_value={"ID": "debian"}):
            with self.assertRaisesRegex(ValueError, "requires Ubuntu"):
                wsl_steps.preflight_wsl(config)

    def test_base_package_actions_are_bounded_to_apt(self):
        with patch.object(wsl_steps, "is_dry_run", return_value=False), \
             patch.object(wsl_steps, "run") as run:
            wsl_steps.install_wsl_base(self._config())
        self.assertEqual(run.call_args_list[0].args[0][:2], ["apt-get", "update"])
        self.assertEqual(run.call_args_list[1].args[0][:2], ["apt-get", "upgrade"])
        self.assertIn("sudo", run.call_args_list[2].args[0])

    def test_agents_receive_helpers_after_tools_and_before_readiness(self):
        for tools in (["codex"], ["claude"], ["opencode"], ["codex", "claude"], ["gh"], []):
            with self.subTest(tools=tools):
                config = SetupConfig(host="localhost", username="agent", system_type="server_wsl",
                                     machine_type="wsl", agent_tools=tools)
                functions = [function.__name__ for _title, function in build_wsl_steps(config)]
                if set(tools).intersection({"codex", "claude", "opencode"}):
                    self.assertEqual(functions[-4:], ["install_agent_cli_launcher", "install_wsl_agent_skills",
                                                     "update_managed_agent_tools", "report_wsl_readiness"])
                    for tool in tools:
                        self.assertLess(functions.index(f"install_{tool}"),
                                        functions.index("update_managed_agent_tools"))
                else:
                    self.assertNotIn("install_agent_cli_launcher", functions)
                    self.assertNotIn("install_wsl_agent_skills", functions)
                    self.assertNotIn("update_managed_agent_tools", functions)

    def test_skill_dry_run_never_writes_catalogs(self):
        with patch.object(wsl_steps, "is_dry_run", return_value=True), \
             patch("common.agent_steps.install_managed_agent_skills") as install:
            wsl_steps.install_wsl_agent_skills(self._config())
        install.assert_not_called()


class WslPrepareTests(unittest.TestCase):
    def test_invalid_account_marker_stops_before_account_operations(self):
        for content in ('null', '{"version": 2, "password_pending": true}',
                        '{"version": 1, "password_pending": 1}'):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                accounts = root / "accounts"
                accounts.mkdir()
                marker = accounts / "agent.json"
                marker.write_text(content)
                with patch.object(wsl_prepare, "CONF", root / "wsl.conf"), \
                     patch.object(wsl_prepare, "ACCOUNTS", accounts), \
                     patch.object(wsl_prepare.os, "geteuid", return_value=0), \
                     patch.object(wsl_prepare.os, "fchown"), \
                     patch.object(wsl_prepare.platform, "freedesktop_os_release", return_value={"ID": "ubuntu"}), \
                     patch.object(wsl_prepare.pwd, "getpwnam") as lookup, \
                     patch.object(wsl_prepare.subprocess, "run") as run:
                    with self.assertRaisesRegex(ValueError, "account preparation state"):
                        wsl_prepare.configure("agent")
                    lookup.assert_not_called()
                    run.assert_not_called()
                self.assertEqual(marker.read_text(), content)

    def test_interrupted_managed_password_is_retried_before_sudo_and_completion(self):
        for interruption in (subprocess.CalledProcessError(1, "passwd"), KeyboardInterrupt()):
            with self.subTest(interruption=type(interruption)), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                calls = []
                exists = False

                def lookup(_username):
                    if not exists:
                        raise KeyError("agent")
                    return SimpleNamespace(pw_name="agent")

                def run(command, **_kwargs):
                    nonlocal exists
                    calls.append(command)
                    if command[0] == "useradd":
                        exists = True
                    if command[0] == "passwd" and len(calls) == 2:
                        raise interruption

                with patch.object(wsl_prepare, "CONF", root / "wsl.conf"), \
                     patch.object(wsl_prepare, "ACCOUNTS", root / "accounts"), \
                     patch.object(wsl_prepare.os, "geteuid", return_value=0), \
                     patch.object(wsl_prepare.os, "fchown"), \
                     patch.object(wsl_prepare.platform, "freedesktop_os_release", return_value={"ID": "ubuntu"}), \
                     patch.object(wsl_prepare.pwd, "getpwnam", side_effect=lookup), \
                     patch.object(wsl_prepare.subprocess, "run", side_effect=run):
                    with self.assertRaises(type(interruption)):
                        wsl_prepare.configure("agent")
                    marker = root / "accounts/agent.json"
                    self.assertTrue(wsl_prepare.read_json_file(str(marker))["password_pending"])
                    self.assertEqual(marker.stat().st_mode & 0o777, 0o600)
                    self.assertEqual(marker.parent.stat().st_mode & 0o777, 0o700)
                    self.assertFalse((root / "wsl.conf").exists())
                    wsl_prepare.configure("agent")
                    self.assertFalse(wsl_prepare.read_json_file(str(marker))["password_pending"])
                    wsl_prepare.configure("agent")
                self.assertEqual([call[0] for call in calls],
                                 ["useradd", "passwd", "passwd", "usermod", "usermod"])

    def test_preexisting_locked_account_does_not_receive_password_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(wsl_prepare, "CONF", root / "wsl.conf"), \
                 patch.object(wsl_prepare, "ACCOUNTS", root / "accounts"), \
                 patch.object(wsl_prepare.os, "geteuid", return_value=0), \
                 patch.object(wsl_prepare.os, "fchown"), \
                 patch.object(wsl_prepare.platform, "freedesktop_os_release", return_value={"ID": "ubuntu"}), \
                 patch.object(wsl_prepare.pwd, "getpwnam"), \
                 patch.object(wsl_prepare.subprocess, "run") as run:
                wsl_prepare.configure("agent")
                wsl_prepare.configure("agent")
            self.assertEqual([call.args[0][0] for call in run.call_args_list], ["usermod", "usermod"])
            self.assertFalse((root / "accounts/agent.json").exists())

    def test_ini_insertion_separates_an_unterminated_existing_setting(self):
        for section, key, value in (("boot", "systemd", "true"), ("user", "default", "agent")):
            with self.subTest(section=section):
                lines = [f"[{section}]\n", "unrelated=preserved"]
                self.assertTrue(wsl_prepare._set_ini_value(lines, section, key, value))
                self.assertEqual("".join(lines), f"[{section}]\nunrelated=preserved\n{key}={value}\n")
                self.assertFalse(wsl_prepare._set_ini_value(lines, section, key, value))

    def test_ini_changes_preserve_unrelated_settings_and_are_idempotent(self):
        lines = ["[interop]\n", "appendWindowsPath=false\n"]
        self.assertTrue(wsl_prepare._set_ini_value(lines, "boot", "systemd", "true"))
        self.assertTrue(wsl_prepare._set_ini_value(lines, "user", "default", "agent"))
        self.assertFalse(wsl_prepare._set_ini_value(lines, "boot", "systemd", "true"))
        self.assertIn("appendWindowsPath=false\n", lines)

    def test_stage_copies_pinned_source_and_reuses_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            (source / "plugins").mkdir(parents=True)
            (source / "plugins/wsl.py").write_text("ready\n", encoding="utf-8")
            revision = "a" * 40
            with patch.object(wsl_prepare, "RELEASES", root / "releases"), \
                 patch.object(wsl_prepare.os, "geteuid", return_value=0):
                wsl_prepare.stage(str(source), revision)
                wsl_prepare.stage(str(source), revision)
            self.assertEqual((root / "releases" / revision / "plugins/wsl.py").read_text(),
                             "ready\n")

    def test_stage_rejects_symlink_and_bad_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            (source / "plugins").mkdir(parents=True)
            (source / "plugins/wsl.py").write_text("ready\n", encoding="utf-8")
            (source / "link").symlink_to("plugins/wsl.py")
            with patch.object(wsl_prepare, "RELEASES", root / "releases"), \
                 patch.object(wsl_prepare.os, "geteuid", return_value=0):
                with self.assertRaisesRegex(ValueError, "commit SHA"):
                    wsl_prepare.stage(str(source), "main")
                with self.assertRaisesRegex(ValueError, "symbolic link"):
                    wsl_prepare.stage(str(source), "b" * 40)


if __name__ == "__main__":
    unittest.main()
