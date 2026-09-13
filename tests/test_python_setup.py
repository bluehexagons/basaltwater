"""Tests for Python tooling setup flag and local installer wiring."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.arg_parser import create_setup_argument_parser
from lib.config import SetupConfig
from lib import python_setup
from lib.update_policy import DEPENDENCY_MIN_AGE_DAYS_ENV, ECOSYSTEM_AUTO_UPGRADE_ENV
from lib.system_types import get_steps_for_system_type
import common.common_steps as common_steps


class TestPythonFlag(unittest.TestCase):
    def test_parser_accepts_python_flag(self):
        parser = create_setup_argument_parser("test")
        args = parser.parse_args(["example.com", "--python"])
        self.assertTrue(args.install_python)

    def test_python_steps_added_when_enabled(self):
        config = SetupConfig(
            host="host",
            username="user",
            system_type="server_dev",
            install_python=True,
        )
        step_names = [name for name, _ in get_steps_for_system_type(config)]
        self.assertIn("Installing Python tooling (aliases + uv)", step_names)
        self.assertIn("Configuring uv auto-update", step_names)

    @patch("common.common_steps.install_or_update_uv", return_value=True)
    @patch("common.common_steps.shutil.which", side_effect=["/usr/bin/python3", "/usr/bin/python"])
    @patch("common.common_steps.run")
    def test_remote_install_python_skips_completion(self, mock_run, _which, _install_uv):
        config = SetupConfig(host="host", username="user", system_type="server_dev", install_python=True)
        common_steps.install_python(config)
        commands = [args[0] for args, _ in mock_run.call_args_list]
        self.assertFalse(any("argcomplete" in cmd for cmd in commands))

    @patch("common.common_steps.is_dry_run", return_value=True)
    def test_install_or_update_uv_returns_true_in_dry_run(self, _is_dry_run):
        self.assertTrue(common_steps.install_or_update_uv(user_home="/home/user", username="user"))

    @patch("common.common_steps._validate_uv_install_script", return_value=True)
    @patch("common.common_steps.record_installer")
    @patch("common.common_steps.run")
    def test_uv_installer_is_readable_by_target_user(self, mock_run, _record, _validate):
        observed_modes = []
        with tempfile.TemporaryDirectory() as user_home:
            installer_path = os.path.join(user_home, "installer.sh")
            fd = os.open(installer_path, os.O_CREAT | os.O_RDWR, 0o600)

            def run_side_effect(command, *_args, **_kwargs):
                if "runuser" in command and f"sh {installer_path}" in command:
                    observed_modes.append(os.stat(installer_path).st_mode & 0o777)
                    uv_dir = os.path.join(user_home, ".local", "bin")
                    os.makedirs(uv_dir, exist_ok=True)
                    with open(os.path.join(uv_dir, "uv"), "w", encoding="utf-8"):
                        pass
                return MagicMock(returncode=0, stdout="", stderr="")

            mock_run.side_effect = run_side_effect
            with patch("common.common_steps.tempfile.mkstemp", return_value=(fd, installer_path)):
                self.assertTrue(
                    common_steps.install_or_update_uv(user_home, username="build-example")
                )

        self.assertEqual(observed_modes, [0o644])

    @patch("common.common_steps.configure_maintenance_timer")
    def test_configure_auto_update_uv_disables_ecosystem_auto_upgrades(self, mock_configure):
        config = SetupConfig(host="host", username="user", system_type="server_dev", install_python=True)
        common_steps.configure_auto_update_uv(config)
        mock_configure.assert_called_once_with(
            service_name="auto-update-uv",
            service_desc="Auto-update uv package manager",
            timer_desc="Auto-update uv weekly",
            script_path="/opt/infra_tools/common/service_tools/auto_update_uv.py",
            schedule="Sun *-*-* 05:00:00",
            check_path="/home/user/.local/bin/uv",
            check_name="uv",
            user="user",
            environment={ECOSYSTEM_AUTO_UPGRADE_ENV: "0"},
            purpose="auto-update",
        )


class TestSetupAdminPython(unittest.TestCase):
    @patch("lib.python_setup.run_completion_setup")
    @patch("lib.python_setup.install_launcher", side_effect=OSError("read-only"))
    @patch("lib.python_setup.os.path.expanduser", return_value="/tmp/testuser")
    @patch("lib.python_setup.os.makedirs")
    @patch("lib.python_setup.subprocess.run")
    @patch("lib.python_setup.install_or_update_uv", return_value=True)
    @patch("lib.python_setup.validate_username", return_value=True)
    @patch("lib.python_setup.get_current_username", return_value="admin")
    @patch(
        "lib.python_setup.shutil.which",
        side_effect=["/usr/bin/python3", "/usr/bin/python"],
    )
    def test_user_launcher_failure_fails_setup(
        self,
        _which,
        _current_username,
        _validate_username,
        _install_uv,
        mock_subprocess_run,
        _mock_makedirs,
        _expanduser,
        _install_launcher,
        mock_completion,
    ):
        mock_subprocess_run.return_value = argparse.Namespace(
            returncode=0,
            stdout="",
            stderr="",
        )
        result = python_setup.run_local_python_setup(
            "bash",
            script_path="/tmp/infra_tools.py",
        )
        self.assertEqual(result, 1)
        mock_completion.assert_not_called()

    @patch("lib.python_setup.run_completion_setup", return_value=0)
    @patch("lib.python_setup.os.path.expanduser", return_value="/tmp/testuser")
    @patch("lib.python_setup.os.symlink")
    @patch("lib.python_setup.os.makedirs")
    @patch("lib.python_setup.subprocess.run")
    @patch("lib.python_setup.install_or_update_uv", return_value=True)
    @patch("lib.python_setup.validate_username", return_value=True)
    @patch("lib.python_setup.get_current_username", return_value="admin")
    @patch("lib.python_setup.shutil.which", side_effect=["/usr/bin/python3", None])
    def test_main_runs_shared_setup_steps(
        self,
        _which,
        _current_username,
        _validate_username,
        mock_install_or_update_uv,
        mock_subprocess_run,
        mock_makedirs,
        mock_symlink,
        _expanduser,
        mock_run_completion_setup,
    ):
        mock_subprocess_run.return_value = argparse.Namespace(returncode=0, stdout="", stderr="")
        with patch.dict(os.environ, {DEPENDENCY_MIN_AGE_DAYS_ENV: "2"}):
            result = python_setup.run_local_python_setup("bash")
        self.assertEqual(result, 0)
        mock_makedirs.assert_called_once()
        mock_symlink.assert_called_once_with("/usr/bin/python3", "/tmp/testuser/.local/bin/python")
        mock_install_or_update_uv.assert_called_once()
        _which.assert_has_calls([call("python3"), call("python")])
        mock_subprocess_run.assert_called_once()
        argcomplete_cmd = mock_subprocess_run.call_args.args[0]
        self.assertEqual(argcomplete_cmd[:5], ["/tmp/testuser/.local/bin/uv", "tool", "install", "--upgrade", "argcomplete"])
        self.assertIn("--exclude-newer", argcomplete_cmd)
        mock_run_completion_setup.assert_called_once_with(
            shell="bash", global_install=False, command_name="infra-tools"
        )

    @patch("lib.python_setup.validate_username", return_value=False)
    @patch("lib.python_setup.get_current_username", return_value="invalid user")
    def test_main_rejects_invalid_username(self, _current_username, _validate_username):
        result = python_setup.run_local_python_setup("bash")
        self.assertEqual(result, 1)


if __name__ == "__main__":
    unittest.main()
