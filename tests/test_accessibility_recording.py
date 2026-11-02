"""Offline contracts for scoped recording; never connect to the desktop."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from desktop import accessibility_recording as recording
from lib.remote_utils import CommandTimeoutError


IDENTITY = {"pid": 42, "start_ticks": 123, "uid": 1000,
            "boot_id": "11111111-2222-3333-4444-555555555555"}


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.real_identity = recording.process_identity
        self.identity = self.enterContext(patch.object(recording, "process_identity", return_value=IDENTITY))
        parser = argparse.ArgumentParser()
        recording.add_recording_parser(parser.add_subparsers())
        self.args = parser.parse_args(["accessibility-record", "--pid", "42", "--output", str(self.home / "new")])
        self.args.native = True

    def run_worker(self, rows=(), done=None, failure=None):
        def stream(_command, **kwargs):
            self.assertEqual(kwargs["timeout"], self.args.seconds + 3)
            self.assertTrue(kwargs["discard_stderr"])
            self.assertEqual(json.loads(kwargs["input_data"])["identity"], IDENTITY)
            for row in rows:
                kwargs["on_output"](json.dumps({"kind": "event", "event": row}))
            if failure:
                raise failure
            kwargs["on_output"](json.dumps({"kind": "done", "errors": [], "truncation_reasons": done or []}))
            return 0
        with patch.object(recording, "run_streamed", side_effect=stream):
            result = recording.run_recording_command(self.args)
        return result, json.loads(Path(result["artifact"]).read_text())

    def test_private_bundle_and_aligned_clock(self):
        result, data = self.run_worker([{"pid": 42, **recording.stamp(), "name": "Save"}])
        self.assertEqual(data["kind"], "accessibility-recording")
        self.assertEqual(data["clock"], {"boot_id": IDENTITY["boot_id"], "monotonic_scope": "linux-boot"})
        self.assertLessEqual(data["started_monotonic_ns"], data["finished_monotonic_ns"])
        self.assertEqual(result["event_count"], 1)
        self.assertEqual(Path(result["artifact"]).stat().st_mode & 0o777, 0o600)
        self.assertEqual(Path(result["artifact"]).parent.stat().st_mode & 0o777, 0o700)
        with self.assertRaises(FileExistsError):
            recording.run_recording_command(self.args)

    def test_timeout_retains_partial_evidence_with_explicit_failure(self):
        result, data = self.run_worker([{"pid": 42}], failure=CommandTimeoutError("worker", 18))
        self.assertIn("timed out", result["error"])
        self.assertEqual(len(data["events"]), 1)
        self.assertEqual(data["status"], "failed")

    def test_foreign_pid_is_never_saved(self):
        result, data = self.run_worker([{"pid": 99, "name": "private"}])
        self.assertIn("error", result)
        self.assertEqual(data["events"], [])

    def test_reused_pid_rejected_before_worker_or_during_collection(self):
        self.args.process_start = 999
        with patch.object(recording, "run_streamed") as worker, self.assertRaisesRegex(ValueError, "identity"):
            recording.run_recording_command(self.args)
        worker.assert_not_called()
        self.args.process_start = None
        self.identity.side_effect = [IDENTITY, {**IDENTITY, "start_ticks": 999}]
        result, data = self.run_worker([{"pid": 42}])
        self.assertIn("error", result)
        self.assertEqual(data["events"], [])

    def test_event_and_byte_bounds_are_explicit(self):
        self.args.max_events = 1
        result, data = self.run_worker([{"pid": 42}, {"pid": 42}])
        self.assertTrue(result["truncated"])
        self.assertEqual(len(data["events"]), 1)
        self.assertIn("max-events", data["truncation_reasons"])
        self.args.output = str(self.home / "bytes")
        self.args.max_output_bytes = 16384
        result, data = self.run_worker([{"pid": 42, "name": "x" * 10000}])
        self.assertTrue(result["truncated"])
        self.assertLessEqual(Path(result["artifact"]).stat().st_size, 16384)

    def test_invalid_bounds_and_backend_never_create_output(self):
        for key, value in (("seconds", 121), ("seconds", 0), ("max_events", 0),
                           ("max_output_bytes", 10), ("max_label_chars", 513), ("pid", -1), ("native", False)):
            with self.subTest(key=key, value=value):
                old = getattr(self.args, key)
                setattr(self.args, key, value)
                with self.assertRaises(ValueError):
                    recording.run_recording_command(self.args)
                setattr(self.args, key, old)
        self.assertFalse(Path(self.args.output).exists())

    def test_symlink_reuse_and_unsafe_output_parent_rejected(self):
        Path(self.args.output).symlink_to(self.home / "absent")
        with self.assertRaises(FileExistsError):
            recording.run_recording_command(self.args)
        Path(self.args.output).unlink()
        self.home.chmod(0o777)
        with self.assertRaisesRegex(ValueError, "parent"):
            recording.run_recording_command(self.args)
        self.home.chmod(0o700)

    def test_linux_process_start_field_dead_process_and_owner_contract(self):
        # comm can contain spaces and parentheses; starttime is field 22.
        stat_text = "42 (sample (worker)) S " + " ".join(["0"] * 18 + ["98765"])
        def read(path, *args, **kwargs):
            return IDENTITY["boot_id"] if path.name == "boot_id" else stat_text
        with patch.object(Path, "read_text", read), patch.object(Path, "stat", return_value=SimpleNamespace(st_uid=1000)), \
                patch.object(recording.os, "getuid", return_value=1000):
            self.assertEqual(self.real_identity(42)["start_ticks"], 98765)
            stat_text = stat_text.replace(") S ", ") Z ")
            with self.assertRaisesRegex(ValueError, "dead"):
                self.real_identity(42)
            stat_text = stat_text.replace(") Z ", ") S ")
            with patch.object(recording.os, "getuid", return_value=1001), self.assertRaisesRegex(ValueError, "belong"):
                self.real_identity(42)


class EventTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(recording, "process_identity", return_value=IDENTITY))
        self.reset_nodes()

    def reset_nodes(self):
        self.api = SimpleNamespace(Role=SimpleNamespace(PASSWORD_TEXT="password"),
                                   StateType=SimpleNamespace(**{name.upper(): name for name in recording.STATES}))
        self.app = Mock()
        self.app.get_process_id.return_value = 42
        self.node = Mock()
        self.node.get_application.return_value = self.app
        self.node.get_parent.return_value = self.app
        for node in (self.app, self.node):
            node.get_role.return_value = "button"
            node.get_state_set.return_value.contains.side_effect = lambda state: state == "focused"
        self.node.get_name.return_value = "Save document"
        self.node.get_role_name.return_value = "button"
        self.event = SimpleNamespace(type="object:announcement", source=self.node, any_data="Ready now")

    def row(self):
        return recording.event_row(self.event, self.app, IDENTITY, self.api, 5)

    def test_names_states_announcements_numeric_values_and_label_truncation(self):
        row = self.row()
        self.assertEqual(row["name"], "Save ")
        self.assertEqual(row["announcement"], "Ready")
        self.assertEqual(row["states"], ["focused"])
        self.assertTrue(row["labels_truncated"])
        self.node.get_text_iface.assert_not_called()
        self.event.type = "object:property-change:accessible-value"
        self.node.is_value.return_value = True
        self.node.get_value_iface.return_value.get_current_value.return_value = 3.5
        self.assertEqual(self.row()["value"], 3.5)
        self.node.get_value_iface.return_value.get_current_value.return_value = float("nan")
        self.assertNotIn("value", self.row())

    def test_protected_ancestor_defunct_and_foreign_events_do_not_read_labels(self):
        for case in ("password", "ancestor", "defunct", "foreign", "unknown-parent", "text-event"):
            with self.subTest(case=case):
                self.reset_nodes()
                if case == "password":
                    self.node.get_role.return_value = "password"
                elif case == "ancestor":
                    self.app.get_role.return_value = "password"
                elif case == "defunct":
                    self.node.get_state_set.return_value.contains.side_effect = lambda state: state == "defunct"
                elif case == "foreign":
                    self.app.get_process_id.return_value = 99
                elif case == "unknown-parent":
                    self.node.get_parent.return_value = None
                else:
                    self.event.type = "object:text-changed:insert"
                self.assertIsNone(self.row())
                self.node.get_name.assert_not_called()

    def worker_fixture(self):
        options = {"pid": 42, "seconds": 1, "max_events": 1, "max_output_bytes": 16384,
                   "max_label_chars": 5, "identity": IDENTITY}
        desktop = Mock()
        desktop.get_child_count.return_value = 1
        desktop.get_child_at_index.return_value = self.app
        self.api.get_desktop = Mock(return_value=desktop)
        self.api.Error = RuntimeError
        listener = Mock()
        self.api.EventListener = Mock()
        self.api.EventListener.new.return_value = listener
        glib = Mock()
        return options, listener, glib

    def test_application_scoped_registration_and_cleanup(self):
        options, listener, glib = self.worker_fixture()
        def run():
            callback = self.api.EventListener.new.call_args.args[0]
            callback(self.event)
            callback(self.event)  # A queued callback after quit cannot exceed the ceiling.
        glib.MainLoop.return_value.run.side_effect = run
        emitted = []
        recording.record_worker(options, self.api, glib, emitted.append)
        self.assertEqual(emitted[-1]["truncation_reasons"], ["max-events"])
        self.assertEqual(listener.register_with_app.call_count, len(recording.EVENT_TYPES))
        for call in listener.register_with_app.call_args_list:
            self.assertIs(call.args[2], self.app)
        self.assertEqual(listener.deregister.call_count, len(recording.EVENT_TYPES))
        self.assertEqual(len(emitted), 2)

    def test_registration_failure_cleans_only_successful_subscriptions(self):
        options, listener, glib = self.worker_fixture()
        listener.register_with_app.side_effect = [True, False]
        with self.assertRaisesRegex(RuntimeError, "registration failed"):
            recording.record_worker(options, self.api, glib, Mock())
        listener.deregister.assert_called_once_with(recording.EVENT_TYPES[0])
        glib.MainLoop.return_value.run.assert_not_called()

    def test_idle_worker_detects_reused_pid(self):
        options, listener, glib = self.worker_fixture()
        def run():
            tick = glib.timeout_add.call_args.args[1]
            with patch.object(recording, "process_identity", return_value={**IDENTITY, "start_ticks": 999}):
                self.assertFalse(tick())
        glib.MainLoop.return_value.run.side_effect = run
        emitted = []
        recording.record_worker(options, self.api, glib, emitted.append)
        self.assertIn("PID was reused", emitted[-1]["errors"][0])
