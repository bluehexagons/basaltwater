"""Provider account and guidance boundaries for a fresh unattended Codex process."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from lib import agent_tasks as tasks


class TaskContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = self.enterContext(tempfile.TemporaryDirectory())
        self.values = {"title": "Review", "prompt": "Inspect project guidance.", "directory": self.home,
                       "mode": "inspect", "interval": "once"}

    def test_supported_disabled_thinking_survives_model_metadata_and_command_building(self) -> None:
        cache = Path(self.home) / ".codex/models_cache.json"
        cache.parent.mkdir()
        cache.write_text(json.dumps({"models": [{"slug": "test-luna", "visibility": "list",
                                                "supported_reasoning_levels": [{"effort": "none"}, {"effort": "low"}]}]}))
        self.assertEqual(tasks.codex_models(self.home)[0]["efforts"], ["none", "low"])
        task = tasks.validate_task({**self.values, "model": "test-luna", "effort": "none"}, self.home)
        tasks._validate_model_effort(task, self.home)
        self.assertIn('model_reasoning_effort="none"', tasks.codex_command(task, "/test/codex"))
        with self.assertRaises(ValueError):
            tasks._validate_model_effort({**task, "effort": "high"}, self.home)

    def test_caller_home_and_thread_state_do_not_redirect_the_panel_provider(self) -> None:
        process, selector = MagicMock(), MagicMock()
        process.__enter__.return_value = process
        process.pid, process.wait.return_value = 1234, 0
        selector.__enter__.return_value = selector
        selector.get_map.return_value = {}
        with (
            patch.dict(os.environ, {"HOME": "/other", "CODEX_HOME": "/other/.codex", "CODEX_THREAD_ID": "thread",
                                    "CODEX_TURN_ID": "turn", "BASALTWATER_T3_LOGINCTL_SHIM": "/other/shim"}),
            patch.object(tasks.os, "geteuid", return_value=1000),
            patch.object(tasks, "_tool_path", return_value="/test/codex"),
            patch.object(tasks.subprocess, "Popen", return_value=process) as spawn,
            patch.object(tasks.selectors, "DefaultSelector", return_value=selector),
            patch.object(tasks.os, "killpg"),
        ):
            result = tasks.execute_prompt(self.values, self.home, threading.Event())
        self.assertEqual(result["status"], "completed")
        environment = spawn.call_args.kwargs["env"]
        self.assertEqual(environment["HOME"], self.home)
        self.assertEqual(environment["CODEX_HOME"], str(Path(self.home) / ".codex"))
        for key in ("CODEX_THREAD_ID", "CODEX_TURN_ID", "BASALTWATER_T3_LOGINCTL_SHIM"):
            self.assertNotIn(key, environment)
        self.assertEqual(spawn.call_args.kwargs["cwd"], self.home)
        instruction = process.stdin.write.call_args.args[0].decode()
        self.assertIn(str(Path(self.home) / ".agents/skills"), instruction)
        self.assertIn("honor disabled skills", instruction)
        self.assertIn("unattended context to workers", instruction)
        self.assertIn("no interactive reply", instruction)
        self.assertIn("does not inherit a T3 thread", instruction)
        self.assertTrue(instruction.endswith("Task prompt:\n" + self.values["prompt"]))
