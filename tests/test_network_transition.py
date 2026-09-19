"""Tests for controller-verified live network handoffs."""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from lib.config import SetupConfig
from lib.network_transition import (
    RemoteNetworkTransition,
    _wait_for_transition_state,
    finish_network_transition,
    network_transition_targets,
)


TRANSITION_ID = "a" * 64


def _config(**overrides: object) -> SetupConfig:
    values: dict[str, object] = {
        "host": "192.168.10.20",
        "username": "admin",
        "system_type": "server_lite",
        "static_ipv4": "192.168.10.21/24",
        "activate_network": True,
    }
    values.update(overrides)
    return SetupConfig(**values)  # type: ignore[arg-type]


def _result(
    returncode: int = 0,
    stderr: str = "",
    stdout: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["ssh"], returncode, stdout, stderr)


class TestNetworkTransitionTargets(unittest.TestCase):
    def test_prefers_ipv4_and_normalizes_dual_stack_targets(self) -> None:
        config = _config(static_ipv6="2001:db8:10::21/64")

        self.assertEqual(
            network_transition_targets(config),
            ["192.168.10.21", "2001:db8:10::21"],
        )


class TestFinishNetworkTransition(unittest.TestCase):
    @patch("lib.network_transition._wait_for_any_transition")
    @patch("lib.network_transition._wait_for_transition_state", return_value=_result())
    @patch("lib.network_transition._wait_for_transition_ssh", return_value=_result())
    def test_verifies_before_and_after_commit_then_adopts_new_host(
        self,
        mock_action,
        mock_state,
        mock_read,
    ) -> None:
        mock_read.return_value = RemoteNetworkTransition(TRANSITION_ID, "prepared")
        config = _config()

        result = finish_network_transition(config, 0)

        self.assertEqual(result, 0)
        self.assertEqual(config.host, "192.168.10.21")
        self.assertEqual(mock_state.call_count, 3)
        self.assertEqual(mock_action.call_count, 2)
        self.assertIn("--commit-transition", mock_action.call_args_list[0].args[2])
        self.assertIn("--finalize-transition", mock_action.call_args_list[1].args[2])

    @patch("lib.network_transition.apply_proxmox_network_plan")
    @patch("lib.network_transition.prepare_proxmox_network_plan")
    @patch("lib.network_transition._wait_for_any_transition")
    @patch("lib.network_transition._wait_for_transition_state", return_value=_result())
    @patch("lib.network_transition._wait_for_transition_ssh", return_value=_result())
    def test_updates_proxmox_metadata_between_guest_verification_checks(
        self,
        _mock_action,
        mock_state,
        mock_read,
        mock_prepare,
        mock_apply,
    ) -> None:
        from lib.proxmox_network_transition import ProxmoxNetworkPlan

        config = _config(hosted_node="10.0.0.10", machine_type="unprivileged")
        plan = ProxmoxNetworkPlan(
            node="10.0.0.10",
            user="root",
            ssh_opts=[],
            guest_kind="LXC",
            vmid=101,
            option="net0",
            previous_value="name=eth0,ip=192.168.10.20/24,type=veth",
            requested_value="name=eth0,ip=192.168.10.21/24,type=veth",
        )
        mock_prepare.return_value = plan
        mock_read.return_value = RemoteNetworkTransition(TRANSITION_ID, "prepared")

        result = finish_network_transition(config, 0)

        self.assertEqual(result, 0)
        mock_prepare.assert_called_once_with(config, "192.168.10.20")
        mock_apply.assert_called_once_with(plan)
        self.assertEqual(mock_state.call_count, 5)

    @patch("lib.network_transition._abort_transition")
    @patch("lib.network_transition._wait_for_any_transition")
    @patch(
        "lib.network_transition._wait_for_transition_state",
        return_value=_result(255, "connection refused"),
    )
    def test_failed_new_address_verification_aborts_without_changing_host(
        self,
        _mock_state,
        mock_read,
        mock_abort,
    ) -> None:
        mock_read.return_value = RemoteNetworkTransition(TRANSITION_ID, "prepared")
        config = _config()

        result = finish_network_transition(config, 0)

        self.assertEqual(result, 1)
        self.assertEqual(config.host, "192.168.10.20")
        mock_abort.assert_called_once_with(config, TRANSITION_ID, [])

    @patch("lib.network_transition._abort_transition")
    def test_failed_setup_cleans_up_temporary_addresses(self, mock_abort) -> None:
        config = _config()

        self.assertEqual(finish_network_transition(config, 3), 3)
        mock_abort.assert_called_once_with(config)

    @patch("lib.network_transition._rollback_handoff")
    @patch("lib.network_transition._wait_for_any_transition")
    @patch("lib.network_transition._wait_for_transition_state", return_value=_result())
    @patch(
        "lib.network_transition._wait_for_transition_ssh",
        return_value=_result(1, "write failed"),
    )
    def test_persistence_failure_rolls_back_guest_and_proxmox_state(
        self,
        _mock_action,
        _mock_state,
        mock_read,
        mock_rollback,
    ) -> None:
        mock_read.return_value = RemoteNetworkTransition(TRANSITION_ID, "prepared")
        config = _config()

        self.assertEqual(finish_network_transition(config, 0), 1)

        mock_rollback.assert_called_once_with(
            config,
            None,
            TRANSITION_ID,
            ["192.168.10.21"],
        )


class TestTransitionIdentity(unittest.TestCase):
    @patch(
        "lib.network_transition._run_transition_ssh",
        return_value=_result(stdout=f"{'b' * 64} prepared\n"),
    )
    def test_rejects_a_reachable_host_with_a_different_transaction(self, _mock_run) -> None:
        result = _wait_for_transition_state(
            _config(),
            "192.168.10.21",
            TRANSITION_ID,
            "prepared",
            attempts=1,
            interval=0,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("identity", result.stderr)


class TestSavedHostMigration(unittest.TestCase):
    @patch("basaltwater.remove_replaced_setup_cache")
    @patch("basaltwater.save_setup_command")
    @patch("basaltwater.store_cli_credentials")
    def test_successful_patch_saves_only_the_verified_new_host(
        self,
        _mock_credentials,
        mock_save,
        mock_remove,
    ) -> None:
        from basaltwater import _execute_patch_config

        config = _config()
        runtime_config = _config()
        access_details = {"syncthing-admin": "https://192.168.10.21:8446/"}

        def finish_runtime(runtime: SetupConfig) -> int:
            runtime.host = "192.168.10.21"
            return 0

        with patch(
            "basaltwater._prepare_runtime_config_for_cli",
            return_value=runtime_config,
        ), patch(
            "basaltwater.run_remote_setup",
            side_effect=finish_runtime,
        ), patch(
            "basaltwater.get_last_remote_access_details",
            return_value=access_details,
        ), patch(
            "basaltwater.print_service_access_summary",
        ) as print_access:
            result = _execute_patch_config(config)

        self.assertEqual(result, 0)
        self.assertEqual(config.host, "192.168.10.21")
        self.assertFalse(config.activate_network)
        self.assertEqual(mock_save.call_args.args[0].host, "192.168.10.21")
        mock_remove.assert_called_once_with("192.168.10.20", "192.168.10.21")
        print_access.assert_called_once_with(
            config,
            remote_access_details=access_details,
        )


if __name__ == "__main__":
    unittest.main()
