"""Tests for explicit project checks and retained, private rendering evidence."""

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

from lib import agent_cli, agent_environment, agent_visual_check
from lib.remote_utils import CommandTimeoutError


class TestAgentVisualCheck(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.project = self.root / "project"
        self.project.mkdir()
        self.output = self.root / "evidence"
        self.settings = self.root / "settings.json"
        self.settings.write_text('{"frame":180,"resolution":[3840,2160]}')
        self.manifest = {
            "workspace": {"repository": str(self.project), "commit": "a" * 40, "dirty": False},
            "recipes": {"graphics": {
                "description": "Check complete game captures", "directory": ".",
                "argv": ["./check-graphics", "literal $(touch unwanted)", "{output}"],
                "requires": [], "missing_tools": [],
            }},
        }
        self.inspect = self.enterContext(patch.object(agent_visual_check, "inspect_environment", return_value=self.manifest))
        self.context = self.enterContext(patch.object(agent_visual_check, "host_context", return_value={"kernel": "test"}))
        self.execute = self.enterContext(patch.object(agent_visual_check, "run", return_value=subprocess.CompletedProcess([], 0)))
        self.enterContext(patch.object(agent_visual_check, "is_dry_run", return_value=False))
        self.parser = argparse.ArgumentParser()
        agent_cli.add_agent_subparser(self.parser.add_subparsers(dest="command"))

    def args(self, *extra):
        return self.parser.parse_args([
            "agent", "visuals", "check", "graphics", "--repository", str(self.project),
            "--output", str(self.output), "--settings", str(self.settings), *extra,
        ])

    def test_check_preserves_literal_argv_backend_and_private_evidence(self) -> None:
        with patch.dict(os.environ, {"SDL_VIDEODRIVER": "offscreen", "UNRELATED_TOKEN": "private"}):
            result = agent_visual_check.check_recipe(self.args("--timeout", "25"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["verification_scope"], "project-recipe-exit-status")
        self.assertEqual(result["gpu_readiness"], "unverified")
        self.assertEqual(result["ui_readiness"], "unverified")
        call = self.execute.call_args
        self.assertEqual(call.args[0], self.manifest["recipes"]["graphics"]["argv"])
        self.assertEqual(call.kwargs["cwd"], str(self.project / "."))
        self.assertEqual(call.kwargs["timeout"], 25)
        self.assertEqual(call.kwargs["input_data"], "")
        self.assertEqual(call.kwargs["env"]["SDL_VIDEODRIVER"], "offscreen")
        self.assertEqual(call.kwargs["env"]["BASALTWATER_VISUAL_EVIDENCE"], str(self.output))
        self.assertEqual(call.kwargs["env"]["BASALTWATER_VISUAL_SETTINGS"], str(self.output / "settings.json"))
        for filename, mode in (("check.json", 0o600), ("check.log", 0o600), ("environment.json", 0o600), ("settings.json", 0o400)):
            self.assertEqual((self.output / filename).stat().st_mode & 0o777, mode)
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o700)
        self.assertEqual(json.loads((self.output / "check.json").read_text()), result)
        snapshot = json.loads((self.output / "environment.json").read_text())
        self.assertEqual(snapshot["manifest"]["workspace"]["commit"], "a" * 40)
        self.assertNotIn("UNRELATED_TOKEN", json.dumps(snapshot))
        self.assertEqual(json.loads((self.output / "settings.json").read_text()), json.loads(self.settings.read_text()))

    def test_failed_recipe_retains_log_returncode_and_report(self) -> None:
        def failure(argv, **kwargs):
            kwargs["stdout"].write("renderer could not initialize\n")
            return subprocess.CompletedProcess(argv, 17)

        self.execute.side_effect = failure
        result = agent_visual_check.check_recipe(self.args())
        self.assertFalse(result["ok"])
        self.assertEqual(result["returncode"], 17)
        self.assertEqual(result["status"], "failed")
        self.assertIn("renderer could not initialize", (self.output / "check.log").read_text())
        self.assertTrue((self.output / "environment.json").is_file())

    def test_timeout_interruption_and_launch_failure_retain_evidence(self) -> None:
        for exception, status in ((CommandTimeoutError("check", 10), "timed-out"), (KeyboardInterrupt(), "interrupted"), (FileNotFoundError("missing executable"), "failed")):
            with self.subTest(status=status):
                self.execute.side_effect = exception
                args = self.args()
                args.output = str(self.root / status)
                result = agent_visual_check.check_recipe(args)
                self.assertFalse(result["ok"])
                self.assertEqual(result["status"], status)
                self.assertIsNone(result["returncode"])
                self.assertTrue(Path(result["report"]).is_file())

    def test_undeclared_recipe_and_missing_tools_fail_before_execution(self) -> None:
        self.manifest["recipes"] = {}
        with self.assertRaisesRegex(ValueError, "Declare recipe"):
            agent_visual_check.check_recipe(self.args())
        self.manifest["recipes"] = {"graphics": {"missing_tools": ["scene-checker"]}}
        with self.assertRaisesRegex(ValueError, "Missing recipe tools"):
            agent_visual_check.check_recipe(self.args())
        self.execute.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_interruption_during_metadata_collection_retains_report(self) -> None:
        self.context.side_effect = KeyboardInterrupt()
        result = agent_visual_check.check_recipe(self.args())
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "interrupted")
        self.assertTrue((self.output / "check.json").is_file())
        self.execute.assert_not_called()

    def test_invalid_settings_timeout_and_dry_run_do_not_launch(self) -> None:
        for timeout in (0, 3601, True):
            args = self.args()
            args.timeout = timeout
            with self.assertRaises(ValueError):
                agent_visual_check.check_recipe(args)
        with patch.object(agent_visual_check, "is_dry_run", return_value=True), self.assertRaisesRegex(ValueError, "dry-run"):
            agent_visual_check.check_recipe(self.args())
        self.settings.write_text('{"exposure":NaN}')
        with self.assertRaises(ValueError):
            agent_visual_check.check_recipe(self.args())
        self.inspect.assert_not_called()
        self.execute.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_changed_recorded_settings_fail_the_check(self) -> None:
        def change_settings(argv, **kwargs):
            path = Path(kwargs["env"]["BASALTWATER_VISUAL_SETTINGS"])
            path.chmod(0o600)
            path.write_text('{"frame":0}')
            return subprocess.CompletedProcess(argv, 0)

        self.execute.side_effect = change_settings
        result = agent_visual_check.check_recipe(self.args())
        self.assertFalse(result["ok"])
        self.assertIn("changed the recorded settings", result["error"])

    def test_existing_output_is_preserved(self) -> None:
        self.output.mkdir()
        marker = self.output / "check.json"
        marker.write_text("preserve")
        with self.assertRaises(FileExistsError):
            agent_visual_check.check_recipe(self.args())
        self.assertEqual(marker.read_text(), "preserve")
        self.execute.assert_not_called()

    def test_recipe_directory_validation_rejects_symlink_escape(self) -> None:
        (self.project / "escape").symlink_to(self.root, target_is_directory=True)
        (self.project / agent_environment.PROJECT_FILE).write_text(json.dumps({"version": 1, "recipes": {
            "graphics": {"description": "Check rendering", "argv": ["./check"], "directory": "escape"},
        }}))
        self.inspect.side_effect = lambda _: agent_environment.load_project_environment(str(self.project))
        with self.assertRaisesRegex(ValueError, "remain below"):
            agent_visual_check.check_recipe(self.args())
        self.execute.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_cli_dispatch_returns_json_with_failure_status(self) -> None:
        self.execute.return_value = subprocess.CompletedProcess([], 3)
        with redirect_stdout(output := StringIO()):
            self.assertEqual(agent_cli.run_agent_command(self.args("--json")), 1)
        result = json.loads(output.getvalue())
        self.assertFalse(result["ok"])
        self.assertEqual(result["returncode"], 3)


