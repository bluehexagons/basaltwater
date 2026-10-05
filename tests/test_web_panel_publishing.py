"""Human review, secret handling, TLS and escaped translation panel boundaries."""

from __future__ import annotations

from io import BytesIO
import json
from types import SimpleNamespace
import unittest
import urllib.parse
from unittest.mock import Mock, patch

from common.service_tools import web_panel_service as panel
from common.service_tools import web_panel_publishing as view
from tests import test_publishing as fixtures


class PublishingPanelTests(unittest.TestCase):
    complete = fixtures.PublishingTests.complete
    approve = fixtures.PublishingTests.approve

    def setUp(self):
        fixtures.PublishingTests.setUp(self)
        self.state = panel.WebPanelState({"host": "vm.example.test", "username": "operator", "system_type": "agent_vm",
            "features": {}, "services": [], "access": [], "panel_url": "https://vm.example.test/"}, agent_home=str(self.home))
        self.assertEqual(self.state.publishing.home, self.home)
        self.state.publishing_auth = SimpleNamespace(views=lambda: [], begin=Mock())
        nonroot = patch.object(panel.os, "geteuid", return_value=1000)
        nonroot.start()
        self.addCleanup(nonroot.stop)

    def handler(self, action, values=None, proto="https"):
        handler = object.__new__(panel.WebPanelHandler)
        handler.state = self.state
        handler.path = action
        body = urllib.parse.urlencode(values or {}, doseq=True).encode()
        handler.rfile = BytesIO(body)
        handler.headers = {"Content-Length": str(len(body)), "X-Forwarded-Proto": proto}
        handler._send = Mock()
        handler._send_json = Mock()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        return handler

    def test_get_has_no_native_or_network_side_effects(self):
        artifact = self.publisher.prepare("game")
        with patch("subprocess.Popen", side_effect=AssertionError("GET process")), patch("http.client.HTTPSConnection", side_effect=AssertionError("GET network")):
            handler = self.handler("/publishing")
            handler.do_GET()
        status, page = handler._send.call_args.args[:2]
        self.assertEqual(status, panel.HTTPStatus.OK)
        self.assertIn(artifact["digest"], page)
        self.assertIn('href="/publishing" aria-current="page"', page)
        self.assertIn('name="key" type="password" value=""', page)
        self.state.publishing_auth.begin.assert_not_called()

    def test_review_exact_hash_and_translation_text_is_escaped(self):
        en = self.publisher.draft("game", "en", "Update <one>", "Hello <script>alert(1)</script>")
        es = self.publisher.draft("game", "es", "Actualización", "¡Hola, mundo!", source=en["id"])
        page = view.render_publishing(self.state, panel._PAGE_STYLE)
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn("<script>", page)
        self.assertIn("¡Hola, mundo!", page)
        for revision, expected in (("wrong", 422), (es["hash"], 303)):
            handler = self.handler("/actions/publishing/review", {"csrf": self.state.csrf_token, "draft": es["id"], "hash": revision, "decision": "approve"})
            handler.do_POST()
            response = handler._send if expected == 422 else handler.send_response
            self.assertEqual(response.call_args.args[0].value, expected)
        self.assertEqual(self.publisher.export(es["id"])["body"], "¡Hola, mundo!")
        with self.assertRaises(ValueError):
            self.publisher.export(en["id"])

    def test_csrf_duplicate_unknown_query_and_http_never_authenticate(self):
        valid = {"csrf": self.state.csrf_token, "provider": "butler"}
        for values, query, proto, expected in (({**valid, "csrf": "bad"}, "", "https", 403),
                ({**valid, "provider": ["butler", "steamcmd"]}, "", "https", 400),
                ({**valid, "approved": "true"}, "", "https", 400), (valid, "?key=secret", "https", 400),
                (valid, "", "http", 403)):
            with self.subTest(values=values):
                handler = self.handler("/actions/publishing/login" + query, values, proto)
                handler.do_POST()
                self.assertEqual(handler._send.call_args.args[0].value, expected)
        self.state.publishing_auth.begin.assert_not_called()

    def test_errors_never_echo_native_credentials(self):
        self.state.publishing_auth.begin.side_effect = RuntimeError("SECRET vendor response")
        handler = self.handler("/actions/publishing/login", {"csrf": self.state.csrf_token, "provider": "butler"})
        handler.do_POST()
        self.assertEqual(handler._send.call_args.args[0], panel.HTTPStatus.UNPROCESSABLE_ENTITY)
        self.assertNotIn("SECRET", handler._send.call_args.args[1])

    def test_history_limit_keeps_pending_runs_project_and_translation_source(self):
        en = self.publisher.draft("game", "en", "Published source", "Exact original source text")
        self.approve(en)
        self.publisher.export(en["id"], dispatch=True)
        self.publisher.confirm_post_from_panel(en["id"], "https://owner.itch.io/game/devlog/1", "operator")
        artifact = self.publisher.prepare("game")
        run = self.publisher.upload(artifact["id"])
        with self.publisher.store.transaction() as db:
            run["state"] = "unknown"
            self.publisher.store.put(db, "runs", run)
            en = self.publisher.store.get(db, "drafts", en["id"])
            for index in range(210):
                self.publisher.store.put(db, "projects", {**self.project, "id": f"new-project-{index}"})
                self.publisher.store.put(db, "drafts", {**en, "id": f"archived-draft-{index}"})
                self.publisher.store.put(db, "runs", {**run, "id": f"finished-run-{index}", "state": "uploaded"})
        es = self.publisher.draft("game", "es", "Traducción", "Texto traducido", source=en["id"])
        handoff = self.publisher.draft("game", "en", "Pending editor", "Review this post")
        self.approve(handoff)
        self.publisher.export(handoff["id"], dispatch=True)
        snapshot = self.publisher.status()
        self.assertEqual(snapshot["runs"][0]["id"], run["id"])
        self.assertIn("game", {project["id"] for project in snapshot["projects"]})
        self.assertIn(en["id"], {draft["id"] for draft in snapshot["drafts"]})
        handler = self.handler("/publishing")
        handler.do_GET()
        self.assertEqual(handler._send.call_args.args[0], panel.HTTPStatus.OK)
        page = handler._send.call_args.args[1]
        self.assertIn(run["id"], page)
        self.assertIn("Exact original source text", page)
        self.assertIn(es["id"], page)
        self.assertIn("Record published post", page)

    def test_insecure_page_has_no_secret_input_or_mutation_forms(self):
        self.state.manifest["panel_url"] = "http://vm.example.test/"
        page = view.render_publishing(self.state, panel._PAGE_STYLE)
        self.assertNotIn('type="password"', page)
        self.assertNotIn('method="post"', page)

    def test_time_requires_explicit_offset_and_spanish_source_can_translate_to_english(self):
        with self.assertRaises(ValueError):
            view.utc_instant("2026-10-05T10:00:00")
        self.assertEqual(view.utc_instant("2026-10-05T10:00:00+02:00"), view.utc_instant("2026-10-05T08:00:00Z"))
        (self.repo / "basaltwater.json").write_text(json.dumps({"version": 1, "components": [], "publishing": {
            "languages": {"source": "es", "supported": ["en", "es"]}}}))
        es = self.publisher.draft("game", "es", "Actualización", "¡Ya está disponible!")
        en = self.publisher.draft("game", "en", "Update", "Now available!", source=es["id"])
        self.approve(en)
        self.assertEqual(self.publisher.export(en["id"])["language"], "en")
