"""Tests for declarative swap parsing, validation, and persistence helpers."""

from __future__ import annotations

import os
import tempfile
import unittest
from argparse import Namespace
from unittest.mock import MagicMock, patch

from common.swap_steps import (
    FSTAB_BEGIN,
    FSTAB_END,
    _configure_resume,
    _fstab_entry,
    _replace_fstab,
    _zram_configuration,
)
import basaltwater
from lib.config import SetupConfig
from lib.proxmox_vm import _disk_hardware_value, _warn_zfs_swap_storage
from lib.swap_config import swap_devices, swap_files, swap_zram
from lib.validation import validate_swap_settings
from lib.vm_storage import VMDataDisk, VMDiskHardware, disk_hardware


class TestSwapConfiguration(unittest.TestCase):
    def _config(self, **overrides) -> SetupConfig:
        values = {
            "host": "10.0.0.50",
            "username": "agent",
            "system_type": "agent_code_vm",
            "machine_type": "vm",
        }
        values.update(overrides)
        return SetupConfig(**values)

    def test_normalizes_multi_tier_swap(self):
        config = self._config(
            swap_files=[["root", "/swap/slow", "2G", "priority=20"]],
            swap_devices=[["ssd", "UUID=abc-def-123", "priority=200", "discard=once"]],
            swap_zram=[["memory", "512M", "priority=300", "algorithm=zstd"]],
        )
        validate_swap_settings(config)

        self.assertEqual(swap_files(config)[0].priority, 20)
        self.assertEqual(swap_devices(config)[0].discard, "once")
        self.assertEqual(swap_zram(config)[0].algorithm, "zstd")

    def test_direct_blank_device_initialization_is_named(self):
        config = self._config(
            swap_devices=[["ssd", "/dev/disk/by-id/scsi-stable"]],
            swap_initialize=["missing"],
        )
        with self.assertRaisesRegex(ValueError, "unknown swap area"):
            validate_swap_settings(config)

    def test_zram_and_zswap_are_mutually_exclusive(self):
        with self.assertRaisesRegex(ValueError, "cannot both"):
            validate_swap_settings(
                self._config(swap_zram=[["fast", "1G"]], zswap=True)
            )

    def test_zram_size_is_rendered_in_generator_mebibytes(self):
        config = self._config(swap_zram=[["fast", "1G"]])

        self.assertIn("zram-size = 1024", _zram_configuration(swap_zram(config)))

    def test_swap_provider_disk_forces_backup_off(self):
        config = self._config(
            hosted_node="pve1",
            container_storage=[
                ["root", "local-lvm", "32G"],
                ["ssd-swap", "fast-lvm", "8G"],
                ["work", "bulk-lvm", "100G"],
            ],
            storage_mounts=[["work", "/srv/work", "ext4"]],
            swap_devices=[["fast", "ssd-swap", "priority=200"]],
        )

        settings = disk_hardware(config)
        self.assertEqual(
            settings["ssd-swap"], VMDiskHardware("ssd-swap", True, False, False)
        )
        self.assertTrue(settings["root"].backup)
        self.assertTrue(settings["work"].backup)

    def test_setup_command_round_trips_swap_and_backup_policy(self):
        config = self._config(
            hosted_node="pve1",
            container_memory="4G",
            container_storage=[["root", "local-lvm", "32G"]],
            swap_files=[["root", "/swapfile", "2G", "priority=50"]],
            swappiness=80,
            zswap=False,
            vm_disk_backup=False,
        )
        command = " ".join(config.to_setup_command())

        self.assertIn("--swap-file root /swapfile 2G priority=50", command)
        self.assertIn("--swappiness 80", command)
        self.assertIn("--no-zswap", command)
        self.assertIn("--no-disk-backup", command)

    def test_initialize_authorization_is_forwarded_but_not_persisted(self):
        config = self._config(
            swap_devices=[["bulk", "/dev/disk/by-id/scsi-stable"]],
            swap_initialize=["bulk"],
        )

        self.assertIn("--swap-initialize bulk", config.to_remote_args())
        self.assertNotIn("swap_initialize", config.to_dict())

    def test_swappiness_only_patch_preserves_saved_areas(self):
        preserved = basaltwater._patch_preserve_keys(Namespace(swappiness=100))

        self.assertIn("swap_devices", preserved)
        self.assertNotIn("swappiness", preserved)

    def test_disabling_zswap_does_not_preserve_pool_limit(self):
        preserved = basaltwater._patch_preserve_keys(Namespace(zswap=False))

        self.assertNotIn("zswap", preserved)
        self.assertNotIn("zswap_max_pool_percent", preserved)

    def test_none_mode_clears_all_managed_swap_policy(self):
        preserved = basaltwater._patch_preserve_keys(Namespace(swap_mode="none"))

        for field in (
            "swap_files",
            "swap_devices",
            "swap_zram",
            "swappiness",
            "zswap",
            "zswap_max_pool_percent",
            "swap_resume",
        ):
            self.assertNotIn(field, preserved)

    def test_explicit_auto_mode_does_not_preserve_stale_resume_device(self):
        preserved = basaltwater._patch_preserve_keys(Namespace(swap_mode="auto"))

        self.assertNotIn("swap_resume", preserved)

    def test_resume_removal_round_trips_and_removes_managed_file(self):
        config = self._config(swap_resume="")
        self.assertIn("--no-swap-resume", config.to_remote_args())
        self.assertIn("--no-swap-resume", config.to_setup_command())

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "resume")
            with open(path, "w", encoding="utf-8") as resume_file:
                resume_file.write("RESUME=UUID=old\n")
            with (
                patch("common.swap_steps.RESUME_PATH", path),
                patch("common.swap_steps.run") as mock_run,
            ):
                _configure_resume("", [])

            self.assertFalse(os.path.exists(path))
            mock_run.assert_called_once_with("update-initramfs -u")

    def test_duplicate_resources_and_fractional_mebibytes_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate --swap-file PATH"):
            validate_swap_settings(
                self._config(
                    swap_files=[
                        ["one", "/swapfile", "1G"],
                        ["two", "/swapfile", "2G"],
                    ]
                )
            )
        with self.assertRaisesRegex(ValueError, "whole MiB"):
            validate_swap_settings(
                self._config(swap_zram=[["fast", "65537K"]])
            )

    def test_proxmox_disk_backup_option_is_explicit_only_when_excluded(self):
        included = _disk_hardware_value(
            "pool:volume", discard=True, ssd=False, backup=True
        )
        excluded = _disk_hardware_value(
            "pool:volume", discard=True, ssd=False, backup=False
        )

        self.assertNotIn("backup=", included)
        self.assertIn("backup=0", excluded)

    def test_fstab_replacement_preserves_unmanaged_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "fstab")
            with open(path, "w", encoding="utf-8") as fstab:
                fstab.write("UUID=root / ext4 defaults 0 1\n")
            with patch("common.swap_steps.FSTAB_PATH", path):
                _replace_fstab(["/swapfile none swap sw,pri=100 0 0"])
                _replace_fstab(["UUID=swap none swap sw,pri=10 0 0"])
            with open(path, encoding="utf-8") as fstab:
                content = fstab.read()

        self.assertIn("UUID=root / ext4 defaults 0 1", content)
        self.assertNotIn("/swapfile", content)
        self.assertEqual(content.count(FSTAB_BEGIN), 1)
        self.assertEqual(content.count(FSTAB_END), 1)

    def test_managed_swap_fstab_entries_do_not_block_boot_when_missing(self):
        file_entry = _fstab_entry(
            {
                "name": "root",
                "type": "file",
                "source": "/swapfile",
                "priority": 100,
                "size": "1G",
            }
        )
        device_entry = _fstab_entry(
            {
                "name": "bulk",
                "type": "device",
                "source": "/dev/vdb",
                "path": "/dev/vdb",
                "uuid": "11111111-1111",
                "priority": 10,
                "discard": "off",
                "provider_owned": False,
            }
        )

        self.assertIn("sw,nofail,pri=100", file_entry or "")
        self.assertIn("sw,nofail,pri=10", device_entry or "")

    @patch("lib.proxmox_vm._ssh_run")
    def test_zfs_provider_swap_disk_warns(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"type":"zfspool"}\n'
        )
        with patch("builtins.print") as mock_print:
            _warn_zfs_swap_storage(
                [VMDataDisk("swap-fast", "tank", "16G")],
                {"swap-fast"},
                "pve1",
                "root",
                [],
            )

        self.assertIn("not yet qualified", mock_print.call_args.args[0])
