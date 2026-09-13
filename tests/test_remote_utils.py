"""Tests for lib/remote_utils.py: dry-run mode, validation, password generation, file_contains."""

from __future__ import annotations

import os
import signal
import string
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.remote_utils import (
    CommandExecutionError,
    CommandTimeoutError,
    DEFAULT_COMMAND_TIMEOUT_SECONDS,
    set_dry_run,
    is_dry_run,
    generate_password,
    get_user_home,
    run,
    file_contains,
    detect_os,
    confirm_unsupported_environment,
)
from lib.validators import validate_username
from lib import remote_utils


class TestProbeDeadlines(unittest.TestCase):
    def test_service_bus_failure_is_not_inactive(self):
        with patch.object(remote_utils.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "bus unavailable")):
            with self.assertRaises(remote_utils.ProbeError):
                remote_utils.is_service_active("nginx")

    def test_probe_timeout_never_becomes_absence_or_triggers_install(self):
        for probe, value in ((remote_utils.is_package_installed, "curl"), (remote_utils.is_service_active, "nginx"), (remote_utils.user_exists, "agent")):
            with self.subTest(probe=probe.__name__), patch.object(remote_utils.subprocess, "run", side_effect=subprocess.TimeoutExpired("probe", 15)) as command, patch.object(remote_utils, "run") as install:
                with self.assertRaisesRegex(remote_utils.ProbeError, "state is unknown"):
                    remote_utils.install_with_verify("test", ["install"], lambda: probe(value))
                install.assert_not_called()
                self.assertEqual(command.call_args.kwargs["timeout"], 15)

    def test_missing_probe_reports_unknown_even_in_dry_run(self):
        with patch.object(remote_utils, "_dry_run", True), patch.object(remote_utils.subprocess, "run", side_effect=FileNotFoundError):
            with self.assertRaises(remote_utils.ProbeError):
                remote_utils.user_exists("agent")


class TestDryRun(unittest.TestCase):
    def setUp(self):
        set_dry_run(False)

    def tearDown(self):
        set_dry_run(False)

    def test_default_not_dry_run(self):
        self.assertFalse(is_dry_run())

    def test_set_dry_run_true(self):
        set_dry_run(True)
        self.assertTrue(is_dry_run())

    def test_set_dry_run_false(self):
        set_dry_run(True)
        set_dry_run(False)
        self.assertFalse(is_dry_run())


class TestRunDryRun(unittest.TestCase):
    def setUp(self):
        set_dry_run(True)

    def tearDown(self):
        set_dry_run(False)

    def test_dry_run_returns_zero(self):
        result = run("echo hello")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_dry_run_type(self):
        result = run("echo hello")
        self.assertIsInstance(result, subprocess.CompletedProcess)


