"""Smoke workflow contract tests; never launch a real desktop application."""

from __future__ import annotations

import argparse
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from desktop import smoke
from lib.desktop_cli import add_desktop_subparser, run_desktop_command


class SmokeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "smoke"
        self.directory.mkdir()
        self.enterContext(patch.object(smoke.runtime, "status", return_value={"control_revision": "r"}))
        self.calls = []
        self.save = True
        self.window = {"id": "0x42", "pid": 42, "identity": "fresh"}
        for target, kwargs in (
            ("shutil.which", {"return_value": "/usr/bin/geany"}),
            ("tempfile.mkdtemp", {"return_value": str(self.directory)}),
            ("runtime.request", {"side_effect": self.handle_request}),
            ("client.wait_for_window", {"return_value": {"windows": [self.window]}}),
            ("client.wait_for_element", {"side_effect": self.observe}),
        ):
            patcher = patch("desktop.smoke." + target, **kwargs)
            setattr(self, target.rsplit(".", 1)[-1], patcher.start())
            self.addCleanup(patcher.stop)

    def handle_request(self, payload):
        self.calls.append(payload)
        if payload["action"] == "exec":
            return {"pid": 42, "launch": "launch"}
        if payload.get("ref") == "Save" and self.save:
            (self.directory / "check.txt").write_bytes((self.directory / "expected.txt").read_bytes())
        return {}

    def observe(self, generation, **selectors):
        return {"elements": [{"ref": selectors.get("name", "text"), "actions": ["click"]}]}

    def test_success_verifies_bytes_scopes_dialog_and_closes_only_launched_window(self):
        result = smoke.run_smoke_check("g")
        self.assertTrue(result["passed"])
        self.assertEqual(result["completed"], ["launch", "edit", "save", "dialog", "close"])
        self.assertFalse(self.directory.exists())
        launch = next(p for p in self.calls if p["action"] == "exec")
        self.assertIn("--new-instance", launch["argv"])
        self.assertIn(str(self.directory / "profile"), launch["argv"])
        scoped = [c.kwargs for c in self.wait_for_element.call_args_list if "root" in c.kwargs]
        self.assertEqual(len(scoped), 3)
        self.assertTrue(all(c["root"] == "Find" for c in scoped))
        close = next(p for p in self.calls if p["action"] == "window")
        self.assertEqual((close["window"], close["identity"]), ("0x42", "fresh"))
        self.wait_for_window.assert_called_with("g", pid=42, condition="absent")
        self.assertTrue(all(p["generation"] == "g" for p in self.calls))
        self.assertTrue(all(p["control_revision"] == "r" for p in self.calls))
        self.assertFalse(any(p["action"] in ("control", "acquire", "release", "pause", "resume") for p in self.calls))

    def test_failed_save_preserves_files_and_never_claims_success_or_closes(self):
        self.save = False
        with patch.object(smoke.time, "monotonic", side_effect=[0, 16]):
            result = smoke.run_smoke_check("g")
        self.assertFalse(result["passed"])
        self.assertEqual(result["stage"], "save")
        self.assertTrue((self.directory / "expected.txt").exists())
        self.assertFalse(any(p["action"] == "window" for p in self.calls))

    def test_ambiguous_launch_is_not_used(self):
        self.wait_for_window.return_value = {"windows": [self.window, self.window]}
        result = smoke.run_smoke_check("g")
        self.assertEqual(result["stage"], "launch")
        self.wait_for_element.assert_not_called()

    def test_wait_timeout_does_not_use_partial_element(self):
        self.wait_for_element.side_effect = None
        self.wait_for_element.return_value = {"error": "timeout", "elements": [{"ref": "wrong"}]}
        result = smoke.run_smoke_check("g")
        self.assertEqual(result["stage"], "edit")
        self.assertFalse(any(p["action"] == "element" for p in self.calls))

    def test_mutation_failure_is_not_retried_or_followed_by_more_mutations(self):
        def fail(payload):
            if payload["action"] == "element":
                raise RuntimeError("human paused")
            return self.handle_request(payload)
        self.request_mock = smoke.runtime.request
        self.request_mock.side_effect = fail
        result = smoke.run_smoke_check("g")
        self.assertEqual(result["error"], "human paused")
        actions = [c.args[0]["action"] for c in self.request_mock.call_args_list]
        self.assertEqual(actions.count("element"), 1)
        self.assertEqual(actions, ["exec", "element"])

    def test_missing_geany_does_not_create_files_or_launch(self):
        self.which.return_value = None
        with self.assertRaisesRegex(RuntimeError, "Geany is required"):
            smoke.run_smoke_check("g")
        self.mkdtemp.assert_not_called()
        smoke.runtime.request.assert_not_called()

    @patch("lib.desktop_cli.runtime.status", return_value={"state": "stopped"})
    @patch.object(smoke, "run_smoke_check")
    def test_cli_requires_running_desktop(self, run, status):
        parser = argparse.ArgumentParser()
        add_desktop_subparser(parser.add_subparsers())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_desktop_command(parser.parse_args(["desktop", "smoke"])), 1)
        run.assert_not_called()
