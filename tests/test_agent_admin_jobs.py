"""Approved maintenance stays fixed, detached, serialized, and fail-closed."""

from __future__ import annotations

import copy
import errno
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
from lib.privilege_execution import ExecutionResult
from lib.admin_status import JOB_ID_ENV
from lib.operation_state import OperationStateStore
from lib.config import SetupConfig
import remote_setup
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
            self.assertEqual(privilege_broker.execute(plan).state, "dispatched")
            self.assertEqual(run.call_args.args[0], plan["argv"])
            run.side_effect = subprocess.TimeoutExpired(plan["argv"], 60)
            self.assertEqual(privilege_broker.execute(plan).state, "uncertain")
        with patch.object(privilege_broker, "protected_path", side_effect=ValueError("Unsafe source")), patch.object(privilege_broker.subprocess, "run") as run:
            self.assertEqual(privilege_broker.execute(plan).state, "failed")
            run.assert_not_called()

    def test_recent_admin_requests_are_owned_and_survive_reopening(self):
        with tempfile.TemporaryDirectory() as directory:
            path = directory + "/requests.db"
            broker = privilege_broker.Broker(path, lambda: copy.deepcopy(policy()), Mock(return_value=ExecutionResult("dispatched", exit_code=0)))
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
                                  ("STATE_DIR", self.directory.name),
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
            self.assertEqual(value["stage"], "command")
            self.assertEqual(value["command_index"], 1)
            self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(kwargs["env"], admin_job.ENVIRONMENT)
            self.assertNotIn("shell", kwargs)
            return Mock(returncode=7)
        with patch.object(admin_job.subprocess, "run", side_effect=execute) as run:
            self.assertEqual(admin_job.run_action("update-packages"), 1)
            run.assert_called_once()
            self.assertEqual(run.call_args.args[0], [
                "/usr/bin/apt-get", "-o", "DPkg::Lock::Timeout=60",
                "-o", "APT::Update::Error-Mode=any", "update",
            ])
        value = json.loads(Path(self.path).read_text())
        self.assertEqual(value["status"], "failed")
        self.assertEqual(value["command_index"], 1)
        self.assertEqual(value["exit_code"], 7)
        self.assertEqual(value["failure"], "nonzero_exit")
        self.assertIsNotNone(value["finished"])

    def test_second_command_progress_is_durable_and_failures_keep_safe_categories(self):
        def execute(argv, **kwargs):
            value = json.loads(Path(self.path).read_text())
            return Mock(returncode=0 if value["command_index"] == 1 else -15)
        with patch.object(admin_job.subprocess, "run", side_effect=execute) as run:
            self.assertEqual(admin_job.run_action("update-packages"), 1)
            self.assertEqual(run.call_count, 2)
        value = json.loads(Path(self.path).read_text())
        self.assertEqual(value["command_index"], 2)
        self.assertEqual(value["failure"], "command_signal")
        with patch.object(admin_job, "unit_state", return_value="inactive"):
            self.assertEqual(admin_job.job_status()["latest"]["command_index"], 2)

    def test_source_rejection_is_reported_without_private_exception_details(self):
        with patch.object(admin_job, "trusted_refresh_source", side_effect=ValueError("root-secret")), \
                patch.object(admin_job.subprocess, "run") as run:
            self.assertEqual(admin_job.run_action("refresh"), 1)
        run.assert_not_called()
        value = json.loads(Path(self.path).read_text())
        self.assertEqual(value["stage"], "source_verification")
        self.assertEqual(value["failure"], "source_rejected")
        self.assertNotIn("root-secret", json.dumps(value))

    def test_refresh_failure_captures_only_its_own_setup_marker_and_survives_later_markers(self):
        marker = self.directory.name + "/setup-operation.json"
        config = SetupConfig(host="localhost", username="root", system_type="server_lite")
        def execute(argv, **kwargs):
            self.assertEqual(kwargs["env"][JOB_ID_ENV], json.loads(Path(self.path).read_text())["job_id"])
            with patch.dict(os.environ, kwargs["env"], clear=True):
                remote_setup._begin_setup_operation(config)
                remote_setup._transition_setup_operation("applying", {"step_index": 3, "step_count": 5,
                                                                      "step": "private configuration path"})
                remote_setup._record_setup_failure(RuntimeError("root-secret"))
            remote_setup._active_setup_operation[0].close()
            remote_setup._active_setup_operation = None
            return Mock(returncode=1)
        with patch.object(remote_setup, "SETUP_OPERATION_FILE", marker), \
                patch.object(remote_setup, "record_setup_activity"), \
                patch.object(admin_job.subprocess, "run", side_effect=execute):
            self.assertEqual(admin_job.run_action("refresh"), 1)
        saved = json.loads(Path(self.path).read_text())
        self.assertEqual(saved["setup_context"], {"job_id": saved["job_id"], "phase": "recovery",
                        "setup_stage": "steps", "step_index": 3, "step_count": 5, "error_type": "RuntimeError"})
        newer = json.loads(Path(marker).read_text())
        newer["context"]["maintenance_job_id"] = "b" * 32
        Path(marker).write_text(json.dumps(newer))
        with patch.object(admin_job, "unit_state", return_value="inactive"):
            result = admin_job.job_status()["latest"]
        self.assertEqual(result["setup_context"], saved["setup_context"])
        self.assertNotIn("private", json.dumps(result))
        self.assertNotIn("root-secret", json.dumps(result))

    def test_stale_legacy_and_invalid_markers_cannot_explain_a_new_job(self):
        marker = self.directory.name + "/setup-operation.json"
        store = OperationStateStore(marker)
        store.begin("target_setup", "server_lite", "applying", context={"maintenance_job_id": "a" * 32,
                            "setup_stage": "pre_steps", "error_type": "RuntimeError", "secret": "root-secret"})
        store.close()
        job = {"action": "refresh", "job_id": "b" * 32, "stage": "command"}
        self.assertIsNone(admin_job.current_setup_context(job))
        job["job_id"] = "a" * 32
        self.assertEqual(admin_job.current_setup_context(job)["setup_stage"], "pre_steps")
        with patch.object(admin_job, "OperationStateStore") as reader:
            self.assertIsNone(admin_job.current_setup_context({"action": "refresh", "stage": "command"}))
            self.assertIsNone(admin_job.current_setup_context({**job, "action": "refresh-preview"}))
            reader.assert_not_called()
        Path(marker).write_text('{"version": "root-secret"}')
        self.assertIsNone(admin_job.current_setup_context(job))
        Path(marker).unlink()
        Path(marker).symlink_to(self.path)
        with patch.object(admin_job, "protected_path", side_effect=ValueError("unsafe private marker")):
            self.assertIsNone(admin_job.current_setup_context(job))

    def test_process_loss_reports_correlated_context_without_replaying(self):
        marker = self.directory.name + "/setup-operation.json"
        store = OperationStateStore(marker)
        store.begin("target_setup", "server_lite", "applying", context={"maintenance_job_id": "a" * 32,
                    "setup_stage": "steps", "step_index": 2, "step_count": 4})
        store.close()
        Path(self.path).write_text(json.dumps({"action": "refresh", "status": "running", "started": 123,
                                             "job_id": "a" * 32, "stage": "command", "command_index": 1}))
        with patch.object(admin_job, "unit_state", return_value="inactive"), \
                patch.object(admin_job.subprocess, "run") as execute:
            result = admin_job.job_status()["latest"]
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(result["setup_context"]["step_index"], 2)
        execute.assert_not_called()

    def test_launch_error_and_timeout_have_fixed_categories(self):
        for exception, failure in ((OSError("root-secret"), "execution_unavailable"),
                                   (subprocess.TimeoutExpired("root-secret", 1), "command_timeout")):
            with self.subTest(failure=failure), patch.object(admin_job.subprocess, "run", side_effect=exception) as run:
                self.assertEqual(admin_job.run_action("update-packages"), 1)
                run.assert_called_once()
                value = json.loads(Path(self.path).read_text())
                self.assertEqual(value["failure"], failure)
                self.assertNotIn("root-secret", json.dumps(value))

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
            self.assertEqual(result["latest"]["failure"], "job_interrupted")
            self.assertNotIn("root-secret", json.dumps(result))
        with patch.object(admin_job, "unit_state", return_value="unavailable"):
            self.assertEqual(admin_job.job_status()["unit_state"], "unavailable")

    def test_unchanged_running_job_with_inactive_unit_is_interrupted(self):
        running = {"action": "refresh", "status": "running", "started": 123,
                   "finished": None, "exit_code": None, "job_id": "a" * 32}
        Path(self.path).write_text(json.dumps(running))
        with patch.object(admin_job, "unit_state", return_value="inactive"):
            result = admin_job.job_status()
        self.assertEqual(result["latest"]["status"], "interrupted")
        self.assertEqual(result["latest"]["job_id"], running["job_id"])

    def test_terminal_record_published_during_unit_query_wins(self):
        running = {"action": "refresh", "status": "running", "started": 123,
                   "finished": None, "exit_code": None, "job_id": "a" * 32}
        finished = {**running, "status": "succeeded", "finished": 124, "exit_code": 0}
        Path(self.path).write_text(json.dumps(running))

        def publish_terminal_then_report_inactive():
            Path(self.path).write_text(json.dumps(finished))
            return "inactive"

        with patch.object(admin_job, "unit_state", side_effect=publish_terminal_then_report_inactive):
            result = admin_job.job_status()
        self.assertEqual(result["latest"]["status"], "succeeded")
        self.assertEqual(result["latest"]["finished"], 124)
        self.assertIsNone(result["latest"]["failure"])

    def test_replacement_job_is_not_classified_by_previous_unit_observation(self):
        running = {"action": "refresh", "status": "running", "started": 123,
                   "finished": None, "exit_code": None, "job_id": "a" * 32}
        replacement = {**running, "started": 125, "job_id": "b" * 32}
        Path(self.path).write_text(json.dumps(running))

        def publish_replacement_then_report_inactive():
            Path(self.path).write_text(json.dumps(replacement))
            return "inactive"

        with patch.object(admin_job, "unit_state", side_effect=publish_replacement_then_report_inactive):
            result = admin_job.job_status()
        self.assertEqual(result["latest"], {"status": "unavailable"})
        self.assertEqual(result["unit_state"], "unavailable")

    def test_absent_job_history_is_empty_after_trusted_parent_check(self):
        parent = os.path.dirname(self.path)

        def validate_path(path, *, directory=False):
            if path == self.path:
                raise ValueError("Missing protected file")
            self.assertEqual(path, parent)
            self.assertTrue(directory)

        with patch.object(admin_job, "protected_path", side_effect=validate_path) as protect, \
                patch.object(admin_job, "unit_state", return_value="inactive") as unit_state:
            result = admin_job.job_status()
        self.assertEqual(result["latest"], {})
        self.assertEqual(result["unit_state"], "inactive")
        self.assertEqual(protect.call_count, 2)
        self.assertEqual(protect.call_args_list[0].args, (parent,))
        self.assertEqual(protect.call_args_list[0].kwargs, {"directory": True})
        self.assertEqual(protect.call_args_list[1].args, (parent,))
        self.assertEqual(protect.call_args_list[1].kwargs, {"directory": True})
        unit_state.assert_called_once()

    def test_unsafe_job_history_parent_is_unavailable(self):
        with patch.object(admin_job, "protected_path", side_effect=ValueError("Unsafe parent")) as protect, \
                patch.object(admin_job, "unit_state", return_value="inactive"):
            result = admin_job.job_status()
        self.assertEqual(result["latest"], {"status": "unavailable"})
        self.assertEqual(protect.call_count, 2)
        self.assertTrue(all(call.args == (os.path.dirname(self.path),)
                            and call.kwargs == {"directory": True} for call in protect.call_args_list))

    def test_job_history_lookup_errors_and_dangling_symlink_are_unavailable(self):
        real_lstat = os.lstat

        def fail_state_lstat(path):
            if path == self.path:
                raise OSError(errno.EIO, "I/O error")
            return real_lstat(path)

        with patch.object(admin_job.os, "lstat", side_effect=fail_state_lstat), \
                patch.object(admin_job, "unit_state", return_value="inactive"):
            result = admin_job.job_status()
        self.assertEqual(result["latest"], {"status": "unavailable"})
        self.assertEqual(result["unit_state"], "unavailable")

        Path(self.path).symlink_to(self.directory.name + "/missing-target")
        with patch.object(admin_job, "unit_state", return_value="inactive"):
            result = admin_job.job_status()
        self.assertEqual(result["latest"], {"status": "unavailable"})
        self.assertEqual(result["unit_state"], "unavailable")

    def test_new_record_published_after_inactive_observation_invalidates_status(self):
        previous = {"action": "refresh", "status": "succeeded", "started": 123,
                    "finished": 124, "exit_code": 0}
        running = {"action": "refresh", "status": "running", "started": 125,
                   "finished": None, "exit_code": None}
        Path(self.path).write_text(json.dumps(previous))

        def observe_inactive_then_publish():
            # Model an old inactive observation followed by service-start's
            # atomic publication of its new running record.
            Path(self.path).write_text(json.dumps(running))
            return "inactive"

        with patch.object(admin_job, "unit_state", side_effect=observe_inactive_then_publish):
            result = admin_job.job_status()
        self.assertEqual(result["unit_state"], "unavailable")
        self.assertEqual(result["latest"], {"status": "unavailable"})

    def test_new_job_published_after_empty_snapshot_invalidates_status(self):
        running = {"action": "refresh", "status": "running", "started": 125,
                   "finished": None, "exit_code": None, "job_id": "b" * 32}

        def publish_running_then_report_inactive():
            Path(self.path).write_text(json.dumps(running))
            return "inactive"

        with patch.object(admin_job, "unit_state", side_effect=publish_running_then_report_inactive):
            result = admin_job.job_status()
        self.assertEqual(result["unit_state"], "unavailable")
        self.assertEqual(result["latest"], {"status": "unavailable"})

    def test_busy_unit_state_does_not_trigger_a_second_history_read(self):
        running = {"action": "refresh", "status": "running", "started": 123,
                   "finished": None, "exit_code": None, "job_id": "a" * 32}
        Path(self.path).write_text(json.dumps(running))
        with patch.object(admin_job, "_read_job_record", wraps=admin_job._read_job_record) as read_record, \
                patch.object(admin_job, "unit_state", return_value="active"):
            result = admin_job.job_status()
        self.assertEqual(result["latest"]["status"], "running")
        read_record.assert_called_once_with()

    def test_corrupt_job_status_remains_unavailable(self):
        for values in ({"action": []}, {"action": "refresh", "status": "running", "started": float("nan")},
                       {"action": "refresh", "status": "running", "started": 10 ** 1000}):
            Path(self.path).write_text(json.dumps(values))
            with patch.object(admin_job, "unit_state", return_value="inactive"):
                self.assertEqual(admin_job.job_status()["latest"], {"status": "unavailable"})

    def test_invalid_diagnostics_are_rejected_and_legacy_results_remain_readable(self):
        legacy = {"action": "refresh", "status": "failed", "started": 123,
                  "finished": 124, "exit_code": 1}
        for diagnostics in ({}, {"stage": "root-secret"}, {"failure": "root-secret"},
                            {"failure": []}, {"command_index": True}, {"command_index": 0}):
            with self.subTest(diagnostics=diagnostics):
                Path(self.path).write_text(json.dumps({**legacy, **diagnostics}))
                with patch.object(admin_job, "unit_state", return_value="inactive"):
                    latest = admin_job.job_status()["latest"]
                if diagnostics:
                    self.assertEqual(latest, {"status": "unavailable"})
                else:
                    self.assertEqual(latest["exit_code"], 1)
                    self.assertIsNone(latest["stage"])
                    self.assertIsNone(latest["failure"])

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
        self.assertEqual(commands[0], [
            "/usr/bin/apt-get", "-o", "DPkg::Lock::Timeout=60",
            "-o", "APT::Update::Error-Mode=any", "update",
        ])
        self.assertEqual(commands[1], [
            "/usr/bin/apt-get", "-o", "DPkg::Lock::Timeout=60",
            "-o", "Dpkg::Options::=--force-confold", "--assume-yes", "upgrade",
        ])
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

    def test_availability_blocks_hard_killed_setup_but_allows_recovery_required_setup(self):
        marker = Path(self.directory.name) / "setup-operation.json"
        source = Path(self.directory.name) / "basaltwater"
        store = OperationStateStore(str(marker))
        record = store.begin("target_setup", "server_lite", "applying", context={
            "machine_type": "hardware", "system_type": "server_lite", "username": "root",
        })
        store.close()
        with patch.object(admin_job, "SOURCE", str(source)), \
                patch.object(admin_job, "can_manage_system_services", return_value=True), \
                patch.object(admin_job, "can_restart_system", return_value=True), \
                patch.object(admin_job, "read_os_release", return_value={"ID": "debian"}), \
                patch.object(admin_job.os.path, "isfile", return_value=True), \
                patch.object(admin_job, "read_json_file", return_value={"channel": "dev"}):
            blocked = self.availability_checker()
            self.assertIn("refresh", blocked)
            self.assertIn("interrupted or unknown target setup", blocked["refresh"])
            self.assertIn("refresh-preview", blocked)

            recovered = store.transition(
                record.operation_id, "recovery", status="recovery_required", expected_record=record,
            )
            blocked = self.availability_checker()
            self.assertNotIn("refresh", blocked)
            self.assertNotIn("refresh-preview", blocked)
            self.assertEqual(OperationStateStore(str(marker)).load(), recovered)

            activation_marker = Path(self.directory.name) / admin_job.SOURCE_ACTIVATION_MARKER_FILENAME
            activation_store = OperationStateStore(str(activation_marker))
            activation = activation_store.begin("source_refresh", str(source), "prepared", context={
                "repository_path": str(source), "backup_path": str(source.parent / "backup"),
                "staged_path": str(source.parent / "staged"), "runtime_data_names": [],
            })
            activation_store.close()
            blocked = self.availability_checker()
            self.assertIn("refresh", blocked)
            self.assertIn("source activation", blocked["refresh"])
            activation_store.complete(activation.operation_id, outcome="rolled_back")
            self.assertNotIn("refresh", self.availability_checker())

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

    def test_refresh_recovery_lookup_errors_make_actions_unavailable(self):
        source = Path(self.directory.name) / "source"
        markers = (source.parent / admin_job.SOURCE_ACTIVATION_MARKER_FILENAME,
                   source.parent / "setup-operation.json")
        real_lstat = os.lstat
        for marker in markers:
            for target in (str(marker), str(marker) + ".lock"):
                def lookup(path, *args, **kwargs):
                    if path == target:
                        raise OSError(errno.EIO, "private lookup details")
                    return real_lstat(path, *args, **kwargs)
                with self.subTest(target=target), patch.object(admin_job, "SOURCE", str(source)), \
                        patch.object(admin_job.os, "lstat", side_effect=lookup):
                    reason = admin_job._refresh_recovery_block_reason()
                self.assertIn("could not be verified", reason)
                self.assertNotIn("private lookup details", reason)
