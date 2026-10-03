"""Supported-release and local maintenance safety regression tests."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from common.proxmox_steps import preflight_proxmox
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

    def test_rejects_old_unknown_and_newer_unvalidated_releases(self) -> None:
        for version in ("pve-manager/8.4.1/x", "pve-manager/9.1.1/x", "garbage", "pve-manager/9.20.1/x"):
            with self.subTest(version=version):
                self.command.return_value.stdout = version
                with self.assertRaisesRegex(RuntimeError, "Only stable Proxmox"):
                    check_proxmox_installation()

    def test_upgrade_candidate_must_remain_on_supported_release(self) -> None:
        for version, accepted in (("9.2.2", True), ("1:9.2.2", True), ("9.3.1", False), ("(none)", False)):
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

    def test_backup_exception_is_opt_in_and_retains_evacuated_guest_gate(self) -> None:
        check_proxmox_update_safety()
        self.audit.assert_called_once_with(allow_inactive_backup_storage=False)
        self.report.warnings = ["Storage backup is inactive (backup-only network storage)"]
        self.report.running_guests = [ContainerInfo(vmid=107, status="running", name="samba")]
        with self.assertRaisesRegex(RuntimeError, "running guests: 107"):
            check_proxmox_update_safety(require_evacuated=True, allow_inactive_backup_storage=True)
        self.audit.assert_called_with(allow_inactive_backup_storage=True)

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
