"""Tests for cache-aware Proxmox provisioning dispatch."""

from __future__ import annotations

from argparse import Namespace
import unittest
from unittest.mock import patch

import basaltwater
from lib.config import SetupConfig


def _config(**overrides: object) -> SetupConfig:
    values: dict[str, object] = {
        "host": "10.0.0.50",
        "username": "agent",
        "system_type": "workstation_dev",
        "machine_type": "vm",
        "hosted_node": "10.0.0.10",
    }
    values.update(overrides)
    return SetupConfig(**values)


def _args(**overrides: object) -> Namespace:
    values: dict[str, object] = {
        "host": "10.0.0.50",
        "username": "agent",
        "system_type": "workstation_dev",
        "hosted_node": "10.0.0.10",
        "machine_type": None,
        "hosted_bridge": None,
        "container_memory": None,
        "vm_balloon_min": None,
        "container_storage": None,
        "storage_mounts": None,
        "storage_caches": None,
        "vm_image": None,
        "vm_image_storage": None,
        "static_ipv4": None,
        "system_hostname": None,
        "friendly_name": None,
    }
    values.update(overrides)
    return Namespace(**values)


class TestCachedProvisioningMetadata(unittest.TestCase):
    def test_setup_parser_accepts_additive_mounted_disk_without_root(self) -> None:
        parser, _setup_parser, _patch_parser = basaltwater.create_basaltwater_parser()
        args = parser.parse_args(
            [
                "setup",
                "server_lite",
                "10.0.0.50",
                "admin",
                "--provision-on",
                "pve1",
                "--storage",
                "team-data",
                "bulk-lvm",
                "512G",
                "--storage-mount",
                "team-data",
                "/srv/team-files",
                "ext4",
                "empty",
            ]
        )

        config = SetupConfig.from_args(args, args.system_type)

        self.assertEqual(
            config.container_storage,
            [["team-data", "bulk-lvm", "512G"]],
        )
        self.assertEqual(
            config.storage_mounts,
            [["team-data", "/srv/team-files", "ext4", "empty"]],
        )

    def test_setup_parser_accepts_force_provider_verification(self) -> None:
        parser, _setup_parser, _patch_parser = basaltwater.create_basaltwater_parser()

        args = parser.parse_args(
            [
                "setup",
                "agent_code_vm",
                "10.0.0.50",
                "agent",
                "--provision-on",
                "pve1",
                "--verify-provider",
            ]
        )

        self.assertTrue(args.verify_provider)

    def test_force_provider_verification_requires_provisioning_host(self) -> None:
        args = _args(verify_provider=True)

        with patch("basaltwater.prompt_for_missing_passwords"), \
             patch(
                 "basaltwater.SetupConfig.from_args",
                 return_value=_config(hosted_node=None),
             ), \
             patch("builtins.print") as mock_print:
            result = basaltwater.run_setup_command(args)

        self.assertEqual(result, 1)
        mock_print.assert_called_once_with(
            "Error: --verify-provider requires --provision-on"
        )

    def test_setup_reports_configuration_errors_without_a_traceback(self) -> None:
        args = _args()

        with patch("basaltwater.prompt_for_missing_passwords"), \
             patch(
                 "basaltwater.SetupConfig.from_args",
                 side_effect=ValueError("invalid setup selection"),
             ), \
             patch("builtins.print") as mock_print:
            result = basaltwater.run_setup_command(args)

        self.assertEqual(result, 1)
        mock_print.assert_called_once_with("Error: invalid setup selection")

    def test_patch_reports_configuration_errors_without_a_traceback(self) -> None:
        args = _args()
        cached = _config()

        with patch("basaltwater.validate_host", return_value=True), \
             patch("basaltwater.validate_username", return_value=True), \
             patch("basaltwater.load_setup_command", return_value=cached), \
             patch(
                 "basaltwater.SetupConfig.from_args",
                 side_effect=ValueError("invalid patch selection"),
             ), \
             patch("builtins.print") as mock_print:
            result = basaltwater.run_patch_command(args)

        self.assertEqual(result, 1)
        mock_print.assert_called_once_with("Error: invalid patch selection")

    def test_reuses_saved_guest_shape_when_no_changes_are_requested(self) -> None:
        current = _config()
        cached = _config(
            hosted_node="10.0.0.10",
            hosted_bridge="vmbr0",
            container_memory="4G",
            vm_balloon_min="1G",
            container_storage=[
                ["root", "local-lvm", "32G"],
                ["agent-data", "bulk-lvm", "128G"],
            ],
            storage_mounts=[["agent-data", "/srv/agent-workspace"]],
            storage_caches=[["agent-data", "agent-cache"]],
            container_cores=4,
            static_ipv4="10.0.0.50/24",
            network_gateway4="10.0.0.1",
            network_dns=["1.1.1.1"],
            ssh_key="/keys/agent",
        )

        with patch("basaltwater.load_setup_command", return_value=cached):
            reused = basaltwater._reuse_cached_provisioning_metadata(current, _args())

        self.assertTrue(reused)
        self.assertEqual(current.hosted_node, "10.0.0.10")
        self.assertEqual(current.container_memory, "4G")
        self.assertEqual(current.container_cores, 4)
        self.assertEqual(
            current.container_storage,
            [
                ["root", "local-lvm", "32G"],
                ["agent-data", "bulk-lvm", "128G"],
            ],
        )
        self.assertEqual(
            current.storage_mounts,
            [["agent-data", "/srv/agent-workspace"]],
        )
        self.assertEqual(
            current.storage_caches,
            [["agent-data", "agent-cache"]],
        )
        self.assertEqual(current.static_ipv4, "10.0.0.50/24")
        self.assertEqual(current.network_gateway4, "10.0.0.1")
        self.assertEqual(current.network_dns, ["1.1.1.1"])
        self.assertEqual(current.ssh_key, "/keys/agent")

    def test_explicit_guest_shape_change_requires_proxmox(self) -> None:
        current = _config(container_memory="8G")
        cached = _config(
            hosted_node="10.0.0.10",
            container_memory="4G",
            container_storage=[
                ["root", "local-lvm", "32G"],
                ["agent-data", "bulk-lvm", "128G"],
            ],
            storage_mounts=[["agent-data", "/srv/agent-workspace"]],
            static_ipv4="10.0.0.50/24",
            network_gateway4="10.0.0.1",
            network_dns=["1.1.1.1"],
        )

        with patch("basaltwater.load_setup_command", return_value=cached) as mock_load:
            reused = basaltwater._reuse_cached_provisioning_metadata(
                current,
                _args(container_memory="8G"),
            )

        self.assertFalse(reused)
        mock_load.assert_called_once_with("10.0.0.50")
        self.assertEqual(current.container_memory, "8G")
        self.assertEqual(current.hosted_node, "10.0.0.10")
        self.assertEqual(current.container_storage, cached.container_storage)
        self.assertEqual(current.storage_mounts, cached.storage_mounts)
        self.assertEqual(current.network_gateway4, "10.0.0.1")

        self.assertTrue(
            basaltwater._provisioning_changes_requested(
                _config(container_cores=2),
                _config(container_cores=1),
                _args(container_cores=2),
            )
        )
        self.assertTrue(
            basaltwater._provisioning_changes_requested(
                _config(storage_mounts=[["data", "/srv/new"]]),
                _config(storage_mounts=[["data", "/srv/old"]]),
                _args(storage_mounts=[["data", "/srv/new"]]),
            )
        )
        self.assertTrue(
            basaltwater._provisioning_changes_requested(
                _config(container_base="ubuntu"),
                _config(container_base="debian"),
                _args(container_base="ubuntu"),
            )
        )

    def test_provider_rebind_preserves_explicit_destination_metadata(self) -> None:
        current = _config(
            hosted_node="10.0.0.11",
            hosted_user="admin",
            hosted_key="/keys/pve2",
            hosted_bridge="vmbr1",
        )
        cached = _config(
            hosted_node="10.0.0.10",
            hosted_user="root",
            hosted_key="/keys/pve1",
            hosted_bridge="vmbr0",
            container_memory="4G",
            container_storage=[["root", "local-lvm", "32G"]],
            static_ipv4="10.0.0.50/24",
            network_gateway4="10.0.0.1",
            network_dns=["1.1.1.1"],
        )
        args = _args(
            hosted_node="10.0.0.11",
            hosted_user="admin",
            hosted_key="/keys/pve2",
            hosted_bridge="vmbr1",
        )

        provider_rebind = basaltwater._provider_rebind_requested(
            current,
            cached,
            args,
        )
        reused = basaltwater._reuse_cached_provisioning_metadata(
            current,
            args,
            cached,
            provider_rebind=provider_rebind,
        )

        self.assertTrue(provider_rebind)
        self.assertFalse(reused)
        self.assertEqual(current.hosted_node, "10.0.0.11")
        self.assertEqual(current.hosted_user, "admin")
        self.assertEqual(current.hosted_key, "/keys/pve2")
        self.assertEqual(current.hosted_bridge, "vmbr1")
        self.assertEqual(current.container_storage, cached.container_storage)

    def test_same_node_rerun_preserves_explicit_provider_credentials(self) -> None:
        current = _config(
            hosted_user="operator",
            hosted_key="/keys/new",
        )
        cached = _config(
            hosted_user="admin",
            hosted_key="/keys/old",
            container_memory="4G",
            container_storage=[["root", "local-lvm", "32G"]],
            static_ipv4="10.0.0.50/24",
            network_gateway4="10.0.0.1",
            network_dns=["1.1.1.1"],
        )

        basaltwater._reuse_cached_provisioning_metadata(
            current,
            _args(hosted_user="operator", hosted_key="/keys/new"),
            cached,
        )

        self.assertEqual(current.hosted_user, "operator")
        self.assertEqual(current.hosted_key, "/keys/new")

    def test_same_node_rerun_inherits_unmodified_provider_credentials(self) -> None:
        current = _config()
        cached = _config(
            hosted_user="admin",
            hosted_key="/keys/old",
            container_memory="4G",
            container_storage=[["root", "local-lvm", "32G"]],
            static_ipv4="10.0.0.50/24",
            network_gateway4="10.0.0.1",
            network_dns=["1.1.1.1"],
        )

        basaltwater._reuse_cached_provisioning_metadata(
            current,
            _args(),
            cached,
        )

        self.assertEqual(current.hosted_user, "admin")
        self.assertEqual(current.hosted_key, "/keys/old")

    def test_existing_vm_storage_drift_is_live_verifiable(self) -> None:
        cached = _config(
            hosted_node="10.0.0.10",
            hosted_bridge="vmbr0",
            container_storage=[
                ["root", "local-lvm", "32G"],
                ["data", "bulk", "64G"],
                ["template", "local"],
            ],
        )
        updated = _config(
            hosted_node="10.0.0.10",
            hosted_bridge="vmbr1",
            container_storage=[
                ["root", "fast-zfs", "64G"],
                ["data", "archive", "128G"],
                ["template", "fast-zfs"],
            ],
        )
        args = _args(
            hosted_node="10.0.0.10",
            hosted_bridge="vmbr1",
            container_storage=updated.container_storage,
        )

        self.assertEqual(
            basaltwater._unsupported_cached_provisioning_changes(
                updated,
                cached,
                args,
            ),
            ["--bridge"],
        )

        updated.hosted_bridge = cached.hosted_bridge
        args.hosted_bridge = None
        self.assertEqual(
            basaltwater._unsupported_cached_provisioning_changes(
                updated,
                cached,
                args,
            ),
            [],
        )

        updated.container_storage = [["root", "fast-zfs", "64G"]]
        args.container_storage = updated.container_storage
        self.assertEqual(
            basaltwater._unsupported_cached_provisioning_changes(
                updated,
                cached,
                args,
            ),
            ["--storage"],
        )

    def test_existing_vm_disk_set_change_stops_before_cache_update(self) -> None:
        current = _config(
            container_storage=[
                ["root", "fast-lvm", "64G"],
                ["data", "bulk", "128G"],
            ],
        )
        cached = _config(
            container_storage=[["root", "local-lvm", "32G"]],
        )
        args = _args(
            container_storage=current.container_storage,
        )

        with (
            patch("basaltwater.prompt_for_missing_passwords"),
            patch("basaltwater.SetupConfig.from_args", return_value=current),
            patch("basaltwater.load_setup_command", return_value=cached),
            patch("basaltwater.save_setup_command") as mock_save,
            patch("builtins.print") as mock_print,
        ):
            result = basaltwater.run_setup_command(args)

        self.assertEqual(result, 1)
        mock_save.assert_not_called()
        self.assertIn(
            "--storage",
            "\n".join(str(call.args[0]) for call in mock_print.call_args_list),
        )

    def test_mounted_disk_addition_merges_saved_vm_storage(self) -> None:
        current = _config(
            container_storage=[
                ["syncthing-data", "bulk-lvm", "512G"],
            ],
            storage_mounts=[
                ["syncthing-data", "/srv/syncthing", "ext4", "empty"],
            ],
            vm_disk_settings=[["syncthing-data", "backup=on"]],
        )
        cached = _config(
            container_storage=[
                ["root", "local-lvm", "32G"],
                ["agent-data", "bulk-lvm", "128G"],
            ],
            storage_mounts=[["agent-data", "/srv/agent-workspace"]],
            vm_disk_settings=[["agent-data", "ssd=on"]],
        )
        args = _args(
            container_storage=current.container_storage,
            storage_mounts=current.storage_mounts,
            vm_disk_settings=current.vm_disk_settings,
        )

        additions = basaltwater._merge_additive_vm_storage(
            current,
            cached,
            args,
        )

        self.assertEqual(additions, {"syncthing-data"})
        self.assertEqual(
            current.container_storage,
            [
                ["root", "local-lvm", "32G"],
                ["agent-data", "bulk-lvm", "128G"],
                ["syncthing-data", "bulk-lvm", "512G"],
            ],
        )
        self.assertEqual(
            current.storage_mounts,
            [
                ["agent-data", "/srv/agent-workspace"],
                ["syncthing-data", "/srv/syncthing", "ext4", "empty"],
            ],
        )
        self.assertEqual(
            current.vm_disk_settings,
            [
                ["agent-data", "ssd=on"],
                ["syncthing-data", "backup=on"],
            ],
        )
        self.assertEqual(
            basaltwater._unsupported_cached_provisioning_changes(
                current,
                cached,
                args,
                additive_storage_request=additions is not None,
            ),
            [],
        )

    def test_repeated_concise_storage_addition_is_an_idempotent_subset(
        self,
    ) -> None:
        cached = _config(
            container_storage=[
                ["root", "local-lvm", "32G"],
                ["team-data", "bulk-lvm", "512G"],
            ],
            storage_mounts=[
                ["team-data", "/srv/team-files", "ext4", "empty"],
            ],
        )
        current = _config(
            container_storage=[["team-data", "bulk-lvm", "512G"]],
            storage_mounts=[
                ["team-data", "/srv/team-files", "ext4", "empty"],
            ],
        )
        args = _args(
            container_storage=current.container_storage,
            storage_mounts=current.storage_mounts,
        )

        additions = basaltwater._merge_additive_vm_storage(
            current,
            cached,
            args,
        )

        self.assertEqual(additions, set())
        self.assertEqual(current.container_storage, cached.container_storage)
        self.assertEqual(current.storage_mounts, cached.storage_mounts)
        self.assertEqual(
            basaltwater._unsupported_cached_provisioning_changes(
                current,
                cached,
                args,
                additive_storage_request=additions is not None,
            ),
            [],
        )

    def test_multiple_mounted_disks_can_be_added_without_syncthing(self) -> None:
        cached = _config(
            container_storage=[["root", "local-lvm", "32G"]],
        )
        args = _args(
            container_storage=[
                ["team-data", "bulk-lvm", "512G"],
                ["archive-data", "archive", "1T"],
            ],
            storage_mounts=[
                ["team-data", "/srv/team-files"],
                ["archive-data", "/mnt/archive", "xfs", "empty"],
            ],
        )
        current = _config(
            container_storage=args.container_storage,
            storage_mounts=args.storage_mounts,
        )

        additions = basaltwater._merge_additive_vm_storage(
            current,
            cached,
            args,
        )

        self.assertEqual(additions, {"team-data", "archive-data"})
        self.assertEqual(current.container_storage[0][0], "root")
        self.assertFalse(current.enable_syncthing)

    def test_additive_storage_rejects_changes_to_saved_declarations(self) -> None:
        cached = _config(
            container_storage=[
                ["root", "local-lvm", "32G"],
                ["existing-data", "bulk-lvm", "64G"],
            ],
            storage_mounts=[["existing-data", "/srv/existing"]],
        )
        current = _config(
            container_storage=[
                ["root", "fast-lvm", "64G"],
                ["new-data", "bulk-lvm", "128G"],
            ],
            storage_mounts=[["new-data", "/srv/new"]],
        )
        args = _args(
            container_storage=current.container_storage,
            storage_mounts=current.storage_mounts,
        )

        additions = basaltwater._merge_additive_vm_storage(
            current,
            cached,
            args,
        )

        self.assertIsNone(additions)
        self.assertEqual(
            basaltwater._unsupported_cached_provisioning_changes(
                current,
                cached,
                args,
                additive_storage_request=additions is not None,
            ),
            ["--storage", "--storage-mount"],
        )

    def test_existing_vm_storage_addition_rejects_home_mount(self) -> None:
        cached = _config(
            container_storage=[["root", "local-lvm", "32G"]],
        )
        current = _config(
            container_storage=[["home-data", "bulk-lvm", "128G"]],
            storage_mounts=[["home-data", "/home", "ext4", "empty"]],
        )
        args = _args(
            container_storage=current.container_storage,
            storage_mounts=current.storage_mounts,
        )

        self.assertIsNone(
            basaltwater._merge_additive_vm_storage(current, cached, args)
        )

    def test_reuses_metadata_when_explicit_guest_shape_matches(self) -> None:
        current = _config(
            container_memory="4G",
            container_storage=[["root", "local-lvm", "32G"]],
            container_cores=4,
            container_base="debian",
        )
        cached = _config(
            hosted_node="10.0.0.10",
            container_memory="4G",
            container_storage=[["root", "local-lvm", "32G"]],
            container_cores=4,
            container_base="debian",
        )
        args = _args(
            container_memory="4G",
            container_storage=[["root", "local-lvm", "32G"]],
            container_cores=4,
            container_base="debian",
        )

        with patch("basaltwater.load_setup_command", return_value=cached):
            reused = basaltwater._reuse_cached_provisioning_metadata(current, args)

        self.assertTrue(reused)
        self.assertEqual(current.hosted_node, "10.0.0.10")

    def test_force_provider_verification_bypasses_matching_cache(self) -> None:
        current = _config(container_cores=3)
        cached = _config(
            hosted_node="10.0.0.10",
            container_cores=3,
            static_ipv4="10.0.0.50/24",
            network_gateway4="10.0.0.1",
            network_dns=["1.1.1.1"],
        )

        with patch("basaltwater.load_setup_command", return_value=cached):
            reused = basaltwater._reuse_cached_provisioning_metadata(
                current,
                _args(container_cores=3, verify_provider=True),
            )

        self.assertFalse(reused)
        self.assertEqual(current.hosted_node, "10.0.0.10")

    def test_explicit_matching_disk_policy_bypasses_cache(self) -> None:
        for explicit_args in (
            {"vm_disk_discard": True},
            {"vm_disk_ssd": False},
            {"vm_disk_backup": True},
            {"vm_disk_settings": [["root", "ssd=on"]]},
        ):
            with self.subTest(explicit_args=explicit_args):
                current = _config(**explicit_args)
                cached = _config(
                    hosted_node="10.0.0.10",
                    static_ipv4="10.0.0.50/24",
                    network_gateway4="10.0.0.1",
                    network_dns=["1.1.1.1"],
                    **explicit_args,
                )

                reused = basaltwater._reuse_cached_provisioning_metadata(
                    current,
                    _args(**explicit_args),
                    cached,
                )

                self.assertFalse(reused)
                self.assertEqual(current.hosted_node, "10.0.0.10")

    def test_missing_local_metadata_requires_proxmox(self) -> None:
        with patch("basaltwater.load_setup_command", return_value=None):
            reused = basaltwater._reuse_cached_provisioning_metadata(
                _config(),
                _args(),
            )

        self.assertFalse(reused)

    def test_changed_vm_name_requires_proxmox_reconciliation(self) -> None:
        current = _config(friendly_name="agent-min-2")
        cached = _config(
            friendly_name="agent-min-1",
            static_ipv4="10.0.0.50/24",
            network_gateway4="10.0.0.1",
            network_dns=["1.1.1.1"],
        )

        with patch("basaltwater.load_setup_command", return_value=cached):
            reused = basaltwater._reuse_cached_provisioning_metadata(
                current,
                _args(friendly_name="agent-min-2"),
            )

        self.assertFalse(reused)
        self.assertEqual(current.hosted_node, cached.hosted_node)
        self.assertEqual(current.static_ipv4, "10.0.0.50/24")

    def test_explicit_cached_vm_identity_reuses_metadata(self) -> None:
        current = _config(friendly_name="agent-min-2")
        cached = _config(
            friendly_name="agent-min-2",
            static_ipv4="10.0.0.50/24",
            network_gateway4="10.0.0.1",
            network_dns=["1.1.1.1"],
        )

        with patch("basaltwater.load_setup_command", return_value=cached):
            reused = basaltwater._reuse_cached_provisioning_metadata(
                current,
                _args(friendly_name="agent-min-2"),
            )

        self.assertTrue(reused)

    def test_friendly_label_change_preserves_explicit_system_hostname(self) -> None:
        current = _config(friendly_name="new-label")
        cached = _config(
            friendly_name="old-label",
            system_hostname="stable-vm-name",
            static_ipv4="10.0.0.50/24",
            network_gateway4="10.0.0.1",
            network_dns=["1.1.1.1"],
        )

        with patch("basaltwater.load_setup_command", return_value=cached):
            reused = basaltwater._reuse_cached_provisioning_metadata(
                current,
                _args(friendly_name="new-label"),
            )

        self.assertTrue(reused)
        self.assertEqual(current.friendly_name, "new-label")
        self.assertEqual(current.system_hostname, "stable-vm-name")

    def test_changed_system_hostname_requires_proxmox_reconciliation(self) -> None:
        current = _config(system_hostname="new-host")
        cached = _config(
            system_hostname="old-host",
            static_ipv4="10.0.0.50/24",
            network_gateway4="10.0.0.1",
            network_dns=["1.1.1.1"],
        )

        with patch("basaltwater.load_setup_command", return_value=cached):
            reused = basaltwater._reuse_cached_provisioning_metadata(
                current,
                _args(system_hostname="new-host"),
            )

        self.assertFalse(reused)

    def test_omitted_names_are_preserved_from_cached_metadata(self) -> None:
        current = _config()
        cached = _config(
            friendly_name="agent-min-1",
            system_hostname="agent-host",
            static_ipv4="10.0.0.50/24",
            network_gateway4="10.0.0.1",
            network_dns=["1.1.1.1"],
        )

        with patch("basaltwater.load_setup_command", return_value=cached):
            reused = basaltwater._reuse_cached_provisioning_metadata(
                current,
                _args(),
            )

        self.assertTrue(reused)
        self.assertEqual(current.friendly_name, "agent-min-1")
        self.assertEqual(current.system_hostname, "agent-host")

    def test_legacy_cache_without_network_defaults_requires_refresh(self) -> None:
        cached = _config(static_ipv4="10.0.0.50/24")

        with patch("basaltwater.load_setup_command", return_value=cached):
            reused = basaltwater._reuse_cached_provisioning_metadata(
                _config(),
                _args(),
            )

        self.assertFalse(reused)

    def test_cidr_target_looks_up_metadata_by_guest_address(self) -> None:
        current = _config(host="10.0.0.50/20")
        cached = _config(
            static_ipv4="10.0.0.50/20",
            network_gateway4="10.0.0.1",
            network_dns=["1.1.1.1"],
        )

        with patch(
            "basaltwater.load_setup_command",
            return_value=cached,
        ) as mock_load:
            reused = basaltwater._reuse_cached_provisioning_metadata(current, _args())

        self.assertTrue(reused)
        mock_load.assert_called_once_with("10.0.0.50")

    def test_saved_guest_identity_is_independent_of_provider_node(self) -> None:
        cached = _config(
            host="10.0.0.50",
            hosted_node="10.0.0.10",
            machine_type="vm",
        )

        self.assertTrue(
            basaltwater._is_cached_provisioned_guest_identity(
                _config(
                    host="10.0.0.50/24",
                    hosted_node="10.0.0.10",
                    machine_type="vm",
                ),
                cached,
            )
        )
        self.assertFalse(
            basaltwater._is_cached_provisioned_guest_identity(
                _config(host="10.0.0.51", hosted_node="10.0.0.10"),
                cached,
            )
        )
        self.assertTrue(
            basaltwater._is_cached_provisioned_guest_identity(
                _config(host="10.0.0.50", hosted_node="10.0.0.11"),
                cached,
            )
        )
        self.assertFalse(
            basaltwater._is_cached_provisioned_guest_identity(
                _config(
                    host="10.0.0.50",
                    hosted_node="10.0.0.10",
                    machine_type="unprivileged",
                ),
                cached,
            )
        )

    @patch("lib.proxmox_vm.provision_vm")
    @patch("basaltwater.register_proxmox_setup_host")
    @patch("basaltwater.save_setup_command")
    @patch("basaltwater.store_cli_credentials")
    @patch("basaltwater.print_setup_summary")
    @patch("basaltwater.run_remote_setup", return_value=0)
    @patch("basaltwater.validate_host", return_value=True)
    @patch("basaltwater.validate_username", return_value=True)
    def test_setup_refreshes_cached_guest_host_key_without_shape_changes(
        self,
        _mock_username,
        _mock_host,
        _mock_remote,
        _mock_summary,
        _mock_credentials,
        _mock_save,
        _mock_register,
        mock_provision,
    ) -> None:
        current = _config(
            friendly_name="agent-min-1",
            system_hostname="agent-host",
        )
        cached = _config(
            hosted_node="10.0.0.10",
            friendly_name="agent-min-1",
            system_hostname="agent-host",
            container_memory="4G",
            container_storage=[["root", "local-lvm", "32G"]],
            static_ipv4="10.0.0.50/24",
            network_gateway4="10.0.0.1",
            network_dns=["1.1.1.1"],
        )
        args = _args(
            friendly_name="agent-min-1",
            system_hostname="agent-host",
        )

        with patch("basaltwater.SetupConfig.from_args", return_value=current), \
             patch("basaltwater.load_setup_command", return_value=cached), \
             patch(
                 "basaltwater._prepare_runtime_config_for_cli",
                 side_effect=lambda config: config,
             ) as mock_prepare, \
             patch(
                 "basaltwater.refresh_managed_guest_host_keys",
             ) as mock_refresh:
            with patch("basaltwater.ensure_guest_ipv4_route") as mock_route:
                result = basaltwater.run_setup_command(args)

        self.assertEqual(result, 0)
        mock_provision.assert_not_called()
        mock_refresh.assert_called_once_with(
            "10.0.0.50",
            "10.0.0.10",
            "root",
            None,
            dry_run=False,
        )
        mock_prepare.assert_called_once_with(current)
        mock_route.assert_called_once_with(
            "10.0.0.50/24",
            "10.0.0.1",
            "root",
            current.ssh_key,
            dry_run=False,
        )

    @patch("lib.proxmox_vm.provision_vm")
    @patch("basaltwater.register_proxmox_setup_host")
    @patch("basaltwater.save_setup_command")
    @patch("basaltwater.store_cli_credentials")
    @patch("basaltwater.print_setup_summary")
    @patch("basaltwater.run_remote_setup", return_value=0)
    @patch("basaltwater.validate_host", return_value=True)
    @patch("basaltwater.validate_username", return_value=True)
    def test_nopasswd_still_uses_root_to_prepare_guest_network(
        self,
        _mock_username,
        _mock_host,
        _mock_remote,
        _mock_summary,
        _mock_credentials,
        _mock_save,
        _mock_register,
        mock_provision,
    ) -> None:
        current = _config(
            nopasswd=True,
            friendly_name="agent-min-1",
            system_hostname="agent-host",
        )
        cached = _config(
            nopasswd=True,
            hosted_node="10.0.0.10",
            friendly_name="agent-min-1",
            system_hostname="agent-host",
            container_memory="4G",
            container_storage=[["root", "local-lvm", "32G"]],
            static_ipv4="10.0.0.50/24",
            network_gateway4="10.0.0.1",
            network_dns=["1.1.1.1"],
        )

        with patch("basaltwater.SetupConfig.from_args", return_value=current), \
             patch("basaltwater.load_setup_command", return_value=cached), \
             patch(
                 "basaltwater._prepare_runtime_config_for_cli",
                 side_effect=lambda config: config,
             ), \
             patch("basaltwater.refresh_managed_guest_host_keys"), \
             patch("basaltwater.ensure_guest_ipv4_route") as mock_route:
            result = basaltwater.run_setup_command(
                _args(friendly_name="agent-min-1", system_hostname="agent-host")
            )

        self.assertEqual(result, 0)
        mock_provision.assert_not_called()
        mock_route.assert_called_once_with(
            "10.0.0.50/24",
            "10.0.0.1",
            "root",
            current.ssh_key,
            dry_run=False,
        )

    def test_cached_guest_host_key_refresh_failure_stops_setup(self) -> None:
        current = _config()
        cached = _config(
            hosted_node="10.0.0.10",
            container_memory="4G",
            container_storage=[["root", "local-lvm", "32G"]],
            static_ipv4="10.0.0.50/24",
            network_gateway4="10.0.0.1",
            network_dns=["1.1.1.1"],
        )

        with patch("basaltwater.prompt_for_missing_passwords"), \
             patch("basaltwater.SetupConfig.from_args", return_value=current), \
             patch("basaltwater.load_setup_command", return_value=cached), \
             patch(
                 "basaltwater._prepare_runtime_config_for_cli",
                 side_effect=lambda config: config,
             ), \
             patch("basaltwater.validate_host", return_value=True), \
             patch("basaltwater.validate_username", return_value=True), \
             patch("basaltwater.print_setup_summary"), \
             patch(
                 "basaltwater.refresh_managed_guest_host_keys",
                 side_effect=basaltwater.ProvisionError("scan failed"),
             ), \
             patch("basaltwater.run_remote_setup") as mock_remote:
            result = basaltwater.run_setup_command(_args())

        self.assertEqual(result, 1)
        mock_remote.assert_not_called()

    @patch("lib.proxmox_vm.provision_vm")
    @patch("basaltwater.register_proxmox_setup_host")
    @patch("basaltwater.save_setup_command")
    @patch("basaltwater.store_cli_credentials")
    @patch("basaltwater.print_setup_summary")
    @patch("basaltwater.run_remote_setup", return_value=0)
    @patch("basaltwater.validate_host", return_value=True)
    @patch("basaltwater.validate_username", return_value=True)
    def test_home_storage_uses_root_for_initial_network_repair(
        self,
        _mock_username,
        _mock_host,
        _mock_remote,
        _mock_summary,
        _mock_credentials,
        _mock_save,
        _mock_register,
        _mock_provision,
    ) -> None:
        current = _config(
            hosted_node="10.0.0.10",
            container_memory="4G",
            container_storage=[
                ["root", "local-lvm", "32G"],
                ["home-data", "local-lvm", "32G"],
            ],
            storage_mounts=[["home-data", "/home"]],
            static_ipv4="10.0.0.50/24",
            network_gateway4="10.0.0.1",
            network_dns=["1.1.1.1"],
        )

        with patch("basaltwater.SetupConfig.from_args", return_value=current), \
             patch("basaltwater.load_setup_command", return_value=None), \
             patch(
                 "basaltwater._prepare_runtime_config_for_cli",
                 side_effect=lambda config: config,
             ), \
             patch("basaltwater.ensure_guest_ipv4_route") as mock_route:
            result = basaltwater.run_setup_command(_args())

        self.assertEqual(result, 0)
        mock_route.assert_called_once_with(
            "10.0.0.50/24",
            "10.0.0.1",
            "root",
            current.ssh_key,
            dry_run=False,
        )

    def test_existing_cached_vm_refreshes_host_key_after_provisioning_check(
        self,
    ) -> None:
        from lib.proxmox_vm import VMAlreadyExists

        current = _config(
            hosted_node="10.0.0.10",
            hosted_user="root",
            hosted_key="/keys/proxmox",
            container_memory="8G",
            container_storage=[
                ["root", "local-lvm", "32G"],
                ["agent-data", "bulk-lvm", "128G"],
            ],
            storage_mounts=[["agent-data", "/srv/agent-workspace"]],
            static_ipv4="10.0.0.50/24",
            network_gateway4="10.0.0.1",
            network_dns=["1.1.1.1"],
        )
        cached = _config(
            hosted_node="10.0.0.10",
            hosted_user="root",
            hosted_key="/keys/proxmox",
            container_memory="4G",
            container_storage=[
                ["root", "local-lvm", "32G"],
                ["agent-data", "bulk-lvm", "128G"],
            ],
            storage_mounts=[["agent-data", "/srv/agent-workspace"]],
            static_ipv4="10.0.0.50/24",
            network_gateway4="10.0.0.1",
            network_dns=["1.1.1.1"],
        )
        args = _args(container_memory="8G")

        with patch("basaltwater.SetupConfig.from_args", return_value=current), \
             patch("basaltwater.load_setup_command", return_value=cached), \
             patch(
                 "basaltwater._prepare_runtime_config_for_cli",
                 side_effect=lambda config: config,
             ), \
             patch("basaltwater.validate_host", return_value=True), \
             patch("basaltwater.validate_username", return_value=True), \
             patch("basaltwater.print_setup_summary"), \
             patch("basaltwater.store_cli_credentials"), \
             patch("basaltwater.save_setup_command"), \
             patch("basaltwater.register_proxmox_setup_host"), \
             patch("basaltwater.run_remote_setup", return_value=0), \
             patch("basaltwater.ensure_guest_ipv4_route"), \
             patch(
                 "lib.proxmox_vm.provision_vm",
                 side_effect=VMAlreadyExists(),
             ) as mock_provision, \
             patch(
                 "basaltwater.refresh_managed_guest_host_keys",
             ) as mock_refresh:
            result = basaltwater.run_setup_command(args)

        self.assertEqual(result, 0)
        mock_provision.assert_called_once_with(
            current,
            image=current.vm_image,
            allow_existing_data_disks=True,
            require_existing_vm=True,
        )
        mock_refresh.assert_called_once_with(
            "10.0.0.50",
            "10.0.0.10",
            "root",
            "/keys/proxmox",
            dry_run=False,
        )

    def test_existing_vm_storage_drift_is_verified_before_cache_update(
        self,
    ) -> None:
        from lib.proxmox_vm import VMAlreadyExists

        current = _config(
            hosted_node="10.0.0.10",
            hosted_user="root",
            hosted_key="/keys/proxmox",
            container_memory="4G",
            container_storage=[["root", "fast-lvm", "64G"]],
            static_ipv4="10.0.0.50/24",
            network_gateway4="10.0.0.1",
            network_dns=["1.1.1.1"],
        )
        cached = _config(
            hosted_node="10.0.0.10",
            hosted_user="root",
            hosted_key="/keys/proxmox",
            container_memory="4G",
            container_storage=[["root", "local-lvm", "32G"]],
            static_ipv4="10.0.0.50/24",
            network_gateway4="10.0.0.1",
            network_dns=["1.1.1.1"],
        )
        args = _args(container_storage=current.container_storage)

        with patch("basaltwater.SetupConfig.from_args", return_value=current), \
             patch("basaltwater.load_setup_command", return_value=cached), \
             patch(
                 "basaltwater._prepare_runtime_config_for_cli",
                 side_effect=lambda config: config,
             ), \
             patch("basaltwater.validate_host", return_value=True), \
             patch("basaltwater.validate_username", return_value=True), \
             patch("basaltwater.print_setup_summary"), \
             patch("basaltwater.store_cli_credentials"), \
             patch("basaltwater.save_setup_command") as mock_save, \
             patch("basaltwater.register_proxmox_setup_host"), \
             patch("basaltwater.run_remote_setup", return_value=0), \
             patch("basaltwater.ensure_guest_ipv4_route"), \
             patch(
                 "lib.proxmox_vm.provision_vm",
                 side_effect=VMAlreadyExists(),
             ) as mock_provision, \
             patch("basaltwater.refresh_managed_guest_host_keys"):
            result = basaltwater.run_setup_command(args)

        self.assertEqual(result, 0)
        mock_provision.assert_called_once_with(
            current,
            image=current.vm_image,
            allow_existing_data_disks=True,
            require_existing_vm=True,
            verify_existing_storage=True,
        )
        self.assertTrue(mock_save.called)
        self.assertEqual(
            mock_save.call_args.args[0].container_storage,
            [["root", "fast-lvm", "64G"]],
        )

    def test_existing_vm_can_add_declared_mounted_storage(self) -> None:
        from lib.proxmox_vm import VMAlreadyExists

        current = _config(
            hosted_node="10.0.0.10",
            hosted_user="root",
            hosted_key="/keys/proxmox",
            container_storage=[
                ["team-data", "bulk-lvm", "512G"],
            ],
            storage_mounts=[
                ["team-data", "/srv/team-files", "ext4", "empty"],
            ],
        )
        cached = _config(
            hosted_node="10.0.0.10",
            hosted_user="root",
            hosted_key="/keys/proxmox",
            container_memory="4G",
            container_storage=[["root", "local-lvm", "32G"]],
            static_ipv4="10.0.0.50/24",
            network_gateway4="10.0.0.1",
            network_dns=["1.1.1.1"],
        )
        args = _args(
            container_storage=current.container_storage,
            storage_mounts=current.storage_mounts,
        )

        with patch("basaltwater.SetupConfig.from_args", return_value=current), \
             patch("basaltwater.load_setup_command", return_value=cached), \
             patch(
                 "basaltwater._prepare_runtime_config_for_cli",
                 side_effect=lambda config: config,
             ), \
             patch("basaltwater.validate_host", return_value=True), \
             patch("basaltwater.validate_username", return_value=True), \
             patch("basaltwater.print_setup_summary"), \
             patch("basaltwater.store_cli_credentials"), \
             patch("basaltwater.save_setup_command") as mock_save, \
             patch("basaltwater.register_proxmox_setup_host"), \
             patch("basaltwater.run_remote_setup", return_value=0), \
             patch("basaltwater.ensure_guest_ipv4_route"), \
             patch(
                 "lib.proxmox_vm.provision_vm",
                 side_effect=VMAlreadyExists(),
             ) as mock_provision, \
             patch("basaltwater.refresh_managed_guest_host_keys"):
            result = basaltwater.run_setup_command(args)

        self.assertEqual(result, 0)
        self.assertEqual(
            current.container_storage,
            [
                ["root", "local-lvm", "32G"],
                ["team-data", "bulk-lvm", "512G"],
            ],
        )
        mock_provision.assert_called_once_with(
            current,
            image=current.vm_image,
            allow_existing_data_disks=True,
            require_existing_vm=True,
            verify_existing_storage=True,
            attach_missing_data_disks={"team-data"},
        )
        self.assertTrue(mock_save.called)
        self.assertEqual(
            mock_save.call_args.args[0].storage_mounts,
            [
                ["team-data", "/srv/team-files", "ext4", "empty"],
            ],
        )

    def test_failed_additive_mount_keeps_previous_cached_layout(self) -> None:
        from lib.proxmox_vm import VMAlreadyExists

        current = _config(
            container_storage=[["team-data", "bulk-lvm", "512G"]],
            storage_mounts=[["team-data", "/srv/team-files"]],
        )
        cached = _config(
            container_memory="4G",
            container_storage=[["root", "local-lvm", "32G"]],
            static_ipv4="10.0.0.50/24",
            network_gateway4="10.0.0.1",
            network_dns=["1.1.1.1"],
        )
        args = _args(
            container_storage=current.container_storage,
            storage_mounts=current.storage_mounts,
        )

        with patch("basaltwater.SetupConfig.from_args", return_value=current), \
             patch("basaltwater.load_setup_command", return_value=cached), \
             patch(
                 "basaltwater._prepare_runtime_config_for_cli",
                 side_effect=lambda config: config,
             ), \
             patch("basaltwater.validate_host", return_value=True), \
             patch("basaltwater.validate_username", return_value=True), \
             patch("basaltwater.print_setup_summary"), \
             patch("basaltwater.store_cli_credentials"), \
             patch("basaltwater.save_setup_command") as mock_save, \
             patch("basaltwater.register_proxmox_setup_host"), \
             patch("basaltwater.run_remote_setup", return_value=1), \
             patch("basaltwater.ensure_guest_ipv4_route"), \
             patch(
                 "lib.proxmox_vm.provision_vm",
                 side_effect=VMAlreadyExists(),
             ), \
             patch("basaltwater.refresh_managed_guest_host_keys"):
            result = basaltwater.run_setup_command(args)

        self.assertEqual(result, 1)
        mock_save.assert_not_called()

    def test_repeated_concise_storage_command_skips_provider_mutation(
        self,
    ) -> None:
        current = _config(
            container_storage=[["team-data", "bulk-lvm", "512G"]],
            storage_mounts=[["team-data", "/srv/team-files"]],
        )
        cached = _config(
            container_memory="4G",
            container_storage=[
                ["root", "local-lvm", "32G"],
                ["team-data", "bulk-lvm", "512G"],
            ],
            storage_mounts=[["team-data", "/srv/team-files"]],
            static_ipv4="10.0.0.50/24",
            network_gateway4="10.0.0.1",
            network_dns=["1.1.1.1"],
        )
        args = _args(
            container_storage=current.container_storage,
            storage_mounts=current.storage_mounts,
        )

        with patch("basaltwater.prompt_for_missing_passwords"), \
             patch("basaltwater.SetupConfig.from_args", return_value=current), \
             patch("basaltwater.load_setup_command", return_value=cached), \
             patch(
                 "basaltwater._prepare_runtime_config_for_cli",
                 side_effect=lambda config: config,
             ), \
             patch("basaltwater.validate_host", return_value=True), \
             patch("basaltwater.validate_username", return_value=True), \
             patch("basaltwater.print_setup_summary"), \
             patch("basaltwater.store_cli_credentials"), \
             patch("basaltwater.save_setup_command"), \
             patch("basaltwater.register_proxmox_setup_host"), \
             patch("basaltwater.run_remote_setup", return_value=0), \
             patch("basaltwater.ensure_guest_ipv4_route"), \
             patch("basaltwater.refresh_managed_guest_host_keys"), \
             patch("lib.proxmox_vm.provision_vm") as mock_provision:
            result = basaltwater.run_setup_command(args)

        self.assertEqual(result, 0)
        mock_provision.assert_not_called()
        self.assertEqual(current.container_storage, cached.container_storage)
        self.assertEqual(current.storage_mounts, cached.storage_mounts)

    def test_migrated_vm_is_verified_and_rebound_to_explicit_destination(
        self,
    ) -> None:
        from lib.proxmox_vm import VMAlreadyExists

        current = _config(
            hosted_node="10.0.0.11",
            hosted_user="root",
            hosted_key="/keys/pve2",
            hosted_bridge="vmbr1",
            container_memory="4G",
            container_storage=[
                ["root", "fast-zfs", "32G"],
                ["agent-data", "archive", "128G"],
            ],
            storage_mounts=[["agent-data", "/srv/agent-workspace"]],
            static_ipv4="10.0.0.50/24",
            network_gateway4="10.0.0.1",
            network_dns=["1.1.1.1"],
        )
        cached = _config(
            hosted_node="10.0.0.10",
            hosted_user="root",
            hosted_key="/keys/pve1",
            hosted_bridge="vmbr0",
            container_memory="4G",
            container_storage=[
                ["root", "local-lvm", "32G"],
                ["agent-data", "bulk", "128G"],
            ],
            storage_mounts=[["agent-data", "/srv/agent-workspace"]],
            static_ipv4="10.0.0.50/24",
            network_gateway4="10.0.0.1",
            network_dns=["1.1.1.1"],
        )
        args = _args(
            hosted_node="10.0.0.11",
            hosted_key="/keys/pve2",
            hosted_bridge="vmbr1",
            container_storage=current.container_storage,
        )

        with patch("basaltwater.SetupConfig.from_args", return_value=current), \
             patch("basaltwater.load_setup_command", return_value=cached), \
             patch(
                 "basaltwater._prepare_runtime_config_for_cli",
                 side_effect=lambda config: config,
             ), \
             patch("basaltwater.validate_host", return_value=True), \
             patch("basaltwater.validate_username", return_value=True), \
             patch("basaltwater.print_setup_summary"), \
             patch("basaltwater.store_cli_credentials"), \
             patch("basaltwater.save_setup_command") as mock_save, \
             patch("basaltwater.register_proxmox_setup_host"), \
             patch("basaltwater.run_remote_setup", return_value=0), \
             patch("basaltwater.ensure_guest_ipv4_route"), \
             patch(
                 "lib.proxmox_vm.verify_vm_rebind_source_stopped",
             ) as mock_source, \
             patch(
                 "lib.proxmox_vm.provision_vm",
                 side_effect=VMAlreadyExists(),
             ) as mock_provision, \
             patch(
                 "basaltwater.refresh_managed_guest_host_keys",
             ) as mock_refresh:
            result = basaltwater.run_setup_command(args)

        self.assertEqual(result, 0)
        mock_source.assert_called_once_with(cached, dry_run=False)
        mock_provision.assert_called_once_with(
            current,
            image=current.vm_image,
            allow_existing_data_disks=True,
            require_existing_vm=True,
            require_existing_name=True,
            start_existing_vm=True,
            verify_existing_bridge=True,
            verify_existing_storage=True,
        )
        mock_refresh.assert_called_once_with(
            "10.0.0.50",
            "10.0.0.11",
            "root",
            "/keys/pve2",
            dry_run=False,
        )
        mock_save.assert_called_once()
        self.assertEqual(mock_save.call_args.args[0].hosted_node, "10.0.0.11")

    def test_failed_provider_rebind_preserves_saved_binding(self) -> None:
        current = _config(
            hosted_node="10.0.0.11",
            container_memory="4G",
            container_storage=[["root", "fast-zfs", "32G"]],
            static_ipv4="10.0.0.50/24",
            network_gateway4="10.0.0.1",
            network_dns=["1.1.1.1"],
        )
        cached = _config(
            hosted_node="10.0.0.10",
            container_memory="4G",
            container_storage=[["root", "local-lvm", "32G"]],
            static_ipv4="10.0.0.50/24",
            network_gateway4="10.0.0.1",
            network_dns=["1.1.1.1"],
        )
        args = _args(
            hosted_node="10.0.0.11",
            container_storage=current.container_storage,
        )

        with patch("basaltwater.SetupConfig.from_args", return_value=current), \
             patch("basaltwater.load_setup_command", return_value=cached), \
             patch(
                 "basaltwater._prepare_runtime_config_for_cli",
                 side_effect=lambda config: config,
             ), \
             patch("basaltwater.validate_host", return_value=True), \
             patch("basaltwater.validate_username", return_value=True), \
             patch("basaltwater.print_setup_summary"), \
             patch("basaltwater.save_setup_command") as mock_save, \
             patch(
                 "lib.proxmox_vm.verify_vm_rebind_source_stopped",
                 side_effect=basaltwater.ProvisionError("source is running"),
             ), \
             patch("builtins.print"):
            result = basaltwater.run_setup_command(args)

        self.assertEqual(result, 1)
        mock_save.assert_not_called()

    def test_existing_unsaved_vm_does_not_refresh_host_key(self) -> None:
        from lib.proxmox_vm import VMAlreadyExists

        current = _config(
            hosted_node="10.0.0.10",
            container_memory="4G",
            container_storage=[["root", "local-lvm", "32G"]],
            static_ipv4="10.0.0.50/24",
            network_gateway4="10.0.0.1",
            network_dns=["1.1.1.1"],
        )

        with patch("basaltwater.SetupConfig.from_args", return_value=current), \
             patch("basaltwater.load_setup_command", return_value=None), \
             patch(
                 "basaltwater._prepare_runtime_config_for_cli",
                 side_effect=lambda config: config,
             ), \
             patch("basaltwater.validate_host", return_value=True), \
             patch("basaltwater.validate_username", return_value=True), \
             patch("basaltwater.print_setup_summary"), \
             patch("basaltwater.store_cli_credentials"), \
             patch("basaltwater.save_setup_command"), \
             patch("basaltwater.register_proxmox_setup_host"), \
             patch("basaltwater.run_remote_setup", return_value=0), \
             patch("basaltwater.ensure_guest_ipv4_route"), \
             patch(
                 "lib.proxmox_vm.provision_vm",
                 side_effect=VMAlreadyExists(),
             ), \
             patch(
                 "basaltwater.refresh_managed_guest_host_keys",
             ) as mock_refresh:
            result = basaltwater.run_setup_command(_args())

        self.assertEqual(result, 0)
        mock_refresh.assert_not_called()


if __name__ == "__main__":
    unittest.main()
