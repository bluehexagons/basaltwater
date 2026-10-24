"""Window readiness stays bounded, observable and independent of control leases."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from io import StringIO
import json
from unittest.mock import Mock, patch
import unittest

from desktop import client, native_session, session_runtime as runtime
from lib.desktop_cli import add_desktop_subparser, run_desktop_command


def window(title="MuseScore 3: score", identity="document", visible=True):
    return {"id": "0x123", "title": title, "identity": identity, "visible": visible, "pid": 42}


def observation(*windows, truncated=False):
    return {"generation": "g", "windows": list(windows), "truncated": truncated}


class FakeClock:
    def __init__(self):
        self.now = 0
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class WindowReadinessTests(unittest.TestCase):
    def run_wait(self, observations, **options):
        backend = Mock()
        backend.request.side_effect = observations
        clock = FakeClock()
        with patch.object(client.time, "monotonic", side_effect=lambda: clock.now), patch.object(
            client.time, "sleep", side_effect=clock.sleep,
        ):
            result = client.wait_for_window("g", title="MuseScore", backend=backend, **options)
        self.assertTrue(all(call.args[0]["action"] == "windows" for call in backend.request.call_args_list))
        return result

    def test_splash_is_skipped_and_only_the_document_is_returned(self):
        splash = window("MuseScore Startup", "splash")
        document = window()
        result = self.run_wait([observation(splash), observation(splash, document)],
                               exclude_titles=["Startup"])
        self.assertEqual(result["windows"], [document])

    def test_transient_and_changed_identity_restart_stability(self):
        result = self.run_wait([observation(window(identity="splash")), observation(),
                               observation(window()), observation(window(identity="loading")),
                               observation(window()), observation(window()), observation(window())],
                              stable_seconds=0.5)
        self.assertEqual(result["windows"], [window()])

    def test_incomplete_inventory_restarts_stability(self):
        result = self.run_wait([observation(window()), observation(window(), truncated=True),
                               observation(window()), observation(window()), observation(window())],
                              stable_seconds=0.5)
        self.assertEqual(result["windows"], [window()])

    def test_absence_must_stay_absent_and_complete_for_the_interval(self):
        result = self.run_wait([observation(), observation(window()), observation(truncated=True),
                               observation(), observation(), observation()],
                              condition="absent", stable_seconds=0.5)
        self.assertEqual(result["windows"], [])

    def test_filtering_works_with_pid_and_visibility(self):
        wrong_pid = {**window(), "pid": 99}
        hidden = window(visible=False)
        result = self.run_wait([observation(wrong_pid, hidden), observation(window())],
                              pid=42, exclude_titles=["Startup"])
        self.assertEqual(result["windows"], [window()])

    def test_continuously_changing_window_times_out(self):
        backend = Mock()
        backend.request.side_effect = [observation(window(identity=str(i))) for i in range(5)]
        clock = FakeClock()
        with patch.object(client.time, "monotonic", side_effect=lambda: clock.now), patch.object(
            client.time, "sleep", side_effect=clock.sleep,
        ), self.assertRaisesRegex(RuntimeError, "Timed out"):
            client.wait_for_window("g", title="MuseScore", backend=backend, stable_seconds=0.5, timeout=1)
        self.assertEqual(backend.request.call_count, 4)

    def test_invalid_options_fail_before_any_backend_request(self):
        cases = [{"exclude_titles": "Startup"}, {"exclude_titles": [""]},
                 {"exclude_titles": ["x"] * 17}, {"exclude_titles": ["x" * 513]},
                 {"stable_seconds": -1}, {"stable_seconds": 6}, {"stable_seconds": True},
                 {"stable_seconds": float("nan")}, {"timeout": 1, "stable_seconds": 2}]
        for options in cases:
            backend = Mock()
            with self.subTest(options=options), self.assertRaises(ValueError):
                client.wait_for_window("g", title="MuseScore", backend=backend, **options)
            backend.request.assert_not_called()

    def test_crashes_name_the_signal_and_do_not_relaunch(self):
        backend = Mock()
        backend.request.return_value = {"returncode": -11}
        with self.assertRaisesRegex(RuntimeError, "SIGSEGV.*11"):
            client.wait_for_window("g", title="MuseScore", backend=backend, launch="token")
        backend.request.assert_called_once_with({"action": "launch-status", "generation": "g", "launch": "token"})


class WaitPollingTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        for name, effect in (("monotonic", lambda: self.clock.now), ("sleep", self.clock.sleep)):
            patcher = patch.object(client.time, name, side_effect=effect)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.backend = Mock()

    def test_idle_window_wait_uses_fewer_scans_and_stops_at_deadline(self):
        self.backend.request.return_value = observation()
        with self.assertRaisesRegex(RuntimeError, "Timed out"):
            client.wait_for_window("g", title="MuseScore", backend=self.backend)
        self.assertLessEqual(self.backend.request.call_count, 20)
        self.assertLessEqual(max(self.clock.sleeps), 1)
        self.assertEqual(self.clock.now, 15)

    def test_incomplete_element_wait_backs_off_without_claiming_absence(self):
        root = "0:" + "a" * 32
        self.backend.request.return_value = {"generation": "g", "elements": [], "truncated": True, "root": root}
        result = client.wait_for_element("g", pid=42, name="Save", root=root,
                                         state="absent", backend=self.backend)
        self.assertIn("Timed out", result["error"])
        self.assertTrue(result["truncated"])
        self.assertEqual(result["root"], root)
        self.assertLessEqual(self.backend.request.call_count, 20)
        self.assertLessEqual(max(self.clock.sleeps), 1)
        self.assertEqual(self.clock.now, 15)
        self.assertTrue(all(call.args[0]["root"] == root for call in self.backend.request.call_args_list))

    def test_immediately_ready_waits_do_not_sleep(self):
        self.backend.request.return_value = observation(window())
        client.wait_for_window("g", title="MuseScore", backend=self.backend)
        self.backend.request.assert_called_once()
        self.backend.request.reset_mock()
        self.backend.request.return_value = {"generation": "g", "elements": [{"ref": "save"}], "truncated": False}
        client.wait_for_element("g", pid=42, name="Save", backend=self.backend)
        self.backend.request.assert_called_once()
        self.assertEqual(self.clock.sleeps, [])

    def test_short_waits_never_start_a_second_scan_at_the_deadline(self):
        self.backend.request.return_value = observation()
        with self.assertRaisesRegex(RuntimeError, "Timed out"):
            client.wait_for_window("g", title="MuseScore", timeout=0.1, backend=self.backend)
        self.backend.request.assert_called_once()
        self.backend.request.reset_mock()
        self.backend.request.return_value = {"generation": "g", "elements": [], "truncated": False}
        result = client.wait_for_element("g", pid=42, name="Save", timeout=0.1, backend=self.backend)
        self.assertIn("Timed out", result["error"])
        self.backend.request.assert_called_once()

    def test_window_observation_after_deadline_is_not_success(self):
        def request(payload):
            self.clock.now += 2
            return observation(window())
        self.backend.request.side_effect = request
        with self.assertRaisesRegex(RuntimeError, "Timed out"):
            client.wait_for_window("g", title="MuseScore", timeout=1, backend=self.backend)
        self.backend.request.assert_called_once()

    def test_element_observation_after_deadline_is_retained_without_success(self):
        observed = {"generation": "g", "elements": [{"ref": "save"}], "truncated": False}
        def request(payload):
            self.clock.now += 2
            return observed
        self.backend.request.side_effect = request
        result = client.wait_for_element("g", pid=42, name="Save", timeout=1, backend=self.backend)
        self.assertIn("Timed out", result["error"])
        self.assertEqual(result["elements"], observed["elements"])
        self.assertEqual(result["generation"], "g")
        self.backend.request.assert_called_once()

    def test_slow_launch_status_does_not_start_a_window_scan_after_deadline(self):
        def request(payload):
            self.clock.now += 2
            return {"returncode": None}
        self.backend.request.side_effect = request
        with self.assertRaisesRegex(RuntimeError, "Timed out"):
            client.wait_for_window("g", title="MuseScore", timeout=1, launch="token", backend=self.backend)
        self.backend.request.assert_called_once_with({"action": "launch-status", "generation": "g", "launch": "token"})


class WindowWaitCliTests(unittest.TestCase):
    def args(self, command):
        parser = argparse.ArgumentParser()
        add_desktop_subparser(parser.add_subparsers())
        return parser.parse_args(["desktop", *command])

    def test_invalid_launch_options_never_acquire_control_or_launch(self):
        commands = (["exec", "--stable-seconds", "1", "--", "editor"],
                    ["exec", "--wait-window", "Editor", "--stable-seconds", "6", "--", "editor"],
                    ["open", "/task/score.mid", "--exclude-title", "Startup"],
                    ["open", "/task/score.mid", "--wait-window", "Editor", "--exclude-title", ""])
        for command in commands:
            with self.subTest(command=command), patch.object(runtime, "status", return_value={
                "state": "running", "generation": "g",
            }), patch.object(runtime, "request") as request, redirect_stdout(StringIO()):
                self.assertEqual(run_desktop_command(self.args(command)), 1)
                request.assert_not_called()

    def test_launch_filters_reach_wait_after_control_is_released(self):
        for backend, prefix in ((runtime, []), (native_session, ["--native"])):
            events = []
            def request(payload):
                events.append(payload["action"])
                return {"windows": []} if payload["action"] == "windows" else (
                    {"lease": "l"} if payload["action"] == "acquire" else {"pid": 42, "launch": "token"})
            def wait(*args, **kwargs):
                self.assertEqual(events[-1], "release")
                self.assertEqual(kwargs["exclude_titles"], ["Startup", "Welcome"])
                self.assertEqual(kwargs["stable_seconds"], 1)
                self.assertIs(kwargs["backend"], backend)
                return {"windows": [window()], "launch_status": {"returncode": None}}
            with self.subTest(backend=backend.__name__), patch.object(backend, "status", return_value={
                "state": "running", "generation": "g",
            }), patch.object(backend, "request", side_effect=request), patch.object(
                client, "wait_for_window", side_effect=wait,
            ), redirect_stdout(StringIO()):
                self.assertEqual(run_desktop_command(self.args([*prefix, "exec", "--wait-window", "MuseScore",
                    "--exclude-title", "Startup", "--exclude-title", "Welcome", "--stable-seconds", "1",
                    "--", "musescore3"])), 0)

    def test_wait_remains_read_only_during_human_pause(self):
        with patch.object(runtime, "status", return_value={"state": "running", "generation": "g", "paused": True}), patch.object(
            runtime, "request", return_value=observation(window()),
        ) as request, redirect_stdout(StringIO()) as output:
            self.assertEqual(run_desktop_command(self.args(["wait", "--title", "MuseScore", "--exclude-title", "Startup"])), 0)
        self.assertEqual(json.loads(output.getvalue())["windows"], [window()])
        self.assertEqual(request.call_args.args[0]["action"], "windows")


if __name__ == "__main__":
    unittest.main()
