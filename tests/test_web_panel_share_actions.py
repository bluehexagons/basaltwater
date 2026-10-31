"""Approved share plans, supervised handoff and browser review boundaries."""

from __future__ import annotations

import http.client
import io
import json
from pathlib import Path
import re
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode

from common.service_tools import privilege_broker as broker_module, web_panel_share_actions as panel, web_panel_storage as storage
from common.service_tools.web_panel_admin import PanelAdmin
from common.service_tools.web_panel_features import PanelFeatures
from common.service_tools.web_panel_service import WebPanelHandler
from common.service_tools.web_panel_shares_data import parse_shares_data_query
from lib.privilege_execution import ExecutionResult
from lib.privilege_policy import MAX_MESSAGE, canonical, operation_plan
from lib.samba_shares import SHARE_HELPER, SHARE_UNIT, share_dispatch_argv, validate_share_action, validate_share_snapshot
from scripts.panel_samples import shares_data_snapshot
from smb import share_job as worker


def parameters():
    return {"before": None, "after": {"name": "documents", "access": "read", "path": "/srv/documents", "users": ["alice"]},
            "revision": "c" * 64, "job_id": "b" * 32}


def policy():
    return {"version": 1, "machine": "a" * 32, "origin": "https://nas.example:9444", "requester_uid": 1000,
            "ttl_seconds": 300, "services": {}, "reboot": "deny", "commands": "approve"}


def idle():
    return {"available": True, "blocked": False, "unit_state": "inactive", "latest": {}, "requests": [], "outstanding": False}


