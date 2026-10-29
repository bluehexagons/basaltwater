"""CachyOS restart advice from temporary evidence, without touching the host."""

from __future__ import annotations

from contextlib import ExitStack
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import cachyos_reboot as reboot


class RebootNoticeTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.modules = self.root / "modules"
        self.database = self.root / "packages"
        self.proc_stat = self.root / "stat"
        self.marker = self.root / "reboot-required"
        for name, value in (("MODULES", self.modules), ("LOCAL_DATABASE", self.database),
                            ("PROC_STAT", self.proc_stat), ("REBOOT_MARKER", self.marker)):
            stack.enter_context(patch.object(reboot, name, value))
        stack.enter_context(patch.object(reboot.platform, "release", return_value="6.19-fixture"))
        stack.enter_context(patch.object(reboot.time, "time", return_value=1000))
        stack.enter_context(patch("lib.remote_utils.is_dry_run", return_value=False))
        self.proc_stat.write_text("cpu 1 2 3\nbtime 100\n")
        current = self.modules / "6.19-fixture"
        current.mkdir(parents=True)
        (current / "pkgbase").write_text("linux-cachyos\n")
        for package in (*reboot.CORE_PACKAGES, "linux-cachyos"):
            self.package(package, 50)

    def package(self, name, installed):
        directory = self.database / (name + "-1.0-1")
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "desc"
        path.write_text(f"%NAME%\n{name}\n\n%INSTALLDATE%\n{installed}\n")
        return path

    def test_no_signal_is_limited_to_inspected_booted_kernel_and_core_packages(self):
        self.package("linux-lts", 200)
        self.package("systemd-libs", 200)
        notice = reboot.collect_reboot_notice()
        self.assertEqual(notice.reasons, ())
        self.assertEqual(notice.unknown, ())

    def test_core_or_booted_kernel_package_newer_than_boot_recommends_restart(self):
        for name in (*reboot.CORE_PACKAGES, "linux-cachyos"):
            with self.subTest(package=name):
                self.package(name, 101)
                notice = reboot.collect_reboot_notice()
                self.assertEqual(notice.reasons, (f"{name} was installed or updated after this boot",))
                self.assertFalse(notice.unknown)
                self.package(name, 50)

    def test_removed_running_kernel_modules_remain_a_signal_with_partial_evidence(self):
        current = self.modules / "6.19-fixture"
        (current / "pkgbase").unlink()
        current.rmdir()
        self.proc_stat.write_text("private unrelated data")
        notice = reboot.collect_reboot_notice()
        self.assertEqual(notice.reasons, ("the running kernel's module directory is absent",))
        self.assertTrue(notice.unknown)

    def test_missing_malformed_or_future_metadata_is_unknown(self):
        path = self.package("systemd", 50)
        for text in ("", "%NAME%\nsystemd\n%INSTALLDATE%\ninvalid\n",
                     "%NAME%\nsystemd\n%INSTALLDATE%\n1001\n",
                     "%NAME%\nsystemd\n%INSTALLDATE%\n0\n"):
            with self.subTest(text=text):
                path.write_text(text)
                notice = reboot.collect_reboot_notice()
                self.assertFalse(notice.reasons)
                self.assertEqual(notice.unknown, ("systemd installation time could not be inspected",))

    def test_unsafe_metadata_is_not_followed_or_changed(self):
        path = self.package("glibc", 50)
        path.unlink()
        private = self.root / "private"
        private.write_text("private fixture")
        path.symlink_to(private)
        notice = reboot.collect_reboot_notice()
        self.assertIn("glibc installation time could not be inspected", notice.unknown)
        self.assertTrue(path.is_symlink())
        self.assertEqual(private.read_text(), "private fixture")

    def test_invalid_boot_or_kernel_identity_is_unknown(self):
        for boot in ("", "btime 1001\n", "btime 100\nbtime 200\n"):
            with self.subTest(boot=boot):
                self.proc_stat.write_text(boot)
                notice = reboot.collect_reboot_notice()
                self.assertFalse(notice.reasons)
                self.assertIn("boot time is unavailable for core package checks", notice.unknown)
        self.proc_stat.write_text("btime 100\n")
        with patch.object(reboot.platform, "release", return_value="../private"):
            self.assertIn("the running kernel release could not be validated",
                          reboot.collect_reboot_notice().unknown)

    def test_marker_can_recommend_restart_without_reading_its_content(self):
        self.marker.write_text("private marker fixture")
        notice = reboot.collect_reboot_notice()
        self.assertIn("the system has a reboot-required marker", notice.reasons)
        self.marker.unlink()
        self.marker.symlink_to(self.root / "missing")
        notice = reboot.collect_reboot_notice()
        self.assertIn("the reboot-required marker is not a regular file", notice.unknown)

    def test_report_distinguishes_unknown_and_never_claims_no_restart_required(self):
        self.package("systemd", 200)
        self.proc_stat.unlink()
        config = SimpleNamespace(dry_run=False)
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            reboot.report_reboot_notice(config)
        self.assertIn("status unknown", output.getvalue())
        self.assertNotIn("No kernel", output.getvalue())
        self.proc_stat.write_text("btime 100\n")
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            reboot.report_reboot_notice(config)
        self.assertIn("System restart recommended", output.getvalue())
        self.assertIn("does not reboot", output.getvalue())

    def test_dry_run_does_not_observe_host(self):
        with patch.object(reboot, "collect_reboot_notice") as collect:
            reboot.report_reboot_notice(SimpleNamespace(dry_run=True))
        collect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
