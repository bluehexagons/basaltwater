"""Runner panel's bounded, sanitized and read-only inspection contract."""

from __future__ import annotations

from http import HTTPStatus
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from common.service_tools import web_panel_github_runner as runner
from common.service_tools import web_panel_features as features
from common.service_tools import web_panel_service as panel
from common.service_tools.web_panel_templates import panel_navigation
from lib.github_runner_config import SERVICE
from tests.test_github_runner import policy


class RunnerPanelTests(unittest.TestCase):
    def test_page_waits_for_explicit_load_and_rejects_unsupported_queries(self) -> None:
        with patch.object(runner, "collect_runner_status") as collect:
            page = runner.render_runner_page(False, "", "host<test>", navigation=())
            collect.assert_not_called()
            self.assertIn("host&lt;test&gt;", page)
        self.assertTrue(runner.parse_runner_query("load=1"))
        for raw in ("load=0", "load=1&load=1", "repository=other/repo", "x" * 65):
            with self.subTest(query=raw), self.assertRaises(ValueError):
                runner.parse_runner_query(raw)

    def test_collector_uses_fixed_unit_and_strips_unknown_fields(self) -> None:
        row = dict(time="2026-10-18T00:00:00+00:00", allowed=False, reason="actor-denied",
                   identity="mallory", event="push", run_id="123", token="never-display")
        with (patch.object(runner, "read_json_file", side_effect=[policy(), [row]]),
              patch.object(runner, "_bounded_command", return_value=("ActiveState=active\nUnitFileState=enabled\n", "")) as command):
            snapshot = runner.collect_runner_status()
            self.assertIn(SERVICE, command.call_args.args[0])
            self.assertNotIn("token", snapshot.outcomes[0])
            self.assertEqual(snapshot.service["ActiveState"], "active")
        with patch.object(runner, "collect_runner_status", return_value=snapshot):
            page = runner.render_runner_page(True, "", "host", navigation=())
        for text in ("owner/repo", "alice", "debian-ci", "Denied", "actor-denied"):
            self.assertIn(text, page)
        self.assertNotIn("never-display", page)
        self.assertNotIn('method="post"', page)

    def test_invalid_config_outcomes_and_service_failures_remain_visible(self) -> None:
        with (patch.object(runner, "read_json_file", side_effect=[ValueError("secret"), [dict(token="secret")]]),
              patch.object(runner, "_bounded_command", return_value=("", "Query timed out"))):
            snapshot = runner.collect_runner_status()
        self.assertEqual(len(snapshot.issues), 3)
        self.assertEqual(snapshot.outcomes, [])
        self.assertNotIn("secret", str(snapshot))

    def test_navigation_and_feature_require_fixed_installation_evidence(self) -> None:
        self.assertNotIn("github-runner", features.PanelFeatures().pages)
        installed = features.PanelFeatures(github_runner=True)
        self.assertIn("/github-runner", [row[0] for row in panel_navigation(available=installed.pages)])
        with (patch.object(features, "_tool_path", return_value=None),
              patch.object(features, "unit_installed", side_effect=lambda unit, *_args, **_kwargs: unit == SERVICE),
              patch.object(features.os.path, "isfile", return_value=True)):
            self.assertTrue(features.discover_features("/home/operator", admin=False).github_runner)

    def test_route_does_not_collect_dashboard_and_absent_feature_is_404(self) -> None:
        handler = object.__new__(panel.WebPanelHandler)
        handler.state = SimpleNamespace(manifest={"host": "host"}, features=lambda: features.PanelFeatures(github_runner=True),
                                        navigation=lambda current: panel_navigation(current=current, available=features.PanelFeatures(github_runner=True).pages))
        handler._send = Mock()
        handler.path = "/github-runner"
        with patch.object(panel, "render_page") as dashboard, patch.object(runner, "collect_runner_status") as collect:
            handler.do_GET()
            dashboard.assert_not_called()
            collect.assert_not_called()
        self.assertEqual(handler._send.call_args.args[0], HTTPStatus.OK)
        handler.path += "?load=0"
        handler.do_GET()
        self.assertEqual(handler._send.call_args.args[0], HTTPStatus.BAD_REQUEST)
        handler.state.features = lambda: features.PanelFeatures()
        handler.path = "/github-runner"
        handler.do_GET()
        self.assertEqual(handler._send.call_args.args[0], HTTPStatus.NOT_FOUND)
