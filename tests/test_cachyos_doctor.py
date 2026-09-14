"""Portable qualification contract tests; no live desktop or system mutations."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
import io
import json
from pathlib import Path
import stat
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import infra_tools
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
            self.assertFalse(records[name]["selected"])
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
        parser, _, _ = infra_tools.create_infra_tools_parser()
        args = parser.parse_args(["local", "cachyos-doctor", "--json"])
        with patch("sys.stdout", new_callable=io.StringIO) as output, \
                patch("lib.local_cli._run_step") as mutate:
            self.assertEqual(run_local_command(args), 0)
        self.assertEqual(json.loads(output.getvalue())["schema_version"], 1)
        mutate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
