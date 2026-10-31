"""Routine browser review, complete selection, history and one-shot submission."""

from __future__ import annotations

import http.client
import io
import re
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode

from common.service_tools import web_panel_routine_actions as panel, web_panel_storage as storage
from common.service_tools.web_panel_admin import PanelAdmin
from common.service_tools.web_panel_features import PanelFeatures
from common.service_tools.web_panel_service import WebPanelHandler
from common.service_tools.web_panel_shares_data import parse_shares_data_query, render_shares_data
from lib.storage_routines import routine_identity
from scripts.panel_samples import shares_data_snapshot


def idle(**changes):
    return {"available": True, "blocked": False, "unit_state": "inactive", "latest": {}, "requests": [],
            "outstanding": False, "other_active": False, **changes}


class RoutinePanelTests(unittest.TestCase):
    def setUp(self):
        admin = PanelAdmin({"host": "nas.example", "services": [{"label": "Privilege approvals", "url": "https://nas.example:9444/"}]})
        self.manager = panel.PanelRoutines(admin)
        self.state = SimpleNamespace(manifest={"host": "nas.example"}, routines=self.manager, admin=admin,
            csrf_token="csrf", features=lambda: PanelFeatures(admin=True), navigation=lambda _current: ())
        self.snapshot = shares_data_snapshot("full")
        self.enterContext(patch.object(storage, "load_storage_snapshot", side_effect=lambda: self.snapshot))
        self.exchange = self.enterContext(patch.object(panel, "exchange", return_value=idle()))
        self.enterContext(patch("common.service_tools.web_panel_shares_data.load_findings", return_value={"available": False}))

    def fields(self, kind="backup", **changes):
        row = next(row for row in self.snapshot["routine_management"]["routines"] if row["kind"] == kind)
        return {"op": "save", "kind": kind, "original": routine_identity(kind, row["spec"]), "revision": "d" * 64,
                "first": row["spec"][0], "second": row["spec"][1], "interval": "monthly", "paused": "1",
                **({"redundancy": "15%"} if kind == "scrub" else {}), **changes}

    def operation(self, operation, kind="backup"):
        return {key: value for key, value in self.fields(kind, op=operation).items() if key in {"op", "kind", "original", "revision"}}

    def handler(self, action, fields):
        handler = object.__new__(WebPanelHandler)
        handler.state, handler.path = self.state, "/actions/routines/" + action
        body = urlencode(fields, doseq=True).encode()
        handler.headers = http.client.HTTPMessage()
        handler.headers["Content-Length"] = str(len(body))
        handler.rfile, handler.wfile, handler._send = io.BytesIO(body), io.BytesIO(), Mock()
        return handler

    def test_http_review_binds_before_after_and_only_creates_approval(self):
        handler = self.handler("review", {"csrf": "csrf", **self.fields()})
        with patch("subprocess.run") as run:
            handler.do_POST()
        run.assert_not_called()
        self.assertEqual(handler._send.call_args.args[0], 200)
        document = handler._send.call_args.args[1]
        self.assertIn("Current", document)
        self.assertIn("Proposed", document)
        self.assertIn("replace and delete", document)
        ticket = re.search(r'name="ticket" value="([^"]+)"', document).group(1)
        self.assertTrue(all(call.args[0] == {"action": "routine-status"} for call in self.exchange.call_args_list))
        submit = self.handler("submit", {"csrf": "csrf", "ticket": ticket, "confirmation": "SAVE ROUTINE", "view": "routines"})
        submit.do_POST()
        self.assertEqual(submit._send.call_args.args[0], 303)
        message = self.exchange.call_args.args[0]
        self.assertEqual(message["operation"], "data.routine")
        self.assertEqual(message["parameters"]["before"]["spec"][-1], "daily")
        self.assertEqual(message["parameters"]["after"]["spec"][-1], "monthly")
        self.assertTrue(message["parameters"]["after"]["paused"])

    def test_create_scrub_edit_remove_pause_resume_and_selected_run(self):
        for kind in ("backup", "sync", "scrub"):
            with self.subTest(kind=kind):
                _, value = self.manager.prepare(self.fields(kind, original="", first="/srv/new", second="/srv/new-output"))
                self.assertIsNone(value["before"])
                self.assertEqual(value["after"]["kind"], kind)
                for operation in ("remove", "run", "resume" if kind == "sync" else "pause"):
                    _, value = self.manager.prepare(self.operation(operation, kind))
                    self.assertEqual(value["action"], "run" if operation == "run" else "change")
                    if operation in {"run", "remove"}:
                        self.assertIsNone(value["after"])
                    else:
                        self.assertEqual(value["before"]["spec"], value["after"]["spec"])
                        self.assertEqual(value["after"]["paused"], operation == "pause")
        self.exchange.assert_not_called()

    def test_stale_missing_wrong_kind_noop_and_conflicting_proposals_rejected(self):
        for change in ({"revision": "a" * 64}, {"original": "a" * 64}, {"kind": "sync"},
                       {"interval": "daily", "paused": "0"}, {"second": "/srv/parity/archive"},
                       {"first": "/"}, {"first": "/srv/../etc"}, {"paused": "yes"},
                       {"original": "", "interval": "daily", "paused": "0"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.manager.prepare({**self.fields(), **change})
        with self.assertRaises(ValueError):
            self.manager.prepare(self.fields("scrub", second="/srv/archive", redundancy="0%"))
        self.snapshot["routine_management"]["available"] = False
        with self.assertRaises(ValueError):
            self.manager.prepare(self.fields())
        self.exchange.assert_not_called()

    def test_validation_preserves_entered_values_and_confirmation_typo_keeps_review(self):
        fields = self.fields(first="/srv/kept <input>", interval="invalid")
        handler = self.handler("review", {"csrf": "csrf", **fields})
        handler.do_POST()
        self.assertEqual(handler._send.call_args.args[0], 422)
        self.assertIn('value="/srv/kept &lt;input&gt;"', handler._send.call_args.args[1])
        self.assertIn('name="original" value="' + fields["original"] + '"', handler._send.call_args.args[1])
        ticket, _ = self.manager.prepare(self.fields())
        handler = self.handler("submit", {"csrf": "csrf", "ticket": ticket, "confirmation": "wrong", "view": "routines"})
        handler.do_POST()
        self.assertEqual(handler._send.call_args.args[0], 409)
        self.assertIn('name="ticket" value="' + ticket + '"', handler._send.call_args.args[1])
        self.assertTrue(self.manager.reviewed(ticket))
        self.assertFalse(any(call.args[0]["action"] == "request" for call in self.exchange.call_args_list))

    def test_one_use_after_lost_response_expiry_and_simultaneous_submission(self):
        ticket, _ = self.manager.prepare(self.fields())
        self.exchange.side_effect = [idle(), OSError("PRIVATE response lost")]
        with self.assertRaises(OSError):
            self.manager.submit(ticket, "SAVE ROUTINE")
        with self.assertRaises(ValueError):
            self.manager.submit(ticket, "SAVE ROUTINE")

        self.assertEqual(self.exchange.call_count, 2)
        self.exchange.side_effect = None
        ticket, _ = self.manager.prepare(self.fields())
        with patch.object(panel.time, "monotonic", return_value=float("inf")), self.assertRaises(ValueError):
            self.manager.submit(ticket, "SAVE ROUTINE")
        ticket, _ = self.manager.prepare(self.fields())
        self.exchange.side_effect = lambda _: (self.manager.tickets.pop(ticket), idle())[1]
        with self.assertRaises(ValueError):
            self.manager.submit(ticket, "SAVE ROUTINE")

    def test_changed_or_removed_routine_preserves_draft_without_rebasing_inputs(self):
        for changed in ("revision", "removed"):
            with self.subTest(changed=changed):
                self.snapshot = shares_data_snapshot("full")
                fields = self.fields(first="/srv/unsaved <input>", second="/srv/draft-output")
                control = self.snapshot["routine_management"]
                if changed == "revision":
                    control["revision"] = "e" * 64
                else:
                    control["routines"] = [row for row in control["routines"] if row["kind"] != "backup"]
                handler = self.handler("review", {"csrf": "csrf", **fields})
                handler.do_POST()
                self.assertEqual(handler._send.call_args.args[0], 422)
                document = handler._send.call_args.args[1]
                self.assertIn("Unsaved routine draft", document)
                self.assertIn("/srv/unsaved &lt;input&gt;", document)
                self.assertIn("/srv/draft-output", document)
                self.assertNotIn('id="routine-first"', document)
                self.assertNotIn("Create routine approval request", document)
                self.assertFalse(self.manager.tickets)
        self.assertFalse(any(call.args[0]["action"] == "request" for call in self.exchange.call_args_list))

    def test_unavailable_editing_retains_copyable_routine_inputs(self):
        for kind, unavailable in (("backup", "busy"), ("sync", "configuration"), ("scrub", "snapshot")):
            with self.subTest(kind=kind, unavailable=unavailable):
                self.snapshot = shares_data_snapshot("full")
                fields = self.fields(kind, first="/srv/draft <source>", second="/srv/draft-output")
                self.exchange.return_value = idle(other_active=unavailable == "busy")
                if unavailable == "configuration":
                    self.snapshot["routine_management"] = {"available": False}
                elif unavailable == "snapshot":
                    self.snapshot = {"available": False}
                handler = self.handler("review", {"csrf": "csrf", **fields})
                handler.do_POST()
                self.assertEqual(handler._send.call_args.args[0], 422)
                document = handler._send.call_args.args[1]
                self.assertIn("Unsaved routine draft", document)
                self.assertIn("/srv/draft &lt;source&gt;", document)
                self.assertIn("/srv/draft-output", document)
                self.assertIn("monthly", document)
                self.assertIn("Paused", document)
                if kind == "scrub":
                    self.assertIn("15%", document)
                self.assertNotIn('id="routine-first"', document)
                self.assertFalse(self.manager.tickets)
        self.assertFalse(any(call.args[0]["action"] == "request" for call in self.exchange.call_args_list))

    def test_cancel_uses_owned_broker_request_and_returns_to_its_view(self):
        with patch.object(self.state.admin, "cancel") as cancel:
            handler = self.handler("cancel", {"csrf": "csrf", "id": "a" * 32, "view": "scrubs"})
            handler.do_POST()
        cancel.assert_called_once_with("a" * 32)
        self.assertEqual(handler._send.call_args.args[0], 303)
        self.assertEqual(handler._send.call_args.kwargs["headers"]["Location"], "/shares-data?view=scrubs")

    def test_status_gating_csrf_feature_duplicates_limits_and_unknown_fields(self):
        for changes in ({"outstanding": True}, {"other_active": True}, {"unit_state": "active"}, {"blocked": True}, {"available": False}):
            with self.subTest(changes=changes):
                self.exchange.return_value = idle(**changes)
                handler = self.handler("review", {"csrf": "csrf", **self.fields()})
                handler.do_POST()
                self.assertEqual(handler._send.call_args.args[0], 422)
                self.assertFalse(self.manager.tickets)
        self.exchange.return_value = idle()
        for extra, expected in (({"csrf": "wrong"}, 403), ({"op": ["run", "remove"]}, 400),
                                ({"first": "x" * 513}, 400), ({"first": "/srv/line\nbreak"}, 400),
                                ({"password": "PRIVATE"}, 400)):
            handler = self.handler("review", {"csrf": "csrf", **self.fields(), **extra})
            handler.do_POST()
            self.assertEqual(handler._send.call_args.args[0], expected)
        self.state.features = lambda: PanelFeatures(admin_page=False)
        handler = self.handler("review", {"csrf": "csrf", **self.fields()})
        handler.do_POST()
        self.assertEqual(handler._send.call_args.args[0], 404)

    def test_all_256_routines_are_editable_outside_summary_and_inventory_limits(self):
        control = self.snapshot["routine_management"]
        control["history"] = {"available": False}
        control["routines"] = [{"kind": "scrub", "spec": [f"/srv/data{i}", f"/srv/parity{i}", "10%", "weekly"], "paused": False}
                               for i in range(256)]
        selected = control["routines"][-1]
        identifier = routine_identity("scrub", selected["spec"])
        query = parse_shares_data_query("view=scrubs&routine=" + identifier)
        with patch("subprocess.run") as run:
            document = render_shares_data(self.state, "", query)
        run.assert_not_called()
        self.assertIn('value="/srv/data255"', document)
        self.assertIn('name="original" value="' + identifier + '"', document)
        self.assertEqual(document.count("Edit routine →"), 256)
        _, value = self.manager.prepare({**self.operation("run", "scrub"), "original": identifier})
        self.assertEqual(value["before"], selected)
        with self.assertRaises(ValueError):
            self.manager.prepare(self.fields("scrub", original="", first="/srv/overflow", second="/srv/overflow-parity"))

    def test_approval_and_worker_results_are_correlated_and_not_assumed(self):
        _, value = self.manager.prepare(self.operation("run", "scrub"))
        request = {"id": "a" * 32, "parameters": value, "state": "dispatched", "created": 1,
                   "review_url": "https://nas.example/requests/" + "a" * 32}
        document = panel.render_status(idle(requests=[request]), "csrf")
        self.assertIn("Completion is not established", document)
        for result, text in (("warnings", "repairs or mirror precautions"), ("findings", "unresolved findings"),
                             ("succeeded", "selected run completed"), ("failed", "Required tools are missing")):
            document = panel.render_status(idle(requests=[request], latest={"job_id": value["job_id"], "status": result, "failure": "tools_unavailable"}), "csrf")
            self.assertIn(text, document)
        request["state"], request["expires"] = "pending", 3
        document = panel.render_status(idle(requests=[request], outstanding=True), "csrf")
        self.assertIn("Cancel routine request", document)
        request["review_url"] = "javascript:alert(1)"
        self.assertNotIn("javascript:", panel.render_status(idle(requests=[request]), "csrf"))

    def test_history_keeps_cadence_retry_and_integrity_separate(self):
        control = self.snapshot["routine_management"]
        row = next(row for row in control["routines"] if row["kind"] == "scrub")
        entry = control["history"]["routines"][routine_identity("scrub", row["spec"])]
        entry["retry"] = {"failed_at": 1, "retry_after": 61, "failures": 2}
        document = panel.render_history(control, row)
        self.assertIn("initial deferral", document)
        self.assertIn("2 consecutive failure(s)", document)
        self.assertIn("Last completed parity", document)
        self.assertNotIn("Last completed mirror", document)
        self.assertIn("unavailable", panel.render_history({}, row))

    def test_query_view_and_action_identity_are_strict(self):
        for query in ("view=routines&form=backup", "view=routines&form=sync", "view=scrubs&form=scrub", "view=routines&routine=" + "a" * 64):
            self.assertTrue(parse_shares_data_query(query))
        for query in ("view=shares&routine=" + "a" * 64, "view=routines&form=scrub", "view=scrubs&form=backup",
                      "view=scrubs&routine=invalid", "view=routines&routine=" + "a" * 64 + "&form=sync", "view=scrubs&form=scrub&q=foo"):
            with self.subTest(query=query), self.assertRaises(ValueError):
                parse_shares_data_query(query)
        for action in ("submit", "cancel"):
            with self.assertRaises(ValueError):
                panel.parse_fields({"view": ["shares"]}, action)

    def test_unavailable_controls_do_not_hide_integrity_or_configuration(self):
        self.exchange.return_value = idle(available=False)
        document = render_shares_data(self.state, "", {"view": "scrubs"})
        self.assertIn("Routine controls are unavailable", document)
        self.assertIn("/srv/parity/archive", document)
        self.assertIn("Unresolved findings", document)
        self.assertNotIn("Review routine change", document)
        self.snapshot["routine_management"] = {"available": False}
        document = render_shares_data(self.state, "", {"view": "routines"})
        self.assertIn("older inventory below may be incomplete", document)
        self.assertIn("/srv/backups/projects", document)

    def test_findings_pagination_leaves_editor_query_without_invalid_combinations(self):
        from common.service_tools import web_panel_shares_data as page
        from urllib.parse import urlsplit
        import html

        query = {"view": "scrubs", "form": "scrub"}
        catalog = {"available": True, "tasks": 1, "unavailable_jobs": 0, "findings": [], "total": 21, "page": 1}
        with patch.object(page, "load_findings", return_value=catalog):
            document = page.render_shares_data(self.state, "", query)
        target = re.search(r'href="([^"]+)">Next findings', document).group(1)
        self.assertEqual(parse_shares_data_query(urlsplit(html.unescape(target)).query), {"view": "scrubs", "page": "2"})
