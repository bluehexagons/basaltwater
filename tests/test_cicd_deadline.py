"""CI/CD phases share one budget and report the deployment boundary."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from lib import cicd_deadline as deadline
from lib.remote_utils import CommandTimeoutError
from web.service_tools import cicd_executor as executor


class TestCICDDeadline(unittest.TestCase):
    def test_commands_receive_remaining_time_and_expiry_stops_new_work(self):
        clock = [0]
        with patch.object(deadline.time, "monotonic", side_effect=lambda: clock[0]), patch.object(deadline, "run") as run:
            with deadline.job_budget() as budget:
                deadline.run_command(["git"], timeout=300)
                self.assertEqual(run.call_args.kwargs["timeout"], 300)
                clock[0] = deadline.JOB_TIMEOUT_SECONDS - 3
                deadline.run_command(["rsync"], timeout=300)
                self.assertEqual(run.call_args.kwargs["timeout"], 3)
                clock[0] += 4
                with self.assertRaises(subprocess.TimeoutExpired):
                    deadline.enter_phase("deploy", deployment=True)
                self.assertEqual(run.call_count, 2)
                self.assertTrue(budget.timed_out)
                self.assertFalse(budget.deployment_started)

    def test_timeout_after_deployment_starts_preserves_phase(self):
        with deadline.job_budget() as budget:
            deadline.enter_phase("deploy", deployment=True)
            with patch.object(deadline, "run", side_effect=CommandTimeoutError("ssh", 3)):
                with self.assertRaises(subprocess.TimeoutExpired):
                    deadline.run_command(["ssh"], timeout=3)
            self.assertTrue(budget.timed_out)
            self.assertTrue(budget.deployment_started)
            self.assertEqual(budget.phase, "deploy")

    def test_expired_build_does_not_start_test_or_deploy_and_logs_boundary(self):
        clock = [0]
        repo = "https://github.com/example/repo.git"

        def build(*args):
            clock[0] = deadline.JOB_TIMEOUT_SECONDS + 1
            return True

        with tempfile.TemporaryDirectory() as directory:
            job = os.path.join(directory, "job.json")
            with open(job, "w") as file:
                json.dump({"repo_url": repo, "ref": "refs/heads/main", "commit_sha": "a" * 40, "pusher": "user"}, file)
            with (
                patch.object(deadline.time, "monotonic", side_effect=lambda: clock[0]),
                patch.object(executor, "load_config", return_value={"repositories": [{"url": repo, "scripts": {"build": "build.sh", "test": "test.sh", "deploy": "deploy.sh"}}]}),
                patch.object(executor, "LOGS_DIR", directory),
                patch.object(executor, "clone_or_update_repo", return_value=True),
                patch.object(executor, "run_script", side_effect=build) as script,
                self.assertLogs(executor.logger, level="ERROR") as logs,
            ):
                self.assertFalse(executor.process_job(job))
            self.assertEqual(script.call_count, 1)
            self.assertFalse(os.path.exists(job))
            self.assertIn("deployment_started=False", "\n".join(logs.output))
            self.assertIn("stage='build'", "\n".join(logs.output))
