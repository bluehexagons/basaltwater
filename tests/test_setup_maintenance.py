"""Tests for the setup-time maintenance reconciliation step."""

from __future__ import annotations

import subprocess
import io
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from common import setup_maintenance
from lib.config import SetupConfig
from lib.remote_utils import CommandTimeoutError


class TestSetupMaintenance(unittest.TestCase):
    def setUp(self) -> None:
        self.config = SetupConfig(
            host="host",
            username="agent",
            system_type="server_lite",
        )
        self.success = subprocess.CompletedProcess([], 0, "", "")

    def test_runs_system_and_user_jobs_for_non_root_setup(self) -> None:
        with (
            patch.object(setup_maintenance, "is_dry_run", return_value=False),
            patch.object(
                setup_maintenance,
                "_validated_script",
                side_effect=lambda path, _label: path,
            ),
            patch.object(setup_maintenance, "get_user_home", return_value="/home/agent"),
            patch.object(setup_maintenance.os, "geteuid", return_value=0),
            patch.object(setup_maintenance, "run", return_value=self.success) as run,
            patch.object(
                setup_maintenance,
                "_run_as_login_user",
                return_value=self.success,
            ) as run_as_user,
        ):
            setup_maintenance.run_setup_maintenance(self.config)

        run.assert_called_once_with(
            ["/usr/bin/python3", setup_maintenance._SYSTEM_CLEANUP_SCRIPT],
            check=False,
            capture_output=True,
            timeout=setup_maintenance._MAINTENANCE_TIMEOUT_SECONDS,
        )
        run_as_user.assert_called_once_with(
            "agent",
            "/home/agent",
            f"/usr/bin/python3 {setup_maintenance._USER_CACHE_SCRIPT}",
            check=False,
            capture_output=True,
        )

    def test_user_job_runs_directly_when_setup_is_the_target_user(self) -> None:
        with (
            patch.object(setup_maintenance, "is_dry_run", return_value=False),
            patch.object(
                setup_maintenance,
                "_validated_script",
                side_effect=lambda path, _label: path,
            ),
            patch.object(setup_maintenance, "get_user_home", return_value="/home/agent"),
            patch.object(
                setup_maintenance.pwd,
                "getpwnam",
                return_value=SimpleNamespace(pw_uid=1000),
            ),
            patch.object(setup_maintenance.os, "geteuid", return_value=1000),
            patch.object(setup_maintenance, "run", return_value=self.success) as run,
        ):
            setup_maintenance.run_user_cache_maintenance(self.config)

        run.assert_called_once_with(
            ["/usr/bin/python3", setup_maintenance._USER_CACHE_SCRIPT],
            check=False,
            capture_output=True,
            timeout=setup_maintenance._MAINTENANCE_TIMEOUT_SECONDS,
            cwd="/home/agent",
        )

    def test_root_setup_skips_user_job(self) -> None:
        config = SetupConfig(host="host", username="root", system_type="server_proxmox")
        with (
            patch.object(setup_maintenance, "is_dry_run", return_value=False),
            patch.object(
                setup_maintenance,
                "_validated_script",
                side_effect=lambda path, _label: path,
            ),
            patch.object(setup_maintenance, "run", return_value=self.success) as run,
            patch.object(setup_maintenance, "_run_as_login_user") as run_as_user,
        ):
            setup_maintenance.run_setup_maintenance(config)

        run.assert_called_once()
        run_as_user.assert_not_called()

    def test_timerless_failure_returns_false_and_requests_manual_retry(self) -> None:
        with (
            patch.object(setup_maintenance, "is_dry_run", return_value=False),
            patch.object(setup_maintenance, "get_user_home", return_value="/home/agent"),
            patch.object(setup_maintenance.os, "geteuid", return_value=0),
            patch.object(setup_maintenance, "_validated_script", return_value="/fixture/cleanup.py"),
            patch.object(setup_maintenance, "_run_as_login_user", return_value=subprocess.CompletedProcess([], 1, "", "cleanup failed")),
            patch("sys.stdout", new_callable=io.StringIO) as output,
        ):
            self.assertFalse(setup_maintenance.run_user_cache_maintenance(self.config))
        self.assertIn("no automatic retry is scheduled", output.getvalue())
        self.assertNotIn("scheduled job will retry", output.getvalue())

    def test_cleanup_failures_are_reported_without_aborting_setup(self) -> None:
        failed = SimpleNamespace(returncode=1, stdout="", stderr="cleanup failed")
        with (
            patch.object(setup_maintenance.os, "geteuid", return_value=0),
            patch.object(setup_maintenance, "is_dry_run", return_value=False),
            patch.object(
                setup_maintenance,
                "_validated_script",
                side_effect=lambda path, _label: path,
            ),
            patch.object(setup_maintenance, "get_user_home", return_value="/home/agent"),
            patch.object(setup_maintenance, "run", return_value=failed),
            patch.object(setup_maintenance, "_run_as_login_user", return_value=failed),
        ):
            setup_maintenance.run_setup_maintenance(self.config)

    def test_cleanup_timeouts_are_reported_without_aborting_setup(self) -> None:
        timeout = CommandTimeoutError("maintenance", 3600)
        with (
            patch.object(setup_maintenance.os, "geteuid", return_value=0),
            patch.object(setup_maintenance, "_run_as_login_user", side_effect=timeout),
            patch.object(setup_maintenance, "is_dry_run", return_value=False),
            patch.object(
                setup_maintenance,
                "_validated_script",
                side_effect=lambda path, _label: path,
            ),
            patch.object(setup_maintenance, "get_user_home", return_value="/home/agent"),
            patch.object(setup_maintenance, "run", side_effect=timeout),
        ):
            setup_maintenance.run_setup_maintenance(self.config)

    def test_dry_run_does_not_validate_or_execute_scripts(self) -> None:
        with (
            patch.object(setup_maintenance, "is_dry_run", return_value=True),
            patch.object(setup_maintenance, "_validated_script") as validate,
            patch.object(setup_maintenance, "run") as run,
            patch.object(setup_maintenance, "_run_as_login_user") as run_as_user,
        ):
            setup_maintenance.run_setup_maintenance(self.config)

        validate.assert_not_called()
        run.assert_not_called()
        run_as_user.assert_not_called()


if __name__ == "__main__":
    unittest.main()
