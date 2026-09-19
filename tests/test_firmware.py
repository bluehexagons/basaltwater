"""Tests for local firmware auditing and guarded updates."""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import basaltwater
from lib.firmware import (
    FWUPD_DEPENDENCY,
    FirmwareAuditReport,
    FirmwareDevice,
    apply_firmware_updates,
    collect_firmware_audit,
    ensure_command_dependency,
    install_apt_dependency,
    validate_firmware_device_id,
)
from lib.firmware_cli import run_firmware_command
from lib.proxmox_manage import ContainerInfo


def _completed(
    command: list[str],
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


class TestFirmwareParser(unittest.TestCase):
    def setUp(self) -> None:
        self.parser, _setup, _patch = basaltwater.create_basaltwater_parser()

    def test_parses_audit_options(self) -> None:
        args = self.parser.parse_args(
            ["firmware", "audit", "--json", "--no-refresh"]
        )

        self.assertEqual(args.firmware_command, "audit")
        self.assertTrue(args.json)
        self.assertTrue(args.no_refresh)

    def test_parses_targeted_update_safety_options(self) -> None:
        args = self.parser.parse_args(
            [
                "firmware",
                "update",
                "device-123",
                "--allow-running-guests",
                "--install-dependencies",
                "--yes",
            ]
        )

        self.assertEqual(args.device_id, "device-123")
        self.assertTrue(args.allow_running_guests)
        self.assertTrue(args.install_dependencies)
        self.assertTrue(args.yes)


class TestFirmwareDependency(unittest.TestCase):
    def test_missing_dependency_prompts_and_honors_decline(self) -> None:
        output = io.StringIO()
        with (
            patch("lib.firmware.shutil.which", return_value=None),
            patch("lib.firmware.install_apt_dependency") as install,
            redirect_stdout(output),
        ):
            available = ensure_command_dependency(
                FWUPD_DEPENDENCY,
                prompt=lambda _message: "n",
            )

        self.assertFalse(available)
        install.assert_not_called()
        self.assertIn("declined", output.getvalue())

    def test_missing_dependency_can_be_installed_without_second_prompt(self) -> None:
        with (
            patch("lib.firmware.shutil.which", return_value=None),
            patch("lib.firmware.install_apt_dependency", return_value=True) as install,
        ):
            available = ensure_command_dependency(
                FWUPD_DEPENDENCY,
                install_without_prompt=True,
            )

        self.assertTrue(available)
        install.assert_called_once_with(FWUPD_DEPENDENCY)

    def test_apt_installer_refreshes_then_installs_validated_package(self) -> None:
        command_locations = {
            "apt-get": "/usr/bin/apt-get",
            "fwupdmgr": "/usr/bin/fwupdmgr",
        }
        with (
            patch(
                "lib.firmware.shutil.which",
                side_effect=lambda command: command_locations.get(command),
            ),
            patch("lib.firmware.os.geteuid", return_value=0),
            patch(
                "lib.firmware._run",
                return_value=_completed(["apt-get"]),
            ) as run,
        ):
            installed = install_apt_dependency(FWUPD_DEPENDENCY)

        self.assertTrue(installed)
        self.assertEqual(run.call_count, 2)
        update_command = run.call_args_list[0].args[0]
        install_command = run.call_args_list[1].args[0]
        self.assertEqual(update_command[-2:], ["update", "-q"])
        self.assertEqual(install_command[-4:], ["install", "-y", "-q", "fwupd"])
        self.assertFalse(run.call_args_list[0].kwargs["capture_output"])


class TestFirmwareAudit(unittest.TestCase):
    def test_normalizes_fwupd_devices_and_releases(self) -> None:
        devices = {
            "Devices": [
                {
                    "DeviceId": "device-123",
                    "Name": "System Firmware",
                    "Vendor": "Example Vendor",
                    "Version": "1.0",
                    "Guid": ["aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"],
                    "Flags": ["internal", "updatable"],
                }
            ]
        }
        updates = {
            "Devices": [
                {
                    "DeviceId": "device-123",
                    "Name": "System Firmware",
                    "Version": "1.0",
                    "Releases": [{"Version": "1.1"}],
                }
            ]
        }

        def run(command, **_kwargs):
            if command[-1] == "--version":
                return _completed(command, stdout="fwupd 2.0.0\n")
            if "refresh" in command:
                return _completed(command)
            if "get-devices" in command:
                return _completed(command, stdout=json.dumps(devices))
            return _completed(command, stdout=json.dumps(updates))

        with (
            patch("lib.firmware._read_dmi", return_value={"bios_version": "A1"}),
            patch("lib.firmware._package_versions", return_value={"fwupd": "2.0.0"}),
            patch("lib.firmware.platform.release", return_value="test-kernel"),
            patch("lib.firmware._run", side_effect=run),
        ):
            report = collect_firmware_audit()

        self.assertTrue(report.healthy)
        self.assertEqual(report.kernel, "test-kernel")
        self.assertEqual(report.devices[0].device_id, "device-123")
        self.assertEqual(report.updates[0].available_versions, ["1.1"])

    def test_no_updatable_devices_is_not_an_audit_error(self) -> None:
        def run(command, **_kwargs):
            if command[-1] == "--version":
                return _completed(command, stdout="fwupd 2.0.0\n")
            if "get-devices" in command:
                return _completed(command, stdout='{"Devices": []}')
            return _completed(command, returncode=2, stderr="No updatable devices")

        with (
            patch("lib.firmware._read_dmi", return_value={}),
            patch("lib.firmware._package_versions", return_value={}),
            patch("lib.firmware._run", side_effect=run),
        ):
            report = collect_firmware_audit(refresh=False)

        self.assertTrue(report.healthy)
        self.assertEqual(report.updates, [])

    def test_rejects_unsafe_device_ids(self) -> None:
        with self.assertRaisesRegex(ValueError, "device ID"):
            validate_firmware_device_id("device;shutdown")

    def test_update_adapter_always_suppresses_fwupd_reboot_prompt(self) -> None:
        with (
            patch("lib.firmware.os.geteuid", return_value=0),
            patch(
                "lib.firmware._run",
                return_value=_completed(["fwupdmgr", "update"]),
            ) as run,
        ):
            apply_firmware_updates(device_id="device-123", assume_yes=True)

        self.assertEqual(
            run.call_args.args[0],
            [
                "fwupdmgr",
                "update",
                "device-123",
                "--assume-yes",
                "--no-reboot-check",
            ],
        )


class TestFirmwareUpdateCli(unittest.TestCase):
    def _args(self, **overrides: object) -> argparse.Namespace:
        values: dict[str, object] = {
            "firmware_command": "update",
            "device_id": None,
            "no_refresh": False,
            "allow_running_guests": False,
            "install_dependencies": False,
            "yes": True,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def _report(self) -> FirmwareAuditReport:
        return FirmwareAuditReport(
            kernel="test-kernel",
            updates=[
                FirmwareDevice(
                    device_id="device-123",
                    name="System Firmware",
                    version="1.0",
                    available_versions=["1.1"],
                )
            ],
        )

    def test_running_proxmox_guests_block_update(self) -> None:
        guest = ContainerInfo(
            vmid=114,
            status="running",
            name="dev",
            guest_type="vm",
        )
        output = io.StringIO()
        with (
            patch("lib.firmware_cli._ensure_fwupd", return_value=True),
            patch("lib.firmware_cli.collect_firmware_audit", return_value=self._report()),
            patch(
                "lib.firmware_cli.inspect_running_proxmox_guests",
                return_value=([guest], []),
            ),
            patch("lib.firmware_cli.apply_firmware_updates") as apply_update,
            redirect_stdout(output),
        ):
            result = run_firmware_command(self._args())

        self.assertEqual(result, 1)
        apply_update.assert_not_called()
        self.assertIn("114 (vm)", output.getvalue())

    def test_confirmed_targeted_update_runs_fwupd_adapter(self) -> None:
        update_result = _completed(["fwupdmgr", "update"])
        with (
            patch("lib.firmware_cli._ensure_fwupd", return_value=True),
            patch("lib.firmware_cli.collect_firmware_audit", return_value=self._report()),
            patch(
                "lib.firmware_cli.inspect_running_proxmox_guests",
                return_value=([], []),
            ),
            patch(
                "lib.firmware_cli.apply_firmware_updates",
                return_value=update_result,
            ) as apply_update,
            redirect_stdout(io.StringIO()),
        ):
            result = run_firmware_command(
                self._args(device_id="device-123", yes=True)
            )

        self.assertEqual(result, 0)
        apply_update.assert_called_once_with(
            device_id="device-123", assume_yes=True
        )


if __name__ == "__main__":
    unittest.main()
