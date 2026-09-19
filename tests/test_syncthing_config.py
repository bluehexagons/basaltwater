"""Tests for managed Syncthing CLI configuration and validation."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import basaltwater
from lib.arg_parser import create_setup_argument_parser
from lib.cache import merge_setup_configs
from lib.config import SetupConfig


class SyncthingConfigTest(unittest.TestCase):
    def _config(self, **overrides: object) -> SetupConfig:
        values: dict[str, object] = {
            "host": "fileserver",
            "username": "agent",
            "system_type": "server_lite",
            "enable_syncthing": True,
        }
        values.update(overrides)
        return SetupConfig(**values)  # type: ignore[arg-type]

    def test_flag_defaults_admin_and_round_trips(self) -> None:
        parser = create_setup_argument_parser("test")
        args = parser.parse_args(["fileserver", "agent", "--syncthing"])
        with patch("lib.system_utils.get_local_timezone", return_value="UTC"):
            config = SetupConfig.from_args(args, "server_lite")

        self.assertTrue(config.enable_syncthing)
        self.assertEqual(config.syncthing_admin, "syncthing-admin")
        self.assertIsNone(config.syncthing_root)
        self.assertIn("--syncthing", config.to_remote_args())
        self.assertIn("--syncthing-admin syncthing-admin", config.to_remote_args())
        self.assertIn("--syncthing-root /srv/syncthing", config.to_remote_args())

        restored = SetupConfig.from_dict("fileserver", "server_lite", config.to_dict())
        self.assertEqual(restored.syncthing_admin, "syncthing-admin")
        self.assertEqual(restored.syncthing_root, "/srv/syncthing")

    def test_custom_admin_is_in_commands(self) -> None:
        config = self._config(
            syncthing_admin="file-admin",
            syncthing_root="/mnt/team-files",
        )

        self.assertIn("--syncthing-admin file-admin", config.to_remote_args())
        self.assertIn("--syncthing-admin file-admin", config.to_setup_command())
        self.assertIn("--syncthing-root /mnt/team-files", config.to_remote_args())
        self.assertIn("--syncthing-root /mnt/team-files", config.to_setup_command())

    def test_vm_storage_provisioning_example_parses(self) -> None:
        parser = create_setup_argument_parser("test")
        args = parser.parse_args(
            [
                "192.168.0.60",
                "admin",
                "--provision-on",
                "pve1",
                "--name",
                "fileserver",
                "--cores",
                "2",
                "--memory",
                "4G",
                "--storage",
                "root",
                "local-lvm",
                "32G",
                "--storage",
                "syncthing-data",
                "bulk-lvm",
                "512G",
                "--storage-mount",
                "syncthing-data",
                "/srv/syncthing",
                "ext4",
                "empty",
                "--disk-backup",
                "syncthing-data",
                "--syncthing",
                "--syncthing-root",
                "/srv/syncthing",
            ]
        )
        with patch("lib.system_utils.get_local_timezone", return_value="UTC"):
            config = SetupConfig.from_args(args, "server_lite")

        self.assertEqual(
            config.container_storage,
            [
                ["root", "local-lvm", "32G"],
                ["syncthing-data", "bulk-lvm", "512G"],
            ],
        )
        self.assertEqual(
            config.storage_mounts,
            [["syncthing-data", "/srv/syncthing", "ext4", "empty"]],
        )
        self.assertEqual(config.syncthing_root, "/srv/syncthing")
        self.assertIn(
            "--storage-mount syncthing-data /srv/syncthing ext4 empty",
            config.to_remote_args(),
        )
        self.assertIn(
            "--syncthing-root /srv/syncthing",
            config.to_remote_args(),
        )

    def test_patch_omission_preserves_syncthing_settings(self) -> None:
        parser = create_setup_argument_parser("test")
        args = parser.parse_args(["fileserver", "agent"])
        with patch("lib.system_utils.get_local_timezone", return_value="UTC"):
            update = SetupConfig.from_args(args, "server_lite")
        self.assertIsNone(update.enable_syncthing)
        self.assertIsNone(update.syncthing_admin)

        merged = merge_setup_configs(
            self._config(syncthing_root="/srv/syncthing"),
            update,
        )
        self.assertTrue(merged.enable_syncthing)
        self.assertEqual(merged.syncthing_admin, "syncthing-admin")
        self.assertEqual(merged.syncthing_root, "/srv/syncthing")

    def test_explicit_disable_clears_admin(self) -> None:
        parser = create_setup_argument_parser("test")
        args = parser.parse_args(["fileserver", "agent", "--no-syncthing"])
        with patch("lib.system_utils.get_local_timezone", return_value="UTC"):
            update = SetupConfig.from_args(args, "server_lite")

        merged = merge_setup_configs(
            self._config(syncthing_root="/mnt/team-files"),
            update,
        )

        self.assertFalse(merged.enable_syncthing)
        self.assertTrue(merged.disable_syncthing)
        self.assertIsNone(merged.syncthing_admin)
        self.assertEqual(merged.syncthing_root, "/mnt/team-files")
        self.assertIn("--no-syncthing", merged.to_remote_args())
        self.assertIn(
            "--syncthing-root /mnt/team-files",
            merged.to_remote_args(),
        )
        self.assertNotIn("disable_syncthing", merged.to_dict())

        restored = SetupConfig.from_dict(
            "fileserver",
            "server_lite",
            merged.to_dict(),
        )
        self.assertFalse(restored.enable_syncthing)
        self.assertEqual(restored.syncthing_root, "/mnt/team-files")

    def test_reenable_preserves_saved_custom_storage_root(self) -> None:
        current = self._config(
            enable_syncthing=False,
            syncthing_admin=None,
            syncthing_root="/mnt/team-files",
        )
        parser = create_setup_argument_parser("test")
        args = parser.parse_args(["fileserver", "agent", "--syncthing"])
        with patch("lib.system_utils.get_local_timezone", return_value="UTC"):
            update = SetupConfig.from_args(args, "server_lite")

        self.assertIsNone(update.syncthing_root)
        merged = merge_setup_configs(current, update)

        self.assertTrue(merged.enable_syncthing)
        self.assertEqual(merged.syncthing_root, "/mnt/team-files")

    def test_removed_declarations_are_ignored_in_old_cache(self) -> None:
        data = self._config().to_dict()
        data.update(
            {
                "syncthing_devices": [["old-device", "OLD-ID"]],
                "syncthing_folders": [
                    ["send-receive", "old", "/srv/old", "old-device"]
                ],
                "syncthing_versioning": "trashcan",
            }
        )

        restored = SetupConfig.from_dict("fileserver", "server_lite", data)

        self.assertTrue(restored.enable_syncthing)
        self.assertFalse(hasattr(restored, "syncthing_devices"))

    def test_admin_requires_enablement_and_valid_username(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires --syncthing"):
            self._config(enable_syncthing=False, syncthing_admin="file-admin")
        with self.assertRaisesRegex(ValueError, "valid username"):
            self._config(syncthing_admin="Not Valid")

    def test_storage_root_is_bounded_and_normalized(self) -> None:
        with self.assertRaisesRegex(ValueError, "absolute, normalized"):
            self._config(syncthing_root="srv/syncthing")
        with self.assertRaisesRegex(ValueError, "must be /data or below"):
            self._config(syncthing_root="/home/agent/syncthing")
        with self.assertRaisesRegex(ValueError, "components may contain"):
            self._config(syncthing_root="/srv/team files")
        with self.assertRaisesRegex(ValueError, "must not overlap"):
            self._config(syncthing_root="/var/lib/basaltwater")

    def test_root_and_oci_targets_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "non-root"):
            self._config(username="root")
        with self.assertRaisesRegex(ValueError, "OCI"):
            self._config(machine_type="oci")

    @patch("basaltwater.get_all_configs")
    def test_info_displays_syncthing_feature(self, get_all_configs) -> None:
        get_all_configs.return_value = [
            {
                "host": "fileserver",
                "system_type": "server_lite",
                "args": {
                    "username": "agent",
                    "enable_syncthing": True,
                },
            }
        ]

        output = io.StringIO()
        with redirect_stdout(output):
            result = basaltwater.show_info()

        self.assertEqual(result, 0)
        self.assertIn("Features: Syncthing", output.getvalue())


if __name__ == "__main__":
    unittest.main()
