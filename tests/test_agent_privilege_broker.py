"""Privilege requests never confer reusable or caller-controlled root authority."""

from __future__ import annotations

import argparse
import copy
from contextlib import redirect_stdout
import io
import json
import os
import struct
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from common.service_tools.privilege_broker import Broker, Handler, execute
from lib.privilege_client import add_privilege_parser, run_privilege_command
from lib.privilege_policy import MAX_MESSAGE, canonical, operation_plan, validate_policy


def policy() -> dict:
    return {"version": 1, "machine": "a" * 32, "origin": "https://vm.example:9444",
            "requester_uid": 1000, "ttl_seconds": 300,
            "services": {"demo.service": "approve"}, "reboot": "approve", "commands": "approve"}


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = os.path.join(self.directory.name, "state.db")
        self.policy = policy()
        self.runner = Mock(return_value="succeeded")
        self.broker = Broker(self.path, lambda: copy.deepcopy(self.policy), self.runner)
        self.addCleanup(lambda: self.broker.db.close())

    def request(self):
        return self.broker.request(1000, "service.restart", {"unit": "demo.service"}, "Apply reviewed configuration")

    def test_approval_executes_once_and_keeps_audit(self):
        request = self.request()
        self.assertFalse(self.broker.execute_next())
        self.broker.decide(request["id"], request["digest"], True, "operator")
        self.assertTrue(self.broker.execute_next())
        self.assertFalse(self.broker.execute_next())
        self.runner.assert_called_once_with(request["plan"])
        with self.assertRaises(ValueError):
            self.broker.decide(request["id"], request["digest"], True, "operator")
        events = self.broker.db.execute("SELECT state FROM events ORDER BY sequence").fetchall()
        self.assertEqual([row[0] for row in events], ["pending", "approved", "executing", "succeeded"])

    def test_request_interface_cannot_approve_or_impersonate(self):
        request = self.request()
        with self.assertRaises(PermissionError):
            self.broker.dispatch({"action": "decide", "id": request["id"], "digest": request["digest"], "approve": True, "actor": "operator"}, 1000)
        with self.assertRaises(PermissionError):
            self.broker.status(request["id"], 1001)
        with self.assertRaises(PermissionError):
            self.broker.request(1001, "system.reboot", {}, "test")

    def test_owner_cancellation_is_durable_and_rejects_stale_approval(self):
        request = self.request()
        result = self.broker.dispatch({"action": "cancel", "id": request["id"]}, 1000)
        self.assertEqual(result["state"], "cancelled")
        self.assertEqual(result["actor"], "uid:1000")
        self.assertEqual(self.broker.cancel(request["id"], 1000)["state"], "cancelled")
        with self.assertRaises(ValueError):
            self.broker.decide(request["id"], request["digest"], True, "operator")
        self.broker.db.close()
        self.broker = Broker(self.path, lambda: self.policy, self.runner)
        self.assertEqual(self.broker.status(request["id"])["state"], "cancelled")
        self.assertFalse(self.broker.execute_next())
        self.runner.assert_not_called()
        events = self.broker.db.execute("SELECT state FROM events ORDER BY sequence").fetchall()
        self.assertEqual([row[0] for row in events], ["pending", "cancelled"])

    def test_cancellation_can_withdraw_approval_but_cannot_impersonate_an_owner(self):
        request = self.request()
        for message, uid, approval in (
            ({"action": "cancel", "id": request["id"]}, 1001, False),
            ({"action": "cancel", "id": request["id"], "uid": 1000}, 1000, False),
            ({"action": "cancel", "id": request["id"]}, 1000, True),
        ):
            with self.subTest(message=message, uid=uid), self.assertRaises(PermissionError):
                self.broker.dispatch(message, uid, approval=approval)
        self.broker.decide(request["id"], request["digest"], True, "operator")
        self.assertEqual(self.broker.cancel(request["id"], 1000)["state"], "cancelled")
        self.assertFalse(self.broker.execute_next())
        self.runner.assert_not_called()

    def test_cancellation_cannot_stop_or_relabel_a_claimed_action(self):
        request = self.request()
        self.broker.decide(request["id"], request["digest"], True, "operator")
        started, release = threading.Event(), threading.Event()
        def run(plan):
            started.set()
            release.wait(5)
            return "succeeded"
        self.broker.runner = run
        worker = threading.Thread(target=self.broker.execute_next)
        worker.start()
        try:
            self.assertTrue(started.wait(5))
            with self.assertRaisesRegex(ValueError, "claimed"):
                self.broker.cancel(request["id"], 1000)
            self.assertEqual(self.broker.status(request["id"])["state"], "executing")
        finally:
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        with self.assertRaisesRegex(ValueError, "claimed"):
            self.broker.cancel(request["id"], 1000)
        self.assertEqual(self.broker.status(request["id"])["state"], "succeeded")

    def test_expired_cancellation_is_a_noop_and_cancellation_keeps_hourly_quota(self):
        request = self.request()
        with patch("common.service_tools.privilege_broker.time.time", return_value=request["expires"] + 1):
            self.assertEqual(self.broker.cancel(request["id"], 1000)["state"], "expired")
        for _ in range(29):
            self.broker.cancel(self.request()["id"], 1000)
        with self.assertRaisesRegex(ValueError, "quota"):
            self.request()
        self.runner.assert_not_called()

    def test_no_caller_supplied_commands_or_extra_fields(self):
        for operation, parameters in (("shell", {}), ("system.reboot", {"argv": ["sh"]}),
                                      ("service.restart", {"unit": "--help"}),
                                      ("service.restart", {"unit": "other.service"})):
            with self.subTest(operation=operation, parameters=parameters), self.assertRaises((ValueError, PermissionError)):
                self.broker.request(1000, operation, parameters, "test")

    def test_exact_command_is_reviewable_and_requires_approval(self):
        request = self.broker.request(1000, "command.run", {"argv": ["/usr/bin/true"]}, "Verify command approval")
        self.assertEqual(request["plan"]["argv"], ["/usr/bin/true"])
        self.broker.decide(request["id"], request["digest"], True, "operator")
        self.broker.execute_next()
        self.runner.assert_called_once_with(request["plan"])
        for argv in ([], ["sudo", "id"], ["bad command"], ["/bin/echo", "line\nbreak"]):
            with self.subTest(argv=argv), self.assertRaises(ValueError):
                self.broker.request(1000, "command.run", {"argv": argv}, "test")

    def test_review_digest_is_required(self):
        request = self.request()
        with self.assertRaises(ValueError):
            self.broker.decide(request["id"], "0" * 64, True, "operator")
        self.assertFalse(self.broker.execute_next())

    def test_changed_policy_invalidates_approval_and_execution(self):
        for before_approval in (True, False):
            request = self.request()
            if not before_approval:
                self.broker.decide(request["id"], request["digest"], True, "operator")
            self.policy["ttl_seconds"] += 1
            if before_approval:
                self.broker.decide(request["id"], request["digest"], True, "operator")
            else:
                self.broker.execute_next()
            self.assertEqual(self.broker.status(request["id"])["state"], "invalidated")
        self.runner.assert_not_called()

    def test_expiry_and_denial_never_execute(self):
        request = self.request()
        with patch("common.service_tools.privilege_broker.time.time", return_value=request["expires"] + 1):
            self.assertEqual(self.broker.status(request["id"])["state"], "expired")
        request = self.request()
        self.broker.decide(request["id"], request["digest"], False, "operator")
        self.assertFalse(self.broker.execute_next())
        self.runner.assert_not_called()

    def test_registered_allow_rule_skips_prompt_but_is_audited(self):
        self.policy["services"]["demo.service"] = "allow"
        request = self.request()
        self.assertEqual(request["state"], "approved")
        self.assertEqual(request["actor"], "administrator-policy")
        self.broker.execute_next()
        self.runner.assert_called_once()

    def test_restart_marks_inflight_uncertain_without_retry(self):
        request = self.request()
        with self.broker.db:
            self.broker._transition(request["id"], "executing", "operator")
        self.broker.db.close()
        self.broker = Broker(self.path, lambda: self.policy, self.runner)
        self.assertEqual(self.broker.status(request["id"])["state"], "uncertain")
        self.assertFalse(self.broker.execute_next())

    def test_claim_is_committed_before_execution(self):
        request = self.request()
        self.broker.decide(request["id"], request["digest"], True, "operator")
        def inspect(plan):
            import sqlite3
            from contextlib import closing
            with closing(sqlite3.connect(self.path)) as db:
                self.assertEqual(db.execute("SELECT state FROM requests").fetchone()[0], "executing")
            return "succeeded"
        self.broker.runner = inspect
        self.broker.execute_next()

    def test_concurrent_decisions_only_one_succeeds(self):
        request = self.request()
        outcomes = []
        def decide():
            try:
                self.broker.decide(request["id"], request["digest"], True, "operator")
                outcomes.append(True)
            except ValueError:
                outcomes.append(False)
        threads = [threading.Thread(target=decide) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(outcomes.count(True), 1)

    def test_quota_survives_reopening_database(self):
        for _ in range(30):
            request = self.request()
            self.broker.decide(request["id"], request["digest"], False, "operator")
        self.broker.db.close()
        self.broker = Broker(self.path, lambda: self.policy, self.runner)
        with self.assertRaisesRegex(ValueError, "quota"):
            self.request()

    def test_invalid_policy_cannot_execute_previously_approved_request(self):
        request = self.request()
        self.broker.decide(request["id"], request["digest"], True, "operator")
        self.broker.policy_loader = Mock(side_effect=ValueError("Invalid policy"))
        self.broker.execute_next()
        self.runner.assert_not_called()

    @patch("common.service_tools.privilege_broker.subprocess.run")
    def test_runner_uses_fixed_argv_and_no_caller_environment(self, run):
        run.return_value.returncode = 0
        plan = operation_plan(self.policy, 1000, "service.restart", {"unit": "demo.service"})
        self.assertEqual(execute(plan), "succeeded")
        self.assertEqual(run.call_args.args[0], ["/usr/bin/systemctl", "--no-ask-password", "restart", "--", "demo.service"])
        self.assertNotIn("shell", run.call_args.kwargs)
        self.assertEqual(run.call_args.kwargs["cwd"], "/")


class PolicyTests(unittest.TestCase):
    def test_strict_policy(self):
        self.assertEqual(validate_policy(policy()), policy())
        for key, value in (("version", True), ("requester_uid", 0), ("ttl_seconds", 9999),
                           ("origin", "http://vm.example:9444"), ("origin", "https://vm.example:9444/path"),
                           ("services", {"*.service": "allow"}), ("reboot", "allow")):
            candidate = policy()
            candidate[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_policy(candidate)


class ClientTests(unittest.TestCase):
    def arguments(self, action):
        parser = argparse.ArgumentParser()
        add_privilege_parser(parser.add_subparsers(dest="command"))
        return parser.parse_args(["privilege", action, "a" * 32, "--json"])

    @patch("lib.privilege_client.exchange")
    def test_cancel_dispatch_and_already_closed_exit_status(self, exchange):
        for state in ("cancelled", "expired", "denied", "invalidated"):
            exchange.return_value = {"id": "a" * 32, "state": state}
            with self.subTest(state=state), redirect_stdout(output := io.StringIO()):
                self.assertEqual(run_privilege_command(self.arguments("cancel")), 0)
            self.assertEqual(json.loads(output.getvalue())["state"], state)
            exchange.assert_called_with({"action": "cancel", "id": "a" * 32})

    @patch("lib.privilege_client.exchange")
    @patch("lib.privilege_client.time.sleep")
    def test_wait_stops_on_cancellation_with_an_unsuccessful_result(self, sleep, exchange):
        exchange.return_value = {"id": "a" * 32, "state": "cancelled"}
        with redirect_stdout(io.StringIO()):
            self.assertEqual(run_privilege_command(self.arguments("wait")), 1)
        sleep.assert_not_called()
        exchange.assert_called_once_with({"action": "status", "id": "a" * 32})


class TransportTests(unittest.TestCase):
    def invoke(self, uid, payload, *, approval=False):
        handler = object.__new__(Handler)
        handler.request = Mock()
        handler.request.getsockopt.return_value = struct.pack("3i", 123, uid, 1000)
        handler.server = Mock(allowed_uid=1000, approval=approval)
        handler.server.broker.dispatch.return_value = {"state": "pending"}
        handler.rfile = io.BytesIO(payload)
        handler.wfile = io.BytesIO()
        handler.handle()
        return json.loads(handler.wfile.getvalue()), handler.server.broker.dispatch

    def test_kernel_uid_controls_access_before_dispatch(self):
        response, dispatch = self.invoke(1001, b'{"action":"status"}\n')
        self.assertFalse(response["ok"])
        dispatch.assert_not_called()

    def test_message_cannot_select_approval_interface(self):
        message = {"action": "request", "operation": "system.reboot", "parameters": {},
                   "reason": "test", "approval": True, "uid": 0}
        response, dispatch = self.invoke(1000, (canonical(message) + "\n").encode())
        self.assertTrue(response["ok"])
        dispatch.assert_called_once_with(message, 1000, approval=False)
        # The strict dispatcher independently rejects these extra fields.
        with tempfile.TemporaryDirectory() as directory:
            broker = Broker(directory + "/state.db", policy, Mock())
            try:
                with self.assertRaises(PermissionError):
                    broker.dispatch(message, 1000)
            finally:
                broker.db.close()

    def test_oversize_unterminated_and_malformed_frames_never_dispatch(self):
        for payload in (b"x" * (MAX_MESSAGE + 1), b"{}", b"{bad}\n"):
            with self.subTest(size=len(payload)):
                response, dispatch = self.invoke(1000, payload)
                self.assertFalse(response["ok"])
                dispatch.assert_not_called()
