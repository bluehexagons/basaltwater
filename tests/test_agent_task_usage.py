"""Conversation usage deltas, interrupted baselines and receipt retention races."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from lib import agent_tasks as tasks
from lib.agent_task_context import CodexEvents
from tests.test_agent_task_persistence import SESSION, report


class TaskUsageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = self.enterContext(tempfile.TemporaryDirectory())
        self.manager = tasks.AgentTasks(self.home)
        self.addCleanup(self.manager.close)
        self.enterContext(patch.object(tasks.AgentTasks, "available", return_value=True))
        self.enterContext(patch.object(tasks, "_tool_path", return_value="/test/codex"))
        self.values = {"title": "Review", "prompt": "Review the checks", "directory": self.home,
                       "mode": "inspect", "interval": "hourly", "persistence": "conversation", "token_budget": 100}
        self.identifier = self.manager.create(self.values, run_now=True, now=0)

    def assert_blocked_without_provider(self, *, now: float) -> None:
        provider_os = SimpleNamespace(**vars(os))
        with (patch.object(tasks, "os", provider_os), patch.object(provider_os, "geteuid", return_value=1000),
              patch.object(tasks.subprocess, "Popen") as spawn):
            self.assertTrue(self.manager.tick(now=now))
        spawn.assert_not_called()
        self.assertIn("baseline unavailable", self.manager.snapshot()["runs"][-1]["message"])

    def execute(self, total: dict, *, now: float = 1, session: str = SESSION) -> None:
        payload = b"\n".join(json.dumps(item).encode() for item in (
            {"type": "thread.started", "thread_id": session},
            {"type": "turn.started"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(report())}},
            {"type": "turn.completed", "usage": total},
        )) + b"\n"
        process, selector = MagicMock(), MagicMock()
        process.__enter__.return_value = process
        process.pid, process.wait.return_value = 1234, 0
        selector.__enter__.return_value = selector
        key = SimpleNamespace(fd=99, fileobj=process.stdout)
        selector.get_map.side_effect = [{99: key}, {}]
        selector.select.return_value = [(key, 1)]
        provider_os = SimpleNamespace(**vars(os))
        with (patch.object(tasks, "os", provider_os), patch.object(provider_os, "geteuid", return_value=1000),
              patch.object(tasks.subprocess, "Popen", return_value=process),
              patch.object(tasks.selectors, "DefaultSelector", return_value=selector),
              patch.object(provider_os, "read", return_value=payload), patch.object(provider_os, "killpg")):
            self.assertTrue(self.manager.tick(now=now))

    def test_resume_charges_only_new_tokens_and_retains_raw_totals(self) -> None:
        self.execute({"input_tokens": 15, "cached_input_tokens": 10, "output_tokens": 5})
        self.manager.action(self.identifier, "run")
        self.execute({"input_tokens": 19, "cached_input_tokens": 12, "output_tokens": 7}, now=2)
        state = self.manager.snapshot()
        self.assertEqual(state["tasks"][0]["tokens_used"], 26)
        self.assertTrue(state["tasks"][0]["enabled"])
        self.assertEqual(state["runs"][1]["usage"], {"input_tokens": 4, "output_tokens": 2})
        self.assertEqual(state["runs"][1]["session_usage"], {"input_tokens": 19, "output_tokens": 7})
        context = self.manager.context_snapshot(self.identifier)
        self.assertEqual(context["session_usage"], state["runs"][1]["session_usage"])
        receipt = self.manager.contexts.receipt(self.identifier, state["runs"][1]["id"])
        self.assertEqual(receipt["usage"], {"input_tokens": 4, "output_tokens": 2})

    def test_legacy_unknown_baseline_stops_before_provider_launch(self) -> None:
        self.manager.contexts.prepare(self.manager.snapshot()["tasks"][0], "a" * 32)
        context = self.manager.contexts.load(self.identifier)
        context["session_id"] = SESSION
        self.manager.contexts.save(self.identifier, context)
        self.assert_blocked_without_provider(now=1)
        self.assertFalse(self.manager.snapshot()["tasks"][0]["enabled"])
        self.assertIn("usage unavailable", self.manager.snapshot()["tasks"][0]["stop_reason"])

    def test_decreasing_resumed_total_never_produces_negative_charge_or_another_launch(self) -> None:
        self.execute({"input_tokens": 15, "output_tokens": 5})
        self.manager.action(self.identifier, "run")
        self.execute({"input_tokens": 14, "output_tokens": 7}, now=2)
        state = self.manager.snapshot()
        self.assertEqual(state["tasks"][0]["tokens_used"], 20)
        self.assertFalse(state["tasks"][0]["enabled"])
        self.assertEqual(state["runs"][1]["status"], "failed")
        self.assertIsNone(state["runs"][1]["usage"])
        self.assertIsNone(self.manager.context_snapshot(self.identifier)["session_usage"])
        self.manager.action(self.identifier, "run")
        self.assert_blocked_without_provider(now=3)

    def test_checkpoint_write_failure_cannot_reuse_a_stale_already_charged_baseline(self) -> None:
        self.execute({"input_tokens": 15, "output_tokens": 5})
        context = self.manager.contexts.load(self.identifier)
        save = self.manager.contexts.save

        def fail_checkpoint(identifier: str, value: dict) -> None:
            if value.get("last_run_id") != context["last_run_id"]:
                raise OSError("Simulated checkpoint write failure")
            save(identifier, value)

        self.manager.action(self.identifier, "run")
        with patch.object(self.manager.contexts, "save", side_effect=fail_checkpoint):
            self.execute({"input_tokens": 19, "output_tokens": 7}, now=2)
        state = self.manager.snapshot()
        self.assertEqual(state["tasks"][0]["tokens_used"], 26)
        self.assertFalse(self.manager.context_snapshot(self.identifier)["usage_baseline_valid"])
        self.assertTrue(state["runs"][1]["storage_error"])
        self.manager.action(self.identifier, "run")
        self.assert_blocked_without_provider(now=3)
        self.assertEqual(self.manager.snapshot()["tasks"][0]["tokens_used"], 26)

    def test_interrupted_claim_invalidates_baseline_before_resume(self) -> None:
        self.execute({"input_tokens": 15, "output_tokens": 5})
        self.manager.action(self.identifier, "run")
        with patch.object(tasks, "execute_prompt", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            self.manager.tick(now=2)
        self.manager.start(once=True)
        self.assertEqual(self.manager.error, "")
        self.assertIsNone(self.manager.context_snapshot(self.identifier)["session_usage"])
        self.assertEqual(self.manager.snapshot()["tasks"][0]["tokens_used"], 20)
        self.assertFalse(self.manager.snapshot()["tasks"][0]["enabled"])

    def test_reset_clears_baseline_without_resetting_consumption(self) -> None:
        self.execute({"input_tokens": 15, "output_tokens": 5})
        self.manager.action(self.identifier, "reset-context")
        self.assertNotIn("session_usage", self.manager.context_snapshot(self.identifier))
        self.assertEqual(self.manager.snapshot()["tasks"][0]["tokens_used"], 20)
        self.manager.action(self.identifier, "run")
        self.execute({"input_tokens": 10, "output_tokens": 2}, now=2,
                     session="abcdef01-2345-6789-abcd-ef0123456789")
        self.assertEqual(self.manager.snapshot()["tasks"][0]["tokens_used"], 32)

    def test_saved_final_receipt_recovers_charge_and_baseline_once(self) -> None:
        write, count = tasks.write_text_atomic, 0

        def fail_final_write(*args: object, **kwargs: object) -> None:
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("Simulated final state write failure")
            write(*args, **kwargs)

        with patch.object(tasks, "write_text_atomic", side_effect=fail_final_write), self.assertRaises(OSError):
            self.execute({"input_tokens": 15, "output_tokens": 5})
        self.manager.start(once=True)
        self.assertEqual(self.manager.error, "")
        self.assertEqual(self.manager.snapshot()["tasks"][0]["tokens_used"], 20)
        self.manager.action(self.identifier, "run")
        self.execute({"input_tokens": 19, "output_tokens": 7}, now=2)
        self.assertEqual(self.manager.snapshot()["tasks"][0]["tokens_used"], 26)

    def test_invalid_baseline_is_preserved_and_rejected(self) -> None:
        self.execute({"input_tokens": 15, "output_tokens": 5})
        path = self.manager.contexts.directory(self.identifier) / "context.json"
        value = json.loads(path.read_text())
        value["session_usage"] = {"input_tokens": True, "output_tokens": 5}
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(RuntimeError, "usage baseline"):
            self.manager.context_snapshot(self.identifier)
        self.assertEqual(json.loads(path.read_text()), value)

    def test_history_tolerates_retention_removing_a_record_before_sorting(self) -> None:
        self.execute({"input_tokens": 15, "output_tokens": 5})
        old = self.manager.contexts.directory(self.identifier) / "runs" / (self.manager.snapshot()["runs"][0]["id"] + ".json")
        self.manager.action(self.identifier, "run")
        self.execute({"input_tokens": 19, "output_tokens": 7}, now=2)
        original = Path.lstat

        def remove_old(path: Path, *args: object, **kwargs: object) -> object:
            if path == old:
                old.unlink()
                raise FileNotFoundError(str(path))
            return original(path, *args, **kwargs)

        with patch.object(Path, "lstat", autospec=True, side_effect=remove_old):
            history = self.manager.contexts.history(self.identifier)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["usage"], {"input_tokens": 4, "output_tokens": 2})

    def test_multiple_completion_events_use_latest_total_and_decreases_invalidate_usage(self) -> None:
        events = CodexEvents()
        for total in ({"input_tokens": 15, "output_tokens": 5}, {"input_tokens": 19, "output_tokens": 7}):
            events.feed(json.dumps({"type": "turn.completed", "usage": total}).encode() + b"\n")
        self.assertEqual(events.usage, {"input_tokens": 19, "output_tokens": 7})
        events.feed(b'{"type":"turn.completed","usage":{"input_tokens":18,"output_tokens":8}}\n')
        self.assertTrue(events.usage_incomplete)
        self.assertIsNone(events.usage)


if __name__ == "__main__":
    unittest.main()
