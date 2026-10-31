"""Recovery reviews must bind one approved operation to unchanged evidence."""

from __future__ import annotations

import copy
import http.client
import io
import json
import os
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode

from common.service_tools import data_job, privilege_broker, web_panel_data_actions as actions, web_panel_storage as storage
from common.service_tools.web_panel_admin import PanelAdmin
from common.service_tools.web_panel_features import PanelFeatures
from common.service_tools.web_panel_service import WebPanelHandler
from common.service_tools.web_panel_templates import panel_navigation
from lib.data_actions import DATA_HELPER, DATA_TIMEOUT, finding_revision, validate_data_action
from lib.privilege_execution import ExecutionResult
from lib.privilege_policy import operation_plan
from sync.service_tools.scrub_findings import Findings, file_identity, remediate


def parameters() -> dict:
    return {"action": "repair", "directory": "/srv/archive", "database": "/srv/parity", "file": "/srv/archive/file.bin",
            "backup": "", "finding": "a" * 64, "job_id": "b" * 32}


def status() -> dict:
    return {"available": True, "unit_state": "inactive", "latest": {}, "requests": []}


def policy() -> dict:
    return {"version": 1, "machine": "a" * 32, "origin": "https://vm.example:9444", "requester_uid": 1000,
            "ttl_seconds": 300, "services": {}, "reboot": "deny", "commands": "approve"}


