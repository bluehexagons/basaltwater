"""Godot and secondary workflow plans with private fixtures and mocked services."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from desktop import development, development_workflows as workflows
from lib.atomic_io import write_json_atomic


class TestDevelopmentWorkflows(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.project = self.home / "project %n $HOME with spaces"
        self.project.mkdir()
        self.engine = self.home / "godot"
        self.engine.write_text("never executed")
        self.engine.chmod(0o700)
        self.enterContext(patch.object(development, "_guard", return_value=os.getuid()))
        self.enterContext(patch.object(development.pwd, "getpwuid", return_value=SimpleNamespace(pw_dir=str(self.home))))
        self.enterContext(patch.object(development, "is_dry_run", return_value=False))
        self.enterContext(patch.object(development, "_honor_human_pause"))
        self.enterContext(patch.dict(os.environ, {"PATH": "/usr/bin:/bin", "NVM_DIR": ""}))
        self.enterContext(patch.object(development, "session_environment", side_effect=lambda _: ({"WAYLAND_DISPLAY": "wayland-0"}, "available")))
        self.enterContext(patch.object(development.shutil, "which", side_effect=lambda name: str(self.engine) if name == "godot" else None))
        self.probe = self.enterContext(patch.object(development, "_probe", return_value=("ok", "")))
        self.enterContext(patch.object(development, "_unit", return_value={"LoadState": "loaded", "ActiveState": "active", "SubState": "running"}))
        self.manifest = {"workspace": {"repository": str(self.project)}, "recipes": {
            "smoke": {"directory": ".", "argv": ["./test"], "missing_tools": []},
        }}
        self.inspect = self.enterContext(patch("lib.agent_environment.inspect_environment", return_value=self.manifest))

    def package(self, **extra) -> None:
        (self.project / "package.json").write_text(json.dumps({"scripts": {"dev:electron": "electron .", "test": "node test.js"}, **extra}))

    def launch(self, kind, **kwargs):
        return development.launch(str(self.project), kind, **kwargs)

    def record(self, result):
        return development.load_task(Path(result["directory"]))

    def test_import_is_explicit_headless_and_dry_run_does_not_create_cache_or_state(self):
        (self.project / "project.godot").write_text('[application]\nconfig/features=PackedStringArray("4.7")\n')
        preview = self.launch("import", dry_run=True)
        self.assertEqual(preview["argv"], [str(self.engine), "--path", str(self.project), "--headless", "--import"])
        self.probe.assert_not_called()
        self.assertFalse((self.home / ".local").exists())
        self.assertFalse((self.project / ".godot").exists())
        self.assertEqual(self.record(self.launch("import"))["kind"], "import")

    def test_engine_options_precede_scene_and_literal_game_arguments(self):
        (self.project / "project.godot").write_text("[application]\n")
        (self.project / "test.tscn").write_text("fixture")
        result = self.launch("run", headless=True, quit_after=120, rendering_method="gl_compatibility",
                             scene="res://test.tscn", argv=["--", "--rendering-method", "$HOME", ""])
        self.assertEqual(self.record(result)["argv"], [str(self.engine), "--path", str(self.project),
            "--headless", "--quit-after", "120", "--rendering-method", "gl_compatibility",
            str(self.project / "test.tscn"), "--", "--rendering-method", "$HOME", ""])

    def test_invalid_godot_options_fail_before_services_or_state(self):
        for kind, kwargs in (("run", {"quit_after": 0}), ("run", {"quit_after": True}),
                             ("editor", {"quit_after": 1000001}), ("run", {"rendering_method": "invalid"}),
                             ("exec", {"headless": True}), ("import", {"argv": ["ignored"]})):
            with self.subTest(kind=kind, kwargs=kwargs), self.assertRaises(ValueError):
                self.launch(kind, **kwargs)
        self.probe.assert_not_called()
        self.assertFalse((self.home / ".local").exists())

    def test_node_metadata_is_read_without_script_commands_or_execution(self):
        self.package(packageManager="pnpm@10.0.0", engines={"node": ">=22"})
        result = development.doctor(str(self.project))
        self.assertTrue(result["ok"])
        self.assertEqual(result["project"]["node"], {"scripts": ["dev:electron", "test"],
            "package_manager": "pnpm@10.0.0", "node_requirement": ">=22", "runtime_readiness": "unverified"})
        self.assertNotIn("electron .", json.dumps(result))
        self.probe.assert_not_called()

    def test_node_command_uses_own_cli_and_manager_specific_argument_separator(self):
        literal = ["--flag", "%n", "$HOME", "two words", ""]
        (self.project / "basaltwater.py").write_text("must never run")
        for manager in ("npm", "pnpm", "yarn"):
            with self.subTest(manager=manager):
                self.package(packageManager=manager + "@1.2.3")
                command = workflows.node_command(self.project, "dev:electron", None, literal)
                self.assertEqual(command[:2], [sys.executable, str(Path(development.__file__).resolve().parents[1] / "basaltwater.py")])
                self.assertEqual(command[2:9], ["node", "exec", "--project", str(self.project), "--", manager, "run"])
                self.assertEqual(command[9:], ["dev:electron", *(["--"] if manager == "npm" else []), *literal])

    def test_node_defaults_to_npm_and_explicit_override_handles_other_declarations(self):
        self.package()
        self.assertEqual(workflows.node_command(self.project, "test", None, [])[7], "npm")
        self.package(packageManager="bun@1.0.0")
        with self.assertRaisesRegex(ValueError, "Unsupported packageManager"):
            workflows.node_command(self.project, "test", None, [])
        self.assertEqual(workflows.node_command(self.project, "test", "yarn", [])[7], "yarn")

    def test_undeclared_or_option_like_node_scripts_fail_without_mutation(self):
        self.package()
        for script in ("missing", "--help", "dev:*", "test\n", "a" * 129):
            with self.subTest(script=script), self.assertRaises(ValueError):
                self.launch("node", script=script)
        self.probe.assert_not_called()
        self.assertFalse((self.home / ".local").exists())

    def test_package_metadata_is_bounded_regular_json(self):
        self.package()
        metadata = self.project / "package.json"
        for kind in ("symlink", "fifo", "oversized", "not-object", "invalid-scripts", "invalid-engines"):
            with self.subTest(kind=kind):
                metadata.unlink()
                if kind == "symlink":
                    metadata.symlink_to(self.engine)
                elif kind == "fifo":
                    os.mkfifo(metadata)
                else:
                    metadata.write_text({"oversized": " " * (1024 * 1024 + 1), "not-object": "[]",
                        "invalid-scripts": '{"scripts":[]}', "invalid-engines": '{"engines":[]}' }[kind])
                with self.assertRaises((ValueError, OSError)):
                    development.doctor(str(self.project))
        self.probe.assert_not_called()

    def test_node_retains_selected_nvm_root_and_disables_implicit_corepack_downloads(self):
        self.package()
        nvm = self.home / "runtime %n with spaces"
        nvm.mkdir()
        with patch.dict(os.environ, {"NVM_DIR": str(nvm), "PROVIDER_TOKEN": "secret"}):
            self.launch("node", script="test")
        command = self.probe.call_args.args[0]
        self.assertIn("--setenv=NVM_DIR=" + str(nvm), command)
        self.assertIn("--setenv=COREPACK_ENABLE_NETWORK=0", command)
        self.assertFalse(any("PROVIDER_TOKEN" in arg for arg in command))

    def test_check_records_recipe_and_private_evidence_after_preflight(self):
        settings = self.home / "settings.json"
        settings.write_text('{"frame":180}')
        preview = self.launch("check", recipe="smoke", settings=str(settings), timeout=30, dry_run=True)
        self.assertEqual(preview["argv"][2:], ["agent", "visuals", "check", "smoke", "--repository",
            str(self.project), "--timeout", "30", "--json", "--settings", str(settings)])
        self.probe.assert_not_called()
        self.assertFalse((self.home / ".local").exists())
        result = self.launch("check", recipe="smoke", settings=str(settings), timeout=30)
        self.assertEqual(result["recipe"], "smoke")
        self.assertEqual(result["check_report"], str(Path(result["directory"]) / "check" / "check.json"))
        self.assertEqual(self.record(result)["argv"][-2:], ["--output", result["evidence"]])
        self.assertFalse(Path(result["evidence"]).exists())  # Child creates it when executed.

    def test_recipe_preflight_rejects_unknown_missing_tools_and_directory_escape(self):
        for issue in ("unknown", "missing", "escape"):
            with self.subTest(issue=issue):
                self.manifest["recipes"]["smoke"]["missing_tools"] = ["unavailable"] if issue == "missing" else []
                self.manifest["recipes"]["smoke"]["directory"] = ".." if issue == "escape" else "."
                with self.assertRaises(ValueError):
                    self.launch("check", recipe="unknown" if issue == "unknown" else "smoke")
        self.probe.assert_not_called()
        self.assertFalse((self.home / ".local").exists())

    def test_invalid_settings_deadlines_and_extra_args_fail_before_recipe_discovery(self):
        settings = self.home / "settings.json"
        settings.write_text('{"exposure":NaN}')
        for kwargs in ({"timeout": 0}, {"timeout": 3601}, {"timeout": True},
                       {"settings": str(settings)}, {"argv": ["ignored"]}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.launch("check", recipe="smoke", **kwargs)
        self.inspect.assert_not_called()
        self.probe.assert_not_called()

    def test_invalid_saved_recipe_identity_is_rejected(self):
        result = self.launch("check", recipe="smoke")
        directory = Path(result["directory"])
        record = self.record(result)
        for recipe in (None, "--option", "bad\nname"):
            with self.subTest(recipe=recipe):
                record["recipe"] = recipe
                write_json_atomic(str(directory / "task.json"), record)
                with self.assertRaises(ValueError):
                    development.load_task(directory)

    def test_cli_node_options_before_or_after_script_preserve_arguments(self):
        parser = argparse.ArgumentParser()
        development.add_development_parser(parser.add_subparsers(dest="command"))
        literal = ["--json", "$HOME", "%n", "two words", ""]
        for before in (True, False):
            opts = ["--project", str(self.project), "--dry-run", "--manager", "pnpm"]
            args = parser.parse_args(["develop", "node", *(opts + ["dev:electron"] if before else ["dev:electron", *opts]), "--", *literal])
            self.assertEqual(args.script, "dev:electron")
            self.assertEqual(args.argv, literal)
            self.assertFalse(args.json)

    def test_recipe_git_timeout_returns_cli_failure_without_launching(self):
        parser = argparse.ArgumentParser()
        development.add_development_parser(parser.add_subparsers(dest="command"))
        args = parser.parse_args(["develop", "check", "smoke", "--project", str(self.project), "--json"])
        args.native = True
        self.inspect.side_effect = subprocess.TimeoutExpired("git", 60)
        with redirect_stdout(output := StringIO()):
            self.assertEqual(development.run_development_command(args), 1)
        self.assertFalse(json.loads(output.getvalue())["ok"])
        self.probe.assert_not_called()
        self.assertFalse((self.home / ".local").exists())


if __name__ == "__main__":
    unittest.main()
