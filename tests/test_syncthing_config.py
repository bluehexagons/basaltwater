"""Tests for managed Syncthing CLI configuration and validation."""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import basaltwater
from lib.arg_parser import create_setup_argument_parser
from lib.atomic_io import write_json_atomic
from lib.cache import (
    get_cache_path_for_host,
    load_setup_command,
    merge_setup_configs,
    save_setup_command,
)
from lib.config import SetupConfig
from lib.state_read import StateReadError


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

    def test_removed_declarations_require_intermediate_upgrade(self) -> None:
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

        with self.assertRaisesRegex(ValueError, "docs/migration-guide.md"):
            SetupConfig.from_dict("fileserver", "server_lite", data)
        self.assertEqual(data['syncthing_devices'], [["old-device", "OLD-ID"]])
        self.assertEqual(data['syncthing_versioning'], 'trashcan')

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

    def test_storage_root_rejects_reserved_system_trees_without_host_probes(self) -> None:
        for tree in ("dpkg", "apt", "systemd", "dbus", "private", "polkit-1"):
            for suffix in ("", "/empty", "/not-created-yet"):
                with self.subTest(tree=tree, suffix=suffix):
                    with self.assertRaisesRegex(ValueError, "reserved system trees"):
                        self._config(syncthing_root=f"/var/lib/{tree}{suffix}")

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


class SyncthingSavedConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        cache_dir = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch("lib.cache.get_setup_cache_dir", return_value=cache_dir))
        self.enterContext(patch("lib.system_utils.get_local_timezone", return_value="UTC"))
        self.parser = basaltwater.create_basaltwater_parser()[0]
        self.cache_path = get_cache_path_for_host("fileserver")

    def _write_saved_config(self, **overrides: object) -> None:
        args = {
            "username": "agent",
            "enable_syncthing": True,
            "syncthing_root": "/var/lib/dpkg",
        }
        args.update(overrides)
        write_json_atomic(self.cache_path, {
            "host": "fileserver", "system_type": "server_lite", "args": args,
        })

    def test_patch_can_disable_or_replace_cached_reserved_root_and_save(self) -> None:
        for options, enabled, root in (
            (["--no-syncthing"], False, "/var/lib/dpkg"),
            (["--syncthing-root", "/srv/team-files"], True, "/srv/team-files"),
        ):
            with self.subTest(options=options):
                self._write_saved_config()
                args = self.parser.parse_args(["patch", "fileserver", "agent", *options])
                with patch("basaltwater._execute_patch_config", return_value=0) as execute:
                    self.assertEqual(basaltwater.run_patch_command(args), 0)
                corrected = execute.call_args.args[0]
                self.assertEqual(corrected.enable_syncthing, enabled)
                self.assertEqual(corrected.syncthing_root, root)
                self.assertEqual(corrected.disable_syncthing, not enabled)
                remote_args = corrected.to_remote_args()
                self.assertIn("--syncthing" if enabled else "--no-syncthing", remote_args)
                if not enabled:
                    self.assertIsNone(corrected.syncthing_admin)
                save_setup_command(corrected, operation="patch")
                restored = load_setup_command("fileserver")
                self.assertIsNotNone(restored)
                self.assertEqual(restored.enable_syncthing, enabled)
                self.assertEqual(restored.syncthing_root, root)

    def test_cached_reserved_root_cannot_be_replayed_or_activated(self) -> None:
        for enabled, options in ((True, []), (True, ["--syncthing"]), (False, ["--syncthing"])):
            with self.subTest(enabled=enabled, options=options):
                self._write_saved_config(enable_syncthing=enabled)
                cached = load_setup_command("fileserver")
                self.assertIsNotNone(cached)
                self.assertEqual(cached.syncthing_root, "/var/lib/dpkg")
                if enabled:
                    with self.assertRaisesRegex(ValueError, "reserved system trees"):
                        cached.to_remote_args()
                args = self.parser.parse_args(["patch", "fileserver", "agent", *options])
                with (
                    patch("basaltwater._execute_patch_config") as execute,
                    redirect_stdout(io.StringIO()) as output,
                ):
                    self.assertEqual(basaltwater.run_patch_command(args), 1)
                execute.assert_not_called()
                self.assertIn("reserved system trees", output.getvalue())

    def test_correction_does_not_hide_malformed_saved_settings(self) -> None:
        for overrides in (
            {"syncthing_root": 42},
            {"syncthing_root": "/var/lib/dpkg\n"},
            {"syncthing_root": "/var/lib/dpkg/../dpkg"},
            {"syncthing_root": "var/lib/dpkg"},
            {"syncthing_root": "/var/lib/dpkg/team files"},
            {"syncthing_root": "/home/agent/files"},
            {"syncthing_root": "/var/lib/basaltwater"},
            {"syncthing_admin": "Not Valid"},
            {"web_panel_port": -1},
        ):
            with self.subTest(overrides=overrides):
                self._write_saved_config(**overrides)
                with open(self.cache_path, "rb") as stream:
                    original = stream.read()
                for options in (["--no-syncthing"], ["--syncthing-root", "/srv/team-files"]):
                    args = self.parser.parse_args(["patch", "fileserver", "agent", *options])
                    with patch("basaltwater._execute_patch_config") as execute:
                        with self.assertRaises(StateReadError):
                            basaltwater.run_patch_command(args)
                    execute.assert_not_called()
                with open(self.cache_path, "rb") as stream:
                    self.assertEqual(stream.read(), original)


if __name__ == "__main__":
    unittest.main()
