"""Workstation firewall behavior, with all system operations mocked."""

from __future__ import annotations

from contextlib import ExitStack
import json
from pathlib import Path
from subprocess import CompletedProcess
import tempfile
import unittest
from unittest.mock import patch

import basaltwater
from common import cachyos_firewall as firewall
from lib.cachyos import cachyos_config_from_args


class FirewallTests(unittest.TestCase):
    def config(self, *options):
        parser, _, _ = basaltwater.create_basaltwater_parser()
        return cachyos_config_from_args(parser.parse_args([
            "setup", "agent_cachyos", "localhost", "human", *options,
        ]))

    def test_explicit_sources_roundtrip_and_no_lan_is_supported(self):
        config = self.config("--lan-access", "--access-source", "192.168.3.4", "10.1.2.0/24")
        self.assertEqual(firewall.access_sources(config), ["10.1.2.0/24", "192.168.3.4/32"])
        self.assertTrue(firewall.firewall_requested(self.config("--no-lan-access")))
        self.assertFalse(firewall.firewall_requested(self.config()))
        for value in ("0.0.0.0/0", "192.0.2.0/24", "::/0", "fd00::/64", "192.168.0.0/8"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.config("--access-source", value)

    def test_discovery_uses_real_default_interface_prefix_and_rejects_ambiguity(self):
        routes = [{"dev": "eth0", "gateway": "10.2.3.1"}]
        addresses = [{"ifname": "eth0", "link_type": "ether", "addr_info": [
            {"local": "10.2.3.4", "prefixlen": 23, "scope": "global"},
        ]}, {"ifname": "docker0", "addr_info": [
            {"local": "172.17.0.1", "prefixlen": 16, "scope": "global"},
        ]}]
        def run(command, **kwargs):
            return CompletedProcess(command, 0, json.dumps(routes if "route" in command else addresses))
        with patch.object(firewall, "run", side_effect=run):
            self.assertEqual(firewall.access_sources(self.config("--lan-access")), ["10.2.2.0/23"])
            routes.append({"dev": "tun0", "gateway": "10.5.0.1"})
            with self.assertRaisesRegex(ValueError, "--access-source"):
                firewall.access_sources(self.config("--lan-access"))

    def test_custom_t3_port_cannot_reopen_protected_services(self):
        for port in (3389, 47984, 47989, 47990, 48010):
            with self.subTest(port=port), self.assertRaisesRegex(ValueError, "protected RDP/Sunshine"):
                self.config("--web-interface", "t3code", "--web-interface-port", str(port), "--lan-access")

    def test_access_flags_do_not_widen_default_web_bind(self):
        for options in (("--lan-access",), ("--access-source", "10.2.3.4")):
            with self.subTest(options=options):
                self.assertEqual(self.config("--web-interface", "t3code", *options).web_interface_host,
                                 "127.0.0.1")

    def test_conflicting_manager_and_ipv6_disabled_stop_preflight(self):
        with patch.object(firewall, "can_manage_firewall", return_value=True), \
                patch.object(firewall, "run", return_value=CompletedProcess([], 0, "active\n")):
            with self.assertRaisesRegex(ValueError, "firewalld"):
                firewall.preflight_firewall(self.config("--no-lan-access"))
        with patch.object(firewall, "can_manage_firewall", return_value=True), \
                patch.object(firewall, "run", return_value=CompletedProcess([], 3, "inactive\n")), \
                patch.object(Path, "exists", return_value=True), \
                patch.object(Path, "read_text", return_value="IPV6=no\n"):
            with self.assertRaisesRegex(ValueError, "IPV6=yes"):
                firewall.preflight_firewall(self.config("--no-lan-access"))

    def test_preview_never_runs_commands_or_creates_state(self):
        with patch.object(firewall, "run") as command, patch.object(firewall.os, "open") as opened:
            firewall.configure_firewall(self.config("--lan-access", "--dry-run"))
        command.assert_not_called()
        opened.assert_not_called()

    def test_broad_rules_restricted_rerun_and_deselection_close_access(self):
        # Model UFW's equivalence behavior: prepend skips matching tuples even
        # if the action/comment differs; non-positional commands replace them.
        rules = []
        calls = []
        for ipv6 in (False, True):
            for protocol, port in firewall.GUARDED:
                rules.append([protocol, port, "Anywhere", ipv6, "allow", "legacy"])

        def render():
            lines = ["Status: active"]
            for index, (proto, port, source, ipv6, action, comment) in enumerate(rules, 1):
                suffix = " (v6)" if ipv6 else ""
                lines.append(f"[{index:2}] {port}/{proto}{suffix}  {action.upper()} IN  {source}{suffix} # {comment}")
            return "\n".join(lines)

        def run(command, **kwargs):
            calls.append(command)
            if "ufw" not in command:
                return CompletedProcess(command, 0, "")
            args = command[command.index("ufw") + 1:]
            if args == ["status", "numbered"]:
                return CompletedProcess(command, 0, render())
            if args[:2] == ["--force", "delete"]:
                if len(args) == 3:
                    rules.pop(int(args[2]) - 1)
                    return CompletedProcess(command, 0, "deleted")
                self.fail("Unexpected exact-rule adoption in fixture")
            if args[0] == "default" or args == ["--force", "enable"]:
                return CompletedProcess(command, 0, "")
            prepend = args[0] == "prepend"
            if prepend:
                args = args[1:]
            action = args[0]
            proto, port = args[args.index("proto") + 1], args[args.index("port") + 1]
            source = args[args.index("from") + 1]
            source = "Anywhere" if source == "any" else source
            comment = args[args.index("comment") + 1]
            skipped = False
            for ipv6 in ((False, True) if source == "Anywhere" else (False,)):
                key = [proto, port, source, ipv6]
                existing = next((i for i, rule in enumerate(rules) if rule[:4] == key), None)
                if existing is not None and prepend:
                    skipped = True
                elif existing is not None:
                    rules[existing] = [*key, action, comment]
                else:
                    rules.insert(0 if prepend else len(rules), [*key, action, comment])
            return CompletedProcess(command, 0, "Skipping inserting existing rule" if skipped else "added")

        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            stack.enter_context(patch("common.cachyos_steps._home", return_value=Path(directory)))
            stack.enter_context(patch("common.cachyos_steps.install_missing_packages"))
            stack.enter_context(patch.object(firewall, "preflight_firewall"))
            stack.enter_context(patch.object(firewall, "is_dry_run", return_value=False))
            stack.enter_context(patch.object(firewall, "run", side_effect=run))
            for source in ("192.168.1.0/24", "10.3.0.0/24"):
                config = self.config("--t3code-desktop", "--sunshine", "--access-source", source)
                firewall.configure_firewall(config)
                allows = [rule for rule in rules if rule[4] == "allow"]
                self.assertTrue(all(rule[2] == source and not rule[3] for rule in allows))
                self.assertNotIn("47990", [rule[1] for rule in allows])
                self.assertNotIn("3389", [rule[1] for rule in allows])
                self.assertEqual(len(allows), 5)
                self.assertEqual(len(rules), len(firewall.GUARDED) * 2 + 5)
            firewall.configure_firewall(self.config("--no-lan-access"))
            self.assertFalse(any(rule[4] == "allow" for rule in rules))
        self.assertFalse(any("reset" in call for call in calls))
        self.assertIn(["sudo", "-n", "systemctl", "enable", "--now", "ufw.service"], calls)

    def test_earlier_unmanaged_allow_cannot_be_reported_as_protected(self):
        status = "\n".join([
            "Status: active", "[ 1] Anywhere ALLOW IN Anywhere",
            "[ 2] 3773/tcp DENY IN Anywhere # basaltwater-cachyos:guard",
            "[ 3] 3773/tcp (v6) DENY IN Anywhere (v6) # basaltwater-cachyos:guard",
        ])
        with self.assertRaisesRegex(RuntimeError, "earlier unmanaged"):
            firewall.verify_rule_order(status, {("tcp", "3773")})

    def test_command_failure_never_opens_new_sources(self):
        with patch.object(firewall, "preflight_firewall"), \
                patch("common.cachyos_steps.install_missing_packages"), \
                patch.object(firewall, "run", side_effect=[CompletedProcess([], 0, ""), RuntimeError("fixture")]) as run:
            with self.assertRaises(RuntimeError):
                firewall._configure_firewall(self.config("--t3code-desktop", "--access-source", "10.1.2.3"))
            self.assertFalse(any("allow" in call.args[0] for call in run.call_args_list))


if __name__ == "__main__":
    unittest.main()
