"""Local kiosk CLI contracts; no package installation or real host mutations."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lib import kiosk_cli as cli


class KioskCliTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.root.chmod(0o700)
        self.parser = argparse.ArgumentParser()
        cli.add_kiosk_subparser(self.parser.add_subparsers(dest="command", required=True))
        self.state = dict(LoadState="loaded", ActiveState="inactive", SubState="dead",
                          UnitFileState="disabled", Result="success",
                          FragmentPath=str(self.root / cli.steps.SERVICE), DropInPaths="", NeedDaemonReload="no")
        self.enterContext(patch.object(cli.steps, "UNIT_DIR", self.root))
        self.enterContext(patch.object(cli.os, "geteuid", return_value=0))
        self.enterContext(patch.object(cli, "resolve_machine_type", return_value="hardware"))
        self.capability = self.enterContext(patch.object(cli, "can_manage_system_services", return_value=True))
        self.enterContext(patch.object(cli, "read_os_release", return_value={"ID": "debian", "VERSION_ID": "13"}))
        self.run = self.enterContext(patch.object(cli.subprocess, "run", side_effect=self.command))
        self.cleanup = self.enterContext(patch.object(cli.runtime, "cleanup_sessions", return_value=0))
        self.account = SimpleNamespace(pw_uid=12345, pw_gid=12345, pw_gecos=cli.runtime.ACCOUNT_COMMENT,
                                       pw_dir=str(cli.runtime.HOME), pw_shell="/usr/sbin/nologin")
        self.enterContext(patch.object(cli.pwd, "getpwnam", return_value=self.account))
        self.enterContext(patch.object(cli.grp, "getgrgid", return_value=SimpleNamespace(gr_name=cli.runtime.USER)))
        self.sessions = ""
        self.session_fields = {}

    def command(self, argv, **options):
        output = ""
        if argv[0].endswith("systemctl"):
            if argv[1] == "show":
                fields = self.state if argv[2] == cli.steps.SERVICE else dict(
                    LoadState="loaded", ActiveState="inactive", UnitFileState="disabled")
                output = "\n".join(f"{name}={value}" for name, value in fields.items())
            elif argv[1] in {"start", "restart"}:
                self.state.update(ActiveState="active", SubState="running", Result="success")
            elif argv[1] == "stop":
                self.state.update(ActiveState="inactive", SubState="dead")
            else:
                self.fail(f"Unexpected systemctl mutation: {argv}")
        elif argv[0].endswith("loginctl"):
            output = self.sessions if argv[1] == "list-sessions" else self.session_fields[argv[2]]
        elif argv[0].endswith("/id"):
            output = "12345\n"
        elif argv[0].endswith("/passwd"):
            output = f"{cli.runtime.USER} L 2026-10-25\n"
        else:
            self.fail(f"Unexpected host command: {argv}")
        return subprocess.CompletedProcess(argv, 0, output, "")

    def invoke(self, command, *options):
        stream = io.StringIO()
        args = self.parser.parse_args(["kiosk", command, *options])
        with redirect_stdout(stream):
            code = cli.run_kiosk_command(args)
        return code, json.loads(stream.getvalue())

    def healthy_dependencies(self):
        owned = {"content": cli.steps.MARKER + "[Service]\n"}
        self.enterContext(patch.object(cli.steps, "_owned_service", return_value=owned))
        self.enterContext(patch.object(cli, "_configuration", return_value="Valid configuration; destination redacted"))
        self.enterContext(patch.object(cli, "_executables", return_value="Executables available"))
        self.enterContext(patch.object(cli, "_devices", return_value="Console devices present"))
        self.enterContext(patch.object(cli, "_home", return_value="Private home"))
        self.enterContext(patch.object(cli, "_conflicts", return_value="No display manager conflicts"))

    def test_parser_commands_are_local_and_each_accept_json(self):
        for command in ("status", "doctor", "start", "stop", "restart"):
            with self.subTest(command=command):
                args = self.parser.parse_args(["kiosk", command, "--json"])
                self.assertEqual(args.kiosk_command, command)
                self.assertTrue(args.json)
        with redirect_stdout(io.StringIO()), patch("sys.stderr", new=io.StringIO()), self.assertRaises(SystemExit):
            self.parser.parse_args(["kiosk", "start", "--host", "remote.example"])

    def test_status_distinguishes_absent_disabled_running_and_failed(self):
        for load, active, enabled, result, expected, code in (
            ("not-found", "inactive", "", "", "absent", 0),
            ("not-found", "active", "enabled", "success", "running", 0),
            ("not-found", "activating", "enabled", "success", "activating", 0),
            ("not-found", "failed", "enabled", "exit-code", "failed", 1),
            ("loaded", "inactive", "disabled", "success", "disabled", 0),
            ("loaded", "active", "disabled", "success", "running", 0),
            ("loaded", "failed", "enabled", "exit-code", "failed", 1),
            ("loaded", "inactive", "disabled", "exit-code", "failed", 1),
        ):
            self.state.update(LoadState=load, ActiveState=active, UnitFileState=enabled, Result=result)
            with self.subTest(state=expected):
                actual, observation = self.invoke("status", "--json")
                self.assertEqual((actual, observation["state"]), (code, expected))
                self.assertIsNone(observation["browser_ready"])
                self.assertEqual(observation["service"]["Result"], result)
                self.assertEqual(observation["unit_missing"], load == "not-found")
                self.assertEqual(observation["account"]["uid"], 12345)
        self.cleanup.assert_not_called()
        self.assertTrue(all(call.args[0][1] in {"show", "-G", "list-sessions"} for call in self.run.call_args_list))

    def test_unprivileged_status_honestly_reports_inaccessible_sessions(self):
        with patch.object(cli.os, "geteuid", return_value=1000), \
             patch.object(cli, "_sessions", side_effect=PermissionError("logind permission denied")):
            code, result = self.invoke("status")
        self.assertEqual(code, 0)
        self.assertIn("permission denied", result["sessions_error"])
        self.assertEqual(result["state"], "disabled")

    def test_doctor_accepts_intentionally_stopped_boot_cli(self):
        self.healthy_dependencies()
        code, result = self.invoke("doctor", "--json")
        self.assertEqual(code, 0)
        self.assertTrue(result["healthy"])
        state = next(item for item in result["checks"] if item["name"] == "service_state")
        self.assertIn("Intentionally stopped", state["detail"])
        self.cleanup.assert_not_called()

    def test_doctor_failed_enabled_stopped_or_orphan_sessions_are_unhealthy(self):
        self.healthy_dependencies()
        for active, enabled, result, session in (
            ("failed", "enabled", "exit-code", ""),
            ("inactive", "enabled", "success", ""),
            ("inactive", "disabled", "success", f"c1 12345 {cli.runtime.USER} seat0 tty7\n"),
        ):
            self.state.update(ActiveState=active, UnitFileState=enabled, Result=result)
            self.sessions = session
            self.session_fields["c1"] = f"Name={cli.runtime.USER}\nTTY=tty7\nType=wayland\nState=active\nSeat=seat0\n"
            with self.subTest(active=active, enabled=enabled, orphan=bool(session)):
                code, diagnosis = self.invoke("doctor")
                self.assertEqual(code, 1)
                self.assertFalse(diagnosis["healthy"])

    def test_doctor_unprivileged_password_verification_is_inaccessible(self):
        self.healthy_dependencies()
        with patch.object(cli.os, "geteuid", return_value=1000):
            code, result = self.invoke("doctor")
        self.assertEqual(code, 1)
        check = next(item for item in result["checks"] if item["name"] == "password_lock")
        self.assertFalse(check["ok"])
        self.assertTrue(check["inaccessible"])
        self.assertFalse(any(call.args[0][0].endswith("passwd") for call in self.run.call_args_list))

    def test_doctor_actionable_platform_package_device_and_session_failures(self):
        self.healthy_dependencies()
        self.sessions = "2 1000 operator seat0 tty2\n"
        self.session_fields["2"] = "Name=operator\nTTY=tty2\nType=x11\nState=active\nSeat=seat0\n"
        with patch.object(cli, "read_os_release", return_value={"ID": "debian", "VERSION_ID": "12"}), \
             patch.object(cli, "_executables", side_effect=RuntimeError("Missing cage; rerun package setup")), \
             patch.object(cli, "_devices", side_effect=PermissionError("DRM directory inaccessible")):
            code, result = self.invoke("doctor")
        self.assertEqual(code, 1)
        failures = {item["name"]: item for item in result["checks"] if not item["ok"]}
        self.assertEqual(set(failures), {"platform", "executables", "devices", "logind_sessions"})
        self.assertTrue(failures["devices"]["inaccessible"])

    def test_doctor_detects_missing_package_executable(self):
        executables = cli._executables
        self.healthy_dependencies()
        with patch.object(cli, "_executables", side_effect=executables), \
             patch.object(cli.os, "access", side_effect=lambda path, mode: path != "/usr/bin/cage"):
            code, result = self.invoke("doctor")
        self.assertEqual(code, 1)
        check = next(item for item in result["checks"] if item["name"] == "executables")
        self.assertIn("/usr/bin/cage", check["detail"])

    def test_doctor_requires_character_devices_for_console_hardware(self):
        devices = cli._devices
        self.healthy_dependencies()
        for name in ("dev", "dev/dri", "dev/input"):
            (self.root / name).mkdir(mode=0o755)
        for name in ("dev/tty7", "dev/dri/card0", "dev/input/event0"):
            (self.root / name).touch()
        with patch.object(cli, "_devices", side_effect=devices), \
             patch.object(cli.steps, "VT", self.root / "dev/tty7"), \
             patch.object(cli.steps, "DRM_DIR", self.root / "dev/dri"), \
             patch.object(cli.steps, "INPUT_DIR", self.root / "dev/input"):
            code, result = self.invoke("doctor")
            self.assertEqual(code, 1)
            check = next(item for item in result["checks"] if item["name"] == "devices")
            self.assertIn("tty7", check["detail"])
            with patch.object(Path, "stat", return_value=SimpleNamespace(st_mode=stat.S_IFCHR | 0o600)):
                self.assertEqual(self.invoke("doctor")[0], 0)

    def test_doctor_checks_dedicated_home_owner_and_mode(self):
        home = cli._home
        self.healthy_dependencies()
        private_home = self.root / "home"
        private_home.mkdir(mode=0o700)
        self.account.pw_dir = str(private_home)
        with patch.object(cli.runtime, "HOME", private_home), patch.object(cli, "_home", side_effect=home):
            for uid, mode, expected in ((12345, 0o40700, 0), (12345, 0o40755, 1), (54321, 0o40700, 1)):
                with self.subTest(uid=uid, mode=mode), \
                     patch.object(cli.os, "fstat", return_value=SimpleNamespace(st_uid=uid, st_mode=mode)):
                    code, result = self.invoke("doctor")
                    self.assertEqual(code, expected)
                    if expected:
                        check = next(item for item in result["checks"] if item["name"] == "home")
                        self.assertIn("0700", check["detail"])

    def test_mutations_require_root_and_service_capability_before_commands(self):
        for command in ("start", "stop", "restart"):
            with self.subTest(command=command), patch.object(cli.os, "geteuid", return_value=1000):
                code, result = self.invoke(command)
                self.assertEqual(code, 1)
                self.assertIn("requires root", result["error"])
        self.run.assert_not_called()
        self.capability.return_value = False
        code, result = self.invoke("stop")
        self.assertEqual(code, 1)
        self.assertIn("persistent systemd", result["error"])
        self.run.assert_not_called()

    def test_mutations_refuse_foreign_overridden_or_stale_units(self):
        self.healthy_dependencies()
        for changes in ({"FragmentPath": "/usr/lib/systemd/system/foreign.service"},
                        {"DropInPaths": "/etc/systemd/system/override.conf"},
                        {"NeedDaemonReload": "yes"}):
            original = dict(self.state)
            self.state.update(changes)
            with self.subTest(changes=changes):
                code, result = self.invoke("stop")
                self.assertEqual(code, 1)
                self.assertIn("overridden, stale", result["error"])
            self.state = original
        self.cleanup.assert_not_called()
        self.assertTrue(all(call.args[0][1] == "show" for call in self.run.call_args_list))
        with patch.object(cli.steps, "_owned_service", side_effect=RuntimeError("Unmanaged kiosk file")):
            self.assertEqual(self.invoke("start")[0], 1)

    def test_start_restart_preflight_and_preserve_disabled_boot_enablement(self):
        self.healthy_dependencies()
        for command in ("start", "restart"):
            code, result = self.invoke(command, "--json")
            self.assertEqual(code, 0)
            self.assertEqual(result["state"], "running")
            self.assertEqual(self.state["UnitFileState"], "disabled")
            self.assertIsNone(result["browser_ready"])
        mutations = [call.args[0][1] for call in self.run.call_args_list if call.args[0][1] in {"start", "restart"}]
        self.assertEqual(mutations, ["start", "restart"])
        for call in self.run.call_args_list:
            self.assertGreater(call.kwargs["timeout"], 0)
            self.assertLessEqual(call.kwargs["timeout"], 150)

    def test_start_refuses_invalid_configuration_without_service_mutation(self):
        self.healthy_dependencies()
        with patch.object(cli, "_configuration", side_effect=RuntimeError("Managed configuration invalid")):
            code, result = self.invoke("start")
        self.assertEqual(code, 1)
        self.assertFalse(result["healthy"])
        self.assertTrue(all(call.args[0][1] not in {"start", "restart"} for call in self.run.call_args_list))

    def test_stop_checks_cleanup_even_when_systemctl_succeeded_but_stoppost_failed(self):
        self.healthy_dependencies()
        self.state.update(ActiveState="failed", Result="exit-code")
        self.cleanup.side_effect = RuntimeError("PAM scopes still present")
        code, result = self.invoke("stop")
        self.assertEqual(code, 1)
        self.assertIn("PAM scopes", result["error"])
        self.cleanup.assert_called_once_with()
        self.assertEqual(self.state["ActiveState"], "inactive")

    def test_stop_success_checks_final_state_and_retains_boot_setting(self):
        self.healthy_dependencies()
        self.state.update(ActiveState="active", UnitFileState="enabled")
        code, result = self.invoke("stop")
        self.assertEqual(code, 0)
        self.assertEqual(result["state"], "stopped")
        self.cleanup.assert_called_once_with()
        self.assertEqual(self.state["UnitFileState"], "enabled")
        self.state.update(Result="exit-code")
        code, result = self.invoke("stop")
        self.assertEqual(code, 1)
        self.assertIn("successful inactive", result["error"])

    def test_stop_success_with_failed_producer_still_checks_pam_cleanup(self):
        self.healthy_dependencies()
        normal = self.command

        def failed_stoppost(argv, **options):
            if argv[1] == "stop":
                self.state.update(ActiveState="failed", SubState="failed", Result="exit-code")
                return subprocess.CompletedProcess(argv, 0, "", "")
            return normal(argv, **options)

        self.run.side_effect = failed_stoppost
        code, result = self.invoke("stop")
        self.assertEqual(code, 1)
        self.assertEqual(result["state"], "failed")
        self.cleanup.assert_called_once_with()

    def test_stop_failure_or_timeout_with_active_producer_never_cleans_sessions(self):
        self.healthy_dependencies()
        self.state.update(ActiveState="active")
        normal = self.command
        for timeout in (False, True):
            def failed_stop(argv, **options):
                if argv[1] == "stop":
                    if timeout:
                        raise subprocess.TimeoutExpired(argv, 90)
                    return subprocess.CompletedProcess(argv, 1, "", "")
                return normal(argv, **options)

            with self.subTest(timeout=timeout):
                self.run.side_effect = failed_stop
                code, result = self.invoke("stop")
                self.assertEqual(code, 1)
                self.assertIn("timed out" if timeout else "exit 1", result["error"])
                self.assertIn("cleanup was not attempted", result["error"])
                self.cleanup.assert_not_called()

    def test_stop_never_cleans_a_changed_dedicated_account(self):
        self.healthy_dependencies()
        self.account.pw_gecos = "Other account"
        code, result = self.invoke("stop")
        self.assertEqual(code, 1)
        self.assertIn("account identity changed", result["error"])
        self.cleanup.assert_not_called()

    def test_failed_systemctl_and_timeout_propagate_without_output_or_recovery(self):
        self.healthy_dependencies()
        normal = self.command

        def failure(argv, **options):
            if argv[1] in {"start", "restart"}:
                return subprocess.CompletedProcess(argv, 1, "secret page URL", "private browser info")
            return normal(argv, **options)

        self.run.side_effect = failure
        for command in ("start", "restart"):
            for service_result in ("success", "start-limit-hit"):
                self.state["Result"] = service_result
                with self.subTest(command=command, service_result=service_result):
                    code, result = self.invoke(command)
                    self.assertEqual(code, 1)
                    self.assertIn("exit 1", result["error"])
                    self.assertNotIn("secret", json.dumps(result))
                    self.assertEqual("reset-failed" in result["error"], service_result == "start-limit-hit")
                    if service_result == "start-limit-hit":
                        self.assertIn("inspect", result["error"])
                        self.assertIn(cli.steps.SERVICE, result["error"])
        self.assertFalse(any(call.args[0][1] == "reset-failed" for call in self.run.call_args_list))
        self.run.side_effect = subprocess.TimeoutExpired("systemctl", 10, output="private URL")
        code, result = self.invoke("status")
        self.assertEqual(code, 1)
        self.assertIn("timed out", result["error"])
        self.assertNotIn("private", json.dumps(result))

    def test_configuration_validates_selected_generation_and_redacts_destination(self):
        configuration_check = cli._configuration
        self.healthy_dependencies()
        config_dir = self.root / "config"
        config_dir.mkdir()
        config_dir.chmod(0o755)
        source = self.root / "source"
        (source / "desktop").mkdir(parents=True)
        (source / "desktop/kiosk_runtime.py").touch()
        (source / "desktop/kiosk_runtime.py").chmod(0o644)
        with patch.object(cli.steps, "CONFIG_DIR", config_dir):
            files, unit = cli.steps.render_configuration("https://private.example/secret#token", source)
            for path, value in files.items():
                path.write_text(value)
                path.chmod(0o644)
            snapshot = cli.steps.snapshot_unit_file

            def root_snapshot(path):
                value = snapshot(path)
                if value is not None:
                    value["uid"] = 0
                return value

            real_stat = cli.os.fstat

            def root_stat(fd):
                info = real_stat(fd)
                fields = list(info)
                fields[4] = 0
                return os.stat_result(fields)

            with patch.object(cli, "_configuration", side_effect=configuration_check), \
                 patch.object(cli.steps, "_owned_service", return_value={"content": unit}) as owned, \
                 patch.object(cli.steps, "snapshot_unit_file", side_effect=root_snapshot), \
                 patch.object(cli.os, "fstat", side_effect=root_stat), \
                 patch.object(cli.runtime, "configuration", return_value={"future_schema": True}) as configuration:
                code, result = self.invoke("doctor")
                self.assertEqual(code, 0)
                self.assertNotIn("private.example", json.dumps(result))
                self.assertEqual(configuration.call_args.args[0].suffix, ".json")
                configuration.side_effect = ValueError("secret destination URL")
                code, result = self.invoke("doctor")
                self.assertEqual(code, 1)
                self.assertIn("configuration is invalid", json.dumps(result))
                self.assertNotIn("secret destination", json.dumps(result))
                owned.return_value = {"content": unit.replace("ExecStart=", "ExecStart=/foreign ")}
                self.assertEqual(self.invoke("doctor")[0], 1)


if __name__ == "__main__":
    unittest.main()
