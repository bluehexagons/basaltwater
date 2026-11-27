"""Schedule lifecycle, dispatch races and retained publishing history."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from io import StringIO
import json
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lib.publishing import Publishing, project_revision
from lib.publishing_auth import PublishingAuth
from lib.publishing_cli import add_publishing_subparser, run_publishing_command
from lib.publishing_worker import work
from tests import test_publishing as fixtures


class PublishingJobTests(unittest.TestCase):
    complete = fixtures.PublishingTests.complete

    def setUp(self):
        fixtures.PublishingTests.setUp(self)

    def saved(self, kind, record_id):
        with self.publisher.store.transaction() as db:
            return self.publisher.store.get(db, kind, record_id)

    def test_duplicate_schedule_rejected_and_removal_keeps_history(self):
        job = self.publisher.schedule("game", 60)
        run = self.publisher.upload(self.publisher.prepare("game")["id"], job=job["id"])
        for action in (None, "pause"):
            if action:
                self.publisher.job_action(job["id"], action)
            with self.assertRaises(ValueError):
                Publishing(str(self.home)).schedule("game", 120)
        self.assertEqual(self.saved("runs", run["id"])["state"], "cancelled")
        self.publisher.job_action(job["id"], "remove")
        removed = self.saved("jobs", job["id"])
        self.publisher.job_action(job["id"], "remove")
        self.assertEqual(self.saved("jobs", job["id"]), removed)
        for action in ("pause", "resume"):
            with self.assertRaises(ValueError):
                self.publisher.job_action(job["id"], action)
        with self.assertRaises(ValueError):
            self.publisher.edit_schedule(job["id"], 30)
        replacement = self.publisher.schedule("game", 30)
        self.assertNotEqual(replacement["id"], job["id"])
        self.assertEqual(self.saved("runs", run["id"])["job"], job["id"])
        # Cancellation was pre-dispatch, so the exact artifact can be queued again.
        retried = self.publisher.upload(run["artifact"], job=replacement["id"])
        self.assertNotEqual(retried["id"], run["id"])

    def test_edit_preserves_pause_authority_and_failures_until_resume(self):
        job = self.publisher.schedule("game", 60, enabled=False)
        with self.publisher.store.transaction() as db:
            job.update(failures=3, last_error="Inspect the source")
            self.publisher.store.put(db, "jobs", job)
        current_project = self.publisher.save_project("game", str(self.repo), "butler", "owner/game:new-channel")
        with patch("lib.publishing.now", return_value=100):
            edited = self.publisher.edit_schedule(job["id"], 30)
        self.assertEqual(edited["state"], "paused")
        self.assertEqual(edited["project_revision"], job["project_revision"])
        self.assertEqual(edited["failures"], 3)
        self.assertEqual(edited["last_error"], "Inspect the source")
        self.assertEqual(edited["next_at"], 1900)
        with patch("lib.publishing.now", return_value=200):
            self.publisher.job_action(job["id"], "resume")
        resumed = self.saved("jobs", job["id"])
        self.assertEqual(resumed["state"], "enabled")
        self.assertEqual(resumed["project_revision"], project_revision(current_project))
        self.assertEqual(resumed["next_at"], 2000)
        self.assertEqual(resumed["failures"], 0)
        self.assertNotIn("last_error", resumed)
        for interval in (True, 4, 43201, 5.0, "60"):
            with self.subTest(interval=interval), self.assertRaises(ValueError):
                self.publisher.edit_schedule(job["id"], interval)
        self.assertEqual(self.saved("jobs", job["id"]), resumed)

    def test_edit_cancels_only_its_queued_work(self):
        job = self.publisher.schedule("game", 60)
        scheduled = self.publisher.upload(self.publisher.prepare("game")["id"], job=job["id"])
        (self.output / "game.bin").write_bytes(b"other build")
        self.complete()
        manual = self.publisher.upload(self.publisher.prepare("game")["id"])
        self.publisher.edit_schedule(job["id"], 30)
        self.assertEqual(self.saved("runs", scheduled["id"])["state"], "cancelled")
        self.assertEqual(self.saved("runs", manual["id"])["state"], "queued")
        self.assertEqual(self.saved("jobs", job["id"])["state"], "enabled")

    def test_inflight_poll_cannot_enqueue_after_pause_remove_or_edit(self):
        prepare = self.publisher.prepare
        for action in ("pause", "remove", "edit"):
            job = self.publisher.schedule("game", 60)

            def prepare_then_change(project):
                artifact = prepare(project)
                if action == "edit":
                    self.publisher.edit_schedule(job["id"], 30)
                else:
                    self.publisher.job_action(job["id"], action)
                return artifact

            with self.subTest(action=action), patch.object(self.publisher, "prepare", side_effect=prepare_then_change):
                self.publisher.tick()
                self.assertEqual(self.publisher.status()["runs"], [])
                self.assertEqual(self.publisher.status()["artifacts"], [])
                saved = self.saved("jobs", job["id"])
                self.assertEqual(saved["state"], {"pause": "paused", "remove": "removed", "edit": "enabled"}[action])
                self.assertEqual(saved["failures"], 0)
                self.assertNotIn("last_error", saved)
            self.publisher.job_action(job["id"], "remove")

    def test_removal_during_failed_preparation_is_not_resurrected(self):
        job = self.publisher.schedule("game", 60)

        def fail_after_removal(project):
            self.publisher.job_action(job["id"], "remove")
            raise ValueError("Preparation failed")

        with patch.object(self.publisher, "prepare", side_effect=fail_after_removal):
            self.publisher.tick()
        saved = self.saved("jobs", job["id"])
        self.assertEqual(saved["state"], "removed")
        self.assertEqual(saved["failures"], 0)

    def test_worker_rejects_stale_schedule_revision_without_native_dispatch(self):
        job = self.publisher.schedule("game", 60)
        run = self.publisher.upload(self.publisher.prepare("game")["id"], job=job["id"])
        # Simulate a stale durable queue from an earlier schedule revision.
        with self.publisher.store.transaction() as db:
            job["revision"] += 1
            self.publisher.store.put(db, "jobs", job)
        with patch("lib.publishing_worker.os.geteuid", return_value=1000), \
                patch("lib.publishing_worker.upload_command") as command, patch("lib.publishing_worker.execute") as execute:
            work(self.publisher)
        command.assert_not_called()
        execute.assert_not_called()
        self.assertEqual(self.saved("runs", run["id"])["state"], "cancelled")

    def test_job_upload_requires_enabled_matching_project_and_revision(self):
        job = self.publisher.schedule("game", 60, enabled=False)
        artifact = self.publisher.prepare("game")
        with self.assertRaises(ValueError):
            self.publisher.upload(artifact["id"], job=job["id"])
        self.publisher.job_action(job["id"], "resume")
        with self.assertRaises(ValueError):
            self.publisher.upload(artifact["id"], job=job["id"], job_revision=0)
        self.publisher.save_project("other", str(self.repo), "butler", "owner/game:other")
        with self.assertRaises(ValueError):
            self.publisher.upload(self.publisher.prepare("other")["id"], job=job["id"])
        run = self.publisher.upload(artifact["id"], job=job["id"])
        self.assertEqual(self.publisher.upload(artifact["id"], job=job["id"])["id"], run["id"])
        (self.output / "game.bin").write_bytes(b"another build")
        self.complete()
        with self.assertRaises(ValueError):
            self.publisher.upload(self.publisher.prepare("game")["id"], job=job["id"])

    def test_auth_failure_and_logout_keep_removed_jobs_removed(self):
        job = self.publisher.schedule("game", 60)
        run = self.publisher.upload(self.publisher.prepare("game")["id"], job=job["id"])

        def failure_during_removal(*args, **kwargs):
            # The worker has already durably claimed the run. Removal cannot
            # assert that its remote effects were cancelled.
            self.publisher.job_action(job["id"], "remove")
            self.assertEqual(self.saved("runs", run["id"])["state"], "running")
            replacement = self.publisher.schedule("game", 30)
            self.assertEqual(replacement["state"], "enabled")
            return {"state": "unknown", "needs_login": True}

        with patch("lib.publishing_worker.os.geteuid", return_value=1000), \
                patch("lib.publishing_worker.upload_command", return_value=["/fake/butler"]), \
                patch("lib.publishing_worker.execute", side_effect=failure_during_removal):
            work(self.publisher)
        self.assertEqual(self.saved("jobs", job["id"])["state"], "removed")
        self.assertEqual(self.saved("runs", run["id"])["state"], "unknown")
        active = next(row for row in self.publisher.status()["jobs"] if row["id"] != job["id"])
        self.assertEqual(active["state"], "paused")
        PublishingAuth(self.publisher).logout("butler")
        self.assertEqual(self.saved("jobs", job["id"])["state"], "removed")
        self.assertEqual(self.saved("runs", run["id"])["state"], "unknown")

    def test_logout_cancels_queued_uploads_and_pauses_schedule(self):
        job = self.publisher.schedule("game", 60)
        run = self.publisher.upload(self.publisher.prepare("game")["id"], job=job["id"])
        PublishingAuth(self.publisher).logout("butler")
        self.assertEqual(self.saved("jobs", job["id"])["state"], "paused")
        self.assertEqual(self.saved("runs", run["id"])["state"], "cancelled")

    def test_cli_edits_and_removes_without_ignoring_interval_options(self):
        parser = argparse.ArgumentParser()
        add_publishing_subparser(parser.add_subparsers())
        job = self.publisher.schedule("game", 60)
        for options, expected in ((["edit", "--interval", "30"], 0), (["edit"], 2),
                                  (["resume", "--interval", "30"], 2), (["remove"], 0)):
            args = parser.parse_args(["publish", "job", job["id"], *options])
            with self.subTest(options=options), patch("lib.publishing_cli.Publishing", return_value=self.publisher), redirect_stdout(StringIO()):
                self.assertEqual(run_publishing_command(args), expected)
        saved = self.saved("jobs", job["id"])
        self.assertEqual(saved["interval"], 1800)
        self.assertEqual(saved["state"], "removed")

    def test_cli_hands_prepared_artifact_and_wait_timeout_to_status_and_cancel(self):
        parser = argparse.ArgumentParser()
        add_publishing_subparser(parser.add_subparsers())

        def run(expected, *argv):
            with redirect_stdout(StringIO()) as output:
                self.assertEqual(run_publishing_command(parser.parse_args(["publish", *argv])), expected)
            return json.loads(output.getvalue())

        with patch("lib.publishing_cli.Publishing", return_value=self.publisher), \
                patch.object(self.publisher, "start_worker") as start, \
                patch("lib.publishing_cli.time.monotonic", side_effect=[0, 1]):
            prepared = run(0, "prepare", "game")
            waited = run(124, "upload", prepared["id"], "--wait", "--timeout", "1")
            self.assertEqual(waited["issue"], "wait_timeout")
            job = waited["job"]
            self.assertEqual(job["state"], "queued")
            self.assertEqual(run(0, "runs", "--id", job["id"])["job"], job)
            cancelled = run(0, "cancel", job["id"])
            self.assertEqual(cancelled["job"]["id"], job["id"])
            self.assertEqual(cancelled["cancellation"], "confirmed")
            start.assert_called_once_with()

    def test_interactive_login_keeps_provider_dialogue_out_of_json_stdout(self):
        parser = argparse.ArgumentParser()
        add_publishing_subparser(parser.add_subparsers())
        args = parser.parse_args(["publish", "auth", "login", "butler", "--json"])
        output = StringIO()
        with patch("lib.publishing_cli.Publishing", return_value=self.publisher), \
                patch("lib.publishing_cli.os.geteuid", return_value=1000), \
                patch("sys.stdin.isatty", return_value=True), patch.object(output, "isatty", return_value=True), \
                patch("lib.publishing_cli.executable", return_value="/fixture/butler"), \
                patch("lib.publishing_cli.subprocess.run", return_value=SimpleNamespace(returncode=0)) as launch, \
                redirect_stdout(output):
            self.assertEqual(run_publishing_command(args), 0)
        self.assertIs(launch.call_args.kwargs["stdout"], sys.stderr)
        result = json.loads(output.getvalue())
        self.assertTrue(result["ok"])
        self.assertEqual(result["child_exit_code"], 0)
