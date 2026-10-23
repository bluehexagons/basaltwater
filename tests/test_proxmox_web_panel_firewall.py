"""Native node firewall reconciliation preserves all operator-owned policy."""

from __future__ import annotations

import hashlib
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from lib.config import SetupConfig
from security import proxmox_web_panel_firewall as firewall


class ProxmoxPanelFirewallTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rules = [{"type": "in", "action": "DROP", "comment": "operator rule"}]
        self.commands: list[list[str]] = []
        self.fail_create = False
        self.enterContext(patch.object(firewall, "is_dry_run", return_value=False))
        self.enterContext(patch.object(firewall.socket, "gethostname", return_value="pve1.example.test"))
        self.runner = self.enterContext(patch.object(firewall, "run", side_effect=self.run_api))

    def run_api(self, command: list[str], **kwargs: object) -> SimpleNamespace:
        self.commands.append(command)
        self.assertEqual(command[0], "pvesh")
        self.assertTrue(command[2].startswith("/nodes/pve1/firewall/rules"))
        self.assertEqual(kwargs["timeout"], 30)
        digest = hashlib.sha1(json.dumps(self.rules, sort_keys=True).encode()).hexdigest()
        if command[1] == "get":
            data = [{**rule, "pos": pos, "digest": digest} for pos, rule in enumerate(self.rules)]
            return SimpleNamespace(stdout=json.dumps(data))
        if command[1] == "create":
            if self.fail_create:
                raise RuntimeError("API create failed")
            spec = dict(zip((key.removeprefix("--") for key in command[3::2]), command[4::2]))
            spec["enable"] = int(spec["enable"])
            self.rules.insert(0, spec)
        elif command[1] == "delete":
            self.assertEqual(command[3:], ["--digest", digest])
            self.rules.pop(int(command[2].rsplit("/", 1)[1]))
        else:
            self.fail(f"Unexpected native API operation: {command}")
        return SimpleNamespace(stdout="")

    def configure(self, **overrides: object) -> None:
        firewall.configure_proxmox_web_panel_firewall(SetupConfig(**{
            "host": "pve1", "username": "root", "system_type": "server_proxmox",
            "web_panel_port": 9443, "enable_ssl": True, **overrides}))

    def test_scoped_dual_stack_allows_and_rerun_are_idempotent(self) -> None:
        self.configure(access_sources=["192.0.2.0/24", "2001:db8::/64"])
        owned = [rule for rule in self.rules if rule.get("comment") == firewall._RULE_COMMENT]
        self.assertEqual({(rule["dport"], rule["source"]) for rule in owned},
                         {(port, source) for port in ("9443", "8442")
                          for source in ("192.0.2.0/24", "2001:db8::/64")})
        self.assertEqual(self.rules[-1]["comment"], "operator rule")
        self.commands.clear()
        self.configure(access_sources=["192.0.2.0/24", "2001:db8::/64"])
        self.assertTrue(all(command[1] == "get" for command in self.commands))

    def test_reconfiguration_and_removal_preserve_operator_rules(self) -> None:
        self.configure(access_sources=["192.0.2.0/24"])
        self.configure(web_panel_port=9080, enable_ssl=False, access_sources=["198.51.100.3"])
        self.assertEqual(len(self.rules), 2)
        self.assertEqual(self.rules[0]["dport"], "9080")
        self.assertEqual(self.rules[0]["source"], "198.51.100.3")
        self.rules.insert(0, {"comment": firewall._RULE_COMMENT + " operator copy", "action": "ACCEPT"})
        firewall.remove_proxmox_web_panel_firewall()
        self.assertEqual([rule["comment"] for rule in self.rules],
                         [firewall._RULE_COMMENT + " operator copy", "operator rule"])
        firewall.remove_proxmox_web_panel_firewall()

    def test_duplicates_removed_and_unrestricted_source_is_omitted(self) -> None:
        self.configure(enable_ssl=False)
        self.assertNotIn("source", self.rules[0])
        self.rules.insert(0, dict(self.rules[0]))
        self.configure(enable_ssl=False)
        self.assertEqual(len(self.rules), 2)

    def test_create_failure_keeps_previous_allows(self) -> None:
        self.configure()
        previous = list(self.rules)
        self.fail_create = True
        with self.assertRaisesRegex(RuntimeError, "API create failed"):
            self.configure(web_panel_port=9444)
        self.assertEqual(self.rules, previous)

    def test_invalid_positions_and_missing_digest_refuse_deletion(self) -> None:
        for rules in ([{"pos": 1, "comment": firewall._RULE_COMMENT}],
                      [{"pos": 0, "comment": firewall._RULE_COMMENT, "digest": "unsafe"}]):
            with self.subTest(rules=rules):
                self.runner.side_effect = None
                self.runner.return_value = SimpleNamespace(stdout=json.dumps(rules))
                with self.assertRaises(RuntimeError):
                    firewall.remove_proxmox_web_panel_firewall()
        self.assertTrue(all(command[1] != "delete" for command in self.commands))

    def test_dry_run_does_not_query_or_change_native_policy(self) -> None:
        with patch.object(firewall, "is_dry_run", return_value=True):
            self.configure()
            firewall.remove_proxmox_web_panel_firewall()
        self.runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
