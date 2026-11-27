"""Tests for explicit project checks and retained, private rendering evidence."""

from __future__ import annotations

import argparse
import hashlib
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from lib import agent_cli, agent_environment, agent_visual_check, agent_workspace, godot_qa_prepare
from lib.atomic_io import write_json_atomic
from lib.agent_probe import ProbeResult
from lib.remote_utils import CommandTimeoutError
from tests.test_agent_visuals import png_bytes


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
        self.execute = self.enterContext(patch.object(agent_visual_check, "run_streamed", return_value=0))
        self.enterContext(patch.object(agent_visual_check, "is_dry_run", return_value=False))
        self.parser = argparse.ArgumentParser()
        agent_cli.add_agent_subparser(self.parser.add_subparsers(dest="command"))

    def args(self, *extra):
        return self.parser.parse_args([
            "agent", "visuals", "check", "graphics", "--repository", str(self.project),
            "--output", str(self.output), "--settings", str(self.settings), *extra,
        ])

    def test_check_preserves_literal_argv_backend_and_private_evidence(self) -> None:
        with patch.dict(os.environ, {"SDL_VIDEODRIVER": "offscreen", "UNRELATED_TOKEN": "private", "BASALTWATER_VISUAL_VERIFY": "1"}):
            result = agent_visual_check.check_recipe(self.args("--timeout", "25"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["verification_scope"], "project-recipe-exit-status")
        self.assertEqual(result["gpu_readiness"], "unverified")
        self.assertEqual(result["ui_readiness"], "unverified")
        self.assertNotIn("review", result)
        self.assertNotIn("tool_version", result)
        call = self.execute.call_args
        self.assertEqual(call.args[0], self.manifest["recipes"]["graphics"]["argv"])
        self.assertEqual(call.kwargs["cwd"], str(self.project / "."))
        self.assertEqual(call.kwargs["timeout"], 25)
        self.assertNotIn("input_data", call.kwargs)  # stdin is closed, never an inherited terminal.
        self.assertEqual(call.kwargs["env"]["SDL_VIDEODRIVER"], "offscreen")
        self.assertEqual(call.kwargs["env"]["BASALTWATER_VISUAL_EVIDENCE"], str(self.output))
        self.assertEqual(call.kwargs["env"]["BASALTWATER_VISUAL_SETTINGS"], str(self.output / "settings.json"))
        self.assertEqual(call.kwargs["env"]["BASALTWATER_VISUAL_VERIFY"], "0")
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
            kwargs["on_output"]("renderer could not initialize\n")
            return 17

        self.execute.side_effect = failure
        result = agent_visual_check.check_recipe(self.args())
        self.assertFalse(result["ok"])
        self.assertEqual(result["returncode"], 17)
        self.assertEqual(result["status"], "failed")
        self.assertIn("renderer could not initialize", (self.output / "check.log").read_text())
        self.assertTrue((self.output / "environment.json").is_file())

    def test_godot_preparation_reports_missing_assets_then_imports_before_gpu_recipe(self) -> None:
        subprocess.run(["git", "init", "-q", str(self.project)], check=True, timeout=5)
        (self.project / ".gitignore").write_text(".godot/\nmodels/\naudio/\ntextures/\n")
        (self.project / "project.godot").write_text("[application]\n")
        (self.project / "audio").mkdir()
        (self.project / "audio/local.ogg").write_bytes(b"keep worktree copy")
        bundle = self.root / "approved bundle"
        for asset in ("audio", "models"):
            (bundle / asset).mkdir(parents=True)
            (bundle / asset / "data").write_bytes(b"from approved bundle")
        recipe = self.manifest["recipes"]["graphics"]
        recipe["godot_prepare"] = godot_qa_prepare.parse_declaration({"asset_directories": ["models", "audio", "textures"]})
        locks = self.root / "locks"
        locks.mkdir(mode=0o700)
        engine = self.root / "godot"
        engine.write_text("mock engine")
        engine.chmod(0o700)

        def import_assets(argv, **kwargs):
            self.assertEqual(argv, [str(engine), "--path", str(self.project), "--headless", "--import"])
            self.assertEqual(kwargs["timeout"], 900)
            self.assertEqual(json.loads((self.output / "prepare.json").read_text())["status"], "importing")
            self.execute.assert_not_called()
            self.assertEqual((self.project / "models/data").read_bytes(), b"from approved bundle")
            (self.project / ".godot").mkdir()
            kwargs["on_output"]("Importing assets: 85%\n")
            return 0

        def run_recipe(argv, **kwargs):
            self.assertTrue((self.project / ".godot").is_dir())
            self.assertEqual(json.loads((self.output / "prepare.json").read_text())["status"], "passed")
            return 0

        with (patch("lib.agent_reservations._root", return_value=locks),
              patch.dict(os.environ, {"PATH": f"{self.root}{os.pathsep}{os.environ['PATH']}"}),
              patch.object(godot_qa_prepare, "run_streamed", side_effect=import_assets) as run_import):
            args = self.args("--prepare", "--asset-bundle", str(bundle), "--timeout", "900")
            missing = agent_visual_check.check_recipe(args)
            self.assertFalse(missing["ok"])
            self.assertIn("textures", missing["error"])
            self.assertFalse((self.project / "models").exists())
            run_import.assert_not_called()
            self.execute.assert_not_called()

            (bundle / "textures").mkdir()
            self.output = self.root / "retry"
            args.output = str(self.output)
            self.execute.side_effect = run_recipe
            prepared = agent_visual_check.check_recipe(args)
        self.assertTrue(prepared["ok"], prepared)
        self.assertIn("Importing assets: 85%", (self.output / "check.log").read_text())
        self.assertEqual((self.project / "audio/local.ogg").read_bytes(), b"keep worktree copy")
        self.assertFalse((self.project / "audio/data").exists())
        self.assertFalse((bundle / ".godot").exists())
        self.assertEqual(run_import.call_args.kwargs["pass_fds"], self.execute.call_args.kwargs["pass_fds"])

    def test_godot_preparation_blocks_new_and_cached_import_failures_with_zero_exit(self) -> None:
        self.manifest["recipes"]["graphics"]["godot_prepare"] = {"project": ".", "asset_directories": []}
        plan = {"project": str(self.project), "assets": {}, "import_argv": ["godot", "--headless", "--import"]}
        asset = self.project / "broken.svg"
        asset.write_text("invalid fixture")
        sidecar = asset.with_suffix(".svg.import")
        invalid_remap = '[remap]\nimporter="texture"\nvalid=false\n\n[deps]\n'
        locks = self.root / "locks"
        locks.mkdir(mode=0o700)
        for name, marker in (("ignored", ".gdignore"), ("nested", "project.godot"), (".hidden", None)):
            directory = self.project / name
            directory.mkdir()
            if marker:
                (directory / marker).touch()
            (directory / asset.name).write_text("invalid fixture")
            (directory / sidecar.name).write_text(invalid_remap)
        (self.project / "orphan.svg.import").write_text(invalid_remap)

        def import_assets(argv, *, on_output, **kwargs):
            on_output("\x1b[31mERROR:\x1b[0m Error importing 'res://broken.svg'.\n")
            sidecar.write_text(invalid_remap)
            return 0

        with (patch("lib.agent_reservations._root", return_value=locks),
              patch.object(godot_qa_prepare, "preparation_plan", return_value=plan),
              patch.object(godot_qa_prepare, "run_streamed", side_effect=import_assets) as run_import):
            cold = agent_visual_check.check_recipe(self.args("--prepare"))
            self.assertIn("Error importing 'res://broken.svg'", cold["error"])
            self.assertIn("Error importing", Path(cold["log"]).read_text())
            self.execute.assert_not_called()

            run_import.side_effect = None
            run_import.return_value = 0
            self.output = self.root / "warm"
            warm = agent_visual_check.check_recipe(self.args("--prepare"))
            self.assertIn("invalid import for res://broken.svg", warm["error"])
            self.execute.assert_not_called()

            for result in (cold, warm):
                self.assertEqual(result["failure_stage"], "preparation")
                preparation = json.loads(Path(result["preparation"]["report"]).read_text())
                self.assertEqual((preparation["status"], preparation["returncode"]), ("failed", 0))
                self.assertIsNone(result["returncode"])

            sidecar.write_text('[remap]\nimporter="texture"\n\n[params]\nvalid=false\n')
            self.output = self.root / "repaired"
            self.assertTrue(agent_visual_check.check_recipe(self.args("--prepare"))["ok"])
            self.execute.assert_called_once()

    def test_renderer_loss_surfaces_partial_case_results_after_log_truncation(self) -> None:
        image = self.output / "gameplay.png"

        def renderer_crash(argv, **kwargs):
            image.write_bytes(png_bytes())
            (self.output / "foreign.png").symlink_to(self.root / "foreign.png")
            write_json_atomic(kwargs["env"]["BASALTWATER_VISUAL_CASES"], {"version": 1, "complete": False, "cases": [
                {"id": "gameplay", "status": "passed", "captures": [image.name]},
                {"id": "resize", "status": "incomplete"},
            ]})
            kwargs["on_output"]("Ordinary log output\n")
            kwargs["on_output"]("ERROR: VK_ERROR_")
            kwargs["on_output"]("DEVICE_LOST\n")
            return 7

        self.execute.side_effect = renderer_crash
        with patch.object(agent_visual_check, "LOG_LIMIT", 8):
            result = agent_visual_check.check_recipe(self.args())
        self.assertFalse(result["ok"])
        self.assertTrue(result["log_truncated"])
        self.assertEqual(result["renderer_error"]["code"], "vulkan-device-lost")
        self.assertEqual(result["case_results"]["status"], "partial")
        self.assertEqual(result["case_results"]["last_completed_case"], "gameplay")
        self.assertEqual(result["case_results"]["cases"][0]["captures"], [str(image)])
        self.assertEqual(result["saved_captures"], [str(image)])

    def test_renderer_loss_overrides_zero_exit_and_keeps_saved_captures(self) -> None:
        image = self.output / "before-resize.png"

        def execute(argv, **kwargs):
            image.write_bytes(png_bytes())
            kwargs["on_output"]("Vulkan: device was lost during resize\n")
            return 0

        self.execute.side_effect = execute
        result = agent_visual_check.check_recipe(self.args())
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["returncode"], 0)
        self.assertEqual(result["failure_stage"], "renderer")
        self.assertEqual(result["renderer_error"]["source"], "log")
        self.assertEqual(result["saved_captures"], [str(image)])

    def test_pair_runs_both_pinned_checks_with_one_settings_snapshot_and_matches_cases(self) -> None:
        args = self.parser.parse_args([
            "agent", "visuals", "check-revisions", "graphics", "--repository", str(self.project),
            "--before", "main", "--after", "candidate", "--settings", str(self.settings),
            "--output", str(self.output), "--json",
        ])
        baseline, candidate = self.root / "baseline", self.root / "candidate"
        baseline.mkdir()
        candidate.mkdir()
        revisions = {str(baseline): "a" * 40, str(candidate): "b" * 40}
        self.inspect.side_effect = lambda repository: {
            **self.manifest, "workspace": {"repository": repository, "commit": revisions[repository], "dirty": False},
        }
        observations = []

        def execute(argv, *, cwd, env, **kwargs):
            side = Path(cwd)
            actual = json.loads(Path(env["BASALTWATER_VISUAL_SETTINGS"]).read_text())
            observations.append((side, actual))
            if side == baseline:
                self.settings.write_text('{"frame":99}')
            statuses = ("passed", "failed", "failed") if side == baseline else ("failed", "failed", "passed")
            write_json_atomic(env["BASALTWATER_VISUAL_CASES"], {"version": 1, "complete": True, "cases": [
                {"id": case, "status": status} for case, status in zip(("resume", "settings", "quit"), statuses)
            ]})
            # The candidate records complete assertion results before exiting normally.
            return 1 if side == baseline else 0

        self.execute.side_effect = execute
        with (patch.object(agent_workspace, "_repository_root", return_value=str(self.project)),
              patch.object(agent_workspace, "_validate_base", side_effect=list(revisions.values())),
              patch.object(agent_workspace, "create_agent_worktree", side_effect=[{"path": str(baseline)}, {"path": str(candidate)}]) as create,
              redirect_stdout(output := StringIO())):
            self.assertEqual(agent_cli.run_agent_command(args), 1)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "regressed")
        self.assertEqual(result["checks"]["after"]["returncode"], 0)
        self.assertEqual({case["id"]: case["outcome"] for case in result["cases"]},
                         {"resume": "regressed", "settings": "persisted", "quit": "fixed"})
        self.assertEqual([call.kwargs["base"] for call in create.call_args_list], list(revisions.values()))
        self.assertEqual(observations, [(baseline, {"frame": 180, "resolution": [3840, 2160]}),
                                        (candidate, {"frame": 180, "resolution": [3840, 2160]})])
        self.assertTrue(all(Path(check["check_report"]).is_file() for check in result["checks"].values()))
        saved = json.loads(Path(result["comparison_report"]).read_text())
        self.assertEqual(saved, {key: result[key] for key in saved})
        self.assertIn(result["comparison_report"], [entry["path"] for entry in result["artifacts"]])

    def test_pair_marks_cases_unverified_after_a_renderer_failure_or_recipe_change(self) -> None:
        baseline, candidate = self.root / "baseline", self.root / "candidate"
        baseline.mkdir()
        candidate.mkdir()
        for reason in ("renderer", "declaration"):
            with self.subTest(reason=reason):
                pair_output = self.root / reason
                args = self.parser.parse_args([
                    "agent", "visuals", "check-revisions", "graphics", "--repository", str(self.project),
                    "--before", "main", "--after", "candidate", "--output", str(pair_output), "--json",
                ])

                def inspect(repository):
                    after = repository == str(candidate)
                    recipe = self.manifest["recipes"]["graphics"]
                    if after and reason == "declaration":
                        recipe = {**recipe, "argv": [*recipe["argv"], "--another-fixture"]}
                    return {"workspace": {"repository": repository, "commit": ("b" if after else "a") * 40,
                                          "dirty": False}, "recipes": {"graphics": recipe}}

                def execute(argv, *, cwd, env, on_output, **kwargs):
                    after = Path(cwd) == candidate
                    if after:
                        on_output("ERROR: VK_ERROR_DEVICE_LOST\n")
                    write_json_atomic(env["BASALTWATER_VISUAL_CASES"], {"version": 1, "complete": not after, "cases": [
                        {"id": "gameplay", "status": "failed" if after else "passed"},
                    ]})
                    return 1 if after else 0

                self.inspect.side_effect = inspect
                self.execute.side_effect = execute
                self.execute.reset_mock()
                with (patch.object(agent_workspace, "_repository_root", return_value=str(self.project)),
                      patch.object(agent_workspace, "_validate_base", side_effect=["a" * 40, "b" * 40]),
                      patch.object(agent_workspace, "create_agent_worktree", side_effect=[{"path": str(baseline)},
                                   {"path": str(candidate)}]), redirect_stdout(output := StringIO())):
                    self.assertEqual(agent_cli.run_agent_command(args), 1)
                report = json.loads(output.getvalue())
                self.assertEqual(report["status"], "incomplete")
                self.assertTrue(report["checks"]["before"]["comparable"])
                after = report["checks"]["after"]
                self.assertFalse(after["comparable"])
                self.assertEqual(report["cases"], [{"id": "gameplay", "before": "passed",
                    "after": "failed" if reason == "renderer" else None, "outcome": "unverified"}])
                if reason == "renderer":
                    self.assertEqual(after["renderer_error"]["code"], "vulkan-device-lost")
                    self.assertEqual(self.execute.call_count, 2)
                else:
                    self.assertEqual(after["recipe_differences"], ["argv"])
                    self.execute.assert_called_once()

    def test_pair_skips_candidate_when_baseline_declaration_is_unavailable(self) -> None:
        args = self.parser.parse_args([
            "agent", "visuals", "check-revisions", "graphics", "--repository", str(self.project),
            "--before", "main", "--after", "candidate", "--output", str(self.output), "--json",
        ])
        self.inspect.return_value = {
            "workspace": {"repository": str(self.project), "commit": "a" * 40, "dirty": False},
            "recipes": {},
        }
        with (patch.object(agent_workspace, "_repository_root", return_value=str(self.project)),
              patch.object(agent_workspace, "_validate_base", side_effect=["a" * 40, "b" * 40]),
              patch.object(agent_workspace, "create_agent_worktree", return_value={"path": str(self.project)}) as create,
              redirect_stdout(output := StringIO())):
            self.assertEqual(agent_cli.run_agent_command(args), 1)
        report = json.loads(output.getvalue())
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["checks"]["after"]["status"], "not-run")
        create.assert_called_once()
        self.execute.assert_not_called()

    def test_pinned_executable_reaches_the_recipe_after_a_bounded_probe(self) -> None:
        version = {"executable": "blender", "expected": "Blender 4.5.2"}
        self.manifest["recipes"]["graphics"]["version_check"] = version
        tool = self.project / "tools" / "blender"

        def probe_version(argv, **kwargs):
            self.assertNotIn("BASALTWATER_VISUAL_TOOL", kwargs["env"])
            return ProbeResult("Blender 4.5.2\nBuild details\n")

        with (
            patch.dict(os.environ, {"PATH": "tools:/usr/bin", "BASALTWATER_VISUAL_TOOL": "/other"}),
            patch.object(agent_visual_check.shutil, "which", return_value=str(tool)) as locate,
            patch.object(agent_visual_check, "run_probe", side_effect=probe_version) as probe,
        ):
            result = agent_visual_check.check_recipe(self.args())
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["tool_version"], {**version, "path": str(tool), "observed": version["expected"], "status": "verified"})
        locate.assert_called_once_with("blender", path=f"{tool.parent}:/usr/bin")
        self.assertEqual(probe.call_args.args[0], [str(tool), "--version"])
        self.assertEqual(probe.call_args.kwargs["cwd"], str(self.project))
        self.assertEqual(probe.call_args.kwargs["timeout"], 5)
        self.assertEqual(probe.call_args.kwargs["max_bytes"], 4096)
        self.assertEqual(self.execute.call_args.kwargs["env"]["BASALTWATER_VISUAL_TOOL"], str(tool))
        self.assertEqual(self.execute.call_args.kwargs["env"]["PATH"], "tools:/usr/bin")

    def test_preflight_failures_distinguish_missing_mismatch_and_failed_probes(self) -> None:
        from lib import agent_performance

        selection = {"executable": "./tools/blender", "expected": "Blender 4.5.2"}
        self.manifest["recipes"]["graphics"]["version_check"] = selection
        tool = str(self.project / "tools" / "blender")
        variants = (
            (None, None, "missing", None, None),
            (tool, ProbeResult("Blender 4.3.2\n"), "unexpected-version", "Blender 4.3.2", None),
            (tool, ProbeResult(issue="probe_timeout"), "probe-failed", None, "probe_timeout"),
            (tool, ProbeResult(issue="probe_output_limit"), "probe-failed", None, "probe_output_limit"),
            (tool, ProbeResult("\x1b[34mBlender 4.5.2\n"), "probe-failed", None, "invalid_version_line"),
        )
        for index, (path, response, status, observed, issue) in enumerate(variants):
            with (
                self.subTest(status=status, issue=issue),
                patch.object(agent_visual_check.shutil, "which", return_value=path) as locate,
                patch.object(agent_visual_check, "run_probe", return_value=response) as probe,
                patch.object(agent_performance, "performance_snapshot") as snapshot,
            ):
                self.output = self.root / f"preflight-{index}"
                result = agent_visual_check.check_recipe(self.args("--performance-session"))
                self.assertFalse(result["ok"])
                self.assertIsNone(result["returncode"])
                version = result["tool_version"]
                self.assertEqual((version["status"], version["observed"], version.get("issue")), (status, observed, issue))
                self.assertEqual(json.loads((self.output / "check.json").read_text()), result)
                self.assertTrue((self.output / "environment.json").is_file())
                locate.assert_called_once_with(str(self.project / "./tools/blender"))
                self.assertEqual(probe.call_count, int(path is not None))
                snapshot.assert_not_called()
        self.execute.assert_not_called()

    def test_strict_check_verifies_runtime_and_build_evidence(self) -> None:
        artifact = self.project / "asset.bin"
        artifact.write_bytes(b"asset export")

        def checked(argv, **kwargs):
            env = kwargs["env"]
            self.assertEqual(env["BASALTWATER_VISUAL_VERIFY"], "1")
            receipt = {"version": 1, "revision": env["BASALTWATER_VISUAL_REVISION"],
                       "effective_settings": json.loads(self.settings.read_text()),
                       "artifacts": [{"path": "asset.bin", "kind": "asset", "revision": "a" * 40,
                                      "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}]}
            Path(env["BASALTWATER_VISUAL_RECEIPT"]).write_text(json.dumps(receipt))
            return 0

        self.execute.side_effect = checked
        result = agent_visual_check.check_recipe(self.args("--require-artifact-kind", "asset"))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["verification"]["status"], "verified")
        self.assertEqual(result["verification_scope"], "project-recipe-and-checked-receipt")

    def test_log_quota_keeps_draining_and_preserves_failure(self) -> None:
        def flood(argv, **kwargs):
            for chunk in ("12345", "67890", "still drained"):
                kwargs["on_output"](chunk)
            return 7

        self.execute.side_effect = flood
        with patch.object(agent_visual_check, "LOG_LIMIT", 8):
            result = agent_visual_check.check_recipe(self.args())
        self.assertEqual((self.output / "check.log").read_bytes(), b"12345678")
        self.assertTrue(result["log_truncated"])
        self.assertEqual(result["log_limit_bytes"], 8)
        self.assertEqual(result["returncode"], 7)
        self.assertEqual(result["status"], "failed")

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
            return 0

        self.execute.side_effect = change_settings
        result = agent_visual_check.check_recipe(self.args())
        self.assertFalse(result["ok"])
        self.assertIn("changed the recorded settings", result["error"])

    def test_settings_type_changes_cannot_pass_as_equal_python_values(self) -> None:
        for index, (before, after) in enumerate((
            ('{"value":1}', '{"value":true}'),
            ('{"value":0}', '{"value":false}'),
            ('{"nested":[1,0]}', '{"nested":[true,false]}'),
            ('{"value":1.0}', '{"value":1}'),
        )):
            with self.subTest(after=after):
                self.settings.write_text(before)
                def change_settings(argv, **kwargs):
                    path = Path(kwargs["env"]["BASALTWATER_VISUAL_SETTINGS"])
                    path.chmod(0o600)
                    path.write_text(after)
                    return 0
                self.execute.side_effect = change_settings
                args = self.args()
                args.output = str(self.root / f"types-{index}")
                result = agent_visual_check.check_recipe(args)
                self.assertFalse(result["ok"])
                self.assertIn("changed the recorded settings", result["error"])

    def test_settings_formatting_changes_preserve_values_and_types(self) -> None:
        def reformat_settings(argv, **kwargs):
            path = Path(kwargs["env"]["BASALTWATER_VISUAL_SETTINGS"])
            path.chmod(0o600)
            path.write_text('{"resolution": [3840, 2160], "frame": 180}\n')
            return 0

        self.execute.side_effect = reformat_settings
        result = agent_visual_check.check_recipe(self.args())
        self.assertTrue(result["ok"])

    def test_existing_output_is_preserved(self) -> None:
        self.output.mkdir()
        marker = self.output / "check.json"
        marker.write_text("preserve")
        with self.assertRaises(FileExistsError):
            agent_visual_check.check_recipe(self.args())
        self.assertEqual(marker.read_text(), "preserve")
        self.execute.assert_not_called()

    def test_performance_session_records_before_inside_after_and_keeps_recipe_status(self) -> None:
        from lib import agent_performance

        def observed(argv, **kwargs):
            self.assertIn("--expect-profile", argv)
            self.assertTrue(kwargs["cleanup_group_on_exit"])
            self.assertEqual(argv[-3:], self.manifest["recipes"]["graphics"]["argv"])
            (self.output / "performance-active.json").write_text('{"schema_version":1,"power_profile":"performance"}')
            return 0
        self.execute.side_effect = observed
        with patch.object(agent_performance, "performance_snapshot", side_effect=[{"power_profile": "balanced"}, {"power_profile": "balanced"}]), \
                patch.object(agent_performance.shutil, "which", return_value="/usr/bin/powerprofilesctl"):
            result = agent_visual_check.check_recipe(self.args("--power-profile", "performance"))
        self.assertTrue(result["ok"], result)
        report = json.loads(Path(result["performance"]).read_text())
        self.assertEqual(report["requested_profile"], "performance")
        self.assertEqual(report["before"]["power_profile"], "balanced")
        self.assertEqual(report["after"]["power_profile"], "balanced")
        self.assertTrue(Path(report["active_observation"]).is_file())
        self.assertEqual(Path(result["performance"]).stat().st_mode & 0o777, 0o600)
        self.assertEqual(result["command"], self.manifest["recipes"]["graphics"]["argv"])

    def test_performance_failure_and_timeout_retain_post_session_evidence(self) -> None:
        from lib import agent_performance

        for index, (error, status) in enumerate(((TimeoutError(), "timed-out"), (KeyboardInterrupt(), "interrupted"), (OSError("launch failed"), "failed"))):
            args = self.args("--performance-session")
            args.output = str(self.root / f"performance-{index}")
            self.execute.side_effect = error
            with self.subTest(status=status), patch.object(agent_performance, "performance_snapshot", return_value={"power_profile": None}):
                result = agent_visual_check.check_recipe(args)
            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], status)
            retained = json.loads(Path(result["performance"]).read_text())
            self.assertEqual(retained["after"], {"power_profile": None})

    def test_performance_observations_are_opt_in_and_missing_active_evidence_fails(self) -> None:
        from lib import agent_performance

        with patch.object(agent_performance, "performance_snapshot") as snapshot:
            result = agent_visual_check.check_recipe(self.args())
        self.assertTrue(result["ok"])
        snapshot.assert_not_called()
        self.assertNotIn("performance", result)
        args = self.args("--performance-session")
        args.output = str(self.root / "missing-active")
        with patch.object(agent_performance, "performance_snapshot", return_value={"power_profile": None}):
            result = agent_visual_check.check_recipe(args)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "failed")

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
        self.execute.return_value = 3
        with redirect_stdout(output := StringIO()):
            self.assertEqual(agent_cli.run_agent_command(self.args("--json")), 1)
        result = json.loads(output.getvalue())
        self.assertFalse(result["ok"])
        self.assertEqual(result["returncode"], 3)

    def write_review_fixture(self) -> Path:
        manifest_path = self.output / "captures" / "review.json"
        manifest_path.parent.mkdir()
        (self.output / "before.png").write_bytes(png_bytes(4, 4))
        (self.output / "after.png").write_bytes(png_bytes(4, 4, (255, 0, 0, 255)))
        pairs = [
            {"id": f"frame-{frame}", "category": "pause",
             "before": {"image": "../before.png", "settings": {"frame": frame}},
             "after": {"image": "../after.png", "settings": {"frame": frame}}}
            for frame in (4, 8)
        ]
        manifest_path.write_text(json.dumps({
            "version": 1, "sequence": {"fps": 12}, "compare_keys": ["frame"],
            "expected_categories": ["pause", "settings"], "pairs": pairs,
        }))
        return manifest_path

    def test_check_links_gallery_from_one_completed_recipe(self) -> None:
        def completed(argv, **kwargs):
            self.assertEqual(kwargs["env"]["BASALTWATER_VISUAL_EVIDENCE"], str(self.output))
            self.write_review_fixture()
            return 0

        self.execute.side_effect = completed
        with redirect_stdout(output := StringIO()):
            self.assertEqual(agent_cli.run_agent_command(self.args("--review-manifest", "captures/review.json", "--json")), 0)
        self.execute.assert_called_once()
        result = json.loads(output.getvalue())
        saved = json.loads((self.output / "check.json").read_text())
        self.assertEqual(saved, {key: result[key] for key in saved})
        self.assertIn(str(self.output / "check.json"), [entry["path"] for entry in result["artifacts"]])
        review = result["review"]
        self.assertEqual(Path(review["manifest"]), self.output / "captures/review.json")
        self.assertEqual(Path(review["viewer"]), self.output / "review-gallery/index.html")
        self.assertIn(review["batch_id"], Path(review["viewer"]).read_text())
        batch = json.loads(Path(review["batch_report"]).read_text())
        self.assertEqual([pair["id"] for pair in batch["pairs"]], ["frame-4", "frame-8"])
        self.assertEqual(batch["sequence"], {"fps": 12})
        self.assertEqual(batch["missing_categories"], ["settings"])
        self.assertEqual(batch["visual_correctness"], "unverified")

    def test_check_builds_gallery_after_performance_observations(self) -> None:
        from lib import agent_performance

        def snapshot():
            self.assertFalse((self.output / "review-gallery").exists())
            return {"power_profile": None}

        def completed(argv, **kwargs):
            self.write_review_fixture()
            (self.output / "performance-active.json").write_text('{"schema_version":1}')
            return 0

        self.execute.side_effect = completed
        with patch.object(agent_performance, "performance_snapshot", side_effect=snapshot) as observe:
            result = agent_visual_check.check_recipe(self.args("--performance-session", "--review-manifest", "captures/review.json"))
        self.assertTrue(result["ok"], result)
        self.assertEqual(observe.call_count, 2)
        self.assertTrue(Path(result["review"]["viewer"]).is_file())

    def test_review_manifest_argument_rejects_nonlocal_names_before_recipe(self) -> None:
        for path in ("/tmp/review.json", "../review.json", "a/../review.json", ".", ""):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "relative to|non-empty"):
                agent_visual_check.check_recipe(self.args("--review-manifest", path))
        self.execute.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_failed_review_retains_recipe_exit_status_and_evidence(self) -> None:
        variants = {
            "missing-manifest": "Path does not exist",
            "missing-frame": "Path does not exist",
            "wrong-frame": "Different observed setting: frame",
            "malformed": "Expecting value",
            "parent-escape": "inside the recipe evidence directory",
            "linked-image": "inside the recipe evidence directory",
            "linked-manifest": "inside the recipe evidence directory",
        }
        for variant, error in variants.items():
            self.output = self.root / variant
            with self.subTest(variant=variant):
                def completed(argv, **kwargs):
                    kwargs["on_output"]("recipe rendered\n")
                    if variant == "missing-manifest":
                        return 0
                    path = self.write_review_fixture()
                    manifest = json.loads(path.read_text())
                    frame = manifest["pairs"][-1]["after"]
                    outside = self.root / "extra"
                    outside.mkdir(exist_ok=True)
                    (outside / "outside.png").write_bytes(png_bytes(4, 4))
                    if variant == "missing-frame":
                        frame["image"] = "missing.png"
                    elif variant == "wrong-frame":
                        frame["settings"]["frame"] = 7
                    elif variant == "parent-escape":
                        frame["image"] = "../../extra/outside.png"
                    elif variant == "linked-image":
                        (path.parent / "linked").symlink_to(outside, target_is_directory=True)
                        frame["image"] = "linked/outside.png"
                    elif variant == "linked-manifest":
                        path.rename(outside / "outside.json")
                        path.symlink_to(outside / "outside.json")
                        return 0
                    path.write_text("[" if variant == "malformed" else json.dumps(manifest))
                    return 0

                self.execute.side_effect = completed
                result = agent_visual_check.check_recipe(self.args("--review-manifest", "captures/review.json"))
                self.assertFalse(result["ok"])
                self.assertEqual(result["returncode"], 0)
                self.assertIn("Review gallery failed", result["error"])
                self.assertIn(error, result["error"])
                self.assertNotIn("viewer", result["review"])
                self.assertEqual(json.loads((self.output / "check.json").read_text()), result)
                self.assertEqual((self.output / "check.log").read_text(), "recipe rendered\n")
                self.assertFalse((self.output / "review-gallery").exists())


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
        self.assertEqual(execute.call_args.args[0], ["/usr/bin/pacman", "-Q", *agent_visual_check.ARCH_GRAPHICS_PACKAGES])
        self.assertEqual(execute.call_args.kwargs["timeout"], 10)

    def test_debian_query_retains_only_installed_packages_and_multiarch_versions(self) -> None:
        output = (
            "libgl1-mesa-dri:amd64\t25.0.7-2\tinstalled\n"
            "libgl1-mesa-dri:i386\t25.0.7-1\tinstalled\n"
            "xvfb\t2:21.1.16-1\tinstalled\n"
            "libsdl3-0:amd64\t3.2.10+ds-1\tunpacked\n"
            "nvidia-driver\t550.1\tconfig-files\n"
            "unrelated\t1.0\tinstalled\n"
            "xauth\t\tinstalled\n"
            "malformed\n"
        )
        for release in ({"ID": "debian", "VERSION_ID": "13"}, {"ID": "derivative", "ID_LIKE": "ubuntu debian"}):
            with (
                self.subTest(release=release),
                patch.object(agent_visual_check.platform, "freedesktop_os_release", return_value=release),
                patch.object(agent_visual_check.shutil, "which", return_value="/usr/bin/dpkg-query") as discover,
                patch.object(agent_visual_check, "run", return_value=subprocess.CompletedProcess([], 1, output, "missing optional packages")) as execute,
            ):
                context = agent_visual_check.host_context({})
            self.assertEqual(context["graphics_packages"], {
                "libgl1-mesa-dri:amd64": "25.0.7-2", "libgl1-mesa-dri:i386": "25.0.7-1", "xvfb": "2:21.1.16-1",
            })
            self.assertEqual(context["package_observation"], "partial")
            discover.assert_called_once_with("dpkg-query")
            self.assertEqual(execute.call_args.args[0], [
                "/usr/bin/dpkg-query", "--show", "--showformat=${binary:Package}\t${Version}\t${db:Status-Status}\n",
                *agent_visual_check.DEBIAN_GRAPHICS_PACKAGES,
            ])
            self.assertEqual(execute.call_args.kwargs["env"]["LC_ALL"], "C")

    def test_unknown_distribution_does_not_query_an_unrelated_package_manager(self) -> None:
        with (
            patch.object(agent_visual_check.platform, "freedesktop_os_release", return_value={"ID": "fedora"}),
            patch.object(agent_visual_check.shutil, "which") as discover,
            patch.object(agent_visual_check, "run") as execute,
        ):
            context = agent_visual_check.host_context({})
        self.assertEqual(context["package_observation"], "unavailable")
        discover.assert_not_called()
        execute.assert_not_called()

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
            patch.object(agent_visual_check.platform, "freedesktop_os_release", return_value={"ID": "cachyos"}),
            patch.object(agent_visual_check.shutil, "which", return_value="/usr/bin/pacman"),
            patch.object(agent_visual_check, "run", side_effect=CommandTimeoutError("pacman", 10)),
        ):
            self.assertEqual(agent_visual_check.host_context({})["package_observation"], "unavailable")

    def test_failed_debian_query_is_unavailable_without_retaining_output(self) -> None:
        with (
            patch.object(agent_visual_check.platform, "freedesktop_os_release", return_value={"ID": "debian"}),
            patch.object(agent_visual_check.shutil, "which", return_value="/usr/bin/dpkg-query"),
            patch.object(agent_visual_check, "run", return_value=subprocess.CompletedProcess([], 2, "xvfb\t1.0\tinstalled\n", "database error")),
        ):
            context = agent_visual_check.host_context({})
        self.assertEqual(context["package_observation"], "unavailable")
        self.assertEqual(context["graphics_packages"], {})


if __name__ == "__main__":
    unittest.main()
