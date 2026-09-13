"""Godot and static publisher deadlines preserve active snapshots."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from pathlib import Path
import os
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common.service_tools import godot_web_publish as godot
from common.service_tools import static_web_publish as static
from common.service_tools import infra_web
from lib.remote_utils import CommandTimeoutError


class TestPublisherDeadlines(unittest.TestCase):
    def test_gateway_forwards_godot_deadline(self):
        with patch.object(godot, "main", return_value=0) as publish:
            self.assertEqual(infra_web.main(["publish", "godot", "demo", "--build-timeout", "7"]), 0)
            self.assertIn("--build-timeout", publish.call_args.args[0])
            self.assertEqual(publish.call_args.args[0][-2:], ["--build-timeout", "7"])

    def test_timeout_preserves_active_publication_and_cleans_staging(self):
        for module, root_name, project_file, operation in (
            (godot, "GAMES_ROOT", "project.godot", godot._publish),
            (static, "SITES_ROOT", "package.json", static.publish),
        ):
            with self.subTest(module=module.__name__), ExitStack() as stack:
                root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
                project = root / "project"
                project.mkdir()
                (project / project_file).write_text('{"scripts":{"build":"build"}}')
                published = root / "published"
                destination = published / "agent" / "demo"
                destination.mkdir(parents=True)
                (destination / "index.html").write_text("previous")
                stack.enter_context(patch.object(module, root_name, str(published)))
                stack.enter_context(patch.object(module, "_current_account", return_value=SimpleNamespace(pw_name="agent", pw_uid=os.getuid())))
                command = stack.enter_context(patch.object(module, "run", side_effect=CommandTimeoutError("build", 7)))
                parser = godot._parser() if module is godot else argparse.ArgumentParser()
                if module is static:
                    static.add_publish_arguments(parser)
                args = parser.parse_args(["demo", "--project", str(project), "--build-timeout", "7"])
                with self.assertRaises(CommandTimeoutError):
                    operation(args)
                self.assertEqual(command.call_args.kwargs["timeout"], 7)
                self.assertEqual((destination / "index.html").read_text(), "previous")
                self.assertEqual(sorted(p.name for p in destination.parent.iterdir()), [".infra-tools-demo.lock", "demo"])

    def test_install_and_build_share_one_budget(self):
        with tempfile.TemporaryDirectory() as project, patch.object(static, "run", return_value=SimpleNamespace(returncode=0)) as run, patch.object(static.time, "monotonic", side_effect=[100, 104]):
            static._run_project_build(project, {"scripts": {"build": "build"}}, install=True, timeout=7)
            self.assertEqual([call.kwargs["timeout"] for call in run.call_args_list], [7, 3])

    def test_exhausted_install_budget_never_starts_build(self):
        with tempfile.TemporaryDirectory() as project, patch.object(static, "run", return_value=SimpleNamespace(returncode=0)) as run, patch.object(static.time, "monotonic", side_effect=[100, 108]):
            with self.assertRaises(CommandTimeoutError):
                static._run_project_build(project, {"scripts": {"build": "build"}}, install=True, timeout=7)
            run.assert_called_once()
