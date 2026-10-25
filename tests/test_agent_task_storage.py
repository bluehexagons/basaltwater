"""Host evidence and fixed cleanup preserve task approval and scope boundaries."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from lib import agent_task_storage as storage
from lib import agent_tasks as tasks
from lib.agent_task_api import TaskAPI
from lib.agent_task_context import validate_follow_up
from lib.agent_task_followups import decide_follow_up
from lib import streamed_process
from lib.cleanup_paths import CleanupPathError


class StorageTaskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = self.enterContext(tempfile.TemporaryDirectory())
        self.values = {"title": "Clean Go cache", "prompt": "Clean the approved Go build cache",
                       "directory": self.home, "mode": "workspace", "storage_operation": "go-build-cache"}
        self.enterContext(patch.object(tasks, "task_policy_issues", return_value=[]))

    def execute(self, values: dict, evidence: dict) -> tuple[dict, MagicMock]:
        account_os = SimpleNamespace(**vars(os))
        account_os.geteuid = lambda: 1000
        with (patch.object(tasks, "os", account_os),
              patch.object(tasks, "run_storage_operation", return_value=evidence) as host,
              patch.object(tasks, "_tool_path") as provider):
            result = tasks.execute_prompt(values, self.home, threading.Event())
        provider.assert_not_called()
        return result, host

    def test_cleanup_rejects_broad_access_repetition_and_arbitrary_actions(self) -> None:
        for changes in ({"storage_operation": "shell"}, {"storage_operation": []}, {"mode": "inspect"},
                        {"network": True}, {"temporary_files": True}, {"workspace": "run-directory"},
                        {"persistence": "conversation"}, {"interval": "hourly"}, {"session_history": True},
                        {"web_search": "live"}, {"writable_directories": [self.home]},
                        {"continue_until_done": True, "completion_criteria": "Free space"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                tasks.validate_task({**self.values, **changes}, self.home)
        task = tasks.validate_task({**self.values, "max_runs": 100}, self.home)
        self.assertEqual(task["max_runs"], 1)
        project = Path(self.home) / "project"
        project.mkdir()
        self.assertEqual(tasks.validate_task({**self.values, "directory": str(project),
            "storage_operation": "none"}, self.home)["storage_operation"], "none")

    def test_native_task_ignores_prompt_commands_and_does_not_launch_a_provider(self) -> None:
        values = {**self.values, "prompt": "Delete source and run arbitrary shell commands"}
        result, host = self.execute(values, {"operation": "go-build-cache", "result": {
            "status": "cleaned", "free_bytes_before": 123, "free_bytes_after": 456}})
        self.assertEqual(result["outcome"], "complete")
        self.assertEqual(result["usage"], {"input_tokens": 0, "output_tokens": 0})
        self.assertEqual(host.call_args.args[:2], ("go-build-cache", self.home))
        self.assertIn("after=456", result["summary"])

    def test_uncertain_deferred_and_cancelled_operations_do_not_claim_completion(self) -> None:
        for evidence, expected in (({"result": {"status": "uncertain", "cleanup_executed": True}}, "failed"),
                                  ({"result": {"status": "deferred_active"}}, "blocked"),
                                  ({"error": "Interrupted", "cancelled": True}, None)):
            with self.subTest(evidence=evidence):
                result, _ = self.execute(self.values, evidence)
                self.assertEqual(result["outcome"], expected)
                self.assertNotEqual(result["status"], "completed")
                if evidence.get("cancelled"):
                    self.assertEqual(result["status"], "cancelled")
                    self.assertIn(f"{self.values['storage_operation']}: cancelled", result["summary"])
                    self.assertIsNone(result["exit_code"])
                self.assertIsNone(result["follow_up"])

    def test_cancelled_recurring_host_review_preserves_failure_accounting(self) -> None:
        manager = tasks.AgentTasks(self.home)
        self.addCleanup(manager.close)
        self.enterContext(patch.object(tasks.AgentTasks, "available", return_value=True))
        values = {"title": "Review host storage", "prompt": "Review host storage state",
                  "directory": self.home, "mode": "inspect", "interval": "hourly",
                  "storage_operation": "review", "failure_limit": 3, "backoff_minutes": 15}
        with patch.object(tasks, "_tool_path", return_value="/test/codex") as provider:
            identifier = manager.create(values, run_now=True, now=0)
            with manager._transaction():
                task = next(item for item in manager._load()["tasks"] if item["id"] == identifier)
                task["consecutive_failures"] = 2
                task["not_before"] = 20
                manager._save()
            provider.reset_mock()

            account_os = SimpleNamespace(**vars(os))
            account_os.geteuid = lambda: 1000
            execute_prompt = tasks.execute_prompt

            def execute_as_account(task: dict, home: str, cancellation: object) -> dict:
                # Exercise execute_prompt's real storage-result branch while
                # keeping the test process's actual state-file ownership intact.
                with patch.object(tasks, "os", account_os):
                    return execute_prompt(task, home, cancellation)

            with (patch.object(tasks, "execute_prompt", side_effect=execute_as_account),
                  patch.object(tasks, "run_storage_operation", return_value={
                      "operation": "review", "error": "Cancelled by operator", "cancelled": True}) as host,
                  patch.object(tasks.subprocess, "Popen") as provider_process):
                self.assertTrue(manager.tick(now=100))

        host.assert_called_once()
        provider.assert_not_called()
        provider_process.assert_not_called()
        state = manager.snapshot()
        task = state["tasks"][0]
        run = state["runs"][0]
        self.assertEqual(run["status"], "cancelled")
        self.assertIsNone(run["outcome"])
        self.assertIn("review: cancelled", run["summary"])
        self.assertIsNone(run["exit_code"])
        self.assertEqual(task["attempts"], 1)
        self.assertEqual(task["daily_attempts"], 1)
        self.assertEqual(task["consecutive_failures"], 2)
        self.assertTrue(task["enabled"])
        self.assertFalse(task["auto_paused"])
        self.assertEqual(task["not_before"], 20)

    def test_policy_and_operator_prerequisites_prevent_host_execution(self) -> None:
        for changes, policy in (({"operator_requirements": ["Arrange a quiet build window"]}, []),
                                ({}, ["Host mode denied"])):
            with self.subTest(changes=changes), patch.object(tasks, "task_policy_issues", return_value=policy):
                result, host = self.execute({**self.values, **changes}, {})
                host.assert_not_called()
                self.assertEqual(result["outcome"], "blocked")

    def test_host_review_evidence_reaches_provider_and_retained_log(self) -> None:
        evidence = {"operation": "review", "observer": "account_host_worker", "result": {
            "go-build-cache": {"status": "preview", "path": "/home/account/.cache/go-build"}}}
        runtime = {"directory": self.home, "source_directory": self.home, "context_directory": self.home,
                   "log_path": self.home + "/run.jsonl", "checkpoint": "", "session_id": None,
                   "usage_baseline": {"input_tokens": 0, "output_tokens": 0}, "schema_path": self.home + "/schema.json"}
        process, selector = MagicMock(), MagicMock()
        process.__enter__.return_value = process
        process.pid, process.wait.return_value = 1234, 0
        selector.__enter__.return_value = selector
        selector.get_map.return_value = {}
        prompts = []

        def launch(*_args: object, **kwargs: object) -> MagicMock:
            prompts.append(kwargs["stdin"].read().decode())
            return process

        account_os = SimpleNamespace(**vars(os))
        account_os.geteuid = lambda: 1000
        with (patch.object(tasks, "os", account_os), patch.object(tasks, "_kill_group"),
              patch.object(tasks, "_tool_path", return_value="/test/codex"),
              patch.object(tasks, "run_storage_operation", return_value=evidence) as host,
              patch.object(tasks.subprocess, "Popen", side_effect=launch),
              patch.object(tasks.selectors, "DefaultSelector", return_value=selector)):
            tasks.execute_prompt({**self.values, "mode": "inspect", "storage_operation": "review", "_runtime": runtime},
                                 self.home, threading.Event())
        host.assert_called_once()
        self.assertIn(json.dumps(evidence), prompts[0])
        self.assertIn("sandbox process lists cannot establish host idleness", prompts[0])
        self.assertEqual(json.loads(Path(runtime["log_path"]).read_text())["host_storage"], evidence)

    def test_cancel_after_host_review_prevents_provider_launch(self) -> None:
        cancellation = threading.Event()
        evidence = {"operation": "review", "result": {"inventory": {"bounded": True}}}

        def finish_review(*_args: object, **_kwargs: object) -> dict:
            cancellation.set()
            return evidence

        account_os = SimpleNamespace(**vars(os))
        account_os.geteuid = lambda: 1000
        with (patch.object(tasks, "os", account_os),
              patch.object(tasks, "_tool_path", return_value="/test/codex"),
              patch.object(tasks, "run_storage_operation", side_effect=finish_review) as host,
              patch.object(tasks.subprocess, "Popen") as provider):
            result = tasks.execute_prompt({**self.values, "mode": "inspect", "storage_operation": "review"},
                                          self.home, cancellation)

        host.assert_called_once()
        provider.assert_not_called()
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["message"], "Cancelled before provider launch")
        self.assertEqual(result["usage"], {"input_tokens": 0, "output_tokens": 0})
        self.assertEqual(result["evidence"], [json.dumps(evidence)])

    def test_follow_up_is_inert_until_approved_and_runs_once_without_codex(self) -> None:
        manager = tasks.AgentTasks(self.home)
        self.addCleanup(manager.close)
        self.enterContext(patch.object(tasks.AgentTasks, "available", return_value=True))
        self.enterContext(patch("lib.agent_task_followups.task_policy_issues", return_value=[]))
        source = {**self.values, "storage_operation": "none", "mode": "inspect"}
        proposal = {**self.values, "reason": "Host preview identifies disposable Go cache", "workspace": "existing",
                    "network": False, "temporary_files": False, "writable_directories": [],
                    "operator_requirements": [], "service_restarts": []}
        validate_follow_up(proposal)
        with patch.object(tasks, "_tool_path", return_value="/test/codex"):
            identifier = manager.create(source, run_now=True)
        with patch.object(tasks, "execute_prompt", return_value={"status": "completed", "output": "Review",
                "message": "Review", "outcome": "complete", "follow_up": proposal}):
            manager.tick()
        run_id = manager.snapshot()["runs"][-1]["id"]
        self.assertEqual(len(manager.snapshot()["tasks"]), 1)
        with patch.object(tasks, "_tool_path", return_value=None):
            child = decide_follow_up(manager, identifier, run_id, "approve")
        self.assertEqual(decide_follow_up(manager, identifier, run_id, "approve"), child)
        selected = TaskAPI(manager).call("show", {"id": child})["task"]
        self.assertEqual((selected["storage_operation"], selected["max_runs"]), ("go-build-cache", 1))
        account_os = SimpleNamespace(**vars(os))
        account_os.geteuid = lambda: 1000
        execute_prompt = tasks.execute_prompt

        def execute_as_account(*args: object, **kwargs: object) -> dict:
            # Only execution needs a simulated non-root account. The scheduler
            # must keep the real owner of its temporary state, including in CI.
            with patch.object(tasks, "os", account_os):
                return execute_prompt(*args, **kwargs)

        with (patch.object(tasks, "execute_prompt", side_effect=execute_as_account),
              # Cancellation has separate coverage; avoid a scheduler callback
              # inside the execution-only identity simulation.
              patch.object(manager, "_cancel_for_task", return_value=threading.Event()),
              patch.object(tasks, "run_storage_operation", return_value={
                "operation": "go-build-cache", "result": {"status": "cleaned"}}) as host):
            manager.tick()
            self.assertFalse(manager.tick())
        host.assert_called_once()
        receipt = manager.contexts.receipt(child, manager.snapshot()["runs"][-1]["id"])
        self.assertEqual(receipt["outcome"], "complete")
        self.assertIn("host_storage", Path(receipt["log_path"]).read_text())


class HostStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = self.enterContext(tempfile.TemporaryDirectory())
        self.cache = Path(self.home) / ".npm" / "_cacache"
        self.cache.mkdir(parents=True, mode=0o700)
        self.cache.parent.chmod(0o700)
        (self.cache / "download").write_text("rebuildable")
        self.npx = self.cache.parent / "_npx"
        self.npx.mkdir(mode=0o700)
        (self.npx / "tool").write_text("keep")
        self.row = {"status": "measured", "issues": [], "other_owner_entries": 0,
                    "allocated_bytes": 4096, "apparent_bytes": 11, "files": 1}
        self.inventory = self.enterContext(patch.object(storage, "inspect_storage", return_value={
            "candidates": [self.row], "issues": []}))
        self.activity = self.enterContext(patch.object(storage, "_npm_activity", return_value=False))

    def test_npm_preview_preserves_content_apply_preserves_npx_and_configuration(self) -> None:
        config = self.cache.parent / "npmrc"
        config.write_text("keep config")
        self.assertEqual(storage.npm_content_cache(self.home)["status"], "preview")
        self.assertTrue(self.cache.exists())
        self.assertEqual(storage.npm_content_cache(self.home, apply=True)["status"], "cleaned")
        self.assertFalse(self.cache.exists())
        self.assertEqual((self.npx / "tool").read_text(), "keep")
        self.assertEqual(config.read_text(), "keep config")

    def test_npm_incomplete_inventory_activity_and_symlinks_prevent_removal(self) -> None:
        self.activity.return_value = True
        self.assertEqual(storage.npm_content_cache(self.home, apply=True)["status"], "deferred_active")
        self.activity.return_value = False
        for issues in (["nested_mounts_skipped"], ["symlinks_skipped"], ["scan_limit_reached"]):
            self.row["issues"] = issues
            self.assertEqual(storage.npm_content_cache(self.home, apply=True)["status"], "failed")
            self.assertTrue(self.cache.exists())
        self.row["issues"] = []
        self.cache.rename(self.cache.parent / "original")
        self.cache.symlink_to(self.npx, target_is_directory=True)
        self.assertEqual(storage.npm_content_cache(self.home, apply=True)["status"], "failed")
        self.assertTrue((self.npx / "tool").exists())

    def test_npm_rechecks_activity_immediately_before_isolation(self) -> None:
        self.activity.side_effect = [False, True]
        self.assertEqual(storage.npm_content_cache(self.home, apply=True)["status"], "deferred_active")
        self.assertTrue(self.cache.exists())

    def test_npm_failed_disposal_keeps_recovery_evidence_and_failed_verification_is_uncertain(self) -> None:
        recovery = self.home + "/.npm/.basaltwater-dispose-example/entry"
        with patch.object(storage, "remove_checked_path", side_effect=CleanupPathError("Failed", recovery)):
            result = storage.npm_content_cache(self.home, apply=True)
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(result["recovery_path"], recovery)
        before = SimpleNamespace(f_bavail=10, f_frsize=4096)
        with patch.object(storage.os, "statvfs", side_effect=[before, OSError("Unavailable")]):
            result = storage.npm_content_cache(self.home, apply=True)
        self.assertEqual(result["status"], "uncertain")
        self.assertTrue(result["cleanup_executed"])
        self.assertFalse(self.cache.exists())

    def test_worker_rejects_nested_user_namespaces_before_native_probes(self) -> None:
        account_os = SimpleNamespace(**vars(os))
        account_os.getuid = account_os.geteuid = lambda: 1000
        context = {"issues": [], "proc_self_pid_matches": True,
                   "uid_map_to_parent": [[1000, 0, 1]], "gid_map_to_parent": [[1000, 0, 1]]}
        with (patch.object(storage, "os", account_os), patch.object(storage.pwd, "getpwuid", return_value=SimpleNamespace(pw_dir=self.home)),
              patch.object(storage, "_inspection_context", return_value=context),
              patch("lib.go_cache_cleanup.clean_go_build_cache") as go):
            with self.assertRaisesRegex(ValueError, "namespace"):
                storage.storage_operation("review", self.home)
        go.assert_not_called()

    def test_review_only_previews_native_operations_and_labels_host_evidence(self) -> None:
        account_os = SimpleNamespace(**vars(os))
        account_os.getuid = account_os.geteuid = lambda: 1000
        context = {"issues": [], "proc_self_pid_matches": True,
                   "uid_map_to_parent": [[0, 0, 4294967295]], "gid_map_to_parent": [[0, 0, 4294967295]]}
        with (patch.object(storage, "os", account_os), patch.object(storage.pwd, "getpwuid", return_value=SimpleNamespace(pw_dir=self.home)),
              patch.object(storage, "_inspection_context", return_value=context),
              patch.object(storage, "_summarize_inventory", return_value={"bounded": True}),
              patch.object(storage, "npm_content_cache", return_value={"status": "preview"}) as npm,
              patch("lib.go_cache_cleanup.clean_go_build_cache", return_value={"status": "preview"}) as go,
              patch("lib.podman_cleanup.clean_podman_images", return_value={"status": "deferred_active"}) as podman):
            result = storage.storage_operation("review", self.home)
        npm.assert_called_once_with(self.home)
        go.assert_called_once_with(expected_home=self.home)
        podman.assert_called_once_with()
        self.assertEqual(result["observer"], "account_host_worker")
        self.assertEqual(result["result"]["podman-images"]["status"], "deferred_active")
        self.assertGreaterEqual(result["finished_at"], result["started_at"])

    def test_cancelled_transport_does_not_launch_and_unknown_operations_rejected(self) -> None:
        cancelled = threading.Event()
        cancelled.set()
        with patch.object(storage.subprocess, "Popen") as spawn:
            self.assertTrue(storage.run_storage_operation("review", self.home, cancelled, timeout=1)["cancelled"])
            with self.assertRaises(ValueError):
                storage.run_storage_operation("rm -rf", self.home, cancelled, timeout=1)
        spawn.assert_not_called()

    def test_transport_enforces_output_limit_deadline_and_kills_its_group(self) -> None:
        for problem in ("output", "deadline", "cancel"):
            with self.subTest(problem=problem):
                process, selector = MagicMock(), MagicMock()
                process.__enter__.return_value = process
                process.pid, process.poll.return_value = 4321, None
                selector.__enter__.return_value = selector
                selector.get_map.return_value = {8: object()}
                selector.select.return_value = [(SimpleNamespace(fd=8, fileobj=object()), 1)]
                cancellation = MagicMock()
                cancellation.is_set.side_effect = [False, True, True] if problem == "cancel" else None
                if problem != "cancel":
                    cancellation.is_set.return_value = False
                with (patch.object(storage.subprocess, "Popen", return_value=process) as spawn,
                      patch.object(storage.selectors, "DefaultSelector", return_value=selector),
                      patch.object(storage.os, "read", return_value=b"x" * 100),
                      patch.object(storage.os, "killpg") as kill,
                      patch.object(storage, "_OUTPUT_LIMIT", 10),
                      patch.object(storage.time, "time", return_value=20)):
                    reply = storage.run_storage_operation("review", self.home, cancellation,
                                                         timeout=100, deadline_at=10 if problem == "deadline" else 0,
                                                         lease_fd=73)
                self.assertIn("error", reply)
                kill.assert_called_once_with(4321, storage.signal.SIGKILL)
                argv = spawn.call_args.args[0]
                self.assertEqual(argv[1], "-I")
                self.assertEqual(argv[-2:], ["review", self.home])
                self.assertTrue(spawn.call_args.kwargs["start_new_session"])
                self.assertEqual(spawn.call_args.kwargs["pass_fds"], (73,))

    def test_nested_native_commands_share_only_the_disposable_worker_group(self) -> None:
        process, selector = MagicMock(), MagicMock()
        process.wait.return_value = 0
        process.stdout.fileno.return_value = 8
        selector.__enter__.return_value = selector
        selector.get_map.return_value = {}
        with (patch.object(streamed_process.subprocess, "Popen", return_value=process) as spawn,
              patch.object(streamed_process.selectors, "DefaultSelector", return_value=selector),
              patch.object(streamed_process.os, "set_blocking")):
            streamed_process.run_captured(["/test/tool"], timeout=1)
            with streamed_process.caller_process_group():
                streamed_process.run_captured(["/test/tool"], timeout=1)
            streamed_process.run_captured(["/test/tool"], timeout=1)
        self.assertEqual([call.kwargs["start_new_session"] for call in spawn.call_args_list], [True, False, True])
