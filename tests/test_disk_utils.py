"""Disk accounting contracts use fixed observations, independent of the host."""

from __future__ import annotations

from collections import namedtuple
import unittest
from unittest.mock import patch

from lib import disk_utils as disk


Usage = namedtuple("Usage", "total used free")
MB = 1024 * 1024


class DiskUsageTests(unittest.TestCase):
    @patch("lib.disk_utils.shutil.disk_usage", return_value=Usage(10 * MB, 6 * MB, 4 * MB))
    def test_accounts_for_bytes_and_multiple_paths(self, usage):
        expected = {"total_mb": 10, "used_mb": 6, "free_mb": 4, "usage_percent": 60}
        self.assertEqual(disk.get_free_disk_mb("/fixture"), 4)
        self.assertEqual(disk.get_total_disk_mb("/fixture"), 10)
        self.assertEqual(disk.get_disk_usage_details("/fixture"), expected)
        usage.assert_called_with("/fixture")
        self.assertEqual(disk.get_multiple_paths_usage(["/first", "/second"]),
                         {"/first": expected, "/second": expected})
        usage.reset_mock()
        self.assertEqual(disk.get_multiple_paths_usage([]), {})
        usage.assert_not_called()

    def test_unavailable_usage_returns_zero_for_each_reader(self):
        for failure in (OSError("unavailable"), AttributeError("missing fields")):
            with self.subTest(failure=failure), patch("lib.disk_utils.shutil.disk_usage", side_effect=failure):
                self.assertEqual(disk.get_free_disk_mb("/fixture"), 0)
                self.assertEqual(disk.get_total_disk_mb("/fixture"), 0)
                self.assertEqual(disk.get_multiple_paths_usage(["/fixture"]), {
                    "/fixture": {"total_mb": 0, "used_mb": 0, "free_mb": 0, "usage_percent": 0},
                })

    @patch("lib.disk_utils.shutil.disk_usage", return_value=Usage(0, 0, 0))
    def test_empty_device_has_no_percentage(self, _usage):
        self.assertEqual(disk.get_disk_usage_details("/fixture")["usage_percent"], 0)

    def test_thresholds_include_the_boundary_and_respect_overrides(self):
        for warning, critical in ((80, 90), (60, 75)):
            for percent, status in ((warning - 1, "ok"), (warning, "warning"),
                                    (critical - 1, "warning"), (critical, "critical")):
                with self.subTest(warning=warning, critical=critical, percent=percent), patch(
                    "lib.disk_utils.shutil.disk_usage", return_value=Usage(100 * MB, percent * MB, (100 - percent) * MB),
                ):
                    self.assertEqual(disk.check_disk_space_threshold("/fixture", warning, critical), (status, percent))

    def test_duration_estimates_include_minimum_and_operation_overhead(self):
        for operation, size, expected in (("sync", 1000, 15), ("scrub", 1000, 30),
                                          ("par2", 1000, 55), ("unknown", 500, 20), ("sync", 0, 6)):
            with self.subTest(operation=operation, size=size):
                self.assertEqual(disk.estimate_operation_duration(operation, size), expected)
