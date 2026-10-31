"""Samba controls preserve credentials and require one independently approved plan."""

from __future__ import annotations

from contextlib import nullcontext
import http.client
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode

from common.service_tools import privilege_broker as broker_module, web_panel_samba as panel, web_panel_storage as storage
from common.service_tools.web_panel_admin import PanelAdmin
from common.service_tools.web_panel_features import PanelFeatures
from common.service_tools.web_panel_service import WebPanelHandler
from lib.operation_state import OperationStateStore
from lib.privilege_execution import ExecutionResult
from lib.privilege_client import PrivilegeRequestUnconfirmed
from lib.privilege_policy import canonical, operation_plan
from lib.samba_accounts import account_dispatch_argv, validate_account_action, validate_account_snapshot, validate_reset_password
from smb import account_management as accounts
from smb import password_staging as staging


def parameters():
    return {"action": "disable", "username": "alice", "revision": "a" * 64, "job_id": "b" * 32}


def row():
    return {"username": "alice", "uid": 1000, "disabled": False, "locked": False, "manageable": True, "revision": "a" * 64}


def policy():
    return {"version": 1, "machine": "a" * 32, "origin": "https://nas.example:9444", "requester_uid": 1000,
            "ttl_seconds": 300, "services": {}, "reboot": "deny", "commands": "approve"}


class SambaAccountInspectionTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(accounts, "_inspection_database", side_effect=lambda _private: nullcontext("/private-copy")))

    def inspect(self, *, listing="alice:1000:PRIVATE FULL NAME\n", details=None, uid=1000):
        details = details if details is not None else "Unix username: alice\nAccount Flags: [U ]\nUser SID: S-1-5-21-1-2-3-1000\nFull Name: PRIVATE\n"
        self.inspection = self.enterContext(patch.object(accounts, "_inspect", side_effect=[
            "standalone server\n", "tdbsam\n", "/var/lib/samba/private\n", listing, details]))
        self.enterContext(patch.object(accounts, "_stamp", return_value=(1, 2, 3, 4, 5)))
        self.enterContext(patch.object(accounts.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=uid)))

    def test_export_projects_only_account_flags_and_identity_without_hashes(self):
        self.inspect(details="Unix username: alice\nAccount Flags: [DUXL ]\nUser SID: S-1-5-21-1-2-3-1000\nFull Name: PRIVATE\n")
        value = accounts.collect_account_snapshot({"enable_samba": True})
        self.assertTrue(value["available"])
        self.assertEqual(value["accounts"][0]["username"], "alice")
        self.assertTrue(value["accounts"][0]["disabled"])
        self.assertTrue(value["accounts"][0]["locked"])
        self.assertTrue(value["accounts"][0]["manageable"])
        self.assertNotIn("PRIVATE", json.dumps(value))
        self.assertTrue(all("-w" not in call.args[0] for call in self.inspection.call_args_list))
        self.assertEqual(accounts.collect_account_snapshot({}), {"available": False})

    def test_system_missing_or_mismatched_unix_accounts_have_no_controls(self):
        for uid in (0, 999, 1001, 65534):
            with self.subTest(uid=uid), patch.object(accounts, "_inspect", side_effect=[
                "standalone server", "tdbsam", "/var/lib/samba/private", "alice:1000:Name\n",
                "Unix username: alice\nAccount Flags: [U ]\nUser SID: S-1-5-21-1-2-3-1000\n"]), \
                 patch.object(accounts, "_stamp", return_value=(1, 2, 3, 4, 5)), \
                 patch.object(accounts.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=uid)):
                self.assertFalse(accounts._accounts()[0]["manageable"])
        self.inspect()
        with patch.object(accounts.pwd, "getpwnam", side_effect=KeyError):
            self.assertIsNone(accounts._accounts()[0]["uid"])

    def test_incomplete_or_changed_inventory_never_becomes_an_empty_account_list(self):
        for details in ("", "Unix username: alice\n", "Unix username: unknown\nAccount Flags: [U ]\n",
                        "Unix username: alice\nAccount Flags: [U ]\nAccount Flags: [DU ]\n"):
            with self.subTest(details=details), patch.object(accounts, "_inspect", side_effect=[
                "standalone server", "tdbsam", "/var/lib/samba/private", "alice:1000:Name\n", details]), \
                 patch.object(accounts, "_stamp", return_value=(1, 2, 3, 4, 5)):
                self.assertEqual(accounts.collect_account_snapshot({"enable_samba": True}), {"available": False})
        self.inspect()
        with patch.object(accounts, "_stamp", side_effect=[(1,), (2,), (3,)]):
            self.assertEqual(accounts.collect_account_snapshot({"enable_samba": True}), {"available": False})

    def test_tool_reads_are_bounded_and_timeouts_are_unavailable(self):
        def output(argv, **kwargs):
            kwargs["stdout"].write(b"x" * 9)
            return SimpleNamespace(returncode=0)
        with patch.object(accounts, "MAX_OUTPUT", 8), patch.object(accounts.subprocess, "run", side_effect=output) as run:
            with self.assertRaises(ValueError):
                accounts._inspect(["/usr/bin/pdbedit", "-L"])
            self.assertIn("--fsize=9", run.call_args.args[0])
            self.assertEqual(run.call_args.kwargs["timeout"], 3)
        with patch.object(accounts, "_accounts", side_effect=subprocess.TimeoutExpired("pdbedit", 3)):
            self.assertEqual(accounts.collect_account_snapshot({"enable_samba": True}), {"available": False})

    def test_other_account_writes_preserve_review_but_identity_or_flags_invalidate_it(self):
        def inspect(control="[U ]", sid="S-1-5-21-1-2-3-1000", database=2):
            with patch.object(accounts, "_inspect", side_effect=["standalone server", "tdbsam", "/var/lib/samba/private",
                "alice:1000:Name\n", f"Unix username: alice\nAccount Flags: {control}\nUser SID: {sid}\n"]), \
                 patch.object(accounts, "_stamp", side_effect=[(1,), (database,), (database,), (1,)]), \
                 patch.object(accounts.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=1000)):
                return accounts._accounts()[0]["revision"]
        revision = inspect()
        self.assertEqual(inspect(database=3), revision)
        self.assertNotEqual(inspect(control="[DU ]"), revision)
        self.assertNotEqual(inspect(sid="S-1-5-21-1-2-3-1001"), revision)

    def test_malformed_account_export_does_not_suppress_other_storage_information(self):
        snapshot = {"version": 1, "generated_at": "2026-10-31T12:00:00+00:00", "available": True,
                    "jobs": [], "omitted_jobs": 0, "samba": {"available": True, "accounts": [{**row(), "password": "PRIVATE"}]}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "storage.json"
            path.write_text(json.dumps(snapshot))
            from datetime import datetime
            loaded = storage.load_storage_snapshot(str(path), now=datetime.fromisoformat(snapshot["generated_at"]))
        self.assertTrue(loaded["available"])
        self.assertEqual(loaded["samba"], {"available": False})
        self.assertNotIn("PRIVATE", json.dumps(loaded))
        with self.assertRaises(ValueError):
            validate_account_snapshot({"available": True, "accounts": [row(), row()]})


class SambaAccountExecutionTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.marker = Path(directory) / "setup-operation.json"
        self.enterContext(patch.object(accounts, "STATE_DIR", directory))
        self.enterContext(patch.object(accounts, "protected_path"))
        self.enterContext(patch.object(accounts.os, "geteuid", return_value=0))
        self.enterContext(patch.object(accounts, "load_setup_config", return_value={"enable_samba": True}))
        self.run = self.enterContext(patch.object(accounts.subprocess, "run"))

    def test_only_the_disabled_flag_changes_under_setup_exclusion(self):
        for action, disabled in (("disable", False), ("enable", True)):
            before = {**row(), "disabled": disabled}
            after = {**before, "disabled": not disabled}
            with self.subTest(action=action), patch.object(accounts, "_accounts", side_effect=[[before], [after]]):
                accounts.change_account({**parameters(), "action": action})
                self.assertEqual(self.run.call_args.args[0], ["/usr/bin/smbpasswd", "-d" if action == "disable" else "-e", "alice"])
                self.assertNotIn("input", self.run.call_args.kwargs)
                self.assertEqual(self.run.call_args.kwargs["timeout"], 15)
        self.assertFalse(self.marker.exists())

    def test_stale_privileged_missing_and_already_applied_requests_have_no_effect(self):
        for rows in ([], [{**row(), "revision": "c" * 64}], [{**row(), "manageable": False}], [{**row(), "disabled": True}]):
            with self.subTest(rows=rows), patch.object(accounts, "_accounts", return_value=rows), self.assertRaises(ValueError):
                accounts.change_account(parameters())
        self.run.assert_not_called()
        with OperationStateStore(str(self.marker)).ownership() as store:
            store.begin("setup", "host", "started")
        with self.assertRaisesRegex(ValueError, "Setup or recovery"):
            accounts.change_account(parameters())
        self.run.assert_not_called()

    def test_unverified_effect_is_not_success_and_lock_excludes_competing_setup(self):
        with patch.object(accounts, "_accounts", return_value=[row()]), self.assertRaisesRegex(ValueError, "could not be verified"):
            accounts.change_account(parameters())
        self.run.reset_mock()
        with OperationStateStore(str(self.marker)).ownership(), self.assertRaises(ValueError):
            accounts.change_account(parameters())
        self.run.assert_not_called()


class SambaApprovalTests(unittest.TestCase):
    def test_typed_public_plan_requires_approval_and_waits_for_verified_result(self):
        plan = operation_plan(policy(), 1000, "samba.account", parameters())
        self.assertEqual(plan["mode"], "approve")
        self.assertIn("--wait", plan["argv"])
        self.assertIn("--property=RuntimeMaxSec=45", plan["argv"])
        self.assertEqual(plan["argv"], account_dispatch_argv(parameters()))
        for field, invalid in (("username", "alice;id"), ("action", "delete-unix-user"), ("revision", ""), ("job_id", "%n")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_account_action({**parameters(), field: invalid})
        with self.assertRaises(ValueError):
            validate_account_action({**parameters(), "password": "PRIVATE"})
        with patch.object(broker_module, "protected_path"), patch.object(broker_module.subprocess, "run", return_value=SimpleNamespace(returncode=0)):
            self.assertEqual(broker_module.execute(plan), ExecutionResult("succeeded", exit_code=0))

    def test_approval_deny_cancel_and_owned_status_are_one_shot(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = Mock(return_value=ExecutionResult("succeeded", exit_code=0))
            broker = broker_module.Broker(str(Path(directory) / "requests.db"), policy_loader=policy, runner=runner)
            self.addCleanup(broker.db.close)
            denied = broker.request(1000, "samba.account", parameters(), "Disable login")
            self.assertFalse(broker.execute_next())
            with self.assertRaises(ValueError):
                broker.request(1000, "samba.account", parameters(), "Duplicate")
            broker.decide(denied["id"], denied["digest"], False, "operator")
            self.assertFalse(broker.execute_next())
            cancelled = broker.request(1000, "samba.account", parameters(), "Cancel")
            broker.cancel(cancelled["id"], 1000)
            self.assertFalse(broker.execute_next())
            request = broker.request(1000, "samba.account", parameters(), "Approve")
            broker.decide(request["id"], request["digest"], True, "operator")
            self.assertTrue(broker.execute_next())
            self.assertFalse(broker.execute_next())
            runner.assert_called_once_with(request["plan"])
            with patch.object(broker_module, "protected_path"):
                status = broker.dispatch({"action": "samba-status"}, 1000)
                self.assertEqual(status["requests"][0]["state"], "succeeded")
                with self.assertRaises(PermissionError):
                    broker.dispatch({"action": "samba-status"}, 1001)


class SambaPanelTests(unittest.TestCase):
    def setUp(self):
        self.admin = PanelAdmin({"host": "nas.example", "services": [{"label": "Privilege approvals", "url": "https://nas.example:9444/"}]})
        self.manager = panel.PanelSamba(self.admin)
        self.state = SimpleNamespace(manifest={"host": "nas.example"}, samba=self.manager, admin=self.admin,
                                     csrf_token="csrf", features=lambda: PanelFeatures(admin=True), navigation=lambda _current: ())

    def handler(self, path, values):
        handler = object.__new__(WebPanelHandler)
        handler.state, handler.path = self.state, path
        body = urlencode(values, doseq=True).encode()
        handler.headers = http.client.HTTPMessage()
        handler.headers["Content-Length"] = str(len(body))
        handler.rfile, handler.wfile = io.BytesIO(body), io.BytesIO()
        handler._send = Mock()
        return handler

    def test_http_review_then_confirm_creates_only_an_approval_request(self):
        fields = {key: value for key, value in parameters().items() if key != "job_id"}
        with patch.object(panel, "exchange", return_value={"available": True, "requests": []}) as exchange, \
             patch.object(storage, "load_storage_snapshot", return_value={"available": False}):
            handler = self.handler("/actions/samba/review", {"csrf": "csrf", **fields})
            handler.do_POST()
            self.assertEqual(handler._send.call_args.args[0], 200)
            document = handler._send.call_args.args[1]
            ticket = re.search(r'name="ticket" value="([^"]+)"', document).group(1)
            self.assertIn("separate approval page", document)
            self.assertTrue(all(call.args[0] == {"action": "samba-status"} for call in exchange.call_args_list))
            handler = self.handler("/actions/samba/submit", {"csrf": "csrf", "ticket": ticket, "confirmation": "alice"})
            handler.do_POST()
            self.assertEqual(handler._send.call_args.args[0], 303)
            self.assertEqual(exchange.call_args.args[0]["operation"], "samba.account")

    def test_wrong_confirmation_expiry_and_lost_response_never_replay(self):
        fields = {key: value for key, value in parameters().items() if key != "job_id"}
        ticket, _ = self.manager.prepare(fields)
        with patch.object(panel, "exchange") as exchange, self.assertRaises(ValueError):
            self.manager.submit(ticket, "wrong")
        exchange.assert_not_called()
        ticket, _ = self.manager.prepare(fields)
        with patch.object(panel.time, "monotonic", return_value=float("inf")), self.assertRaises(ValueError):
            self.manager.submit(ticket, "alice")
        ticket, _ = self.manager.prepare(fields)
        with patch.object(panel, "exchange", side_effect=[{"available": True, "requests": []}, OSError("Lost response")]) as exchange:
            with self.assertRaises(OSError):
                self.manager.submit(ticket, "alice")
            with self.assertRaises(ValueError):
                self.manager.submit(ticket, "alice")
            self.assertEqual(exchange.call_count, 2)

    def test_csrf_and_exact_fields_guard_all_samba_posts(self):
        for fields, code in (({}, 403), ({"csrf": "csrf", "ticket": ["x", "y"], "confirmation": "alice"}, 400),
                             ({"csrf": "csrf", "ticket": "x", "confirmation": "alice", "approve": "yes"}, 400)):
            with patch.object(self.manager, "submit") as submit:
                handler = self.handler("/actions/samba/submit", fields)
                handler.do_POST()
                self.assertEqual(handler._send.call_args.args[0], code)
                submit.assert_not_called()

    def test_rendering_is_local_and_system_accounts_have_no_action_forms(self):
        snapshot = {"available": True, "accounts": [row(), {**row(), "username": "root", "uid": 0, "manageable": False}]}
        with patch("subprocess.run") as run, patch.object(panel, "exchange") as exchange:
            document = panel.render_accounts(snapshot, "csrf", controls=True)
        run.assert_not_called()
        exchange.assert_not_called()
        self.assertEqual(document.count('<form '), 1)
        self.assertIn("Review disable Samba login", document)
        self.assertIn("non-system Unix account", document)
        self.assertNotIn('<form ', panel.render_accounts(snapshot, "csrf", controls=False))

    def test_https_password_form_stages_once_and_never_echoes_the_secret(self):
        self.state.credential_transport_available = lambda: True
        secret = "PRIVATE-new-password-123"
        def rpc(message):
            if message["action"] == "samba-stage-password":
                return {"job_id": message["parameters"]["job_id"], "expires": time.time() + 3600}
            return {"available": True, "requests": []}
        fields = {key: value for key, value in parameters().items() if key != "job_id"}
        fields["action"] = "reset-password"
        with patch.object(panel, "exchange", side_effect=rpc) as exchange, \
             patch.object(storage, "load_storage_snapshot", return_value={"available": False}), \
             patch("common.service_tools.web_panel_service.os.geteuid", return_value=1000):
            handler = self.handler("/actions/samba/review", {"csrf": "csrf", **fields})
            handler.headers["X-Forwarded-Proto"] = "https"
            handler.do_POST()
            document = handler._send.call_args.args[1]
            self.assertIn('type="password"', document)
            ticket = re.search(r'name="ticket" value="([^"]+)"', document).group(1)
            values = {"csrf": "csrf", "ticket": ticket, "confirmation": "alice", "password": secret, "password_repeat": secret}
            handler = self.handler("/actions/samba/submit", values)
            handler.headers["X-Forwarded-Proto"] = "https"
            handler.do_POST()
            self.assertEqual(handler._send.call_args.args[0], 303)
            messages = [call.args[0] for call in exchange.call_args_list]
            self.assertEqual(sum(message["action"] == "samba-stage-password" for message in messages), 1)
            public = next(message for message in messages if message["action"] == "request")
            self.assertNotIn(secret, canonical(public))
            self.assertNotIn(secret, str(handler._send.call_args))
            with self.assertRaises(ValueError):
                self.manager.submit(ticket, "alice", password=secret, password_repeat=secret)

    def test_password_posts_require_https_and_do_not_accept_duplicate_secret_fields(self):
        self.state.credential_transport_available = lambda: True
        values = {"csrf": "csrf", "ticket": "ticket", "confirmation": "alice", "password": "PRIVATE-password", "password_repeat": "PRIVATE-password"}
        with patch.object(self.manager, "submit") as submit:
            for changed, code in (({}, 403), ({"password": ["one", "two"]}, 400)):
                handler = self.handler("/actions/samba/submit", {**values, **changed})
                handler.do_POST()
                self.assertEqual(handler._send.call_args.args[0], code)
            submit.assert_not_called()

    def test_reset_mismatch_and_lost_response_never_replay_or_discard_an_uncertain_request(self):
        fields = {key: value for key, value in parameters().items() if key != "job_id"}
        fields["action"] = "reset-password"
        ticket, _ = self.manager.prepare(fields)
        with patch.object(panel, "exchange", return_value={"available": True, "requests": []}) as rpc, self.assertRaises(ValueError):
            self.manager.submit(ticket, "alice", password="PRIVATE-password", password_repeat="different-password")
        self.assertEqual(rpc.call_count, 1)
        ticket, value = self.manager.prepare(fields)
        with patch.object(panel, "exchange", side_effect=[{"available": True, "requests": []},
                {"job_id": value["job_id"], "expires": time.time() + 3600}, PrivilegeRequestUnconfirmed("lost")]) as rpc:
            with self.assertRaises(PrivilegeRequestUnconfirmed):
                self.manager.submit(ticket, "alice", password="PRIVATE-password", password_repeat="PRIVATE-password")
            self.assertEqual(rpc.call_count, 3)
            with self.assertRaises(ValueError):
                self.manager.submit(ticket, "alice", password="PRIVATE-password", password_repeat="PRIVATE-password")


class PasswordStagingTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.directory = str(Path(directory) / "secrets")
        self.enterContext(patch.object(staging, "PASSWORD_DIRECTORY", self.directory))
        self.enterContext(patch.object(staging, "protected_path"))
        self.findmnt = self.enterContext(patch.object(staging.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="tmpfs\n")))
        self.enterContext(patch.object(staging, "load_policy", return_value=policy()))
        self.rows = self.enterContext(patch.object(accounts, "_accounts", side_effect=AssertionError("Staging must not open Samba's database")))
        self.binding = self.enterContext(patch.object(accounts, "password_binding", return_value="d" * 64))
        self.value = {**parameters(), "action": "reset-password"}
        self.secret = "PRIVATE-samba-password"

    def stage(self, value=None):
        return staging.stage_password(value or self.value, self.secret, 1000)

    def test_secret_is_private_runtime_only_consumed_once_and_bound_to_public_parameters(self):
        result = self.stage()
        self.rows.assert_not_called()
        path = Path(staging._path(self.value["job_id"]))
        self.assertNotIn(self.secret, canonical(result))
        self.assertEqual(Path(self.directory).stat().st_mode & 0o777, 0o700)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        staging.require_password(self.value, 1000)
        for changed, uid in (({**self.value, "username": "bob"}, 1000), (self.value, 1001)):
            with self.assertRaises(ValueError):
                staging.require_password(changed, uid)
        with self.assertRaises(ValueError):
            self.stage()
        self.assertEqual(staging.consume_password(self.value), self.secret)
        self.assertFalse(path.exists())
        with self.assertRaises(OSError):
            staging.consume_password(self.value)

    def test_expiry_changed_passdb_and_restart_cleanup_remove_secrets_without_effects(self):
        self.stage()
        self.binding.return_value = "e" * 64
        with self.assertRaises(ValueError):
            staging.consume_password(self.value)
        self.assertFalse(Path(staging._path(self.value["job_id"])).exists())
        self.stage()
        with patch.object(staging.time, "time", return_value=time.time() + 3601), self.assertRaises(ValueError):
            staging.consume_password(self.value)
        self.stage()
        staging.prune_passwords(clear=True)
        self.assertFalse(list(Path(self.directory).glob("*.json")))

    def test_unsupported_storage_quota_and_unsafe_permissions_reject_staging(self):
        self.findmnt.return_value.stdout = "ext4\n"
        with self.assertRaises(ValueError):
            self.stage()
        self.assertFalse(Path(self.directory).exists())
        self.findmnt.return_value.stdout = "tmpfs\n"
        self.stage()
        with patch.object(staging, "MAX_STAGED", 1), self.assertRaises(ValueError):
            self.stage({**self.value, "job_id": "c" * 32})
        Path(self.directory).chmod(0o755)
        with self.assertRaises(ValueError):
            staging.require_password(self.value, 1000)
        self.assertEqual(validate_reset_password("界" * 128), "界" * 128)
        for password in ("short", " " * 12, "new\npassword123", "new\x00password123", "界" * 129, "\ud800" * 12, 123):
            with self.subTest(password=password), self.assertRaises(ValueError):
                validate_reset_password(password)

    def test_broker_never_persists_password_and_rejects_reused_staging_identity(self):
        self.enterContext(patch.object(broker_module, "protected_path"))
        broker = broker_module.Broker(str(Path(self.directory).parent / "requests.db"), policy_loader=policy)
        self.addCleanup(broker.db.close)
        message = {"action": "samba-stage-password", "parameters": self.value, "password": self.secret}
        with self.assertRaises(PermissionError):
            broker.dispatch(message, 1001)
        broker.dispatch(message, 1000)
        request = broker.request(1000, "samba.account", self.value, "Reset password")
        self.assertNotIn(self.secret, canonical(request))
        self.assertNotIn(self.secret, canonical([dict(row) for row in broker.db.execute("SELECT * FROM requests")]))
        with self.assertRaises(ValueError):
            broker.dispatch({"action": "samba-discard-password", "job_id": self.value["job_id"]}, 1000)
        broker.cancel(request["id"], 1000)
        self.assertFalse(Path(staging._path(self.value["job_id"])).exists())
        with self.assertRaises(ValueError):
            broker.dispatch(message, 1000)
        with self.assertRaises(ValueError):
            broker.request(1000, "samba.account", self.value, "Replay")


class SambaPasswordExecutionTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(accounts, "STATE_DIR", directory))
        self.enterContext(patch.object(accounts, "protected_path"))
        self.enterContext(patch.object(accounts.os, "geteuid", return_value=0))
        self.enterContext(patch.object(accounts, "load_setup_config", return_value={"enable_samba": True}))
        self.rows = self.enterContext(patch.object(accounts, "_accounts", return_value=[row()]))
        self.consume = self.enterContext(patch.object(staging, "consume_password", return_value="PRIVATE-samba-password"))
        self.binding = self.enterContext(patch.object(accounts, "password_binding", return_value="d" * 64))
        self.run = self.enterContext(patch.object(accounts.subprocess, "run"))
        self.passdb = Mock(error=type("PassdbError", (Exception,), {}))
        self.enterContext(patch.dict(sys.modules, {"samba": SimpleNamespace(),
                                                  "samba.samba3": SimpleNamespace(passdb=self.passdb)}))
        self.value = {**parameters(), "action": "reset-password"}

    def test_password_and_original_flags_are_written_together_without_unix_password_tools(self):
        for flags in (16, 17, 0x411):
            with self.subTest(flags=flags):
                account = SimpleNamespace(username="alice", acct_ctrl=flags, user_sid="unchanged")
                self.passdb.PDB.return_value.getsampwnam.return_value = account
                self.rows.return_value = [{**row(), "disabled": bool(flags & 1), "locked": bool(flags & 0x400)}]
                self.passdb.PDB.return_value.update_sam_account.side_effect = lambda current: self.assertEqual(
                    (current.username, current.acct_ctrl, current.user_sid, current.plaintext_passwd),
                    ("alice", flags, "unchanged", "PRIVATE-samba-password"))
                accounts.change_account(self.value)
                self.passdb.set_smb_config.assert_called_with(accounts.SMB_CONF)
                self.passdb.PDB.assert_called_with("tdbsam")
                self.consume.assert_called_with(self.value)
                self.run.assert_not_called()

    def test_changed_identity_or_metadata_never_reaches_the_database_update(self):
        database = self.passdb.PDB.return_value
        database.getsampwnam.return_value = SimpleNamespace(username="bob", acct_ctrl=17)
        with self.assertRaises(ValueError):
            accounts.change_account(self.value)
        database.update_sam_account.assert_not_called()
        database.getsampwnam.return_value = SimpleNamespace(username="alice", acct_ctrl=17)
        self.binding.side_effect = ["d" * 64, "e" * 64]
        with self.assertRaises(ValueError):
            accounts.change_account(self.value)
        database.update_sam_account.assert_not_called()

    def test_stale_or_ineligible_account_never_consumes_a_password(self):
        for changed in ({**row(), "manageable": False}, {**row(), "revision": "c" * 64}):
            with self.subTest(account=changed):
                self.rows.return_value = [changed]
                with self.assertRaises(ValueError):
                    accounts.change_account(self.value)
                self.consume.assert_not_called()
                self.passdb.PDB.assert_not_called()

    def test_binding_errors_are_sanitized_and_missing_bindings_never_consume_a_secret(self):
        self.passdb.PDB.side_effect = self.passdb.error("PRIVATE backend diagnostics")
        with self.assertRaisesRegex(RuntimeError, "^Samba password update failed$"):
            accounts.change_account(self.value)
        self.consume.reset_mock()
        with patch.dict(sys.modules, {"samba.samba3": None}), self.assertRaisesRegex(RuntimeError, "Python bindings"):
            accounts.change_account(self.value)
        self.consume.assert_not_called()
