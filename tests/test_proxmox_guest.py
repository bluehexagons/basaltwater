"""Tests for shared Proxmox guest helpers."""

from __future__ import annotations

import ipaddress
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.proxmox_guest import (
    _build_guest_hostname,
    _create_with_vmid_retry,
    _get_guest_gateway,
    _get_host_nameservers,
    _parse_corosync_config,
    ProvisionError,
    enroll_provisioned_guest_host_keys,
    ensure_guest_ipv4_route,
    _ssh_run,
    _wait_for_guest_ssh,
    probe_proxmox_cluster,
    probe_proxmox_host,
    refresh_managed_guest_host_keys,
    resolve_guest_ssh_key,
    guest_provisioning_locks,
)


class TestBuildGuestHostname(unittest.TestCase):
    def test_friendly_name_passthrough(self) -> None:
        self.assertEqual(
            _build_guest_hostname("10.0.0.50", "My Web Server", default_prefix="vm"),
            "my-web-server",
        )

    def test_default_prefix_rewrites_legacy_lxc_prefix(self) -> None:
        self.assertEqual(
            _build_guest_hostname("10.0.0.50", None, default_prefix="vm"),
            "vm-10-0-0-50",
        )
        self.assertEqual(
            _build_guest_hostname("10.0.0.50", None),
            "guest-10-0-0-50",
        )


class TestProvisioningConcurrency(unittest.TestCase):
    def test_same_guest_identity_cannot_be_provisioned_twice(self) -> None:
        with guest_provisioning_locks("pve1", "10.0.0.50", "web-1"):
            with self.assertRaisesRegex(ProvisionError, "already provisioning"):
                with guest_provisioning_locks("pve1", "10.0.0.50", "web-1"):
                    pass

    def test_distinct_guest_identities_can_hold_locks_together(self) -> None:
        with guest_provisioning_locks("pve1", "10.0.0.50", "web-1"):
            with guest_provisioning_locks("pve1", "10.0.0.51", "web-2"):
                pass

    def test_vmid_collisions_are_retried_until_creation_succeeds(self) -> None:
        attempts: list[int] = []
        next_ids = iter([101, 102])

        def create_guest(*, vmid: int) -> None:
            attempts.append(vmid)
            if vmid < 102:
                raise ProvisionError(f"guest {vmid} already exists")

        vmid = _create_with_vmid_retry(
            create_guest,
            lambda: next(next_ids),
            {"vmid": 100},
        )

        self.assertEqual(vmid, 102)
        self.assertEqual(attempts, [100, 101, 102])

    def test_non_collision_creation_error_is_not_retried(self) -> None:
        with self.assertRaisesRegex(ProvisionError, "storage unavailable"):
            _create_with_vmid_retry(
                lambda **_kwargs: (_ for _ in ()).throw(
                    ProvisionError("storage unavailable")
                ),
                lambda: 101,
                {"vmid": 100},
            )


class TestGuestSshKeyResolution(unittest.TestCase):
    def _write_identity(self, directory: str, name: str) -> str:
        private_key = os.path.join(directory, name)
        with open(private_key, "w", encoding="utf-8") as file_obj:
            file_obj.write("private")
        with open(private_key + ".pub", "w", encoding="utf-8") as file_obj:
            file_obj.write("ssh-ed25519 AAAA...\n")
        return private_key

    def test_prefers_matching_preferred_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            preferred = self._write_identity(tmp, "preferred")
            default_dir = os.path.join(tmp, "home")
            os.makedirs(os.path.join(default_dir, ".ssh"))
            self._write_identity(os.path.join(default_dir, ".ssh"), "id_ed25519")

            self.assertEqual(
                resolve_guest_ssh_key(preferred, home=default_dir),
                preferred,
            )

    def test_falls_back_to_default_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ssh_dir = os.path.join(tmp, ".ssh")
            os.makedirs(ssh_dir)
            default_key = self._write_identity(ssh_dir, "id_ed25519")

            self.assertEqual(resolve_guest_ssh_key(home=tmp), default_key)

    def test_returns_none_without_a_complete_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(resolve_guest_ssh_key(home=tmp))


class TestWaitForGuestSsh(unittest.TestCase):
    @patch("lib.proxmox_guest._ssh_run")
    def test_returns_when_probe_succeeds(self, mock_run) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="READY\n", stderr="")

        _wait_for_guest_ssh("10.0.0.50", "10.0.0.1", "root", [], timeout=3)

        mock_run.assert_called_once()
        self.assertTrue(mock_run.call_args.kwargs["quiet"])

    @patch("lib.proxmox_guest._ssh_run")
    def test_dry_run_skips_probe(self, mock_run) -> None:
        _wait_for_guest_ssh("10.0.0.50", "10.0.0.1", "root", [], dry_run=True)
        mock_run.assert_not_called()


