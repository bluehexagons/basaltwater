"""Project memory must preserve privacy, bounded context and cooperative ownership."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
import fcntl
from io import StringIO
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from basaltwater import create_basaltwater_parser
from lib import agent_memory, agent_memory_cli
from lib.agent_cli import run_agent_command
from lib.agent_memory import AgentMemory, MemoryBusyError, MemoryConflictError


class MemoryFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.project = self.home / "project"
        self.project.mkdir(mode=0o700)
        self.enterContext(patch.object(agent_memory, "_project_scope", return_value="directory:" + str(self.project)))
        self.clock = self.enterContext(patch.object(agent_memory.time, "time", return_value=1000))
        self.memory = AgentMemory(str(self.project), home=str(self.home))

    def secret(self) -> Path:
        path = self.home / "credential"
        path.write_text("private-fixture-value")
        path.chmod(0o600)
        return path


class TestProjectMemory(MemoryFixture):
    def test_empty_read_does_not_create_storage(self) -> None:
        self.assertEqual(self.memory.list()["entries"], [])
        with self.assertRaises(FileNotFoundError):
            self.memory.get("note", "missing")
        self.assertFalse((self.home / ".local").exists())

    def test_notes_have_explicit_revision_conflicts_and_private_storage(self) -> None:
        first = self.memory.put("note", "handoff", "First findings")
        self.assertEqual(first["revision"], 1)
        self.assertNotIn("text", first)
        for revision in (0, 2):
            with self.subTest(revision=revision), self.assertRaises(MemoryConflictError):
                self.memory.put("note", "handoff", "Conflicting findings", revision=revision)
        updated = self.memory.put("note", "handoff", "Reviewed findings", revision=1)
        self.assertEqual(updated["revision"], 2)
        self.assertEqual(self.memory.get("note", "handoff")["text"], "Reviewed findings")
        self.assertEqual(self.memory.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.memory.directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.memory.root.stat().st_mode & 0o777, 0o700)

    def test_recreated_records_reject_revisions_from_their_deleted_predecessors(self) -> None:
        for kind in ("note", "todo", "secret"):
            with self.subTest(kind=kind):
                original_value = str(self.secret()) if kind == "secret" else "Old assignment"
                original = self.memory.put(kind, "handoff", original_value)
                self.memory.remove(kind, "handoff", revision=original["revision"])
                value = original_value if kind == "secret" else "Replacement assignment"
                replacement = self.memory.put(kind, "handoff", value)
                with self.assertRaises(MemoryConflictError):
                    self.memory.put(kind, "handoff", original_value, revision=original["revision"])
                with self.assertRaises(MemoryConflictError):
                    self.memory.remove(kind, "handoff", revision=original["revision"])
                self.assertEqual(self.memory.get(kind, "handoff")["revision"], replacement["revision"])
                self.assertGreater(replacement["revision"], original["revision"])

    def test_reopen_cannot_use_the_revision_of_a_deleted_todo(self) -> None:
        self.memory.put("todo", "verify", "Old assignment")
        claim = self.memory.claim("verify", "worker-1")
        original = self.memory.complete_claim("verify", claim["claim_token"], status="done")
        self.memory.remove("todo", "verify", revision=original["revision"])
        self.memory.put("todo", "verify", "Replacement assignment")
        claim = self.memory.claim("verify", "worker-2")
        replacement = self.memory.complete_claim("verify", claim["claim_token"], status="blocked")
        with self.assertRaises(MemoryConflictError):
            self.memory.reopen("verify", revision=original["revision"])
        self.assertEqual(self.memory.get("todo", "verify")["status"], "blocked")
        self.assertGreater(replacement["revision"], original["revision"])

    def test_revisions_survive_empty_storage_and_are_allocated_across_records(self) -> None:
        original = self.memory.put("note", "handoff", "Old findings")
        self.memory.remove("note", "handoff", revision=original["revision"])
        memory = AgentMemory(str(self.project), home=str(self.home))
        replacement = memory.put("note", "handoff", "New findings")
        other = memory.put("note", "other", "Separate findings")
        updated = memory.put("note", "handoff", "Reviewed findings", revision=replacement["revision"])
        self.assertGreater(replacement["revision"], original["revision"])
        self.assertGreater(other["revision"], replacement["revision"])
        self.assertGreater(updated["revision"], other["revision"])

    def test_legacy_state_preserves_revisions_and_migrates_only_after_a_successful_write(self) -> None:
        self.memory.put("note", "handoff", "Old findings")
        state = json.loads(self.memory.path.read_text())
        state["version"] = 1
        state.pop("next_revision", None)
        state["entries"]["note:handoff"]["revision"] = 8
        state["entries"]["note:older"] = {
            "kind": "note", "name": "older", "revision": 5, "updated_at": 1000, "text": "Earlier findings",
        }
        self.memory.path.write_text(json.dumps(state))
        before = self.memory.path.read_bytes()
        self.assertEqual(self.memory.get("note", "handoff")["revision"], 8)
        with self.assertRaises(MemoryConflictError):
            self.memory.put("note", "handoff", "Stale findings", revision=1)
        with patch.object(agent_memory, "write_json_atomic", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.memory.put("note", "handoff", "Updated findings", revision=8)
        self.assertEqual(self.memory.path.read_bytes(), before)
        updated = self.memory.put("note", "handoff", "Updated findings", revision=8)
        self.assertEqual(updated["revision"], 9)
        self.assertEqual(self.memory.get("note", "older")["revision"], 5)
        saved = json.loads(self.memory.path.read_text())
        self.assertEqual(saved["version"], 2)
        self.assertEqual(saved["next_revision"], 10)

    def test_invalid_revision_counters_are_preserved_without_resetting_state(self) -> None:
        self.memory.put("note", "handoff", "Keep this")
        state = json.loads(self.memory.path.read_text())
        state["version"] = 2
        for counter in (None, True, 0, 1, "2"):
            with self.subTest(counter=counter):
                state["next_revision"] = counter
                self.memory.path.write_text(json.dumps(state))
                before = self.memory.path.read_bytes()
                with self.assertRaises(RuntimeError):
                    self.memory.put("note", "handoff", "Must not write", revision=1)
                self.assertEqual(self.memory.path.read_bytes(), before)

    def test_index_is_bounded_and_search_does_not_dump_text_or_secret_paths(self) -> None:
        for index in range(3):
            self.memory.put("note", f"topic-{index}", "Detailed migration findings")
        self.memory.put("secret", "api", str(self.secret()))
        result = self.memory.list(limit=2)
        self.assertEqual(result["total"], 3)
        self.assertTrue(result["truncated"])
        self.assertNotIn("text", json.dumps(result))
        self.assertNotIn("credential", json.dumps(result))
        self.assertEqual(self.memory.list(query="migration")["total"], 3)
        self.assertEqual(self.memory.list(kind="secret")["total"], 1)
        self.assertNotIn("file", self.memory.list(kind="secret")["entries"][0])

    def test_pagination_discovers_records_beyond_the_maximum_page_without_dumping_text(self) -> None:
        for index in range(105):
            self.memory.put("note", f"topic-{index:03}", "Private detailed findings")
        first = self.memory.list(limit=100)
        self.assertEqual(len(first["entries"]), 100)
        self.assertEqual(first["next_offset"], 100)
        last = self.memory.list(limit=100, offset=first["next_offset"])
        self.assertEqual(last["total"], 105)
        self.assertEqual(len(last["entries"]), 5)
        self.assertFalse(last["truncated"])
        self.assertIsNone(last["next_offset"])
        names = [entry["name"] for page in (first, last) for entry in page["entries"]]
        self.assertEqual(len(set(names)), 105)
        self.assertNotIn("Private detailed findings", json.dumps([first, last]))
        empty = self.memory.list(offset=105)
        self.assertEqual(empty["entries"], [])
        self.assertIsNone(empty["next_offset"])

    def test_page_offsets_are_bounded_before_loading_or_creating_storage(self) -> None:
        for offset in (-1, agent_memory.MAX_ENTRIES + 1, True, 0.5):
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                self.memory.list(offset=offset)
        self.assertFalse((self.home / ".local").exists())

    def test_secret_references_never_read_or_copy_values_and_forgetting_preserves_file(self) -> None:
        secret = self.secret()
        self.memory.put("secret", "api", str(secret))
        with patch.object(Path, "read_text", side_effect=AssertionError("Do not read credential values")):
            self.assertEqual(self.memory.get("secret", "api")["file"], str(secret))
        self.assertNotIn("private-fixture-value", self.memory.path.read_text())
        self.memory.remove("secret", "api", revision=1)
        self.assertEqual(secret.read_text(), "private-fixture-value")

    def test_secret_reference_checks_live_ownership_permissions_and_links(self) -> None:
        secret = self.secret()
        self.memory.put("secret", "api", str(secret))
        secret.chmod(0o644)
        with self.assertRaises(RuntimeError):
            self.memory.get("secret", "api")
        secret.chmod(0o600)
        linked = self.home / "linked"
        linked.symlink_to(secret)
        with self.assertRaises(RuntimeError):
            self.memory.put("secret", "link", str(linked))
        os.link(secret, self.home / "hardlinked")
        with self.assertRaises(RuntimeError):
            self.memory.get("secret", "api")
        with tempfile.TemporaryDirectory() as outside:
            with self.assertRaises(ValueError):
                self.memory.put("secret", "outside", outside)

    def test_live_todo_claim_prevents_duplicate_work_editing_and_removal(self) -> None:
        self.memory.put("todo", "verify", "Verify the updated preview")
        claim = self.memory.claim("verify", "worker-1", seconds=30)
        self.assertNotIn(claim["claim_token"], json.dumps(self.memory.list()))
        self.assertNotIn("claim_token", self.memory.get("todo", "verify"))
        with self.assertRaises(MemoryConflictError):
            self.memory.claim("verify", "worker-2")
        with self.assertRaises(MemoryConflictError):
            self.memory.put("todo", "verify", "Changed assignment", revision=claim["revision"])
        with self.assertRaises(MemoryConflictError):
            self.memory.remove("todo", "verify", revision=claim["revision"])

    def test_expired_claim_cannot_renew_or_complete_a_reassigned_todo(self) -> None:
        self.memory.put("todo", "verify", "Verify deployment")
        original = self.memory.claim("verify", "worker-1", seconds=30)
        self.clock.return_value = 1020
        self.memory.complete_claim("verify", original["claim_token"], seconds=30)
        self.clock.return_value = 1050
        with self.assertRaises(MemoryConflictError):
            self.memory.complete_claim("verify", original["claim_token"])
        replacement = self.memory.claim("verify", "worker-2")
        self.assertNotEqual(original["claim_token"], replacement["claim_token"])
        with self.assertRaises(MemoryConflictError):
            self.memory.complete_claim("verify", original["claim_token"], status="done")
        done = self.memory.complete_claim("verify", replacement["claim_token"], status="done")
        self.assertEqual(done["status"], "done")
        self.assertIsNone(done["claim"])
        reopened = self.memory.reopen("verify", revision=done["revision"])
        self.assertEqual(reopened["status"], "open")

    def test_blocking_or_releasing_a_claim_preserves_the_assignment(self) -> None:
        self.memory.put("todo", "verify", "Verify deployment")
        for status in ("open", "blocked"):
            claim = self.memory.claim("verify", "worker-1")
            result = self.memory.complete_claim("verify", claim["claim_token"], status=status)
            self.assertEqual(result["status"], status)
            self.assertEqual(self.memory.get("todo", "verify")["text"], "Verify deployment")
        with self.assertRaises(MemoryConflictError):
            self.memory.claim("verify", "worker-2")

    def test_concurrent_writers_cannot_silently_overwrite_an_observed_revision(self) -> None:
        self.memory.put("note", "handoff", "Initial findings")
        stores = [AgentMemory(str(self.project), home=str(self.home)) for _ in range(2)]

        def update(index: int) -> str:
            try:
                stores[index].put("note", "handoff", f"Worker {index}", revision=1)
                return "updated"
            except MemoryConflictError:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(update, (0, 1))), ["updated", "conflict"])
        self.assertEqual(self.memory.get("note", "handoff")["revision"], 2)

    def test_concurrent_claimers_cannot_both_acquire_the_same_todo(self) -> None:
        self.memory.put("todo", "verify", "Verify rollout")
        stores = [AgentMemory(str(self.project), home=str(self.home)) for _ in range(2)]

        def claim(index: int) -> str:
            try:
                stores[index].claim("verify", f"worker-{index}")
                return "claimed"
            except MemoryConflictError:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(claim, (0, 1))), ["claimed", "conflict"])

    def test_other_account_or_public_state_directory_is_refused(self) -> None:
        with patch.object(agent_memory.os, "geteuid", return_value=os.getuid() + 1):
            with self.assertRaises(RuntimeError):
                self.memory.put("note", "handoff", "Must not write")
        self.assertFalse(self.memory.path.exists())
        self.memory.put("note", "handoff", "Keep this")
        self.memory.root.chmod(0o755)
        with self.assertRaises(RuntimeError):
            self.memory.get("note", "handoff")

    def test_atomic_write_failure_keeps_previous_note(self) -> None:
        self.memory.put("note", "handoff", "Keep this")
        before = self.memory.path.read_bytes()
        with patch.object(agent_memory, "write_json_atomic", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.memory.put("note", "handoff", "New text", revision=1)
        self.assertEqual(self.memory.path.read_bytes(), before)

    def test_busy_lock_is_bounded_and_does_not_rewrite_state(self) -> None:
        self.memory.put("note", "handoff", "Keep this")
        with (self.memory.directory / "memory.lock").open("r+") as holder:
            fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(agent_memory.time, "monotonic", side_effect=[0, 6]):
                with self.assertRaises(MemoryBusyError):
                    self.memory.put("note", "new", "Must not persist")
        self.assertEqual(self.memory.list()["total"], 1)

    def test_storage_limit_does_not_evict_existing_records(self) -> None:
        self.memory.put("note", "first", "Keep this")
        before = self.memory.path.read_bytes()
        with patch.object(agent_memory, "MAX_ENTRIES", 1):
            with self.assertRaises(ValueError):
                self.memory.put("note", "second", "Too many")
        with patch.object(agent_memory, "MAX_STORAGE_BYTES", len(before) + 100):
            with self.assertRaises(ValueError):
                self.memory.put("note", "first", "x" * 1000, revision=1)
        self.assertEqual(self.memory.path.read_bytes(), before)

    def test_invalid_inputs_fail_without_creating_records(self) -> None:
        for name in ("../escape", "", "name\n"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.memory.put("note", name, "text")
        for text in ("", "\x1b[31m", "é" * agent_memory.MAX_TEXT_BYTES):
            with self.subTest(text=text[:20]), self.assertRaises(ValueError):
                self.memory.put("note", "invalid", text)
        for lease in (0, 29, 86401, True):
            with self.subTest(lease=lease), self.assertRaises(ValueError):
                self.memory.claim("missing", "worker", seconds=lease)
        self.assertFalse(self.memory.path.exists())

    def test_unsafe_index_files_are_never_followed_or_replaced(self) -> None:
        for kind in ("symlink", "hardlink", "fifo", "permissions"):
            with self.subTest(kind=kind):
                original = self.memory.put("note", "first", "Keep this")
                saved = self.home / "saved"
                self.memory.path.rename(saved)
                if kind == "symlink":
                    self.memory.path.symlink_to(saved)
                elif kind == "hardlink":
                    os.link(saved, self.memory.path)
                elif kind == "fifo":
                    os.mkfifo(self.memory.path, 0o600)
                else:
                    self.memory.path.write_bytes(saved.read_bytes())
                    self.memory.path.chmod(0o644)
                before = saved.read_bytes()
                with self.assertRaises((OSError, RuntimeError)):
                    self.memory.put("note", "new", "Must not write")
                self.assertEqual(saved.read_bytes(), before)
                self.memory.path.unlink()
                saved.rename(self.memory.path)
                self.memory.remove("note", "first", revision=original["revision"])

    def test_symlinked_state_parent_and_corrupt_schema_are_preserved(self) -> None:
        outside = self.home / "outside"
        outside.mkdir()
        (self.home / ".local").symlink_to(outside)
        with self.assertRaises(RuntimeError):
            self.memory.put("note", "first", "Must not write")
        self.assertEqual(list(outside.iterdir()), [])
        (self.home / ".local").unlink()
        self.memory.put("note", "first", "Keep this")
        for content in ('{"version":1,"version":1}', '{"version":2,"scope":"wrong","entries":{}}'):
            self.memory.path.write_text(content)
            with self.assertRaises(RuntimeError):
                self.memory.put("note", "new", "Must not write")
            self.assertEqual(self.memory.path.read_text(), content)


class TestMemoryCLI(MemoryFixture):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.parser = create_basaltwater_parser()[0]

    def invoke(self, *arguments: str, stdin: str = "") -> tuple[int, dict]:
        args = self.parser.parse_args(["agent", "memory", *arguments, "--json"])
        output = StringIO()
        with (patch.object(agent_memory_cli, "AgentMemory", return_value=self.memory),
              patch.object(agent_memory_cli.sys, "stdin", StringIO(stdin)), redirect_stdout(output)):
            status = run_agent_command(args)
        return status, json.loads(output.getvalue())

    def test_cli_creates_and_reads_selected_text_without_dumping_index_bodies(self) -> None:
        self.assertEqual(self.invoke("put", "handoff", "--file", "-", stdin="Reviewed findings")[0], 0)
        self.assertEqual(self.invoke("get", "handoff")[1]["text"], "Reviewed findings")
        self.assertNotIn("text", json.dumps(self.invoke("list")[1]))
        self.assertEqual(self.invoke("put", "handoff", "--file", "-", stdin="Conflict")[1]["issue"], "conflict")

    def test_cli_secret_path_is_registered_without_reading_values(self) -> None:
        secret = self.secret()
        with patch.object(agent_memory_cli, "_read_text", side_effect=AssertionError("No secret reads")):
            status, result = self.invoke("put", "api", "--kind", "secret", "--file", str(secret))
            self.assertEqual(status, 0)
            self.assertNotIn("private-fixture-value", json.dumps(result))
        self.assertEqual(self.invoke("get", "api", "--kind", "secret")[1]["file"], str(secret))
        self.assertFalse(self.invoke("put", "bad", "--kind", "secret", "--file", "-")[1]["ok"])

    def test_cli_todo_claim_renew_finish_and_reopen(self) -> None:
        self.invoke("put", "verify", "--kind", "todo", "--file", "-", stdin="Verify rollout")
        claim = self.invoke("claim", "verify", "--owner", "worker-1")[1]
        self.assertEqual(self.invoke("renew", "verify", "--claim-token", claim["claim_token"])[0], 0)
        done = self.invoke("finish", "verify", "--claim-token", claim["claim_token"])[1]
        self.assertEqual(done["status"], "done")
        self.assertEqual(self.invoke("reopen", "verify", "--if-revision", str(done["revision"]))[1]["status"], "open")

    def test_cli_rejects_oversized_input_without_creating_a_record(self) -> None:
        self.assertEqual(self.invoke("put", "large", "--file", "-", stdin="x" * (agent_memory.MAX_TEXT_BYTES + 1))[0], 1)
        self.assertFalse(self.memory.path.exists())

    def test_cli_rejects_stale_revisions_after_a_name_is_reused(self) -> None:
        original = self.invoke("put", "handoff", "--file", "-", stdin="Old findings")[1]
        self.invoke("remove", "handoff", "--if-revision", str(original["revision"]))
        replacement = self.invoke("put", "handoff", "--file", "-", stdin="New findings")[1]
        status, result = self.invoke("put", "handoff", "--file", "-", "--if-revision", str(original["revision"]), stdin="Stale findings")
        self.assertEqual(status, 1)
        self.assertEqual(result["issue"], "conflict")
        self.assertEqual(self.invoke("get", "handoff")[1]["revision"], replacement["revision"])

    def test_cli_pages_filtered_metadata_and_rejects_invalid_offsets(self) -> None:
        self.memory.put("todo", "earlier", "Deployment validation")
        self.memory.put("note", "first", "Deployment findings")
        self.memory.put("note", "second", "Deployment findings")
        self.memory.put("note", "unrelated", "Browser findings")
        status, first = self.invoke("list", "--kind", "note", "--query", "deployment", "--limit", "1")
        self.assertEqual(status, 0)
        self.assertEqual(first["total"], 2)
        status, last = self.invoke("list", "--kind", "note", "--query", "deployment", "--limit", "1", "--offset", str(first["next_offset"]))
        self.assertEqual(status, 0)
        self.assertEqual(last["total"], 2)
        self.assertEqual(last["entries"][0]["name"], "second")
        self.assertIsNone(last["next_offset"])
        self.assertNotIn("text", json.dumps([first, last]))
        status, invalid = self.invoke("list", "--offset", "-1")
        self.assertEqual(status, 1)
        self.assertEqual(invalid["issue"], "memory_error")


class TestMemoryScope(unittest.TestCase):
    def test_git_worktrees_and_subdirectories_share_the_common_git_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            projects = [root / name for name in ("main", "worker", "subdirectory")]
            common = root / "main/.git"
            for project in projects:
                project.mkdir()
            common.mkdir()
            completed = SimpleNamespace(returncode=0, stdout=str(common) + "\n", stderr="")
            with (patch.object(agent_memory.shutil, "which", return_value="/usr/bin/git"),
                  patch.object(agent_memory, "run_command", return_value=completed) as run,
                  patch.dict(os.environ, {"GIT_DIR": "/unrelated"})):
                stores = [AgentMemory(str(project), home=directory) for project in projects]
            self.assertEqual(len({store.scope for store in stores}), 1)
            self.assertNotIn("GIT_DIR", run.call_args.kwargs["env"])
            self.assertEqual(run.call_args.kwargs["timeout"], 10)

    def test_non_git_directory_is_local_and_repository_errors_do_not_create_another_scope(self) -> None:
        with tempfile.TemporaryDirectory() as project:
            with patch.object(agent_memory.shutil, "which", return_value="/usr/bin/git"):
                with patch.object(agent_memory, "run_command", return_value=SimpleNamespace(returncode=128, stdout="", stderr="not a git repository")):
                    self.assertEqual(agent_memory._project_scope(project), "directory:" + str(Path(project).resolve()))
                with patch.object(agent_memory, "run_command", return_value=SimpleNamespace(returncode=128, stdout="", stderr="dubious ownership")):
                    with self.assertRaises(RuntimeError):
                        agent_memory._project_scope(project)


if __name__ == "__main__":
    unittest.main()
