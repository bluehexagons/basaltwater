"""Kernel selection management without touching host packages."""

from __future__ import annotations

import re
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from lib import kernel_cleanup
from common.service_tools import cleanup_maintenance


class TestKernelCleanup(unittest.TestCase):
    def setUp(self):
        self.running = "7.0.14-15-pve"
        self.packages = {
            "proxmox-default-kernel", "proxmox-kernel-helper",
            "proxmox-kernel-6.8", "proxmox-kernel-6.17", "proxmox-kernel-7.0",
            *{f"proxmox-kernel-{version}-pve-signed" for version in (
                "6.8.12-9", "6.8.12-17", "6.17.13-21",
                "7.0.14-14", "7.0.14-15", "7.0.14-16",
            )},
        }
        self.held = set()
        self.manual = set(self.packages)
        self.protected = {"proxmox-kernel-6.17", "proxmox-kernel-6.17.13-21-pve-signed"}
        self.config = None
        for target, kwargs in (
            ("is_container", {"return_value": False}),
            ("can_modify_kernel", {"return_value": True}),
            ("os.uname", {"side_effect": lambda: SimpleNamespace(release=self.running)}),
            ("Path.is_file", {"return_value": True}),
            ("subprocess.run", {"side_effect": self.run_command}),
        ):
            mocker = patch("lib.kernel_cleanup." + target, **kwargs)
            setattr(self, target.split(".")[-1], mocker.start())
            self.addCleanup(mocker.stop)

    def run_command(self, command, **kwargs):
        if command[0] == "dpkg-query":
            output = "".join(f"installed {package}\n" for package in sorted(self.packages))
        elif command == ["apt-mark", "showmanual"]:
            output = "\n".join(self.manual)
        elif command == ["apt-mark", "showhold"]:
            output = "\n".join(self.held)
        elif command == ["apt-config", "dump"]:
            output = self.config if self.config is not None else '\n'.join(
                f'APT::NeverAutoRemove:: "^{re.escape(package)}$";'
                for package in self.protected
            )
        elif command[:2] == ["dpkg", "--compare-versions"]:
            # Numeric fixture versions; no real dpkg invocation in tests.
            key = lambda value: tuple(int(part) for part in re.findall(r"\d+", value))
            return subprocess.CompletedProcess(command, int(not key(command[2]) < key(command[4])), "", "")
        else:
            self.fail(f"Unexpected system command: {command}")
        return subprocess.CompletedProcess(command, 0, output, "")

    def test_devhost_old_manual_series_and_images_are_managed(self):
        self.assertEqual(kernel_cleanup.obsolete_manual_kernels(), [
            "proxmox-kernel-6.8", "proxmox-kernel-6.8.12-17-pve-signed",
            "proxmox-kernel-6.8.12-9-pve-signed",
        ])

    def test_holds_and_retention_rules_preserve_explicit_fallbacks(self):
        self.held = {"proxmox-kernel-6.8"}
        self.protected.update(package for package in self.packages if "6.8.12" in package)
        self.assertEqual(kernel_cleanup.obsolete_manual_kernels(), [])

    def test_debian_images_keep_running_newer_and_one_older_fallback(self):
        self.running = "6.1.0-30-amd64"
        self.packages = {"linux-image-amd64", "linux-image-6.1.0-20-cloud-amd64"} | {
            f"linux-image-6.1.0-{version}-amd64" for version in (20, 29, 30, 31)
        }
        self.manual = set(self.packages)
        self.protected = {"linux-image-amd64"}
        self.assertEqual(kernel_cleanup.obsolete_manual_kernels(), ["linux-image-6.1.0-20-amd64"])

    def test_ubuntu_unsigned_images(self):
        self.running = "6.8.0-50-generic"
        self.packages = {f"linux-image-unsigned-6.8.0-{version}-generic" for version in (40, 49, 50)}
        self.manual = set(self.packages)
        self.assertEqual(kernel_cleanup.obsolete_manual_kernels(), ["linux-image-unsigned-6.8.0-40-generic"])

    def test_already_automatic_packages_are_unchanged(self):
        self.manual = set()
        self.assertEqual(kernel_cleanup.obsolete_manual_kernels(), [])

    def test_container_skips_inspection(self):
        self.is_container.return_value = True
        self.assertEqual(kernel_cleanup.obsolete_manual_kernels(), [])
        self.run.assert_not_called()

    def test_machine_without_kernel_capability_skips_inspection(self):
        self.can_modify_kernel.return_value = False
        self.assertEqual(kernel_cleanup.obsolete_manual_kernels(), [])
        self.run.assert_not_called()

    def test_unknown_kernel_skips_inspection(self):
        self.running = "custom"
        self.assertEqual(kernel_cleanup.obsolete_manual_kernels(), [])
        self.run.assert_not_called()

    def test_missing_running_image_fails_closed(self):
        self.is_file.return_value = False
        with self.assertRaises(RuntimeError):
            kernel_cleanup.obsolete_manual_kernels()

    def test_bad_retention_rules_fail_closed(self):
        for config in ('', 'APT::NeverAutoRemove:: "[";', 'APT::NeverAutoRemove:: invalid;'):
            with self.subTest(config=config):
                self.config = config
                with self.assertRaises((ValueError, RuntimeError, re.error)):
                    kernel_cleanup.obsolete_manual_kernels()

    def test_inspection_failure_does_not_mutate_packages(self):
        self.run.side_effect = subprocess.CalledProcessError(1, ["apt-mark"])
        with self.assertRaises(subprocess.CalledProcessError):
            kernel_cleanup.obsolete_manual_kernels()


class TestKernelCleanupIntegration(unittest.TestCase):
    def setUp(self):
        mocker = patch.object(cleanup_maintenance, "is_proxmox_host", return_value=False)
        mocker.start()
        self.addCleanup(mocker.stop)

    @patch("common.service_tools.cleanup_maintenance.shutil.which", return_value="/usr/bin/apt-get")
    @patch("common.service_tools.cleanup_maintenance.run_cleanup_command", return_value=None)
    @patch("common.service_tools.cleanup_maintenance.obsolete_manual_kernels", return_value=["linux-image-6.1.0-20-amd64"])
    def test_marks_before_autoremove(self, _plan, run, _which):
        self.assertEqual(cleanup_maintenance.cleanup_unused_packages(), [])
        self.assertEqual(run.call_args_list[0].args[0], ["apt-mark", "auto", "linux-image-6.1.0-20-amd64"])
        self.assertEqual(run.call_args_list[1].args[0][1], "autoremove")

    @patch("common.service_tools.cleanup_maintenance.shutil.which", return_value="/usr/bin/apt-get")
    @patch("common.service_tools.cleanup_maintenance.run_cleanup_command")
    @patch("common.service_tools.cleanup_maintenance.obsolete_manual_kernels", side_effect=RuntimeError("bad inventory"))
    def test_inspection_failure_prevents_autoremove(self, _plan, run, _which):
        self.assertIn("bad inventory", cleanup_maintenance.cleanup_unused_packages()[0])
        run.assert_not_called()

    @patch("common.service_tools.cleanup_maintenance.shutil.which", return_value="/usr/bin/apt-get")
    @patch("common.service_tools.cleanup_maintenance.run_cleanup_command", return_value="mark failed")
    @patch("common.service_tools.cleanup_maintenance.obsolete_manual_kernels", return_value=["linux-image-6.1.0-20-amd64"])
    def test_mark_failure_prevents_autoremove(self, _plan, run, _which):
        self.assertEqual(cleanup_maintenance.cleanup_unused_packages(), ["mark failed"])
        run.assert_called_once()
