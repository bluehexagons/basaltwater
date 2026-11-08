"""Btrfs observations remain separate from PAR2 and never run maintenance."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common.service_tools import web_panel_btrfs as btrfs, web_panel_storage as storage, web_panel_shares_data as panel

FSID = "12345678-1234-1234-1234-123456789abc"


class BtrfsPanelTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.sysfs = self.root / "sysfs"
        self.state = self.root / "state"
        self.sysfs.mkdir()
        self.state.mkdir()
        (self.sysfs / "features").mkdir()
        self.enterContext(patch.multiple(btrfs, SYSFS=str(self.sysfs), SCRUB_STATE=str(self.state)))

    def device(self, identifier="1", errors=0):
        directory = self.sysfs / FSID / "devinfo" / identifier
        directory.mkdir(parents=True)
        path = directory / "error_stats"
        path.write_text(''.join(f"{key} {errors if key == 'corruption_errs' else 0}\n" for key in btrfs.COUNTERS))
        return path

    def scrub(self, identifiers=("1",), finished=1, canceled=0):
        path = self.state / ("scrub.status." + FSID)
        path.write_text("scrub status:1\n" + ''.join(
            f"{FSID}:{device}|finished:{finished}|canceled:{canceled}|t_start:100|corrected_errors:2|uncorrectable_errors:3|unverified_errors:0\n"
            for device in identifiers))
        return path

    def test_no_btrfs_and_missing_kernel_root_are_distinct_from_unreadable(self):
        self.assertEqual(btrfs.collect_btrfs(), {"available": True, "filesystems": []})
        with patch.object(btrfs, "SYSFS", str(self.root / "missing")):
            self.assertTrue(btrfs.collect_btrfs()["available"])
        with patch.object(btrfs.os, "scandir", side_effect=PermissionError):
            self.assertFalse(btrfs.collect_btrfs()["available"])

    def test_device_history_and_partial_scrub_coverage_are_exposed_without_claiming_health(self):
        self.device(errors=7)
        self.device("2")
        self.scrub()
        with patch("subprocess.run") as run:
            value = btrfs.collect_btrfs()
            body = btrfs.render_btrfs(btrfs.validate_btrfs(value))
        run.assert_not_called()
        self.assertEqual(value["filesystems"][0]["devices"][0]["counters"]["corruption_errs"], 7)
        self.assertIn("Recorded device errors", body)
        self.assertIn("Devices in record: 1 of 2", body)
        self.assertIn("Uncorrectable: 3", body)
        self.assertIn("PAR2", body)
        self.assertIn("do not prove current health", body)
        self.assertNotIn("<button", body)

    def test_unfinished_cancelled_and_absent_native_records(self):
        self.device()
        self.assertEqual(btrfs.collect_btrfs()["filesystems"][0]["scrub"]["state"], "not_recorded")
        for finished, canceled, expected in ((0, 0, "unfinished"), (1, 1, "cancelled"), (1, 0, "completed")):
            with self.subTest(expected=expected):
                self.scrub(finished=finished, canceled=canceled)
                value = btrfs.collect_btrfs()
                self.assertEqual(value["filesystems"][0]["scrub"]["state"], expected)
                btrfs.validate_btrfs(value)

    def test_bad_or_symlinked_metadata_is_unavailable_and_keeps_device_history(self):
        stats = self.device(errors=5)
        path = self.scrub()
        original = path.read_text()
        for text in ("scrub status:2\n", original.replace(FSID, "other"), original + original.splitlines()[1] + "\n",
                     original.replace("finished:1", "finished:2"), original.replace("t_start:100", "t_start:0")):
            with self.subTest(text=text):
                path.write_text(text)
                row = btrfs.collect_btrfs()["filesystems"][0]
                self.assertEqual(row["scrub"]["state"], "unavailable")
                self.assertEqual(row["devices"][0]["counters"]["corruption_errs"], 5)
        stats.unlink()
        stats.symlink_to(path)
        self.assertIsNone(btrfs.collect_btrfs()["filesystems"][0]["devices"][0]["counters"])

    def test_limits_and_unsupported_counters_fail_closed(self):
        path = self.device()
        path.write_text("unsupported 0\n")
        self.assertIsNone(btrfs.collect_btrfs()["filesystems"][0]["devices"][0]["counters"])
        with patch.object(btrfs, "MAX_FILESYSTEMS", 0):
            self.assertFalse(btrfs.collect_btrfs()["available"])
        self.scrub().write_bytes(b"x" * 65537)
        self.assertEqual(btrfs.collect_btrfs()["filesystems"][0]["scrub"]["state"], "unavailable")

    def test_invalid_native_export_does_not_hide_par2_or_render_injected_text(self):
        self.device()
        value = btrfs.collect_btrfs()
        invalid = copy.deepcopy(value)
        invalid["filesystems"][0]["uuid"] = "<script>private</script>"
        self.assertIn("unavailable", btrfs.render_btrfs(invalid))
        self.assertNotIn("<script>", btrfs.render_btrfs(invalid))
        snapshot = {"version": 1, "available": True, "generated_at": "2026-01-01T00:00:00+00:00",
                    "jobs": [], "omitted_jobs": 0, "btrfs": invalid}
        path = self.root / "snapshot.json"
        path.write_text(json.dumps(snapshot))
        from datetime import datetime
        loaded = storage.load_storage_snapshot(str(path), now=datetime.fromisoformat(snapshot["generated_at"]))
        self.assertTrue(loaded["available"])
        self.assertFalse(loaded["btrfs"]["available"])

    def test_btrfs_is_visible_without_par2_tasks_and_page_load_only_reads_export(self):
        self.device()
        snapshot = {"available": True, "generated_at": "2026-01-01T00:00:00+00:00", "jobs": [],
                    "omitted_jobs": 0, "btrfs": btrfs.collect_btrfs(), "inventory": {"available": False}}
        self.assertIn("scrubs", panel._visible_views(snapshot, {}, {}))
        state = SimpleNamespace(manifest={"host": "nas.example.test"}, navigation=lambda _: ())
        with patch.object(storage, "load_storage_snapshot", return_value=snapshot), patch.object(btrfs, "collect_btrfs") as collect, patch("subprocess.run") as run:
            body = panel.render_shares_data(state, "", {"view": "scrubs"})
        collect.assert_not_called()
        run.assert_not_called()
        self.assertIn("Btrfs filesystem health", body)
        self.assertIn(FSID, body)
