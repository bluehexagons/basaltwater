"""Runner panel's bounded, sanitized and read-only inspection contract."""

from __future__ import annotations

from http import HTTPStatus
from email.message import Message
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from common.service_tools import web_panel_github_runner as runner
from common.service_tools import web_panel_features as features
from common.service_tools import web_panel_service as panel
from common.service_tools.web_panel_templates import panel_navigation
from lib.github_runner_config import SERVICE
from lib.github_runner_registration import ENDPOINT, MAX_REQUEST_BYTES
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

    def test_collector_handles_real_corrupt_oversized_and_nonregular_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            policy_file, history = directory / "policy.json", directory / "outcomes.json"
            policy_file.write_text(json.dumps(policy()))
            for kind, content in (("malformed", b'{"secret":"do-not-display"'), ("encoding", b"\xff"),
                                  ("nesting", b"[" * 2000 + b"]" * 2000), ("oversize", b" " * (64 * 1024 + 1)),
                                  ("symlink", b""), ("fifo", b"")):
                history.unlink(missing_ok=True)
                if kind == "symlink":
                    history.symlink_to(policy_file)
                elif kind == "fifo":
                    os.mkfifo(history)
                else:
                    history.write_bytes(content)
                with (self.subTest(kind=kind), patch.object(runner, "POLICY", str(policy_file)),
                      patch.object(runner, "OUTCOMES", str(history)),
                      patch.object(runner, "_bounded_command", return_value=("ActiveState=active\n", ""))):
                    snapshot = runner.collect_runner_status()
                self.assertEqual(snapshot.policy, policy())
                self.assertEqual(snapshot.outcomes, [])
                self.assertIn("Gate outcomes are unavailable or invalid.", snapshot.issues)
                self.assertNotIn("do-not-display", str(snapshot))

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
        handler.state = SimpleNamespace(manifest={"host": "host"}, csrf_token="csrf", credential_transport_available=lambda: True,
                                        features=lambda: features.PanelFeatures(github_runner=True),
                                        navigation=lambda current: panel_navigation(current=current, available=features.PanelFeatures(github_runner=True).pages))
        handler.headers = Message()
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

    def test_pending_form_is_scope_specific_https_only_and_never_replaces_registration(self) -> None:
        for organization in (False, True):
            configured = policy() | {"registration": "pending"}
            if organization:
                configured.update(repository="", organization="owner", runner_group="Private CI")
            snapshot = runner.RunnerSnapshot(policy=configured)
            with patch.object(runner, "collect_runner_status", return_value=snapshot):
                page = runner.render_runner_page(True, "", "host", navigation=(), csrf="test-csrf", secure=True)
                self.assertIn(f'action="{ENDPOINT}"', page)
                self.assertIn('type="password"', page)
                self.assertNotIn('name="token" value=', page)
                settings = "organizations/owner/settings/actions/runners/new" if organization else "owner/repo/settings/actions/runners/new"
                self.assertIn(settings, page)
                self.assertIn("one hour", page)
                self.assertIn("Pending registration", page)
                insecure = runner.render_runner_page(True, "", "host", navigation=(), csrf="test-csrf", secure=False)
                self.assertNotIn('type="password"', insecure)
                self.assertIn("HTTPS", insecure)
                snapshot.policy["registration"] = "registered"
                self.assertNotIn('method="post"', runner.render_runner_page(True, "", "host", navigation=(), csrf="csrf", secure=True))

    def test_partial_registration_shows_recovery_and_hides_form(self) -> None:
        with (patch.object(runner, "read_json_file", side_effect=[policy() | {"registration": "pending"}, []]),
              patch.object(runner.os.path, "lexists", return_value=True),
              patch.object(runner, "_bounded_command", return_value=("ActiveState=inactive\n", ""))):
            snapshot = runner.collect_runner_status()
        self.assertTrue(snapshot.recovery_required)
        with patch.object(runner, "collect_runner_status", return_value=snapshot):
            page = runner.render_runner_page(True, "", "host", navigation=(), csrf="csrf", secure=True)
        self.assertIn("Recovery required", page)
        self.assertNotIn('method="post"', page)


class RegistrationRequestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.handler = object.__new__(panel.WebPanelHandler)
        self.handler.path = ENDPOINT
        self.handler.state = SimpleNamespace(
            manifest={"host": "host", "username": "operator"}, csrf_token="csrf",
            credential_transport_available=lambda: True,
            features=lambda: features.PanelFeatures(github_runner=True), navigation=lambda _: (),
        )
        self.handler.connection = Mock()
        self.handler._send = Mock()
        self.submit = self.enterContext(patch.object(panel, "submit_registration"))
        self.enterContext(patch.object(panel.os, "geteuid", return_value=1000))
        self.enterContext(patch.object(runner, "collect_runner_status", return_value=runner.RunnerSnapshot(policy=policy() | {"registration": "pending"})))
        self.token = "A" * 40

    def request(self, body: bytes | None = None, **headers) -> HTTPStatus:
        body = body if body is not None else f"csrf=csrf&token={self.token}".encode()
        self.handler.headers = Message()
        defaults = {"Content-Type": "application/x-www-form-urlencoded", "Content-Length": str(len(body)),
                    "X-Forwarded-Proto": "https", "X-Basaltwater-User": "operator"}
        for key, value in (defaults | headers).items():
            if value is not None:
                self.handler.headers[key] = value
        self.handler.rfile = io.BytesIO(body)
        self.handler.do_POST()
        self.assertNotIn(self.token, str(self.handler._send.call_args))
        return self.handler._send.call_args.args[0]

    def test_authenticated_tls_post_hands_off_once_and_redirects_without_saving_token(self) -> None:
        self.assertEqual(self.request(), HTTPStatus.SEE_OTHER)
        self.submit.assert_called_once_with(self.token.encode())
        self.assertEqual(self.handler._send.call_args.kwargs["headers"], {"Location": "/github-runner?load=1"})
        self.assertNotIn(self.token, repr(vars(self.handler.state)))

    def test_authentication_transport_and_feature_checks_precede_token_processing(self) -> None:
        for headers in ({"X-Basaltwater-User": None}, {"X-Basaltwater-User": "other"}, {"X-Forwarded-Proto": "http"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request(**headers), HTTPStatus.FORBIDDEN)
        self.handler.state.credential_transport_available = lambda: False
        self.assertEqual(self.request(), HTTPStatus.FORBIDDEN)
        self.handler.state.credential_transport_available = lambda: True
        with patch.object(panel.os, "geteuid", return_value=0):
            self.assertEqual(self.request(), HTTPStatus.FORBIDDEN)
        self.handler.state.features = lambda: features.PanelFeatures()
        self.assertEqual(self.request(), HTTPStatus.NOT_FOUND)
        self.submit.assert_not_called()

    def test_csrf_duplicate_fields_unexpected_settings_and_bad_tokens_are_rejected(self) -> None:
        self.assertEqual(self.request(f"csrf=wrong&token={self.token}".encode()), HTTPStatus.FORBIDDEN)
        for body in (f"csrf=csrf&csrf=csrf&token={self.token}", f"csrf=csrf&token={self.token}&token={self.token}",
                     f"csrf=csrf&token={self.token}&repository=other/repo", "csrf=csrf", "csrf=csrf&token=", "csrf=csrf&token=%FF"):
            with self.subTest(body=body):
                self.assertIn(self.request(body.encode()), (HTTPStatus.BAD_REQUEST, HTTPStatus.UNPROCESSABLE_ENTITY))
        for token in ("short", "ghp_" + "A" * 40, "github_pat_" + "A" * 40, "A" * 4097, "A%0A" * 20):
            self.assertEqual(self.request(f"csrf=csrf&token={token}".encode()), HTTPStatus.UNPROCESSABLE_ENTITY)
        self.submit.assert_not_called()

    def test_bounded_content_length_type_query_and_chunked_requests(self) -> None:
        for length in (None, "-1", "invalid", "0", str(MAX_REQUEST_BYTES + 1), "9" * 5000):
            with self.subTest(length=length):
                self.assertEqual(self.request(**{"Content-Length": length}), HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        self.assertEqual(self.request(**{"Content-Type": "application/json"}), HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
        self.assertEqual(self.request(**{"Transfer-Encoding": "chunked"}), HTTPStatus.BAD_REQUEST)
        self.assertEqual(self.request(**{"Content-Length": "8000"}), HTTPStatus.BAD_REQUEST)
        self.request()
        self.submit.reset_mock()
        self.handler.headers["Content-Length"] = "1"
        self.handler.do_POST()
        self.assertEqual(self.handler._send.call_args.args[0], HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        self.handler.path += "?token=forbidden"
        self.assertEqual(self.request(), HTTPStatus.BAD_REQUEST)
        self.submit.assert_not_called()

    def test_helper_errors_are_not_echoed_and_form_is_cleared_for_retry(self) -> None:
        for error in (RuntimeError(self.token), OSError(self.token), TimeoutError(self.token)):
            self.submit.side_effect = error
            self.assertEqual(self.request(), HTTPStatus.UNPROCESSABLE_ENTITY)
            page = self.handler._send.call_args.args[1]
            self.assertIn("fresh token", page)
            self.assertIn('type="password"', page)
            self.assertNotIn('name="token" value=', page)
