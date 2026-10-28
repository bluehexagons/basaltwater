"""Vendor-channel acceptance, bounded downloads, and durable provenance."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from lib import vendor_installer as installer


class TestVendorInstaller(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / "state"
        self.state.mkdir(mode=0o700)
        self.downloads = self.root / "downloads"
        self.downloads.mkdir()
        state = patch.object(installer, "_state_directory", return_value=self.state)
        state.start()
        self.addCleanup(state.stop)
        self.payload = b"#!/bin/sh\necho installer\n"

    def download(self, command, **kwargs):
        self.assertEqual(command[:2], ["/usr/bin/prlimit", f"--fsize={installer.MAX_INSTALLER_BYTES}"])
        self.assertEqual(kwargs["timeout"], 130)
        curl = command
        curl = curl[curl.index("curl"):]
        for flag, value in (("--proto", "=https"), ("--proto-redir", "=https"),
                            ("--max-time", "120"), ("--connect-timeout", "15")):
            self.assertEqual(curl[curl.index(flag) + 1], value)
        Path(curl[curl.index("--output") + 1]).write_bytes(self.payload)
        return subprocess.CompletedProcess(command, 0, "https://vendor.example/install.sh", "")

    def test_acceptance_required_before_any_execution(self):
        with patch.object(installer, "run") as runner:
            for tool, accepted in (("codex", False), ("unknown", True)):
                with self.assertRaises(ValueError):
                    installer.install(tool, accept_vendor_channel=accepted)
            runner.assert_not_called()

    def test_generated_command_starts_from_an_unrelated_directory(self):
        environment = dict(os.environ, PYTHONPATH=str(Path(installer.__file__).parent))
        # These modules must not override the standard library or trusted lib
        # package when setup invokes the helper from a user's working directory.
        for name in ("types.py", "lib.py", "sitecustomize.py"):
            (self.downloads / name).write_text(
                "raise RuntimeError('untrusted import')\n", encoding="utf-8",
            )
        for tool in installer.POLICIES:
            with self.subTest(tool=tool):
                result = subprocess.run(
                    [*shlex.split(installer.installer_command(tool)), "--help"],
                    cwd=self.downloads,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("--accept-vendor-channel", result.stdout)

    def test_download_records_exact_digest_and_private_file(self):
        with patch.object(installer, "run", side_effect=self.download) as runner:
            path, state_path, record = installer.download_installer("codex", str(self.downloads))
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(runner.call_args.args[0][-1], installer.POLICIES["codex"][0])
        self.assertEqual(Path(path).read_bytes(), self.payload)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(state_path).st_mode & 0o777, 0o600)
        self.assertEqual(record["observed_sha256"], hashlib.sha256(self.payload).hexdigest())
        self.assertEqual(json.loads(Path(state_path).read_text()), record)

    def test_codex_http_failure_uses_official_fallback_and_records_its_payload(self):
        fallback = "https://raw.githubusercontent.com/openai/codex/main/scripts/install/install.sh"
        attempts = []

        def download(command, **kwargs):
            attempts.append(command[-1])
            if len(attempts) == 1:
                Path(command[command.index("--output") + 1]).write_bytes(b"partial response")
                raise installer.CommandExecutionError("curl", 22, "HTTP 404")
            self.assertEqual(command[-1], fallback)
            result = self.download(command, **kwargs)
            result.stdout = fallback
            return result

        with patch.object(installer, "run", side_effect=download):
            path, state_path, record = installer.download_installer("codex", str(self.downloads))
        self.assertEqual(attempts, [installer.POLICIES["codex"][0], fallback])
        self.assertEqual(Path(path).read_bytes(), self.payload)
        self.assertEqual(record["source"], installer.POLICIES["codex"][0])
        self.assertEqual(record["effective_url"], fallback)
        self.assertEqual(record["observed_sha256"], hashlib.sha256(self.payload).hexdigest())
        self.assertEqual(json.loads(Path(state_path).read_text()), record)

    def test_codex_fallback_failure_is_not_retried_and_removes_partial_file(self):
        def failed(command, **kwargs):
            self.download(command, **kwargs)
            raise installer.CommandExecutionError("curl", 22, "HTTP 404")

        with patch.object(installer, "run", side_effect=failed) as runner:
            with self.assertRaises(installer.CommandExecutionError):
                installer.download_installer("codex", str(self.downloads))
        self.assertEqual(runner.call_count, 2)
        self.assertEqual(list(self.downloads.iterdir()), [])
        self.assertEqual(list(self.state.iterdir()), [])

    def test_fallback_does_not_retry_transport_errors_or_other_tools(self):
        failures = [("codex", code) for code in (6, 28, 60)]
        failures.extend((tool, 22) for tool in installer.POLICIES if tool != "codex")
        for tool, code in failures:
            with self.subTest(tool=tool, code=code), patch.object(
                installer, "run", side_effect=installer.CommandExecutionError("curl", code),
            ) as runner:
                with self.assertRaises(installer.CommandExecutionError):
                    installer.download_installer(tool, str(self.downloads))
                self.assertEqual(runner.call_count, 1)
                self.assertEqual(list(self.downloads.iterdir()), [])

    def test_invalid_fallback_payload_never_executes(self):
        calls = 0

        def download(command, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise installer.CommandExecutionError("curl", 22)
            self.payload = b""
            return self.download(command, **kwargs)

        with patch.object(installer, "run", side_effect=download) as runner:
            with self.assertRaisesRegex(ValueError, "empty"):
                installer.install("codex", accept_vendor_channel=True)
        self.assertEqual(runner.call_count, 2)
        self.assertEqual(list(self.state.iterdir()), [])

    def test_invalid_payload_or_redirect_never_executes(self):
        for payload in (b"", b"12345"):
            with self.subTest(payload=payload):
                self.payload = payload
                with patch.object(installer, "MAX_INSTALLER_BYTES", 4), patch.object(
                    installer, "run", side_effect=self.download,
                ) as runner:
                    with self.assertRaises(ValueError):
                        installer.install("codex", accept_vendor_channel=True)
                    self.assertEqual(runner.call_count, 1)
        script = self.downloads / "script"
        script.write_bytes(b"script")
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            installer.record_installer("codex", str(script), effective_url="http://vendor.example/script")

    def test_download_failure_removes_partial_file(self):
        def failed(command, **kwargs):
            self.download(command, **kwargs)
            raise installer.CommandExecutionError("curl", 28)

        with patch.object(installer, "run", side_effect=failed):
            with self.assertRaises(installer.CommandExecutionError):
                installer.download_installer("codex", str(self.downloads))
        self.assertEqual(list(self.downloads.iterdir()), [])

    def test_execution_requires_provenance_and_retains_failure_or_interruption(self):
        for interrupted in (False, True):
            def execute(command, **kwargs):
                if isinstance(command, list) and "curl" in command:
                    return self.download(command, **kwargs)
                record = json.loads((self.state / "codex.json").read_text())
                self.assertEqual(record["status"], "downloaded")
                self.assertEqual(kwargs["timeout"], 3600)
                if interrupted:
                    raise KeyboardInterrupt()
                return subprocess.CompletedProcess(command, 2)

            with patch.object(installer, "run", side_effect=execute):
                if interrupted:
                    with self.assertRaises(KeyboardInterrupt):
                        installer.install("codex", accept_vendor_channel=True)
                else:
                    self.assertEqual(installer.install("codex", accept_vendor_channel=True), 2)
            record = json.loads((self.state / "codex.json").read_text())
            self.assertEqual(record["status"], "interrupted" if interrupted else "failed")
            self.assertEqual([p.name for p in self.state.iterdir()], ["codex.json"])

    def test_non_interactive_install_closes_stdin_and_sets_prompt_guards(self):
        def execute(command, **kwargs):
            if isinstance(command, list) and "curl" in command:
                return self.download(command, **kwargs)
            self.assertEqual(kwargs["input_data"], "")
            self.assertEqual(kwargs["env"]["CI"], "1")
            self.assertEqual(kwargs["env"]["NON_INTERACTIVE"], "1")
            self.assertEqual(kwargs["env"]["npm_config_yes"], "true")
            return subprocess.CompletedProcess(command, 0)

        with patch.object(installer, "run", side_effect=execute):
            self.assertEqual(
                installer.install(
                    "codex", accept_vendor_channel=True, non_interactive=True,
                ),
                0,
            )

    def test_provenance_write_failure_prevents_execution(self):
        with patch.object(installer, "run", side_effect=self.download) as runner, patch.object(
            installer, "write_json_atomic", side_effect=OSError("disk full"),
        ):
            with self.assertRaisesRegex(OSError, "disk full"):
                installer.install("codex", accept_vendor_channel=True)
            self.assertEqual(runner.call_count, 1)

    def test_non_regular_installer_is_rejected_without_blocking(self):
        fifo = self.downloads / "fifo"
        os.mkfifo(fifo)
        with self.assertRaisesRegex(ValueError, "regular"):
            installer.record_installer("codex", str(fifo))


class TestInstallerStateDirectory(unittest.TestCase):
    def test_service_account_uses_explicit_owned_home_without_creating_login_home(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            installer.pwd, "getpwuid", return_value=SimpleNamespace(pw_dir="/nonexistent"),
        ), patch.dict(os.environ, {"HOME": directory}):
            # Observe ownership as the real fixture user; never write root state.
            if os.geteuid() == 0:
                self.skipTest("non-root installer home fixture")
            expected = Path(directory) / ".local/state/basaltwater/installers"
            self.assertEqual(installer._state_directory(), expected)
            self.assertEqual(expected.stat().st_mode & 0o777, 0o700)
            expected.chmod(0o755)
            with self.assertRaisesRegex(ValueError, "private"):
                installer._state_directory()

    def test_relative_home_refuses_before_directory_creation(self):
        with patch.dict(os.environ, {"HOME": "relative"}), patch.object(Path, "mkdir") as mkdir:
            with self.assertRaisesRegex(ValueError, "absolute"):
                installer._state_directory()
        mkdir.assert_not_called()
