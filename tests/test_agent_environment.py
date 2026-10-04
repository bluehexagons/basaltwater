"""Tests for read-only project environment discovery."""

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

from lib import agent_cli, agent_environment, agent_workspace


class TestAgentEnvironment(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.file = Path(self.directory, agent_environment.PROJECT_FILE)
        self.git = self.enterContext(patch.object(
            agent_workspace, "_git", return_value=subprocess.CompletedProcess([], 0, "", ""),
        ))

    def declaration(self, **fields) -> None:
        self.file.write_text(json.dumps({"version": 1, **fields}), encoding="utf-8")

    def test_missing_declarations_do_not_invent_deployments(self) -> None:
        with (
            patch.object(agent_workspace, "_repository_root", return_value=self.directory),
            patch.object(agent_workspace, "_effective_home", return_value=self.directory),
            patch.object(agent_workspace, "_worktree_record", return_value={"branch": "dev", "head": "a" * 40, "dirty": False}),
            patch.object(agent_environment.shutil, "which", return_value=None),
            patch.object(agent_environment.subprocess, "run") as execute,
        ):
            result = agent_environment.inspect_environment(self.directory)
        self.assertIsNone(result["current_deployment"])
        self.assertEqual(result["undeclared_branches"], ["dev", "staging"])
        self.assertEqual(result["deployments"], {})
        self.assertIsNone(result["tools"]["yarn"])
        self.assertEqual(result["desktop_applications"], {})
        self.assertEqual(result["desktop_skills"], [])
        execute.assert_not_called()

    def test_desktop_discovery_provides_workflows_without_claiming_readiness(self) -> None:
        skill = Path(self.directory, ".agents/skills/basaltwater-desktop/SKILL.md")
        skill.parent.mkdir(parents=True)
        skill.write_text("Desktop guidance")
        with (
            patch.object(agent_workspace, "_repository_root", return_value=self.directory),
            patch.object(agent_workspace, "_effective_home", return_value=self.directory),
            patch.object(agent_workspace, "_worktree_record", return_value={"branch": "main", "head": "a" * 40, "dirty": False}),
            patch.object(agent_environment.shutil, "which", side_effect=lambda name: "/bin/" + name if name in ("blender", "krita") else None),
            patch.object(agent_environment.subprocess, "run") as execute,
        ):
            result = agent_environment.inspect_environment(self.directory)
        self.assertEqual(set(result["desktop_applications"]), {"blender", "krita"})
        blender = result["desktop_applications"]["blender"]
        self.assertEqual(blender["executable"], "/bin/blender")
        self.assertEqual(blender["readiness"], "unverified")
        self.assertIn("background rendering", blender["workflows"])
        self.assertTrue(any("--background" in instruction for instruction in blender["instructions"]))
        self.assertEqual(result["desktop_skills"], [str(skill)])
        execute.assert_not_called()

    def test_summary_includes_application_specific_instructions(self) -> None:
        result = {
            "tools": {"blender": "/bin/blender"},
            "desktop_applications": {"blender": {**agent_environment.DESKTOP_APPLICATIONS["blender"], "readiness": "unverified"}},
            "desktop_skills": [],
            "workspace": {"repository": self.directory, "branch": "main", "commit": "a" * 40, "worktree_root": self.directory, "browser_evidence": self.directory, "artifact_directories": []},
            "deployments": {}, "undeclared_branches": ["dev", "staging"],
            "required_tools": {}, "recipes": {},
            "health_command": "basaltw agent doctor --all-capabilities --json",
        }
        output = StringIO()
        with patch.object(agent_environment, "inspect_environment", return_value=result), redirect_stdout(output):
            status = agent_environment.run_manifest_command(argparse.Namespace(repository=self.directory, json=False))
        self.assertEqual(status, 0)
        self.assertIn("Desktop: blender", output.getvalue())
        self.assertIn("--render-frame 1", output.getvalue())
        self.assertIn("readiness unverified", output.getvalue())

    def test_explicit_mappings_artifacts_and_current_branch(self) -> None:
        mapping = {"environment": "preproduction", "provider": "aws", "region": "us-east-1", "url": "https://staging.example.com/"}
        self.declaration(deployments={"staging": mapping}, artifact_directories=[".artifacts", ".artifacts"])
        with (
            patch.object(agent_workspace, "_repository_root", return_value=self.directory),
            patch.object(agent_workspace, "_effective_home", return_value=self.directory),
            patch.object(agent_workspace, "_worktree_record", return_value={"branch": "staging", "head": "b" * 40, "dirty": True}),
            patch.object(agent_environment.shutil, "which", side_effect=lambda name: "/bin/git" if name == "git" else None),
        ):
            result = agent_environment.inspect_environment(self.directory)
        self.assertEqual(result["current_deployment"], mapping)
        self.assertEqual(result["undeclared_branches"], ["dev"])
        self.assertEqual(result["project_source"], str(self.file))
        self.assertTrue(result["workspace"]["dirty"])
        self.assertEqual(result["workspace"]["artifact_directories"], [{"path": ".artifacts", "ignored": True}])
        self.assertEqual(result["tools"]["git"], "/bin/git")

    def test_project_requirements_and_recipes_are_discovered_without_execution(self) -> None:
        self.declaration(
            required_tools=["blender", "scene_tool", "blender"],
            recipes={"render": {
                "description": "Render the shared validation scene",
                "argv": ["blender", "--background", "scene with spaces.blend", "--render-frame", "1"],
                "requires": ["blender", "scene_tool"],
            }},
        )
        with (
            patch.object(agent_workspace, "_repository_root", return_value=self.directory),
            patch.object(agent_workspace, "_effective_home", return_value=self.directory),
            patch.object(agent_workspace, "_worktree_record", return_value={"branch": "main", "head": "a" * 40, "dirty": False}),
            patch.object(agent_environment.shutil, "which", side_effect=lambda name: "/bin/blender" if name == "blender" else None),
            patch.object(agent_environment.subprocess, "run") as execute,
            redirect_stdout(output := StringIO()),
        ):
            result = agent_environment.inspect_environment(self.directory)
            self.assertEqual(agent_environment.run_manifest_command(argparse.Namespace(repository=self.directory, json=False)), 0)
        self.assertEqual(result["required_tools"]["blender"], {"executable": "/bin/blender", "status": "available"})
        self.assertEqual(result["required_tools"]["scene_tool"], {"executable": None, "status": "missing"})
        self.assertEqual(result["recipes"]["render"]["missing_tools"], ["scene_tool"])
        self.assertEqual(result["recipes"]["render"]["directory"], ".")
        self.assertIn("Required tool: scene_tool (missing)", output.getvalue())
        self.assertIn("'scene with spaces.blend'", output.getvalue())
        execute.assert_not_called()

    def test_invalid_requirements_and_recipes_are_rejected(self) -> None:
        recipe = {"description": "Run tests", "argv": ["python3", "tests.py"]}
        for fields in (
            {"required_tools": "blender"}, {"required_tools": ["../bin/blender"]},
            {"required_tools": ["blender\nother"]}, {"required_tools": ["tool --flag"]},
            {"required_tools": [None]}, {"required_tools": ["tool"] * 101},
            {"recipes": []}, {"recipes": {"test": {"argv": ["python3"]}}},
            {"recipes": {"test": {**recipe, "argv": []}}},
            {"recipes": {"test": {**recipe, "argv": ["python3", "bad\nargument"]}}},
            {"recipes": {"test": {**recipe, "environment": {"TOKEN": "secret"}}}},
            {"recipes": {"test": {**recipe, "directory": "../outside"}}},
            {"recipes": {"test": {**recipe, "directory": "/tmp"}}},
            {"recipes": {"test": {**recipe, "requires": ["/bin/python3"]}}},
        ):
            with self.subTest(fields=fields):
                self.declaration(**fields)
                with self.assertRaises(ValueError):
                    agent_environment.load_project_environment(self.directory)

    def test_recipe_directory_cannot_escape_through_a_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as outside:
            Path(self.directory, "outside").symlink_to(outside, target_is_directory=True)
            self.declaration(recipes={"test": {
                "description": "Run tests", "argv": ["python3", "tests.py"], "directory": "outside",
            }})
            with self.assertRaisesRegex(ValueError, "remain below"):
                agent_environment.load_project_environment(self.directory)

    def test_invalid_schema_and_mapping_are_rejected(self) -> None:
        for data in (
            {"version": True}, {"version": 2}, {"version": 1, "secrets": {}},
            {"version": 1, "deployments": []},
            {"version": 1, "deployments": {"dev": {"provider": "aws"}}},
            {"version": 1, "deployments": {"dev": {"environment": "dev", "token": "secret"}}},
            {"version": 1, "deployments": {"--unsafe": {"environment": "dev"}}},
        ):
            with self.subTest(data=data):
                self.file.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(ValueError):
                    agent_environment.load_project_environment(self.directory)

    def test_git_rejects_invalid_branch(self) -> None:
        self.declaration(deployments={"dev..other": {"environment": "dev"}})
        self.git.return_value.returncode = 1
        with self.assertRaisesRegex(ValueError, "branch"):
            agent_environment.load_project_environment(self.directory)

    def test_urls_cannot_include_authentication_or_untrusted_schemes(self) -> None:
        for url in ("http://dev.example.com", "https://u:p@dev.example.com", "https://dev.example.com/?token=secret", "https://dev.example.com/#token", "https://dev.example.com:bad"):
            with self.subTest(url=url):
                self.declaration(deployments={"dev": {"environment": "dev", "url": url}})
                with self.assertRaises(ValueError):
                    agent_environment.load_project_environment(self.directory)

    def test_artifacts_must_be_relative_bounded_paths(self) -> None:
        for path in ("/tmp", "../other", "a/../../other", ".", "a\nother"):
            with self.subTest(path=path):
                self.declaration(artifact_directories=[path])
                with self.assertRaises(ValueError):
                    agent_environment.load_project_environment(self.directory)

    def test_unsafe_or_oversized_declaration_is_not_read(self) -> None:
        self.file.write_bytes(b" " * (64 * 1024 + 1))
        with self.assertRaises(ValueError):
            agent_environment.load_project_environment(self.directory)
        self.file.unlink()
        target = Path(self.directory, "other.json")
        target.write_text('{"version":1}')
        self.file.symlink_to(target)
        with self.assertRaises(OSError):
            agent_environment.load_project_environment(self.directory)
        self.file.unlink()
        os.mkfifo(self.file)
        with self.assertRaises(ValueError):
            agent_environment.load_project_environment(self.directory)

    def test_command_dispatches_and_emits_machine_readable_errors(self) -> None:
        parser = argparse.ArgumentParser()
        agent_cli.add_agent_subparser(parser.add_subparsers(dest="command"))
        args = parser.parse_args(["agent", "manifest", self.directory, "--json"])
        output = StringIO()
        with patch.object(agent_environment, "inspect_environment", side_effect=ValueError("invalid declaration")), redirect_stdout(output):
            self.assertEqual(agent_cli.run_agent_command(args), 1)
        self.assertEqual(json.loads(output.getvalue()), {"ok": False, "error": "invalid declaration"})


if __name__ == "__main__":
    unittest.main()
