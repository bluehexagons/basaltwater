"""Tests for concise UFW command reporting."""

from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib.config import SetupConfig
from security.security_steps import (
    _run_ufw,
    _ufw_failure,
    configure_firewall,
    configure_firewall_ssh_only,
    configure_firewall_web,
)


_FILTERING_STATUS = "Status: active\nDefault: deny (incoming), allow (outgoing), disabled (routed)\n"


class TestFirewallOutput(unittest.TestCase):
    @patch("security.security_steps.run")
    def test_successful_ufw_output_is_captured(self, mock_run) -> None:
        mock_run.return_value = SimpleNamespace(returncode=0, stdout="Rule added\n", stderr="")

        _run_ufw("ufw allow ssh")

        mock_run.assert_called_once_with(
            "ufw allow ssh",
            check=False,
            capture_output=True,
        )

    def test_failed_ufw_output_remains_actionable(self) -> None:
        error = _ufw_failure(
            "Failed to install rule",
            SimpleNamespace(stdout="", stderr="permission denied\n"),
        )

        self.assertEqual(str(error), "Failed to install rule: permission denied")


class TestRestrictedFirewallVerification(unittest.TestCase):
    def setUp(self) -> None:
        self.enterContext(patch.dict(os.environ, {"SSH_CONNECTION": ""}))
        self.command = self.enterContext(patch("security.security_steps.run", side_effect=self.run_command))
        self.status = _FILTERING_STATUS
        self.numbered_status = "Status: active\n"
        self.query_failed = False
        self.delete_failed = False
        self.config = SetupConfig(
            host="192.168.0.44", username="agent", system_type="server_lite",
            access_sources=["192.168.0.0/24"], web_ports=[443], default_web_ports=False,
        )

    def run_command(self, command: str, **_kwargs: object) -> SimpleNamespace:
        if command == "ufw status verbose":
            return SimpleNamespace(returncode=0, stdout=self.status, stderr="")
        if command == "ufw status numbered":
            return SimpleNamespace(
                returncode=1 if self.query_failed else 0,
                stdout=self.numbered_status, stderr="query failed" if self.query_failed else "",
            )
        if self.delete_failed and command.startswith("ufw --force delete "):
            return SimpleNamespace(returncode=1, stdout="", stderr="delete failed")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def test_permissive_default_stops_all_entrypoints_before_mutations(self) -> None:
        self.status = _FILTERING_STATUS.replace("deny (incoming)", "allow (incoming)")
        for configure in (configure_firewall, configure_firewall_web, configure_firewall_ssh_only):
            with self.subTest(configure=configure.__name__):
                self.command.reset_mock()
                with self.assertRaisesRegex(RuntimeError, "requires a deny or reject incoming default"):
                    configure(self.config)
                self.assertTrue(all(call.args[0].startswith("ufw status") for call in self.command.call_args_list))

    def test_missing_or_inactive_verbose_status_cannot_verify_restrictions(self) -> None:
        for status in ("", "Status: inactive\n", "Status: active\n"):
            with self.subTest(status=status):
                self.status = status
                with self.assertRaises(RuntimeError):
                    configure_firewall(self.config)

    def test_reject_default_is_supported(self) -> None:
        self.status = _FILTERING_STATUS.replace("deny (incoming)", "reject (incoming)")
        configure_firewall(self.config)

    def test_rdp_only_sources_also_require_a_filtering_default(self) -> None:
        self.config.access_sources = []
        self.config.enable_rdp = True
        self.config.rdp_allowed_sources = ["192.168.0.0/24"]
        self.status = _FILTERING_STATUS.replace("deny (incoming)", "allow (incoming)")
        with self.assertRaisesRegex(RuntimeError, "requires a deny or reject incoming default"):
            configure_firewall(self.config)

    def test_broad_rules_covering_restricted_ports_are_rejected(self) -> None:
        for destination in (
            "Anywhere", "Anywhere/tcp", "Anywhere (v6)", "22,443/tcp",
            "20:30/tcp", "20:21,22,80:90/tcp", "22/tcp on eth0",
            "192.168.0.44 22/tcp", "192.168.0.44/tcp", "192.168.0.0/24",
            "22/tcp (OpenSSH)", "22/tcp (OpenSSH (v6))", "80,443/tcp (Nginx Full)",
        ):
            with self.subTest(destination=destination):
                self.status = _FILTERING_STATUS + f"{destination} ALLOW IN Anywhere\n"
                with self.assertRaisesRegex(RuntimeError, "still has a broad UFW rule"):
                    configure_firewall(self.config)

    def test_unrelated_or_local_rules_are_preserved(self) -> None:
        self.status += "\n".join((
            "53/udp ALLOW IN Anywhere", "20:21/tcp ALLOW IN Anywhere",
            "23:80/tcp ALLOW IN Anywhere", "Anywhere on lo ALLOW IN Anywhere",
            "127.0.0.1/tcp ALLOW IN Anywhere", "::1 ALLOW IN Anywhere (v6)",
            "127.0.0.1 22/tcp ALLOW IN Anywhere", "::1 22/tcp ALLOW IN Anywhere (v6)",
            "22/tcp ALLOW IN 192.168.0.0/24", "22/tcp DENY IN Anywhere",
            "22/tcp ALLOW OUT Anywhere", "Anywhere/udp ALLOW IN Anywhere/udp",
        ))
        configure_firewall(self.config)

    def test_unknown_verbose_destination_does_not_pass_silently(self) -> None:
        self.status += "Unrecognized-profile ALLOW IN Anywhere\n"
        with self.assertRaisesRegex(RuntimeError, "Could not verify UFW destination"):
            configure_firewall(self.config)

    def test_failed_inventory_stops_obsolete_rule_reconciliation(self) -> None:
        self.query_failed = True
        with self.assertRaisesRegex(RuntimeError, "inspect obsolete managed firewall rules.*query failed"):
            configure_firewall(self.config)

    def test_failed_removal_cannot_claim_source_reconciliation_succeeded(self) -> None:
        self.numbered_status += (
            "[ 1] 22/tcp ALLOW IN 10.0.0.0/8 # basaltwater SSH trusted source 10.0.0.0/8\n"
        )
        self.delete_failed = True
        with self.assertRaisesRegex(RuntimeError, "remove obsolete managed firewall rule 1.*delete failed"):
            configure_firewall(self.config)

    def test_verbose_queries_use_a_stable_locale(self) -> None:
        configure_firewall(self.config)
        queries = [call for call in self.command.call_args_list if call.args[0] == "ufw status verbose"]
        self.assertTrue(queries)
        self.assertTrue(all(call.kwargs["env"]["LC_ALL"] == "C" for call in queries))

    def test_enabling_ufw_prunes_previously_hidden_rules_and_preserves_operator_rules(self) -> None:
        for configure in (configure_firewall, configure_firewall_web, configure_firewall_ssh_only):
            with self.subTest(configure=configure.__name__):
                active = False
                rules = [
                    "22/tcp ALLOW IN 10.0.0.0/8 # basaltwater SSH trusted source 10.0.0.0/8",
                    "443/tcp ALLOW IN 10.0.0.0/8 # basaltwater web TCP 443 source 10.0.0.0/8",
                    "22/tcp ALLOW IN 198.51.100.10 # operator rule",
                ]

                def run_command(command: str, **kwargs: object) -> SimpleNamespace:
                    nonlocal active
                    if command.startswith("ufw status 2>"):
                        return SimpleNamespace(returncode=0 if active else 1, stdout="", stderr="")
                    if command == "ufw status numbered":
                        status = "Status: active\n" + "\n".join(
                            f"[ {i}] {rule}" for i, rule in enumerate(rules, 1)
                        ) if active else "Status: inactive\n"
                        return SimpleNamespace(returncode=0, stdout=status, stderr="")
                    if command == "ufw --force enable":
                        active = True
                    elif command.startswith("ufw --force delete "):
                        del rules[int(command.rsplit(" ", 1)[1]) - 1]
                    return self.run_command(command, **kwargs)

                self.command.side_effect = run_command
                configure(self.config)
                self.assertTrue(active)
                self.assertEqual(rules[-1], "22/tcp ALLOW IN 198.51.100.10 # operator rule")
                if configure is configure_firewall_ssh_only:
                    self.assertEqual(len(rules), 2)  # SSH-only setup does not own web rules.
                else:
                    self.assertEqual(len(rules), 1)


if __name__ == "__main__":
    unittest.main()
