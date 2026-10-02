"""Kernel selection management without touching host packages."""

from __future__ import annotations

import re
import io
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from lib import kernel_cleanup
from common.service_tools import cleanup_maintenance
from common.service_tools import kernel_cleanup_guard


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
        self.boot_kernels = {self.running, "6.17.13-21-pve", "7.0.14-16-pve"}
        self.boot_response = None
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
        elif command == ["proxmox-boot-tool", "kernel", "list"]:
            output = self.boot_response if self.boot_response is not None else (
                "Manually selected kernels:\nNone.\n\nAutomatically selected kernels:\n"
                + "\n".join(sorted(self.boot_kernels))
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

    def test_explicit_purge_includes_automatic_images_and_old_series(self):
        self.manual = set()
        self.assertEqual(kernel_cleanup.obsolete_kernel_packages(), [
            "proxmox-kernel-6.8", "proxmox-kernel-6.8.12-17-pve-signed",
            "proxmox-kernel-6.8.12-9-pve-signed",
        ])

    def test_explicit_purge_respects_holds_and_boot_protections(self):
        self.held = {"proxmox-kernel-6.8"}
        self.protected.update(package for package in self.packages if "6.8.12" in package)
        self.assertEqual(kernel_cleanup.obsolete_kernel_packages(), [])

    def test_retention_query_warning_fails_closed(self):
        self.run.return_value = subprocess.CompletedProcess([], 0, "", "database warning")
        self.run.side_effect = None
        with self.assertRaisesRegex(RuntimeError, "retention query"):
            kernel_cleanup.obsolete_kernel_packages()

    def test_native_boot_pin_is_preserved_even_before_apt_rules_refresh(self):
        self.boot_kernels.add("6.8.12-9-pve")
        self.assertEqual(kernel_cleanup.obsolete_kernel_packages(), ["proxmox-kernel-6.8.12-17-pve-signed"])

    def test_permanent_and_next_boot_pins_are_preserved(self):
        self.boot_response = (
            f"Manually selected kernels:\nNone.\n\nAutomatically selected kernels:\n{self.running}\n"
            "\nPinned kernel:\n6.8.12-9-pve\n\nKernel pinned on next-boot:\n6.8.12-17-pve\n"
        )
        self.assertEqual(kernel_cleanup.obsolete_kernel_packages(), [])

    def test_missing_or_invalid_native_boot_inventory_blocks_explicit_purge(self):
        for response in ("", "garbage", "Manually selected kernels:\nNone.\nAutomatically selected kernels:\n"):
            with self.subTest(response=response):
                self.boot_response = response
                with self.assertRaises((RuntimeError, ValueError)):
                    kernel_cleanup.obsolete_kernel_packages()

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


class TestProxmoxKernelCleanup(unittest.TestCase):
    def setUp(self):
        self.kernels = ["proxmox-kernel-6.8", "proxmox-kernel-6.8.12-9-pve-signed"]
        self.installation = self.enterContext(patch.object(cleanup_maintenance, "check_proxmox_installation"))
        self.health = self.enterContext(patch.object(cleanup_maintenance, "check_proxmox_update_safety"))
        self.plan = self.enterContext(patch.object(cleanup_maintenance, "obsolete_kernel_packages", return_value=self.kernels))
        self.enterContext(patch.object(cleanup_maintenance.shutil, "which", return_value="/usr/bin/apt-get"))
        self.enterContext(patch.object(cleanup_maintenance, "validate_filesystem_path"))
        self.simulation = self.enterContext(patch.object(cleanup_maintenance, "run_command", return_value=
            subprocess.CompletedProcess([], 0, self._simulation(self.kernels), "")))
        self.mutate = self.enterContext(patch.object(cleanup_maintenance, "run_cleanup_command", return_value=None))

    @staticmethod
    def _simulation(kernels):
        return f"0 upgraded, 0 newly installed, {len(kernels)} to remove and 0 not upgraded.\n" + "".join(
            f"Purg {package} [1.0]\n" for package in kernels
        )

    def test_purges_only_planned_kernels_with_guard_and_health_rechecks(self):
        self.assertEqual(cleanup_maintenance.cleanup_proxmox_kernels(), [])
        simulated = self.simulation.call_args.args[0]
        self.assertIn("--simulate", simulated)
        self.assertEqual(simulated[simulated.index("--") + 1:], self.kernels)
        command = self.mutate.call_args.args[0]
        self.assertEqual(command[:2], ["/usr/bin/apt-get", "purge"])
        self.assertNotIn("autoremove", command)
        self.assertNotIn("apt-mark", command)
        self.assertIn("APT::Get::AutomaticRemove=false", command)
        self.assertTrue(any(option.startswith("DPkg::Pre-Install-Pkgs::=") for option in command))
        self.assertTrue(any(option.endswith("kernel_cleanup_guard.py::Version=2") for option in command))
        self.assertEqual(command[command.index("--") + 1:], self.kernels)
        self.assertEqual(self.health.call_count, 3)
        self.assertEqual(self.simulation.call_args.kwargs["env"]["LC_ALL"], "C")

    def test_unsupported_or_unhealthy_node_never_runs_purge(self):
        for probe in (self.installation, self.health):
            with self.subTest(probe=probe):
                probe.side_effect = RuntimeError("maintenance blocked")
                self.assertIn("maintenance blocked", cleanup_maintenance.cleanup_proxmox_kernels()[0])
                probe.side_effect = None
        self.simulation.assert_not_called()
        self.mutate.assert_not_called()

    def test_backup_starting_after_simulation_prevents_purge(self):
        self.health.side_effect = [None, RuntimeError("active task")]
        self.assertIn("active task", cleanup_maintenance.cleanup_proxmox_kernels()[0])
        self.mutate.assert_not_called()

    def test_dependency_or_incomplete_simulation_prevents_purge(self):
        for stdout in (
            "", self._simulation(self.kernels + ["proxmox-ve"]),
            self._simulation(self.kernels) + "Inst pve-manager [9.2]\n",
            self._simulation(self.kernels) + "Conf pve-manager [9.2]\n",
            self._simulation(self.kernels[:1]),
        ):
            with self.subTest(stdout=stdout):
                self.simulation.return_value.stdout = stdout
                self.assertTrue(cleanup_maintenance.cleanup_proxmox_kernels())
                self.mutate.assert_not_called()

    def test_simulation_warning_or_failure_prevents_purge(self):
        for result in (
            subprocess.CompletedProcess([], 100, "", "resolver failed"),
            subprocess.CompletedProcess([], 0, self._simulation(self.kernels), "database warning"),
        ):
            with self.subTest(result=result):
                self.simulation.return_value = result
                self.assertTrue(cleanup_maintenance.cleanup_proxmox_kernels())
                self.mutate.assert_not_called()

    def test_no_eligible_kernels_skips_apt(self):
        self.plan.return_value = []
        self.assertEqual(cleanup_maintenance.cleanup_proxmox_kernels(), [])
        self.simulation.assert_not_called()
        self.mutate.assert_not_called()

    def test_purge_failure_is_reported(self):
        self.mutate.return_value = "purge failed"
        self.assertEqual(cleanup_maintenance.cleanup_proxmox_kernels(), ["purge failed"])


class TestKernelRemovalGuard(unittest.TestCase):
    def setUp(self):
        self.kernels = ["proxmox-kernel-6.8", "proxmox-kernel-6.8.12-9-pve-signed"]
        self.payload = "VERSION 2\nAPT::Get::Purge=true\n\n" + "".join(
            f"{package} 1.0 > - **REMOVE**\n" for package in self.kernels
        )

    def test_accepts_exact_kernel_only_removal_protocol(self):
        kernel_cleanup.validate_kernel_removal_actions(self.payload, self.kernels)

    def test_rejects_unapproved_actions_and_incomplete_protocol(self):
        for payload in (
            "", "VERSION 1\n", "VERSION 2\nAPT::Get::Purge=true\n",
            "VERSION 2\n\n", self.payload + "proxmox-ve 9.2 > - **REMOVE**\n",
            self.payload.replace("**REMOVE**", "**CONFIGURE**"),
            self.payload.replace("1.0 > - **REMOVE**", "- < 1.0 /tmp/kernel.deb"),
            self.payload.replace("VERSION 2", "VERSION 3"),
            self.payload.replace("APT::Get::Purge=true", "bad header"),
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                kernel_cleanup.validate_kernel_removal_actions(payload, self.kernels)

    def test_default_and_non_kernel_packages_cannot_be_authorized(self):
        for package in ("proxmox-ve", "proxmox-default-kernel", "proxmox-kernel-helper", "linux-image-amd64"):
            with self.subTest(package=package), self.assertRaises(ValueError):
                kernel_cleanup.validate_kernel_removal_actions(
                    f"VERSION 2\n\n{package} 1.0 > - **REMOVE**\n", [package],
                )

    def test_guard_rechecks_retention_inside_apt_transaction(self):
        with (
            patch.object(kernel_cleanup_guard.sys, "argv", ["guard"] + self.kernels),
            patch.object(kernel_cleanup_guard.sys, "stdin", io.StringIO(self.payload)),
            patch.object(kernel_cleanup_guard, "obsolete_kernel_packages", return_value=self.kernels),
        ):
            self.assertEqual(kernel_cleanup_guard.main(), 0)

    def test_new_boot_pin_prevents_actual_purge(self):
        with (
            patch.object(kernel_cleanup_guard.sys, "argv", ["guard"] + self.kernels),
            patch.object(kernel_cleanup_guard.sys, "stdin", io.StringIO(self.payload)),
            patch.object(kernel_cleanup_guard.sys, "stderr", io.StringIO()) as errors,
            patch.object(kernel_cleanup_guard, "obsolete_kernel_packages", return_value=self.kernels[:1]),
        ):
            self.assertEqual(kernel_cleanup_guard.main(), 1)
            self.assertIn("boot-retention policy changed", errors.getvalue())
