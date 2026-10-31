"""Tests for declarative swap parsing, validation, and persistence helpers."""

from __future__ import annotations

import os
import tempfile
import unittest
from argparse import Namespace
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

from common import swap_steps
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
from lib.validation import validate_swap_settings, validate_vm_storage_settings
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
                patch("common.swap_steps.SWAP_STATE_FILE", os.path.join(directory, "swap.json")),
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

    def test_fstab_block_changes_preserve_surrounding_content_exactly(self):
        old_block = f"{FSTAB_BEGIN}\n/old-swap none swap sw 0 0\n{FSTAB_END}\n"
        entry = "UUID=swap none swap sw,pri=10 0 0"
        new_block = f"{FSTAB_BEGIN}\n{entry}\n{FSTAB_END}\n"
        root = "UUID=root / ext4 defaults 0 1\n"
        data = "UUID=data /data ext4 defaults 0 2"
        for prefix, suffix in (
            (root, data + "\n"),
            ("# root mount\n" + root + "\n\n", "\n# data mount\n" + data + "\n\n"),
            (root, data),
            ("", data + "\n"),
            (root, ""),
            ("", ""),
        ):
            for entries, block in (([], ""), ([entry], new_block)):
                with (
                    self.subTest(prefix=prefix, suffix=suffix, entries=entries),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    path = os.path.join(directory, "fstab")
                    with open(path, "w", encoding="utf-8") as fstab:
                        fstab.write(prefix + old_block + suffix)
                    with (
                        patch("common.swap_steps.FSTAB_PATH", path),
                        patch("common.swap_steps.run") as mock_run,
                    ):
                        for _ in range(2):
                            _replace_fstab(entries)
                            with open(path, encoding="utf-8") as fstab:
                                self.assertEqual(fstab.read(), prefix + block + suffix)
                        mock_run.assert_not_called()

    def test_fstab_removal_preserves_content_without_a_managed_block(self):
        for content in ("", "# mounts\n\n", "UUID=root / ext4 defaults 0 1"):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                path = os.path.join(directory, "fstab")
                with open(path, "w", encoding="utf-8") as fstab:
                    fstab.write(content)
                with patch("common.swap_steps.FSTAB_PATH", path):
                    _replace_fstab([])
                with open(path, encoding="utf-8") as fstab:
                    self.assertEqual(fstab.read(), content)

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


class TestResumeRecovery(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.resume = Path(directory) / "resume"
        self.state = Path(directory) / "state" / "swap.json"
        self.pending = Path(str(self.state) + ".resume-pending")
        self.enterContext(patch.object(swap_steps, "RESUME_PATH", str(self.resume)))
        self.enterContext(patch.object(swap_steps, "SWAP_STATE_FILE", str(self.state)))
        self.run = self.enterContext(patch.object(swap_steps, "run"))
        self.areas = [{"name": "disk", "type": "device", "uuid": "new-uuid"}]

    def test_failed_rebuild_retries_add_replace_and_remove_then_stops(self):
        for operation in ("add", "replace", "remove"):
            with self.subTest(operation=operation):
                self.resume.unlink(missing_ok=True)
                if operation != "add":
                    self.resume.write_text("RESUME=UUID=old-uuid\n")
                name = None if operation == "remove" else "disk"
                self.run.reset_mock()
                self.run.side_effect = [RuntimeError("rebuild failed"), None]
                with self.assertRaisesRegex(RuntimeError, "rebuild failed"):
                    _configure_resume(name, self.areas)
                self.assertTrue(self.pending.exists())
                self.assertEqual(self.pending.stat().st_mode & 0o777, 0o600)
                _configure_resume(name, self.areas)
                self.assertFalse(self.pending.exists())
                _configure_resume(name, self.areas)
                self.assertEqual(self.run.call_count, 2)
                self.run.assert_called_with("update-initramfs -u")
                if name:
                    self.assertEqual(self.resume.read_text(), "RESUME=UUID=new-uuid\n")
                else:
                    self.assertFalse(self.resume.exists())

    def test_interruption_before_or_after_resume_change_retains_rebuild_intent(self):
        real_write = swap_steps.write_text_atomic
        real_remove = swap_steps.remove_file_durable
        for operation in ("add", "replace", "remove"):
            for after in (False, True):
                with self.subTest(operation=operation, after=after):
                    self.resume.unlink(missing_ok=True)
                    if operation != "add":
                        self.resume.write_text("RESUME=UUID=old-uuid\n")
                    name = None if operation == "remove" else "disk"
                    self.run.reset_mock()

                    def interrupt(real, path, *args, **kwargs):
                        if path != str(self.resume):
                            return real(path, *args, **kwargs)
                        if after:
                            real(path, *args, **kwargs)
                        raise SystemExit("interrupted")

                    with (
                        patch.object(swap_steps, "write_text_atomic",
                                     side_effect=lambda *a, **kw: interrupt(real_write, *a, **kw)),
                        patch.object(swap_steps, "remove_file_durable",
                                     side_effect=lambda *a, **kw: interrupt(real_remove, *a, **kw)),
                    ):
                        with self.assertRaises(SystemExit):
                            _configure_resume(name, self.areas)
                    self.run.assert_not_called()
                    self.assertTrue(self.pending.exists())
                    _configure_resume(name, self.areas)
                    self.run.assert_called_once_with("update-initramfs -u")
                    self.assertFalse(self.pending.exists())

    def test_failure_to_save_intent_preserves_resume_without_rebuilding(self):
        self.resume.write_text("RESUME=UUID=old-uuid\n")
        with patch.object(swap_steps, "write_text_atomic", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                _configure_resume("disk", self.areas)
        self.assertEqual(self.resume.read_text(), "RESUME=UUID=old-uuid\n")
        self.run.assert_not_called()

    def test_default_without_resume_or_pending_intent_does_not_rebuild(self):
        _configure_resume(None, [])
        self.run.assert_not_called()
        self.assertFalse(self.pending.exists())

    def test_missing_resume_device_preserves_pending_intent(self):
        self.pending.parent.mkdir()
        self.pending.write_text("pending\n")
        with self.assertRaisesRegex(RuntimeError, "not reconciled"):
            _configure_resume("missing", self.areas)
        self.assertEqual(self.pending.read_text(), "pending\n")
        self.run.assert_not_called()

    def test_pending_lookup_failure_cannot_be_treated_as_absence(self):
        with patch.object(swap_steps.os, "lstat", side_effect=PermissionError("unreadable intent")):
            with self.assertRaisesRegex(PermissionError, "unreadable intent"):
                _configure_resume(None, [])
        self.run.assert_not_called()

    def test_symlink_pending_intent_is_preserved_and_rejected(self):
        self.pending.parent.mkdir()
        target = self.pending.parent / "other"
        target.write_text("unrelated\n")
        self.pending.symlink_to(target)
        with self.assertRaisesRegex(RuntimeError, "regular file"):
            _configure_resume(None, [])
        self.assertTrue(self.pending.is_symlink())
        self.assertEqual(target.read_text(), "unrelated\n")
        self.run.assert_not_called()


class TestCachedSwapConfiguration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parser, _, _ = basaltwater.create_basaltwater_parser()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.key = os.path.join(self.directory.name, "id_test")
        for path in (self.key, self.key + ".pub"):
            with open(path, "w", encoding="utf-8") as key_file:
                key_file.write("test key\n")
        self.cached = SetupConfig(
            host="10.100.0.100",
            username="agent",
            system_type="server_lite",
            machine_type="vm",
            hosted_node="pve1",
            ssh_key=self.key,
            container_memory="1.5G",
            vm_balloon_min="1G",
            vm_cpu_type="x86-64-v2",
            container_storage=[
                ["root", "lab-ssd", "10G"],
                ["home-data", "lab-ssd", "1G"],
                ["xfs-data", "local-lvm", "1G"],
                ["swap-fast", "lab-ssd", "1G"],
            ],
            storage_mounts=[
                ["home-data", "/home", "ext4"],
                ["xfs-data", "/srv/test-xfs", "xfs"],
            ],
            swap_devices=[["fast", "swap-fast", "priority=200", "discard=once"]],
            swappiness=20,
            zswap=False,
            static_ipv4="10.100.0.100/24",
            network_gateway4="10.100.0.1",
            network_dns=["10.100.0.1"],
        )

    def _args(self, *options):
        return self.parser.parse_args([
            "setup", "server_lite", "10.100.0.100", "agent",
            "--provision-on", "pve1", "--timezone", "UTC", "--dry-run",
            *options,
        ])

    def test_additive_setup_replay_and_verification_preserve_saved_swap(self):
        from lib.proxmox_vm import VMAlreadyExists

        additive_options = (
            "--storage", "extra-data", "lab-ssd", "1G",
            "--storage-mount", "extra-data", "/srv/test-add", "ext4", "empty",
        )
        for options, attached in (
            (additive_options, {"extra-data"}),
            (additive_options, set()),
            (("--verify-provider",), set()),
        ):
            with self.subTest(options=options, attached=attached), ExitStack() as stack:
                stack.enter_context(patch("basaltwater.load_setup_command", return_value=self.cached))
                stack.enter_context(patch("basaltwater.prompt_for_missing_passwords"))
                stack.enter_context(patch("basaltwater._apply_hosted_proxmox_defaults"))
                stack.enter_context(patch("basaltwater.prepare_runtime_config", side_effect=lambda config: config))
                for name in (
                    "print_setup_summary", "print_service_access_summary",
                    "_refresh_existing_managed_guest_host_keys", "ensure_guest_ipv4_route",
                    "register_proxmox_setup_host", "save_setup_command",
                ):
                    stack.enter_context(patch(f"basaltwater.{name}"))
                stack.enter_context(patch("basaltwater.adopt_verified_network_host", return_value=None))
                stack.enter_context(patch("lib.proxmox_vm.ensure_vm_running"))
                provision = stack.enter_context(patch("lib.proxmox_vm.provision_vm", side_effect=VMAlreadyExists))
                remote = stack.enter_context(patch("basaltwater.run_remote_setup", return_value=0))

                self.assertEqual(basaltwater.run_setup_command(self._args(*options)), 0)

                config = remote.call_args.args[0]
                self.assertEqual(config.swap_devices, self.cached.swap_devices)
                self.assertEqual(config.swappiness, 20)
                self.assertIs(config.zswap, False)
                self.assertIsNone(config.swap_initialize)
                validate_vm_storage_settings(config, require_provisioning=True)
                if attached or "--verify-provider" in options:
                    self.assertTrue(provision.call_args.kwargs["verify_existing_storage"])
                    self.assertTrue(provision.call_args.kwargs["require_existing_vm"])
                    self.assertEqual(provision.call_args.kwargs.get("attach_missing_data_disks", set()), attached)
                else:
                    provision.assert_not_called()
                self.cached = SetupConfig.from_dict(config.host, config.system_type, config.to_dict())

    def test_saved_swap_merge_respects_explicit_replacement_and_clear(self):
        self.cached.swap_files = [["slow", "/swapfile", "1G"]]
        self.cached.swap_zram = [["memory", "512M"]]
        self.cached.swap_resume = "fast"
        self.cached.swap_initialize = ["fast"]
        fields = (
            "swap_mode", "swap_files", "swap_devices", "swap_zram",
            "swappiness", "zswap", "zswap_max_pool_percent", "swap_resume",
        )
        inherited = {field: getattr(self.cached, field) for field in fields}
        for options, overrides in (
            ((), {}),
            (("--swappiness", "0"), {"swappiness": 0}),
            (("--swap-device", "fast", "swap-fast", "priority=100"),
             {"swap_devices": [["fast", "swap-fast", "priority=100"]]}),
            (("--swap-file", "other", "/other-swap", "2G"),
             {"swap_files": [["other", "/other-swap", "2G"]]}),
            (("--swap-zram", "other", "1G"), {"swap_zram": [["other", "1G"]]}),
            (("--no-swap-resume",), {"swap_resume": ""}),
            (("--swap-mode", "auto"),
             {"swap_mode": "auto", "swap_files": None, "swap_devices": None,
              "swap_zram": None, "swap_resume": None}),
            (("--swap-mode", "preserve"),
             {"swap_mode": "preserve", "swap_files": None, "swap_devices": None,
              "swap_zram": None, "swap_resume": None}),
            (("--swap-mode", "none"), {field: None for field in fields[1:]} | {"swap_mode": "none"}),
        ):
            with self.subTest(options=options):
                args = self._args(*options)
                config = SetupConfig.from_args(args, args.system_type)
                basaltwater._reuse_cached_provisioning_metadata(config, args, self.cached)
                self.assertEqual({field: getattr(config, field) for field in fields}, inherited | overrides)
                self.assertIsNone(config.swap_initialize)

        self.cached.swap_zram = None
        self.cached.zswap = True
        self.cached.zswap_max_pool_percent = 20
        args = self._args("--no-zswap")
        config = SetupConfig.from_args(args, args.system_type)
        basaltwater._reuse_cached_provisioning_metadata(config, args, self.cached)
        self.assertIs(config.zswap, False)
        self.assertIsNone(config.zswap_max_pool_percent)
        validate_swap_settings(config)

        args = self._args("--swap-initialize", "fast")
        config = SetupConfig.from_args(args, args.system_type)
        basaltwater._reuse_cached_provisioning_metadata(config, args, self.cached)
        self.assertEqual(config.swap_initialize, ["fast"])

    def test_omitted_mode_preserves_saved_preserve_policy(self):
        self.cached.swap_mode = "preserve"
        self.cached.swap_devices = None
        args = self._args()
        config = SetupConfig.from_args(args, args.system_type)
        basaltwater._reuse_cached_provisioning_metadata(config, args, self.cached)
        self.assertEqual(config.swap_mode, "preserve")
        self.assertEqual(config.swappiness, 20)
        validate_swap_settings(config)

    def test_additive_merge_cannot_introduce_new_swap_media(self):
        for mount in ((), ("--storage-mount", "new-swap", "/srv/new", "ext4", "empty")):
            with self.subTest(mount=mount):
                args = self._args(
                    "--storage", "new-swap", "lab-ssd", "1G", *mount,
                    "--swap-device", "new", "new-swap",
                )
                config = SetupConfig.from_args(args, args.system_type)
                added = basaltwater._merge_additive_vm_storage(config, self.cached, args)
                if added is None:
                    self.assertIn("--storage", basaltwater._unsupported_cached_provisioning_changes(config, self.cached, args))
                else:
                    basaltwater._reuse_cached_provisioning_metadata(config, args, self.cached)
                    with self.assertRaisesRegex(ValueError, "swap.*mount|mount.*swap"):
                        validate_vm_storage_settings(config, require_provisioning=True)

    def test_inherited_swap_still_requires_machine_capability(self):
        self.cached.machine_type = "unprivileged"
        args = self._args("--machine", "unprivileged")
        config = SetupConfig.from_args(args, args.system_type)
        basaltwater._reuse_cached_provisioning_metadata(config, args, self.cached)
        with self.assertRaisesRegex(ValueError, "not supported inside containers"):
            validate_swap_settings(config)
