"""Tests for Proxmox host and guest memory safety policy."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, call, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from common.proxmox_steps import (
    configure_proxmox_balloon_target,
    configure_proxmox_host_memory_safety,
)
from lib.arg_parser import create_setup_argument_parser
from lib.config import SetupConfig
from lib.proxmox_memory import (
    SWAPON_STATUS_COMMAND,
    calculate_balloon_target,
    parse_swapon_output,
    parse_guest_memory_config,
)
from lib.proxmox_vm import (
    ProvisionError,
    _create_vm,
    _enforce_memory_floor,
    _report_memory_capacity,
)
from lib.validation import validate_hosted_flags


class TestAutomaticBalloonTarget(unittest.TestCase):
    def test_large_host_keeps_twenty_percent_headroom(self):
        policy = calculate_balloon_target(32768)

        self.assertEqual(policy.target_percent, 80)
        self.assertEqual(policy.reserve_mib, 6554)
        self.assertTrue(policy.automatic)

    def test_eight_gib_host_keeps_two_gib_headroom(self):
        policy = calculate_balloon_target(8192)

        self.assertEqual(policy.target_percent, 75)
        self.assertEqual(policy.reserve_mib, 2048)

    def test_small_host_uses_fifty_percent_safety_floor(self):
        policy = calculate_balloon_target(3072)

        self.assertEqual(policy.target_percent, 50)
        self.assertEqual(policy.reserve_mib, 1536)

    def test_override_is_preserved(self):
        policy = calculate_balloon_target(16384, 65)

        self.assertEqual(policy.target_percent, 65)
        self.assertEqual(policy.reserve_mib, 5735)
        self.assertFalse(policy.automatic)


class TestConfigureBalloonTarget(unittest.TestCase):
    @patch("common.proxmox_steps.is_dry_run", return_value=False)
    @patch("common.proxmox_steps.run")
    @patch("common.proxmox_steps._host_total_memory_mib", return_value=32768)
    def test_materializes_implicit_default(self, _mock_memory, mock_run, _mock_dry):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="{}", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(
                returncode=0,
                stdout='{"ballooning-target": 80}',
                stderr="",
            ),
        ]
        config = SetupConfig(
            username="root",
            host="pve1",
            system_type="server_proxmox",
        )

        configure_proxmox_balloon_target(config)

        self.assertIn(
            call("pvenode config set --ballooning-target 80"),
            mock_run.call_args_list,
        )

    @patch("common.proxmox_steps.is_dry_run", return_value=False)
    @patch("common.proxmox_steps.run")
    @patch("common.proxmox_steps._host_total_memory_mib", return_value=8192)
    def test_changes_and_verifies_target(self, _mock_memory, mock_run, _mock_dry):
        mock_run.side_effect = [
            MagicMock(
                returncode=0,
                stdout='{"ballooning-target": 80}',
                stderr="",
            ),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(
                returncode=0,
                stdout='{"ballooning-target": 75}',
                stderr="",
            ),
        ]
        config = SetupConfig(
            username="root",
            host="pve1",
            system_type="server_proxmox",
        )

        configure_proxmox_balloon_target(config)

        self.assertEqual(
            mock_run.call_args_list,
            [
                call(
                    "pvesh get /nodes/$(hostname -s)/config --output-format json",
                    capture_output=True,
                ),
                call("pvenode config set --ballooning-target 75"),
                call(
                    "pvesh get /nodes/$(hostname -s)/config --output-format json",
                    capture_output=True,
                ),
            ],
        )

    @patch("common.proxmox_steps.is_dry_run", return_value=False)
    @patch("common.proxmox_steps.run")
    @patch("common.proxmox_steps._host_total_memory_mib", return_value=16384)
    def test_override_is_idempotent(self, _mock_memory, mock_run, _mock_dry):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"ballooning-target": 70}',
            stderr="",
        )
        config = SetupConfig(
            username="root",
            host="pve1",
            system_type="server_proxmox",
            proxmox_balloon_target=70,
        )

        configure_proxmox_balloon_target(config)

        mock_run.assert_called_once_with(
            "pvesh get /nodes/$(hostname -s)/config --output-format json",
            capture_output=True,
        )


class TestHostMemorySafety(unittest.TestCase):
    def test_requests_explicit_swapon_columns(self):
        self.assertEqual(
            SWAPON_STATUS_COMMAND,
            "swapon --show=NAME,TYPE,SIZE,USED --bytes --noheadings --raw",
        )

    def test_parses_partition_and_zfs_swap_devices(self):
        devices = parse_swapon_output(
            "/dev/dm-0 partition 8053063680 0 -2\n"
            "/dev/zvol/rpool/swap partition 2147483648 1024\n"
        )

        self.assertEqual(len(devices), 2)
        self.assertFalse(devices[0].zfs_backed)
        self.assertTrue(devices[1].zfs_backed)

    @patch("common.proxmox_steps.is_dry_run", return_value=False)
    @patch("common.proxmox_steps.run")
    def test_persists_and_verifies_low_swappiness(self, mock_run, _mock_dry):
        mock_run.side_effect = [
            MagicMock(
                returncode=0,
                stdout="/dev/dm-0 partition 8053063680 0\n",
            ),
            MagicMock(returncode=0, stdout="60\n"),
            MagicMock(returncode=1, stdout=""),
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout="10\n"),
        ]

        configure_proxmox_host_memory_safety(
            SetupConfig(
                username="root",
                host="pve1",
                system_type="server_proxmox",
            )
        )

        commands = [printed.args[0] for printed in mock_run.call_args_list]
        self.assertIn(
            "/usr/lib/systemd/systemd-sysctl --prefix=/vm/swappiness",
            commands,
        )
        tee_call = next(
            printed
            for printed in mock_run.call_args_list
            if printed.args[0].startswith("tee ")
        )
        self.assertEqual(
            tee_call.kwargs["input_data"],
            "# Managed by basaltwater for Proxmox hosts.\nvm.swappiness = 10\n",
        )

    @patch("common.proxmox_steps.is_dry_run", return_value=False)
    @patch("common.proxmox_steps.run")
    def test_host_memory_policy_is_idempotent(self, mock_run, _mock_dry):
        content = (
            "# Managed by basaltwater for Proxmox hosts.\n"
            "vm.swappiness = 10\n"
        )
        mock_run.side_effect = [
            MagicMock(
                returncode=0,
                stdout="/dev/dm-0 partition 8053063680 0\n",
            ),
            MagicMock(returncode=0, stdout="10\n"),
            MagicMock(returncode=0, stdout=content),
            MagicMock(returncode=0, stdout="10\n"),
        ]

        configure_proxmox_host_memory_safety(
            SetupConfig(
                username="root",
                host="pve1",
                system_type="server_proxmox",
            )
        )

        commands = [printed.args[0] for printed in mock_run.call_args_list]
        self.assertFalse(any(command.startswith("tee ") for command in commands))
        self.assertNotIn(
            "/usr/lib/systemd/systemd-sysctl --prefix=/vm/swappiness",
            commands,
        )


class TestGuestMemoryCapacity(unittest.TestCase):
    def test_qemu_balloon_minimum_is_the_floor(self):
        allocation = parse_guest_memory_config(
            "memory: 8192\nballoon: 2048\n",
            guest_type="qemu",
            vmid=101,
        )

        self.assertIsNotNone(allocation)
        assert allocation is not None
        self.assertEqual(allocation.minimum_mib, 2048)
        self.assertEqual(allocation.maximum_mib, 8192)

    def test_disabled_ballooning_is_fixed_memory(self):
        allocation = parse_guest_memory_config(
            "memory: 4096\nballoon: 0\n",
            guest_type="qemu",
            vmid=102,
        )

        self.assertIsNotNone(allocation)
        assert allocation is not None
        self.assertEqual(allocation.minimum_mib, 4096)
        self.assertEqual(allocation.maximum_mib, 4096)

    def test_lxc_memory_is_a_ceiling_not_a_floor(self):
        allocation = parse_guest_memory_config(
            "memory: 2048\n",
            guest_type="lxc",
            vmid=103,
        )

        self.assertIsNotNone(allocation)
        assert allocation is not None
        self.assertEqual(allocation.minimum_mib, 0)
        self.assertEqual(allocation.maximum_mib, 2048)

    @patch("builtins.print")
    @patch("lib.proxmox_vm._ssh_run")
    def test_capacity_report_compares_floors_and_bursts(self, mock_run, mock_print):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="pve1\n", stderr=""),
            MagicMock(
                returncode=0,
                stdout='{"memory": {"total": 8589934592, "used": 4294967296}}',
                stderr="",
            ),
            MagicMock(
                returncode=0,
                stdout='{"ballooning-target": 75}',
                stderr="",
            ),
            MagicMock(
                returncode=0,
                stdout=(
                    '[{"node": "pve1", "status": "running", '
                    '"type": "qemu", "vmid": 100}]'
                ),
                stderr="",
            ),
            MagicMock(
                returncode=0,
                stdout="memory: 4096\nballoon: 2048\n",
                stderr="",
            ),
        ]

        safe = _report_memory_capacity(
            node_ip="192.0.2.10",
            user="root",
            ssh_opts=[],
            proposed_minimum_mib=2048,
            proposed_maximum_mib=6144,
        )

        self.assertEqual(
            mock_run.call_args_list[2].args[3],
            "pvesh get /nodes/pve1/config --output-format json",
        )
        output = "\n".join(
            " ".join(str(arg) for arg in printed.args)
            for printed in mock_print.call_args_list
        )
        self.assertIn("Host: 8.0 GiB total, 4.0 GiB used", output)
        self.assertIn("floors 4.0 GiB (66% of target)", output)
        self.assertIn("burst maxima 10.0 GiB (166% of target)", output)
        self.assertIn("Guest burst maxima exceed", output)
        self.assertTrue(safe)

    @patch("builtins.print")
    @patch("lib.proxmox_vm._ssh_run")
    def test_capacity_report_replaces_existing_vm_allocation(
        self,
        mock_run,
        mock_print,
    ):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="pve1\n", stderr=""),
            MagicMock(
                returncode=0,
                stdout='{"memory": {"total": 8589934592, "used": 4294967296}}',
                stderr="",
            ),
            MagicMock(
                returncode=0,
                stdout='{"ballooning-target": 75}',
                stderr="",
            ),
            MagicMock(
                returncode=0,
                stdout=(
                    '[{"node": "pve1", "status": "running", '
                    '"type": "qemu", "vmid": 100}, '
                    '{"node": "pve1", "status": "running", '
                    '"type": "qemu", "vmid": 101}]'
                ),
                stderr="",
            ),
            MagicMock(
                returncode=0,
                stdout="memory: 4096\nballoon: 2048\n",
                stderr="",
            ),
            MagicMock(
                returncode=0,
                stdout="memory: 2048\nballoon: 1024\n",
                stderr="",
            ),
        ]

        safe = _report_memory_capacity(
            node_ip="192.0.2.10",
            user="root",
            ssh_opts=[],
            proposed_minimum_mib=2048,
            proposed_maximum_mib=4096,
            replacing_vmid=100,
        )

        output = "\n".join(
            " ".join(str(arg) for arg in printed.args)
            for printed in mock_print.call_args_list
        )
        self.assertIn("Running guests (1)", output)
        self.assertIn("floors 3.0 GiB (50% of target)", output)
        self.assertIn("burst maxima 6.0 GiB (100% of target)", output)
        self.assertTrue(safe)

    def test_floor_over_target_requires_explicit_override(self):
        with self.assertRaisesRegex(ProvisionError, "allow-memory-overcommit"):
            _enforce_memory_floor(False, False)

        with patch("builtins.print") as mock_print:
            _enforce_memory_floor(False, True)
        self.assertTrue(
            any("Continuing" in str(item) for item in mock_print.call_args_list)
        )

    @patch("lib.proxmox_vm._ssh_run")
    def test_vm_creation_sets_relative_balloon_shares(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        _create_vm(
            vmid=101,
            target_ip="192.0.2.50",
            image_remote_path="/var/lib/vz/import/debian.qcow2",
            storage_ref=None,
            memory_mb=8192,
            balloon_min_mb=2048,
            balloon_shares=2500,
            cores=4,
            root_pool="local-lvm",
            disk_size_gib=32,
            data_disk_specs=[],
            cidr_prefix="24",
            bridge="vmbr0",
            gateway="192.0.2.1",
            nameservers=["192.0.2.1"],
            hostname="dynamic-vm",
            user_data_path=None,
            user_data_ref=None,
            graphical_console=False,
            node_ip="192.0.2.10",
            user="root",
            ssh_opts=[],
        )

        create_command = mock_run.call_args_list[0].args[3]
        self.assertIn("--memory 8192", create_command)
        self.assertIn("--balloon 2048", create_command)
        self.assertIn("--shares 2500", create_command)


class TestBalloonFlags(unittest.TestCase):
    def test_local_parser_accepts_host_target_and_vm_shares(self):
        parser = create_setup_argument_parser("Test", for_remote=False)
        args = parser.parse_args(
            [
                "pve1",
                "--proxmox-balloon-target",
                "70",
                "--balloon-shares",
                "2500",
                "--allow-memory-overcommit",
            ]
        )

        self.assertEqual(args.proxmox_balloon_target, 70)
        self.assertEqual(args.vm_balloon_shares, 2500)
        self.assertTrue(args.allow_memory_overcommit)

    def test_remote_parser_accepts_host_target(self):
        parser = create_setup_argument_parser("Test", for_remote=True)
        args = parser.parse_args(["--proxmox-balloon-target", "70"])

        self.assertEqual(args.proxmox_balloon_target, 70)

    def test_host_target_rejects_non_proxmox_setup(self):
        config = SetupConfig(
            username="root",
            host="server1",
            system_type="server_lite",
            proxmox_balloon_target=70,
        )

        with self.assertRaisesRegex(ValueError, "server_proxmox"):
            validate_hosted_flags(config)

    def test_memory_overcommit_requires_hosted_vm(self):
        config = SetupConfig(
            username="root",
            host="server1",
            system_type="server_lite",
            allow_memory_overcommit=True,
        )

        with self.assertRaisesRegex(ValueError, "provision-on"):
            validate_hosted_flags(config)

    def test_overcommit_override_is_reconstructed(self):
        config = SetupConfig(
            username="agent",
            host="192.0.2.50",
            system_type="agent_vm",
            hosted_node="pve1",
            container_memory="8G",
            vm_balloon_min="2G",
            allow_memory_overcommit=True,
        )

        self.assertIn(
            "--allow-memory-overcommit",
            config.to_setup_command(),
        )

    def test_host_target_is_forwarded_to_remote_setup(self):
        config = SetupConfig(
            username="root",
            host="pve1",
            system_type="server_proxmox",
            proxmox_balloon_target=70,
        )

        self.assertIn(
            "--proxmox-balloon-target 70",
            config.to_remote_args(),
        )


if __name__ == "__main__":
    unittest.main()
