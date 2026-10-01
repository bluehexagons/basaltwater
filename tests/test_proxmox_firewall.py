"""Native management firewall verification and failure-order regressions."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import unittest
from unittest.mock import patch
from urllib.parse import unquote

from lib.config import SetupConfig
from security.security_steps import configure_proxmox_management_firewall


class TestNativeManagementFirewall(unittest.TestCase):
    def setUp(self) -> None:
        self.enterContext(patch.dict(os.environ, {}, clear=True))
        self.enterContext(patch("security.security_steps.is_dry_run", return_value=False))
        self.cluster = {"enable": 0}
        self.node = {}
        self.entries = [{"cidr": "172.16.0.0/12", "comment": "basaltwater access source 172.16.0.0/12"}]
        self.fail_command = ""
        self.fail_after_activation = False
        self.persist_additions = True
        self.command = self.enterContext(patch("security.security_steps.run", side_effect=self._run))
        self.config = SetupConfig(host="pve", username="root", system_type="server_proxmox", access_sources=["192.168.1.0/24"])

    def _run(self, command: str, **_kwargs) -> subprocess.CompletedProcess[str]:
        if command == self.fail_command or (
            self.fail_after_activation and self.cluster["enable"] and command.startswith("systemctl is-active")
        ):
            return subprocess.CompletedProcess([], 1, "", "failure")
        stdout = ""
        if command == "pvesh get /cluster/firewall/options --output-format json":
            stdout = json.dumps(self.cluster)
        elif "/options --output-format" in command:
            stdout = json.dumps(self.node)
        elif command.startswith("pvesh get /cluster/firewall/ipset/management"):
            stdout = json.dumps(self.entries)
        elif command.startswith("pvesh create /cluster/firewall/ipset/management") and self.persist_additions:
            args = shlex.split(command)
            self.entries.append({"cidr": args[args.index("--cidr") + 1], "comment": args[args.index("--comment") + 1]})
        elif command.startswith("pvesh delete /cluster/firewall/ipset/management/"):
            cidr = unquote(command.split("/management/", 1)[1])
            self.entries[:] = [entry for entry in self.entries if entry["cidr"] != cidr]
        elif command.startswith("pvesh set /cluster/firewall/options --enable "):
            self.cluster["enable"] = int(command.split()[-1])
        return subprocess.CompletedProcess([], 0, stdout, "")

    def _mutations(self) -> list[str]:
        return [call.args[0] for call in self.command.call_args_list if call.args[0].startswith(("pvesh set ", "pvesh create ", "pvesh delete "))]

    def test_disabled_node_and_accept_policy_stop_before_mutation(self) -> None:
        for options, field, value in ((self.node, "enable", 0), (self.cluster, "policy_in", "ACCEPT")):
            with self.subTest(field=field):
                options[field] = value
                self.command.reset_mock()
                with self.assertRaises(RuntimeError):
                    configure_proxmox_management_firewall(self.config)
                self.assertEqual(self._mutations(), [])
                del options[field]

    def test_inactive_backend_and_compile_failure_stop_before_mutation(self) -> None:
        for command in ("systemctl is-active --quiet pve-firewall", "pve-firewall compile"):
            with self.subTest(command=command):
                self.command.reset_mock()
                self.fail_command = command
                with self.assertRaises(RuntimeError):
                    configure_proxmox_management_firewall(self.config)
                self.assertEqual(self._mutations(), [])

    def test_legacy_compile_warning_is_not_success_despite_zero_exit(self) -> None:
        original = self._run
        self.command.side_effect = lambda command, **kwargs: (
            subprocess.CompletedProcess([], 0, "", "host.fw: invalid rule")
            if command == "pve-firewall compile" else original(command, **kwargs)
        )
        with self.assertRaisesRegex(RuntimeError, "compilation warnings"):
            configure_proxmox_management_firewall(self.config)
        self.assertEqual(self._mutations(), [])

    def test_existing_nftables_backend_is_verified_without_switching_backends(self) -> None:
        self.node["nftables"] = 1
        configure_proxmox_management_firewall(self.config)
        commands = [call.args[0] for call in self.command.call_args_list]
        self.assertIn("systemctl is-active --quiet proxmox-firewall", commands)
        self.assertIn("/usr/libexec/proxmox/proxmox-firewall compile", commands)
        self.assertFalse(any("--nftables" in command for command in commands))

    def test_unpersisted_addition_prevents_activation_and_preserves_old_access(self) -> None:
        self.persist_additions = False
        with self.assertRaisesRegex(RuntimeError, "did not persist"):
            configure_proxmox_management_firewall(self.config)
        self.assertEqual(self.cluster["enable"], 0)
        self.assertEqual(self.entries[0]["cidr"], "172.16.0.0/12")
        self.assertFalse(any(command.startswith("pvesh delete ") for command in self._mutations()))

    def test_activation_failure_restores_enablement_before_removing_old_access(self) -> None:
        self.fail_after_activation = True
        with self.assertRaisesRegex(RuntimeError, "not active"):
            configure_proxmox_management_firewall(self.config)
        self.assertEqual(self.cluster["enable"], 0)
        self.assertTrue(any(entry["cidr"] == "172.16.0.0/12" for entry in self.entries))
        self.assertFalse(any(command.startswith("pvesh delete ") for command in self._mutations()))

    def test_failed_activation_never_disables_preexisting_cluster_firewall(self) -> None:
        self.cluster["enable"] = 1
        with patch("security.security_steps._check_proxmox_firewall", side_effect=[self.cluster, RuntimeError("postcheck failed")]):
            with self.assertRaisesRegex(RuntimeError, "postcheck failed"):
                configure_proxmox_management_firewall(self.config)
        self.assertEqual(self.cluster["enable"], 1)
        self.assertNotIn("pvesh set /cluster/firewall/options --enable 0", self._mutations())

    def test_current_peer_must_be_allowed_after_stale_sources_are_removed(self) -> None:
        with patch.dict(os.environ, {"SSH_CONNECTION": "172.20.1.2 50000 192.168.1.5 22"}):
            with self.assertRaisesRegex(RuntimeError, "Current SSH peer is outside"):
                configure_proxmox_management_firewall(self.config)
        self.assertEqual(self._mutations(), [])

    def test_retained_operator_source_can_preserve_current_peer(self) -> None:
        self.entries.append({"cidr": "203.0.113.7/32", "comment": "operator"})
        with patch.dict(os.environ, {"SSH_CONNECTION": "203.0.113.7 50000 192.168.1.5 22"}), patch("builtins.print") as output:
            configure_proxmox_management_firewall(self.config)
        self.assertIn("203.0.113.7/32", "\n".join(str(call) for call in output.call_args_list))
        self.assertTrue(any(entry["cidr"] == "203.0.113.7/32" for entry in self.entries))

    def test_canonical_host_prefix_does_not_duplicate_existing_entry(self) -> None:
        self.config.access_sources = ["203.0.113.7"]
        self.entries = [{"cidr": "203.0.113.7/32", "comment": "operator"}]
        configure_proxmox_management_firewall(self.config)
        self.assertFalse(any(command.startswith("pvesh create ") for command in self._mutations()))

    def test_excluded_requested_source_cannot_count_as_allowed(self) -> None:
        self.entries.append({"cidr": "192.168.0.0/16", "nomatch": 1, "comment": "operator exclusion"})
        with self.assertRaisesRegex(RuntimeError, "excluded management"):
            configure_proxmox_management_firewall(self.config)
        self.assertEqual(self._mutations(), [])

    def test_dry_run_issues_no_api_commands(self) -> None:
        with patch("security.security_steps.is_dry_run", return_value=True):
            configure_proxmox_management_firewall(self.config)
        self.command.assert_not_called()
