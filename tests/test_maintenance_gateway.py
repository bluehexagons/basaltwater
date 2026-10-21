"""Gateway maintenance findings cover both inventories without mutating them."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from lib import maintenance_gateway as gateway


class MaintenanceGatewayTest(unittest.TestCase):
    def collect(self, previews: object, forwards: object) -> tuple[list[dict], Mock]:
        issues = []
        command = Mock(side_effect=[
            SimpleNamespace(returncode=0, stdout=json.dumps({"previews": previews})),
            SimpleNamespace(returncode=0, stdout=json.dumps({"forwards": forwards})),
        ])
        with patch.object(gateway.os.path, "isfile", return_value=True):
            gateway.collect_gateway_checks(lambda key, message: issues.append({"key": key, "message": message}), command)
        return issues, command

    def test_two_dead_legacy_forwards_produce_exact_owner_scoped_actions(self) -> None:
        forwards = [{"name": name, "owner": "agent", "ready": False} for name in ("preview-agent-8080", "preview-agent-8081")]
        forwards.append({"name": "healthy", "owner": "other", "ready": True})
        issues, command = self.collect([], forwards)
        self.assertEqual(len(issues), 2)
        for issue, row in zip(issues, forwards):
            self.assertEqual(issue["key"], "gateway-forward:agent:" + row["name"])
            self.assertIn("owner agent", issue["message"])
            self.assertIn("after retirement approval", issue["message"])
            self.assertIn("basaltwater-web forward remove " + row["name"] + " --json", issue["message"])
        self.assertEqual([call.args[0] for call in command.call_args_list], [
            [gateway._CLI, kind, "list", "--json"] for kind in ("preview", "forward")])

    def test_preview_lifecycle_is_used_for_inactive_and_active_unreachable_previews(self) -> None:
        previews = [{"name": name, "owner": "agent", "active": active} for name, active in (("stopped", False), ("hung", True))]
        forwards = [{"name": "hung", "owner": "agent", "ready": False}]
        issues, _ = self.collect(previews, forwards)
        self.assertEqual(len(issues), 2)
        self.assertTrue(all(issue["key"].startswith("gateway-preview:") for issue in issues))
        self.assertTrue(all("preview stop" in issue["message"] and "forward remove" not in issue["message"] for issue in issues))

    def test_missing_installation_performs_no_gateway_probes(self) -> None:
        command = Mock()
        with patch.object(gateway.os.path, "isfile", return_value=False):
            gateway.collect_gateway_checks(Mock(), command)
        command.assert_not_called()

    def test_malformed_inventory_is_a_finding_and_never_becomes_a_command(self) -> None:
        for bad in (None, {}, [{"name": "x;touch bad", "owner": "agent", "ready": False}],
                    [{"name": "x", "owner": "bad owner", "ready": False}],
                    [{"name": "x", "owner": "agent", "ready": "false"}]):
            with self.subTest(bad=bad):
                issues, _ = self.collect([], bad)
                self.assertEqual([issue["key"] for issue in issues], ["gateway-unavailable:forward"])
                self.assertNotIn("touch bad", issues[0]["message"])

    def test_partial_preview_inventory_cannot_recommend_removing_an_unknown_lifecycle(self) -> None:
        previews = [{"name": "p" + str(index), "owner": "agent", "active": True} for index in range(gateway._LIMIT + 1)]
        issues, _ = self.collect(previews, [{"name": "unknown", "owner": "agent", "ready": False}])
        self.assertIn("gateway-partial:preview", [issue["key"] for issue in issues])
        self.assertIn("establish its lifecycle", issues[-1]["message"])
        self.assertNotIn("forward remove", issues[-1]["message"])

    def test_unavailable_preview_inventory_keeps_forward_evidence_without_guessing_lifecycle(self) -> None:
        issues, _ = self.collect(None, [{"name": "unknown", "owner": "agent", "ready": False}])
        self.assertEqual(len(issues), 2)
        self.assertIn("unreachable", issues[-1]["message"])
        self.assertNotIn("forward remove", issues[-1]["message"])


if __name__ == "__main__":
    unittest.main()
