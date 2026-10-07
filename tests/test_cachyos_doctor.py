"""Portable qualification contract tests; no live desktop or system mutations."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
import io
import json
from pathlib import Path
import stat
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import basaltwater
from lib import cachyos_doctor as doctor
from lib.local_cli import run_local_command


class CapabilityContractTests(unittest.TestCase):
    def setUp(self):
        self.record = doctor.CapabilityResult(
            "desktop.portal", "deferred", "Qualification pending.", "alice", "wayland",
            "2026-09-14T12:00:00+00:00", origin="portal",
        )

    def test_complete_json_roundtrip_preserves_unknowns(self):
        restored = doctor.CapabilityResult.from_dict(json.loads(json.dumps(self.record.to_dict())))
        self.assertEqual(restored, self.record)
        self.assertIsNone(restored.selected)
        self.assertIsNone(restored.interactive_required)
        self.assertIsNone(restored.last_verified)

    def test_invalid_contract_values_fail_closed(self):
        for values in (
            {"schema_version": 2}, {"schema_version": True}, {"selected": 1},
            {"interactive_required": "yes"}, {"name": "../portal"},
            {"owner": "alice\n"}, {"session": "remote"}, {"state": "ready"},
            {"origin": "arbitrary"}, {"sensitivity": "screenshot"},
            {"reason": "secret\nvalue"}, {"reason": "x" * 513},
            {"version": "secret\nvalue"}, {"version": "1" * 129},
            {"observed_at": None}, {"observed_at": "2026-09-14"},
            {"last_verified": "yesterday"},
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                replace(self.record, **values)

    def test_partial_or_extended_records_are_rejected(self):
        for value in ({}, {**self.record.to_dict(), "credentials": "secret"}):
            with self.assertRaises(ValueError):
                doctor.CapabilityResult.from_dict(value)


class ProbeTests(unittest.TestCase):
    def test_bounded_runner_uses_sanitized_local_environment(self):
        def stream(command, **kwargs):
            self.assertEqual(command, ["/usr/bin/pacman", "-Q", "--", "kwin"])
            self.assertEqual(kwargs["timeout"], doctor.PROBE_TIMEOUT)
            self.assertEqual(kwargs["cwd"], "/")
            self.assertEqual(kwargs["env"]["DBUS_SESSION_BUS_ADDRESS"], "unix:path=/run/user/1000/bus")
            self.assertEqual(kwargs["env"]["COREPACK_ENABLE_NETWORK"], "0")
            self.assertEqual(kwargs["env"]["COREPACK_ENABLE_PROJECT_SPEC"], "0")
            for key in ("TOKEN", "LD_PRELOAD", "HTTP_PROXY", "HOME"):
                self.assertNotIn(key, kwargs["env"])
            kwargs["on_output"]("kwin 6.0.0-1\n")
            return 0

        with patch.dict("os.environ", {"TOKEN": "secret", "DBUS_SESSION_BUS_ADDRESS": "tcp:host=remote"}), \
                patch.object(doctor, "run_streamed", side_effect=stream):
            self.assertEqual(doctor._probe(["/usr/bin/pacman", "-Q", "--", "kwin"], 1000),
                             ("ok", "kwin 6.0.0-1\n"))

    def test_output_overflow_aborts_stream_and_discards_output(self):
        def stream(_command, **kwargs):
            kwargs["on_output"]("private data\n")
            kwargs["on_output"]("x" * doctor.OUTPUT_LIMIT)
            self.fail("Output callback must abort the bounded runner")

        with patch.object(doctor, "run_streamed", side_effect=stream):
            self.assertEqual(doctor._probe(["/usr/bin/pacman"], 1000), ("output-limit", ""))

    def test_empty_upgrade_query_is_success_but_other_exit_one_results_fail(self):
        with patch.object(doctor, "run_streamed", return_value=1):
            self.assertEqual(doctor._probe(["/usr/bin/pacman", "-Qu"], 1000), ("ok", ""))
            self.assertEqual(doctor._probe(["/usr/bin/pacman", "-Q", "--", "missing"], 1000), ("error", ""))

        def stream(_command, **kwargs):
            kwargs["on_output"]("error: private database path\n")
            return 1

        with patch.object(doctor, "run_streamed", side_effect=stream):
            self.assertEqual(doctor._probe(["/usr/bin/pacman", "-Qu"], 1000), ("error", ""))

    def test_errors_never_publish_raw_output(self):
        for error, expected in ((FileNotFoundError("secret"), "missing"),
                                (TimeoutError("secret"), "error"),
                                (PermissionError("secret"), "error")):
            with self.subTest(error=error), patch.object(doctor, "run_streamed", side_effect=error):
                self.assertEqual(doctor._probe(["/usr/bin/pacman"], 1000), (expected, ""))

        def stream(_command, **kwargs):
            kwargs["on_output"]("secret")
            return 1

        with patch.object(doctor, "run_streamed", side_effect=stream):
            self.assertEqual(doctor._probe(["/usr/bin/pacman"], 1000), ("error", ""))

    def test_socket_ownership_and_symlinks(self):
        for socket_mode, parent_mode, socket_uid, parent_uid, expected in (
            (stat.S_IFSOCK | 0o600, stat.S_IFDIR | 0o700, 1000, 1000, True),
            (stat.S_IFLNK | 0o777, stat.S_IFDIR | 0o700, 1000, 1000, False),
            (stat.S_IFSOCK | 0o600, stat.S_IFLNK | 0o700, 1000, 1000, False),
            (stat.S_IFSOCK | 0o600, stat.S_IFDIR | 0o777, 1000, 1000, False),
            (stat.S_IFSOCK | 0o600, stat.S_IFDIR | 0o700, 2000, 1000, False),
            (stat.S_IFSOCK | 0o600, stat.S_IFDIR | 0o700, 1000, 2000, False),
        ):
            with self.subTest(socket_mode=socket_mode, parent_mode=parent_mode, socket_uid=socket_uid), \
                    patch.object(Path, "lstat", side_effect=[
                        SimpleNamespace(st_mode=socket_mode, st_uid=socket_uid),
                        SimpleNamespace(st_mode=parent_mode, st_uid=parent_uid),
                    ]):
                self.assertEqual(doctor._owned_socket(Path("/run/user/1000/bus"), 1000), expected)


class DoctorTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.uid = stack.enter_context(patch.object(doctor.os, "getuid", return_value=1000))
        stack.enter_context(patch.object(doctor.os, "geteuid", return_value=1000))
        stack.enter_context(patch.object(doctor.pwd, "getpwuid", return_value=SimpleNamespace(pw_name="alice")))
        self.supported = stack.enter_context(patch.object(doctor, "is_cachyos", return_value=True))
        stack.enter_context(patch.object(doctor.platform, "machine", return_value="x86_64"))
        self.socket = stack.enter_context(patch.object(doctor, "_owned_socket", return_value=True))
        self.probe = stack.enter_context(patch.object(doctor, "_probe", side_effect=self.healthy_probe))
        stack.enter_context(patch.object(doctor, "saved_selection", return_value=None))
        stack.enter_context(patch("lib.cachyos_health.collect_host_health", return_value=[]))
        stack.enter_context(patch("lib.cachyos_health.collect_network_health", return_value=[]))
        stack.enter_context(patch.dict("os.environ", {
            "XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-0",
            "TOKEN": "secret-value", "DISPLAY": "secret-display",
        }, clear=True))

    @staticmethod
    def healthy_probe(command, _uid):
        if command[0] == "/usr/bin/pacman":
            return "ok", f"{command[-1]} 6.0.0-1\n"
        if command[0] == "/usr/bin/busctl":
            return "ok", "b true\n"
        return "ok", "active\n"

    def test_healthy_prerequisites_never_claim_automation_or_consent(self):
        report = doctor.collect_cachyos_doctor()
        records = {item["name"]: item for item in report["capabilities"]}
        self.assertEqual(records["session.portal"]["state"], "available")
        self.assertEqual(records["package.kwin"]["version"], "6.0.0-1")
        for name in ("browser.playwright", "desktop.portal", "desktop.accessibility"):
            self.assertEqual(records[name]["state"], "deferred")
        self.assertFalse(records["browser.playwright"]["selected"])
        for name in ("desktop.portal", "desktop.accessibility"):
            self.assertIsNone(records[name]["selected"])
            self.assertIn("--native", records[name]["reason"])
        for item in records.values():
            self.assertIsNone(item["last_verified"])
            self.assertIsNone(item["interactive_required"])
            doctor.CapabilityResult.from_dict(item)
        self.assertNotIn("secret", json.dumps(report))

    def test_only_explicit_read_only_commands_are_executed(self):
        doctor.collect_cachyos_doctor()
        for call in self.probe.call_args_list:
            command, uid = call.args
            self.assertEqual(uid, 1000)
            if command[0] == "/usr/bin/pacman":
                self.assertEqual(command[1:3], ["-Q", "--"])
                self.assertIn(command[3], doctor.PACKAGES)
            elif command[0] == "/usr/bin/busctl":
                self.assertEqual(command[4:8], ["org.freedesktop.DBus", "/org/freedesktop/DBus",
                                              "org.freedesktop.DBus", "NameHasOwner"])
            else:
                self.assertEqual(command[:4], ["/usr/bin/systemctl", "--user", "--no-pager", "is-active"])

    def test_selected_automation_dependencies_report_missing_packages_as_failures(self):
        from common.cachyos_steps import CACHYOS_AUTOMATION_PACKAGES
        from lib.config import SetupConfig

        def probe(command, uid):
            if command[0] == "/usr/bin/pacman" and command[-1] in CACHYOS_AUTOMATION_PACKAGES:
                return "error", ""
            return self.healthy_probe(command, uid)

        self.probe.side_effect = probe
        with patch.object(doctor.shutil, "which", return_value=None):
            for selected in (False, True):
                config = SetupConfig(host="localhost", username="alice", system_type="agent_cachyos",
                                     install_material_maker=selected)
                records = {item["name"]: item for item in doctor.collect_cachyos_doctor(config=config)["capabilities"]}
                for package in CACHYOS_AUTOMATION_PACKAGES:
                    self.assertEqual(records["package." + package]["selected"], selected)
                    self.assertEqual(records["package." + package]["state"], "failed" if selected else "deferred")

    def test_selected_game_dependencies_and_modules_are_observed_without_launches(self):
        from common.cachyos_development import GAME_DEV_PACKAGES
        from lib.config import SetupConfig

        def probe(command, uid):
            if command[0] == "/usr/bin/pkg-config":
                return ("error", "") if command[-1] == "sdl3-image" else ("ok", "3.4.6\n")
            if command[0] == "/usr/bin/pacman" and command[-1] == "sdl3_image":
                return "error", ""
            return self.healthy_probe(command, uid)

        self.probe.side_effect = probe
        config = SetupConfig(host="localhost", username="alice", system_type="agent_cachyos",
                             install_game_dev=True, install_python=True)
        with patch.object(doctor.shutil, "which", return_value="/usr/bin/available"):
            report = doctor.collect_cachyos_doctor(config=config)
        records = {item["name"]: item for item in report["capabilities"]}
        for package in GAME_DEV_PACKAGES:
            self.assertTrue(records["package." + package]["selected"])
        self.assertEqual(records["package.sdl3_image"]["state"], "failed")
        self.assertEqual(records["native.sdl3-image"]["state"], "failed")
        self.assertEqual(records["native.sdl3"]["version"], "3.4.6")
        self.assertEqual(records["tool.xvfb"]["state"], "available")
        self.assertEqual(sum(item["name"] == "tool.python" for item in report["capabilities"]), 1)
        for call in self.probe.call_args_list:
            command = call.args[0]
            if command[0] == "/usr/bin/available":
                self.assertEqual(command[1:], ["--version"])
            else:
                self.assertIn(command[0], ("/usr/bin/pkg-config", "/usr/bin/pacman", "/usr/bin/systemctl", "/usr/bin/busctl"))
        for item in records.values():
            doctor.CapabilityResult.from_dict(item)

    def test_selected_nvm_reports_missing_and_loadable_installations(self):
        from lib.config import SetupConfig

        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            config = SetupConfig(host="localhost", username="alice", system_type="agent_cachyos", install_node_versions=True)
            with patch.object(doctor.pwd, "getpwuid", return_value=SimpleNamespace(pw_name="alice", pw_dir=temporary)), \
                    patch.object(doctor.shutil, "which", return_value=None), \
                    patch("common.cachyos_development.nvm_script", return_value=home / ".nvm/nvm.sh"), \
                    patch.dict("os.environ", {"NVM_DIR": ""}):
                missing = doctor.collect_cachyos_doctor(config=config)
                self.assertEqual(next(item for item in missing["capabilities"] if item["name"] == "tool.nvm")["state"], "failed")
                (home / ".nvm").mkdir()
                (home / ".nvm/nvm.sh").write_text("# fixture\n")
                self.probe.side_effect = lambda command, uid: ("ok", "0.40.6\n") if command[0] == "/usr/bin/env" else self.healthy_probe(command, uid)
                report = doctor.collect_cachyos_doctor(config=config)
                nvm = next(item for item in report["capabilities"] if item["name"] == "tool.nvm")
                self.assertEqual(nvm["state"], "available")
                self.assertEqual(nvm["version"], "0.40.6")
                self.assertNotIn(temporary, json.dumps(report))

    def test_selected_native_commands_are_checked_without_launching_them(self):
        from lib.config import SetupConfig

        config = SetupConfig(host="localhost", username="alice", system_type="agent_cachyos",
                             agent_tools=[], install_av_tools=True, install_gl_tools=True,
                             install_blender=True, install_sysadmin_tools=True,
                             install_butler=True, install_steamcmd=True, install_moonlight=True)
        # Package queries succeed, but missing executables must still fail.
        missing = {"ffprobe", "apitrace", "blender", "tcpdump", "butler", "moonlight"}
        with patch.object(doctor.shutil, "which", side_effect=lambda cmd: None if cmd in missing else "/fixture/" + cmd):
            report = doctor.collect_cachyos_doctor(config=config)
        records = {item["name"]: item for item in report["capabilities"]}
        for command in missing:
            self.assertEqual(records["tool." + command]["state"], "failed")
            self.assertTrue(records["tool." + command]["selected"])
        for command in ("ffmpeg", "magick", "exiftool", "glxinfo", "vulkaninfo", "steamcmd", "nmap"):
            self.assertEqual(records["tool." + command]["state"], "available")
            self.assertIsNone(records["tool." + command]["version"])
        self.assertEqual(records["package.blender"]["state"], "available")
        for call in self.probe.call_args_list:
            self.assertIn(call.args[0][0], ("/usr/bin/pacman", "/usr/bin/systemctl", "/usr/bin/busctl",
                                          "/fixture/git", "/fixture/rg"))

    def test_unselected_native_commands_are_not_required_and_overlaps_are_unique(self):
        from lib.config import SetupConfig

        config = SetupConfig(host="localhost", username="alice", system_type="agent_cachyos")
        with patch.object(doctor.shutil, "which", return_value=None):
            records = {item["name"] for item in doctor.collect_cachyos_doctor(config=config)["capabilities"]}
        for command in ("ffprobe", "apitrace", "blender", "steamcmd", "tcpdump"):
            self.assertNotIn("tool." + command, records)
        config.install_game_dev = config.install_gl_tools = True
        with patch.object(doctor.shutil, "which", return_value=None):
            report = doctor.collect_cachyos_doctor(config=config)
        names = [item["name"] for item in report["capabilities"]]
        self.assertEqual(names.count("tool.glxinfo"), 1)
        self.assertEqual(len(names), len(set(names)))

    def test_remmina_protocol_dependencies_follow_the_selection(self):
        from common.cachyos_steps import CACHYOS_REMMINA_PACKAGES
        from lib.config import SetupConfig

        self.probe.side_effect = lambda command, uid: ("error", "") if (
            command[-1] in CACHYOS_REMMINA_PACKAGES) else self.healthy_probe(command, uid)
        for selected in (False, True):
            config = SetupConfig(host="localhost", username="alice", system_type="agent_cachyos",
                                 install_remmina=selected)
            with patch.object(doctor.shutil, "which", return_value=None):
                records = {item["name"]: item for item in doctor.collect_cachyos_doctor(config=config)["capabilities"]}
            for package in CACHYOS_REMMINA_PACKAGES:
                if selected:
                    self.assertEqual(records["package." + package]["state"], "failed")
                    self.assertTrue(records["package." + package]["selected"])
                else:
                    self.assertNotIn("package." + package, records)

    def test_desktop_package_does_not_require_managed_web_service(self):
        def probe(command, uid):
            if command[-1] == "basaltwater-cachyos-t3.service":
                return "error", ""
            return self.healthy_probe(command, uid)
        self.probe.side_effect = probe
        records = {item["name"]: item for item in doctor.collect_cachyos_doctor()["capabilities"]}
        self.assertEqual(records["package.t3code-bin"]["state"], "available")
        self.assertEqual(records["service.t3code"]["state"], "deferred")
        self.assertIn("Selection may be unknown", records["service.t3code"]["reason"])

    def test_selected_desktop_includes_storage_metadata(self):
        from lib.config import SetupConfig

        config = SetupConfig(host="localhost", username="alice", system_type="agent_cachyos",
                             t3code_desktop=True, agent_tools=["codex"])
        with tempfile.TemporaryDirectory() as home, \
                patch.object(doctor.pwd, "getpwuid", return_value=SimpleNamespace(pw_name="alice", pw_dir=home)), \
                patch.object(doctor.shutil, "which", return_value=None), \
                patch("lib.cachyos_health.collect_t3_storage_health", return_value=[
                    ("security.t3-storage", "failed", "Unsafe permissions.")]) as storage:
            records = {item["name"]: item for item in doctor.collect_cachyos_doctor(config=config)["capabilities"]}
        storage.assert_called_once_with(Path(home), 1000)
        self.assertEqual(records["security.t3-storage"]["state"], "failed")
        self.assertTrue(records["security.t3-storage"]["selected"])

    def test_selected_sunshine_includes_service_and_encoding_observations(self):
        from lib.config import SetupConfig

        config = SetupConfig(host="localhost", username="alice", system_type="agent_cachyos", install_sunshine=True)
        with patch.object(doctor.shutil, "which", return_value=None), \
                patch("lib.cachyos_health.collect_sunshine_health", return_value=[
                    ("service.sunshine", "failed", "Sunshine service failed.")]) as sunshine:
            records = {item["name"]: item for item in doctor.collect_cachyos_doctor(config=config)["capabilities"]}
        sunshine.assert_called_once_with(doctor._probe, 1000, bus_ready=True)
        self.assertEqual(records["service.sunshine"]["state"], "failed")
        self.assertTrue(records["service.sunshine"]["selected"])

    def test_webhook_observations_use_local_receipts_without_sending(self):
        from lib.config import SetupConfig

        config = SetupConfig(host="localhost", username="alice", system_type="agent_cachyos",
                             notify_specs=[["webhook", "https://receiver.example/private_hook"]])
        with patch.object(doctor.shutil, "which", return_value=None), \
                patch("lib.notifications._open_webhook_request") as send, \
                patch("lib.cachyos_notification_state.collect_notification_health") as receipt:
            for state in ("available", "failed", "deferred"):
                receipt.return_value = (state, "Local delivery evidence.")
                records = {item["name"]: item for item in doctor.collect_cachyos_doctor(config=config)["capabilities"]}
                self.assertEqual(records["notifications.webhook"]["state"], state)
                self.assertTrue(records["notifications.webhook"]["selected"])
                self.assertNotIn("private_hook", json.dumps(records))
                receipt.assert_called_with(config)
            receipt.reset_mock()
            config.notify_specs = None
            records = {item["name"]: item for item in doctor.collect_cachyos_doctor(config=config)["capabilities"]}
            self.assertNotIn("notifications.webhook", records)
            receipt.assert_not_called()
            send.assert_not_called()

    def test_unsupported_host_and_root_do_not_probe(self):
        self.supported.return_value = False
        report = doctor.collect_cachyos_doctor()
        self.assertEqual(report["capabilities"][0]["state"], "deferred")
        self.probe.assert_not_called()
        self.socket.assert_not_called()
        self.supported.return_value = True
        self.uid.return_value = 0
        doctor.collect_cachyos_doctor()
        self.probe.assert_not_called()

    def test_missing_bus_does_not_connect_or_activate_services(self):
        self.socket.return_value = False
        report = doctor.collect_cachyos_doctor()
        self.assertTrue(all(call.args[0][0] == "/usr/bin/pacman" for call in self.probe.call_args_list))
        self.assertTrue(all(item["state"] == "deferred" for item in report["capabilities"]
                            if item["name"].startswith("session.")))

    def test_hostile_display_path_is_not_inspected(self):
        with patch.dict("os.environ", {"WAYLAND_DISPLAY": "/private/secret"}):
            doctor.collect_cachyos_doctor()
        self.socket.assert_called_once_with(Path("/run/user/1000/bus"), 1000)

    def test_package_output_is_allowlisted_and_bounded(self):
        self.probe.return_value = ("ok", "kwin private-secret\nTOKEN=secret")
        self.probe.side_effect = None
        serialized = json.dumps(doctor.collect_cachyos_doctor())
        self.assertNotIn("private-secret", serialized)
        self.assertNotIn("TOKEN", serialized)

    def test_cli_json_is_parseable_and_needs_no_root_or_setup(self):
        parser, _, _ = basaltwater.create_basaltwater_parser()
        args = parser.parse_args(["local", "cachyos-doctor", "--json"])
        with patch("sys.stdout", new_callable=io.StringIO) as output, \
                patch("lib.local_cli._run_step") as mutate:
            self.assertEqual(run_local_command(args), 0)
        self.assertEqual(json.loads(output.getvalue())["schema_version"], 1)
        mutate.assert_not_called()

    def test_main_doctor_bypasses_distribution_prompt_and_emits_only_json(self):
        with patch("sys.argv", ["basaltwater", "local", "cachyos-doctor", "--json"]), \
                patch("sys.stdout", new_callable=io.StringIO) as output, \
                patch.object(basaltwater, "confirm_unsupported_environment") as confirm, \
                patch("lib.local_cli._run_step") as mutate:
            self.assertEqual(basaltwater.main(), 0)
        self.assertEqual(json.loads(output.getvalue())["schema_version"], 1)
        confirm.assert_not_called()
        mutate.assert_not_called()

    def test_main_mutating_local_command_keeps_distribution_guard(self):
        with patch("sys.argv", ["basaltwater", "local", "update"]), \
                patch.object(basaltwater, "confirm_unsupported_environment", return_value=False) as confirm, \
                patch.object(basaltwater, "run_local_command") as dispatch:
            self.assertEqual(basaltwater.main(), 1)
        confirm.assert_called_once_with("local maintenance")
        dispatch.assert_not_called()

    def test_brave_only_host_has_native_browser_without_claiming_automation(self):
        def probe(command, uid):
            if command[0] == "/usr/bin/pacman" and command[-1] in doctor.BROWSER_PACKAGES:
                return ("ok", "brave-bin 1:1.95.101-1\n") if command[-1] == "brave-bin" else ("error", "")
            return self.healthy_probe(command, uid)

        self.probe.side_effect = probe
        records = {item["name"]: item for item in doctor.collect_cachyos_doctor()["capabilities"]}
        self.assertEqual(records["browser.native"]["state"], "available")
        self.assertIsNone(records["browser.native"]["selected"])
        self.assertEqual(records["package.firefox"]["state"], "deferred")
        self.assertEqual(records["browser.playwright"]["state"], "deferred")


if __name__ == "__main__":
    unittest.main()