class TestRunCommandDispatch(unittest.TestCase):
    @patch("lib.remote_utils.subprocess.Popen")
    def test_redirected_output_streams_and_keeps_diagnostics_off_stdout(self, mock_popen):
        self._completed_process(mock_popen)
        output = StringIO()
        errors = StringIO()
        with patch.dict(os.environ, {"INFRA_TOOLS_VERBOSE": "1"}), redirect_stdout(output), patch("lib.remote_utils.sys.stderr", errors):
            run(["build"], stdout=errors, timeout=7)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("Running:", errors.getvalue())
        self.assertIs(mock_popen.call_args.kwargs["stdout"], errors)
        mock_popen.return_value.communicate.assert_called_once_with(input=None, timeout=7.0)

    @staticmethod
    def _completed_process(mock_popen, *, returncode=0, stdout=None, stderr=None):
        process = mock_popen.return_value
        process.communicate.return_value = (stdout, stderr)
        process.returncode = returncode
        process.pid = 1234
        return process

    @patch("lib.remote_utils.subprocess.Popen")
    def test_simple_commands_use_argument_list(self, mock_popen):
        process = self._completed_process(mock_popen)
        run("echo hello")
        mock_popen.assert_called_once_with(
            ["echo", "hello"],
            stdin=None,
            stdout=None,
            stderr=None,
            text=True,
            cwd=None,
            start_new_session=True,
        )
        process.communicate.assert_called_once_with(
            input=None,
            timeout=float(DEFAULT_COMMAND_TIMEOUT_SECONDS),
        )

    @patch("lib.remote_utils.subprocess.Popen")
    def test_argv_commands_bypass_shell_parsing(self, mock_popen):
        self._completed_process(mock_popen)

        run(["printf", "%s", "value with spaces; no shell expansion"])

        mock_popen.assert_called_once_with(
            ["printf", "%s", "value with spaces; no shell expansion"],
            stdin=None,
            stdout=None,
            stderr=None,
            text=True,
            cwd=None,
            start_new_session=True,
        )

    @patch("lib.remote_utils.subprocess.Popen")
    def test_argv_command_redaction_preserves_safe_diagnostics(self, mock_popen):
        self._completed_process(mock_popen, returncode=2, stderr="invalid token")

        with self.assertRaises(CommandExecutionError) as raised:
            run(["deploy", "--token", "secret-value", "--name", "public"])

        self.assertNotIn("secret-value", str(raised.exception))
        self.assertIn("--token <redacted>", str(raised.exception))

    @patch("lib.remote_utils.subprocess.Popen")
    def test_piped_commands_use_explicit_shell_process(self, mock_popen):
        self._completed_process(mock_popen)
        run("echo test | cat")
        mock_popen.assert_called_once_with(
            ["/bin/bash", "-lc", "echo test | cat"],
            stdin=None,
            stdout=None,
            stderr=None,
            text=True,
            cwd=None,
            start_new_session=True,
        )

    @patch("lib.remote_utils.subprocess.Popen")
    def test_explicit_none_allows_deliberately_unbounded_command(self, mock_popen):
        process = self._completed_process(mock_popen)

        run("echo hello", timeout=None)

        process.communicate.assert_called_once_with(input=None, timeout=None)

    @patch("lib.remote_utils.subprocess.Popen")
    def test_check_true_raises_for_failed_command(self, mock_popen):
        self._completed_process(mock_popen, returncode=17, stderr="permission denied")

        with self.assertRaises(CommandExecutionError) as raised:
            run("false", display_cmd="false (sanitized)")

        error = raised.exception
        self.assertEqual(error.command, "false (sanitized)")
        self.assertEqual(error.returncode, 17)
        self.assertEqual(error.stderr, "permission denied")
        self.assertEqual(error.result.args, ["false"])

    @patch("lib.remote_utils.subprocess.Popen")
    def test_check_false_returns_failed_result(self, mock_popen):
        self._completed_process(mock_popen, returncode=17, stderr="failed")

        result = run("false", check=False)

        self.assertEqual(result.returncode, 17)
        self.assertEqual(result.stderr, "failed")

    @patch("lib.remote_utils.subprocess.Popen")
    def test_failure_diagnostics_redact_secret_values(self, mock_popen):
        self._completed_process(
            mock_popen,
            returncode=1,
            stderr="SECRET_KEY_BASE=stderr-secret --token cli-secret",
        )

        with self.assertRaises(CommandExecutionError) as raised:
            run("deploy SECRET_KEY_BASE=command-secret --password option-secret")

        message = str(raised.exception)
        self.assertNotIn("command-secret", message)
        self.assertNotIn("option-secret", message)
        self.assertNotIn("stderr-secret", message)
        self.assertNotIn("cli-secret", message)
        self.assertIn("SECRET_KEY_BASE=<redacted>", message)

    @patch("lib.remote_utils.subprocess.Popen")
    def test_failure_diagnostics_redact_complete_quoted_secret_values(self, mock_popen):
        self._completed_process(
            mock_popen,
            returncode=1,
            stderr=(
                "API_KEY='stderr secret;with|delimiters' "
                '--private-key "stderr key phrase"'
            ),
        )

        with self.assertRaises(CommandExecutionError) as raised:
            run(
                "deploy --password 'command secret phrase' "
                '--token "command;token|suffix" '
                "--credentials 'quoted secret'concatenated"
            )

        message = str(raised.exception)
        for secret_fragment in (
            "command secret phrase",
            "command;token|suffix",
            "stderr secret;with|delimiters",
            "stderr key phrase",
            "token|suffix",
            "key phrase",
            "quoted secret",
            "concatenated",
        ):
            self.assertNotIn(secret_fragment, message)
        self.assertEqual(message.count("<redacted>"), 5)

    @patch("lib.remote_utils.subprocess.Popen")
    def test_failure_diagnostics_redact_escaped_and_unterminated_values(self, mock_popen):
        self._completed_process(
            mock_popen,
            returncode=1,
            stderr="--token 'unterminated secret phrase",
        )

        with self.assertRaises(CommandExecutionError) as raised:
            run(r"deploy --password escaped\ secret --name 'public value'")

        message = str(raised.exception)
        self.assertNotIn("escaped\\ secret", message)
        self.assertNotIn("unterminated secret phrase", message)
        self.assertIn("--name 'public value'", message)

    @patch("lib.remote_utils.os.killpg")
    @patch("lib.remote_utils.subprocess.Popen")
    def test_non_shell_timeout_raises_typed_error_even_when_best_effort(self, mock_popen, mock_killpg):
        process = self._completed_process(mock_popen)
        process.communicate.side_effect = [
            subprocess.TimeoutExpired(["deploy"], 2, stderr="--token timeout-secret"),
            (None, None),
        ]

        with self.assertRaises(CommandTimeoutError) as raised:
            run("deploy --token command-secret", check=False, timeout=2)

        error = raised.exception
        self.assertEqual(error.timeout, 2)
        self.assertNotIn("command-secret", str(error))
        self.assertNotIn("timeout-secret", str(error))
        self.assertEqual(
            mock_killpg.call_args_list,
            [unittest.mock.call(1234, signal.SIGTERM), unittest.mock.call(1234, signal.SIGKILL)],
        )
        process.wait.assert_called_once_with(timeout=5.0)
        self.assertEqual(process.communicate.call_args.kwargs, {"timeout": 5.0})

    @patch("lib.remote_utils.os.killpg")
    @patch("lib.remote_utils.subprocess.Popen")
    def test_escaped_child_cannot_make_timeout_cleanup_unbounded(self, mock_popen, mock_killpg):
        process = self._completed_process(mock_popen)
        process.wait.side_effect = subprocess.TimeoutExpired(["deploy"], 5)
        process.communicate.side_effect = [
            subprocess.TimeoutExpired(["deploy"], 1),
            subprocess.TimeoutExpired(["deploy"], 5, stderr=b"partial --token secret"),
        ]

        with self.assertRaises(CommandTimeoutError) as raised:
            run(["deploy"], capture_output=True, timeout=1)

        self.assertIn("partial --token <redacted>", str(raised.exception))
        self.assertEqual(process.communicate.call_args.kwargs, {"timeout": 5.0})
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close.assert_called_once_with()
        process.poll.assert_called_once_with()
        mock_killpg.assert_called_with(1234, signal.SIGKILL)

    @patch("lib.remote_utils.os.killpg")
    @patch("lib.remote_utils.subprocess.Popen")
    def test_shell_timeout_terminates_process_group(self, mock_popen, mock_killpg):
        process = self._completed_process(mock_popen)
        process.communicate.side_effect = [
            subprocess.TimeoutExpired(["/bin/bash"], 3),
            (None, None),
        ]

        with self.assertRaises(CommandTimeoutError):
            run("deploy | cat", timeout=3)

        self.assertEqual(
            mock_killpg.call_args_list,
            [unittest.mock.call(1234, signal.SIGTERM), unittest.mock.call(1234, signal.SIGKILL)],
        )
        process.wait.assert_called_once_with(timeout=5.0)

    def test_rejects_invalid_timeout(self):
        for timeout in (0, -1, True, float("inf"), float("nan"), "30"):
            with self.subTest(timeout=timeout):
                with self.assertRaisesRegex(ValueError, "positive number"):
                    run("echo hello", timeout=timeout)

    @patch("lib.remote_utils.os.killpg")
    @patch("lib.remote_utils.subprocess.Popen")
    def test_keyboard_interrupt_terminates_isolated_children(self, mock_popen, mock_killpg):
        process = self._completed_process(mock_popen)
        process.communicate.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            run(['deploy'], capture_output=True)
        self.assertEqual(
            mock_killpg.call_args_list,
            [unittest.mock.call(1234, signal.SIGTERM), unittest.mock.call(1234, signal.SIGKILL)],
        )
        process.wait.assert_called_once_with(timeout=5.0)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close.assert_called_once_with()
        process.poll.assert_called_once_with()

    @patch("lib.remote_utils.subprocess.Popen")
    def test_command_echo_is_opt_in(self, mock_popen):
        self._completed_process(mock_popen)
        output = StringIO()

        with patch.dict(os.environ, {"INFRA_TOOLS_VERBOSE": "0"}, clear=False), \
             redirect_stdout(output):
            run("echo hello")

        self.assertNotIn("Running: echo hello", output.getvalue())

        output.seek(0)
        output.truncate()
        with patch.dict(os.environ, {"INFRA_TOOLS_VERBOSE": "1"}, clear=False), \
             redirect_stdout(output):
            run("echo hello")

        self.assertIn("Running: echo hello", output.getvalue())


