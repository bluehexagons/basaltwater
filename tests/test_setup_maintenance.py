"""Tests for the setup-time maintenance reconciliation step."""

from __future__ import annotations

import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from common import setup_maintenance
from lib.config import SetupConfig


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

    def test_cleanup_failures_are_reported_without_aborting_setup(self) -> None:
        failed = SimpleNamespace(returncode=1, stdout="", stderr="cleanup failed")
        with (
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