class TestProxmoxSshRun(unittest.TestCase):
    @patch("lib.proxmox_guest.get_ssh_control_path", return_value="/tmp/control.sock")
    @patch("lib.proxmox_guest.subprocess.run")
    def test_streamed_input_reuses_the_control_connection(
        self, mock_run, _mock_control_path
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            ["ssh"], 0, stdout="", stderr=""
        )

        _ssh_run(
            "192.0.2.10",
            "root",
            [
                "-o",
                "UserKnownHostsFile=/workspace/known_hosts",
                "-i",
                "/keys/proxmox",
            ],
            "cat > /tmp/payload",
            input_data="payload",
        )

        command = mock_run.call_args.args[0]
        self.assertIn("UserKnownHostsFile=/workspace/known_hosts", command)
        self.assertIn("ControlMaster=auto", command)
        self.assertIn("ControlPath=/tmp/control.sock", command)
        self.assertEqual(mock_run.call_args.kwargs["input"], "payload")

    @patch("lib.proxmox_guest.ssh_batch_mode", return_value=False)
    @patch("lib.proxmox_guest.get_ssh_control_path", return_value="/tmp/control.sock")
    @patch("lib.proxmox_guest.subprocess.run")
    def test_interactive_node_passphrase_prompt_has_no_wall_clock_timeout(
        self, mock_run, _mock_control_path, _mock_batch_mode
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            ["ssh"], 0, stdout="", stderr=""
        )

        _ssh_run("192.0.2.10", "root", [], "hostname -s")

        self.assertIsNone(mock_run.call_args.kwargs["timeout"])


class TestProvisionedGuestHostKeyEnrollment(unittest.TestCase):
    @patch(
        "lib.proxmox_guest.get_workspace_known_hosts_path",
        return_value="/workspace/known_hosts",
    )
    @patch(
        "lib.proxmox_guest.os.path.expanduser",
        return_value="/home/loren/.ssh/known_hosts",
    )
    @patch(
        "lib.proxmox_guest.replace_scanned_host_keys",
    )
    @patch("lib.proxmox_guest._ssh_run")
    def test_scans_through_proxmox_and_replaces_workspace_and_user_keys(
        self,
        mock_run,
        mock_replace,
        _mock_expanduser,
        _mock_workspace_known_hosts,
    ) -> None:
        scan = "192.0.2.40 ssh-ed25519 AAAA"
        mock_run.return_value = MagicMock(returncode=0, stdout=scan, stderr="")

        enroll_provisioned_guest_host_keys(
            "192.0.2.40",
            "192.0.2.10",
            "root",
            ["-i", "/keys/proxmox"],
        )

        self.assertIn(
            "ssh-keyscan -T 10 -t ed25519 192.0.2.40",
            mock_run.call_args.args[3],
        )
        self.assertTrue(mock_run.call_args.kwargs["quiet"])
        self.assertEqual(
            mock_replace.call_args_list,
            [
                call(
                    "192.0.2.40",
                    scan,
                    known_hosts_path="/workspace/known_hosts",
                ),
                call(
                    "192.0.2.40",
                    scan,
                    known_hosts_path="/home/loren/.ssh/known_hosts",
                ),
            ],
        )

    @patch("lib.proxmox_guest.replace_scanned_host_keys")
    @patch("lib.proxmox_guest._ssh_run")
    def test_missing_key_fails_before_guest_login(self, mock_run, mock_replace) -> None:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="timeout")

        with self.assertRaisesRegex(ProvisionError, "timeout"):
            enroll_provisioned_guest_host_keys(
                "192.0.2.40",
                "192.0.2.10",
                "root",
                [],
            )

        mock_replace.assert_not_called()

    @patch("lib.proxmox_guest.enroll_provisioned_guest_host_keys")
    @patch("lib.proxmox_guest._ssh_opts", return_value=["node-options"])
    def test_refresh_builds_authenticated_node_options(
        self,
        mock_opts,
        mock_enroll,
    ) -> None:
        refresh_managed_guest_host_keys(
            "192.0.2.40",
            "192.0.2.10",
            "root",
            "/keys/proxmox",
        )

        mock_opts.assert_called_once_with("/keys/proxmox")
        mock_enroll.assert_called_once_with(
            "192.0.2.40",
            "192.0.2.10",
            "root",
            ["node-options"],
            dry_run=False,
        )


