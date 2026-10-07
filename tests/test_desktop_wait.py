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


class WindowReadinessTests(unittest.TestCase):
    def run_wait(self, observations, times, **options):
        backend = Mock()
        backend.request.side_effect = observations
        with patch.object(client.time, "monotonic", side_effect=times), patch.object(client.time, "sleep"):
            result = client.wait_for_window("g", title="MuseScore", backend=backend, **options)
        self.assertTrue(all(call.args[0]["action"] == "windows" for call in backend.request.call_args_list))
        return result

    def test_splash_is_skipped_and_only_the_document_is_returned(self):
        splash = window("MuseScore Startup", "splash")
        document = window()
        result = self.run_wait([observation(splash), observation(splash, document)], [0, 0, 0.25],
                               exclude_titles=["Startup"])
        self.assertEqual(result["windows"], [document])

    def test_transient_and_changed_identity_restart_stability(self):
        result = self.run_wait([observation(window(identity="splash")), observation(),
                               observation(window()), observation(window(identity="loading")),
                               observation(window()), observation(window()), observation(window())],
                              [0, 0, 0.25, 0.5, 0.75, 1, 1.25, 1.5], stable_seconds=0.5)
        self.assertEqual(result["windows"], [window()])

    def test_incomplete_inventory_restarts_stability(self):
        result = self.run_wait([observation(window()), observation(window(), truncated=True),
                               observation(window()), observation(window()), observation(window())],
                              [0, 0, 0.25, 0.5, 0.75, 1], stable_seconds=0.5)
        self.assertEqual(result["windows"], [window()])

    def test_absence_must_stay_absent_and_complete_for_the_interval(self):
        result = self.run_wait([observation(), observation(window()), observation(truncated=True),
                               observation(), observation(), observation()],
                              [0, 0, 0.25, 0.5, 0.75, 1, 1.25],
                              condition="absent", stable_seconds=0.5)
        self.assertEqual(result["windows"], [])

    def test_filtering_works_with_pid_and_visibility(self):
        wrong_pid = {**window(), "pid": 99}
        hidden = window(visible=False)
        result = self.run_wait([observation(wrong_pid, hidden), observation(window())],
                              [0, 0, 0.25], pid=42, exclude_titles=["Startup"])
        self.assertEqual(result["windows"], [window()])

    def test_continuously_changing_window_times_out(self):
        backend = Mock()
        backend.request.side_effect = [observation(window(identity=str(i))) for i in range(5)]
        with patch.object(client.time, "monotonic", side_effect=[0, 0, 0.25, 0.5, 0.75, 1]), patch.object(
            client.time, "sleep",
        ), self.assertRaisesRegex(RuntimeError, "Timed out"):
            client.wait_for_window("g", title="MuseScore", backend=backend, stable_seconds=0.5, timeout=1)
        self.assertEqual(backend.request.call_count, 5)

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
