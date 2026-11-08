"""Local filesystem observations stay bounded, read-only and independent of PAR2."""

from __future__ import annotations

import copy
from datetime import datetime
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common.service_tools import web_panel_filesystems as filesystems
from common.service_tools import web_panel_storage as storage, web_panel_shares_data as panel


class FilesystemPanelTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.mountinfo = self.root / "mountinfo"
        self.sysfs = self.root / "sysfs"
        self.mountinfo.write_text("")
        self.sysfs.mkdir()
        (self.sysfs / "features").mkdir()
        self.enterContext(patch.multiple(filesystems, MOUNTINFO=str(self.mountinfo), EXT4_SYSFS=str(self.sysfs)))

    def mount(self, kind="ext4", target="/srv/archive", options="rw", super_options="rw", source="/dev/mapper/archive"):
        return f"32 20 253:0 / {target} {options},relatime shared:5 - {kind} {source} {super_options}\n"

    def device(self, name="dm-0", count="7", first="1767225600", last="1767312000"):
        directory = self.sysfs / name
        directory.mkdir()
        for field, value in zip(filesystems.EXT4_FIELDS, (count, first, last)):
            (directory / field).write_text(value + "\n")
        return directory

    def test_local_mount_types_escapes_and_both_access_modes_without_probes(self):
        self.mountinfo.write_text(self.mount("ext4", r"/srv/shared\040files", options="ro")
                                  + self.mount("xfs", "/", super_options="ro")
                                  + self.mount("f2fs", r"/srv/literal\134040")
                                  + self.mount("nfs4", source="server:/PRIVATE")
                                  + self.mount("cifs", super_options="rw,password=SECRET")
                                  + self.mount("fuseblk") + self.mount("overlay"))
        with patch("subprocess.run") as run, patch.object(filesystems.os, "stat") as stat_call, patch.object(filesystems.os, "statvfs") as statvfs:
            value = filesystems.collect_filesystem_snapshot()
        run.assert_not_called()
        stat_call.assert_not_called()
        statvfs.assert_not_called()
        self.assertEqual([row["fstype"] for row in value["mounts"]], ["xfs", "ext4", "f2fs"])
        self.assertEqual([row["read_only"] for row in value["mounts"]], [True, True, False])
        self.assertEqual(value["mounts"][1]["target"], "/srv/shared files")
        self.assertEqual(value["mounts"][2]["target"], r"/srv/literal\040")
        self.assertNotIn("PRIVATE", json.dumps(value))
        self.assertNotIn("SECRET", json.dumps(value))
        filesystems.validate_filesystem_snapshot(value)

    def test_ext4_history_is_not_health_and_timestamps_are_utc(self):
        self.device()
        value = filesystems.collect_filesystem_snapshot()
        body = filesystems.render_filesystem_snapshot(value)
        self.assertTrue(value["ext4_available"])
        self.assertEqual(value["ext4"][0]["errors_count"], 7)
        self.assertIn("Recorded filesystem errors", body)
        self.assertIn("2026-01-01 00:00:00 UTC", body)
        self.assertIn("2026-01-02 00:00:00 UTC", body)
        self.assertIn("zero does not prove current health", body)
        self.assertIn("filesystem unmounted", body)
        self.assertNotIn("<button", body)

    def test_missing_invalid_and_symlinked_fields_do_not_erase_other_history(self):
        directory = self.device(count="0", first="0")
        last = directory / "last_error_time"
        for raw in ("-1", "9" * 40, str(2**40), "not-a-time"):
            with self.subTest(raw=raw):
                last.write_text(raw)
                row = filesystems.collect_filesystem_snapshot()["ext4"][0]
                self.assertIsNone(row["last_error_time"])
                self.assertEqual(row["errors_count"], 0)
        last.unlink()
        last.symlink_to(directory / "errors_count")
        row = filesystems.collect_filesystem_snapshot()["ext4"][0]
        self.assertIsNone(row["last_error_time"])
        self.assertIn("Not recorded", filesystems.render_filesystem_snapshot(filesystems.collect_filesystem_snapshot()))
        (directory / "errors_count").unlink()
        self.assertIsNone(filesystems.collect_filesystem_snapshot()["ext4"][0]["errors_count"])

    def test_missing_inventory_differs_from_empty_and_bad_mounts_leave_ext4_visible(self):
        self.assertEqual(filesystems.collect_filesystem_snapshot()["mounts"], [])
        self.device()
        for raw in ("invalid\n", self.mount(target=r"/srv/invalid\012name"), self.mount(options="ro,rw")):
            with self.subTest(raw=raw):
                self.mountinfo.write_text(raw)
                value = filesystems.collect_filesystem_snapshot()
                self.assertTrue(value["available"])
                self.assertEqual(value["rejected_mounts"], 1)
                self.assertTrue(value["ext4_available"])
                self.assertEqual(value["ext4"][0]["errors_count"], 7)
                body = filesystems.render_filesystem_snapshot(value)
                self.assertIn("Inventory is incomplete", body)
                self.assertNotIn("No supported local filesystem mounts", body)
        self.mountinfo.unlink()
        value = filesystems.collect_filesystem_snapshot()
        self.assertFalse(value["available"])
        with patch.object(filesystems, "EXT4_SYSFS", str(self.root / "missing")):
            self.assertTrue(filesystems.collect_filesystem_snapshot()["ext4_available"])
        with patch.object(filesystems.os, "scandir", side_effect=PermissionError):
            self.assertFalse(filesystems.collect_filesystem_snapshot()["ext4_available"])

    def test_unrepresentable_mount_does_not_hide_other_mounts_and_read_only_summary_is_visible(self):
        self.mountinfo.write_text(self.mount("xfs", target="/srv/read-only", super_options="ro")
                                  + self.mount("ext4", target="/srv/data")
                                  + self.mount(target=r"/srv/unrepresentable\011name"))
        value = filesystems.collect_filesystem_snapshot()
        filesystems.validate_filesystem_snapshot(value)
        self.assertEqual(len(value["mounts"]), 2)
        self.assertEqual(value["rejected_mounts"], 1)
        body = filesystems.render_filesystem_snapshot(value)
        self.assertIn("1 malformed or unsupported-name mount records omitted", body)
        summary = body.split("<summary>", 1)[1].split("</summary>", 1)[0]
        self.assertIn("2 shown", summary)
        self.assertIn("1 read-only", summary)
        self.assertIn("/srv/data", body)
        self.assertIn("Read-only mounts may be intentional", body)

    def test_mount_limit_keeps_root_and_read_only_records_and_supports_previous_exports(self):
        self.mountinfo.write_text(''.join(self.mount(target=f"/srv/data{i}") for i in range(4))
                                  + self.mount(target="/", options="rw")
                                  + self.mount(target="/z-last", options="ro"))
        with patch.object(filesystems, "MAX_MOUNTS", 2):
            value = filesystems.collect_filesystem_snapshot()
            self.assertEqual([row["target"] for row in value["mounts"]], ["/", "/z-last"])
            self.assertEqual(value["omitted_mounts"], 4)
            del value["rejected_mounts"]
            filesystems.validate_filesystem_snapshot(value)
            self.assertIn("1 read-only", filesystems.render_filesystem_snapshot(value))
            value["rejected_mounts"] = True
            with self.assertRaises(ValueError):
                filesystems.validate_filesystem_snapshot(value)

    def test_inventory_bounds_and_symlinked_device_fail_closed(self):
        self.mountinfo.write_text(''.join(self.mount(target=f"/srv/data{i}") for i in range(4)))
        self.device()
        with patch.object(filesystems, "MAX_MOUNTS", 2):
            value = filesystems.collect_filesystem_snapshot()
            self.assertEqual((len(value["mounts"]), value["omitted_mounts"]), (2, 2))
            self.assertIn("2 additional local mounts omitted", filesystems.render_filesystem_snapshot(value))
        self.mountinfo.write_bytes(b"x" * (filesystems.MAX_BYTES + 1))
        self.assertFalse(filesystems.collect_filesystem_snapshot()["available"])
        with patch.object(filesystems, "MAX_EXT4", 0):
            self.assertFalse(filesystems.collect_filesystem_snapshot()["ext4_available"])
        (self.sysfs / "dm-1").symlink_to(self.sysfs / "dm-0", target_is_directory=True)
        self.assertFalse(filesystems.collect_filesystem_snapshot()["ext4_available"])

    def test_invalid_export_is_isolated_from_par2_and_btrfs(self):
        self.mountinfo.write_text(self.mount())
        self.device()
        value = filesystems.collect_filesystem_snapshot()
        mutations = (("available", "yes"), ("omitted_mounts", -1), ("ext4", [{"device": "../private"}]),
                     ("mounts", [{**value["mounts"][0], "fstype": "cifs"}]))
        for key, invalid in mutations:
            with self.subTest(key=key):
                bad = copy.deepcopy(value)
                bad[key] = invalid
                snapshot = {"version": 1, "available": True, "generated_at": "2026-01-01T00:00:00+00:00",
                            "jobs": [], "omitted_jobs": 0, "filesystems": bad,
                            "btrfs": {"available": True, "filesystems": []}}
                path = self.root / "snapshot.json"
                path.write_text(json.dumps(snapshot))
                loaded = storage.load_storage_snapshot(str(path), now=datetime.fromisoformat(snapshot["generated_at"]))
                self.assertTrue(loaded["available"])
                self.assertTrue(loaded["btrfs"]["available"])
                self.assertIsNone(loaded["filesystems"])
        value["ext4"][0]["errors_count"] = True
        with self.assertRaises(ValueError):
            filesystems.validate_filesystem_snapshot(value)

    def test_page_exposes_ext4_without_par2_and_http_only_reads_export(self):
        self.device()
        self.mountinfo.write_text(self.mount("xfs", target="/srv/<archive>"))
        value = filesystems.collect_filesystem_snapshot()
        snapshot = {"available": True, "generated_at": "2026-01-01T00:00:00+00:00", "jobs": [],
                    "omitted_jobs": 0, "filesystems": value, "inventory": {"available": False}}
        self.assertIn("scrubs", panel._visible_views(snapshot, {}, {}))
        state = SimpleNamespace(manifest={"host": "nas.example.test"}, navigation=lambda _: ())
        for view in ("overview", "scrubs"):
            with self.subTest(view=view), patch.object(storage, "load_storage_snapshot", return_value=snapshot), patch.object(filesystems, "collect_filesystem_snapshot") as collect, patch.object(filesystems, "_read") as read, patch("subprocess.run") as run:
                body = panel.render_shares_data(state, "", {"view": view})
            collect.assert_not_called()
            read.assert_not_called()
            run.assert_not_called()
            self.assertIn("Linux filesystems", body)
            self.assertIn("dm-0", body)
            self.assertIn("xfs_scrub -n MOUNTPOINT", body)
            self.assertIn("&lt;archive&gt;", body)
            self.assertNotIn("<archive>", body)