class TestRemoteValidateUsername(unittest.TestCase):
    def test_valid(self):
        self.assertTrue(validate_username('admin'))

    def test_invalid(self):
        self.assertFalse(validate_username('Admin'))


class TestDetectOS(unittest.TestCase):
    @patch("lib.remote_utils.read_os_release", return_value={"ID": "debian"})
    def test_accepts_debian(self, _mock_read):
        self.assertEqual(detect_os(), "Debian")

    @patch("lib.remote_utils.read_os_release", return_value={"ID": "ubuntu"})
    def test_accepts_ubuntu_as_best_effort(self, _mock_read):
        self.assertIn("Ubuntu", detect_os())

    @patch("lib.remote_utils.read_os_release", return_value={"ID": "linuxmint"})
    def test_accepts_linux_mint_as_best_effort(self, _mock_read):
        self.assertIn("Linux Mint", detect_os())

    @patch("lib.remote_utils.read_os_release", return_value={"ID": "fedora"})
    def test_rejects_other_distributions(self, _mock_read):
        with self.assertRaises(SystemExit):
            detect_os()


class TestUnsupportedEnvironmentConfirmation(unittest.TestCase):
    @patch("lib.remote_utils.read_os_release", return_value={"ID": "cachyos"})
    def test_accepts_explicit_confirmation_for_unsupported_host(self, _mock_read):
        self.assertTrue(
            confirm_unsupported_environment(
                "local bootstrap",
                input_fn=lambda _prompt: "y",
            )
        )

    @patch("lib.remote_utils.read_os_release", return_value={"ID": "cachyos"})
    def test_defaults_to_refusing_unsupported_host(self, _mock_read):
        self.assertFalse(
            confirm_unsupported_environment(
                "local maintenance",
                input_fn=lambda _prompt: "n",
            )
        )

    @patch("lib.remote_utils.read_os_release", return_value={"ID": "debian"})
    def test_does_not_prompt_on_supported_host(self, _mock_read):
        def fail_if_prompted(_prompt: str) -> str:
            raise AssertionError("supported hosts should not prompt")

        self.assertTrue(
            confirm_unsupported_environment(
                "local bootstrap",
                input_fn=fail_if_prompted,
            )
        )


