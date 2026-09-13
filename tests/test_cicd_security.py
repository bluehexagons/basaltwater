"""Security regression tests for the CI/CD webhook boundary."""

from __future__ import annotations

from io import BytesIO
import json
import os
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

from web.service_tools import cicd_executor, webhook_receiver
from web.service_tools.cicd_security import (
    DEFAULT_BRANCHES,
    MAX_WEBHOOK_PAYLOAD_BYTES,
    get_workspace_name,
    validate_branch_ref,
    validate_commit_sha,
    validate_job_data,
)


class TestCICDInputValidation(unittest.TestCase):
    def test_default_branch_is_main_only(self):
        self.assertEqual(DEFAULT_BRANCHES, ("main",))

    def test_commit_sha_requires_full_hex_object_id(self):
        self.assertEqual(validate_commit_sha("A" * 40), "a" * 40)
        for invalid in ("abc123", "g" * 40, "0" * 40, "../etc/passwd"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_commit_sha(invalid)

    def test_branch_ref_rejects_revision_and_path_tricks(self):
        self.assertEqual(
            validate_branch_ref("refs/heads/release/one"),
            ("refs/heads/release/one", "release/one"),
        )
        for invalid in (
            "main",
            "refs/tags/v1",
            "refs/heads/../main",
            "refs/heads/main^{commit}",
            "refs/heads/-bad.lock",
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_branch_ref(invalid)

    def test_job_data_rejects_control_characters(self):
        with self.assertRaisesRegex(ValueError, "pusher"):
            validate_job_data(
                {
                    "repo_url": "https://example.test/repo.git",
                    "ref": "refs/heads/main",
                    "commit_sha": "a" * 40,
                    "pusher": "alice\nforged-log-entry",
                }
            )

    def test_http_repository_url_rejects_embedded_credentials(self):
        with self.assertRaisesRegex(ValueError, "credentials"):
            validate_job_data(
                {
                    "repo_url": "https://token@example.test/repo.git",
                    "ref": "refs/heads/main",
                    "commit_sha": "a" * 40,
                    "pusher": "alice",
                }
            )

    def test_workspace_names_are_safe_and_distinguish_same_repo_names(self):
        first = get_workspace_name("https://example.test/one/repo.git")
        second = get_workspace_name("https://example.test/two/repo.git")
        self.assertNotEqual(first, second)
        self.assertNotIn("/", first)
        self.assertTrue(first.startswith("repo-"))


class TestWebhookRequestLimits(unittest.TestCase):
    def test_oversized_request_is_rejected_before_reading_body(self):
        handler = object.__new__(webhook_receiver.WebhookHandler)
        handler.path = "/webhook"
        handler.headers = {"Content-Length": str(MAX_WEBHOOK_PAYLOAD_BYTES + 1)}
        handler.rfile = BytesIO(b"")
        handler.wfile = BytesIO()
        handler.send_error = MagicMock()

        handler.do_POST()

        handler.send_error.assert_called_once_with(413, "Payload Too Large")
        self.assertEqual(handler.rfile.tell(), 0)

    def test_job_filename_does_not_embed_untrusted_payload_fields(self):
        with tempfile.TemporaryDirectory() as jobs_dir, patch.object(
            webhook_receiver,
            "JOBS_DIR",
            jobs_dir,
        ), patch.object(webhook_receiver, 'DELIVERIES_FILE', os.path.join(jobs_dir, 'receipts.sqlite3')):
            result = webhook_receiver.trigger_cicd_job(
                "https://example.test/org/repo.git",
                "refs/heads/main",
                "a" * 40,
                "alice",
            )

            self.assertTrue(result)
            filenames = [name for name in os.listdir(jobs_dir) if name.endswith('.json')]
            self.assertEqual(len(filenames), 1)
            self.assertNotIn("https", filenames[0])
            with open(os.path.join(jobs_dir, filenames[0]), encoding="utf-8") as file_obj:
                job_data = json.load(file_obj)
            self.assertEqual(job_data["commit_sha"], "a" * 40)


class TestExecutorJobHardening(unittest.TestCase):
    def test_fifo_is_opened_nonblocking_and_rejected(self):
        with patch.object(cicd_executor.os, 'open', return_value=42) as open_fd, patch.object(cicd_executor.os, 'fstat', return_value=SimpleNamespace(st_mode=stat.S_IFIFO)), patch.object(cicd_executor.os, 'close') as close_fd:
            with self.assertRaisesRegex(ValueError, 'regular file'):
                cicd_executor._load_job_file('/mock/job.json')
            self.assertTrue(open_fd.call_args.args[1] & os.O_NONBLOCK)
            close_fd.assert_called_once_with(42)

    def test_read_limit_survives_file_growth_after_stat(self):
        with tempfile.TemporaryDirectory() as directory:
            job = os.path.join(directory, 'job.json')
            with open(job, 'wb') as file_obj:
                file_obj.write(b'{"value":"' + b'a' * 100 + b'"}')
            with patch.object(cicd_executor, 'MAX_JOB_FILE_BYTES', 20), patch.object(cicd_executor.os, 'fstat', return_value=SimpleNamespace(st_mode=stat.S_IFREG, st_size=1)):
                with self.assertRaisesRegex(ValueError, 'too large'):
                    cicd_executor._load_job_file(job)

    def test_fresh_clone_checks_out_authenticated_commit_with_hooks_disabled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "source")
            workspace = os.path.join(temp_dir, "workspace")
            subprocess.run(["git", "init", "-b", "main", source], check=True, capture_output=True)
            subprocess.run(
                ["git", "-C", source, "-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "--allow-empty", "-m", "initial"],
                check=True,
                capture_output=True,
            )
            commit_sha = subprocess.check_output(
                ["git", "-C", source, "rev-parse", "HEAD"],
                text=True,
            ).strip()

            result = cicd_executor.clone_or_update_repo(
                source,
                workspace,
                "refs/heads/main",
                commit_sha,
            )

            self.assertTrue(result)
            checked_out_sha = subprocess.check_output(
                ["git", "-C", workspace, "rev-parse", "HEAD"],
                text=True,
            ).strip()
            hooks_path = subprocess.check_output(
                ["git", "-C", workspace, "config", "--get", "core.hooksPath"],
                text=True,
            ).strip()
            self.assertEqual(checked_out_sha, commit_sha)
            self.assertEqual(hooks_path, "/dev/null")

    def test_malformed_job_is_consumed(self):
        with tempfile.TemporaryDirectory() as jobs_dir:
            job_path = os.path.join(jobs_dir, "bad.json")
            with open(job_path, "w", encoding="utf-8") as file_obj:
                json.dump({"repo_url": "https://example.test/repo.git"}, file_obj)

            with patch.object(cicd_executor, 'load_config', return_value={'repositories': []}), self.assertLogs(cicd_executor.logger, level="ERROR"):
                result = cicd_executor.process_job(job_path)

            self.assertFalse(result)
            self.assertFalse(os.path.exists(job_path))

    def test_non_file_queue_entry_is_consumed(self):
        with tempfile.TemporaryDirectory() as jobs_dir:
            job_path = os.path.join(jobs_dir, "bad.json")
            os.makedirs(job_path)

            with patch.object(cicd_executor, 'load_config', return_value={'repositories': []}), self.assertLogs(cicd_executor.logger, level="ERROR"):
                result = cicd_executor.process_job(job_path)

            self.assertFalse(result)
            self.assertFalse(os.path.exists(job_path))

    @unittest.skipUnless(hasattr(os, "O_NOFOLLOW"), "O_NOFOLLOW is unavailable")
    def test_job_loader_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as jobs_dir:
            target = os.path.join(jobs_dir, "target")
            link = os.path.join(jobs_dir, "job.json")
            with open(target, "w", encoding="utf-8") as file_obj:
                file_obj.write("{}")
            os.symlink(target, link)

            with self.assertRaises(OSError):
                cicd_executor._load_job_file(link)


if __name__ == "__main__":
    unittest.main()
