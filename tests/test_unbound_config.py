"""Private recursion, local record validation and collision behavior."""

from __future__ import annotations

from dataclasses import replace
import unittest

from lib.headscale_dns_binding import DNSBinding
from lib.unbound_config import HEALTH_NAME, LocalRecord, health_identity, parse_local_records, records_document, render_unbound


class UnboundConfigTests(unittest.TestCase):
    def setUp(self):
        self.binding = DNSBinding("https://vpn.example.com", "7", ("100.64.0.2", "fd7a:115c:a1e0::2"))
        self.record = LocalRecord("lan.example.net", "files.lan.example.net", "A", "192.0.2.5")

    def parse(self, *records):
        return parse_local_records(records_document(records), forbidden_domains=("mesh.example.net", "vpn.example.com"))

    def test_private_listener_and_recursive_dnssec_defaults(self):
        content = render_unbound(self.binding)
        self.assertIn("interface: 100.64.0.2", content)
        self.assertIn("interface: fd7a:115c:a1e0::2", content)
        self.assertNotIn("interface: 0.0.0.0", content)
        self.assertNotIn("forward-addr:", content)
        self.assertNotIn("forward-zone:", content)
        self.assertIn('module-config: "validator iterator"', content)
        self.assertIn("val-permissive-mode: no", content)
        self.assertIn("log-queries: no", content)
        self.assertIn("control-enable: no", content)
        self.assertIn("ip-freebind: yes", content)
        self.assertIn("access-control: 0.0.0.0/0 refuse", content)
        self.assertIn("access-control: ::/0 refuse", content)

    def test_health_is_specific_to_network_node_and_addresses_not_publication(self):
        digest = health_identity(self.binding)
        self.assertEqual(digest, health_identity(replace(self.binding, addresses=tuple(reversed(self.binding.addresses)))))
        self.assertNotEqual(digest, health_identity(replace(self.binding, node_id="8")))
        self.assertNotEqual(digest, health_identity(replace(self.binding, network_url="https://other.example.com")))
        line = next(line for line in render_unbound(self.binding).splitlines() if HEALTH_NAME in line)
        self.assertIn('TXT "' + digest + '"', line)
        self.assertNotIn("\\", line)
        self.assertTrue(line.endswith('"\''))

    def test_normalized_records_render_as_data(self):
        records = self.parse(self.record, LocalRecord("lan.example.net.", "alias.lan.example.net.",
                                                     "CNAME", "files.lan.example.net."))
        self.assertEqual(records[0].name, "alias.lan.example.net.")
        content = render_unbound(self.binding, records, forbidden_domains=("mesh.example.net",))
        self.assertIn('local-zone: "lan.example.net." static', content)
        self.assertIn("files.lan.example.net. 300 IN A 192.0.2.5", content)

    def test_rejects_configuration_injection_collisions_and_invalid_records(self):
        invalid = [replace(self.record, zone="mesh.example.net", name="x.mesh.example.net"),
                   replace(self.record, zone="example.com", name="x.example.com"),
                   replace(self.record, name="elsewhere.example.net"),
                   replace(self.record, value='192.0.2.5\nforward-zone:'),
                   replace(self.record, name='x.lan.example.net"'),
                   replace(self.record, rrtype="TXT"), replace(self.record, rrtype=[]),
                   replace(self.record, value="2001:db8::1"), replace(self.record, ttl=True),
                   replace(self.record, zone="basaltwater-resolver.invalid", name="x.basaltwater-resolver.invalid")]
        for record in invalid:
            with self.subTest(record=record), self.assertRaises(ValueError):
                self.parse(record)

    def test_duplicate_cname_and_alias_loops_rejected(self):
        for records in ((self.record, self.record),
                        (self.record, replace(self.record, rrtype="CNAME", value="other.lan.example.net")),
                        (replace(self.record, rrtype="CNAME", value="alias.lan.example.net"),
                         replace(self.record, name="alias.lan.example.net", rrtype="CNAME", value=self.record.name))):
            with self.subTest(records=records), self.assertRaises(ValueError):
                self.parse(*records)
