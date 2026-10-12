"""Old/new maintenance readers expose fixed diagnostics without privileged data."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from io import StringIO
import json
import unittest
from unittest.mock import patch

from lib.admin_status import sanitize_admin_status, setup_details
from lib.privilege_client import add_privilege_parser, run_privilege_command


class AdminStatusTests(unittest.TestCase):
    def test_old_results_keep_unknown_diagnostics_and_drop_private_fields(self):
        result = sanitize_admin_status({"unit_state": "inactive", "latest": {
            "action": "refresh", "status": "failed", "started": 123, "exit_code": 1,
            "output": "root-secret", "exception": "root-secret", "config": {"secret": "root-secret"},
        }, "requests": [{"id": "a" * 32, "action": "refresh", "state": "dispatched", "plan": "root-secret"}]})
        self.assertEqual(result["latest"]["exit_code"], 1)
        for key in ("stage", "command_index", "failure", "setup_context", "job_id"):
            self.assertIsNone(result["latest"][key])
        self.assertEqual(result["requests"][0]["id"], "a" * 32)
        self.assertNotIn("root-secret", json.dumps(result))

    def test_invalid_or_unrelated_context_is_unknown(self):
        context = {"job_id": "a" * 32, "phase": "recovery", "step_index": 2, "step_count": 3,
                   "error_type": "root-secret", "step": "/private/config"}
        result = setup_details(context, "a" * 32)
        self.assertIsNone(result["error_type"])
        self.assertNotIn("private", json.dumps(result))
        for override in ({"job_id": "b" * 32}, {"phase": []}, {"step_index": True}, {"step_count": 1}):
            self.assertIsNone(setup_details({**context, **override}, "a" * 32))

    def test_cli_status_only_reads_and_produces_structured_unavailability(self):
        parser = argparse.ArgumentParser()
        add_privilege_parser(parser.add_subparsers(dest="command"))
        args = parser.parse_args(["privilege", "admin-status", "--json"])
        with patch("lib.privilege_client.exchange", return_value={"unit_state": "inactive", "latest": {
            "action": "refresh", "status": "failed", "stage": "command", "command_index": 1,
            "failure": "nonzero_exit", "output": "root-secret",
        }}) as exchange, redirect_stdout(output := StringIO()):
            self.assertEqual(run_privilege_command(args), 1)
        exchange.assert_called_once_with({"action": "admin-status"})
        self.assertEqual(json.loads(output.getvalue())["latest"]["failure"], "nonzero_exit")
        self.assertNotIn("root-secret", output.getvalue())
        with patch("lib.privilege_client.exchange", side_effect=OSError("private socket path")), \
                redirect_stdout(output := StringIO()):
            self.assertEqual(run_privilege_command(args), 1)
        self.assertEqual(json.loads(output.getvalue())["issue"], "maintenance_status_unavailable")
        self.assertNotIn("private socket", output.getvalue())

    def test_malformed_optional_values_never_escape_as_text_or_zero_resources(self):
        value = {"unit_state": [], "latest": {"action": "refresh", "status": "running",
                 "stage": [], "failure": {}, "command_index": True, "started": 10**1000},
                 "requests": [{"id": "a" * 32, "action": "refresh", "state": []}],
                 "blocked": {"refresh": "root-secret"}}
        result = sanitize_admin_status(value)
        self.assertEqual(result["unit_state"], "unavailable")
        self.assertIsNone(result["latest"]["started"])
        self.assertEqual(result["blocked_actions"], ["refresh"])
        self.assertNotIn("root-secret", json.dumps(result))
