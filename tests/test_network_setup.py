"""Tests for setup-time hostname and static network configuration."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from common import network_steps
from basaltwater import create_basaltwater_parser, run_setup_command
from lib.config import SetupConfig
from lib.validation import validate_network_setup_settings, validate_system_hostname


def _config(**overrides: object) -> SetupConfig:
    values: dict[str, object] = {
        "host": "192.168.10.20",
        "username": "admin",
        "system_type": "server_lite",
    }
    values.update(overrides)
    return SetupConfig(**values)  # type: ignore[arg-type]


def _transition_payload(
    network_path: str,
    cloud_path: str,
    *,
    state: str = "prepared",
) -> dict[str, object]:
    return {
        "version": 2,
        "transition_id": "a" * 64,
        "state": state,
        "backend": "networkd",
        "interface": "eth0",
        "static_ipv4": "192.168.10.21/24",
        "static_ipv6": None,
        "gateway4": "192.168.10.1",
        "gateway6": None,
        "dns": ["1.1.1.1"],
        "added_addresses": ["192.168.10.21/24"],
        "policy_tables": {"4": 20000},
        "rollback": {
            "kind": "networkd",
            "files": [
                {"path": network_path, "kind": "absent"},
                {"path": cloud_path, "kind": "absent"},
            ],
        },
    }


class TestNetworkSetupCLI(unittest.TestCase):
    def test_parses_and_serializes_network_and_hostname_options(self):
        parser, _setup_parser, _patch_parser = create_basaltwater_parser()
        args = parser.parse_args(
            [
                "setup",
                "server_lite",
                "192.168.10.20",
                "admin",
                "--hostname",
                "app-01.example.test",
                "--ip",
                "192.168.10.20/24",
                "--ipv6",
                "2001:db8:10::20/64",
                "--gateway",
                "192.168.10.1",
                "--gateway6",
                "2001:db8:10::1",
                "--dns",
                "1.1.1.1",
                "--dns",
                "2606:4700:4700::1111",
                "--network-interface",
                "enp1s0",
            ]
        )
        config = SetupConfig.from_args(args, args.system_type)
        validate_network_setup_settings(config)

        self.assertEqual(config.system_hostname, "app-01.example.test")
        self.assertEqual(config.static_ipv4, "192.168.10.20/24")
        self.assertEqual(config.static_ipv6, "2001:db8:10::20/64")
        self.assertEqual(config.network_dns, ["1.1.1.1", "2606:4700:4700::1111"])
        self.assertIn("--hostname app-01.example.test", config.to_remote_args())
        self.assertIn("--ip 192.168.10.20/24", config.to_setup_command())
        self.assertEqual(config.to_dict()["network_interface"], "enp1s0")

    def test_parses_and_serializes_live_network_activation(self):
        parser, _setup_parser, _patch_parser = create_basaltwater_parser()
        args = parser.parse_args(
            [
                "patch",
                "192.168.10.20",
                "admin",
                "--ip",
                "192.168.10.21/24",
                "--activate-network",
            ]
        )
        config = SetupConfig.from_args(args, "server_lite")

        self.assertTrue(config.activate_network)
        self.assertIn("--activate-network", config.to_remote_args())
        self.assertIn("--activate-network", config.to_setup_command())
        self.assertNotIn("activate_network", config.to_dict())
        reloaded = SetupConfig.from_dict(config.host, config.system_type, config.to_dict())
        self.assertFalse(reloaded.activate_network)
        legacy_data = config.to_dict()
        legacy_data["activate_network"] = True
        legacy = SetupConfig.from_dict(config.host, config.system_type, legacy_data)
        self.assertFalse(legacy.activate_network)

    def test_initial_provisioned_setup_rejects_live_handoff(self):
        parser, _setup_parser, _patch_parser = create_basaltwater_parser()
        args = parser.parse_args(
            [
                "setup",
                "server_lite",
                "192.168.10.20",
                "admin",
                "--provision-on",
                "192.168.10.2",
                "--activate-network",
            ]
        )

        with patch("basaltwater._prepare_runtime_config_for_cli") as mock_prepare:
            self.assertEqual(run_setup_command(args), 1)

        mock_prepare.assert_not_called()

    def test_provisioned_setup_rejects_duplicate_ipv4_option(self):
        parser, _setup_parser, _patch_parser = create_basaltwater_parser()
        args = parser.parse_args(
            [
                "setup",
                "server_lite",
                "192.168.10.20",
                "admin",
                "--provision-on",
                "192.168.10.2",
                "--ip",
                "192.168.10.20/24",
            ]
        )

        with patch("basaltwater._prepare_runtime_config_for_cli") as mock_prepare:
            self.assertEqual(run_setup_command(args), 1)

        mock_prepare.assert_not_called()


class TestNetworkSetupValidation(unittest.TestCase):
    def test_accepts_dual_stack_settings(self):
        validate_network_setup_settings(
            _config(
                system_hostname="app-01",
                static_ipv4="192.168.10.20/24",
                static_ipv6="2001:db8:10::20/64",
                network_gateway4="192.168.10.1",
                network_gateway6="fe80::1",
                network_dns=["192.168.10.53", "2001:4860:4860::8888"],
                network_interface="eth0",
            )
        )

    def test_requires_prefix_and_address_dependencies(self):
        with self.assertRaisesRegex(ValueError, "CIDR notation"):
            validate_network_setup_settings(_config(static_ipv4="192.168.10.20"))
        with self.assertRaisesRegex(ValueError, "requires --ip"):
            validate_network_setup_settings(_config(network_gateway4="192.168.10.1"))
        with self.assertRaisesRegex(ValueError, "requires --ip or --ipv6"):
            validate_network_setup_settings(_config(network_dns=["1.1.1.1"]))

    def test_rejects_wrong_families_duplicates_and_unsafe_hostnames(self):
        with self.assertRaisesRegex(ValueError, "requires an IPv4"):
            validate_network_setup_settings(_config(static_ipv4="2001:db8::2/64"))
        with self.assertRaisesRegex(ValueError, "Duplicate DNS"):
            validate_network_setup_settings(
                _config(static_ipv4="192.168.10.20/24", network_dns=["1.1.1.1", "1.1.1.1"])
            )
        with self.assertRaises(ValueError):
            validate_system_hostname("bad_name")
        with self.assertRaises(ValueError):
            validate_system_hostname("a" * 64)

    def test_rejects_generic_proxmox_node_network_changes(self):
        with self.assertRaisesRegex(ValueError, "not supported for server_proxmox"):
            validate_network_setup_settings(
                _config(system_type="server_proxmox", system_hostname="pve-new")
            )

    def test_hosted_literal_host_must_match_static_address(self):
        with self.assertRaisesRegex(ValueError, "must match"):
            validate_network_setup_settings(
                _config(
                    hosted_node="192.168.10.2",
                    static_ipv4="192.168.10.21/24",
                )
            )

    def test_live_activation_allows_existing_hosted_guest_address_change(self):
        validate_network_setup_settings(
            _config(
                hosted_node="192.168.10.2",
                static_ipv4="192.168.10.21/24",
                activate_network=True,
            )
        )

    def test_live_activation_requires_an_address(self):
        with self.assertRaisesRegex(ValueError, "requires --ip or --ipv6"):
            validate_network_setup_settings(_config(activate_network=True))

    def test_live_activation_requires_an_external_controller(self):
        with self.assertRaisesRegex(ValueError, "separate controller"):
            validate_network_setup_settings(
                _config(
                    host="127.0.0.1",
                    static_ipv4="192.168.10.21/24",
                    activate_network=True,
                )
            )

    def test_gateway_must_be_reachable_on_the_configured_link(self):
        with self.assertRaisesRegex(ValueError, "another address"):
            validate_network_setup_settings(
                _config(
                    static_ipv4="192.168.10.21/24",
                    network_gateway4="192.168.11.1",
                )
            )


class TestNetworkConfigRendering(unittest.TestCase):
    @patch("common.network_steps.shutil.which", return_value="/usr/bin/nmcli")
    def test_command_available_uses_executable_lookup(self, mock_which: MagicMock):
        self.assertTrue(network_steps._command_available("nmcli"))
        mock_which.assert_called_once_with("nmcli")

    @patch("common.network_steps.shutil.which", return_value=None)
    def test_command_available_handles_missing_executable(self, mock_which: MagicMock):
        self.assertFalse(network_steps._command_available("nmcli"))
        mock_which.assert_called_once_with("nmcli")

    def test_networkd_dual_stack_render(self):
        rendered = network_steps._render_networkd_config(
            _config(
                static_ipv4="192.168.10.20/24",
                static_ipv6="2001:db8:10::20/64",
                network_gateway4="192.168.10.1",
                network_gateway6="fe80::1",
                network_dns=["1.1.1.1", "2606:4700:4700::1111"],
            ),
            "enp1s0",
        )
        self.assertIn("Name=enp1s0", rendered)
        self.assertIn("DHCP=no", rendered)
        self.assertIn("Address=192.168.10.20/24", rendered)
        self.assertIn("Address=2001:db8:10::20/64", rendered)
        self.assertIn("Gateway=fe80::1", rendered)
        self.assertIn("DNS=2606:4700:4700::1111", rendered)

    def test_ifupdown_replaces_stanzas_and_keeps_original_backup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            network_dir = os.path.join(temp_dir, "network")
            interfaces_dir = os.path.join(network_dir, "interfaces.d")
            os.makedirs(interfaces_dir)
            main_path = os.path.join(network_dir, "interfaces")
            managed_path = os.path.join(interfaces_dir, "basaltwater_static")
            original = (
                "auto lo\niface lo inet loopback\n\n"
                "auto eth0\niface eth0 inet dhcp\n    metric 10\n\n"
                f"source {interfaces_dir}/*\n"
            )
            with open(main_path, "w", encoding="utf-8") as file_obj:
                file_obj.write(original)

            with patch.multiple(
                network_steps,
                IFUPDOWN_MAIN_CONFIG_PATH=main_path,
                IFUPDOWN_CONFIG_DIR=interfaces_dir,
                IFUPDOWN_CONFIG_PATH=managed_path,
            ):
                config = _config(
                    static_ipv4="192.168.10.20/24",
                    network_gateway4="192.168.10.1",
                    network_dns=["1.1.1.1"],
                )
                network_steps._configure_ifupdown(config, "eth0")
                network_steps._configure_ifupdown(config, "eth0")

            with open(main_path, "r", encoding="utf-8") as file_obj:
                self.assertNotIn("iface eth0 inet dhcp", file_obj.read())
            with open(f"{main_path}.basaltwater.bak", "r", encoding="utf-8") as file_obj:
                self.assertEqual(file_obj.read(), original)
            with open(managed_path, "r", encoding="utf-8") as file_obj:
                managed = file_obj.read()
            self.assertIn("iface eth0 inet static", managed)
            self.assertIn("address 192.168.10.20/24", managed)
            self.assertIn("dns-nameservers 1.1.1.1", managed)

    @patch("common.network_steps.run")
    def test_networkmanager_configuration_is_persistent_but_not_activated(self, mock_run):
        mock_run.side_effect = [
            subprocess.CompletedProcess([], 0, "Wired connection 1\n", ""),
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 0, "", ""),
        ]
        network_steps._configure_networkmanager(
            _config(
                static_ipv4="192.168.10.20/24",
                network_gateway4="192.168.10.1",
                network_dns=["1.1.1.1"],
            ),
            "eth0",
        )
        commands = [call.args[0] for call in mock_run.call_args_list]
        self.assertTrue(any("ipv4.method manual" in command for command in commands))
        self.assertTrue(any("ipv4.dns 1.1.1.1" in command for command in commands))
        self.assertFalse(any("connection up" in command for command in commands))

    @patch("common.network_steps._restore_persistence_rollback")
    @patch("common.network_steps._persist_static_network", side_effect=RuntimeError("failed"))
    @patch(
        "common.network_steps._capture_persistence_rollback",
        return_value={"kind": "networkd", "files": []},
    )
    @patch("common.network_steps._detect_network_backend", return_value="networkd")
    @patch("common.network_steps._reject_managed_bridge")
    @patch("common.network_steps._resolve_network_interface", return_value="eth0")
    @patch("common.network_steps.is_dry_run", return_value=False)
    def test_staged_persistence_restores_snapshot_on_failure(
        self,
        _mock_dry_run: MagicMock,
        _mock_interface: MagicMock,
        _mock_bridge: MagicMock,
        _mock_backend: MagicMock,
        _mock_capture: MagicMock,
        _mock_persist: MagicMock,
        mock_restore: MagicMock,
    ):
        with self.assertRaisesRegex(RuntimeError, "failed"):
            network_steps.configure_static_network(
                _config(static_ipv4="192.168.10.20/24")
            )

        mock_restore.assert_called_once_with(
            {"kind": "networkd", "files": []},
            "networkd",
        )


class TestHostnameStep(unittest.TestCase):
    @patch("common.network_steps.write_text_atomic")
    @patch("common.network_steps.os.path.isdir", return_value=True)
    @patch("common.network_steps.is_dry_run", return_value=False)
    @patch("common.network_steps.run")
    def test_sets_hostname_and_prevents_cloud_init_reset(
        self,
        mock_run: MagicMock,
        _mock_dry_run: MagicMock,
        _mock_isdir: MagicMock,
        mock_write: MagicMock,
    ):
        network_steps.configure_system_hostname(_config(system_hostname="app-01"))
        mock_run.assert_called_once_with("hostnamectl set-hostname app-01")
        mock_write.assert_called_once()


class TestLiveNetworkTransition(unittest.TestCase):
    @patch("common.network_steps.run")
    @patch("common.network_steps._select_policy_table", return_value=20000)
    @patch("common.network_steps._capture_persistence_rollback")
    @patch("common.network_steps._active_interface_addresses")
    def test_prepare_adds_new_address_without_removing_current_address(
        self,
        mock_active: MagicMock,
        mock_rollback: MagicMock,
        _mock_table: MagicMock,
        mock_run: MagicMock,
    ):
        mock_active.return_value = {"192.168.10.20"}
        mock_rollback.return_value = {
            "kind": "networkd",
            "files": [],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            pending_path = os.path.join(temp_dir, "transition.json")
            with patch.object(network_steps, "NETWORK_TRANSITION_PATH", pending_path):
                network_steps._prepare_live_network_transition(
                    _config(
                        static_ipv4="192.168.10.21/24",
                        network_gateway4="192.168.10.1",
                        activate_network=True,
                    ),
                    "eth0",
                    "networkd",
                )

            self.assertTrue(os.path.exists(pending_path))
            with open(pending_path, "r", encoding="utf-8") as file_obj:
                payload = network_steps.json.load(file_obj)
            self.assertRegex(payload["transition_id"], r"^[0-9a-f]{64}$")
            self.assertEqual(payload["state"], "prepared")

        commands = [call.args[0] for call in mock_run.call_args_list]
        self.assertIn("ip -4 address add 192.168.10.21/24 dev eth0", commands)
        self.assertTrue(any("route replace table 20000 default" in command for command in commands))
        self.assertFalse(any("address del" in command for command in commands))

    @patch("common.network_steps._persist_static_network")
    def test_commit_persists_only_after_pending_transition_is_loaded(self, mock_persist):
        with tempfile.TemporaryDirectory() as temp_dir:
            pending_path = os.path.join(temp_dir, "transition.json")
            network_path = os.path.join(temp_dir, "static.network")
            cloud_path = os.path.join(temp_dir, "cloud.cfg")
            network_steps.write_json_atomic(
                pending_path,
                _transition_payload(network_path, cloud_path),
            )
            with patch.multiple(
                network_steps,
                NETWORK_TRANSITION_PATH=pending_path,
                NETWORKD_CONFIG_PATH=network_path,
                CLOUD_INIT_NETWORK_CONFIG_PATH=cloud_path,
            ), patch.object(network_steps, "_reject_managed_bridge"):
                network_steps.commit_network_transition("a" * 64)

                self.assertTrue(os.path.exists(pending_path))
                with open(pending_path, "r", encoding="utf-8") as file_obj:
                    self.assertEqual(network_steps.json.load(file_obj)["state"], "committed")
                network_steps.commit_network_transition("a" * 64)
                network_steps.finalize_network_transition("a" * 64)

            self.assertFalse(os.path.exists(pending_path))
            mock_persist.assert_called_once()
            persisted_config, interface, backend, rollback = mock_persist.call_args.args
            self.assertEqual(persisted_config.static_ipv4, "192.168.10.21/24")
            self.assertEqual((interface, backend), ("eth0", "networkd"))
            self.assertEqual(rollback["kind"], "networkd")

    @patch("common.network_steps.run")
    def test_abort_removes_only_temporary_transition_state(self, mock_run):
        with tempfile.TemporaryDirectory() as temp_dir:
            pending_path = os.path.join(temp_dir, "transition.json")
            network_path = os.path.join(temp_dir, "static.network")
            cloud_path = os.path.join(temp_dir, "cloud.cfg")
            payload = _transition_payload(network_path, cloud_path, state="committed")
            payload["rollback"] = {
                "kind": "networkd",
                "files": [
                    {
                        "path": network_path,
                        "kind": "file",
                        "content": "old network\n",
                        "mode": 0o640,
                    },
                    {"path": cloud_path, "kind": "absent"},
                ],
            }
            with open(network_path, "w", encoding="utf-8") as file_obj:
                file_obj.write("new network\n")
            network_steps.write_json_atomic(
                pending_path,
                payload,
            )
            with patch.multiple(
                network_steps,
                NETWORK_TRANSITION_PATH=pending_path,
                NETWORKD_CONFIG_PATH=network_path,
                CLOUD_INIT_NETWORK_CONFIG_PATH=cloud_path,
            ):
                network_steps.abort_network_transition("a" * 64)

            self.assertFalse(os.path.exists(pending_path))
            with open(network_path, "r", encoding="utf-8") as file_obj:
                self.assertEqual(file_obj.read(), "old network\n")

        commands = [call.args[0] for call in mock_run.call_args_list]
        self.assertIn(
            "ip -4 rule del priority 20000 from 192.168.10.21/32 table 20000",
            commands,
        )
        self.assertIn("ip -4 route flush table 20000", commands)
        self.assertIn("ip -4 address del 192.168.10.21/24 dev eth0", commands)

    @patch("common.network_steps._restore_persistence_rollback")
    @patch(
        "common.network_steps._persist_static_network",
        side_effect=RuntimeError("write failed"),
    )
    def test_failed_commit_restores_snapshot_and_keeps_transaction_for_abort(
        self,
        _mock_persist: MagicMock,
        mock_restore: MagicMock,
    ):
        with tempfile.TemporaryDirectory() as temp_dir:
            pending_path = os.path.join(temp_dir, "transition.json")
            network_path = os.path.join(temp_dir, "static.network")
            cloud_path = os.path.join(temp_dir, "cloud.cfg")
            network_steps.write_json_atomic(
                pending_path,
                _transition_payload(network_path, cloud_path),
            )
            with patch.multiple(
                network_steps,
                NETWORK_TRANSITION_PATH=pending_path,
                NETWORKD_CONFIG_PATH=network_path,
                CLOUD_INIT_NETWORK_CONFIG_PATH=cloud_path,
            ), patch.object(network_steps, "_reject_managed_bridge"):
                with self.assertRaisesRegex(RuntimeError, "write failed"):
                    network_steps.commit_network_transition("a" * 64)

            self.assertTrue(os.path.exists(pending_path))
            with open(pending_path, "r", encoding="utf-8") as file_obj:
                self.assertEqual(network_steps.json.load(file_obj)["state"], "prepared")
            mock_restore.assert_called_once()

    @patch("common.network_steps.os.path.isdir", return_value=True)
    def test_rejects_generic_changes_to_proxmox_style_bridge(self, _mock_isdir):
        with self.assertRaisesRegex(RuntimeError, "topology-aware"):
            network_steps._reject_managed_bridge("vmbr0")


if __name__ == "__main__":
    unittest.main()
