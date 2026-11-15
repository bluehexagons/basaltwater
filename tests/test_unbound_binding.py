"""Resolver publication guards survive reruns and block unsafe client teardown."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from lib.headscale_dns_binding import (
    DNSBinding, read_dns_binding, resolver_lifecycle_guard, save_dns_binding,
)
from lib.state_read import StateReadError


class ResolverBindingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = str(Path(temporary.name) / "resolver" / "binding.json")
        self.binding = DNSBinding("https://vpn.example.com", "nDNS123", ("100.64.0.2",))

    def test_missing_read_creates_nothing_and_compare_write_preserves_identity(self):
        self.assertIsNone(read_dns_binding(self.path))
        self.assertFalse(Path(self.path).parent.exists())
        save_dns_binding(self.binding, expected=None, path=self.path)
        self.assertEqual(self.binding, read_dns_binding(self.path))
        with self.assertRaisesRegex(ValueError, "changed"):
            save_dns_binding(replace(self.binding, node_id="other"), expected=None, path=self.path)
        self.assertEqual(self.binding, read_dns_binding(self.path))

    def test_publication_reservation_blocks_disable_and_leave_until_withdrawal(self):
        advertised = replace(self.binding, advertised=True,
                             publication_id="291b358d-4327-4cc1-9f3c-095ce02e4ec3")
        save_dns_binding(advertised, expected=None, path=self.path)
        entered = False
        with self.assertRaisesRegex(ValueError, "Withdraw or replace"):
            with resolver_lifecycle_guard(self.path):
                entered = True
        self.assertFalse(entered)
        save_dns_binding(self.binding, expected=advertised, path=self.path)
        with resolver_lifecycle_guard(self.path) as current:
            self.assertEqual(self.binding, current)

    def test_corrupt_and_unsafe_state_is_retained(self):
        save_dns_binding(self.binding, expected=None, path=self.path)
        path = Path(self.path)
        path.write_text('{"version":99}')
        before = path.read_bytes()
        with self.assertRaises(StateReadError):
            read_dns_binding(self.path)
        with self.assertRaises(StateReadError):
            save_dns_binding(self.binding, expected=None, path=self.path)
        self.assertEqual(before, path.read_bytes())
        path.chmod(0o644)
        with self.assertRaises(StateReadError):
            read_dns_binding(self.path)

    def test_invalid_network_and_unprotected_publication_are_rejected_before_write(self):
        for binding in (replace(self.binding, addresses=("198.51.100.5",)),
                        replace(self.binding, network_url="https://user:secret@vpn.example.com"),
                        replace(self.binding, advertised=True)):
            with self.subTest(binding=binding), self.assertRaises(ValueError):
                save_dns_binding(binding, expected=None, path=self.path)
        self.assertFalse(Path(self.path).exists())
