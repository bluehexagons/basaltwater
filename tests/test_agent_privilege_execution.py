"""Approved commands leave broker confinement without losing argv or one-shot claims."""

from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from common.service_tools import privilege_broker, privilege_command
from lib.privilege_execution import COMMAND_HELPER, ENVIRONMENT, ExecutionResult
from lib.privilege_policy import MAX_MESSAGE, canonical
from tests.test_agent_privilege_broker import policy


class CommandExecutionTests(unittest.TestCase):
    def test_approved_directory_command_dispatches_to_the_supervised_helper(self):
        argv = ["/usr/bin/install", "-d", "-m", "0700", "-o", "agent", "/srv/archive"]
        with tempfile.TemporaryDirectory() as root:
            broker = privilege_broker.Broker(root + "/state.db", policy)
            self.addCleanup(broker.db.close)
            request = broker.request(1000, "command.run", {"argv": argv}, "Create private archive")
            with patch.object(privilege_broker, "protected_path") as protect, \
                    patch.object(privilege_broker.subprocess, "run", return_value=Mock(returncode=0)) as run:
                self.assertFalse(broker.execute_next())
                run.assert_not_called()
                broker.decide(request["id"], request["digest"], True, "operator")
                self.assertTrue(broker.execute_next())
                self.assertFalse(broker.execute_next())
                run.assert_called_once()
                protect.assert_called_once_with(COMMAND_HELPER)
                dispatch = run.call_args.args[0]
                self.assertEqual(dispatch[0], "/usr/bin/systemd-run")
                self.assertIn("--wait", dispatch)
                self.assertIn("--collect", dispatch)
                self.assertIn("--property=KillMode=control-group", dispatch)
                self.assertIn("--property=Restart=no", dispatch)
                self.assertEqual(dispatch[-4:], ["--", "/usr/bin/python3", "-I", COMMAND_HELPER])
                self.assertNotIn("/srv/archive", dispatch)
                self.assertEqual(json.loads(run.call_args.kwargs["input"]), argv)
            self.assertEqual(broker.status(request["id"])["execution"]["exit_code"], 0)

    @patch.object(privilege_command.subprocess, "run", return_value=Mock(returncode=0))
    def test_helper_preserves_literal_arguments_and_fixed_environment(self, run):
        argv = ["/usr/bin/printf", "$HOME", "%n", "$(id)", "space here", "π"]
        self.assertEqual(privilege_command.execute_command(canonical(argv).encode()), 0)
        self.assertEqual(run.call_args.args[0], argv)
        self.assertEqual(run.call_args.kwargs["env"], ENVIRONMENT)
        for stream in ("stdin", "stdout", "stderr"):
            self.assertEqual(run.call_args.kwargs[stream], subprocess.DEVNULL)
        self.assertNotIn("shell", run.call_args.kwargs)

    @patch.object(privilege_command.subprocess, "run")
    def test_helper_rejects_malformed_or_oversize_payloads_before_effects(self, run):
        for data in (b"x" * (MAX_MESSAGE + 1), b"{", b"{}", b"[]", b'["sudo","id"]', b'["true","a\\n"]'):
            self.assertEqual(privilege_command.execute_command(data), 125)
        run.assert_not_called()

    @patch.object(privilege_command.subprocess, "run")
    def test_helper_maps_command_failure_signal_timeout_and_missing_program(self, run):
        for outcome, expected in ((Mock(returncode=7), 7), (Mock(returncode=-9), 137),
                                  (subprocess.TimeoutExpired("cmd", 60), 124), (FileNotFoundError(), 125)):
            run.side_effect = outcome if isinstance(outcome, Exception) else None
            run.return_value = outcome
            self.assertEqual(privilege_command.execute_command(b'["true"]'), expected)

    @patch.object(privilege_command.os, "geteuid", return_value=1000)
    @patch.object(privilege_command, "execute_command")
    def test_unprivileged_helper_never_reads_or_executes_input(self, execute, _uid):
        with patch.object(privilege_command.sys, "stdin", Mock(buffer=io.BytesIO(b'["true"]'))):
            self.assertEqual(privilege_command.main(), 125)
        execute.assert_not_called()

    def test_broker_failures_report_metadata_without_privileged_output(self):
        plan = {"operation": "command.run", "argv": ["true"]}
        with patch.object(privilege_broker, "protected_path", side_effect=ValueError("unsafe helper")), \
                patch.object(privilege_broker.subprocess, "run") as run:
            self.assertEqual(privilege_broker.execute(plan).failure, "executor_unavailable")
            run.assert_not_called()
        with patch.object(privilege_broker, "protected_path"), \
                patch.object(privilege_broker.subprocess, "run", return_value=Mock(returncode=7, stderr="secret")) as run:
            self.assertEqual(privilege_broker.execute(plan), ExecutionResult("failed", 7, "nonzero_exit"))
            run.side_effect = subprocess.TimeoutExpired("cmd", 80)
            self.assertEqual(privilege_broker.execute(plan).state, "uncertain")
            run.side_effect = None
            run.return_value.returncode = -9
            self.assertEqual(privilege_broker.execute(plan), ExecutionResult("uncertain", -9, "executor_signal"))
