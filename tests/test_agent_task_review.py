"""Task lifecycle boundaries found during the persistent-task review."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from lib import agent_tasks as tasks
from lib import agent_task_context as contexts
from lib.agent_task_api import TaskAPI
from tests.test_agent_task_persistence import SESSION, report


class TaskReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = self.enterContext(tempfile.TemporaryDirectory())
        self.project = Path(self.home) / "project"
        self.project.mkdir()
        self.manager = tasks.AgentTasks(self.home)
        self.addCleanup(self.manager.close)
        self.enterContext(patch.object(tasks.AgentTasks, "available", return_value=True))
        self.enterContext(patch.object(tasks, "_tool_path", return_value="/test/codex"))
        self.values = {"title": "Review", "prompt": "Review the remaining checks", "directory": str(self.project),
                       "mode": "workspace", "interval": "hourly", "persistence": "checkpoint"}

    def execute(self, result: dict | None = None, *, now: float = 1) -> None:
        with patch.object(tasks, "execute_prompt", return_value={"status": "completed", "output": "", "message": "Done",
                          **report(), **(result or {})}):
            self.assertTrue(self.manager.tick(now=now))

    def leave_running_claim_after_receipt(self, *, input_tokens: int = 7, output_tokens: int = 3) -> str:
        write, count = tasks.write_text_atomic, 0

        def fail_hot_state(*args: object, **kwargs: object) -> None:
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("Simulated final hot-state write failure")
            write(*args, **kwargs)

        with patch.object(tasks, "write_text_atomic", side_effect=fail_hot_state):
            with self.assertRaisesRegex(OSError, "final hot-state"):
                self.execute({**report("complete"), "usage": {
                    "input_tokens": input_tokens, "output_tokens": output_tokens}})
        run = self.manager.snapshot()["runs"][0]
        self.assertEqual(run["status"], "running")
        return run["id"]

    def test_daily_limit_keeps_a_queued_one_time_invocation_until_next_day(self) -> None:
        identifier = self.manager.create({**self.values, "interval": "once", "daily_limit": 1}, run_now=True, now=0)
        self.execute()
        self.manager.action(identifier, "run")
        with patch.object(tasks, "execute_prompt") as execute:
            self.assertTrue(self.manager.tick(now=2))
            self.assertFalse(self.manager.tick(now=86399))
        execute.assert_not_called()
        saved = self.manager.snapshot()["tasks"][0]
        self.assertTrue(saved["queued"])
        self.assertFalse(saved["enabled"])
        self.assertEqual(saved["not_before"], 86400)
        self.execute(now=86400)
        self.assertEqual(self.manager.snapshot()["tasks"][0]["attempts"], 2)

    def test_idle_and_deferred_event_tasks_expire_without_provider_calls(self) -> None:
        for queued in (False, True):
            identifier = self.manager.create({**self.values, "interval": "event", "deadline_at": 10}, run_now=queued, now=0)
            with self.manager._transaction():
                self.manager._load()["tasks"][-1]["not_before"] = 86400
                self.manager._save()
        with patch.object(tasks, "execute_prompt") as execute:
            self.assertTrue(self.manager.tick(now=10))
            self.assertFalse(self.manager.tick(now=86400))
        execute.assert_not_called()
        for saved in self.manager.snapshot()["tasks"]:
            self.assertFalse(saved["enabled"])
            self.assertFalse(saved["queued"])
            self.assertEqual(saved["stop_reason"], "Task deadline reached")

    def test_failed_provider_cannot_change_goal_cadence_with_waiting_report(self) -> None:
        self.manager.create({**self.values, "continue_until_done": True, "completion_criteria": "Checks pass"}, run_now=True, now=0)
        with patch.object(tasks.time, "time", return_value=1):
            self.execute({**report("waiting"), "status": "failed", "retry_after_minutes": 1})
        self.assertEqual(self.manager.snapshot()["tasks"][0]["next_run"], 3600)

    def test_context_failure_preserves_receipt_and_pauses_without_more_paid_work(self) -> None:
        identifier = self.manager.create({**self.values, "failure_limit": 0}, run_now=True, now=0)
        real_save = self.manager.contexts.save
        def save(task_id: str, value: dict) -> None:
            if "last_run_id" in value:
                raise OSError("Simulated checkpoint write failure")
            real_save(task_id, value)
        with patch.object(self.manager.contexts, "save", side_effect=save):
            self.execute(report("complete"))
        saved = self.manager.snapshot()["tasks"][0]
        self.assertFalse(saved["enabled"])
        self.assertIn("storage failed", saved["stop_reason"])
        receipt = self.manager.contexts.history(identifier)[0]
        self.assertEqual(receipt["outcome"], "complete")
        with patch.object(tasks, "execute_prompt") as execute:
            self.assertFalse(self.manager.tick(now=86400))
        execute.assert_not_called()

    def test_repaired_resume_reconciles_receipt_checkpoint_once_and_keeps_failure_evidence(self) -> None:
        identifier = self.manager.create({**self.values, "failure_limit": 0, "token_budget": 100}, run_now=True, now=0)
        real_save = self.manager.contexts.save

        def fail_checkpoint(task_id: str, value: dict) -> None:
            if "last_run_id" in value:
                raise OSError("Simulated checkpoint write failure")
            real_save(task_id, value)

        with patch.object(self.manager.contexts, "save", side_effect=fail_checkpoint):
            self.execute({**report("complete"), "usage": {"input_tokens": 5, "output_tokens": 3}})
        state = self.manager.snapshot()
        failed_run = state["runs"][0]
        receipt = self.manager.contexts.receipt(identifier, failed_run["id"])
        self.assertEqual(failed_run["status"], "failed")
        self.assertTrue(failed_run["storage_error"])
        self.assertEqual(receipt["status"], "completed")
        self.assertEqual(self.manager.contexts.load(identifier)["checkpoint"], "")
        self.assertEqual(state["tasks"][0]["tokens_used"], 8)

        with (patch.object(self.manager.contexts, "save", side_effect=OSError("Still unavailable")),
              patch.object(tasks, "execute_prompt") as execute):
            with self.assertRaisesRegex(OSError, "Still unavailable"):
                self.manager.action(identifier, "resume", now=2)
            self.assertFalse(self.manager.tick(now=3))
            execute.assert_not_called()

        self.manager.action(identifier, "resume", now=4)
        self.assertEqual(self.manager.contexts.load(identifier)["checkpoint"], receipt["checkpoint"])
        self.assertEqual(self.manager.contexts.load(identifier)["last_run_id"], failed_run["id"])
        self.assertEqual(self.manager.snapshot()["tasks"][0]["tokens_used"], 8)
        self.assertEqual(self.manager.snapshot()["runs"][0]["status"], "failed")
        self.assertTrue(self.manager.snapshot()["runs"][0]["storage_error"])

        self.manager.action(identifier, "pause")
        with patch.object(self.manager.contexts, "record") as record:
            self.manager.action(identifier, "resume", now=5)
        record.assert_not_called()
        self.manager.action(identifier, "run")
        checkpoints = []

        def continue_from_checkpoint(task: dict, _home: str, _cancel: object) -> dict:
            checkpoints.append(task["_runtime"]["checkpoint"])
            return {"status": "completed", "output": "", "message": "Continued", **report(),
                    "usage": {"input_tokens": 0, "output_tokens": 0}}

        with patch.object(tasks, "execute_prompt", side_effect=continue_from_checkpoint):
            self.assertTrue(self.manager.tick(now=6))
        self.assertEqual(checkpoints, [receipt["checkpoint"]])
        self.assertEqual(self.manager.snapshot()["tasks"][0]["tokens_used"], 8)

    def test_storage_recovery_identity_survives_hot_run_eviction(self) -> None:
        target = self.manager.create({**self.values, "title": "Recover checkpoint", "failure_limit": 0},
                                     run_now=True, now=0)
        real_save = self.manager.contexts.save

        def fail_target_checkpoint(task_id: str, value: dict) -> None:
            if task_id == target and value.get("last_run_id"):
                raise OSError("Simulated checkpoint write failure")
            real_save(task_id, value)

        with patch.object(tasks, "MAX_RUNS", 1):
            with patch.object(self.manager.contexts, "save", side_effect=fail_target_checkpoint):
                self.execute(report("waiting"))
            failed_task = next(task for task in self.manager.snapshot()["tasks"] if task["id"] == target)
            failed_run_id = failed_task["last_run_id"]
            self.assertEqual(failed_task["context_recovery"], {
                "run_id": failed_run_id, "status": "pending", "accounting_pending": False})
            self.assertTrue(self.manager.snapshot()["runs"][0]["storage_error"])
            receipt = self.manager.contexts.receipt(target, failed_run_id)
            unrelated = self.manager.create({**self.values, "title": "Unrelated task"}, run_now=True, now=2)
            self.execute(report("waiting"), now=3)
            self.assertEqual([run["task"]["id"] for run in self.manager.snapshot()["runs"]], [unrelated])
            self.assertEqual(self.manager.contexts.receipt(target, failed_run_id)["checkpoint"], receipt["checkpoint"])

            self.manager.action(target, "resume", now=4)
            self.assertEqual(self.manager.contexts.load(target)["checkpoint"], receipt["checkpoint"])
            recovered_task = next(task for task in self.manager.snapshot()["tasks"] if task["id"] == target)
            self.assertEqual(recovered_task["context_recovery"], {
                "run_id": failed_run_id, "status": "reconciled", "accounting_pending": False})
            self.manager.action(target, "run")
            checkpoints = []

            def continue_from_checkpoint(task: dict, _home: str, _cancel: object) -> dict:
                checkpoints.append(task["_runtime"]["checkpoint"])
                return {"status": "completed", "output": "", "message": "Continued", **report()}

            with patch.object(tasks, "execute_prompt", side_effect=continue_from_checkpoint):
                self.assertTrue(self.manager.tick(now=5))
            self.assertEqual(checkpoints, [receipt["checkpoint"]])

    def test_missing_recovery_receipt_quarantines_one_task_without_stopping_worker(self) -> None:
        broken = self.manager.create({**self.values, "title": "Missing receipt", "failure_limit": 0},
                                     run_now=True, now=0)
        with patch.object(self.manager.contexts, "record", side_effect=OSError("Receipt publication failed")):
            self.execute(report("waiting"))
        failed = self.manager.snapshot()["tasks"][0]
        self.assertEqual(failed["context_recovery"]["status"], "pending")
        self.assertTrue(self.manager.snapshot()["runs"][0]["storage_error"])

        healthy = self.manager.create({**self.values, "title": "Healthy task"}, run_now=True, now=2)
        self.manager.start(once=True)
        self.assertEqual(self.manager.error, "")
        self.assertIsNotNone(self.manager._lease)
        with self.assertRaisesRegex(RuntimeError, "receipt is missing"):
            self.manager.action(broken, "run")

        invoked = []

        def execute(task: dict, _home: str, _cancel: object) -> dict:
            invoked.append(task["id"])
            return {"status": "completed", "output": "", "message": "Done", **report()}

        with patch.object(tasks, "execute_prompt", side_effect=execute):
            self.assertTrue(self.manager.tick(now=3))
        self.assertEqual(invoked, [healthy])
        saved = next(task for task in self.manager.snapshot()["tasks"] if task["id"] == broken)
        self.assertEqual(saved["context_recovery"]["status"], "pending")
        self.assertFalse(saved["enabled"])
        self.assertFalse(saved["queued"])

    def test_start_quarantines_receipt_read_error_and_recovers_usage_once(self) -> None:
        target = self.manager.create({**self.values, "title": "Receipt read error", "token_budget": 100},
                                     run_now=True, now=0)
        run_id = self.leave_running_claim_after_receipt()
        receipt = self.manager.contexts.receipt(target, run_id)
        healthy = self.manager.create({**self.values, "title": "Healthy after read error"}, run_now=True, now=2)

        with (patch.object(tasks, "private_json", side_effect=OSError("Simulated receipt read EIO")),
              patch.object(self.manager.contexts, "receipt", side_effect=OSError("Simulated receipt read EIO"))):
            self.manager.start(once=True)
        self.assertEqual(self.manager.error, "")
        self.assertIsNotNone(self.manager._lease)
        pending = next(task for task in self.manager.snapshot()["tasks"] if task["id"] == target)
        self.assertEqual(pending["context_recovery"], {
            "run_id": run_id, "status": "pending", "accounting_pending": True})
        self.assertEqual(pending["attempts"], 1)
        self.assertEqual(pending["tokens_used"], 0)
        self.assertTrue(next(run for run in self.manager.snapshot()["runs"] if run["id"] == run_id)["storage_error"])
        self.assertEqual(self.manager.contexts.receipt(target, run_id), receipt)

        invoked = []

        def execute(task: dict, _home: str, _cancel: object) -> dict:
            invoked.append(task["id"])
            return {"status": "completed", "output": "", "message": "Done", **report()}

        with patch.object(tasks, "execute_prompt", side_effect=execute):
            self.assertTrue(self.manager.tick(now=3))
        self.assertEqual(invoked, [healthy])

        self.manager.close()
        self.manager = tasks.AgentTasks(self.home)
        self.addCleanup(self.manager.close)
        self.manager.start(once=True)
        saved = next(task for task in self.manager.snapshot()["tasks"] if task["id"] == target)
        self.assertEqual(saved["tokens_used"], 10)
        self.assertEqual(saved["attempts"], 1)
        self.assertEqual(saved["context_recovery"]["status"], "reconciled")
        self.assertEqual(next(run for run in self.manager.snapshot()["runs"] if run["id"] == run_id)["status"], "failed")

        self.manager.close()
        self.manager = tasks.AgentTasks(self.home)
        self.addCleanup(self.manager.close)
        self.manager.start(once=True)
        self.assertEqual(next(task for task in self.manager.snapshot()["tasks"] if task["id"] == target)["tokens_used"], 10)

    def test_start_quarantines_receipt_replay_save_error_and_dispatches_healthy_task(self) -> None:
        target = self.manager.create({**self.values, "title": "Receipt replay save error", "token_budget": 100},
                                     run_now=True, now=0)
        run_id = self.leave_running_claim_after_receipt()
        receipt = self.manager.contexts.receipt(target, run_id)
        self.manager.contexts.reset(target)
        healthy = self.manager.create({**self.values, "title": "Healthy after replay error"}, run_now=True, now=2)

        with patch.object(self.manager.contexts, "save", side_effect=OSError("Simulated context save EIO")):
            self.manager.start(once=True)
        self.assertEqual(self.manager.error, "")
        self.assertIsNotNone(self.manager._lease)
        pending = next(task for task in self.manager.snapshot()["tasks"] if task["id"] == target)
        self.assertEqual(pending["context_recovery"], {
            "run_id": run_id, "status": "pending", "accounting_pending": False})
        self.assertEqual(pending["attempts"], 1)
        self.assertEqual(pending["tokens_used"], 10)
        self.assertEqual(self.manager.contexts.receipt(target, run_id), receipt)
        failed_run = next(run for run in self.manager.snapshot()["runs"] if run["id"] == run_id)
        self.assertEqual(failed_run["status"], "failed")
        self.assertTrue(failed_run["storage_error"])

        invoked = []

        def execute(task: dict, _home: str, _cancel: object) -> dict:
            invoked.append(task["id"])
            return {"status": "completed", "output": "", "message": "Done", **report()}

        with patch.object(tasks, "execute_prompt", side_effect=execute):
            self.assertTrue(self.manager.tick(now=3))
        self.assertEqual(invoked, [healthy])

        self.manager.close()
        self.manager = tasks.AgentTasks(self.home)
        self.addCleanup(self.manager.close)
        self.manager.start(once=True)
        self.assertEqual(next(task for task in self.manager.snapshot()["tasks"] if task["id"] == target)["tokens_used"], 10)
        self.manager.close()
        self.manager = tasks.AgentTasks(self.home)
        self.addCleanup(self.manager.close)
        self.manager.start(once=True)
        self.assertEqual(next(task for task in self.manager.snapshot()["tasks"] if task["id"] == target)["tokens_used"], 10)

    def test_unknown_usage_blocks_run_resume_and_partial_update_until_audited_repair(self) -> None:
        values = {**self.values, "continue_until_done": True, "completion_criteria": "Review is complete",
                  "max_runs": 0, "deadline_at": 0, "token_budget": 100}
        identifier = self.manager.create(values, run_now=True, now=0)
        self.execute({"usage": None})
        saved = self.manager.snapshot()["tasks"][0]
        self.assertTrue(saved["usage_uncertain"])
        self.assertEqual(saved["tokens_used"], 0)
        self.assertEqual(saved["max_runs"], 0)
        self.assertEqual(saved["deadline_at"], 0)
        receipt = self.manager.contexts.receipt(identifier, saved["last_run_id"])
        self.assertIsNone(receipt["usage"])

        for action in ("run", "resume"):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, "usage unavailable"):
                self.manager.action(identifier, action, now=2)
        self.manager.update(identifier, {"title": "Updated goal"}, partial=True)
        saved = self.manager.snapshot()["tasks"][0]
        self.assertTrue(saved["usage_uncertain"])
        self.assertFalse(saved["enabled"])
        self.assertFalse(saved["queued"])
        self.assertIn("usage unavailable", saved["stop_reason"])
        with patch.object(tasks, "execute_prompt") as execute:
            self.assertFalse(self.manager.tick(now=3))
        execute.assert_not_called()

    def test_known_prelaunch_cancellation_survives_partial_update_and_manual_run(self) -> None:
        identifier = self.manager.create({**self.values, "token_budget": 100}, run_now=True, now=0)
        cancellation = SimpleNamespace(is_set=lambda: True)
        with patch.object(self.manager, "_cancel_for_task", return_value=cancellation):
            self.assertTrue(self.manager.tick(now=1))
        cancelled = self.manager.snapshot()["runs"][0]
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(cancelled["message"], "Cancelled before workspace preparation")
        self.assertFalse(self.manager.snapshot()["tasks"][0]["usage_uncertain"])

        self.manager.update(identifier, {"title": "Renamed after cancellation"}, partial=True)
        self.assertFalse(self.manager.snapshot()["tasks"][0]["usage_uncertain"])
        self.manager.action(identifier, "run")
        with patch.object(tasks, "execute_prompt", return_value={
                "status": "completed", "output": "", "message": "Done", **report(),
                "usage": {"input_tokens": 2, "output_tokens": 1}}) as execute:
            self.assertTrue(self.manager.tick(now=2))
        execute.assert_called_once()
        self.assertFalse(self.manager.snapshot()["tasks"][0]["usage_uncertain"])

    def test_legacy_unknown_usage_is_recovered_after_stop_reason_was_cleared(self) -> None:
        values = {**self.values, "continue_until_done": True, "completion_criteria": "Review is complete",
                  "max_runs": 0, "deadline_at": 0, "token_budget": 100}
        identifier = self.manager.create(values, run_now=True, now=0)
        self.execute({"usage": None})
        state = json.loads(self.manager.path.read_text())
        state["version"] = 1
        state["tasks"][0].pop("usage_uncertain")
        state["tasks"][0].pop("usage_repairs")
        state["tasks"][0]["stop_reason"] = ""
        state["tasks"][0]["enabled"] = True
        state["tasks"][0]["queued"] = True
        self.manager.path.write_text(json.dumps(state))

        upgraded = self.manager.snapshot()["tasks"][0]
        self.assertTrue(upgraded["usage_uncertain"])
        self.assertFalse(upgraded["enabled"])
        self.assertFalse(upgraded["queued"])
        self.assertIn("usage unavailable", upgraded["stop_reason"])
        with self.assertRaisesRegex(ValueError, "usage unavailable"):
            self.manager.action(identifier, "run")

    def test_legacy_unknown_usage_is_found_before_a_later_known_run(self) -> None:
        values = {**self.values, "token_budget": 0, "history_limit": 10}
        identifier = self.manager.create(values, run_now=True, now=0)
        self.execute({"usage": None})
        self.manager.action(identifier, "run")
        self.execute({"usage": {"input_tokens": 5, "output_tokens": 2}}, now=2)
        self.assertTrue(self.manager.snapshot()["tasks"][0]["usage_uncertain"])
        state = json.loads(self.manager.path.read_text())
        state["version"] = 1
        task = state["tasks"][0]
        task["token_budget"] = 100
        task["stop_reason"] = ""
        task.pop("usage_uncertain")
        task.pop("usage_repairs")
        task.pop("context_recovery")
        self.manager.path.write_text(json.dumps(state))

        upgraded = tasks.AgentTasks(self.home)
        self.addCleanup(upgraded.close)
        saved = upgraded.snapshot()["tasks"][0]
        self.assertEqual(saved["tokens_used"], 7)
        self.assertTrue(saved["usage_uncertain"])
        self.assertFalse(saved["enabled"])
        with self.assertRaisesRegex(ValueError, "usage unavailable"):
            upgraded.action(identifier, "run")

    def test_legacy_usage_gap_after_history_eviction_is_uncertain(self) -> None:
        values = {**self.values, "token_budget": 0, "history_limit": 1}
        identifier = self.manager.create(values, run_now=True, now=0)
        with patch.object(tasks, "MAX_RUNS", 1):
            self.execute({"usage": None})
            self.manager.action(identifier, "run")
            self.execute({"usage": {"input_tokens": 5, "output_tokens": 2}}, now=2)
        self.assertEqual(len(self.manager.contexts.history(identifier, limit=100)), 1)
        state = json.loads(self.manager.path.read_text())
        state["version"] = 1
        task = state["tasks"][0]
        task["token_budget"] = 100
        task["stop_reason"] = ""
        task.pop("usage_uncertain")
        task.pop("usage_repairs")
        task.pop("context_recovery")
        self.manager.path.write_text(json.dumps(state))

        upgraded = tasks.AgentTasks(self.home)
        self.addCleanup(upgraded.close)
        saved = upgraded.snapshot()["tasks"][0]
        self.assertEqual(saved["attempts"], 2)
        self.assertEqual(saved["tokens_used"], 7)
        self.assertTrue(saved["usage_uncertain"])
        self.assertFalse(saved["enabled"])

    def test_legacy_upgrade_does_not_mark_a_known_running_receipt_uncertain(self) -> None:
        identifier = self.manager.create({**self.values, "token_budget": 100}, run_now=True, now=0)
        run_id = self.leave_running_claim_after_receipt()
        state = json.loads(self.manager.path.read_text())
        state["version"] = 1
        task = state["tasks"][0]
        task.pop("usage_uncertain")
        task.pop("usage_repairs")
        task.pop("context_recovery")
        self.manager.path.write_text(json.dumps(state))

        self.manager.start(once=True)
        saved = self.manager.snapshot()["tasks"][0]
        self.assertEqual(saved["last_run_id"], run_id)
        self.assertEqual(saved["tokens_used"], 10)
        self.assertFalse(saved["usage_uncertain"])

    def test_usage_repair_is_exact_audited_and_cannot_lower_known_lifetime_usage(self) -> None:
        values = {**self.values, "continue_until_done": True, "completion_criteria": "Review is complete",
                  "max_runs": 0, "deadline_at": 0, "token_budget": 100}
        identifier = self.manager.create(values, run_now=True, now=0)
        self.execute({"usage": {"input_tokens": 10, "output_tokens": 5}})
        self.manager.action(identifier, "run")
        self.execute({"usage": None}, now=2)
        api = TaskAPI(self.manager)
        with self.assertRaisesRegex(ValueError, "below the recorded total"):
            api.call("update", {"id": identifier, "task": {"usage_repair_tokens_used": 14}})
        self.assertEqual(self.manager.snapshot()["tasks"][0]["tokens_used"], 15)
        self.assertTrue(self.manager.snapshot()["tasks"][0]["usage_uncertain"])

        api.call("update", {"id": identifier, "task": {"usage_repair_tokens_used": 20}})
        repaired = self.manager.snapshot()["tasks"][0]
        self.assertFalse(repaired["usage_uncertain"])
        self.assertEqual(repaired["tokens_used"], 20)
        self.assertEqual(repaired["usage_repairs"], [{
            "run_id": repaired["last_run_id"], "previous_tokens_used": 15,
            "tokens_used": 20, "repaired_at": unittest.mock.ANY,
        }])
        self.manager.action(identifier, "run")
        self.execute({"usage": {"input_tokens": 2, "output_tokens": 1}}, now=3)
        self.assertEqual(self.manager.snapshot()["tasks"][0]["tokens_used"], 23)
        self.assertFalse(self.manager.snapshot()["tasks"][0]["usage_uncertain"])

    def test_request_key_deduplication_still_matches_normalized_create_settings(self) -> None:
        first = self.manager.create(self.values, run_now=False, request_key="same-settings", now=0)
        second = self.manager.create(self.values, run_now=False, request_key="same-settings", now=0)
        self.assertEqual(first, second)

    def test_replaced_source_is_not_silently_resolved_into_a_different_checkout(self) -> None:
        source, other = Path(self.home) / "source", Path(self.home) / "other"
        source.mkdir()
        other.mkdir()
        identifier = self.manager.create({**self.values, "directory": str(source)}, run_now=True, now=0)
        self.execute()
        source.rmdir()
        source.symlink_to(other, target_is_directory=True)
        self.assertEqual(self.manager.snapshot()["tasks"][0]["directory"], str(source))
        self.manager.action(identifier, "run")
        with patch.object(tasks, "execute_prompt") as execute:
            self.manager.tick(now=2)
        execute.assert_not_called()
        self.assertIn("moved or changed", self.manager.snapshot()["runs"][-1]["message"])

    def test_missing_managed_directory_is_not_recreated_as_an_empty_workspace(self) -> None:
        identifier = self.manager.create({**self.values, "workspace": "directory"}, run_now=True, now=0)
        self.execute()
        workspace = Path(self.manager.context_snapshot(identifier)["workspace"]["path"])
        workspace.rmdir()
        self.manager.action(identifier, "run")
        with patch.object(tasks, "execute_prompt") as execute:
            self.manager.tick(now=2)
        execute.assert_not_called()
        self.assertFalse(workspace.exists())
        self.assertIn("workspace is missing", self.manager.snapshot()["runs"][-1]["message"])

    def test_falsey_invalid_control_values_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.manager.create(self.values, run_now=0)
        identifier = self.manager.create({**self.values, "interval": "event"}, run_now=False)
        for key in (None, 0, False):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.manager.notify(identifier, "New evidence", key=key)

    def test_context_source_metadata_requires_an_absolute_path(self) -> None:
        identifier = self.manager.create(self.values, run_now=True, now=0)
        self.execute()
        context = self.manager.contexts.load(identifier)
        self.manager.contexts.save(identifier, {**context, "source_directory": "relative"})
        with self.assertRaisesRegex(RuntimeError, "source directory"):
            self.manager.contexts.load(identifier)

    def test_failed_persistence_update_does_not_erase_a_saved_checkpoint(self) -> None:
        identifier = self.manager.create(self.values, run_now=True, now=0)
        self.execute()
        checkpoint = self.manager.contexts.load(identifier)["checkpoint"]
        with patch.object(tasks, "write_text_atomic", side_effect=OSError("Full state disk")), self.assertRaises(OSError):
            self.manager.update(identifier, {"persistence": "fresh"}, partial=True)
        self.assertEqual(self.manager.contexts.load(identifier)["checkpoint"], checkpoint)
        self.assertEqual(self.manager.snapshot()["tasks"][0]["persistence"], "checkpoint")
        self.manager.update(identifier, {"persistence": "fresh"}, partial=True)
        self.assertEqual(self.manager.contexts.load(identifier)["checkpoint"], checkpoint)
        self.assertEqual(self.manager.context_snapshot(identifier)["checkpoint"], "")
        self.assertTrue(self.manager.context_snapshot(identifier)["reset_pending"])
        self.manager.action(identifier, "run")
        self.execute(now=2)
        self.assertEqual(self.manager.contexts.load(identifier)["checkpoint"], "")
        self.assertFalse(self.manager.context_snapshot(identifier)["reset_pending"])


class ProviderTurnReviewTests(unittest.TestCase):
    def feed(self, events: contexts.CodexEvents, value: dict) -> None:
        events.feed(json.dumps(value).encode() + b"\n")

    def test_new_turn_invalidates_an_earlier_completion_report(self) -> None:
        events = contexts.CodexEvents()
        self.feed(events, {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(report("complete"))}})
        self.feed(events, {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}})
        self.assertTrue(events.final_turn_completed)
        self.feed(events, {"type": "turn.started"})
        self.assertIsNone(events.report)
        self.assertFalse(events.final_turn_completed)

    def test_missing_turn_usage_never_becomes_a_partial_budget_total(self) -> None:
        events = contexts.CodexEvents()
        self.feed(events, {"type": "turn.completed"})
        self.feed(events, {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}})
        self.assertIsNone(events.usage)
        self.assertTrue(events.usage_incomplete)

    def test_changed_conversation_id_remains_a_protocol_failure_after_valid_report(self) -> None:
        events = contexts.CodexEvents()
        for session in (SESSION, "ffffffff-ffff-ffff-ffff-ffffffffffff"):
            self.feed(events, {"type": "thread.started", "thread_id": session})
        self.feed(events, {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(report())}})
        self.assertTrue(events.protocol_error)
        self.assertEqual(events.session_id, SESSION)

    def test_zero_exit_without_terminal_turn_is_a_failed_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            project = Path(home) / "project"
            project.mkdir()
            task = tasks.validate_task({"title": "Review", "prompt": "Review", "directory": str(project),
                                      "mode": "workspace", "interval": "once", "persistence": "conversation"}, home)
            task["id"] = "a" * 32
            runtime = contexts.TaskContextStore(home).prepare(task, "b" * 32)
            payload = b"\n".join(json.dumps(event).encode() for event in (
                {"type": "thread.started", "thread_id": SESSION},
                {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(report("complete"))}},
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
                  patch.object(tasks, "_tool_path", return_value="/test/codex"), patch.object(tasks.subprocess, "Popen", return_value=process),
                  patch.object(tasks.selectors, "DefaultSelector", return_value=selector), patch.object(provider_os, "read", return_value=payload),
                  patch.object(provider_os, "killpg")):
                result = tasks.execute_prompt({**task, "_runtime": runtime}, home, threading.Event())
            self.assertEqual(result["status"], "failed")
            self.assertNotIn("outcome", result)
            self.assertIsNone(result["usage"])


if __name__ == "__main__":
    unittest.main()