class TestVisualHostContext(unittest.TestCase):
    def test_snapshot_records_only_allowlisted_environment_and_packages(self) -> None:
        with (
            patch.object(agent_visual_check.platform, "system", return_value="Linux"),
            patch.object(agent_visual_check.platform, "release", return_value="test-kernel"),
            patch.object(agent_visual_check.platform, "machine", return_value="x86_64"),
            patch.object(agent_visual_check.platform, "freedesktop_os_release", return_value={"ID": "cachyos", "SECRET": "private"}),
            patch.object(agent_visual_check.shutil, "which", return_value="/usr/bin/pacman"),
            patch.object(agent_visual_check, "run", return_value=subprocess.CompletedProcess([], 1, "mesa 26.2\nsdl3 3.4\nunrelated 1.0\n", "missing optional packages")) as execute,
        ):
            context = agent_visual_check.host_context({"SDL_VIDEODRIVER": "offscreen", "TOKEN": "private"})
        self.assertEqual(context["graphics_environment"], {"SDL_VIDEODRIVER": "offscreen"})
        self.assertEqual(context["graphics_packages"], {"mesa": "26.2", "sdl3": "3.4"})
        self.assertEqual(context["package_observation"], "partial")
        self.assertEqual(context["distribution"], {"ID": "cachyos"})
        self.assertNotIn("private", json.dumps(context))
        self.assertEqual(execute.call_args.args[0], ["/usr/bin/pacman", "-Q", *agent_visual_check.GRAPHICS_PACKAGES])
        self.assertEqual(execute.call_args.kwargs["timeout"], 10)

    def test_missing_package_manager_and_metadata_are_optional(self) -> None:
        with (
            patch.object(agent_visual_check.platform, "freedesktop_os_release", side_effect=OSError("no metadata")),
            patch.object(agent_visual_check.shutil, "which", return_value=None),
            patch.object(agent_visual_check, "run") as execute,
        ):
            context = agent_visual_check.host_context({})
        self.assertEqual(context["distribution"], {})
        self.assertEqual(context["package_observation"], "unavailable")
        execute.assert_not_called()

    def test_package_probe_failure_does_not_fail_project_check(self) -> None:
        with (
            patch.object(agent_visual_check.platform, "freedesktop_os_release", return_value={}),
            patch.object(agent_visual_check.shutil, "which", return_value="/usr/bin/pacman"),
            patch.object(agent_visual_check, "run", side_effect=CommandTimeoutError("pacman", 10)),
        ):
            self.assertEqual(agent_visual_check.host_context({})["package_observation"], "unavailable")


if __name__ == "__main__":
    unittest.main()
