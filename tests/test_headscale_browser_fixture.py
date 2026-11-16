"""Synthetic browser adapters cannot dispatch native Headscale mutations."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from tests.browser.fixtures import SyntheticHeadscaleBroker


class HeadscaleBrowserFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.broker = SyntheticHeadscaleBroker()
        self.broker.review_origin = "https://review.example.test"

    def test_register_keeps_exact_input_transient_and_review_metadata_secret_free(self) -> None:
        secret = "fixture_exact_AuthID_123"
        with patch("lib.headscale_portal.native", side_effect=AssertionError("native")), \
                patch("subprocess.run", side_effect=AssertionError("command")):
            row = self.broker({"action": "headscale-register", "user": "12", "auth_id": secret})
            self.assertEqual(row["state"], "pending")
            self.assertNotIn(secret, json.dumps(self.broker({"action": "list"})))
            self.assertEqual(self.broker.registrations.consume(row), secret)
            self.assertEqual(self.broker.registrations.entries, {})
            for action in ("decide", "execute", "native", "issue-key"):
                with self.assertRaises(ValueError):
                    self.broker({"action": action, "id": row["id"]})

    def test_fixed_actions_leave_inventory_unchanged_and_reject_unknown_operations(self) -> None:
        before = self.broker({"action": "headscale-inspect"})
        for parameters in ({"action": "users_create", "name": "alice"},
                           {"action": "users_delete", "id": "13"},
                           {"action": "node_expire", "id": "7"},
                           {"action": "node_delete", "id": "7"}):
            row = self.broker({"action": "request", "operation": "headscale.admin", "parameters": parameters})
            self.assertEqual(row["plan"]["parameters"], parameters)
            self.assertEqual(row["plan"]["argv"], [])
            self.assertEqual(self.broker({"action": "status", "id": row["id"]}), row)
        self.assertEqual(self.broker({"action": "headscale-inspect"}), before)
        with self.assertRaises(ValueError):
            self.broker({"action": "request", "operation": "command.run", "parameters": {}})
        with self.assertRaises(ValueError):
            self.broker({"action": "headscale-register", "user": "99", "auth_id": "synthetic-secret"})
        self.assertEqual(self.broker.registrations.entries, {})


if __name__ == "__main__":
    unittest.main()
