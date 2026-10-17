"""Maintenance permission boundaries, durable budgets, setup and recovery."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
import warnings
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import basaltwater
from common import maintenance_agent_steps as steps
from lib import maintenance_agent as agent
from lib.agent_tasks import codex_command, validate_task
from lib.agent_probe import ProbeResult
from lib.atomic_io import write_json_atomic
from lib.config import SetupConfig
from lib.maintenance_agent_config import DEFAULTS, setup_policy, validate_policy
from plugins.post_setup import build_post_setup_steps


MODELS = [{"slug": "test-mini", "efforts": ["low"]}, {"slug": "test-large", "efforts": ["high"]}]
ISSUE = {"key": "service:x", "message": "A configured service failed", "severity": "warning"}


class MaintenancePolicyTest(unittest.TestCase):
    def test_boolean_flags_preserve_omission_and_polarity_without_deprecations(self) -> None:
        # Deprecated constructor options are rejected by Python 3.14.
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            parser, _, _ = basaltwater.create_basaltwater_parser()
        options = {
            "maintenance_agent": "maintenance-agent",
            "maintenance_agent_routines": "maintenance-agent-routines",
            "maintenance_agent_security": "maintenance-agent-security",
        }
        for command in (["setup", "server_proxmox", "pve.example", "root"],
                        ["patch", "pve.example", "root"],
                        ["agent", "maintenance-agent", "configure"]):
            with self.subTest(command=command):
                omitted = parser.parse_args(command)
                self.assertTrue(all(not hasattr(omitted, name) for name in options))
                for enabled in (True, False):
                    flags = ["--" + ("" if enabled else "no-") + flag for flag in options.values()]
                    selected = parser.parse_args(command + flags)
                    self.assertTrue(all(getattr(selected, name) is enabled for name in options))

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
        with patch.object(agent, "run_probe", return_value=ProbeResult(output='{"status":"completed","output":"done"}')) as run:
            self.assertEqual(agent._review_as_user(owner, DEFAULTS, {"units": []}, [ISSUE], selection)["status"], "completed")
        self.assertEqual(run.call_args.args[0][:5], ["/usr/sbin/runuser", "-u", "agent", "--", "/usr/bin/python3"])
        self.assertNotIn("shell", run.call_args.kwargs)
        self.assertNotIn("OPENAI_API_KEY", run.call_args.kwargs["env"])
        self.assertIn("issues", json.loads(run.call_args.kwargs["input_data"]))
        self.assertEqual(run.call_args.kwargs["max_bytes"], 16384)

    def test_invalid_worker_results_fail_the_review_without_crashing_the_supervisor(self) -> None:
        owner = SimpleNamespace(pw_name="agent", pw_dir="/home/agent")
        selection = {"model": "test-mini", "effort": "low"}
        for result in ({"status": [], "output": "done"}, {"status": "completed", "output": 42}, []):
            with self.subTest(result=result), patch.object(agent, "run_probe", return_value=ProbeResult(output=json.dumps(result))):
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
            real_stat, real_lstat, real_fstat = os.stat, os.lstat, os.fstat
            directory_owners = {home: account.pw_uid, str(root): 0,
                                str(control): account.pw_uid, str(config_file.parent): 0}
            def target_metadata(path: str | Path, metadata: os.stat_result) -> os.stat_result:
                uid = directory_owners.get(os.fspath(path))
                if uid is not None:
                    fields = list(metadata)
                    fields[4] = uid
                    return os.stat_result(fields)
                return metadata
            config = SetupConfig("example.test", "agent", "server_web", machine_type="vm", web_panel_port=8445)
            with (
                patch.multiple(steps, CONFIG_FILE=str(config_file), DATA_DIR=str(root), CONTROL_DIR=str(control)),
                patch.object(steps, "is_dry_run", return_value=False),
                patch.object(steps, "_identity", return_value=account),
                patch.object(steps.pwd, "getpwnam", return_value=account) as controller,
                patch.object(steps.os, "stat", side_effect=lambda path, *args, **kwargs: target_metadata(path, real_stat(path, *args, **kwargs))),
                patch.object(steps.os, "lstat", side_effect=lambda path, *args, **kwargs: target_metadata(path, real_lstat(path, *args, **kwargs))),
                patch.object(steps.os, "fstat", side_effect=lambda fd: target_metadata(os.readlink(f"/proc/self/fd/{fd}"), real_fstat(fd))),
                patch.object(steps.os, "fchown"),
                patch.object(steps, "write_json_atomic", side_effect=lambda path, value, **kwargs: write_json_atomic(path, value, mode=kwargs.get("mode", 0o600))) as write,
                patch.object(steps, "replace_units") as replace,
                patch("common.agent_steps.install_codex") as install,
            ):
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
                with patch.object(steps, "_load_baseline", return_value=({}, account)):
                    steps.configure_maintenance_agent(config)
                self.assertEqual(agent.load_policy(str(control / "settings.json"))["interval"], 120)
                self.assertEqual(agent.load_policy(str(control / "settings.json"))["daily_calls"], 0)
                self.assertEqual(write.call_args.kwargs["gid"], account.pw_gid + 1)
                self.assertEqual(write.call_args.kwargs["mode"], 0o640)
                self.assertEqual(agent.load_reports(str(root / "reports.json"))["reports"], [retained])
                large_record = {**retained, "review": {"status": "completed", "output": "a" * 600}}
                write_json_atomic(str(root / "reports.json"), {"version": 1, "context": {}, "reports": [large_record] * 5})
                with patch.object(steps, "_load_baseline", return_value=({}, account)), patch.object(steps, "maintenance_context", return_value={"units": [], "description": "a" * 2400}), patch.object(agent, "MAX_STATE_BYTES", 5000):
                    steps.configure_maintenance_agent(config)
                self.assertLessEqual((root / "reports.json").stat().st_size, 5000)
                self.assertEqual(agent.load_reports(str(root / "reports.json"))["reports"][-1], large_record)


class MaintenanceControllerMigrationTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.root = self.home / "maintenance"
        self.control = self.root / "control"
        self.root.mkdir(mode=0o750)
        self.control.mkdir(mode=0o700)
        self.config_file = self.home / "config/baseline.json"
        self.config_file.parent.mkdir(mode=0o755)
        self.maintenance = SimpleNamespace(pw_name="basaltwater-maintenance", pw_uid=101, pw_gid=101, pw_dir=str(self.home))
        self.panel = SimpleNamespace(pw_name="basaltwater-web-panel", pw_uid=102, pw_gid=102)
        self.owners = {str(self.home): 101, str(self.root): 0,
                       str(self.control): 101, str(self.config_file.parent): 0,
                       str(self.control / "settings.lock"): 101}
        self.retained = {"checked_at": "earlier", "issues": [], "routines": [], "review": {"status": "skipped"}}
        write_json_atomic(str(self.control / "settings.json"), {**DEFAULTS, "enabled": True, "interval": 120, "daily_calls": 0})
        write_json_atomic(str(self.root / "reports.json"), {"version": 1, "context": {}, "reports": [self.retained]})
        (self.root / "state.json").write_text("retained private counters")
        (self.control / "unrelated").write_text("operator file")
        (self.control / "settings.lock").touch(mode=0o600)
        real_fstat = os.fstat

        def metadata(fd: int) -> os.stat_result:
            fields = list(real_fstat(fd))
            path = os.readlink(f"/proc/self/fd/{fd}")
            if path in self.owners:
                fields[4] = self.owners[path]
            return os.stat_result(fields)

        def chown(fd: int, uid: int, gid: int) -> None:
            self.owners[os.readlink(f"/proc/self/fd/{fd}")] = uid

        patches = [
            patch.multiple(steps, CONFIG_FILE=str(self.config_file), DATA_DIR=str(self.root), CONTROL_DIR=str(self.control)),
            patch.object(steps, "is_dry_run", return_value=False),
            patch.object(steps, "_identity", return_value=self.maintenance),
            patch.object(steps, "_load_baseline"),
            patch.object(steps.pwd, "getpwnam", side_effect={self.maintenance.pw_name: self.maintenance, self.panel.pw_name: self.panel}.__getitem__),
            patch.object(steps.os, "fstat", side_effect=metadata),
            patch.object(steps.os, "fchown", side_effect=chown),
            patch.object(steps, "maintenance_context", return_value={"units": []}),
            patch.object(steps, "replace_units"),
            patch.object(steps, "run"),
            patch("common.agent_steps.install_codex"),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.write = patch.object(steps, "write_json_atomic", side_effect=lambda path, value, **kwargs: write_json_atomic(path, value, mode=kwargs.get("mode", 0o600))).start()
        self.addCleanup(patch.stopall)

    def configure(self, *, panel: bool, enabled: bool | None = None) -> None:
        steps.configure_maintenance_agent(SetupConfig("example.test", "root", "server_web", machine_type="vm",
                                                     web_panel_port=8445 if panel else None, maintenance_agent=enabled))

    def test_both_controller_transitions_preserve_policy_reports_and_private_state(self) -> None:
        for panel, controller in ((True, self.panel), (False, self.maintenance)):
            with self.subTest(panel=panel):
                self.configure(panel=panel)
                self.assertEqual(self.owners[str(self.control)], controller.pw_uid)
                policy = agent.load_policy(str(self.control / "settings.json"))
                self.assertTrue(policy["enabled"])
                self.assertEqual((policy["interval"], policy["daily_calls"]), (120, 0))
                settings_write = next(call for call in self.write.call_args_list[-3:] if call.args[0].endswith("settings.json"))
                self.assertEqual((settings_write.kwargs["uid"], settings_write.kwargs["gid"]), (controller.pw_uid, controller.pw_gid))
                self.assertEqual(agent.load_reports(str(self.root / "reports.json"))["reports"], [self.retained])
                self.assertEqual(self.write.call_args.kwargs["uid"], 0)
                self.assertEqual(self.write.call_args.kwargs["gid"], controller.pw_gid)
                self.assertEqual(self.owners[str(self.root)], 0)
                self.assertEqual((self.root / "state.json").read_text(), "retained private counters")
                self.assertEqual((self.control / "unrelated").read_text(), "operator file")
                self.assertEqual(self.owners[str(self.control / "settings.lock")], controller.pw_uid)
                real_lstat = Path.lstat

                def owned_metadata(path):
                    fields = list(real_lstat(path))
                    if os.fspath(path) in self.owners:
                        fields[4] = self.owners[os.fspath(path)]
                    return os.stat_result(fields)

                with (patch.object(agent.os, "geteuid", return_value=controller.pw_uid),
                      patch.object(Path, "lstat", owned_metadata)):
                    updated = agent.configure_policy({"routines": True}, path=str(self.control / "settings.json"))
                self.assertTrue(updated["routines"])

    def test_explicit_no_panel_and_no_agent_disables_previous_panel_policy(self) -> None:
        self.owners[str(self.control)] = self.panel.pw_uid
        self.configure(panel=False, enabled=False)
        self.assertFalse(agent.load_policy(str(self.control / "settings.json"))["enabled"])
        self.assertEqual(self.owners[str(self.control)], self.maintenance.pw_uid)
        self.assertEqual(agent.load_reports(str(self.root / "reports.json"))["reports"], [self.retained])

    def test_foreign_control_owner_is_rejected_without_overwriting_settings(self) -> None:
        self.owners[str(self.control)] = 999
        original = (self.control / "settings.json").read_bytes()
        with self.assertRaisesRegex(RuntimeError, "Unsafe maintenance directory"):
            self.configure(panel=True)
        self.write.assert_not_called()
        self.assertEqual((self.control / "settings.json").read_bytes(), original)

    def test_symlink_ancestors_are_rejected_before_mutation(self) -> None:
        alias = self.home / "alias"
        alias.symlink_to(self.home, target_is_directory=True)
        for field, path in (("DATA_DIR", alias / "maintenance"),
                            ("CONTROL_DIR", alias / "maintenance/control"),
                            ("CONFIG_FILE", alias / "config/baseline.json")):
            with self.subTest(field=field), patch.object(steps, field, str(path)):
                with self.assertRaisesRegex(ValueError, "symlink"):
                    self.configure(panel=True)
        self.write.assert_not_called()
        steps.os.fchown.assert_not_called()

    def test_unsafe_policy_locks_are_rejected_without_overwriting_settings(self) -> None:
        lock = self.control / "settings.lock"
        original = (self.control / "settings.json").read_bytes()
        for kind in ("symlink", "foreign", "public", "hardlink"):
            with self.subTest(kind=kind):
                lock.unlink()
                self.owners[str(lock)] = 101
                if kind == "symlink":
                    lock.symlink_to(self.control / "settings.json")
                else:
                    lock.touch(mode=0o644 if kind == "public" else 0o600)
                    if kind == "foreign":
                        self.owners[str(lock)] = 999
                    elif kind == "hardlink":
                        os.link(lock, self.control / "other-lock")
                with self.assertRaisesRegex(RuntimeError, "maintenance settings lock"):
                    self.configure(panel=True)
                self.write.assert_not_called()
                self.assertEqual((self.control / "settings.json").read_bytes(), original)

    def test_busy_policy_lock_stops_setup_without_changing_policy(self) -> None:
        original = (self.control / "settings.json").read_bytes()
        original_owners = self.owners.copy()
        with patch.object(steps.fcntl, "flock", side_effect=BlockingIOError):
            with self.assertRaisesRegex(RuntimeError, "being updated; retry setup"):
                self.configure(panel=True)
        self.write.assert_not_called()
        steps.os.fchown.assert_not_called()
        self.assertEqual(self.owners, original_owners)
        self.assertEqual((self.control / "settings.json").read_bytes(), original)


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
