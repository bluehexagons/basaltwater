"""Filesystem and pressure snapshots must remain bounded and honest."""

from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from common.service_tools import web_panel_host as host
from common.service_tools import web_panel_service as panel


def _mount(target: str = "/", source: str = "/dev/vda1", **updates: object) -> dict[str, object]:
    return {
        "target": target, "source": source, "fstype": "ext4", "fsroot": "/",
        "size": 100 * 1024**3, "used": 50 * 1024**3, "avail": 45 * 1024**3,
        "ino.total": "1000", "ino.used": "100", "ino.avail": "900",
        "options": "rw,relatime", **updates,
    }


class HostSnapshotTest(unittest.TestCase):
    def _collect(self, payload: object, returncode: int = 0) -> list[dict[str, str]]:
        def run(command: list[str], **kwargs: object) -> SimpleNamespace:
            self.assertEqual(command[0], "findmnt")
            self.assertEqual(command[command.index("--task") + 1], "1")
            self.assertEqual(kwargs["timeout"], 2)
            self.assertNotIn("nfs", command[command.index("--types") + 1].split(","))
            kwargs["stdout"].write(json.dumps(payload).encode())
            return SimpleNamespace(returncode=returncode)

        with patch.object(host.subprocess, "run", side_effect=run):
            return host.collect_filesystems()

    def test_separate_home_var_boot_and_data_report_capacity_and_inodes(self) -> None:
        mounts = [
            _mount(), _mount("/home", "/dev/vda2", used=90 * 1024**3, avail=5 * 1024**3),
            _mount("/var", "/dev/vda3", **{"ino.used": "950", "ino.avail": "50"}),
            _mount("/boot/efi", "/dev/vda4", fstype="vfat", options="ro", **{"ino.total": "0"}),
            _mount("/mnt/data archive", "/dev/mapper/data"),
        ]
        rows = self._collect({"filesystems": mounts})
        by_target = {row["label"]: row for row in rows}
        self.assertEqual(set(by_target), {"/", "/home", "/var", "/boot/efi", "/mnt/data archive"})
        # Reserved blocks must not be mistaken for space a user can write.
        self.assertEqual(by_target["/"]["percent"], "53")
        self.assertEqual(by_target["/home"]["percent"], "95")
        self.assertEqual(by_target["/var"]["inode_percent"], "95")
        self.assertEqual(rows[0]["label"], "/home")
        self.assertEqual(by_target["/boot/efi"]["read_only"], "yes")
        self.assertEqual(by_target["/boot/efi"]["inode_status"], "Not reported")

    def test_deduplicates_bind_aliases_but_keeps_btrfs_subvolumes(self) -> None:
        rows = self._collect({"filesystems": [
            _mount("/mnt/root-copy"), _mount(), _mount("/mnt/another-copy"),
            _mount("/srv", "/dev/vdb1[/srv]", fstype="btrfs", fsroot="/srv"),
            _mount("/home", "/dev/vdb1[/home]", fstype="btrfs", fsroot="/home"),
        ]})
        self.assertEqual({row["label"] for row in rows}, {"/", "/srv", "/home"})

    def test_large_counters_round_up_without_float_precision_loss(self) -> None:
        unit = 2**54
        total = 100 * unit
        for used, percent in ((0, 0), (79 * unit, 79), (79 * unit + 1, 80),
                              (95 * unit + 1, 96), (total, 100)):
            with self.subTest(used=used):
                row = self._collect({"filesystems": [_mount(
                    size=total, used=used, avail=total - used,
                    **{"ino.total": total, "ino.used": used, "ino.avail": total - used},
                )]})[0]
                self.assertEqual(row["percent"], str(percent))
                self.assertEqual(row["inode_percent"], str(percent))

    def test_omits_virtual_remote_container_layers_and_invalid_mounts(self) -> None:
        rows = self._collect({"filesystems": [
            _mount(), _mount("/proc", "proc", fstype="proc"),
            _mount("/mnt/remote", "server:/export", fstype="nfs"),
            _mount("/var/lib/docker/layer", "overlay", fstype="overlay"),
            _mount("relative"), _mount("/bad\npath"), None,
        ]})
        self.assertEqual([row["label"] for row in rows if row["kind"] == "filesystem"], ["/"])
        self.assertIn("Omitted 6", rows[-1]["description"])
        overlay = self._collect({"filesystems": [_mount(source="overlay", fstype="overlay")]})
        self.assertEqual(overlay[0]["label"], "/")

    def test_missing_or_malformed_counters_are_not_zero_usage(self) -> None:
        for counters in (
            {"size": None}, {"used": True}, {"avail": -1}, {"size": "NaN"},
            {"used": 2**80}, {"used": 200 * 1024**3},
        ):
            with self.subTest(counters=counters):
                row = self._collect({"filesystems": [_mount(**counters)]})[0]
                self.assertEqual(row["value"], "Unavailable")
                self.assertEqual(row["percent"], "")
                self.assertEqual(row["total"], "")
        row = self._collect({"filesystems": [_mount(**{"ino.used": None})]})[0]
        self.assertEqual(row["inode_status"], "Unavailable")
        self.assertEqual(row["inode_percent"], "")

    def test_row_limit_preserves_high_usage_mounts_and_explains_truncation(self) -> None:
        mounts = [_mount(f"/data/{number}", f"/dev/disk{number}") for number in range(40)]
        mounts.append(_mount("/home", "/dev/home", used=100 * 1024**3, avail=0))
        rows = self._collect({"filesystems": mounts})
        self.assertEqual(len([row for row in rows if row["kind"] == "filesystem"]), 32)
        self.assertEqual(rows[0]["label"], "/home")
        self.assertIn("32 of 41", rows[-1]["description"])

    def test_failed_queries_remain_explicit(self) -> None:
        for payload, code in (({}, 0), ([], 0), ({"filesystems": []}, 0), ({"filesystems": []}, 1)):
            with self.subTest(payload=payload, code=code):
                self.assertEqual(self._collect(payload, code)[0]["kind"], "filesystem_issue")
        for error in (FileNotFoundError(), subprocess.TimeoutExpired("findmnt", 2)):
            with self.subTest(error=error), patch.object(host.subprocess, "run", side_effect=error):
                self.assertEqual(host.collect_filesystems()[0]["value"], "Unavailable")

    def test_invalid_and_oversized_json_is_rejected(self) -> None:
        for raw in (b"not json", b"\xff", b"x" * (256 * 1024 + 1)):
            def run(_command: list[str], **kwargs: object) -> SimpleNamespace:
                kwargs["stdout"].write(raw)
                return SimpleNamespace(returncode=0)
            with self.subTest(length=len(raw)), patch.object(host.subprocess, "run", side_effect=run):
                self.assertEqual(host.collect_filesystems()[0]["kind"], "filesystem_issue")

    def test_pressure_reads_kernel_windows_without_collecting_utilization(self) -> None:
        def read(path: str, **_kwargs: object) -> io.StringIO:
            self.assertIn(path, {"/proc/pressure/cpu", "/proc/pressure/memory", "/proc/pressure/io"})
            return io.StringIO("some avg10=12.34 avg60=5.67 avg300=1.23 total=1000\nfull avg10=0.00\n")
        with patch("builtins.open", side_effect=read):
            rows = host.collect_pressure()
        self.assertEqual([row["label"] for row in rows], ["CPU pressure", "Memory pressure", "I/O pressure"])
        self.assertEqual(rows[0]["value"], "12.34% stalled")
        self.assertIn("60s 5.67%", rows[0]["description"])
        self.assertIn("5m 1.23%", rows[0]["description"])

    def test_pressure_failure_and_invalid_numbers_are_unavailable(self) -> None:
        for text in ("", "full avg10=1.0", "some avg10=NaN avg60=0 avg300=0", "some avg10=101 avg60=0 avg300=0", "some avg10=1 avg60=inf avg300=0"):
            with self.subTest(text=text), patch("builtins.open", side_effect=lambda *_args, **_kwargs: io.StringIO(text)):
                self.assertTrue(all(row["value"] == "Unavailable" for row in host.collect_pressure()))
        with patch("builtins.open", side_effect=PermissionError()):
            self.assertTrue(all(row["value"] == "Unavailable" for row in host.collect_pressure()))

    def test_storage_rendering_escapes_mounts_and_hides_mount_options(self) -> None:
        rows = self._collect({"filesystems": [
            _mount('/home/<archive>', '/dev/<disk>', options="ro,password=NEVER-DISPLAY", used=100 * 1024**3, avail=0),
        ]})
        rendered = host.render_filesystems(rows, panel._format_bytes)
        self.assertIn("/home/&lt;archive&gt;", rendered)
        self.assertIn("/dev/&lt;disk&gt;", rendered)
        self.assertIn("Read-only", rendered)
        self.assertIn("100% used", rendered)
        self.assertIn("critical", rendered)
        self.assertIn('aria-label="/home/&lt;archive&gt; space used"', rendered)
        self.assertNotIn("NEVER-DISPLAY", rendered)
        self.assertNotIn("<archive>", rendered)

    def test_dashboard_uses_one_cached_snapshot_and_root_fallback(self) -> None:
        rows = self._collect({"filesystems": [_mount(), _mount("/home", "/dev/vda2")]})
        with tempfile.TemporaryDirectory() as directory:
            state = panel.WebPanelState({
                "host": "test.example", "system_type": "server_lite", "username": "tester",
                "features": {}, "services": [], "access": [],
            }, agent_home=directory, audit_snapshot_path=directory + "/audit.json")
            overview = [
                {"label": "Memory", "value": "25% used", "description": "3 GiB available"},
                {"label": "Root disk", "value": "50% used", "description": "Fallback"},
                {"label": "Root inodes", "value": "10% used", "description": "Fallback"},
                {"kind": "pressure", "label": "CPU pressure", "value": "1.00% stalled", "description": "10s average"},
                *rows,
            ]
            with (
                patch.object(panel, "collect_system_overview", return_value=overview) as collect,
                patch.object(panel, "discover_basaltwater_web_services", return_value=[]),
                patch.object(panel, "discover_certificate_trust", return_value=None),
                patch.object(state, "audit_snapshot", return_value={"events": [], "status": "ok"}),
                patch.object(state.agent_tasks, "snapshot", return_value={"tasks": [], "runs": []}),
                patch.object(panel.time, "monotonic", return_value=100),
            ):
                rendered = panel.render_page(state)
                panel.render_page(state)
                collect.assert_called_once()
                self.assertIn("Mounted storage", rendered)
                self.assertIn("/home", rendered)
                self.assertIn("Resource pressure", rendered)
                self.assertNotIn("Root disk", rendered)
                for failed_rows in (
                    [host._issue("Query timed out")],
                    [host._filesystem(_mount(size=None)), host._issue("Query timed out")],
                ):
                    state._overview = []
                    collect.return_value = [overview[1], *failed_rows]
                    fallback = panel.render_page(state)
                    self.assertIn("Root disk", fallback)
                    self.assertIn("Query timed out", fallback)

    def test_host_cache_expires_after_thirty_seconds_and_caches_failed_queries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = panel.WebPanelState({"features": {}}, agent_home=directory)
            failed = [host._issue("Filesystem query timed out")]
            with (
                patch.object(panel, "collect_system_overview", return_value=failed) as collect,
                patch.object(panel.time, "monotonic", side_effect=[0, 0, 1, 31, 31]),
            ):
                for _ in range(3):
                    self.assertEqual(state.system_overview(), failed)
            self.assertEqual(collect.call_count, 2)


if __name__ == "__main__":
    unittest.main()
