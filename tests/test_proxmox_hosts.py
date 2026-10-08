"""Tests for lib/proxmox_hosts.py: registry CRUD."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.proxmox_hosts import (
    PROXMOX_HOST_SCHEMA_VERSION,
    PROXMOX_PROVIDER,
    ProxmoxHost,
    ProxmoxHostFacts,
    ProxmoxStoragePool,
    add_proxmox_host,
    find_proxmox_host,
    get_proxmox_hosts_path,
    load_proxmox_hosts,
    remove_proxmox_host,
    save_proxmox_hosts,
    sync_proxmox_host,
)


class _WorkspaceFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()


class TestProxmoxHostRecord(unittest.TestCase):
    def test_to_from_dict_roundtrip(self) -> None:
        host = ProxmoxHost(
            name="pve1", address="10.0.0.10", user="root",
            ssh_key="/key", description="primary",
            default_storage="local-lvm",
            default_template_storage="local",
            default_bridge="vmbr0",
            facts=ProxmoxHostFacts(
                node_name="pve1",
                bridges=["vmbr0", "vmbr1"],
                gateway="10.0.0.1",
                nameservers=["1.1.1.1", "8.8.8.8"],
                storage_pools=[
                    ProxmoxStoragePool(
                        name="local-lvm",
                        type="lvmthin",
                        status="active",
                        content=["images", "rootdir"],
                    ),
                    ProxmoxStoragePool(
                        name="local",
                        type="dir",
                        status="active",
                        content=["vztmpl"],
                    ),
                ],
                default_root_storage="local-lvm",
                default_template_storage="local",
                default_bridge="vmbr0",
            ),
            tags=["prod", "az-east"],
        )
        restored = ProxmoxHost.from_dict(host.to_dict())
        self.assertEqual(restored, host)
        self.assertEqual(restored.schema_version, PROXMOX_HOST_SCHEMA_VERSION)
        self.assertEqual(restored.provider, PROXMOX_PROVIDER)

    def test_from_dict_rejects_development_records_without_schema_or_provider(self) -> None:
        with self.assertRaisesRegex(ValueError, "re-register"):
            ProxmoxHost.from_dict({"name": "pve", "address": "10.0.0.10"})
        with self.assertRaisesRegex(ValueError, "provider='proxmox'"):
            ProxmoxHost.from_dict(
                {
                    "schema_version": PROXMOX_HOST_SCHEMA_VERSION,
                    "provider": "other",
                    "name": "pve",
                    "address": "10.0.0.10",
                }
            )

    def test_from_dict_requires_name_and_address(self) -> None:
        with self.assertRaises(ValueError):
            ProxmoxHost.from_dict({"name": "pve"})
        with self.assertRaises(ValueError):
            ProxmoxHost.from_dict({"address": "10.0.0.10"})

    def test_from_dict_rejects_bad_tags(self) -> None:
        with self.assertRaises(ValueError):
            ProxmoxHost.from_dict({
                "schema_version": PROXMOX_HOST_SCHEMA_VERSION,
                "provider": PROXMOX_PROVIDER,
                "name": "pve", "address": "10.0.0.10", "tags": "prod"
            })

    def test_from_dict_rejects_bad_facts_lists(self) -> None:
        with self.assertRaises(ValueError):
            ProxmoxHost.from_dict({
                "schema_version": PROXMOX_HOST_SCHEMA_VERSION,
                "provider": PROXMOX_PROVIDER,
                "name": "pve",
                "address": "10.0.0.10",
                "facts": {"bridges": "vmbr0"},
            })


class TestRegistryRoundTrip(_WorkspaceFixture):
    def test_empty_registry_returns_empty_list(self) -> None:
        self.assertEqual(load_proxmox_hosts(self.workspace), [])

    def test_save_and_load_preserves_order_and_fields(self) -> None:
        hosts = [
            ProxmoxHost(name="a", address="10.0.0.1"),
            ProxmoxHost(name="b", address="10.0.0.2", user="admin"),
        ]
        path = save_proxmox_hosts(hosts, self.workspace)
        self.assertEqual(path, get_proxmox_hosts_path(self.workspace))
        loaded = load_proxmox_hosts(self.workspace)
        self.assertEqual(loaded, hosts)

    def test_save_writes_file_with_restrictive_perms(self) -> None:
        save_proxmox_hosts([ProxmoxHost(name="a", address="10.0.0.1")], self.workspace)
        path = get_proxmox_hosts_path(self.workspace)
        mode = os.stat(path).st_mode & 0o777
        self.assertEqual(mode, 0o600)
        with open(path, encoding="utf-8") as source:
            record = json.load(source)[0]
        self.assertEqual(record["schema_version"], PROXMOX_HOST_SCHEMA_VERSION)
        self.assertEqual(record["provider"], PROXMOX_PROVIDER)

    def test_save_rejects_invalid_provider_before_writing(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid provider"):
            save_proxmox_hosts(
                [ProxmoxHost(name="a", address="10.0.0.1", provider="other")],
                self.workspace,
            )
        self.assertFalse(os.path.exists(get_proxmox_hosts_path(self.workspace)))

    def test_load_rejects_non_array(self) -> None:
        path = get_proxmox_hosts_path(self.workspace)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('{"not": "an array"}')
        with self.assertRaises(ValueError):
            load_proxmox_hosts(self.workspace)

    def test_load_error_identifies_incompatible_record_and_registry(self) -> None:
        path = get_proxmox_hosts_path(self.workspace)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as destination:
            json.dump([{"name": "old", "address": "10.0.0.1"}], destination)

        with self.assertRaisesRegex(ValueError, "proxmox_hosts.json.*record 0"):
            load_proxmox_hosts(self.workspace)


class TestAddProxmoxHost(_WorkspaceFixture):
    def test_add_new_host(self) -> None:
        host = ProxmoxHost(name="pve1", address="10.0.0.10")
        add_proxmox_host(host, self.workspace)
        self.assertEqual(load_proxmox_hosts(self.workspace), [host])

    def test_add_duplicate_without_replace_raises(self) -> None:
        host = ProxmoxHost(name="pve1", address="10.0.0.10")
        add_proxmox_host(host, self.workspace)
        with self.assertRaises(ValueError):
            add_proxmox_host(
                ProxmoxHost(name="PVE1", address="10.0.0.99"),
                self.workspace,
            )

    def test_add_duplicate_address_without_replace_raises(self) -> None:
        add_proxmox_host(
            ProxmoxHost(name="pve1", address="10.0.0.10"), self.workspace
        )
        with self.assertRaisesRegex(ValueError, "address.*already exists"):
            add_proxmox_host(
                ProxmoxHost(name="other", address="10.0.0.10"), self.workspace
            )

    def test_add_rejects_invalid_address_and_user(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid Proxmox host address"):
            add_proxmox_host(
                ProxmoxHost(name="pve1", address="not a host"), self.workspace
            )
        with self.assertRaisesRegex(ValueError, "Invalid Proxmox SSH user"):
            add_proxmox_host(
                ProxmoxHost(name="pve1", address="10.0.0.10", user="bad user"),
                self.workspace,
            )

    def test_add_rejects_wrong_provider_or_schema(self) -> None:
        with self.assertRaisesRegex(ValueError, "schema version"):
            add_proxmox_host(
                ProxmoxHost(
                    name="pve1",
                    address="10.0.0.10",
                    schema_version=2,
                ),
                self.workspace,
            )
        with self.assertRaisesRegex(ValueError, "Invalid provider"):
            add_proxmox_host(
                ProxmoxHost(
                    name="pve1",
                    address="10.0.0.10",
                    provider="other",
                ),
                self.workspace,
            )

    def test_add_duplicate_with_replace_overwrites(self) -> None:
        add_proxmox_host(
            ProxmoxHost(name="pve1", address="10.0.0.10"), self.workspace
        )
        add_proxmox_host(
            ProxmoxHost(name="pve1", address="10.0.0.11", description="updated"),
            self.workspace, replace=True,
        )
        loaded = load_proxmox_hosts(self.workspace)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].address, "10.0.0.11")
        self.assertEqual(loaded[0].description, "updated")

    def test_add_requires_name_and_address(self) -> None:
        with self.assertRaises(ValueError):
            add_proxmox_host(ProxmoxHost(name="", address="10.0.0.1"), self.workspace)
        with self.assertRaises(ValueError):
            add_proxmox_host(ProxmoxHost(name="pve", address=""), self.workspace)

    def test_sync_merges_by_address_and_preserves_existing_metadata(self) -> None:
        add_proxmox_host(
            ProxmoxHost(
                name="seed",
                address="10.0.0.10",
                description="manual note",
                tags=["prod"],
                default_storage="manual-root",
            ),
            self.workspace,
        )

        synced = sync_proxmox_host(
            ProxmoxHost(
                name="pve1",
                address="10.0.0.10",
                default_template_storage="local",
                tags=["cluster"],
            ),
            self.workspace,
        )

        self.assertEqual(synced.name, "pve1")
        self.assertEqual(synced.description, "manual note")
        self.assertEqual(synced.default_storage, "manual-root")
        self.assertEqual(synced.default_template_storage, "local")
        self.assertEqual(synced.tags, ["prod", "cluster"])


class TestFindAndRemove(_WorkspaceFixture):
    def setUp(self) -> None:
        super().setUp()
        add_proxmox_host(
            ProxmoxHost(name="pve1", address="10.0.0.10"), self.workspace
        )
        add_proxmox_host(
            ProxmoxHost(name="pve2", address="10.0.0.20"), self.workspace
        )

    def test_find_by_name_case_insensitive(self) -> None:
        host = find_proxmox_host("PVE1", self.workspace)
        self.assertIsNotNone(host)
        assert host is not None
        self.assertEqual(host.address, "10.0.0.10")

    def test_find_by_address(self) -> None:
        host = find_proxmox_host("10.0.0.20", self.workspace)
        self.assertIsNotNone(host)
        assert host is not None
        self.assertEqual(host.name, "pve2")

    def test_find_missing_returns_none(self) -> None:
        self.assertIsNone(find_proxmox_host("nope", self.workspace))

    def test_remove_by_name(self) -> None:
        self.assertTrue(remove_proxmox_host("pve1", self.workspace))
        self.assertEqual(
            [h.name for h in load_proxmox_hosts(self.workspace)], ["pve2"]
        )

    def test_remove_missing_returns_false(self) -> None:
        self.assertFalse(remove_proxmox_host("missing", self.workspace))

    def test_remove_can_delete_incompatible_development_record(self) -> None:
        path = get_proxmox_hosts_path(self.workspace)
        with open(path, "w", encoding="utf-8") as destination:
            json.dump(
                [
                    ProxmoxHost(name="pve1", address="10.0.0.10").to_dict(),
                    {"name": "old", "address": "10.0.0.99"},
                ],
                destination,
            )

        self.assertTrue(remove_proxmox_host("old", self.workspace))
        self.assertEqual(
            [host.name for host in load_proxmox_hosts(self.workspace)],
            ["pve1"],
        )


if __name__ == "__main__":
    unittest.main()
