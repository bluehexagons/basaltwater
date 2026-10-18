"""Restart activity checks against temporary process and T3 database fixtures."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from lib.agent_activity import _one_shot, _t3_work_active, active_agent_workloads
from lib.agent_tasks import codex_command


START = "2026-10-18T10:00:00+00:00"
NOW = "2026-10-18T11:00:00+00:00"
OLD = "2026-10-17T10:00:00+00:00"


class AgentActivityTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.proc = self.root / "proc"
        self.proc.mkdir()
        (self.proc / "stat").write_text("btime 0\n")
        userdata = self.root / "userdata"
        userdata.mkdir()
        self.database = userdata / "statev2.sqlite"
        self.writer = sqlite3.connect(self.database)
        self.addCleanup(self.writer.close)
        self.writer.execute("PRAGMA journal_mode=WAL")
        self.writer.executescript("""
            CREATE TABLE orchestration_v2_projection_runs (
                run_id TEXT, thread_id TEXT, status TEXT, requested_at TEXT);
            CREATE TABLE orchestration_v2_projection_provider_turns (
                provider_turn_id TEXT, thread_id TEXT, node_id TEXT,
                status TEXT, started_at TEXT);
            CREATE TABLE orchestration_v2_projection_runtime_requests (
                thread_id TEXT, node_id TEXT, provider_turn_id TEXT, status TEXT);
            CREATE TABLE orchestration_v2_projection_nodes (
                node_id TEXT, parent_node_id TEXT);
            CREATE TABLE orchestration_v2_projection_subagents (
                subagent_id TEXT, origin TEXT, status TEXT, started_at TEXT,
                completed_at TEXT, child_thread_id TEXT);
            CREATE TABLE orchestration_v2_projection_turn_items (
                type TEXT, status TEXT, updated_at TEXT, run_id TEXT,
                node_id TEXT, payload_json TEXT);
            CREATE TABLE orchestration_v2_projection_provider_threads (
                status TEXT, updated_at TEXT, payload_json TEXT);
        """)

    def process(self, pid, name, args, *, parent=1):
        process = self.proc / str(pid)
        process.mkdir()
        (process / "comm").write_text(name + "\n")
        (process / "stat").write_text(f"{pid} ({name}) S {parent}" + " 0" * 18)
        (process / "cmdline").write_bytes(b"\0".join(os.fsencode(arg) for arg in args) + b"\0")
        (process / "fd").mkdir()
        return process

    def insert(self, table, **values):
        self.writer.execute(
            f"INSERT INTO orchestration_v2_projection_{table} ({','.join(values)}) "
            f"VALUES ({','.join('?' for _ in values)})", tuple(values.values()),
        )
        self.writer.commit()

    def active(self):
        return _t3_work_active(self.database, START, os.getuid())

    def test_scheduled_ephemeral_codex_command_is_active_with_global_options(self):
        command = codex_command({
            "mode": "inspect", "model": "example", "effort": "low",
            "web_search": "disabled", "session_history": False,
        }, "/usr/bin/codex")
        self.assertIn("--ephemeral", command)
        self.process(101, "codex", command)
        self.assertEqual(active_agent_workloads(os.getuid(), proc_root=str(self.proc)),
                         ["coding agent task"])

    def test_codex_global_option_values_and_prompts_are_not_subcommands(self):
        for index, arguments in enumerate((
            ["-c", "exec", "app-server"],
            ["--model=exec", "app-server"],
            ["-mexec", "app-server"],
            ["--", "exec"],
            ["please review this", "exec"],
        )):
            with self.subTest(arguments=arguments):
                process = self.process(101 + index, "codex", ["codex", *arguments])
                self.assertFalse(_one_shot(process, "codex"))

    def test_codex_exec_alias_and_review_after_options(self):
        for index, subcommand in enumerate(("exec", "e", "review")):
            process = self.process(101 + index, "codex",
                                   ["codex", "-cmodel=example", "--search", subcommand])
            self.assertTrue(_one_shot(process, "codex"))

    def test_unknown_or_truncated_codex_options_report_unknown_activity(self):
        for index, arguments in enumerate((["--new-option", "value", "exec"],
                                           ["-c", "x" * 5000, "exec"])):
            with self.subTest(case=index):
                self.process(101 + index, "codex", ["codex", *arguments])
                self.assertIsNone(active_agent_workloads(os.getuid(), proc_root=str(self.proc)))

    def test_native_child_outlives_completed_parent_even_with_parent_request(self):
        self.insert("runs", run_id="run", thread_id="thread", status="completed", requested_at=NOW)
        self.insert("provider_turns", provider_turn_id="turn", thread_id="thread",
                    node_id="root", status="completed", started_at=NOW)
        self.insert("runtime_requests", thread_id="thread", node_id="root",
                    provider_turn_id="turn", status="pending")
        self.insert("subagents", subagent_id="child", origin="provider_native",
                    status="running", started_at=NOW, completed_at=None)
        server = self.process(101, "t3", ["t3", "serve"])
        (server / "fd/3").symlink_to(self.database)
        self.process(102, "codex", ["codex", "app-server"], parent=101)
        self.assertEqual(active_agent_workloads(os.getuid(), proc_root=str(self.proc)),
                         ["T3 agent work"])
        self.writer.execute("UPDATE orchestration_v2_projection_subagents "
                            "SET status='completed', completed_at=?", (NOW,))
        self.writer.commit()
        self.assertEqual(active_agent_workloads(os.getuid(), proc_root=str(self.proc)), [])

    def test_old_terminal_and_unstarted_native_children_do_not_block(self):
        for status, started, completed in (("completed", NOW, NOW), ("running", OLD, None),
                                            ("idle", NOW, None), ("pending", None, None)):
            self.insert("subagents", subagent_id="child", origin="provider_native",
                        status=status, started_at=started, completed_at=completed)
        self.assertFalse(self.active())

    def test_child_waiting_for_human_does_not_block(self):
        self.insert("subagents", subagent_id="child", origin="provider_native",
                    status="waiting", started_at=NOW, completed_at=None,
                    child_thread_id="child-thread")
        self.insert("turn_items", type="subagent", status="waiting", updated_at=NOW,
                    node_id="child", payload_json="{}")
        self.assertTrue(self.active())
        self.insert("nodes", node_id="approval", parent_node_id="tool")
        self.insert("nodes", node_id="tool", parent_node_id="child")
        self.insert("runtime_requests", thread_id="child-thread", node_id="approval",
                    provider_turn_id="child-turn", status="pending")
        self.assertFalse(self.active())
        self.insert("subagents", subagent_id="sibling", origin="provider_native",
                    status="running", started_at=NOW, completed_at=None,
                    child_thread_id="sibling-thread")
        self.assertTrue(self.active())

    def test_child_request_with_separate_root_resolves_by_child_thread(self):
        self.insert("subagents", subagent_id="child", origin="provider_native",
                    status="waiting", started_at=NOW, completed_at=None,
                    child_thread_id="child-thread")
        self.insert("turn_items", type="subagent", status="waiting", updated_at=NOW,
                    node_id="child", payload_json="{}")
        self.insert("nodes", node_id="approval", parent_node_id="child-root")
        self.insert("runtime_requests", thread_id="child-thread", node_id="approval",
                    provider_turn_id="child-turn", status="pending")
        self.assertFalse(self.active())
        self.insert("turn_items", type="subagent", status="running", updated_at=NOW,
                    node_id="sibling", payload_json="{}")
        self.assertTrue(self.active())

    def test_approval_does_not_hide_independent_background_command(self):
        self.insert("nodes", node_id="approval", parent_node_id="waiting-tool")
        self.insert("runtime_requests", thread_id="thread", node_id="approval",
                    provider_turn_id="turn", status="pending")
        self.insert("turn_items", type="command_execution", status="waiting", updated_at=NOW,
                    node_id="waiting-tool", payload_json="{}")
        self.assertFalse(self.active())
        self.insert("turn_items", type="command_execution", status="running", updated_at=NOW,
                    node_id="independent-tool", payload_json="{}")
        self.assertTrue(self.active())

    def test_unrelated_request_does_not_hide_executing_provider_turn(self):
        self.insert("provider_turns", provider_turn_id="turn", thread_id="thread",
                    node_id="child", status="running", started_at=NOW)
        self.insert("runtime_requests", thread_id="thread", node_id="root",
                    provider_turn_id="parent-turn", status="pending")
        self.assertTrue(self.active())
        self.insert("runtime_requests", thread_id="thread", node_id="child",
                    provider_turn_id="turn", status="pending")
        self.assertFalse(self.active())

    def test_background_items_outlive_parent_and_ignore_passive_or_abandoned_work(self):
        self.insert("runs", run_id="run", thread_id="thread", status="completed", requested_at=NOW)
        for kind in ("subagent", "command_execution", "dynamic_tool"):
            with self.subTest(kind=kind):
                self.insert("turn_items", type=kind, status="running", updated_at=NOW,
                            run_id="run", node_id="child", payload_json="{}")
                self.assertTrue(self.active())
                self.writer.execute("DELETE FROM orchestration_v2_projection_turn_items")
        self.insert("runs", run_id="abandoned", thread_id="thread",
                    status="rolled_back", requested_at=NOW)
        for kind, status, timestamp, run_id, payload in (
            ("subagent", "running", OLD, "run", {}),
            ("command_execution", "completed", NOW, "run", {}),
            ("command_execution", "running", NOW, "abandoned", {}),
            ("dynamic_tool", "running", NOW, "run", {"input": {"persistent": True}}),
        ):
            self.insert("turn_items", type=kind, status=status, updated_at=timestamp,
                        run_id=run_id, node_id="child", payload_json=json.dumps(payload))
        self.assertFalse(self.active())

    def test_background_roster_excludes_monitors_and_old_servers(self):
        for timestamp, tasks in ((OLD, [{"kind": "subagent"}]),
                                 (NOW, [{"kind": "monitor"}])):
            self.insert("provider_threads", status="idle", updated_at=timestamp,
                        payload_json=json.dumps({"pendingBackgroundTasks": tasks}))
        self.assertFalse(self.active())
        self.insert("provider_threads", status="idle", updated_at=NOW,
                    payload_json=json.dumps({"pendingBackgroundTasks": [{"kind": "subagent"}]}))
        self.assertTrue(self.active())

    def test_queued_and_human_waiting_runs_are_idle(self):
        self.insert("runs", run_id="queued", thread_id="thread", status="queued", requested_at=NOW)
        self.insert("runs", run_id="waiting", thread_id="other", status="running", requested_at=NOW)
        self.insert("runtime_requests", thread_id="other", node_id="root",
                    provider_turn_id=None, status="pending")
        self.assertFalse(self.active())

    def test_missing_schema_is_unknown_activity_instead_of_idle(self):
        self.writer.execute("DROP TABLE orchestration_v2_projection_subagents")
        self.writer.commit()
        server = self.process(101, "t3", ["t3", "serve"])
        (server / "fd/3").symlink_to(self.database)
        self.assertIsNone(active_agent_workloads(os.getuid(), proc_root=str(self.proc)))


if __name__ == "__main__":
    unittest.main()
