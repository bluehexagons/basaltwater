"""Tests for managed setup audit-window tracking."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from lib.operation_state import OperationRecord
from lib import security_activity


class TestSecurityActivity(unittest.TestCase):
    def _record(self) -> OperationRecord:
        started = datetime.now(timezone.utc) - timedelta(minutes=5)
        return OperationRecord(
            schema_version=1,
            operation_id="setup-1",
            operation_type="target_setup",
            resource="server_lite",
            phase="applying",
            status="in_progress",
            started_at=started.isoformat(),
            updated_at=started.isoformat(),
            context={},
        )

    def test_completed_setup_advances_only_the_audit_cursor(self) -> None:
        now = datetime.now()
        with tempfile.TemporaryDirectory() as directory, patch.object(
            security_activity,
            "SETUP_ACTIVITY_FILE",
            os.path.join(directory, "activity.json"),
        ):
            record = self._record()
            security_activity.record_setup_activity(record, "in_progress")
            security_activity.record_setup_activity(record, "succeeded")

            windows = security_activity.managed_setup_audit_windows(
                now - timedelta(minutes=15),
                datetime.now(),
            )

        self.assertEqual(len(windows), 1)
        window = windows[0]
        self.assertLessEqual(window[0], window[1])
        self.assertGreater(window[1], now - timedelta(seconds=1))

    def test_old_or_untrusted_activity_does_not_suppress_audit_events(self) -> None:
        now = datetime.now()
        with tempfile.TemporaryDirectory() as directory:
            activity_path = os.path.join(directory, "activity.json")
            with open(activity_path, "w", encoding="utf-8") as file_obj:
                json.dump(
                    {
                        "schema_version": 1,
                        "operation_id": "old",
                        "operation_type": "target_setup",
                        "status": "succeeded",
                        "started_at": (now - timedelta(days=2)).isoformat(),
                        "finished_at": (now - timedelta(days=2)).isoformat(),
                    },
                    file_obj,
                )
            with patch.object(security_activity, "SETUP_ACTIVITY_FILE", activity_path):
                self.assertEqual(
                    security_activity.managed_setup_audit_windows(
                        now - timedelta(minutes=15), now
                    ), []
                )
                os.unlink(activity_path)
                os.symlink("missing", activity_path)
                self.assertEqual(
                    security_activity.managed_setup_audit_windows(
                        now - timedelta(minutes=15), now
                    ), []
                )

    def test_repeated_setups_keep_separate_windows_and_second_precision(self) -> None:
        from dataclasses import replace

        now = datetime(2026, 10, 4, 18)
        with tempfile.TemporaryDirectory() as directory, patch.object(
            security_activity, "SETUP_ACTIVITY_FILE", os.path.join(directory, "activity.json"),
        ), patch.object(security_activity, "_timestamp", side_effect=[
            "2026-10-04T13:00:00.500000", "2026-10-04T13:10:00.500000",
            "2026-10-04T16:00:00.500000", "2026-10-04T16:10:00.500000",
        ]):
            first = self._record()
            security_activity.record_setup_activity(first, "in_progress")
            security_activity.record_setup_activity(first, "succeeded")
            second = replace(first, operation_id="setup-2")
            security_activity.record_setup_activity(second, "in_progress")
            security_activity.record_setup_activity(second, "failed")
            windows = security_activity.managed_setup_audit_windows(now - timedelta(days=1), now)
        self.assertEqual(windows, [
            (datetime(2026, 10, 4, 13), datetime(2026, 10, 4, 13, 10, 1)),
            (datetime(2026, 10, 4, 16), datetime(2026, 10, 4, 16, 10, 1)),
        ])


if __name__ == "__main__":
    unittest.main()
