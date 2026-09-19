"""Tests for fail-closed VM data-disk setup."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import call, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from common import storage_steps
from lib.config import SetupConfig
from lib.vm_storage import VMStorageCache, VMStorageMount


def _result(*, returncode: int = 0, stdout: str = "", stderr: str = ""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class TestDiskIdentity(unittest.TestCase):
    @patch("common.storage_steps._lsblk")
    def test_shared_mapper_can_appear_under_multiple_backing_disks(self, mock_lsblk):
        mapper = {"path": "/dev/mapper/it_data-data", "type": "lvm", "fstype": "ext4"}
        mock_lsblk.return_value = [
            {"path": "/dev/sdb", "children": [mapper]},
            {"path": "/dev/sdc", "children": [mapper.copy()]},
        ]
        self.assertEqual(storage_steps._find_device_by_path(mapper["path"]), mapper)
        mock_lsblk.return_value[1]["children"][0]["fstype"] = "xfs"
        with self.assertRaisesRegex(RuntimeError, "conflicting device"):
            storage_steps._find_device_by_path(mapper["path"])

    @patch("common.storage_steps._lsblk")
    def test_requires_exactly_one_matching_serial(self, mock_lsblk):
        mock_lsblk.return_value = [
            {"type": "disk", "serial": "it-data", "size": 2 * 1024**3},
            {"type": "disk", "serial": "it-data", "size": 2 * 1024**3},
        ]

        with self.assertRaisesRegex(RuntimeError, "exactly one.*found 2"):
            storage_steps._find_declared_disk("it-data", "1G")

    @patch("common.storage_steps._lsblk")
    def test_rejects_disk_smaller_than_declaration(self, mock_lsblk):
        mock_lsblk.return_value = [
            {"type": "disk", "serial": "it-data", "size": 1024**3},
        ]

        with self.assertRaisesRegex(RuntimeError, "smaller than declared"):
            storage_steps._find_declared_disk("it-data", "2G")

    @patch("common.storage_steps.time.sleep")
    @patch("common.storage_steps._run_capture")
    @patch("common.storage_steps._find_declared_disk")
    def test_waits_for_hot_added_disk_to_reach_guest(
        self,
        mock_find,
        mock_run,
        mock_sleep,
    ):
        disk = {"type": "disk", "serial": "it-data", "size": 2 * 1024**3}
        mock_find.side_effect = [
            storage_steps._MissingDeclaredDisk(
                "Expected exactly one VM data disk with serial 'it-data'; found 0"
            ),
            disk,
        ]

        self.assertIs(
            storage_steps._wait_for_declared_disk("it-data", "2G"),
            disk,
        )
        mock_run.assert_called_once_with("udevadm settle", check=False)
        mock_sleep.assert_called_once_with(1)


class TestDiskPreparationSafety(unittest.TestCase):
    @patch("common.storage_steps._run_capture")
    def test_failed_signature_scan_never_partitions_disk(self, mock_run):
        mock_run.return_value = _result(returncode=1, stderr="permission denied")
        with self.assertRaisesRegex(RuntimeError, "Could not inspect"):
            storage_steps._partition_for_mount(
                {"path": "/dev/sdb", "children": []}, "it-data", "2G"
            )
        self.assertEqual(mock_run.call_count, 1)
        self.assertTrue(mock_run.call_args.args[0].startswith("wipefs "))

    @patch("common.storage_steps._wipefs_signatures", return_value=["ext4"])
    def test_refuses_signatures_on_unpartitioned_disk(self, _signatures):
        disk = {
            "path": "/dev/sdb",
            "type": "disk",
            "size": 2 * 1024**3,
            "serial": "it-data",
            "children": [],
        }

        with self.assertRaisesRegex(RuntimeError, "signatures found: ext4"):
            storage_steps._partition_for_mount(disk, "it-data", "2G")

    @patch("common.storage_steps._lsblk")
    def test_refuses_existing_wrong_filesystem(self, mock_lsblk):
        mock_lsblk.return_value = [
            {
                "type": "disk",
                "children": [
                    {
                        "type": "part",
                        "path": "/dev/sdb1",
                        "fstype": "xfs",
                    }
                ],
            }
        ]

        with self.assertRaisesRegex(RuntimeError, "contains xfs, expected ext4"):
            storage_steps._ensure_filesystem("/dev/sdb1", "ext4")

    @patch("common.storage_steps._run_capture")
    def test_findmnt_parent_mount_is_not_accepted(self, mock_run):
        mock_run.return_value = _result(
            stdout=json.dumps(
                {
                    "filesystems": [
                        {"source": "/dev/sda1", "fstype": "ext4", "target": "/"}
                    ]
                }
            )
        )

        self.assertIsNone(storage_steps._mounted_info("/srv/data"))

    @patch("common.storage_steps._run_capture")
    def test_rejects_uuid_unsafe_for_systemd_unit(self, mock_run):
        mock_run.return_value = _result(stdout="valid-looking%specifier\n")

        with self.assertRaisesRegex(RuntimeError, "filesystem UUID"):
            storage_steps._filesystem_uuid("/dev/sdb1")


class TestLVMCachePreparation(unittest.TestCase):
    @patch("common.storage_steps._find_device_by_path", return_value={"type": "lvm"})
    @patch("common.storage_steps._wipefs_signatures", return_value=[])
    @patch("common.storage_steps._run_capture")
    def test_creates_writethrough_cache_from_two_blank_disks(
        self,
        mock_run,
        _wipefs,
        _find_device,
    ):
        def run_side_effect(command: str, **_kwargs: object):
            if command.startswith("lvs "):
                return _result(returncode=5)
            return _result()

        mock_run.side_effect = run_side_effect
        record = storage_steps._prepare_lvm_cache(
            VMStorageCache("data", "cache"),
            data_disk={"path": "/dev/sdb", "children": []},
            cache_disk={"path": "/dev/sdc", "children": []},
        )

        commands = [item.args[0] for item in mock_run.call_args_list]
        self.assertIn(
            "pvcreate --yes --zero y -- /dev/sdb /dev/sdc",
            commands,
        )
        self.assertIn(
            "lvconvert --yes --type cache --cachevol it_data/cache "
            "--cachemode writethrough it_data/data",
            commands,
        )
        self.assertEqual(record["device"], "/dev/mapper/it_data-data")

    @patch("common.storage_steps._find_device_by_path", return_value={"type": "lvm"})
    @patch("common.storage_steps._wipefs_signatures", return_value=[])
    @patch("common.storage_steps._run_capture")
    def test_failed_conversion_removes_new_vg_and_pvs(
        self,
        mock_run,
        _wipefs,
        _find_device,
    ):
        def run_side_effect(command: str, **_kwargs: object):
            if command.startswith("lvs "):
                return _result(returncode=5)
            if command.startswith("lvconvert "):
                raise RuntimeError("conversion failed")
            return _result()

        mock_run.side_effect = run_side_effect

        with self.assertRaisesRegex(RuntimeError, "conversion failed"):
            storage_steps._prepare_lvm_cache(
                VMStorageCache("data", "cache"),
                data_disk={"path": "/dev/sdb", "children": []},
                cache_disk={"path": "/dev/sdc", "children": []},
            )

        commands = [item.args[0] for item in mock_run.call_args_list]
        self.assertIn("vgremove --yes --force it_data", commands)
        self.assertIn(
            "pvremove --yes -- /dev/sdb /dev/sdc",
            commands,
        )

    @patch("common.storage_steps._wipefs_signatures", return_value=[])
    @patch("common.storage_steps._run_capture")
    def test_failed_pv_creation_or_vg_cleanup_preserves_pv_labels(self, mock_run, _wipefs):
        for failed_command in ("pvcreate ", "vgremove "):
            with self.subTest(failed_command=failed_command):
                mock_run.reset_mock()

                def run_side_effect(command, **_kwargs):
                    if command.startswith("lvs "):
                        return _result(returncode=5)
                    if command.startswith("pvcreate ") and failed_command == "pvcreate ":
                        raise RuntimeError("creation failed")
                    if command.startswith("lvconvert "):
                        raise RuntimeError("conversion failed")
                    if command.startswith("vgremove "):
                        return _result(returncode=5)
                    return _result()

                mock_run.side_effect = run_side_effect
                with self.assertRaises(RuntimeError):
                    storage_steps._prepare_lvm_cache(
                        VMStorageCache("data", "cache"),
                        data_disk={"path": "/dev/sdb", "children": []},
                        cache_disk={"path": "/dev/sdc", "children": []},
                    )
                self.assertFalse(any(
                    item.args[0].startswith("pvremove ") for item in mock_run.call_args_list
                ))

    @patch("common.storage_steps._find_device_by_path", return_value={"type": "lvm"})
    @patch("common.storage_steps._verify_lvm_physical_volume")
    @patch("common.storage_steps._run_capture")
    def test_rerun_verifies_existing_cache_mode_and_pvs(
        self,
        mock_run,
        mock_verify_pv,
        _find_device,
    ):
        mock_run.return_value = _result(stdout=" writethrough \n")

        storage_steps._prepare_lvm_cache(
            VMStorageCache("data", "cache"),
            data_disk={"path": "/dev/sdb", "children": [{"type": "lvm"}]},
            cache_disk={"path": "/dev/sdc", "children": [{"type": "lvm"}]},
        )

        self.assertEqual(mock_verify_pv.call_count, 2)

    @patch("common.storage_steps._run_capture")
    def test_rejects_cache_mode_drift(self, mock_run):
        mock_run.return_value = _result(stdout="writeback\n")

        with self.assertRaisesRegex(RuntimeError, "expected writethrough"):
            storage_steps._prepare_lvm_cache(
                VMStorageCache("data", "cache"),
                data_disk={"path": "/dev/sdb"},
                cache_disk={"path": "/dev/sdc"},
            )


class TestPrepareMount(unittest.TestCase):
    def test_nonempty_mount_path_is_rejected_before_disk_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, "existing"), "w", encoding="utf-8") as file_obj:
                file_obj.write("preserve")
            mount = VMStorageMount("agent-data", directory, "ext4", "empty")
            with (
                patch("common.storage_steps._mounted_info", return_value=None),
                patch("common.storage_steps._find_declared_disk") as find_disk,
                patch("common.storage_steps._partition_for_mount") as partition,
            ):
                with self.assertRaisesRegex(RuntimeError, "must be empty"):
                    storage_steps._prepare_mount(
                        mount,
                        "64G",
                        "it-agent-data",
                        "scsi1",
                    )

            find_disk.assert_not_called()
            partition.assert_not_called()

    def test_writes_native_mount_unit_and_marker_after_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            mount = VMStorageMount("agent-data", directory, "ext4", "empty")
            mounted = ("/dev/sdb1", "ext4")
            with (
                patch("common.storage_steps._find_declared_disk", return_value={}),
                patch(
                    "common.storage_steps._partition_for_mount",
                    return_value="/dev/sdb1",
                ),
                patch(
                    "common.storage_steps._ensure_filesystem",
                    return_value="filesystem-uuid",
                ),
                patch(
                    "common.storage_steps._mounted_info",
                    side_effect=[None, mounted],
                ),
                patch(
                    "common.storage_steps._filesystem_uuid",
                    return_value="filesystem-uuid",
                ),
                patch(
                    "common.storage_steps._systemd_mount_unit",
                    return_value="srv-agent.mount",
                ),
                patch("common.storage_steps._run_capture") as mock_run,
                patch("common.storage_steps.write_text_atomic") as mock_text,
                patch("common.storage_steps.write_json_atomic") as mock_json,
            ):
                record = storage_steps._prepare_mount(
                    mount,
                    "64G",
                    "it-agent-data",
                    "scsi1",
                )

        unit_path, unit_text = mock_text.call_args.args[:2]
        self.assertEqual(unit_path, "/etc/systemd/system/srv-agent.mount")
        self.assertIn("What=/dev/disk/by-uuid/filesystem-uuid", unit_text)
        self.assertIn(f"Where={directory}", unit_text)
        self.assertNotIn("nofail", unit_text)
        mock_run.assert_has_calls(
            [
                call("systemctl daemon-reload"),
                call("systemctl enable --now srv-agent.mount"),
            ]
        )
        self.assertEqual(
            mock_json.call_args.args[0],
            os.path.join(directory, storage_steps.STORAGE_MARKER),
        )
        self.assertEqual(record["uuid"], "filesystem-uuid")
        self.assertEqual(record["bus_slot"], "scsi1")

    def test_rerun_verifies_existing_mount_before_rewriting_unit(self):
        with tempfile.TemporaryDirectory() as directory:
            mount = VMStorageMount("git-data", directory, "xfs", "empty")
            mounted = ("/dev/sdc1", "xfs")
            with (
                patch(
                    "common.storage_steps._find_declared_disk",
                    return_value={"children": [{"type": "part"}]},
                ),
                patch(
                    "common.storage_steps._partition_for_mount",
                    return_value="/dev/sdc1",
                ),
                patch(
                    "common.storage_steps._ensure_filesystem",
                    return_value="existing-uuid",
                ),
                patch(
                    "common.storage_steps._mounted_info",
                    side_effect=[mounted, mounted, mounted],
                ),
                patch(
                    "common.storage_steps._filesystem_uuid",
                    return_value="existing-uuid",
                ),
                patch(
                    "common.storage_steps._systemd_mount_unit",
                    return_value="srv-git.mount",
                ),
                patch("common.storage_steps._run_capture"),
                patch("common.storage_steps.write_text_atomic"),
                patch("common.storage_steps.write_json_atomic"),
            ):
                storage_steps._prepare_mount(
                    mount,
                    "128G",
                    "it-git-data",
                    "scsi2",
                )


class TestSetupVMStorage(unittest.TestCase):
    def test_cached_origin_mount_uses_mapper_and_records_cache_state(self):
        config = SetupConfig(
            host="host",
            username="deploy",
            system_type="server_web",
            machine_type="vm",
            container_storage=[
                ["root", "local-lvm", "32G"],
                ["data", "ts1-storage", "3T"],
                ["data-cache", "local-lvm", "128G"],
            ],
            storage_mounts=[["data", "/srv/data", "xfs"]],
            storage_caches=[["data", "data-cache"]],
        )
        disks = {
            "it-data": {"path": "/dev/sdb"},
            "it-data-cache": {"path": "/dev/sdc"},
        }
        cache_record = {
            "data_name": "data",
            "cache_name": "data-cache",
            "device": "/dev/mapper/it_data-data",
        }

        with (
            patch("common.storage_steps.is_dry_run", return_value=False),
            patch("common.storage_steps._run_capture", return_value=_result()),
            patch(
                "common.storage_steps._find_declared_disk",
                side_effect=lambda serial, _size: disks[serial],
            ),
            patch(
                "common.storage_steps._prepare_lvm_cache",
                return_value=cache_record.copy(),
            ) as prepare_cache,
            patch(
                "common.storage_steps._prepare_mount",
                return_value={"name": "data"},
            ) as prepare_mount,
            patch("common.storage_steps.write_json_atomic") as write_state,
        ):
            storage_steps.setup_vm_storage(config)

        prepare_cache.assert_called_once()
        self.assertEqual(
            prepare_mount.call_args.kwargs["prepared_device"],
            "/dev/mapper/it_data-data",
        )
        self.assertEqual(
            prepare_mount.call_args.kwargs["cache_record"]["cache_pool"],
            "local-lvm",
        )
        state = write_state.call_args.args[1]
        self.assertEqual(state["schema_version"], 2)
        self.assertEqual(state["caches"][0]["data_pool"], "ts1-storage")


class TestDeclaredMountAssertion(unittest.TestCase):
    def test_application_write_requires_matching_active_mount(self):
        with tempfile.TemporaryDirectory() as directory:
            marker_path = os.path.join(directory, storage_steps.STORAGE_MARKER)
            with open(marker_path, "w", encoding="utf-8") as file_obj:
                json.dump({"name": "agent-data", "uuid": "data-uuid"}, file_obj)
            config = SetupConfig(
                host="host",
                username="agent",
                system_type="workstation_dev",
                storage_mounts=[["agent-data", directory]],
            )

            with patch("common.storage_steps._verify_active_mount") as verify:
                storage_steps.assert_declared_storage_mount(
                    config,
                    os.path.join(directory, "repos"),
                )

        verify.assert_called_once_with(
            VMStorageMount("agent-data", directory, "ext4", "empty"),
            "data-uuid",
        )

    def test_missing_marker_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            config = SetupConfig(
                host="host",
                username="git",
                system_type="server_web",
                storage_mounts=[["git-data", directory]],
            )

            with self.assertRaisesRegex(RuntimeError, "marker is missing"):
                storage_steps.assert_declared_storage_mount(config, directory)


if __name__ == "__main__":
    unittest.main()
