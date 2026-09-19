"""Tests for obsolete workspace configuration cleanup."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import basaltwater
from lib.config import SetupConfig
from lib.config_cleanup import run_cleanup
from lib.proxmox_hosts import get_proxmox_hosts_path


class TestConfigCleanup(unittest.TestCase):
    def _write_json(self, path: Path, value: object) -> None:
        path.write_text(json.dumps(value), encoding="utf-8")

    def test_parser_accepts_target_host_and_options(self) -> None:
        parser, _setup_parser, _patch_parser = basaltwater.create_basaltwater_parser()
        args = parser.parse_args(
            ["cleanup", "192.168.0.41", "--dry-run", "--yes"]
        )
        self.assertEqual(args.command, "cleanup")
        self.assertEqual(args.host, "192.168.0.41")
        self.assertTrue(args.dry_run)
        self.assertTrue(args.yes)
        root_workspace_args = parser.parse_args(
            ["--workspace", "/tmp/workspace", "cleanup", "192.168.0.41", "--dry-run"]
        )
        self.assertEqual(root_workspace_args.workspace, "/tmp/workspace")

    def test_targeted_cleanup_removes_only_obsolete_host_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            setup_dir = workspace / "setups"
            setup_dir.mkdir()
            stale_path = setup_dir / "stale.json"
            valid_path = setup_dir / "valid.json"
            self._write_json(
                stale_path,
                {
                    "host": "192.168.0.41",
                    "system_type": "workstation_dev",
                    "args": {"username": "loren", "install_t3code": True},
                },
            )
            valid_config = SetupConfig(
                host="192.168.0.42",
                username="loren",
                system_type="server_lite",
            )
            self._write_json(
                valid_path,
                {
                    "host": valid_config.host,
                    "system_type": valid_config.system_type,
                    "args": valid_config.to_dict(),
                },
            )

            result = run_cleanup(
                "192.168.0.41",
                workspace=str(workspace),
                assume_yes=True,
            )

            self.assertEqual(result, 0)
            self.assertFalse(stale_path.exists())
            self.assertTrue(valid_path.exists())
            backup_files = list((workspace / "cleanup-backups").glob("*/*"))
            self.assertEqual(len(backup_files), 1)
            self.assertEqual(backup_files[0].name, stale_path.name)

    def test_cleanup_removes_invalid_proxmox_record_and_preserves_valid_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            registry_path = Path(get_proxmox_hosts_path(str(workspace)))
            self._write_json(
                registry_path,
                [
                    {"name": "old", "address": "10.0.0.10"},
                    {
                        "name": "pve1",
                        "address": "10.0.0.11",
                        "schema_version": 1,
                        "provider": "proxmox",
                    },
                ],
            )

            result = run_cleanup(
                workspace=str(workspace),
                include_setup_cache=False,
                include_proxmox_registry=True,
                assume_yes=True,
            )

            self.assertEqual(result, 0)
            records = json.loads(registry_path.read_text(encoding="utf-8"))
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["name"], "pve1")
            backup_files = list((workspace / "cleanup-backups").glob("*/*"))
            self.assertEqual(len(backup_files), 1)
            self.assertEqual(backup_files[0].name, registry_path.name)

    def test_dry_run_does_not_change_state_or_create_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            setup_dir = workspace / "setups"
            setup_dir.mkdir()
            stale_path = setup_dir / "stale.json"
            self._write_json(
                stale_path,
                {
                    "host": "192.168.0.41",
                    "system_type": "workstation_dev",
                    "args": {"username": "loren", "install_t3code": True},
                },
            )

            result = run_cleanup(
                "192.168.0.41",
                workspace=str(workspace),
                dry_run=True,
            )

            self.assertEqual(result, 0)
            self.assertTrue(stale_path.exists())
            self.assertFalse((workspace / "cleanup-backups").exists())

    def test_noninteractive_cleanup_requires_confirmation_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            setup_dir = workspace / "setups"
            setup_dir.mkdir()
            stale_path = setup_dir / "stale.json"
            self._write_json(
                stale_path,
                {
                    "host": "192.168.0.41",
                    "system_type": "workstation_dev",
                    "args": {"username": "loren", "install_t3code": True},
                },
            )

            with patch("lib.config_cleanup.sys.stdin.isatty", return_value=False):
                result = run_cleanup("192.168.0.41", workspace=str(workspace))

            self.assertEqual(result, 1)
            self.assertTrue(stale_path.exists())


if __name__ == "__main__":
    unittest.main()
