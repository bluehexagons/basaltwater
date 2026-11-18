"""Ordered UFW ingress checks against synthetic IPv4 and IPv6 filter chains."""

from __future__ import annotations

import unittest

from lib.ufw_ingress import verify_filter_ingress


class TestUfwIngress(unittest.TestCase):
    def verify(self, rules: str, *, family: int = 4, sources=None, peer=None, preflight=False):
        baseline = """*filter
:INPUT DROP [0:0]
:ufw-before-input - [0:0]
:ufw-user-input - [0:0]
:ufw-user-limit - [0:0]
:ufw-user-limit-accept - [0:0]
-A INPUT -j ufw-before-input
-A ufw-before-input -i lo -j ACCEPT
-A ufw-before-input -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
"""
        verify_filter_ingress(
            baseline + rules + "\nCOMMIT\n", family=family, protocol="tcp", port=22,
            sources=sources if sources is not None else ["192.0.2.0/24"],
            peer=peer, before_changes=preflight,
        )

    def test_disallowed_subnet_matches_protocol_neutral_port_range_in_earlier_chain(self):
        with self.assertRaisesRegex(RuntimeError, "outside the requested policy.*20:30"):
            self.verify("-A ufw-before-input -s 198.51.100.0/24 -p tcp -m multiport --dports 20:30,443 -j ACCEPT")

    def test_compiled_limit_rule_in_ipv6_table_cannot_widen_sources(self):
        with self.assertRaisesRegex(RuntimeError, "UFW IPv6 TCP 22.*outside"):
            self.verify("""-A ufw-before-input -j ufw-user-input
-A ufw-user-input -s fd00:fc::/64 -p tcp --dport 22 -j ufw-user-limit-accept
-A ufw-user-limit-accept -j ACCEPT""", family=6, sources=["fd00:ab::/64"])

    def test_order_allows_peer_and_drops_other_sources_before_global_allow(self):
        self.verify("""-A ufw-before-input -j ufw-user-input
-A ufw-user-input -s 192.0.2.0/24 -p tcp --dport 22 -j ACCEPT
-A ufw-user-input -p tcp --dport 22 -j DROP
-A INPUT -p tcp --dport 22 -j ACCEPT""", peer=("192.0.2.5", "192.0.2.10"))

    def test_earlier_conditional_deny_can_block_current_peer(self):
        rules = """-A ufw-before-input -i eth0 -s 192.0.2.5/32 -d 192.0.2.10/32 -p tcp --dport 22 -j DROP
-A ufw-before-input -j ufw-user-input
-A ufw-user-input -s 192.0.2.0/24 -p tcp --dport 22 -j ACCEPT"""
        with self.assertRaisesRegex(RuntimeError, "can block the current SSH peer"):
            self.verify(rules, peer=("192.0.2.5", "192.0.2.10"), preflight=True)
        self.verify(rules, peer=("192.0.2.5", "192.0.2.11"))

    def test_preflight_can_see_deny_after_legacy_rate_limit(self):
        with self.assertRaisesRegex(RuntimeError, "can block the current SSH peer"):
            self.verify("""-A ufw-before-input -j ufw-user-input
-A ufw-user-input -p tcp --dport 22 -j ufw-user-limit-accept
-A ufw-user-input -s 192.0.2.5/32 -j REJECT
-A ufw-user-limit-accept -j ACCEPT""", peer=("192.0.2.5", "192.0.2.10"), preflight=True)

    def test_unknown_selector_does_not_hide_later_allow(self):
        with self.assertRaisesRegex(RuntimeError, "outside the requested policy"):
            self.verify("""-A ufw-before-input -m set --match-set good-peers src -j DROP
-A INPUT -p tcp --dport 22 -j ACCEPT""")