class ShareApprovalTests(unittest.TestCase):
    def test_review_is_typed_and_paths_never_enter_systemd_argv(self):
        value = parameters()
        value["after"]["path"] = "/srv/literal $HOME %n"
        plan = operation_plan(policy(), 1000, "samba.shares", value)
        self.assertEqual(plan["mode"], "approve")
        self.assertEqual(plan["argv"], share_dispatch_argv(value))
        self.assertEqual(plan["argv"][-2:], [SHARE_HELPER, value["job_id"]])
        self.assertIn("--unit=" + SHARE_UNIT, plan["argv"])
        self.assertIn("--property=RuntimeMaxSec=3600", plan["argv"])
        self.assertNotIn("$HOME", canonical(plan["argv"]))
        for changed in ({"password": "PRIVATE"}, {"job_id": "%n"}, {"revision": ""}, {"before": None, "after": None}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                validate_share_action({**value, **changed})
        for changed in ({"users": ["root", "root"]}, {"users": ["alice:PRIVATE"]}, {"path": "/"},
                        {"path": "/srv/../etc"}, {"path": "/srv/bad\npath"}, {"path": "/srv/one,/srv/two"}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                validate_share_action({**value, "after": {**value["after"], **changed}})
        with self.assertRaises(ValueError):
            validate_share_snapshot({"available": True, "revision": "x", "password": "PRIVATE"})

    def test_single_approval_dispatch_and_cross_samba_exclusion(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = Mock(return_value=ExecutionResult("dispatched", exit_code=0))
            broker = broker_module.Broker(str(Path(directory) / "requests.db"), policy_loader=policy, runner=runner)
            self.addCleanup(broker.db.close)
            request = broker.request(1000, "samba.shares", parameters(), "Create share")
            self.assertFalse(broker.execute_next())
            account = {"action": "disable", "username": "alice", "revision": "a" * 64, "job_id": "c" * 32}
            with self.assertRaises(ValueError):
                broker.request(1000, "samba.account", account, "Conflicting login change")
            with patch.object(broker_module, "share_job_status", return_value=idle()), patch.object(broker_module, "protected_path"):
                status = broker.dispatch({"action": "share-status"}, 1000)
                self.assertTrue(status["outstanding"])
                self.assertEqual(status["requests"][0]["name"], "documents")
                self.assertNotIn("/srv/documents", canonical(status))
                with self.assertRaises(PermissionError):
                    broker.dispatch({"action": "share-status"}, 1001)
            broker.decide(request["id"], request["digest"], True, "operator")
            self.assertTrue(broker.execute_next())
            self.assertFalse(broker.execute_next())
            runner.assert_called_once_with(request["plan"])

    def test_maximum_supported_review_and_history_fit_the_socket_contract(self):
        value = parameters()
        value["after"] = {**value["after"], "path": "/srv/" + "界" * 507,
                          "users": ["u" + str(number).zfill(31) for number in range(64)]}
        value["before"] = {**value["after"], "access": "write"}
        with tempfile.TemporaryDirectory() as directory:
            broker = broker_module.Broker(str(Path(directory) / "requests.db"), policy_loader=policy)
            self.addCleanup(broker.db.close)
            request = broker.request(1000, "samba.shares", value, "Maximum supported share review")
            self.assertLess(len(canonical({"ok": True, "result": request}).encode()), MAX_MESSAGE)
            broker.cancel(request["id"], 1000)

    def test_dispatch_checks_worker_before_staging_and_never_reports_completion(self):
        plan = operation_plan(policy(), 1000, "samba.shares", parameters())
        with patch.object(broker_module, "protected_path"), \
             patch.object(broker_module, "share_job_status", return_value=idle()) as status, \
             patch.object(broker_module, "stage_share_job") as stage, \
             patch.object(broker_module.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run:
            self.assertEqual(broker_module.execute(plan).state, "dispatched")
            stage.assert_called_once_with(plan["parameters"])
            run.assert_called_once()
            for change in ({"blocked": True}, {"unit_state": "active"}, {"available": False}):
                status.return_value = {**idle(), **change}
                stage.reset_mock()
                self.assertEqual(broker_module.execute(plan).state, "failed")
                stage.assert_not_called()


class ShareWorkerTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.state = str(Path(directory) / "samba-shares.json")
        self.enterContext(patch.object(worker, "SHARE_STATE", self.state))
        self.enterContext(patch.object(worker, "STATE_DIR", directory))
        self.enterContext(patch.object(worker, "protected_path"))
        self.enterContext(patch.object(worker, "can_manage_system_services", return_value=True))
        self.enterContext(patch.object(worker.os, "geteuid", return_value=0))

    def test_payload_is_private_consumed_once_and_bound_to_its_job(self):
        value = parameters()
        worker.stage_share_job(value)
        path = Path(worker._payload_path(value["job_id"]))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            worker.stage_share_job(value)
        self.assertEqual(worker.consume_share_job(value["job_id"]), value)
        self.assertFalse(path.exists())
        with self.assertRaises(OSError):
            worker.consume_share_job(value["job_id"])
        worker.stage_share_job(value)
        path.write_text(json.dumps({**value, "job_id": "a" * 32}))
        with self.assertRaises(ValueError):
            worker.consume_share_job(value["job_id"])
        self.assertTrue(path.exists())

    def test_worker_records_claim_completion_failure_and_interruption_without_replay(self):
        with patch.object(worker, "apply_reviewed_share_update") as apply:
            self.assertEqual(worker.run_action(parameters()), 0)
            with self.assertRaises(ValueError):
                worker.run_action(parameters())
            apply.assert_called_once()
            apply.side_effect = ValueError("PRIVATE DETAIL")
            self.assertEqual(worker.run_action({**parameters(), "job_id": "a" * 32}), 1)
            self.assertEqual(worker._record()["status"], "failed")
            self.assertNotIn("PRIVATE DETAIL", Path(self.state).read_text())
            apply.side_effect = KeyboardInterrupt
            with self.assertRaises(KeyboardInterrupt):
                worker.run_action({**parameters(), "job_id": "c" * 32})
            self.assertEqual(worker._record()["status"], "interrupted")

    def test_status_is_metadata_only_and_interrupted_or_changed_records_are_not_success(self):
        value = {"parameters": parameters(), "status": "running", "started": 1, "finished": None}
        with patch.object(worker, "_record", return_value=value), \
             patch.object(worker.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="inactive\n")) as run:
            status = worker.job_status()
            self.assertEqual(status["latest"]["status"], "interrupted")
            self.assertNotIn("/srv/documents", canonical(status))
            self.assertEqual(run.call_args.args[0][0], "/usr/bin/systemctl")
            self.assertEqual(run.call_args.kwargs["timeout"], 2)
        with patch.object(worker, "_record", side_effect=[{}, value]), \
             patch.object(worker.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="inactive\n")):
            self.assertFalse(worker.job_status()["available"])
        with patch.object(worker.subprocess, "run", side_effect=subprocess.TimeoutExpired("systemctl", 2)):
            self.assertFalse(worker.job_status()["available"])


class SharePanelTests(unittest.TestCase):
    def setUp(self):
        admin = PanelAdmin({"host": "nas.example", "services": [{"label": "Privilege approvals", "url": "https://nas.example:9444/"}]})
        self.manager = panel.PanelShares(admin)
        self.state = SimpleNamespace(manifest={"host": "nas.example"}, share_actions=self.manager, admin=admin,
                                     csrf_token="csrf", features=lambda: PanelFeatures(admin=True), navigation=lambda _current: ())
        self.snapshot = shares_data_snapshot("full")
        self.enterContext(patch.object(storage, "load_storage_snapshot", side_effect=lambda: self.snapshot))
        self.exchange = self.enterContext(patch.object(panel, "exchange", return_value=idle()))

    def fields(self):
        return {"kind": "save", "original": "projects", "name": "projects", "path": "/srv/projects",
                "access": "read", "users": ["operator"], "revision": "c" * 64}

    def handler(self, path, fields):
        handler = object.__new__(WebPanelHandler)
        handler.state, handler.path = self.state, path
        body = urlencode(fields, doseq=True).encode()
        handler.headers = http.client.HTTPMessage()
        handler.headers["Content-Length"] = str(len(body))
        handler.rfile, handler.wfile = io.BytesIO(body), io.BytesIO()
        handler._send = Mock()
        return handler

    def test_http_review_shows_before_after_then_creates_only_an_approval(self):
        handler = self.handler("/actions/shares/review", {"csrf": "csrf", **self.fields()})
        with patch("subprocess.run") as run:
            handler.do_POST()
        run.assert_not_called()
        self.assertEqual(handler._send.call_args.args[0], 200)
        document = handler._send.call_args.args[1]
        self.assertIn("Current", document)
        self.assertIn("Proposed", document)
        self.assertIn("operator, maintainer", document)
        ticket = re.search(r'name="ticket" value="([^"]+)"', document).group(1)
        self.assertTrue(all(call.args[0] == {"action": "share-status"} for call in self.exchange.call_args_list))
        submit = self.handler("/actions/shares/submit", {"csrf": "csrf", "ticket": ticket, "confirmation": "projects"})
        submit.do_POST()
        self.assertEqual(submit._send.call_args.args[0], 303)
        message = self.exchange.call_args.args[0]
        self.assertEqual(message["operation"], "samba.shares")
        self.assertEqual(message["parameters"]["before"]["users"], ["operator", "maintainer"])
        self.assertEqual(message["parameters"]["after"]["users"], ["operator"])

    def test_add_remove_stale_and_missing_users_are_explicit(self):
        _, value = self.manager.prepare({**self.fields(), "original": "", "name": "new"})
        self.assertIsNone(value["before"])
        _, value = self.manager.prepare({"kind": "remove", "name": "archive", "revision": "c" * 64})
        self.assertIsNone(value["after"])
        for change in ({"revision": "a" * 64}, {"users": []}, {"users": ["root"]}, {"original": "", "name": "projects"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.manager.prepare({**self.fields(), **change})
        self.exchange.assert_not_called()

    def test_http_accepts_64_users_and_rejects_csrf_duplicates_or_extra_fields(self):
        users = ["u" + str(number) for number in range(64)]
        self.snapshot["samba"]["accounts"] = [{"username": name, "uid": 1000 + number, "manageable": True,
            "disabled": False, "locked": False, "revision": "a" * 64} for number, name in enumerate(users)]
        handler = self.handler("/actions/shares/review", {"csrf": "csrf", **self.fields(), "original": "", "name": "new", "users": users})
        handler.do_POST()
        self.assertEqual(handler._send.call_args.args[0], 200)
        for fields, code in (({}, 403), ({"csrf": "csrf", **self.fields(), "name": ["a", "b"]}, 400),
                             ({"csrf": "csrf", **self.fields(), "password": "PRIVATE"}, 400)):
            handler = self.handler("/actions/shares/review", fields)
            handler.do_POST()
            self.assertEqual(handler._send.call_args.args[0], code)

    def test_confirmation_expiry_and_lost_responses_never_replay(self):
        ticket, value = self.manager.prepare(self.fields())
        with self.assertRaises(ValueError):
            self.manager.submit(ticket, "wrong")
        self.assertEqual(self.manager.reviewed(ticket), value)
        self.exchange.assert_not_called()
        ticket, _ = self.manager.prepare(self.fields())
        with patch.object(panel.time, "monotonic", return_value=float("inf")), self.assertRaises(ValueError):
            self.manager.submit(ticket, "projects")
        ticket, _ = self.manager.prepare(self.fields())
        self.exchange.side_effect = [idle(), OSError("Response lost")]
        with self.assertRaises(OSError):
            self.manager.submit(ticket, "projects")
        with self.assertRaises(ValueError):
            self.manager.submit(ticket, "projects")
        self.assertEqual(self.exchange.call_count, 2)

    def test_http_errors_retain_editor_values_and_a_confirmation_review_can_be_corrected(self):
        fields = {"csrf": "csrf", **self.fields(), "path": '/srv/<draft> " & literal $HOME %n'}
        fields.pop("users")
        handler = self.handler("/actions/shares/review", fields)
        handler.do_POST()
        self.assertEqual(handler._send.call_args.args[0], 409)
        document = handler._send.call_args.args[1]
        self.assertIn('value="/srv/&lt;draft&gt; &quot; &amp; literal $HOME %n"', document)
        self.assertIn('name="original" value="projects"', document)
        self.assertNotIn('value="operator" checked', document)
        self.assertNotIn('value="maintainer" checked', document)
        self.assertFalse(self.manager.tickets)
        ticket, value = self.manager.prepare(self.fields())
        handler = self.handler("/actions/shares/submit", {"csrf": "csrf", "ticket": ticket, "confirmation": "wrong"})
        handler.do_POST()
        self.assertEqual(handler._send.call_args.args[0], 409)
        self.assertIn('name="ticket" value="' + ticket + '"', handler._send.call_args.args[1])
        self.assertEqual(self.manager.reviewed(ticket), value)
        self.assertTrue(all(call.args[0] == {"action": "share-status"} for call in self.exchange.call_args_list))
        self.manager.submit(ticket, "projects")
        self.assertEqual(self.exchange.call_args.args[0]["operation"], "samba.shares")
        with self.assertRaises(ValueError):
            self.manager.submit(ticket, "projects")

    def test_stale_and_busy_review_keep_a_copyable_draft_without_rebasing(self):
        for changed in ({"revision": "a" * 64}, {}):
            self.exchange.return_value = {**idle(), **({"unit_state": "active"} if not changed else {})}
            handler = self.handler("/actions/shares/review", {"csrf": "csrf", **self.fields(), **changed})
            handler.do_POST()
            self.assertEqual(handler._send.call_args.args[0], 409)
            document = handler._send.call_args.args[1]
            self.assertIn("Unsaved share draft", document)
            self.assertIn("/srv/projects", document)
            self.assertNotIn('action="/actions/shares/review"', document)
            self.assertFalse(self.manager.tickets)
        self.snapshot["inventory"] = {"available": False}
        document = panel.render_editor(self.snapshot, {"edit": "projects"}, "csrf", self.fields())
        self.assertIn("Unsaved share draft", document)
        self.assertNotIn('action="/actions/shares/review"', document)

    def test_editing_and_queries_work_locally_without_probing_the_host(self):
        with patch("subprocess.run") as run:
            document = panel.render_editor(self.snapshot, {"edit": "projects"}, "csrf")
        run.assert_not_called()
        self.exchange.assert_not_called()
        self.assertIn("maintainer (login disabled)", document)
        self.assertNotIn('value="root"', document)
        self.assertIn("Review share removal", document)
        self.assertIn('name="original" value="projects"', document)
        self.assertEqual(parse_shares_data_query("view=shares&edit=projects")["edit"], "projects")
        for query in ("view=scrubs&edit=projects", "form=remove", "edit=projects&form=add", "edit=bad%0Aname"):
            with self.subTest(query=query), self.assertRaises(ValueError):
                parse_shares_data_query(query)
