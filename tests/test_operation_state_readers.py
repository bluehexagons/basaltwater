"""Saved inventories must preserve invalid state rather than reset defaults."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lib.proxmox_hosts import ProxmoxHost, add_proxmox_host, load_proxmox_hosts, save_proxmox_hosts
from lib.state_read import StateReadError
from web.service_tools import webhook_manager


class TestInventoryStateReaders(unittest.TestCase):
    def test_invalid_proxmox_registry_blocks_reads_and_saves(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "proxmox_hosts.json"
            for content in ("broken", "{}", "null", '[{"schema_version": 99}]', '[{"schema_version": true}]'):
                with self.subTest(content=content):
                    path.write_text(content)
                    for action in (
                        lambda: load_proxmox_hosts(directory),
                        lambda: save_proxmox_hosts([], directory),
                        lambda: add_proxmox_host(ProxmoxHost("node", "node.example"), directory),
                    ):
                        with self.assertRaisesRegex(StateReadError, "File retained"):
                            action()
                        self.assertEqual(path.read_text(), content)

    def test_symlink_and_fifo_host_registries_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "proxmox_hosts.json"
            path.symlink_to(Path(directory) / "missing")
            with self.assertRaises(StateReadError):
                load_proxmox_hosts(directory)
            path.unlink()
            os.mkfifo(path)
            with self.assertRaises(StateReadError):
                load_proxmox_hosts(directory)

    def test_host_record_wrong_field_types_are_not_coerced(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "proxmox_hosts.json"
            original = ProxmoxHost("node", "node.example").to_dict()
            for field, invalid in (
                ("name", []), ("user", 1), ("facts", []), ("tags", [1]), ("tags", False),
                ("facts", {"bridges": [1]}), ("facts", {"gateway": 123}),
                ("facts", {"storage_pools": False}),
                ("facts", {"storage_pools": [{"name": "pool", "content": [1]}]}),
            ):
                with self.subTest(field=field):
                    path.write_text(json.dumps([{**original, field: invalid}]))
                    with self.assertRaises(StateReadError):
                        load_proxmox_hosts(directory)

    def test_invalid_host_diagnostic_does_not_echo_record_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "proxmox_hosts.json"
            host = ProxmoxHost("node", "private-invalid-address!secret").to_dict()
            path.write_text(json.dumps([host]))
            with self.assertRaises(StateReadError) as raised:
                load_proxmox_hosts(directory)
            self.assertNotIn("secret", str(raised.exception))

    def test_missing_registry_has_defaults_and_can_be_created(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(load_proxmox_hosts(directory), [])
            host = ProxmoxHost("node", "node.example")
            save_proxmox_hosts([host], directory)
            self.assertEqual(load_proxmox_hosts(directory), [host])

    def test_dangling_webhook_config_link_is_not_missing_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.symlink_to(Path(directory) / "missing")
            with patch.object(webhook_manager, "CONFIG_FILE", str(path)):
                with self.assertRaises((OSError, ValueError)):
                    webhook_manager.load_config()
            self.assertTrue(path.is_symlink())
