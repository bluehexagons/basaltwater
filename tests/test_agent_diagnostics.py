"""CLI contracts for fixed, read-only service diagnostics."""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from common.service_tools import web_panel_diagnostics as collector
from lib.agent_cli import add_agent_subparser, run_agent_command
from lib.agent_diagnostics import run_diagnostics_command


class AgentDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parser = argparse.ArgumentParser()
        add_agent_subparser(cls.parser.add_subparsers(dest="command"))

    def invoke(self, *options: str) -> tuple[int, dict]:
        args = self.parser.parse_args(["agent", "diagnostics", *options])
        output = io.StringIO()
        with redirect_stdout(output):
            code = run_agent_command(args)
        return code, json.loads(output.getvalue())

    @patch("lib.agent_diagnostics.collect_diagnostics")
    def test_dispatch_defaults_and_structured_evidence(self, collect):
        evidence = {
            "properties": {"ActiveState": "failed"},
            "events": [{"message": "Application failed", "priority": "3", "timestamp": "Unknown time"}],
            "issues": [],
        }
        collect.return_value = {**evidence, "raw_output": "must not be emitted"}
        code, result = self.invoke("--service", "nginx.service")
        self.assertEqual(code, 0)  # Collection success is independent of service state.
        self.assertEqual(result, evidence)
        collect.assert_called_once_with(collector.DiagnosticQuery(
            service="nginx.service", window="24h", priority="4", load=True,
        ))

    @patch("lib.agent_diagnostics.collect_diagnostics")
    def test_each_selector_and_combined_overrides_propagate(self, collect):
        collect.return_value = {"properties": {}, "events": [], "issues": []}
        # The options are independent. Sweep each domain once and retain a
        # combined override instead of multiplying every catalog size.
        cases = [
            *((service, "24h", "4") for service in collector.SOURCES),
            *(("nginx.service", window, "4") for window in collector.WINDOWS),
            *(("nginx.service", "24h", priority) for priority in collector.PRIORITIES),
            ("nginx.service", "1h", "3"),
        ]
        for service, window, priority in dict.fromkeys(cases):
            with self.subTest(service=service, window=window, priority=priority):
                code, result = self.invoke(
                    "--service", service, "--window", window, "--priority", priority,
                )
                self.assertEqual(code, 0)
                self.assertEqual(result["events"], [])
                self.assertEqual(collect.call_args.args[0], collector.DiagnosticQuery(
                    service=service, window=window, priority=priority, load=True,
                ))

    @patch("lib.agent_diagnostics.collect_diagnostics")
    def test_rejects_missing_service_arbitrary_units_paths_and_filters(self, collect):
        for options in (
            [], ["--service", "arbitrary.service"], ["--service", "/etc/passwd"],
            ["--service", "nginx.service", "--window", "999h"],
            ["--service", "nginx.service", "--priority", "0"],
            ["--service", "nginx.service", "--output", "/tmp/evidence"],
        ):
            with self.subTest(options=options), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    self.parser.parse_args(["agent", "diagnostics", *options])
                self.assertEqual(error.exception.code, 2)
        collect.assert_not_called()

    @patch("lib.agent_diagnostics.collect_diagnostics")
    def test_dispatch_revalidates_selectors_before_collection(self, collect):
        output = io.StringIO()
        with redirect_stdout(output):
            code = run_diagnostics_command(argparse.Namespace(
                service="arbitrary.service", window="24h", priority="4",
            ))
        self.assertEqual(code, 1)
        self.assertTrue(json.loads(output.getvalue())["issues"])
        collect.assert_not_called()

    @patch("lib.agent_diagnostics.collect_diagnostics")
    def test_collection_issues_return_one_and_preserve_partial_evidence(self, collect):
        evidence = {"properties": {"LoadState": "loaded"}, "events": [], "issues": ["Query unavailable."]}
        collect.return_value = evidence
        self.assertEqual(self.invoke("--service", "nginx.service"), (1, evidence))

    @patch("lib.agent_diagnostics.collect_diagnostics")
    def test_exceptions_never_emit_raw_output(self, collect):
        secret = "raw-secret-output"
        for error in (
            OSError(secret), RuntimeError(secret), ValueError(secret),
            subprocess.CalledProcessError(1, [secret], output=secret, stderr=secret),
            subprocess.TimeoutExpired([secret], 5, output=secret, stderr=secret),
        ):
            with self.subTest(error=type(error).__name__):
                collect.side_effect = error
                code, result = self.invoke("--service", "nginx.service")
                self.assertEqual(code, 1)
                self.assertTrue(result["issues"])
                self.assertEqual(result["events"], [])
                self.assertNotIn(secret, json.dumps(result))

    @patch("common.service_tools.web_panel_diagnostics._bounded_command")
    def test_shared_collector_redaction_and_property_allowlist_reach_json(self, bounded):
        bounded.side_effect = [
            ("LoadState=loaded\nEnvironment=password=raw-secret\n", ""),
            (json.dumps({"MESSAGE": "password=raw-secret", "PRIORITY": "3"}), ""),
        ]
        code, result = self.invoke("--service", "nginx.service")
        self.assertEqual(code, 0)
        self.assertEqual(result["properties"], {"LoadState": "loaded"})
        self.assertEqual(len(result["events"]), 1)
        self.assertNotIn("raw-secret", json.dumps(result))
        self.assertEqual(bounded.call_count, 2)
        command = bounded.call_args.args[0]
        self.assertIn("--unit=nginx.service", command)
        self.assertNotIn("--system", command)
        self.assertNotIn("--user", command)


if __name__ == "__main__":
    unittest.main()
