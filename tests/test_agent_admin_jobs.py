"""Approved maintenance stays fixed, detached, serialized, and fail-closed."""

from __future__ import annotations

import copy
import fcntl
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from common.service_tools import admin_job, privilege_broker
from lib.admin_actions import ADMIN_ACTIONS, ADMIN_HELPER, ADMIN_TIMEOUT, ADMIN_UNIT, dispatch_argv
from lib.privilege_policy import operation_plan
from tests.test_agent_privilege_broker import policy


class AdminPolicyTests(unittest.TestCase):
    def test_every_action_is_fixed_and_individually_approved(self):
        candidate = policy()
        candidate["services"]["basaltwater-web-panel.service"] = "allow"
        for action in ADMIN_ACTIONS:
            with self.subTest(action=action):
                plan = operation_plan(candidate, 1000, "admin.run", {"action": action})
                self.assertEqual(plan["mode"], "approve")
                self.assertEqual(plan["argv"], dispatch_argv(action))
                self.assertIn(ADMIN_ACTIONS[action]["effect"], plan["effect"])
        for parameters in ({"action": "--help"}, {"action": []}, {"action": "refresh", "argv": ["sh"]}):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                operation_plan(candidate, 1000, "admin.run", parameters)
        candidate["reboot"] = "deny"
        for action in ("reboot", "shutdown"):
            with self.assertRaises(PermissionError):
                operation_plan(candidate, 1000, "admin.run", {"action": action})
        self.assertEqual(operation_plan(candidate, 1000, "admin.run", {"action": "cancel-shutdown"})["mode"], "approve")

    def test_long_jobs_are_managed_separately_from_panel_and_broker(self):
        argv = dispatch_argv("refresh")
        self.assertEqual(argv[-5:], ["--", "/usr/bin/python3", "-I", ADMIN_HELPER, "refresh"])
        self.assertIn(f"--unit={ADMIN_UNIT}", argv)
        self.assertIn(f"--property=RuntimeMaxSec={ADMIN_TIMEOUT}", argv)
        self.assertIn("--property=KillMode=control-group", argv)
        for flag in ("--wait", "--scope", "--pipe", "--pty", "--no-block"):
            self.assertNotIn(flag, argv)

    def test_detached_dispatch_does_not_report_job_completion(self):
        plan = operation_plan(policy(), 1000, "admin.run", {"action": "refresh"})
        with patch.object(privilege_broker, "protected_path"), patch.object(privilege_broker, "admin_availability", return_value={}), patch.object(privilege_broker.subprocess, "run", return_value=Mock(returncode=0)) as run:
            self.assertEqual(privilege_broker.execute(plan), "dispatched")
            self.assertEqual(run.call_args.args[0], plan["argv"])
            run.side_effect = subprocess.TimeoutExpired(plan["argv"], 60)
            self.assertEqual(privilege_broker.execute(plan), "uncertain")
        with patch.object(privilege_broker, "protected_path", side_effect=ValueError("Unsafe source")), patch.object(privilege_broker.subprocess, "run") as run:
            self.assertEqual(privilege_broker.execute(plan), "failed")
            run.assert_not_called()

    def test_recent_admin_requests_are_owned_and_survive_reopening(self):
        with tempfile.TemporaryDirectory() as directory:
            path = directory + "/requests.db"
            broker = privilege_broker.Broker(path, lambda: copy.deepcopy(policy()), Mock(return_value="dispatched"))
            try:
                request = broker.request(1000, "admin.run", {"action": "refresh"}, "Requested by administrator")
                with self.assertRaisesRegex(ValueError, "outstanding"):
                    broker.request(1000, "admin.run", {"action": "reboot"}, "Second click")
                with patch.object(privilege_broker, "job_status", return_value={"blocked": {}, "latest": {}, "unit_state": "inactive"}) as status:
                    result = broker.dispatch({"action": "admin-status"}, 1000)
                    self.assertEqual(result["requests"][0]["id"], request["id"])
                    self.assertEqual(result["requests"][0]["state"], "pending")
                    with self.assertRaises(PermissionError):
                        broker.dispatch({"action": "admin-status"}, 1001)
                    status.assert_called_once()
                broker.decide(request["id"], request["digest"], True, "operator")
                broker.execute_next()
                broker.db.close()
                broker = privilege_broker.Broker(path, policy, Mock())
                self.assertEqual(broker.status(request["id"], 1000)["state"], "dispatched")
                self.assertFalse(broker.execute_next())
            finally:
                broker.db.close()


class AdminJobTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = self.directory.name + "/job.json"
        self.source_validator = admin_job.trusted_refresh_source
        self.availability_checker = admin_job.availability
        for name, replacement in (("ADMIN_STATE", self.path), ("protected_path", Mock()),
                                  ("availability", Mock(return_value={})), ("trusted_refresh_source", Mock())):
            patcher = patch.object(admin_job, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(admin_job.os, "geteuid", return_value=0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_claim_is_persisted_before_effects_and_steps_stop_on_failure(self):
        def execute(argv, **kwargs):
            value = json.loads(Path(self.path).read_text())
            self.assertEqual(value["status"], "running")
            self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(kwargs["env"], admin_job.ENVIRONMENT)
            self.assertNotIn("shell", kwargs)
            return Mock(returncode=7)
        with patch.object(admin_job.subprocess, "run", side_effect=execute) as run:
            self.assertEqual(admin_job.run_action("update-packages"), 1)
            run.assert_called_once()
        value = json.loads(Path(self.path).read_text())
        self.assertEqual(value["status"], "failed")
        self.assertEqual(value["exit_code"], 7)
        self.assertIsNotNone(value["finished"])

    def test_check_then_reload_never_reloads_invalid_nginx_configuration(self):
        with patch.object(admin_job.subprocess, "run", return_value=Mock(returncode=1)) as run:
            admin_job.run_action("reload-web")
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0], ["/usr/sbin/nginx", "-t"])
        with patch.object(admin_job.subprocess, "run", return_value=Mock(returncode=0)) as run:
            self.assertEqual(admin_job.run_action("reload-web"), 0)
            self.assertEqual(run.call_count, 2)
            self.assertEqual(run.call_args.args[0][-2:], ["reload", "nginx.service"])

    def test_timeout_interruption_and_busy_lock_do_not_retry(self):
        with patch.object(admin_job.subprocess, "run", side_effect=subprocess.TimeoutExpired("apt", 1)) as run:
            self.assertEqual(admin_job.run_action("update-packages"), 1)
            run.assert_called_once()
        with patch.object(admin_job.subprocess, "run", side_effect=SystemExit(1)):
            with self.assertRaises(SystemExit):
                admin_job.run_action("refresh")
            self.assertEqual(json.loads(Path(self.path).read_text())["status"], "interrupted")
        with open(self.path + ".lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(admin_job.subprocess, "run") as run, self.assertRaises(BlockingIOError):
                admin_job.run_action("refresh")
            run.assert_not_called()

    def test_root_and_availability_are_rechecked_at_execution(self):
        with patch.object(admin_job.os, "geteuid", return_value=1000), patch.object(admin_job.subprocess, "run") as run:
            with self.assertRaises(PermissionError):
                admin_job.run_action("refresh")
            run.assert_not_called()
        with patch.object(admin_job, "availability", return_value={"refresh": "No managed channel"}), patch.object(admin_job.subprocess, "run") as run:
            self.assertEqual(admin_job.run_action("refresh"), 1)
            run.assert_not_called()

    def test_status_never_exposes_output_and_interrupted_jobs_are_not_clean(self):
        Path(self.path).write_text(json.dumps({"action": "refresh", "status": "running", "started": 123,
                                             "finished": None, "exit_code": None, "output": "root-secret"}))
        with patch.object(admin_job, "unit_state", return_value="inactive"):
            result = admin_job.job_status()
            self.assertEqual(result["latest"]["status"], "interrupted")
            self.assertNotIn("root-secret", json.dumps(result))
        with patch.object(admin_job, "unit_state", return_value="unavailable"):
            self.assertEqual(admin_job.job_status()["unit_state"], "unavailable")

    def test_corrupt_job_status_remains_unavailable(self):
        for values in ({"action": []}, {"action": "refresh", "status": "running", "started": float("nan")},
                       {"action": "refresh", "status": "running", "started": 10 ** 1000}):
            Path(self.path).write_text(json.dumps(values))
            with patch.object(admin_job, "unit_state", return_value="inactive"):
                self.assertEqual(admin_job.job_status()["latest"], {"status": "unavailable"})

    def test_availability_requires_managed_debian_source_and_matching_capabilities(self):
        with patch.object(admin_job, "can_manage_system_services", return_value=True), patch.object(admin_job, "can_restart_system", return_value=True), patch.object(admin_job, "read_os_release", return_value={"ID": "debian"}), patch.object(admin_job.os.path, "isfile", return_value=True), patch.object(admin_job, "read_json_file", return_value={"channel": "dev"}):
            self.assertEqual(self.availability_checker(), {})
            with patch.object(admin_job, "protected_path", side_effect=ValueError("Unsafe source")):
                blocked = self.availability_checker()
                self.assertEqual(set(blocked), {"refresh", "refresh-preview"})
            with patch.object(admin_job, "read_os_release", return_value={"ID": "arch"}), patch.object(admin_job, "can_restart_system", return_value=False):
                blocked = self.availability_checker()
                self.assertIn("update-packages", blocked)
                self.assertIn("shutdown", blocked)
        with patch.object(admin_job, "can_manage_system_services", return_value=False):
            self.assertEqual(set(self.availability_checker()), set(ADMIN_ACTIONS))

    def test_package_and_refresh_commands_do_not_reboot_or_remove_packages(self):
        commands = admin_job.commands("update-packages")
        self.assertEqual(commands[0][-1], "update")
        self.assertEqual(commands[1][-1], "upgrade")
        self.assertIn("Dpkg::Options::=--force-confold", commands[1])
        self.assertEqual(admin_job.commands("refresh-preview")[0][-2:], ["refresh", "--dry-run"])
        self.assertEqual(admin_job.commands("reboot"), [["/usr/sbin/shutdown", "-r", "+2"]])

    def test_snapshot_installation_has_refresh_actions_without_a_managed_channel(self):
        with patch.object(admin_job, "can_manage_system_services", return_value=True), \
                patch.object(admin_job, "can_restart_system", return_value=True), \
                patch.object(admin_job, "read_os_release", return_value={"ID": "debian"}), \
                patch.object(admin_job.os.path, "isfile", side_effect=lambda path: not path.endswith("channel.json")), \
                patch.object(admin_job, "read_installation_metadata", return_value={"installation_type": "setup-snapshot"}):
            self.assertEqual(self.availability_checker(), {})
            with patch.object(admin_job, "read_installation_metadata", return_value=None):
                self.assertIn("refresh", self.availability_checker())

    def test_refresh_source_validation_checks_git_hooks_and_rejects_symlinks(self):
        source = Path(self.directory.name) / "source"
        hooks = source / ".git/hooks"
        hooks.mkdir(parents=True)
        hook = hooks / "post-checkout"
        hook.write_text("untrusted")
        with patch.object(admin_job, "SOURCE", str(source)), patch.object(admin_job, "protected_path", side_effect=lambda path, **kw: (_ for _ in ()).throw(ValueError("Unsafe hook")) if path == str(hook) else None) as protect:
            with self.assertRaisesRegex(ValueError, "hook"):
                self.source_validator()
            self.assertIn(str(hooks), [call.args[0] for call in protect.call_args_list])
        link = source / "lib"
        link.symlink_to(hooks, target_is_directory=True)
        with patch.object(admin_job, "SOURCE", str(source)), patch.object(admin_job, "protected_path", side_effect=lambda path, **kw: (_ for _ in ()).throw(ValueError("Symlink")) if os.path.islink(path) else None):
            with self.assertRaisesRegex(ValueError, "Symlink"):
                self.source_validator()