class TestGuestRouteRepair(unittest.TestCase):
    @patch("lib.proxmox_guest.ssh_batch_mode", return_value=True)
    @patch("lib.proxmox_guest.ensure_remote_sudo", return_value=True)
    @patch("lib.proxmox_guest.build_ssh_command", return_value=["ssh"])
    @patch("lib.proxmox_guest.subprocess.run")
    def test_existing_route_is_left_in_place(
        self,
        mock_run,
        mock_build,
        _mock_sudo,
        _mock_batch_mode,
    ):
        mock_run.return_value = subprocess.CompletedProcess(
            ["ssh"], 0, stdout="already\n", stderr=""
        )

        ensure_guest_ipv4_route(
            "192.168.0.41/24",
            "192.168.0.1",
            "agent",
            "/keys/agent",
        )

        mock_build.assert_called_once()
        self.assertEqual(mock_build.call_args.args[1], "agent")
        self.assertIn("192.168.0.41", mock_build.call_args.kwargs["remote_command"])
        self.assertIn("192.168.0.1", mock_build.call_args.kwargs["remote_command"])
        self.assertTrue(mock_build.call_args.kwargs["batch_mode"])
        mock_run.assert_called_once_with(
            ["ssh"],
            capture_output=True,
            text=True,
            timeout=60,
        )

    @patch("lib.proxmox_guest.ensure_remote_sudo", return_value=True)
    @patch("lib.proxmox_guest.build_ssh_command", return_value=["ssh"])
    @patch("lib.proxmox_guest.subprocess.run")
    def test_repairs_missing_route(self, mock_run, _mock_build, _mock_sudo):
        mock_run.return_value = subprocess.CompletedProcess(
            ["ssh"], 0, stdout="repaired\n", stderr=""
        )

        ensure_guest_ipv4_route(
            "192.168.0.41/24",
            "192.168.0.1",
            "agent",
            "/keys/agent",
        )

        self.assertIn(
            'sudo -n ip -4 route replace default via "$gateway"',
            _mock_build.call_args.kwargs["remote_command"],
        )
        mock_run.assert_called_once()

    @patch("lib.proxmox_guest.ssh_batch_mode", return_value=False)
    @patch("lib.proxmox_guest.ensure_remote_sudo", return_value=True)
    @patch("lib.proxmox_guest.build_ssh_command", return_value=["ssh"])
    @patch("lib.proxmox_guest.subprocess.run")
    def test_interactive_guest_passphrase_prompt_has_no_wall_clock_timeout(
        self,
        mock_run,
        _mock_build,
        _mock_sudo,
        _mock_batch_mode,
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            ["ssh"], 0, stdout="already\n", stderr=""
        )

        ensure_guest_ipv4_route(
            "192.168.0.41/24",
            "192.168.0.1",
            "agent",
            "/keys/agent",
        )

        self.assertIsNone(mock_run.call_args.kwargs["timeout"])

    @patch("lib.proxmox_guest.ensure_remote_sudo", return_value=True)
    @patch("lib.proxmox_guest.build_ssh_command", return_value=["ssh"])
    @patch("lib.proxmox_guest.subprocess.run")
    def test_route_failure_is_reported(self, mock_run, _mock_build, _mock_sudo):
        mock_run.return_value = subprocess.CompletedProcess(
            ["ssh"], 1, stdout="", stderr="route failed"
        )

        with self.assertRaisesRegex(ProvisionError, "route failed"):
            ensure_guest_ipv4_route(
                "192.168.0.41/24",
                "192.168.0.1",
                "agent",
                "/keys/agent",
            )


class TestGuestNetworkDefaults(unittest.TestCase):
    @patch("lib.proxmox_guest._ssh_run")
    def test_gateway_prefers_default_route_on_selected_bridge(self, mock_run) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "default via 10.0.0.1 dev vmbr0 proto static\n"
                "2: vmbr0 inet 10.0.0.10/24 scope global vmbr0\n"
            ),
            stderr="",
        )

        gateway = _get_guest_gateway(
            "10.0.0.10",
            "root",
            [],
            "vmbr0",
            ipaddress.IPv4Interface("10.0.0.50/24"),
        )

        self.assertEqual(gateway, "10.0.0.1")

    @patch("lib.proxmox_guest._ssh_run")
    def test_gateway_uses_bridge_address_for_isolated_network(self, mock_run) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "default via 10.0.0.1 dev vmbr0 proto static\n"
                "3: sdn-private inet 172.20.0.254/24 scope global sdn-private\n"
            ),
            stderr="",
        )

        gateway = _get_guest_gateway(
            "10.0.0.10",
            "root",
            [],
            "sdn-private",
            ipaddress.IPv4Interface("172.20.0.50/24"),
        )

        self.assertEqual(gateway, "172.20.0.254")

    @patch("lib.proxmox_guest._ssh_run")
    def test_dns_prefers_node_values_and_falls_back_to_gateway(self, mock_run) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="127.0.0.53\n",
            stderr="",
        )

        nameservers = _get_host_nameservers(
            "10.0.0.10",
            "root",
            [],
            bridge="sdn-private",
            fallback_gateway="172.20.0.254",
        )

        self.assertEqual(nameservers, ["172.20.0.254"])
        self.assertIn("resolvectl dns sdn-private", mock_run.call_args.args[3])


