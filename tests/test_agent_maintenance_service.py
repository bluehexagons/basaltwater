"""Maintenance permission boundaries, durable budgets, setup and recovery."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import basaltwater
from common import maintenance_agent_steps as steps
from lib import maintenance_agent as agent
from lib.agent_tasks import codex_command, validate_task
from lib.atomic_io import write_json_atomic
from lib.config import SetupConfig
from lib.maintenance_agent_config import DEFAULTS, setup_policy, validate_policy
from plugins.post_setup import build_post_setup_steps


MODELS = [{"slug": "test-mini", "efforts": ["low"]}, {"slug": "test-large", "efforts": ["high"]}]
ISSUE = {"key": "service:x", "message": "A configured service failed", "severity": "warning"}


class MaintenancePolicyTest(unittest.TestCase):
    def test_flags_round_trip_setup_remote_and_saved_state(self) -> None:
        parser, _, _ = basaltwater.create_basaltwater_parser()
        args = parser.parse_args(["setup", "server_web", "test.example", "agent", "--maintenance-agent",
                                  "--maintenance-agent-model", "test-mini", "--maintenance-agent-daily-calls", "3",
                                  "--no-maintenance-agent-security", "--maintenance-agent-escalation-model", "test-large"])
        config = SetupConfig.from_args(args, "server_web")
        restored = SetupConfig.from_dict(config.host, config.system_type, config.to_dict())
        self.assertEqual(setup_policy(restored), {**DEFAULTS, "enabled": True, "model": "test-mini", "daily_calls": 3,
                                                 "security": False, "escalation_model": "test-large"})
        for command in (config.to_remote_args(), config.to_setup_command()):
            self.assertIn("--maintenance-agent", command)
            self.assertIn("--maintenance-agent-daily-calls 3", command)
            self.assertIn("--no-maintenance-agent-security", command)
        self.assertEqual(build_post_setup_steps(config, Mock())[-1][1], steps.configure_maintenance_agent)
        self.assertFalse(setup_policy(SetupConfig("test.example", "root", "server_web"))["enabled"])

    def test_policy_rejects_unbounded_or_injectable_inputs(self) -> None:
        for key, value in (("daily_calls", True), ("daily_calls", 25), ("timeout", 61), ("interval", 0),
                           ("model", "x\n--sandbox"), ("effort", "ultra"), ("escalation_model", "auto"),
                           ("routines", "true"), ("unknown", 1), ("model", [])):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_policy({key: value})

    def test_default_never_inherits_unknown_expensive_model_or_unsupported_effort(self) -> None:
        selection = agent.select_review(DEFAULTS, MODELS, critical=False, repeated=1, calls=0, escalations=0)
        self.assertEqual(selection["model"], "test-mini")
        self.assertEqual(agent.select_review(DEFAULTS, MODELS[1:], critical=False, repeated=1, calls=0, escalations=0)["status"], "blocked")
        self.assertEqual(agent.select_review({**DEFAULTS, "effort": "xhigh"}, MODELS, critical=False, repeated=1, calls=0, escalations=0)["status"], "blocked")

    def test_escalation_requires_explicit_model_reason_and_both_budgets(self) -> None:
        policy = {**DEFAULTS, "escalation_model": "test-large"}
        for critical, repeated in ((True, 1), (False, 3)):
            self.assertEqual(agent.select_review(policy, MODELS, critical=critical, repeated=repeated, calls=0, escalations=0)["role"], "escalation")
        self.assertEqual(agent.select_review(policy, MODELS, critical=False, repeated=1, calls=0, escalations=0)["role"], "routine")
        self.assertEqual(agent.select_review(policy, MODELS, critical=True, repeated=1, calls=0, escalations=1)["role"], "routine")
        self.assertEqual(agent.select_review(policy, MODELS, critical=True, repeated=1, calls=2, escalations=0)["status"], "skipped")


class MaintenanceSupervisorTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "control").mkdir(mode=0o700)
        self.owner = SimpleNamespace(pw_name="agent", pw_dir=str(self.root), pw_uid=os.geteuid(), pw_gid=os.getegid())
        self.baseline = {"version": 1, "username": "agent", "report_gid": os.getegid(), "context": {"units": [], "system_type": "server_web"}}
        self.policy = {**DEFAULTS, "enabled": True, "security": False, "escalation_model": "test-large"}
        write_json_atomic(str(self.root / "control/settings.json"), self.policy)
        real_fstat, real_lstat = os.fstat, Path.lstat
        def root_stat(value: os.stat_result) -> os.stat_result:
            fields = list(value)
            fields[4] = 0
            return os.stat_result(fields)
        def path_stat(path: Path) -> os.stat_result:
            return root_stat(real_lstat(path))
        patches = [patch.object(agent.os, "geteuid", return_value=0),
                   patch.object(agent, "_load_baseline", return_value=(self.baseline, self.owner)),
                   patch.object(agent.os, "fstat", side_effect=lambda descriptor: root_stat(real_fstat(descriptor))),
                   patch.object(Path, "lstat", path_stat),
                   patch.object(agent, "write_json_atomic", side_effect=lambda path, value, **kwargs: write_json_atomic(path, value, mode=kwargs.get("mode", 0o600))),
                   patch("lib.agent_tasks.codex_models", return_value=MODELS),
                   patch("lib.notifications.load_notification_configs_from_state", return_value=[Mock()])]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.notify = patch("lib.notifications.send_notification_safe", return_value=True).start()
        self.addCleanup(patch.stopall)

    def run_check(self, now: int, issues: list[dict[str, str]]) -> bool:
        with patch.object(agent, "collect_checks", return_value=issues):
            return agent.run_supervisor(config_path="unused", directory=str(self.root), now=now)

    def test_healthy_checks_skip_paid_calls_and_survive_restart(self) -> None:
        with patch.object(agent, "_review_as_user") as review:
            self.assertTrue(self.run_check(100000, []))
            self.assertFalse(self.run_check(100001, []))
            self.assertTrue(self.run_check(103600, []))
        review.assert_not_called()
        self.notify.assert_not_called()
        self.assertEqual(len(agent.load_reports(str(self.root / "reports.json"))["reports"]), 2)

    def test_recurring_findings_escalate_once_and_do_not_exceed_daily_attempts(self) -> None:
        with patch.object(agent, "_review_as_user", return_value={"status": "completed", "output": "Review"}) as review:
            for index in range(4):
                self.run_check(100000 + index * 3600, [ISSUE])
        self.assertEqual(review.call_count, 2)
        self.assertEqual(review.call_args_list[0].args[-1]["role"], "routine")
        self.assertEqual(review.call_args_list[1].args[-1]["role"], "escalation")
        self.assertEqual(self.notify.call_count, 1)
        state = agent._load_state(str(self.root / "state.json"))
        self.assertEqual((state["calls"], state["escalations"]), (2, 1))

    def test_critical_changed_findings_keep_deterministic_checks_after_budget_exhaustion(self) -> None:
        with patch.object(agent, "_review_as_user", return_value={"status": "failed", "output": "blocked"}) as review:
            for index in range(3):
                self.run_check(100000 + index * 3600, [{**ISSUE, "key": str(index), "severity": "error"}])
        self.assertEqual(review.call_count, 2)
        report = agent.load_reports(str(self.root / "reports.json"))["reports"][-1]
        self.assertEqual(report["review"]["status"], "skipped")
        self.assertEqual(len(report["issues"]), 1)

    def test_interrupted_model_attempt_is_charged_and_reported_on_restart(self) -> None:
        with patch.object(agent, "_review_as_user", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_check(100000, [ISSUE])
        state = agent._load_state(str(self.root / "state.json"))
        self.assertEqual(state["calls"], 1)
        with patch.object(agent, "_review_as_user", return_value={"status": "completed", "output": "done"}):
            self.run_check(103600, [ISSUE])
        report = agent.load_reports(str(self.root / "reports.json"))["reports"][0]
        self.assertEqual(report["check_status"], "interrupted")
        self.assertEqual(report["review"]["status"], "failed")

    def test_notification_failure_preserves_findings_retries_and_emits_recovery(self) -> None:
        self.notify.side_effect = [False, True, True]
        with patch.object(agent, "_review_as_user", return_value={"status": "completed", "output": "done"}):
            self.run_check(100000, [ISSUE])
            self.run_check(103600, [ISSUE])
            self.run_check(107200, [])
        self.assertEqual(self.notify.call_count, 3)
        self.assertEqual(self.notify.call_args.kwargs["state"], "resolved")
        self.assertEqual(len(agent.load_reports(str(self.root / "reports.json"))["reports"]), 3)

    def test_disabled_policy_consumes_wake_without_running_checks(self) -> None:
        write_json_atomic(str(self.root / "control/settings.json"), {**self.policy, "enabled": False})
        write_json_atomic(str(self.root / "control/wake.json"), {"id": "a" * 32})
        with patch.object(agent, "collect_checks") as collect:
            self.assertFalse(agent.run_supervisor(config_path="unused", directory=str(self.root), now=100000))
        collect.assert_not_called()
        self.assertFalse((self.root / "control/wake.json").exists())

    def test_corrupt_budget_state_is_not_reset_or_overwritten(self) -> None:
        self.run_check(100000, [])
        path = self.root / "state.json"
        content = path.read_text().replace('"calls": 0', '"calls": "invalid"')
        path.write_text(content)
        with self.assertRaises(RuntimeError):
            self.run_check(103600, [ISSUE])
        self.assertEqual(path.read_text(), content)

    def test_missing_notification_targets_do_not_claim_delivery(self) -> None:
        with patch("lib.notifications.load_notification_configs_from_state", return_value=[]), patch.object(agent, "_review_as_user", return_value={"status": "completed", "output": "done"}):
            self.run_check(100000, [ISSUE])
        self.notify.assert_not_called()
        self.assertEqual(agent.load_reports(str(self.root / "reports.json"))["reports"][-1]["notification"], "not configured")

    def test_public_budget_state_is_refused_without_resetting_counters(self) -> None:
        self.run_check(100000, [])
        (self.root / "state.json").chmod(0o644)
        with self.assertRaises(RuntimeError):
            self.run_check(103600, [ISSUE])

    def test_oversized_history_preserves_latest_check_and_budget(self) -> None:
        state = agent._initial_state()
        state["calls"] = 2
        record = {"checked_at": "latest", "issues": [], "routines": [], "review": {"status": "completed", "output": "é" * 8192}}
        state["reports"] = [dict(record) for _ in range(40)]
        with patch.object(agent, "MAX_STATE_BYTES", 100000):
            agent._persist(state, self.baseline, self.root)
        self.assertLess((self.root / "state.json").stat().st_size, 100000)
        restored = agent._load_state(str(self.root / "state.json"))
        self.assertEqual(restored["calls"], 2)
        self.assertEqual(restored["reports"][-1]["checked_at"], "latest")

    def test_public_export_budget_accounts_for_setup_context(self) -> None:
        state = agent._initial_state()
        state["calls"] = 2
        record = {"checked_at": "latest", "issues": [], "routines": [], "review": {"status": "completed", "output": "a" * 600}}
        state["reports"] = [dict(record) for _ in range(5)]
        baseline = {**self.baseline, "context": {"units": [], "description": "a" * 2400}}
        with patch.object(agent, "MAX_STATE_BYTES", 5000):
            agent._persist(state, baseline, self.root)
        self.assertLessEqual((self.root / "reports.json").stat().st_size, 5000)
        self.assertEqual(agent._load_state(str(self.root / "state.json"))["calls"], 2)
        self.assertEqual(agent.load_reports(str(self.root / "reports.json"))["reports"][-1]["checked_at"], "latest")

    def test_routines_only_dispatch_verified_jobs_once_daily(self) -> None:
        write_json_atomic(str(self.root / "control/settings.json"), {**self.policy, "routines": True, "daily_calls": 0})
        definition = {"uid": 0, "mode": 0o644, "content": "[Service]\nExecStart=/usr/bin/python3 /opt/basaltwater/common/service_tools/cleanup_maintenance.py\n"}
        with patch.object(agent, "snapshot_unit_file", side_effect=[definition, {**definition, "content": "ExecStart=/usr/bin/other-command\n"}]) as inspect, patch.object(agent, "_command", return_value=SimpleNamespace(returncode=0)) as dispatch:
            self.run_check(100000, [])
            self.run_check(103600, [])
        self.assertEqual(inspect.call_count, 2)
        dispatch.assert_called_once_with(["/usr/bin/systemctl", "start", "--no-block", "cleanup-maintenance.service"])
        routines = agent.load_reports(str(self.root / "reports.json"))["reports"][0]["routines"]
        self.assertEqual(routines, [{"unit": "cleanup-maintenance", "status": "requested"}, {"unit": "user-cache-maintenance", "status": "blocked"}])

    def test_unreadable_units_are_recorded_without_dispatching_or_interrupting_checks(self) -> None:
        write_json_atomic(str(self.root / "control/settings.json"), {**self.policy, "routines": True, "daily_calls": 0})
        with patch.object(agent, "snapshot_unit_file", side_effect=[ValueError("Unsafe unit"), None]), patch.object(agent, "_command") as dispatch:
            self.run_check(100000, [])
        dispatch.assert_not_called()
        report = agent.load_reports(str(self.root / "reports.json"))["reports"][-1]
        self.assertEqual(report["check_status"], "completed")
        self.assertEqual(report["routines"], [{"unit": "cleanup-maintenance", "status": "blocked"}])

    def test_corrupt_export_fields_are_refused_without_overwriting_evidence(self) -> None:
        self.run_check(100000, [])
        path = self.root / "reports.json"
        original = json.loads(path.read_text())
        for values in ({"context": None}, {"review": {"status": []}}, {"routines": [42]},
                       {"issues": [{**ISSUE, "severity": []}]}):
            corrupted = json.loads(json.dumps(original))
            if "context" in values:
                corrupted.update(values)
            else:
                corrupted["reports"][0].update(values)
            write_json_atomic(str(path), corrupted)
            content = path.read_bytes()
            with self.subTest(values=values), self.assertRaises(RuntimeError):
                agent.load_reports(str(path))
            self.assertEqual(path.read_bytes(), content)


class MaintenanceExecutionTest(unittest.TestCase):
    def test_review_is_dropped_to_named_account_without_a_shell_or_inherited_environment(self) -> None:
        owner = SimpleNamespace(pw_name="agent", pw_dir="/home/agent")
        selection = {"model": "test-mini", "effort": "low"}
        with patch.object(agent.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout='{"status":"completed","output":"done"}')) as run:
            self.assertEqual(agent._review_as_user(owner, DEFAULTS, {"units": []}, [ISSUE], selection)["status"], "completed")
        self.assertEqual(run.call_args.args[0][:5], ["/usr/sbin/runuser", "-u", "agent", "--", "/usr/bin/python3"])
        self.assertNotIn("shell", run.call_args.kwargs)
        self.assertNotIn("OPENAI_API_KEY", run.call_args.kwargs["env"])
        self.assertIn("issues", json.loads(run.call_args.kwargs["input"]))

    def test_invalid_worker_results_fail_the_review_without_crashing_the_supervisor(self) -> None:
        owner = SimpleNamespace(pw_name="agent", pw_dir="/home/agent")
        selection = {"model": "test-mini", "effort": "low"}
        for result in ({"status": [], "output": "done"}, {"status": "completed", "output": 42}, []):
            with self.subTest(result=result), patch.object(agent.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=json.dumps(result))):
                self.assertEqual(agent._review_as_user(owner, DEFAULTS, {"units": []}, [ISSUE], selection)["status"], "failed")

    def test_review_refuses_root_and_isolated_runner_is_inspect_only(self) -> None:
        with patch.object(agent.os, "geteuid", return_value=0), self.assertRaises(RuntimeError):
            agent.run_review({})
        with tempfile.TemporaryDirectory() as home, patch.object(agent.os, "geteuid", return_value=1000), patch.object(agent.pwd, "getpwuid", return_value=SimpleNamespace(pw_dir=home)), patch("lib.agent_tasks.execute_prompt", return_value={"status": "completed", "output": "done"}) as execute:
            agent.run_review({"home": home, "policy": DEFAULTS, "context": {"units": []}, "issues": [ISSUE], "selection": {"model": "test-mini", "effort": "low"}})
            self.assertTrue(execute.call_args.kwargs["isolated"])
            command = codex_command(validate_task(execute.call_args.args[0], home), "/test/codex")
            self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
            self.assertIn("--ephemeral", command)

    def test_setup_dry_run_and_oci_never_mutate_host(self) -> None:
        config = SetupConfig("example.test", "root", "server_web", machine_type="vm", maintenance_agent=True)
        with patch.object(steps, "is_dry_run", return_value=True), patch.object(steps, "run") as run, patch.object(steps, "replace_units") as replace:
            steps.configure_maintenance_agent(config)
        run.assert_not_called()
        replace.assert_not_called()
        config.machine_type = "oci"
        with self.assertRaises(ValueError):
            steps.configure_maintenance_agent(config)

    def test_panel_setup_preserves_policy_and_prepares_disabled_fixed_units(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            root = Path(home) / "maintenance"
            control = root / "control"
            config_file = Path(home) / "config/baseline.json"
            # The target account stays non-root even when CI itself runs as root.
            account = SimpleNamespace(pw_name="agent", pw_uid=1000, pw_gid=1000, pw_dir=home)
            real_stat = os.stat
            def account_stat(path: str | Path, *args: object, **kwargs: object) -> os.stat_result:
                metadata = real_stat(path, *args, **kwargs)
                if path == home:
                    fields = list(metadata)
                    fields[4] = account.pw_uid
                    return os.stat_result(fields)
                return metadata
            config = SetupConfig("example.test", "agent", "server_web", machine_type="vm", web_panel_port=8445)
            with patch.multiple(steps, CONFIG_FILE=str(config_file), DATA_DIR=str(root), CONTROL_DIR=str(control)), patch.object(steps, "is_dry_run", return_value=False), patch.object(steps, "_identity", return_value=account), patch.object(steps.pwd, "getpwnam", return_value=account) as controller, patch.object(steps.os, "stat", side_effect=account_stat), patch.object(steps.os, "chown"), patch.object(steps, "write_json_atomic", side_effect=lambda path, value, **kwargs: write_json_atomic(path, value, mode=kwargs.get("mode", 0o600))) as write, patch.object(steps, "replace_units") as replace, patch("common.agent_steps.install_codex") as install:
                steps.configure_maintenance_agent(config)
                self.assertFalse(agent.load_policy(str(control / "settings.json"))["enabled"])
                install.assert_not_called()
                units = replace.call_args.args[0]
                self.assertIn("NoNewPrivileges=true", units["basaltwater-maintenance-agent.service"])
                self.assertIn("wake.json", units["basaltwater-maintenance-agent.path"])
                self.assertEqual(len(replace.call_args.kwargs["activate"]), 2)
                write_json_atomic(str(control / "settings.json"), {**DEFAULTS, "interval": 120, "daily_calls": 0})
                retained = {"checked_at": "earlier", "issues": [], "routines": [], "review": {"status": "skipped"}}
                write_json_atomic(str(root / "reports.json"), {"version": 1, "context": {}, "reports": [retained]})
                controller.return_value = SimpleNamespace(pw_name="panel", pw_uid=account.pw_uid, pw_gid=account.pw_gid + 1)
                with patch.object(steps, "_load_baseline", return_value=({}, account)), patch.object(steps.Path, "stat", return_value=SimpleNamespace(st_uid=0, st_mode=0o40755)):
                    steps.configure_maintenance_agent(config)
                self.assertEqual(agent.load_policy(str(control / "settings.json"))["interval"], 120)
                self.assertEqual(agent.load_policy(str(control / "settings.json"))["daily_calls"], 0)
                self.assertEqual(write.call_args.kwargs["gid"], account.pw_gid + 1)
                self.assertEqual(write.call_args.kwargs["mode"], 0o640)
                self.assertEqual(agent.load_reports(str(root / "reports.json"))["reports"], [retained])
                large_record = {**retained, "review": {"status": "completed", "output": "a" * 600}}
                write_json_atomic(str(root / "reports.json"), {"version": 1, "context": {}, "reports": [large_record] * 5})
                with patch.object(steps, "_load_baseline", return_value=({}, account)), patch.object(steps.Path, "stat", return_value=SimpleNamespace(st_uid=0, st_mode=0o40755)), patch.object(steps, "maintenance_context", return_value={"units": [], "description": "a" * 2400}), patch.object(agent, "MAX_STATE_BYTES", 5000):
                    steps.configure_maintenance_agent(config)
                self.assertLessEqual((root / "reports.json").stat().st_size, 5000)
                self.assertEqual(agent.load_reports(str(root / "reports.json"))["reports"][-1], large_record)


class MaintenanceChecksTest(unittest.TestCase):
    def test_workload_context_never_serializes_credentials(self) -> None:
        config = SetupConfig("example.test", "agent", "server_web", homebox=["homebox.example.test"], password="private-fixture")
        with patch.object(steps.Path, "is_file", return_value=False):
            context = steps.maintenance_context(config)
        self.assertIn("nginx.service", context["units"])
        self.assertIn("homebox.service", context["units"])
        self.assertNotIn("private-fixture", json.dumps(context))
        self.assertNotIn("homebox.example.test", json.dumps(context))

    def test_collect_reports_capacity_service_and_security_evidence_without_logs(self) -> None:
        def command(arguments: list[str]) -> SimpleNamespace:
            if "--failed" in arguments:
                output = "broken.service loaded failed failed Broken\n"
            elif "show" in arguments:
                output = "LoadState=loaded\nActiveState=inactive\nResult=success\n"
            else:
                output = "permitrootlogin yes\npermitemptypasswords yes\n"
            return SimpleNamespace(returncode=0, stdout=output)
        with patch.object(agent.shutil, "disk_usage", return_value=SimpleNamespace(total=100, used=96)), patch.object(agent.os, "statvfs", return_value=SimpleNamespace(f_files=100, f_favail=5)), patch.object(agent.os.path, "exists", return_value=True), patch.object(agent.os, "stat", return_value=SimpleNamespace(st_mode=0o100666)), patch.object(agent, "_command", side_effect=command) as run:
            issues = agent.collect_checks({"units": ["nginx.service"]}, security=True)
        keys = {issue["key"] for issue in issues}
        self.assertTrue({"root-disk", "root-inodes", "failed:broken.service", "inactive:nginx.service", "reboot-pending", "ssh-root-login", "ssh-empty-passwords", "permissions:/etc/shadow"} <= keys)
        self.assertTrue(all("journalctl" not in call.args[0] for call in run.call_args_list))


if __name__ == "__main__":
    unittest.main()
