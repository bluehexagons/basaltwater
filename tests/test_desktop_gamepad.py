"""Offline contracts for the optional Linux QA controller; no real devices."""

from __future__ import annotations

import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from desktop import gamepad_qa as qa


class GamepadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.current = {"state": "running", "helper_running": True, "agent_control": "enabled",
                        "generation": "gen", "control_revision": "rev", "geometry": [640, 480],
                        "observation_id": "capture", "capture_freshness": {"fresh": True}}
        self.control = self.enterContext(patch.object(qa.control_state, "require_enabled"))
        self.status = self.enterContext(patch.object(qa, "helper_status", return_value=self.current))
        self.pad = Mock()
        self.bridge = qa.Bridge(self.pad, self.folder, 60, dict(self.current))
        self.payload = {"buttons": ["south"], "axes": {}, "hold_ms": 0,
                        "observation": dict(self.current)}
        self.connection = Mock()

    def test_mapping_and_input_bounds(self):
        self.assertEqual(qa.validate_action({"buttons": ["south", "start", "west", "north"],
                                            "axes": {"right-x": -32768, "right-trigger": 255},
                                            "hold_ms": 5000}),
                         [(1, 304, 1), (1, 315, 1), (1, 307, 1), (1, 308, 1),
                          (3, 3, -32768), (3, 5, 255)])
        for change in ({"hold_ms": True}, {"hold_ms": 5001}, {"hold_ms": -1},
                       {"buttons": ["invalid"]}, {"buttons": ["south", "south"]},
                       {"axes": {"left-trigger": -1}}, {"axes": {"left-x": 32768}},
                       {"axes": {"dpad-y": 2}}, {"axes": {"invalid": 0}},
                       {"axes": {"dpad-y": True}}, {"buttons": [], "axes": {}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                qa.validate_action({**self.payload, **change})

    def test_fresh_capture_is_single_use(self):
        result = self.bridge.press(self.payload, self.connection)
        self.assertEqual(result["delivery"], "completed")
        self.assertTrue(result["neutralized"])
        self.pad.write.assert_called_once_with([(1, 304, 1)])
        self.pad.neutral.assert_called_once()
        self.assertEqual(json.loads(Path(result["artifact"]).read_text())["id"], result["id"])
        with self.assertRaises(ValueError):
            self.bridge.press(self.payload, self.connection)

    def test_stale_or_changed_capture_never_dispatches(self):
        for change in ({"generation": "other"}, {"control_revision": "other"},
                       {"geometry": [800, 600]}, {"observation_id": "old"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.bridge.press({**self.payload, "observation": {**self.current, **change}}, self.connection)
        self.current["capture_freshness"] = {"fresh": False}
        with self.assertRaises(ValueError):
            self.bridge.press(self.payload, self.connection)
        self.pad.write.assert_not_called()

    def test_blocked_or_stopped_helper_never_dispatches(self):
        for change in ({"agent_control": "disabled"}, {"agent_control": "human-override"},
                       {"helper_running": False}, {"state": "stopped"}):
            self.status.return_value = {**self.current, **change}
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                self.bridge.press(self.payload, self.connection)
        self.pad.write.assert_not_called()

    def test_revision_change_stops_idle_bridge(self):
        self.status.return_value = {**self.current, "control_revision": "new"}
        with self.assertRaises(RuntimeError):
            self.bridge.guard()

    def test_hold_releases_on_control_change(self):
        # Initial guard/status, observation validation, final guard, held guard.
        self.control.side_effect = [None, None, None, None, RuntimeError("human override")]
        result = self.bridge.press({**self.payload, "hold_ms": 100}, self.connection)
        self.assertEqual(result["delivery"], "unknown")
        self.assertTrue(result["neutralized"])
        self.assertTrue(self.bridge.stopping)
        self.pad.neutral.assert_called_once()

    def test_disconnect_releases_and_stops(self):
        self.connection.recv.return_value = b""
        with patch.object(qa.select, "select", return_value=([self.connection], [], [])):
            result = self.bridge.press({**self.payload, "hold_ms": 100}, self.connection)
        self.assertIn("disconnected", result["error"])
        self.assertTrue(result["neutralized"])
        self.assertTrue(self.bridge.stopping)

    def test_partial_device_failure_is_uncertain_and_released(self):
        self.pad.write.side_effect = OSError("write interrupted")
        result = self.bridge.press(self.payload, self.connection)
        self.assertEqual(result["delivery"], "unknown")
        self.assertTrue(result["neutralized"])
        self.assertTrue(self.bridge.stopping)

    def test_release_failure_stops_and_remains_visible(self):
        self.pad.neutral.side_effect = OSError("release failed")
        result = self.bridge.press(self.payload, self.connection)
        self.assertFalse(result["neutralized"])
        self.assertIn("release_error", result)
        self.assertTrue(self.bridge.stopping)

    def test_expiry_and_stop_block_new_actions(self):
        for expired in (False, True):
            self.bridge.stopping = not expired
            self.bridge.until = 0 if expired else float("inf")
            with self.subTest(expired=expired), self.assertRaises(RuntimeError):
                self.bridge.press(self.payload, self.connection)
        self.pad.write.assert_not_called()

    def test_pad_cleanup_attempts_all_neutrals_then_closes(self):
        pad = qa.Pad.__new__(qa.Pad)
        pad.device = Mock()
        pad.close()
        writes = [call.args for call in pad.device.write.call_args_list]
        self.assertEqual(len(writes), len(qa.BUTTONS) + len(qa.AXES))
        self.assertTrue(all(value == 0 for _, _, value in writes))
        pad.device.close.assert_called_once()
        pad.device.write.side_effect = OSError("gone")
        with self.assertRaises(OSError):
            pad.close()
        self.assertEqual(pad.device.close.call_count, 2)

    def test_real_socket_message_limits(self):
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        left.sendall(b'{"action":"status"}\n')
        self.assertEqual(qa.receive(right), {"action": "status"})
        left.sendall(b"x" * (qa.MAX_MESSAGE + 1) + b"\n")
        with self.assertRaises(ValueError):
            qa.receive(right)

    def test_server_rejection_recapture_stop_and_owned_cleanup(self):
        path = self.folder / "pad.sock"
        output = self.folder / "evidence"
        self.pad.device.device = "/fake/event1"
        self.pad.device.name = "Fake QA pad"
        ready = threading.Event()
        failures = []
        writer = qa.write_json_atomic
        def write(path, value, **options):
            writer(path, value, **options)
            if Path(path).name == "device.json":
                ready.set()
        def worker():
            try:
                qa.serve(str(output), 60)
            except Exception as exc:
                failures.append(exc)
                ready.set()
        with patch.object(qa, "socket_path", return_value=path), \
                patch.object(qa, "Pad", return_value=self.pad), \
                patch.object(qa.signal, "signal"), \
                patch.object(qa, "write_json_atomic", side_effect=write):
            thread = threading.Thread(target=worker, daemon=True)
            thread.start()
            self.assertTrue(ready.wait(2), "server did not become ready")
            self.assertFalse(failures)
            try:
                self.assertTrue(qa.call({"action": "status"})["running"])
                invalid = {**self.payload, "action": "press", "observation": {**self.current, "observation_id": "old"}}
                rejected = qa.call(invalid)
                self.assertEqual(rejected["delivery"], "not-sent")
                self.assertTrue(rejected["rejected"])
                self.assertEqual(json.loads(Path(rejected["artifact"]).read_text())["delivery"], "not-sent")
                self.pad.write.assert_not_called()
                self.assertTrue(qa.call({"action": "status"})["running"])
                completed = qa.call({**self.payload, "action": "press"})
                self.assertEqual(completed["delivery"], "completed")
                self.assertTrue(completed["neutralized"])
                self.pad.write.assert_called_once_with([(1, 304, 1)])
                reused = qa.call({**self.payload, "action": "press"})
                self.assertEqual(reused["delivery"], "not-sent")
                self.assertTrue(qa.call({"action": "status"})["running"])
            finally:
                qa.call({"action": "stop"})
                thread.join(2)
            self.assertFalse(thread.is_alive())
        self.assertFalse(failures)
        self.assertFalse(path.exists())
        self.assertTrue((output / "closed.json").is_file())
        self.pad.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
