"""Supported-release and local maintenance safety regression tests."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from unittest.mock import call, patch

from common.proxmox_steps import preflight_proxmox, upgrade_proxmox_packages
from lib.apt_sources import inspect_apt_sources
from lib.config import SetupConfig
from lib.proxmox_maintenance import ProxmoxMaintenanceReport
from lib.proxmox_manage import ContainerInfo
from lib.proxmox_preflight import (
    _read_config,
    check_proxmox_installation,
    check_proxmox_update_safety,
    check_proxmox_upgrade_candidate,
)


class TestProxmoxRelease(unittest.TestCase):
    def setUp(self) -> None:
        self.root = self.enterContext(tempfile.TemporaryDirectory())
        os.mkdir(os.path.join(self.root, "sources.list.d"))
        self.sources = os.path.join(self.root, "sources.list")
        self._write_sources(
            "deb https://download.proxmox.com/debian/pve trixie pve-no-subscription\n"
        )
        self.enterContext(patch("lib.proxmox_preflight.read_os_release", return_value={
            "ID": "debian", "VERSION_CODENAME": "trixie",
        }))
        self.command = self.enterContext(patch("lib.proxmox_preflight.run", return_value=
            subprocess.CompletedProcess([], 0, "pve-manager/9.2.1/abcdef", "")))
        self.enterContext(patch("lib.proxmox_preflight.inspect_apt_sources",
            side_effect=lambda codename: inspect_apt_sources(codename, self.root)))

    def _write_sources(self, extra: str) -> None:
        with open(self.sources, "w", encoding="utf-8") as source:
            source.write(
                "deb https://deb.debian.org/debian trixie main\n"
                "deb https://security.debian.org/debian-security trixie-security main\n"
                + extra
            )

    def test_accepts_both_stable_channels(self) -> None:
        for uri, component in (
            ("download.proxmox.com", "pve-no-subscription"),
            ("enterprise.proxmox.com", "pve-enterprise"),
        ):
            self._write_sources(f"deb https://{uri}/debian/pve trixie {component}\n")
            check_proxmox_installation()

    def test_disabled_deb822_enterprise_does_not_block_no_subscription(self) -> None:
        path = os.path.join(self.root, "sources.list.d", "enterprise.sources")
        with open(path, "w", encoding="utf-8") as source:
            source.write("Types: deb\nURIs: https://enterprise.proxmox.com/debian/pve\n"
                         "Suites: bookworm\nComponents: pve-enterprise\nEnabled: no\n")
        check_proxmox_installation()

    def test_accepts_point_releases_within_supported_major(self) -> None:
        for version in ("9.0.11", "9.1.1", "9.2.1", "9.3.1", "9.20.1"):
            with self.subTest(version=version):
                self.command.return_value.stdout = f"pve-manager/{version}/abcdef"
                check_proxmox_installation()

    def test_rejects_other_majors_and_unknown_releases(self) -> None:
        for version in (
            "pve-manager/8.4.1/x", "pve-manager/10.0.1/x",
            "pve-manager/90.2.1/x", "pve-manager/9.x.1/x", "garbage",
        ):
            with self.subTest(version=version):
                self.command.return_value.stdout = version
                with self.assertRaisesRegex(RuntimeError, "Only stable Proxmox"):
                    check_proxmox_installation()

    def test_upgrade_candidate_must_remain_on_supported_major(self) -> None:
        for version, accepted in (
            ("9.0.11", True), ("9.1.1", True), ("9.2.2", True),
            ("1:9.2.2", True), ("9.3.1", True), ("9.20.1", True),
            ("8.4.1", False), ("10.0.1", False), ("1:10.0.1", False),
            ("90.2.1", False), ("9.x.1", False), ("(none)", False),
        ):
            with self.subTest(version=version):
                self.command.return_value.stdout = f"pve-manager:\n  Candidate: {version}\n"
                if accepted:
                    check_proxmox_upgrade_candidate()
                else:
                    with self.assertRaisesRegex(RuntimeError, "candidate is not supported"):
                        check_proxmox_upgrade_candidate()

    def test_rejects_missing_test_and_mixed_repositories(self) -> None:
        for source in (
            "",
            "deb https://download.proxmox.com/debian/pve trixie pvetest\n",
            "deb https://download.proxmox.com/debian/pve bookworm pve-no-subscription\n",
            "deb https://download.proxmox.com/debian/pve trixie pve-no-subscription\n"
            "deb https://deb.debian.org/debian sid main\n",
            "deb https://download.proxmox.com/debian/pve trixie pve-no-subscription\n"
            "deb https://download.proxmox.com/debian/ceph-squid trixie test\n",
        ):
            with self.subTest(source=source):
                self._write_sources(source)
                with self.assertRaises(RuntimeError):
                    check_proxmox_installation()

    def test_post_upgrade_manager_must_match_apt_candidate(self) -> None:
        for installed in ("9.1.1", "(none)", None):
            with self.subTest(installed=installed):
                self.command.return_value.stdout = (
                    (f"  Installed: {installed}\n" if installed is not None else "")
                    + "  Candidate: 9.3.1\n"
                )
                check_proxmox_upgrade_candidate()
                with self.assertRaisesRegex(RuntimeError, "did not reach its APT candidate"):
                    check_proxmox_upgrade_candidate(require_current=True)
        self.command.return_value.stdout = "  Installed: 1:9.3.1\n  Candidate: 1:9.3.1\n"
        check_proxmox_upgrade_candidate(require_current=True)


class TestLocalUpdateSafety(unittest.TestCase):
    def setUp(self) -> None:
        self.config = self.enterContext(patch("lib.proxmox_preflight._read_config", return_value=""))
        self.command = self.enterContext(patch("lib.proxmox_preflight.run", return_value=
            subprocess.CompletedProcess([], 0, "", "")))
        self.report = ProxmoxMaintenanceReport(host_name="local", address="127.0.0.1")
        self.audit = self.enterContext(patch("lib.proxmox_preflight.collect_local_maintenance_report",
                                             return_value=self.report))

    def test_healthy_node_allows_package_update_with_running_guest_but_blocks_reboot(self) -> None:
        self.report.running_guests = [ContainerInfo(vmid=100, status="running", name="web")]
        self.assertIs(check_proxmox_update_safety(), self.report)
        with self.assertRaisesRegex(RuntimeError, "running guests: 100"):
            check_proxmox_update_safety(require_evacuated=True)

    def test_failed_health_checks_block_update(self) -> None:
        self.report.errors = ["Cluster is not quorate", "Storage backup is inactive"]
        with self.assertRaisesRegex(RuntimeError, "not quorate"):
            check_proxmox_update_safety()

    def test_storage_exception_is_opt_in_and_retains_evacuated_guest_gate(self) -> None:
        check_proxmox_update_safety()
        self.audit.assert_called_once_with(allow_inactive_storage=False)
        self.report.warnings = ["Storage ts1sd32 is inactive"]
        self.report.running_guests = [ContainerInfo(vmid=107, status="running", name="samba")]
        with self.assertRaisesRegex(RuntimeError, "running guests: 107"):
            check_proxmox_update_safety(require_evacuated=True, allow_inactive_storage=True)
        self.audit.assert_called_with(allow_inactive_storage=True)

    def test_ha_and_ceph_defer_to_operator_before_package_commands(self) -> None:
        for configured_path, content in (
            ("/etc/pve/ha/resources.cfg", "vm: 100"),
            ("/etc/pve/storage.cfg", "rbd: shared"),
            ("/etc/pve/ceph.conf", "[global]"),
        ):
            with self.subTest(path=configured_path):
                self.config.side_effect = lambda path, **_: content if path == configured_path else ""
                with self.assertRaisesRegex(RuntimeError, "operator-managed"):
                    check_proxmox_update_safety()
                self.command.assert_not_called()

    def test_incomplete_transaction_and_core_holds_block_update(self) -> None:
        self.command.return_value.stdout = "half-installed package"
        with self.assertRaisesRegex(RuntimeError, "incomplete package transaction"):
            check_proxmox_update_safety()
        self.command.side_effect = [
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 0, "proxmox-ve\npve-manager\n", ""),
        ]
        with self.assertRaisesRegex(RuntimeError, "Held Proxmox packages"):
            check_proxmox_update_safety()
        self.audit.assert_not_called()

    def test_required_configuration_cannot_be_silently_missing(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(RuntimeError, "missing"):
                _read_config(os.path.join(root, "storage.cfg"), required=True)

    def test_package_probe_stderr_cannot_be_ignored(self) -> None:
        for failing_probe in (0, 1):
            with self.subTest(failing_probe=failing_probe):
                self.command.side_effect = [
                    subprocess.CompletedProcess([], 0, "", "package database warning" if index == failing_probe else "")
                    for index in range(2)
                ]
                with self.assertRaises(RuntimeError):
                    check_proxmox_update_safety()
        self.audit.assert_not_called()

    def test_held_cluster_storage_and_proxmox_libraries_block_updates(self) -> None:
        for package in (
            "libproxmox-rs-perl", "libcorosync-common4", "libknet1t64",
            "libqb100", "libzfs6linux", "libzpool6linux", "lxc-pve", "liblxc1",
        ):
            with self.subTest(package=package):
                self.command.side_effect = [
                    subprocess.CompletedProcess([], 0, "", ""),
                    subprocess.CompletedProcess([], 0, package + "\n", ""),
                ]
                with self.assertRaisesRegex(RuntimeError, "Held Proxmox packages"):
                    check_proxmox_update_safety()
        self.audit.assert_not_called()


class TestSetupPreflight(unittest.TestCase):
    def setUp(self) -> None:
        self.packages = self.enterContext(patch("common.proxmox_steps.check_proxmox_package_state"))
        self.candidate = self.enterContext(patch("common.proxmox_steps.check_proxmox_upgrade_candidate"))

    @patch("common.proxmox_steps.run")
    @patch("common.proxmox_steps.is_dry_run", return_value=False)
    @patch("common.proxmox_steps.check_proxmox_installation")
    def test_subscription_failure_stops_setup(self, installation, _dry_run, command) -> None:
        command.return_value = subprocess.CompletedProcess([], 100, "", "401 Unauthorized")
        with self.assertRaisesRegex(RuntimeError, "subscription validity"):
            preflight_proxmox(SetupConfig(host="pve", username="root", system_type="server_proxmox"))
        installation.assert_called_once()
        self.packages.assert_called_once()
        self.candidate.assert_not_called()
        self.assertIn("APT::Update::Error-Mode=any", command.call_args.args[0])

    @patch("common.proxmox_steps.run")
    @patch("common.proxmox_steps.is_dry_run", return_value=False)
    @patch("common.proxmox_steps.check_proxmox_installation")
    def test_package_state_failure_stops_before_repository_refresh(self, installation, _dry_run, command) -> None:
        self.packages.side_effect = RuntimeError("dpkg reports an incomplete package transaction")
        with self.assertRaisesRegex(RuntimeError, "incomplete package transaction"):
            preflight_proxmox(SetupConfig(host="pve", username="root", system_type="server_proxmox"))
        installation.assert_called_once()
        command.assert_not_called()
        self.candidate.assert_not_called()

    @patch("common.proxmox_steps.run")
    @patch("common.proxmox_steps.is_dry_run", return_value=False)
    @patch("common.proxmox_steps.check_proxmox_installation")
    def test_refreshed_candidate_is_checked_before_setup_proceeds(self, installation, _dry_run, command) -> None:
        command.return_value = subprocess.CompletedProcess([], 0, "", "")
        self.candidate.side_effect = RuntimeError("APT candidate is not supported")
        with self.assertRaisesRegex(RuntimeError, "candidate is not supported"):
            preflight_proxmox(SetupConfig(host="pve", username="root", system_type="server_proxmox"))
        installation.assert_called_once()
        self.packages.assert_called_once()
        command.assert_called_once()
        self.candidate.assert_called_once()

    @patch("common.proxmox_steps.run")
    @patch("common.proxmox_steps.is_dry_run", return_value=True)
    @patch("common.proxmox_steps.check_proxmox_installation")
    def test_dry_run_does_not_read_or_mutate_package_state(self, installation, _dry_run, command) -> None:
        preflight_proxmox(SetupConfig(host="pve", username="root", system_type="server_proxmox", dry_run=True))
        installation.assert_not_called()
        self.packages.assert_not_called()
        self.candidate.assert_not_called()
        command.assert_not_called()

    @patch("common.proxmox_steps.run")
    def test_root_forwarding_restrictions_rejected_before_commands(self, command) -> None:
        with self.assertRaisesRegex(ValueError, "--harden-user"):
            preflight_proxmox(SetupConfig(host="pve", username="root", system_type="server_proxmox", harden_user=True))
        command.assert_not_called()


class TestSetupUpgrade(unittest.TestCase):
    def setUp(self) -> None:
        self.config = SetupConfig(host="pve", username="root", system_type="server_proxmox")
        self.dry_run = self.enterContext(patch("common.proxmox_steps.is_dry_run", return_value=False))
        self.capability = self.enterContext(patch("common.proxmox_steps.can_modify_kernel", return_value=True))
        self.installation = self.enterContext(patch("common.proxmox_steps.check_proxmox_installation"))
        self.candidate = self.enterContext(patch("common.proxmox_steps.check_proxmox_upgrade_candidate"))
        self.health = self.enterContext(patch("common.proxmox_steps.check_proxmox_update_safety", return_value=
            ProxmoxMaintenanceReport("pve", "localhost", warnings=["Storage backup is inactive"])))
        self.hook = self.enterContext(patch("common.proxmox_steps.install_kernel_restart_hook"))
        self.command = self.enterContext(patch("common.proxmox_steps.run", return_value=
            subprocess.CompletedProcess([], 0, "packages upgraded", "")))

    def test_every_rerun_upgrades_without_refresh_packages_flag(self) -> None:
        self.assertFalse(self.config.refresh_packages)
        for _ in range(2):
            upgrade_proxmox_packages(self.config)
        self.assertEqual(self.command.call_count, 2)
        command = self.command.call_args.args[0]
        self.assertEqual(command[:5], ["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "dist-upgrade", "-y"])
        for option in ("--no-remove", "DPkg::Lock::Timeout=300",
                       "Dpkg::Options::=--force-confdef", "Dpkg::Options::=--force-confold"):
            self.assertIn(option, command)
        self.assertEqual(self.candidate.call_args_list, [call(), call(require_current=True)] * 2)

    def test_hook_precedes_package_installation_and_post_upgrade_checks(self) -> None:
        events = []
        self.health.side_effect = lambda **_: events.append("health") or ProxmoxMaintenanceReport("pve", "localhost")
        self.hook.side_effect = lambda: events.append("hook")
        self.command.side_effect = lambda *args, **kwargs: (
            events.append("upgrade") or subprocess.CompletedProcess([], 0, "", "")
        )
        upgrade_proxmox_packages(self.config)
        self.assertEqual(events, ["health", "hook", "upgrade", "health"])
        self.assertEqual(self.installation.call_count, 2)
        self.assertEqual(self.health.call_args_list, [call(allow_inactive_storage=True)] * 2)

    def test_upgrade_is_in_the_profile_before_version_dependent_configuration(self) -> None:
        from plugins.proxmox import build_server_proxmox_steps

        steps = build_server_proxmox_steps(self.config)
        self.assertEqual([function for _, function in steps[:2]], [preflight_proxmox, upgrade_proxmox_packages])
        self.assertEqual(steps[-1][0], "Checking if restart required")
        self.assertLess(
            [name for name, _ in steps].index("Upgrading Proxmox packages"),
            [name for name, _ in steps].index("Configuring Proxmox memory balloon target"),
        )

    def test_dry_run_does_not_install_hook_or_inspect_or_upgrade_target(self) -> None:
        self.dry_run.return_value = True
        upgrade_proxmox_packages(self.config)
        for mock in (self.installation, self.candidate, self.health, self.capability, self.hook, self.command):
            mock.assert_not_called()

    def test_unsafe_node_or_candidate_stops_before_hook_and_upgrade(self) -> None:
        for probe in (self.installation, self.candidate, self.health):
            with self.subTest(probe=probe):
                probe.side_effect = RuntimeError("unsafe node or repository")
                with self.assertRaisesRegex(RuntimeError, "unsafe node or repository"):
                    upgrade_proxmox_packages(self.config)
                probe.side_effect = None
        self.hook.assert_not_called()
        self.command.assert_not_called()

    def test_kernel_access_is_required_before_installing_hook(self) -> None:
        self.capability.return_value = False
        with self.assertRaisesRegex(RuntimeError, "requires kernel access"):
            upgrade_proxmox_packages(self.config)
        self.hook.assert_not_called()
        self.command.assert_not_called()

    def test_failed_apt_upgrade_stops_setup_before_post_upgrade_checks(self) -> None:
        self.command.return_value = subprocess.CompletedProcess([], 100, "", "Packages need to be removed")
        with self.assertRaisesRegex(RuntimeError, "Packages need to be removed"):
            upgrade_proxmox_packages(self.config)
        self.assertEqual(self.installation.call_count, 1)
        self.candidate.assert_called_once_with()
        self.health.assert_called_once_with(allow_inactive_storage=True)

    def test_stale_manager_and_failed_post_upgrade_health_cannot_report_success(self) -> None:
        self.candidate.side_effect = [None, RuntimeError("did not reach its APT candidate")]
        with self.assertRaisesRegex(RuntimeError, "did not reach its APT candidate"):
            upgrade_proxmox_packages(self.config)
        self.candidate.side_effect = None
        self.health.side_effect = [ProxmoxMaintenanceReport("pve", "localhost"), RuntimeError("lost quorum")]
        with self.assertRaisesRegex(RuntimeError, "lost quorum"):
            upgrade_proxmox_packages(self.config)

    def test_setup_does_not_reacquire_its_lock_via_scheduled_updater(self) -> None:
        with patch("lib.maintenance_lock.maintenance_lock", side_effect=AssertionError("setup owns lock")):
            upgrade_proxmox_packages(self.config)
        self.command.assert_called_once()
