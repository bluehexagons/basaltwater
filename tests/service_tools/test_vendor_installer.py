"""Vendor-channel acceptance, bounded downloads, and durable provenance."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

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
        self.assertEqual(command[0], "curl")
        self.assertEqual(kwargs["timeout"], 130)
        for flag, value in (("--proto", "=https"), ("--proto-redir", "=https"),
                            ("--max-time", "120"), ("--connect-timeout", "15")):
            self.assertEqual(command[command.index(flag) + 1], value)
        Path(command[command.index("--output") + 1]).write_bytes(self.payload)
        return subprocess.CompletedProcess(command, 0, "https://vendor.example/install.sh", "")

    def test_acceptance_required_before_any_execution(self):
        with patch.object(installer, "run") as runner:
            for tool, accepted in (("codex", False), ("unknown", True)):
                with self.assertRaises(ValueError):
                    installer.install(tool, accept_vendor_channel=accepted)
            runner.assert_not_called()

    def test_download_records_exact_digest_and_private_file(self):
        with patch.object(installer, "run", side_effect=self.download):
            path, state_path, record = installer.download_installer("codex", str(self.downloads))
        self.assertEqual(Path(path).read_bytes(), self.payload)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(state_path).st_mode & 0o777, 0o600)
        self.assertEqual(record["observed_sha256"], hashlib.sha256(self.payload).hexdigest())
        self.assertEqual(json.loads(Path(state_path).read_text()), record)

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
            raise subprocess.CalledProcessError(28, command)

        with patch.object(installer, "run", side_effect=failed):
            with self.assertRaises(subprocess.CalledProcessError):
                installer.download_installer("codex", str(self.downloads))
        self.assertEqual(list(self.downloads.iterdir()), [])

    def test_execution_requires_provenance_and_retains_failure_or_interruption(self):
        for interrupted in (False, True):
            def execute(command, **kwargs):
                if command[0] == "curl":
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