class TestGeneratePassword(unittest.TestCase):
    def test_default_length(self):
        pwd = generate_password()
        self.assertEqual(len(pwd), 16)

    def test_custom_length(self):
        pwd = generate_password(32)
        self.assertEqual(len(pwd), 32)

    def test_uniqueness(self):
        passwords = {generate_password() for _ in range(10)}
        self.assertEqual(len(passwords), 10)

    def test_uses_allowed_characters(self):
        pwd = generate_password(100)
        allowed = set(string.ascii_letters + string.digits + "!@#$%^&*")
        for c in pwd:
            self.assertIn(c, allowed)


class TestFileContains(unittest.TestCase):
    def test_file_contains_string(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write("hello world\nfoo bar\n")
            path = f.name
        try:
            self.assertTrue(file_contains(path, 'hello'))
            self.assertTrue(file_contains(path, 'foo bar'))
            self.assertFalse(file_contains(path, 'missing'))
        finally:
            os.unlink(path)


    def test_file_not_found(self):
        self.assertFalse(file_contains('/nonexistent/file/xyz', 'content'))

    def test_empty_file(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write("")
            path = f.name
        try:
            self.assertFalse(file_contains(path, 'anything'))
        finally:
            os.unlink(path)


class TestUserHome(unittest.TestCase):
    @patch("lib.remote_utils.pwd.getpwnam")
    def test_returns_home_recorded_for_account(self, mock_getpwnam):
        mock_getpwnam.return_value = SimpleNamespace(pw_dir="/srv/users/agent")

        self.assertEqual(get_user_home("agent"), "/srv/users/agent")

    @patch("lib.remote_utils.pwd.getpwnam", side_effect=KeyError("agent"))
    def test_rejects_missing_account(self, _mock_getpwnam):
        with self.assertRaisesRegex(RuntimeError, "Target user does not exist"):
            get_user_home("agent")


if __name__ == '__main__':
    unittest.main()
