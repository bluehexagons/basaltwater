"""Regression coverage for hosts without unattended-upgrades kernel hooks."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lib import kernel_restart
from lib.config import SetupConfig
from common.common_steps import check_restart_required
from security.security_steps import configure_auto_restart


class KernelRestartTests(unittest.TestCase):
    def test_hook_is_executable_and_preserves_other_hooks_on_rerun(self):
        with tempfile.TemporaryDirectory() as directory:
            hook = Path(directory) / "basaltwater-reboot-required"
            other = Path(directory) / "unattended-upgrades"
            other.write_text("existing hook")
            with patch.object(kernel_restart, "KERNEL_HOOK", str(hook)):
                kernel_restart.install_kernel_restart_hook()
                kernel_restart.install_kernel_restart_hook()
            self.assertEqual(hook.stat().st_mode & 0o777, 0o755)
            self.assertEqual(other.read_text(), "existing hook")
            self.assertEqual(hook.read_text(), kernel_restart.KERNEL_HOOK_CONTENT)

    def test_hook_records_and_deduplicates_without_unattended_upgrades(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "hook"
            script.write_text(
                kernel_restart.KERNEL_HOOK_CONTENT.replace("/run/", directory + "/")
                .replace('$(uname -r)', "7.0.14-15-pve")
            )
            marker = Path(directory) / "reboot-required"
            packages = Path(directory) / "reboot-required.pkgs"
            env = {**os.environ, "DPKG_MAINTSCRIPT_PACKAGE": "proxmox-kernel-7.0.14-16-pve-signed"}
            for release in ("", "7.0.14-15-pve", "bad\nvalue"):
                subprocess.run(["sh", str(script), release], env=env, check=True)
                self.assertFalse(marker.exists())
            packages.write_text("dbus\n")
            for _ in range(2):
                subprocess.run(["sh", str(script), "7.0.14-16-pve"], env=env, check=True)
            self.assertTrue(marker.exists())
            self.assertEqual(packages.read_text(), "dbus\nproxmox-kernel-7.0.14-16-pve-signed\n")

    @patch("lib.kernel_restart.Path.is_file", return_value=True)
    @patch("lib.kernel_restart.os.uname", return_value=SimpleNamespace(release="7.0.14-15-pve"))
    @patch("lib.kernel_restart.subprocess.run")
    def test_ts1_detected_using_only_configured_kernel_packages(self, run, _uname, image):
        run.return_value = SimpleNamespace(stdout=(
            "installed proxmox-kernel-7.0.14-14-pve-signed\n"
            "installed proxmox-kernel-7.0.14-15-pve-signed\n"
            "installed proxmox-kernel-7.0.14-16-pve-signed\n"
            "unpacked proxmox-kernel-7.0.14-17-pve-signed\n"
            "installed proxmox-kernel-7.0\n"
            "installed proxmox-kernel-../../bad\n"
        ))
        query = run.return_value
        run.side_effect = lambda args, **kw: query if args[0] == "dpkg-query" else SimpleNamespace(
            returncode=0 if args[2] == "7.0.14-16-pve" else 1
        )
        self.assertEqual(kernel_restart.newer_installed_kernel(), "7.0.14-16-pve")
        image.return_value = False
        self.assertIsNone(kernel_restart.newer_installed_kernel())
        run.return_value.stdout = "installed proxmox-kernel-7.0.14-15-pve-signed\n"
        image.return_value = True
        self.assertIsNone(kernel_restart.newer_installed_kernel())

    @patch("lib.kernel_restart.subprocess.run")
    @patch("lib.kernel_restart.os.uname", return_value=SimpleNamespace(release="custom"))
    def test_unrecognized_kernel_does_not_query_packages(self, _uname, run):
        self.assertIsNone(kernel_restart.newer_installed_kernel())
        run.assert_not_called()

    @patch("lib.kernel_restart.Path.is_file", return_value=True)
    @patch("lib.kernel_restart.os.uname")
    @patch("lib.kernel_restart.subprocess.run")
    def test_debian_ubuntu_and_cloud_kernels_use_dpkg_ordering(self, run, uname, _image):
        for running, newer in (
            ("6.1.0-9-amd64", "6.1.0-10-amd64"),
            ("6.12.43+deb13-amd64", "6.12.44+deb13-amd64"),
            ("6.12.43+deb13-cloud-amd64", "6.12.44+deb13-cloud-amd64"),
            ("6.8.0-99-generic", "6.8.0-100-generic"),
        ):
            with self.subTest(running=running):
                uname.return_value = SimpleNamespace(release=running)
                def command(args, **kwargs):
                    if args[0] == "dpkg-query":
                        return SimpleNamespace(stdout=(
                            f"installed linux-image-{newer}\n"
                            "installed linux-image-99.0.0-rt-arm64\n"
                            "installed linux-image-amd64\n"
                        ))
                    self.assertEqual(args, ["dpkg", "--compare-versions", newer, "gt", running])
                    return SimpleNamespace(returncode=0)
                run.side_effect = command
                self.assertEqual(kernel_restart.newer_installed_kernel(), newer)

    @patch("lib.kernel_restart.Path.is_file", return_value=True)
    @patch("lib.kernel_restart.os.uname", return_value=SimpleNamespace(release="6.8.0-99-generic"))
    @patch("lib.kernel_restart.subprocess.run")
    def test_unsigned_kernel_and_comparison_failure(self, run, _uname, _image):
        query = SimpleNamespace(stdout="installed linux-image-unsigned-6.8.0-100-generic\n")
        run.side_effect = [query, SimpleNamespace(returncode=0)]
        self.assertEqual(kernel_restart.newer_installed_kernel(), "6.8.0-100-generic")
        run.side_effect = [query, SimpleNamespace(returncode=2)]
        with self.assertRaisesRegex(RuntimeError, "compare installed kernel"):
            kernel_restart.newer_installed_kernel()

    @patch("security.security_steps.configure_maintenance_timer", return_value=True)
    @patch("common.common_steps.newer_installed_kernel", return_value="7.0.14-16-pve")
    @patch("security.security_steps.can_modify_kernel", return_value=True)
    @patch("security.security_steps.is_dry_run", return_value=False)
    def test_setup_installs_hook_and_reports_pending_kernel(self, _dry, _kernel, probe, timer):
        with tempfile.TemporaryDirectory() as directory:
            hook = Path(directory) / "basaltwater-reboot-required"
            with patch.object(kernel_restart, "KERNEL_HOOK", str(hook)), patch("builtins.print") as output:
                config = SetupConfig(username="root", host="ts1", system_type="server_proxmox")
                configure_auto_restart(config)
                with (
                    patch("common.common_steps.can_modify_kernel", return_value=True),
                    patch("common.common_steps.is_dry_run", return_value=False),
                    patch("common.common_steps.os.path.exists", return_value=False),
                ):
                    check_restart_required(config)
            self.assertTrue(hook.is_file())
            self.assertIn("7.0.14-16-pve", str(output.call_args_list))
            probe.assert_called_once()
            timer.assert_called_once()

    @patch("security.security_steps.install_kernel_restart_hook")
    @patch("common.common_steps.newer_installed_kernel")
    @patch("security.security_steps.configure_maintenance_timer", return_value=True)
    def test_dry_run_and_container_do_not_install_or_probe(self, _timer, probe, install):
        for dry, capable in ((True, True), (False, False)):
            with patch("security.security_steps.is_dry_run", return_value=dry), patch(
                "security.security_steps.can_modify_kernel", return_value=capable
            ):
                configure_auto_restart(SetupConfig(username="root", host="ts1", system_type="server_proxmox"))
        probe.assert_not_called()
        install.assert_not_called()
