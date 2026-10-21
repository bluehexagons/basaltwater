"""CLI contracts for fixed, read-only service diagnostics."""

from __future__ import annotations

import argparse
import io
import json
import os
import stat
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
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
            ["--service", "nginx.service", "--output"],
            ["--service", "nginx.service", "--summary=yes"],
        ):
            with self.subTest(options=options), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    self.parser.parse_args(["agent", "diagnostics", *options])
                self.assertEqual(error.exception.code, 2)
        collect.assert_not_called()

    @patch("lib.agent_diagnostics.collect_diagnostics")
    def test_summary_requires_output_before_collection(self, collect):
        code, result = self.invoke("--service", "nginx.service", "--summary")
        self.assertEqual(code, 2)
        self.assertIn("--summary requires --output", result["issues"][0])
        collect.assert_not_called()

    @patch("lib.agent_diagnostics.collect_diagnostics")
    def test_summary_preserves_properties_all_issues_selectors_and_counts(self, collect):
        evidence = {
            "properties": {"ActiveState": "failed", "ExecMainStatus": "1"},
            "events": [
                {"message": "Event omitted from stdout", "timestamp": "Unknown time", "priority": priority}
                for priority in ("3", "3", "4", "", "unexpected")
            ],
            "issues": ["Property query timed out.", "Journal access restricted.", "Entry limit reached."],
        }
        collect.return_value = {**evidence, "raw_output": "must not be saved"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "evidence.json")
            code, result = self.invoke(
                "--service", "t3code.service", "--window", "boot", "--priority", "7",
                "--summary", "--output", str(path),
            )
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(path.read_text()), evidence)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(list(Path(directory).iterdir()), [path])
        self.assertEqual(result["properties"], evidence["properties"])
        self.assertEqual(result["issues"], evidence["issues"])
        self.assertEqual(result["query"], {"service": "t3code.service", "window": "boot", "priority": "7"})
        self.assertEqual(result["event_count"], 5)
        self.assertEqual(result["priority_counts"], {"3": 2, "4": 1, "unknown": 2})
        self.assertEqual(result["evidence_path"], str(path))
        self.assertTrue(result["events_omitted_from_stdout"])
        self.assertNotIn("events", result)
        self.assertNotIn("Event omitted from stdout", json.dumps(result))
        for qualification in ("100", "five seconds", "64 KiB", "permissions", "retention", "do not prove service health"):
            self.assertIn(qualification, result["qualification"])

    @patch("lib.agent_diagnostics.collect_diagnostics")
    def test_output_alone_keeps_full_stdout_and_failed_service_is_not_collection_failure(self, collect):
        evidence = {"properties": {"ActiveState": "failed"}, "events": [], "issues": []}
        collect.return_value = evidence
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "evidence.json")
            self.assertEqual(self.invoke("--service", "nginx.service", "--output", str(path)), (0, evidence))
            self.assertEqual(json.loads(path.read_text()), evidence)
            summary_path = Path(directory, "summary-evidence.json")
            code, result = self.invoke("--service", "nginx.service", "--output", str(summary_path), "--summary")
            self.assertEqual(code, 0)
            self.assertEqual(result["event_count"], 0)
            self.assertEqual(result["priority_counts"], {})
            self.assertEqual(result["issues"], [])

    @patch("lib.agent_diagnostics.collect_diagnostics")
    def test_relative_output_is_private_and_readable_under_restrictive_umask(self, collect):
        collect.return_value = {"properties": {}, "events": [], "issues": []}
        with tempfile.TemporaryDirectory() as directory:
            previous_umask = os.umask(0o777)
            try:
                with patch("lib.agent_diagnostics.os.getcwd", return_value=directory):
                    code, result = self.invoke(
                        "--service", "nginx.service", "--summary", "--output", "./evidence.json",
                    )
            finally:
                os.umask(previous_umask)
            path = Path(directory, "evidence.json")
            self.assertEqual(code, 0)
            self.assertEqual(result["evidence_path"], str(path))
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(json.loads(path.read_text()), collect.return_value)

    @patch("lib.agent_diagnostics.collect_diagnostics")
    def test_output_rejects_unsafe_paths_and_never_overwrites_existing_evidence(self, collect):
        evidence = {"properties": {}, "events": [{"message": "Keep evidence visible", "priority": "3"}], "issues": ["Collection notice."]}
        collect.return_value = evidence
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            existing = root / "existing.json"
            existing.write_text("unrelated evidence")
            (root / "link.json").symlink_to(existing)
            (root / "broken.json").symlink_to(root / "absent.json")
            (root / "link-directory").symlink_to(root, target_is_directory=True)
            os.mkfifo(root / "fifo")
            for path in (
                "", str(root / "control\nsecret"), str(existing), str(root / "link.json"),
                str(root / "broken.json"), str(root / "link-directory" / "new.json"),
                str(root / "link-directory" / ".." / "new.json"),
                str(root / "missing" / "new.json"), str(root), str(root / "fifo"),
            ):
                with self.subTest(path=path):
                    code, result = self.invoke("--service", "nginx.service", "--summary", "--output", path)
                    self.assertEqual(code, 1)
                    self.assertEqual(result["events"], evidence["events"])
                    self.assertEqual(result["properties"], evidence["properties"])
                    self.assertEqual(result["issues"][:-1], evidence["issues"])
                    self.assertIn("could not be saved", result["issues"][-1])
                    self.assertNotIn("evidence_path", result)
                    self.assertNotIn("control", json.dumps(result))
            self.assertEqual(existing.read_text(), "unrelated evidence")
            self.assertTrue((root / "link.json").is_symlink())
            self.assertFalse((root / "absent.json").exists())
            self.assertFalse((root / "new.json").exists())
            self.assertFalse(list(root.glob(".diagnostics-*")))

    @patch("lib.agent_diagnostics.collect_diagnostics")
    def test_save_errors_retain_full_evidence_without_raw_exception_text(self, collect):
        evidence = {"properties": {}, "events": [{"message": "Evidence", "priority": "4"}], "issues": []}
        collect.return_value = evidence
        for operation in ("os.open", "os.fchmod", "os.fsync", "os.link"):
            for summary in (False, True):
                with self.subTest(operation=operation, summary=summary), tempfile.TemporaryDirectory() as directory:
                    path = Path(directory, "evidence.json")
                    options = ["--service", "nginx.service", "--output", str(path)]
                    with patch("lib.agent_diagnostics." + operation, side_effect=PermissionError("credential=raw-secret")):
                        code, result = self.invoke(*options, *(["--summary"] if summary else []))
                    self.assertEqual(code, 1)
                    self.assertEqual(result["events"], evidence["events"])
                    self.assertNotIn("raw-secret", json.dumps(result))
                    self.assertEqual(list(Path(directory).iterdir()), [])

    @patch("lib.agent_diagnostics.collect_diagnostics")
    def test_late_save_failure_keeps_complete_file_and_full_stdout(self, collect):
        evidence = {"properties": {}, "events": [{"message": "Evidence", "priority": "3"}], "issues": []}
        collect.return_value = evidence
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "evidence.json")
            with patch("lib.agent_diagnostics.os.fsync", side_effect=[None, OSError("credential=raw-secret")]):
                code, result = self.invoke("--service", "nginx.service", "--summary", "--output", str(path))
            self.assertEqual(code, 1)
            self.assertEqual(result["events"], evidence["events"])
            self.assertNotIn("raw-secret", json.dumps(result))
            self.assertEqual(json.loads(path.read_text()), evidence)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    @patch("lib.agent_diagnostics.collect_diagnostics")
    def test_concurrent_destination_creation_cannot_replace_unrelated_evidence(self, collect):
        collect.return_value = {"properties": {}, "events": [], "issues": []}
        link = os.link
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "evidence.json")

            def create_before_link(*args, **kwargs):
                path.write_text("concurrent evidence")
                return link(*args, **kwargs)

            with patch("lib.agent_diagnostics.os.link", side_effect=create_before_link):
                code, result = self.invoke("--service", "nginx.service", "--summary", "--output", str(path))
            self.assertEqual(code, 1)
            self.assertIn("events", result)
            self.assertEqual(path.read_text(), "concurrent evidence")
            self.assertEqual(list(Path(directory).iterdir()), [path])

    @patch("lib.agent_diagnostics.collect_diagnostics")
    def test_writable_or_unowned_directory_keeps_full_stdout_evidence(self, collect):
        evidence = {"properties": {}, "events": [{"message": "Retain this", "priority": "3"}], "issues": []}
        collect.return_value = evidence
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for mode in (0o770, 0o707, 0o1777):
                with self.subTest(mode=oct(mode)):
                    root.chmod(mode)
                    code, result = self.invoke("--service", "nginx.service", "--summary", "--output", str(root / "new.json"))
                    self.assertEqual(code, 1)
                    self.assertEqual(result["events"], evidence["events"])
                    self.assertEqual(list(root.iterdir()), [])
            root.chmod(0o700)
            with patch("lib.agent_diagnostics.os.geteuid", return_value=root.stat().st_uid + 1):
                code, result = self.invoke("--service", "nginx.service", "--summary", "--output", str(root / "new.json"))
            self.assertEqual(code, 1)
            self.assertEqual(result["events"], evidence["events"])
            self.assertEqual(list(root.iterdir()), [])

    @patch("lib.agent_diagnostics.collect_diagnostics")
    def test_shared_ancestor_cannot_make_reported_artifact_path_disappear(self, collect):
        evidence = {"properties": {}, "events": [{"message": "Retain this", "priority": "3"}], "issues": []}
        collect.return_value = evidence
        link = os.link
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shared = root / "shared"
            private = shared / "private"
            private.mkdir(parents=True, mode=0o700)

            def rename_before_link(*args, **kwargs):
                private.rename(shared / "renamed")
                return link(*args, **kwargs)

            for mode in (0o777, 0o770):
                shared.chmod(mode)
                with self.subTest(mode=oct(mode)), patch("lib.agent_diagnostics.os.link", side_effect=rename_before_link) as publish:
                    code, result = self.invoke("--service", "nginx.service", "--summary", "--output", str(private / "new.json"))
                self.assertEqual(code, 1)
                self.assertEqual(result["events"], evidence["events"])
                self.assertNotIn("evidence_path", result)
                publish.assert_not_called()
            shared.chmod(0o1777)
            code, result = self.invoke("--service", "nginx.service", "--summary", "--output", str(private / "new.json"))
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(Path(result["evidence_path"]).read_text()), evidence)

    @patch("lib.agent_diagnostics.collect_diagnostics")
    def test_temporary_path_substitution_never_hides_collected_evidence(self, collect):
        evidence = {"properties": {}, "events": [{"message": "Retain this", "priority": "3"}], "issues": []}
        collect.return_value = evidence
        link = os.link
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unrelated = root / "unrelated.json"
            unrelated.write_text("unrelated evidence")

            for kind in ("symlink", "regular"):
                def substitute_before_link(source, destination, **kwargs):
                    os.unlink(source, dir_fd=kwargs["src_dir_fd"])
                    if kind == "symlink":
                        os.symlink(str(unrelated), source, dir_fd=kwargs["src_dir_fd"])
                    else:
                        link(str(unrelated), source, dst_dir_fd=kwargs["src_dir_fd"], follow_symlinks=False)
                    return link(source, destination, **kwargs)

                with self.subTest(kind=kind), patch("lib.agent_diagnostics.os.link", side_effect=substitute_before_link):
                    code, result = self.invoke("--service", "nginx.service", "--summary", "--output", str(root / f"{kind}.json"))
                self.assertEqual(code, 1)
                self.assertEqual(result["events"], evidence["events"])
                self.assertNotIn("events_omitted_from_stdout", result)
                self.assertNotIn("evidence_path", result)
                self.assertEqual(unrelated.read_text(), "unrelated evidence")
                self.assertFalse(list(root.glob(".diagnostics-*")))

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

    @patch("common.service_tools.web_panel_diagnostics._bounded_command")
    def test_full_artifact_uses_collector_redaction_and_keeps_entry_limit_issue(self, bounded):
        bounded.side_effect = [
            ("LoadState=loaded\nEnvironment=password=raw-secret\n", ""),
            ("\n".join(json.dumps({"MESSAGE": "password=raw-secret", "PRIORITY": "3"}) for _ in range(100)), ""),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "evidence.json")
            code, result = self.invoke("--service", "nginx.service", "--summary", "--output", str(path))
            content = path.read_text()
            evidence = json.loads(content)
        self.assertEqual(code, 1)
        self.assertNotIn("raw-secret", content)
        self.assertEqual(evidence["properties"], {"LoadState": "loaded"})
        self.assertEqual(len(evidence["events"]), 100)
        self.assertIn("[redacted]", evidence["events"][0]["message"])
        self.assertEqual(result["event_count"], 100)
        self.assertEqual(result["priority_counts"], {"3": 100})
        self.assertEqual(result["issues"], evidence["issues"])
        self.assertIn("Entry limit reached", result["issues"][0])


if __name__ == "__main__":
    unittest.main()
