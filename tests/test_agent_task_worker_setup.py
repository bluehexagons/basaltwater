"""Independent task worker setup respects account and service capabilities."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from common import agent_task_steps as steps
from lib.config import SetupConfig


class TaskWorkerSetupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = self.enterContext(tempfile.TemporaryDirectory())
        self.account = SimpleNamespace(pw_uid=1000, pw_gid=1000, pw_name="agent", pw_dir=self.home)
        real_stat = os.stat

        def account_stat(path: object, *args: object, **kwargs: object) -> os.stat_result:
            info = real_stat(path, *args, **kwargs)
            if isinstance(path, (str, os.PathLike)) and os.fspath(path) == self.home:
                fields = list(info)
                fields[4] = self.account.pw_uid
                return os.stat_result(fields)
            return info

        self.enterContext(patch.object(steps.os, "stat", side_effect=account_stat))
        self.config = SetupConfig(system_type="agent_vm", host="example.test", username="agent", machine_type="vm")
        self.enterContext(patch.object(steps, "can_manage_system_services", return_value=True))
        self.enterContext(patch.object(steps, "is_dry_run", return_value=False))
        self.enterContext(patch.object(steps.pwd, "getpwnam", return_value=self.account))
        self.enterContext(patch.object(steps, "UNIT_DIR", self.home))

    def test_render_keeps_provider_and_workspace_identity_non_root(self) -> None:
        content = steps.render_task_worker(self.account, revision="reviewed")
        self.assertIn("User=agent", content)
        self.assertIn('Environment="HOME=' + self.home + '"', content)
        self.assertIn("KillMode=control-group", content)
        self.assertIn("agent tasks worker", content)
        self.assertNotIn("User=root", content)
        self.assertIn("%", steps._quote("value%"))
        self.assertEqual(steps._quote("value%"), '"value%%"')

    def test_install_uses_transactional_unit_activation_without_starting_prompts(self) -> None:
        with patch.object(steps, "_worker_busy", return_value=False), patch.object(steps, "_active", return_value=False), patch.object(
            steps, "replace_units",
        ) as replace:
            steps.configure_agent_task_worker(self.config)
        units = replace.call_args.args[0]
        unit = next(iter(units))
        self.assertEqual(unit, f"basaltwater-agent-tasks-{self.account.pw_uid}.service")
        self.assertEqual(replace.call_args.kwargs["activate"], (unit,))
        self.assertEqual(replace.call_args.kwargs["unit_dir"], self.home)

    def test_container_root_and_dry_run_never_change_services(self) -> None:
        with patch.object(steps, "replace_units") as replace, patch.object(steps, "run") as run:
            with patch.object(steps, "can_manage_system_services", return_value=False):
                steps.configure_agent_task_worker(self.config)
            steps.configure_agent_task_worker(SetupConfig(system_type="server_web", host="example.test", username="root"))
            with patch.object(steps, "is_dry_run", return_value=True):
                steps.configure_agent_task_worker(self.config)
        replace.assert_not_called()
        run.assert_not_called()

    def test_busy_worker_refuses_refresh_before_any_service_is_stopped(self) -> None:
        with patch.object(steps, "_worker_busy", return_value=True), patch.object(steps, "run") as run, patch.object(
            steps, "replace_units",
        ) as replace, self.assertRaises(RuntimeError):
            steps.configure_agent_task_worker(self.config)
        run.assert_not_called()
        replace.assert_not_called()

    def test_unmanaged_unit_is_preserved(self) -> None:
        path = Path(self.home) / f"basaltwater-agent-tasks-{self.account.pw_uid}.service"
        path.write_text("[Service]\nExecStart=/another/service\n")
        with patch.object(steps, "replace_units") as replace, self.assertRaises(RuntimeError):
            steps.configure_agent_task_worker(self.config)
        replace.assert_not_called()
        self.assertIn("/another/service", path.read_text())

    def test_panel_handoff_only_touches_same_account_and_restores_on_failure(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="agent\n")
        with patch.object(steps, "_worker_busy", return_value=False), patch.object(steps, "_active", return_value=True), patch.object(
            steps, "run", return_value=completed,
        ) as run, patch.object(steps, "replace_units", side_effect=RuntimeError("activation failed")), self.assertRaises(RuntimeError):
            steps.configure_agent_task_worker(self.config)
        run.assert_any_call(["systemctl", "stop", "basaltwater-web-panel.service"])
        run.assert_any_call(["systemctl", "start", "basaltwater-web-panel.service"])
        with patch.object(steps, "_worker_busy", return_value=False), patch.object(steps, "_active", return_value=True), patch.object(
            steps, "run", return_value=SimpleNamespace(returncode=0, stdout="another-account\n"),
        ) as run, patch.object(steps, "replace_units"):
            steps.configure_agent_task_worker(self.config)
        self.assertEqual(run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