class TestProbeProxmoxHost(unittest.TestCase):
    @patch("lib.proxmox_guest._ssh_run")
    def test_discovers_defaults_and_storage_content(self, mock_run) -> None:
        mock_run.side_effect = [
            MagicMock(stdout="vmbr1\nvmbr0\n", returncode=0, stderr=""),
            MagicMock(
                stdout=(
                    "Name         Type     Status\n"
                    "local        dir      active\n"
                    "local-lvm    lvmthin  active\n"
                    "backup       dir      disabled\n"
                ),
                returncode=0,
                stderr="",
            ),
            MagicMock(stdout="Name Type Status\nlocal-lvm lvmthin active\n", returncode=0, stderr=""),
            MagicMock(stdout="Name Type Status\nlocal-lvm lvmthin active\n", returncode=0, stderr=""),
            MagicMock(stdout="Name Type Status\nlocal dir active\n", returncode=0, stderr=""),
            MagicMock(stdout="pve1\n", returncode=0, stderr=""),
            MagicMock(stdout="10.0.0.1\n", returncode=0, stderr=""),
            MagicMock(stdout="1.1.1.1\n8.8.8.8\n", returncode=0, stderr=""),
        ]

        facts = probe_proxmox_host("10.0.0.10", "root")

        self.assertEqual(facts.node_name, "pve1")
        self.assertEqual(facts.default_bridge, "vmbr0")
        self.assertEqual(facts.default_root_storage, "local-lvm")
        self.assertEqual(facts.default_template_storage, "local")
        self.assertEqual(facts.gateway, "10.0.0.1")
        self.assertEqual(facts.nameservers, ["1.1.1.1", "8.8.8.8"])
        self.assertEqual(facts.bridges, ["vmbr0", "vmbr1"])
        self.assertIn(
            "ip -o link show type bridge",
            mock_run.call_args_list[0].args[3],
        )
        self.assertEqual(
            {pool.name: pool.content for pool in facts.storage_pools},
            {
                "local": ["vztmpl"],
                "local-lvm": ["images", "rootdir"],
                "backup": [],
            },
        )

    def test_dry_run_returns_sample_defaults(self) -> None:
        facts = probe_proxmox_host("10.0.0.10", "root", dry_run=True)

        self.assertEqual(facts.default_bridge, "vmbr0")
        self.assertEqual(facts.default_root_storage, "local-lvm")
        self.assertEqual(facts.default_template_storage, "local")


class TestProbeProxmoxCluster(unittest.TestCase):
    def test_parse_corosync_config_extracts_nodes(self) -> None:
        cluster_name, members = _parse_corosync_config(
            """
            totem {
              cluster_name: homelab
            }
            nodelist {
              node {
                name: pve1
                ring0_addr: 10.0.0.10
              }
              node {
                name: pve2
                ring0_addr: 10.0.0.11
              }
            }
            """
        )

        self.assertEqual(cluster_name, "homelab")
        self.assertEqual(
            members,
            [("pve1", "10.0.0.10"), ("pve2", "10.0.0.11")],
        )

    @patch("lib.proxmox_guest.probe_proxmox_host")
    @patch("lib.proxmox_guest._get_corosync_config")
    def test_cluster_probe_discovers_each_node(self, mock_corosync, mock_probe_host) -> None:
        mock_corosync.return_value = """
        nodelist {
          node {
            name: pve1
            ring0_addr: 10.0.0.10
          }
          node {
            name: pve2
            ring0_addr: 10.0.0.11
          }
        }
        """
        mock_probe_host.side_effect = [
            MagicMock(
                node_name="pve1",
                default_root_storage="local-lvm",
                default_template_storage="local",
                default_bridge="vmbr0",
            ),
            MagicMock(
                node_name="pve2",
                default_root_storage="shared-lvm",
                default_template_storage="local",
                default_bridge="vmbr1",
            ),
        ]

        hosts = probe_proxmox_cluster(
            "10.0.0.10",
            user="root",
            hosted_key="/keys/proxmox",
            tags=["prod"],
        )

        self.assertEqual([host.name for host in hosts], ["pve1", "pve2"])
        self.assertEqual([host.address for host in hosts], ["10.0.0.10", "10.0.0.11"])
        self.assertEqual(hosts[0].tags, ["prod"])
        self.assertEqual(hosts[1].default_storage, "shared-lvm")


if __name__ == "__main__":
    unittest.main()
