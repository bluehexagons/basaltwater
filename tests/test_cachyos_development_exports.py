"""Optional export diagnosis and task waiting without native execution."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from desktop import development, development_exports as exports
from lib.streamed_process import CommandOutputLimitError


class TestDevelopmentExports(unittest.TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(Path, "home", return_value=self.home))
        self.enterContext(patch.dict(os.environ, {"XDG_DATA_HOME": str(self.home / "data")}))
        self.info = {"kind": "godot", "engine": str(self.home / "godot"), "missing_tools": []}
        self.root = self.home / "data/godot/export_templates"
        self.probe = self.enterContext(patch.object(exports, "run_captured", return_value=
            subprocess.CompletedProcess([], 0, "4.7.1.stable.official.abcdef123\n", "")))

    def template(self, version, name="linux_debug.x86_64"):
        directory = self.root / version
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_text("fixture")
        return directory

    def test_exact_version_inventory_and_mismatch_do_not_require_exports(self):
        self.template("4.7.1.stable")
        self.template("4.7.stable")
        result = exports.inspect_exports(self.info)
        self.assertTrue(result["ok"])
        self.assertEqual(self.info["engine_version"], "4.7.1.stable.official.abcdef123")
        self.assertEqual(result["expected_template_version"], "4.7.1.stable")
        self.assertEqual(result["installed"][0]["files"], ["linux_debug.x86_64"])
        self.assertEqual(result["mismatching_templates"], ["4.7.stable"])
        self.assertEqual(result["export_readiness"], "unverified")
        self.probe.assert_called_once_with([self.info["engine"], "--version"],
            timeout=3, max_bytes=4096, cwd="/", env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})

    def test_missing_empty_metadata_only_and_other_versions_are_not_matching(self):
        for fixture in ("missing", "other", "metadata"):
            with self.subTest(fixture=fixture):
                if fixture == "other":
                    self.template("4.7.stable")
                elif fixture == "metadata":
                    directory = self.template("4.7.1.stable", "version.txt")
                    (directory / "empty").touch()
                    (directory / "linked").symlink_to(self.home)
                result = exports.inspect_exports(self.info)
                self.assertFalse(result["ok"])
                self.assertEqual(result["state"], "missing")
                self.assertEqual(result["missing_templates"], ["4.7.1.stable"])

    def test_mono_double_and_self_contained_paths(self):
        (self.home / "_sc_").touch()
        self.probe.return_value.stdout = "4.7.rc1.mono.double.custom_build.abcdef123"
        root = self.home / "editor_data/export_templates"
        directory = root / "4.7.rc1.mono.double"
        directory.mkdir(parents=True)
        (directory / "linux_debug.x86_64").write_text("fixture")
        result = exports.inspect_exports(self.info)
        self.assertTrue(result["ok"])
        self.assertEqual(result["template_root"], str(root))

    def test_inventory_is_bounded_and_never_follows_child_links(self):
        self.template("4.7.1.stable")
        (self.root / "4.8.stable").symlink_to(self.home, target_is_directory=True)
        with patch.object(exports, "ENTRY_LIMIT", 1):
            result = exports.inspect_exports(self.info)
        self.assertFalse(result["ok"])
        self.assertTrue(result["truncated"])
        result = exports.inspect_exports(self.info)
        self.assertEqual(len(result["installed"]), 1)

    def test_bad_probe_results_never_become_export_readiness(self):
        for code, text in ((1, "4.7.stable.official.abc"), (0, "4.7\nextra"),
                           (0, "../escape"), (0, "3.6.stable.official.abc")):
            with self.subTest(code=code, text=text):
                self.probe.return_value = subprocess.CompletedProcess([], code, text, "")
                self.assertFalse(exports.inspect_exports(self.info)["ok"])
        self.info["kind"] = "native"
        self.probe.reset_mock()
        self.assertFalse(exports.inspect_exports(self.info)["ok"])
        self.probe.assert_not_called()

    def test_doctor_opt_in_only_and_propagates_missing_templates(self):
        with patch.object(development, "_guard", return_value=1234), \
                patch.object(development, "_project", return_value=self.home), \
                patch.object(development, "project_info", return_value=self.info), \
                patch.object(development, "session_environment", return_value=({"owned": "session"}, "available")):
            self.assertTrue(development.doctor(str(self.home))["ok"])
            self.probe.assert_not_called()
            result = development.doctor(str(self.home), export_check=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["project"]["export_check"]["state"], "missing")
        self.assertFalse((self.home / ".local").exists())

    def test_probe_timeout_output_limit_and_missing_binary_stay_unverified(self):
        for error in (subprocess.TimeoutExpired("godot", 3),
                      CommandOutputLimitError("too much output"), FileNotFoundError("godot")):
            with self.subTest(error=error):
                self.probe.side_effect = error
                result = exports.inspect_exports(self.info)
                self.assertFalse(result["ok"])
                self.assertEqual(result["state"], "unverified")
                self.assertIsNone(result["engine_version"])
                self.assertFalse(self.root.exists())

    def test_relative_xdg_path_is_rejected_and_empty_xdg_uses_home(self):
        with patch.dict(os.environ, {"XDG_DATA_HOME": "relative"}), self.assertRaises(ValueError):
            exports.inspect_exports(self.info)
        with patch.dict(os.environ, {"XDG_DATA_HOME": ""}):
            result = exports.inspect_exports(self.info)
        self.assertEqual(result["template_root"], str(self.home / ".local/share/godot/export_templates"))


class TestDevelopmentWait(unittest.TestCase):
    def snapshot(self, state, ok=True, returncode=None):
        return {"state": state, "ok": ok, "returncode": returncode,
                "task": "a" * 32, "log": "/private/output.log", "report": "/private/result.json"}

    def test_running_until_verified_completion_and_preserves_evidence(self):
        with patch.object(development, "task_status", side_effect=[self.snapshot("running"), self.snapshot("completed", returncode=0)]) as status, \
                patch.object(development.time, "monotonic", side_effect=[0, 0, 0.5]), \
                patch.object(development.time, "sleep") as sleep:
            result = development.wait_task("a" * 32, timeout=10)
        self.assertTrue(result["ok"])
        self.assertFalse(result["wait_timed_out"])
        self.assertEqual(result["report"], "/private/result.json")
        self.assertEqual(status.call_count, 2)
        sleep.assert_called_once_with(0.5)

    def test_terminal_noncompletion_and_failed_exit_are_failures(self):
        for state, ok, code in (("stopped", True, 0), ("unverified", False, 0),
                                ("unavailable", False, None), ("failed", False, 2)):
            with self.subTest(state=state), patch.object(development, "task_status", return_value=self.snapshot(state, ok, code)), \
                    patch.object(development.time, "sleep") as sleep:
                result = development.wait_task("a" * 32)
                self.assertFalse(result["ok"])
                self.assertFalse(result["wait_timed_out"])
                sleep.assert_not_called()

    def test_deadline_retains_running_state_without_stopping(self):
        with patch.object(development, "task_status", return_value=self.snapshot("running")) as status, \
                patch.object(development.time, "monotonic", side_effect=[0, 1]), \
                patch.object(development.time, "sleep") as sleep:
            result = development.wait_task("a" * 32, timeout=1)
        self.assertFalse(result["ok"])
        self.assertTrue(result["wait_timed_out"])
        self.assertEqual(result["state"], "running")
        status.assert_called_once_with("a" * 32)
        sleep.assert_not_called()

    def test_invalid_deadlines_fail_before_task_inspection(self):
        with patch.object(development, "task_status") as status:
            for timeout in (0, -1, 3601, True, float("inf")):
                with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                    development.wait_task("a" * 32, timeout=timeout)
            status.assert_not_called()

    def test_cli_json_outcomes_and_export_check_routing(self):
        parser = argparse.ArgumentParser()
        development.add_development_parser(parser.add_subparsers(dest="command"))
        args = parser.parse_args(["develop", "wait", "a" * 32, "--timeout", "20", "--json"])
        args.native = True
        for state, expected in (("completed", 0), ("failed", 1), ("running", 1), ("stopped", 1)):
            snapshot = self.snapshot(state, state in ("completed", "running", "stopped"), 0)
            with self.subTest(state=state), patch.object(development, "task_status", return_value=snapshot), \
                    patch.object(development.time, "monotonic", side_effect=[0, 20 if state == "running" else 1]), \
                    redirect_stdout(output := StringIO()):
                self.assertEqual(development.run_development_command(args), expected)
                self.assertEqual(json.loads(output.getvalue())["ok"], expected == 0)
        args = parser.parse_args(["develop", "doctor", "--project", "/project", "--export-check", "--json"])
        args.native = True
        with patch.object(development, "doctor", return_value={"ok": True}) as doctor, redirect_stdout(StringIO()):
            self.assertEqual(development.run_development_command(args), 0)
        doctor.assert_called_once_with("/project", None, export_check=True)


if __name__ == "__main__":
    unittest.main()
