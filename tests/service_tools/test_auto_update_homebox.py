"""Tests for the HomeBox recurring update entry point."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from common.service_tools import auto_update_homebox


class AutoUpdateHomeBoxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.record_update_function = auto_update_homebox._record_update_result
        self.record_patcher = patch.object(auto_update_homebox, "_record_update_result")
        self.record_update = self.record_patcher.start()
        self.addCleanup(self.record_patcher.stop)

    @patch("common.service_tools.auto_update_homebox.send_notification_safe")
    @patch("common.service_tools.auto_update_homebox.update_homebox_to_latest", return_value=("v0.27.1", False))
    @patch("common.service_tools.auto_update_homebox.load_notification_configs_from_state", return_value=[])
    def test_current_release_is_recorded_without_a_notification(self, _configs, _update, notify):
        self.assertEqual(auto_update_homebox.main(), 0)
        self.record_update.assert_called_once_with(0, "v0.27.1", False)
        notify.assert_not_called()

    @patch("common.service_tools.auto_update_homebox.send_notification_safe")
    @patch("common.service_tools.auto_update_homebox.update_homebox_to_latest", return_value=("v0.27.1", True))
    @patch("common.service_tools.auto_update_homebox.load_notification_configs_from_state", return_value=["notification"])
    def test_successful_update_notifies_and_records_version(self, _configs, _update, notify):
        self.assertEqual(auto_update_homebox.main(), 0)
        self.record_update.assert_called_once_with(0, "v0.27.1", True)
        self.assertIn("HomeBox updated", notify.call_args.kwargs["subject"])
        self.assertEqual(notify.call_args.kwargs["status"], "good")

    @patch("common.service_tools.auto_update_homebox.send_notification_safe")
    @patch("common.service_tools.auto_update_homebox.update_homebox_to_latest", side_effect=RuntimeError("migration failed"))
    @patch("common.service_tools.auto_update_homebox.load_notification_configs_from_state", return_value=["notification"])
    def test_failed_update_notifies_and_records_failure(self, _configs, _update, notify):
        self.assertEqual(auto_update_homebox.main(), 1)
        self.record_update.assert_called_once_with(1, None, False)
        self.assertIn("HomeBox update failed", notify.call_args.kwargs["subject"])

    @patch("common.service_tools.auto_update_homebox.update_homebox_to_latest", return_value=None)
    @patch("common.service_tools.auto_update_homebox.load_notification_configs_from_state", return_value=[])
    def test_disabled_service_skips_update(self, _configs, _update):
        self.assertEqual(auto_update_homebox.main(), 0)
        self.record_update.assert_called_once_with(0, None, False)

    def test_update_result_is_non_secret_root_state(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = os.path.join(directory, "homebox_update.json")
            with patch.object(auto_update_homebox, "UPDATE_STATE", state_path):
                self.record_update_function(0, "v0.27.1", True)
            with open(state_path, encoding="utf-8") as source:
                state = json.load(source)

        self.assertEqual(state["schema_version"], 1)
        self.assertEqual(state["version"], "v0.27.1")
        self.assertTrue(state["successful"])
        self.assertTrue(state["changed"])
        self.assertTrue(state["checked_at"].endswith("+00:00"))


if __name__ == "__main__":
    unittest.main()
