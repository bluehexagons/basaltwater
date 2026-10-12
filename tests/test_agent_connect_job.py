"""Connect completion includes its restart receipt before another job can start."""

from __future__ import annotations

import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from common.service_tools import device_pairing_service as pairing


class ConnectCompletionTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.job = pairing.ConnectJob({"restart_request": str(root / "restart"), "link_command": ["fixture"]})
        self.process = Mock(stdout=io.StringIO(""), stdin=Mock())
        self.process.wait.return_value = 0
        self.job._process = self.process
        self.timer = Mock()

    def test_restart_publication_precedes_completion_and_prevents_a_new_start(self):
        def publish(path, flags, mode):
            snapshot = self.job.snapshot()
            self.assertTrue(snapshot["active"])
            self.assertIsNone(snapshot["returncode"])
            with self.assertRaisesRegex(pairing.PairingError, "already running"):
                self.job.start()
            return 42
        with patch.object(pairing.os, "open", side_effect=publish) as receipt, \
                patch.object(pairing.os, "close") as close, \
                patch.object(pairing.subprocess, "Popen") as start:
            self.job._watch(self.process, self.timer)
        receipt.assert_called_once()
        close.assert_called_once_with(42)
        start.assert_not_called()
        snapshot = self.job.snapshot()
        self.assertFalse(snapshot["active"])
        self.assertEqual(snapshot["returncode"], 0)
        self.assertIsNone(snapshot["error"])

    def test_receipt_failure_is_reported_with_completion_without_private_error_text(self):
        def fail(*args):
            self.assertTrue(self.job.snapshot()["active"])
            raise OSError("private fixture details")
        with patch.object(pairing.os, "open", side_effect=fail), patch.object(pairing.os, "close") as close:
            self.job._watch(self.process, self.timer)
        close.assert_not_called()
        snapshot = self.job.snapshot()
        self.assertFalse(snapshot["active"])
        self.assertEqual(snapshot["returncode"], 0)
        self.assertIn("could not be requested", snapshot["error"])
        self.assertNotIn("private fixture", snapshot["error"])
        handler = object.__new__(pairing.PairingRequestHandler)
        handler.server = Mock()
        handler.server.pairing_state.connect_snapshot.return_value = snapshot
        page = handler._connect_section("t3code", {"label": "T3 Code"}, "fixture-nonce")
        self.assertNotIn("managed service is applying", page)
        self.assertIn("verify relay status", page)

    def test_failed_provider_does_not_request_a_restart(self):
        self.process.wait.return_value = 1
        with patch.object(pairing.os, "open") as receipt:
            self.job._watch(self.process, self.timer)
        receipt.assert_not_called()
        self.assertEqual(self.job.snapshot()["returncode"], 1)