class DataActionContractTests(unittest.TestCase):
    def test_reviewed_plan_is_typed_and_dispatched_with_a_deadline(self):
        value = parameters()
        plan = operation_plan(policy(), 1000, "data.run", value)
        self.assertEqual(plan["mode"], "approve")
        self.assertIn(f"--property=RuntimeMaxSec={DATA_TIMEOUT}", plan["argv"])
        self.assertIn(DATA_HELPER, plan["argv"])
        self.assertEqual(plan["argv"][-1], value["job_id"])
        self.assertNotIn(value["file"], plan["argv"])
        self.assertIn("retain originals", plan["effect"])
        with patch.object(privilege_broker, "protected_path"), patch.object(privilege_broker, "stage_data_job") as stage, patch.object(privilege_broker, "data_job_status", return_value=status()), patch.object(privilege_broker.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run:
            self.assertEqual(privilege_broker.execute(plan), ExecutionResult("dispatched", exit_code=0))
            self.assertEqual(run.call_args.args[0], plan["argv"])
            stage.assert_called_once_with(value)
        for field, invalid in (("action", "shell"), ("file", "/srv/archive/../other"), ("file", "/srv/parity/file.bin"),
                               ("file", "/etc/shadow"), ("directory", "/"), ("backup", "/tmp/copy"), ("finding", ""), ("job_id", "../job")):
            with self.subTest(field=field, invalid=invalid), self.assertRaises(ValueError):
                validate_data_action({**value, field: invalid})
        with self.assertRaises(ValueError):
            validate_data_action({**value, "command": "sh"})
        with self.assertRaises(ValueError):
            validate_data_action({**value, "action": "restore"})

    def test_request_needs_separate_approval_and_status_is_owned_and_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = Mock(return_value=ExecutionResult("dispatched", exit_code=0))
            broker = privilege_broker.Broker(str(Path(directory) / "requests.db"), policy_loader=policy, runner=runner)
            self.addCleanup(broker.db.close)
            request = broker.request(1000, "data.run", parameters(), "Repair reviewed file")
            self.assertFalse(broker.execute_next())
            with self.assertRaises(ValueError):
                broker.request(1000, "data.run", {**parameters(), "job_id": "c" * 32}, "Second repair")
            with patch.object(privilege_broker, "data_job_status", return_value=status()):
                result = broker.dispatch({"action": "data-status"}, 1000)
                self.assertEqual(result["requests"][0]["parameters"], parameters())
                with self.assertRaises(PermissionError):
                    broker.dispatch({"action": "data-status"}, 1001)
            broker.decide(request["id"], request["digest"], True, "operator")
            self.assertTrue(broker.execute_next())
            self.assertFalse(broker.execute_next())
            runner.assert_called_once_with(request["plan"])


class DataRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory, self.database = self.root / "data", self.root / "parity"
        self.directory.mkdir()
        self.database.mkdir()
        self.file = self.directory / "file.bin"
        self.file.write_bytes(b"damaged data")
        self.report = Findings(str(self.directory), str(self.database))
        self.report.record("file.bin", {"category": "repairable", "file_identity": file_identity(str(self.file)), "evidence": "PRIVATE EVIDENCE"})
        self.saved = {"username": "root", "scrub_specs": [[str(self.directory), str(self.database), "10%", "weekly"]]}
        self.value = {**parameters(), "directory": str(self.directory), "database": str(self.database), "file": str(self.file),
                      "finding": finding_revision(self.report.data["files"]["file.bin"])}

    def test_approved_repair_uses_existing_staged_recovery(self):
        with patch("sync.service_tools.scrub_manage.remediate", return_value={"category": "healthy"}) as remediate:
            self.assertEqual(data_job.recover(self.value, self.saved), ({"file": str(self.file), "category": "healthy"}, 0))
        remediate.assert_called_once_with(str(self.file), str(self.directory), str(self.database), 10, "repair", backup=None,
                                          reviewed_identity=file_identity(str(self.file)))

    def test_remediation_rechecks_reviewed_identity_before_retaining_or_staging(self):
        reviewed = file_identity(str(self.file))
        self.file.write_bytes(b"changed in the handoff to remediation")
        with self.assertRaisesRegex(ValueError, "changed after recovery review"):
            remediate(str(self.file), str(self.directory), str(self.database), 10, "accept", reviewed_identity=reviewed)
        self.assertEqual(list((self.database / ".basaltwater-scrub").iterdir()), [Path(self.report.path)])

    def test_changed_finding_file_or_configuration_rejects_before_recovery(self):
        with patch("sync.service_tools.scrub_manage.remediate") as remediate:
            self.file.write_bytes(b"changed after review")
            with self.assertRaisesRegex(data_job.DataJobRejected, "file_changed"):
                data_job.recover(self.value, self.saved)
            self.report.record("file.bin", {"category": "unrepairable"})
            with self.assertRaisesRegex(data_job.DataJobRejected, "finding_changed"):
                data_job.recover(self.value, self.saved)
            with self.assertRaisesRegex(ValueError, "No configured scrub job"):
                data_job.recover(self.value, {"scrub_specs": []})
            remediate.assert_not_called()

    def test_legacy_identity_requires_verify_and_verify_updates_report(self):
        del self.report.data["files"]["file.bin"]["file_identity"]
        self.report.save()
        value = {**self.value, "finding": finding_revision(self.report.data["files"]["file.bin"])}
        with self.assertRaisesRegex(data_job.DataJobRejected, "identity_unavailable"):
            data_job.recover(value, self.saved)
        with patch("sync.service_tools.scrub_manage.examine", return_value={"category": "healthy"}):
            result, code = data_job.recover({**value, "action": "verify"}, self.saved)
        self.assertEqual(code, 0)
        self.assertEqual(result["category"], "healthy")
        self.assertFalse(Findings(str(self.directory), str(self.database)).active("file.bin"))

    def run_job(self, recover=None):
        return self.enterContext(patch.multiple(data_job, DATA_STATE=str(self.root / "job.json"), LOCK_FILE=str(self.root / "storage.lock"),
            protected_path=Mock(), can_manage_system_services=Mock(return_value=True),
            load_setup_config=Mock(return_value=self.saved), recover=recover or Mock(return_value=({"category": "healthy", "evidence": "PRIVATE EVIDENCE"}, 0))))

    def test_job_retains_only_safe_results_and_rejects_replay(self):
        self.run_job()
        with patch.object(data_job.os, "geteuid", return_value=0):
            self.assertEqual(data_job.run_action(self.value), 0)
            with self.assertRaisesRegex(ValueError, "already claimed"):
                data_job.run_action(self.value)
        raw = (self.root / "job.json").read_text()
        self.assertNotIn("PRIVATE EVIDENCE", raw)
        self.assertEqual(os.stat(self.root / "job.json").st_mode & 0o777, 0o600)
        with patch.object(data_job, "_unit_state", return_value="inactive"):
            self.assertEqual(data_job.job_status()["latest"]["status"], "succeeded")

    def test_failure_is_redacted_and_storage_lock_blocks_effects(self):
        recover = Mock(side_effect=ValueError("PRIVATE EVIDENCE"))
        self.run_job(recover=recover)
        with patch.object(data_job.os, "geteuid", return_value=0):
            self.assertEqual(data_job.run_action(self.value), 1)
        self.assertNotIn("PRIVATE EVIDENCE", (self.root / "job.json").read_text())
        recover.reset_mock()
        with data_job.OperationLock(data_job.LOCK_FILE) as lock:
            self.assertTrue(lock.acquire())
            with patch.object(data_job.os, "geteuid", return_value=0):
                self.assertEqual(data_job.run_action({**self.value, "job_id": "d" * 32}), 1)
        recover.assert_not_called()
        self.assertEqual(json.loads((self.root / "job.json").read_text())["failure"], "storage_busy")

    def test_interrupted_or_racing_status_never_reports_completion(self):
        record = {"parameters": self.value, "status": "running"}
        with patch.object(data_job, "_record", return_value=copy.deepcopy(record)), patch.object(data_job, "_unit_state", return_value="inactive"), patch.object(data_job, "can_manage_system_services", return_value=True):
            self.assertEqual(data_job.job_status()["latest"]["status"], "interrupted")
        with patch.object(data_job, "_record", side_effect=[record, {}]), patch.object(data_job, "_unit_state", return_value="inactive"):
            self.assertFalse(data_job.job_status()["available"])

    def test_private_payload_preserves_literal_paths_and_is_consumed_once(self):
        self.run_job()
        value = {**self.value, "file": str(self.directory / "$HOME-%n.bin")}
        data_job.stage_data_job(value)
        path = Path(data_job._payload_path(value["job_id"]))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with self.assertRaisesRegex(ValueError, "already exists"):
            data_job.stage_data_job(value)
        self.assertEqual(data_job.consume_data_job(value["job_id"]), value)
        self.assertFalse(path.exists())
        with self.assertRaises(FileNotFoundError):
            data_job.consume_data_job(value["job_id"])


class DataReviewTests(unittest.TestCase):
    def setUp(self):
        self.admin = PanelAdmin({"host": "vm.example", "services": [{"label": "Privilege approvals", "url": "https://vm.example:9444/"}]})
        self.manager = actions.PanelData(self.admin)
        self.state = SimpleNamespace(manifest={"host": "vm.example"}, admin=self.admin, data=self.manager, csrf_token="csrf",
            features=lambda: PanelFeatures(admin=True), navigation=lambda current: panel_navigation(current=current))

    def fields(self):
        return {key: value for key, value in parameters().items() if key != "job_id"}

    def test_confirmation_single_use_and_lost_response_never_replay(self):
        ticket, _ = self.manager.prepare(self.fields())
        with patch.object(actions, "exchange", side_effect=[status(), OSError("Lost response")]) as exchange:
            with self.assertRaises(OSError):
                self.manager.submit(ticket, "CHANGE FILE")
            with self.assertRaises(ValueError):
                self.manager.submit(ticket, "CHANGE FILE")
            self.assertEqual(exchange.call_count, 2)
            self.assertEqual(exchange.call_args.args[0]["operation"], "data.run")
        for action in ("repair", "restore", "accept"):
            fields = {**self.fields(), "action": action, "backup": "/srv/backup/file.bin" if action == "restore" else ""}
            ticket, _ = self.manager.prepare(fields)
            with patch.object(actions, "exchange") as exchange, self.assertRaises(ValueError):
                self.manager.submit(ticket, "")
            exchange.assert_not_called()

    def test_busy_unavailable_and_expired_reviews_cannot_request_recovery(self):
        for snapshot in ({**status(), "unit_state": "active"}, {**status(), "available": False},
                         {**status(), "requests": [{"state": "pending"}]}):
            ticket, _ = self.manager.prepare(self.fields())
            with patch.object(actions, "exchange", return_value=snapshot) as exchange, self.assertRaises(ValueError):
                self.manager.submit(ticket, "CHANGE FILE")
            exchange.assert_called_once_with({"action": "data-status"})
        ticket, _ = self.manager.prepare(self.fields())
        with patch.object(actions.time, "monotonic", return_value=float("inf")), patch.object(actions, "exchange") as exchange, self.assertRaises(ValueError):
            self.manager.submit(ticket, "CHANGE FILE")
        exchange.assert_not_called()

    def handler(self, path, values):
        handler = object.__new__(WebPanelHandler)
        handler.state, handler.path = self.state, path
        body = urlencode(values, doseq=True).encode()
        handler.headers = http.client.HTTPMessage()
        handler.headers["Content-Length"] = str(len(body))
        handler.rfile, handler.wfile = io.BytesIO(body), io.BytesIO()
        handler._send = Mock()
        return handler

    def test_http_review_and_confirm_create_only_one_approval_request(self):
        with patch.object(actions, "exchange", return_value=status()) as exchange, patch.object(storage, "load_storage_snapshot", return_value={"available": False}):
            handler = self.handler("/actions/data/review", {"csrf": "csrf", **self.fields()})
            handler.do_POST()
            self.assertEqual(handler._send.call_args.args[0], 200)
            document = handler._send.call_args.args[1]
            ticket = re.search(r'name="ticket" value="([^"]+)"', document).group(1)
            self.assertIn("CHANGE FILE", document)
            self.assertTrue(all(call.args[0]["action"] == "data-status" for call in exchange.call_args_list))
        with patch.object(actions, "exchange", side_effect=[status(), {"id": "c" * 32, "state": "pending"}]) as exchange:
            handler = self.handler("/actions/data/submit", {"csrf": "csrf", "ticket": ticket, "confirmation": "CHANGE FILE"})
            handler.do_POST()
            self.assertEqual(handler._send.call_args.args[0], 303)
            self.assertEqual(exchange.call_args.args[0]["parameters"]["file"], parameters()["file"])

    def test_http_missing_csrf_duplicate_or_extra_fields_cannot_submit(self):
        for values, expected in (({"ticket": "x"}, 403), ({"csrf": "csrf", "ticket": ["x", "y"], "confirmation": ""}, 400),
                                 ({"csrf": "csrf", "ticket": "x", "confirmation": "", "approve": "1"}, 400)):
            with patch.object(self.manager, "submit") as submit:
                handler = self.handler("/actions/data/submit", values)
                handler.do_POST()
                self.assertEqual(handler._send.call_args.args[0], expected)
                submit.assert_not_called()

    def test_invalid_review_explains_the_field_without_creating_a_request(self):
        with patch.object(actions, "exchange", return_value=status()) as exchange, patch.object(storage, "load_storage_snapshot", return_value={"available": False}):
            handler = self.handler("/actions/data/review", {"csrf": "csrf", **self.fields(), "action": "verify", "backup": "/srv/backup/file.bin"})
            handler.do_POST()
            self.assertEqual(handler._send.call_args.args[0], 422)
            self.assertIn("A backup path is allowed only for restore", handler._send.call_args.args[1])
            self.assertTrue(all(call.args[0]["action"] == "data-status" for call in exchange.call_args_list))
