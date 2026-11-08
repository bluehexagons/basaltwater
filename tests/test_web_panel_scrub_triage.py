"""Browser selection, exact batch reviews and single-use deletion approvals."""

from __future__ import annotations

import json
import re
import unittest
from unittest.mock import patch

from common.service_tools import web_panel_data_actions as actions
from common.service_tools import web_panel_shares_data as panel
from common.service_tools.web_panel_findings import task_key
from tests import test_web_panel_data_actions as data_tests

status = data_tests.status


class ScrubTriagePanelTests(unittest.TestCase):
    setUp = data_tests.DataReviewTests.setUp
    handler = data_tests.DataReviewTests.handler

    def fields(self, action="delete", count=2):
        return {"action": action, "directory": "/srv/data", "database": "/srv/parity",
                "items": ["", *[json.dumps({"file": f"/srv/data/{index}<x>.bin", "finding": "a" * 64})
                                 for index in range(count)]]}

    def test_exact_targets_confirmation_and_independent_approval(self):
        ticket, parameters = self.manager.prepare(self.fields())
        review = actions.render_saved_review(ticket, parameters, "CSRF")
        self.assertIn("DELETE 2 FILES", review)
        self.assertIn("/srv/data/0&lt;x&gt;.bin", review)
        self.assertIn("Permanently remove", review)
        with patch.object(actions, "exchange", return_value=status()) as exchange:
            with self.assertRaises(ValueError):
                self.manager.submit(ticket, "DELETE 1 FILES")
            exchange.assert_not_called()
            self.manager.submit(ticket, "DELETE 2 FILES")
            self.assertEqual(exchange.call_args.args[0]["operation"], "data.run")
            self.assertEqual(exchange.call_args.args[0]["parameters"], parameters)
            with self.assertRaises(ValueError):
                self.manager.submit(ticket, "DELETE 2 FILES")

    def test_empty_large_malformed_duplicate_and_cross_root_selections_rejected(self):
        invalid = [self.fields(count=0), self.fields(count=11),
                   {**self.fields(), "items": ["not-json"]},
                   {**self.fields(), "action": "accept"},
                   {**self.fields(), "items": [json.dumps({"file": "/other/file", "finding": "a" * 64})]}]
        duplicate = self.fields()
        duplicate["items"] = [duplicate["items"][1]] * 2
        invalid.append(duplicate)
        for fields in invalid:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.manager.prepare(fields)
        self.assertFalse(self.manager.tickets)

    def test_http_batch_review_and_confirmation_error(self):
        with patch.object(actions, "exchange", return_value=status()), \
             patch.object(panel.storage, "load_storage_snapshot", return_value={"available": False}):
            handler = self.handler("/actions/data/review", {"csrf": "csrf", **self.fields()})
            handler.do_POST()
            self.assertEqual(handler._send.call_args.args[0], 200)
            document = handler._send.call_args.args[1]
            ticket = re.search(r'name="ticket" value="([^"]+)"', document).group(1)
            handler = self.handler("/actions/data/submit", {"csrf": "csrf", "ticket": ticket, "confirmation": "wrong"})
            handler.do_POST()
            self.assertEqual(handler._send.call_args.args[0], 409)
            self.assertIn("DELETE 2 FILES", handler._send.call_args.args[1])
            self.assertIn(ticket, self.manager.tickets)

    def test_http_rejects_duplicates_and_missing_csrf_before_preparing(self):
        for values, code in ((self.fields(), 403),
                             ({"csrf": "csrf", **self.fields(), "action": ["verify", "delete"]}, 400),
                             ({"csrf": "csrf", **self.fields(), "backup": "/extra"}, 400)):
            with patch.object(self.manager, "prepare") as prepare:
                handler = self.handler("/actions/data/review", values)
                handler.do_POST()
                self.assertEqual(handler._send.call_args.args[0], code)
                prepare.assert_not_called()

    def test_selection_is_scoped_filtered_and_native_form_controls(self):
        job = {"directory": "/srv/data", "database": "/srv/parity", "findings": [],
               "status": "findings", "interval": "weekly", "last_scan": "", "open_count": 1, "omitted_findings": 0}
        finding = {"path": "/srv/data/one", "revision": "a" * 64, "category": "unrepairable",
                   "first_seen": "2026-01-01T00:00:00+00:00", "last_checked": "2026-01-01T00:00:00+00:00"}
        catalog = {"available": True, "tasks": 1, "unavailable_jobs": 0, "scope": job,
                   "findings": [(job, finding)], "total": 1, "page": 1}
        snapshot = {"jobs": [job], "omitted_jobs": 0}
        with patch.object(panel, "load_findings", return_value=catalog):
            body = panel._scrubs(snapshot, {"scope": task_key(job["directory"], job["database"])}, "example", controls=True)
            self.assertIn('form="scrub-batch" name="items"', body)
            self.assertIn('id="scrub-batch"', body)
            self.assertIn("Quarantine as corrupt (recommended)", body)
            self.assertNotIn('form="scrub-batch"', panel._scrubs(snapshot, {}, "example", controls=True))
            self.assertNotIn('id="scrub-batch"', panel._scrubs(snapshot, {"scope": "a" * 64}, "example", controls=False))

    def test_partial_results_show_every_target_and_no_replay_control(self):
        _, parameters = self.manager.prepare(self.fields(count=3))
        latest = {"parameters": parameters, "status": "interrupted", "started": 1, "finished": 2,
                  "items": [{"index": 0, "status": "completed", "category": "delete"},
                            {"index": 1, "status": "skipped", "failure": "file_changed"}]}
        body = actions.render_status({**status(), "latest": latest}, "CSRF")
        for expected in ("completed", "skipped", "unresolved", "never replayed", "0&lt;x&gt;.bin", "2&lt;x&gt;.bin"):
            self.assertIn(expected, body)
        self.assertNotIn('<button>', body)

    def test_individual_recovery_does_not_offer_batch_only_actions(self):
        body = actions.recovery_form({"directory": "/srv/data", "database": "/srv/parity"},
                                     {"path": "/srv/data/one", "revision": "a" * 64}, "CSRF")
        self.assertNotIn('value="delete"', body)
        self.assertNotIn('value="quarantine"', body)
