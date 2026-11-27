"""Agent recipes exercise real parsers and workflows with isolated system calls."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch

from desktop import capture_preview, client, control_state, development, evidence, native_session, observations, png_regions, qa_artifacts
from desktop.receipts import DesktopRequestError
from lib import desktop_cli
from scripts import desktop_observation_probe as probe

ROOT = Path(__file__).resolve().parents[1]


class ObservationProbeTests(unittest.TestCase):
    def test_small_decoy_does_not_advance_or_block_capture_and_default_is_preserved(self):
        for small in (False, True):
            with self.subTest(small=small), tempfile.TemporaryDirectory() as folder:
                buttons = {}
                root = Mock()
                label = Mock()
                def button(_parent, **kwargs):
                    buttons[kwargs["text"]] = kwargs["command"]
                    return Mock()
                log = Path(folder) / "probe.jsonl"
                blocked = Path(folder) / "blocked.png"
                def interact():
                    if small:
                        buttons["Decoy"]()
                        self.assertFalse(blocked.exists())
                        rows = [json.loads(line) for line in log.read_text().splitlines()]
                        self.assertEqual([row["event"] for row in rows], ["opened", "decoy"])
                    buttons["Advance" if small else "Advance once"]()
                    buttons["Close probe"]()
                root.mainloop.side_effect = interact
                tk = Mock(Tk=Mock(return_value=root), Label=Mock(return_value=label), Button=button)
                with patch.dict("sys.modules", {"tkinter": tk}):
                    args = ["--log", str(log), "--block-output", str(blocked)]
                    self.assertEqual(probe.main(args + (["--small-controls"] if small else [])), 0)
                rows = [json.loads(line) for line in log.read_text().splitlines()]
                self.assertEqual(sum(row["event"] == "button" for row in rows), 1)
                self.assertEqual(sum(row["event"] == "decoy" for row in rows), int(small))
                self.assertTrue(blocked.exists())
                self.assertEqual(rows[-1]["event"], "closed")
                root.destroy.assert_called_once()


class ObservationRecipeTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(Path, "home", return_value=self.home))
        self.backend = Mock(MAX_MESSAGE=65536)
        self.current = {"state": "running", "generation": "g", "control_revision": "r",
                        "agent_control": "enabled", "input_capabilities": ["observation-input"]}
        self.backend.status.return_value = self.current
        self.input = {"action": "input", "kind": "click", "geometry": [800, 600], "x": 20, "y": 30,
                      "observation_id": "a" * 32}
        self.steps = [self.input, {"action": "screenshot", "after_ms": 150},
                      {"action": "screenshot", "after_ms": 400}]
        self.receipt = {"input_delivery": "completed", "input_id": "input-1",
                        "input_completed_monotonic_ns": 123, "generation": "g"}
        self.timed_receipt = {**self.receipt, "timing": {"worker_mode": "resident", "input_round_trip_ms": 20.0}}
        self.now = 10.0
        self.enterContext(patch.object(client.time, "monotonic", side_effect=lambda: self.now))
        self.enterContext(patch.object(client.time, "monotonic_ns", side_effect=lambda: int(self.now * 1e9)))
        self.sleep = self.enterContext(patch.object(client.time, "sleep", side_effect=self.advance))
        self.backend.request.side_effect = self.request

    def advance(self, seconds):
        self.now += seconds

    def request(self, payload):
        self.advance(.02)
        if payload["action"] == "input":
            return dict(self.receipt)
        return {"output": payload["output"], "generation": "g", "control_revision": "r",
                "observation_id": "b" * 32, "geometry": [800, 600], "initial_frame_skipped": True,
                "capture_freshness": {"fresh": True}, "unneeded_status": "omit"}

    def run_recipe(self):
        path = self.home / "steps.json"
        path.write_text(json.dumps(self.steps))
        return client.run_sequence(str(path), "g", backend=self.backend)

    def test_one_input_two_delayed_captures_keep_receipts_and_actual_timing(self):
        self.steps[1]["next_frame"] = self.steps[2]["next_frame"] = True
        result = self.run_recipe()
        self.assertEqual(result["completed"], 3)
        self.assertEqual(result["results"][0], self.timed_receipt)
        self.assertNotIn("unneeded_status", result["results"][1])
        self.assertEqual([row["requested_delay_ms"] for row in result["timings"]], [0, 150, 400])
        for timing, expected in zip(result["timings"], (0, 150, 400)):
            self.assertAlmostEqual(timing["actual_delay_ms"], expected)
            self.assertGreaterEqual(timing["finished_monotonic_ns"], timing["request_started_monotonic_ns"])
        self.assertEqual([call.args[0]["action"] for call in self.backend.request.call_args_list],
                         ["input", "screenshot", "screenshot"])
        paths = [row["output"] for row in result["results"][1:]]
        self.assertEqual(len(set(paths)), 2)
        self.assertTrue(all(Path(path).parent == self.home / "Pictures" / "basaltwater" for path in paths))
        self.assertTrue(all("after_ms" not in call.args[0] for call in self.backend.request.call_args_list))

    def test_partial_capture_failures_never_replay_completed_input(self):
        for failed_index in (1, 2):
            with self.subTest(failed_index=failed_index):
                self.backend.request.reset_mock()
                def request(payload):
                    if self.backend.request.call_count - 1 == failed_index:
                        raise RuntimeError("capture unavailable")
                    return self.request(payload)
                self.backend.request.side_effect = request
                result = self.run_recipe()
                self.assertEqual(result["completed"], failed_index)
                self.assertEqual(result["results"][0], self.timed_receipt)
                self.assertEqual(result["failed_action_index"], failed_index)
                self.assertEqual(len(result["timings"]), failed_index + 1)
                self.assertEqual(self.backend.request.call_count, failed_index + 1)
                self.assertIn("capture unavailable", result["failed_action"]["error"])

    def test_control_change_during_delay_stops_before_capture(self):
        def intervene(seconds):
            self.advance(seconds)
            self.backend.status.return_value = {**self.current, "control_revision": "changed", "agent_control": "human-override"}
        self.sleep.side_effect = intervene
        result = self.run_recipe()
        self.assertEqual(result["completed"], 1)
        self.assertEqual(result["results"][0], self.timed_receipt)
        self.backend.request.assert_called_once()
        self.assertIn("control changed", result["error"])

    def test_expired_input_refusal_retains_stage_without_captures(self):
        refusal = {"error": "Observation expired", "input_delivery": "not-sent", "dispatch_stage": "before-input"}
        self.backend.request.side_effect = DesktopRequestError(refusal)
        result = self.run_recipe()
        self.assertEqual(result["completed"], 0)
        self.assertEqual(result["failed_action"], refusal)
        self.backend.request.assert_called_once()

    def test_invalid_capture_plan_is_refused_before_input(self):
        used = self.home / "used.png"
        used.write_bytes(b"existing")
        duplicate = str(self.home / "duplicate.png")
        variants = ([{"action": "screenshot", "after_ms": value}] for value in (-1, True, 5001, 1.5))
        plans = [*variants, [{"action": "screenshot", "next_frame": "true"}],
                 [{"action": "screenshot", "delay_ms": 150}],
                 *[[{"action": "screenshot", "output": value}] for value in (True, 12, ["frame.png"])],
                 [{"action": "screenshot", "after_ms": 3000}, {"action": "screenshot", "after_ms": 3000}],
                 [{"action": "screenshot", "output": str(used)}],
                 [{"action": "screenshot", "output": str(self.home / "absent" / "a.png")}],
                 [{"action": "screenshot", "output": duplicate}, {"action": "screenshot", "output": duplicate}]]
        for plan in plans:
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                self.steps = [self.input, *plan]
                self.run_recipe()
        self.steps = [{**self.input, "after_ms": 1}]
        with self.assertRaisesRegex(ValueError, "only.*screenshots"):
            self.run_recipe()
        self.backend.status.assert_not_called()
        self.backend.request.assert_not_called()

    def test_same_recipe_runs_on_debian_without_native_frame_flags(self):
        with patch.object(client.runtime, "status", return_value=self.current), \
                patch.object(client.runtime, "request", side_effect=self.request) as request:
            path = self.home / "steps.json"
            path.write_text(json.dumps(self.steps))
            self.assertEqual(client.run_sequence(str(path), "g")["completed"], 3)
            self.assertEqual(request.call_count, 3)
            self.steps[1]["next_frame"] = True
            path.write_text(json.dumps(self.steps))
            with self.assertRaisesRegex(ValueError, "omit next_frame on Debian"):
                client.run_sequence(str(path), "g")
            self.assertEqual(request.call_count, 3)

    def test_debian_format_paths_fail_before_input_but_native_paths_stay_literal(self):
        with patch.object(client.runtime, "status") as status, patch.object(client.runtime, "request") as request:
            for char in ("%", "$", "\\"):
                output = str(self.home / f"literal{char}name.png")
                self.steps[1]["output"] = output
                path = self.home / "steps.json"
                path.write_text(json.dumps(self.steps))
                with self.subTest(char=char), self.assertRaisesRegex(ValueError, "scrot format"):
                    client.run_sequence(str(path), "g")
                self.assertEqual(client.validate_capture_output(output, backend=self.backend), output)
            status.assert_not_called()
            request.assert_not_called()

    def test_status_checks_share_the_sequence_budget_and_keep_completed_input(self):
        def status(*, deadline):
            self.assertEqual(deadline, 30)
            if self.backend.request.call_count:
                self.advance(20)
                raise TimeoutError("status deadline expired")
            return self.current
        self.backend.status.side_effect = status
        result = self.run_recipe()
        self.assertEqual(result["completed"], 1)
        self.assertEqual(result["results"][0], self.timed_receipt)
        self.assertEqual(result["failed_action_index"], 1)
        self.assertEqual(len(result["timings"]), 2)
        self.backend.request.assert_called_once()
        self.assertEqual(self.backend.request.call_args.args[0]["deadline"], 24)

    def test_old_helper_and_deadline_stop_without_further_input(self):
        self.steps[1]["next_frame"] = True
        self.backend.request.side_effect = [self.receipt, {"output": "/unused.png"}]
        result = self.run_recipe()
        self.assertEqual(result["completed"], 1)
        self.assertIn("did not confirm", result["error"])
        self.backend.request.reset_mock()
        self.steps[1].pop("next_frame")
        def overrun(payload):
            self.advance(21)
            return dict(self.receipt)
        self.backend.request.side_effect = overrun
        result = self.run_recipe()
        self.assertEqual(result["completed"], 1)
        self.backend.request.assert_called_once()


class RecorderHeadroomTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(Path, "home", return_value=self.home))
        self.enterContext(patch.object(evidence, "revision", return_value={}))

    def test_headroom_boundaries_and_action_reply_do_not_hide_truncation(self):
        recorder = evidence.Recorder(str(self.home), "g")
        for count, state, rotate in ((449, "available", False), (450, "low", True), (499, "low", True), (500, "full", True)):
            recorder.data["events"] = [{"action": "input"}] * count
            capacity = recorder.capacity()
            self.assertEqual((capacity["remaining_events"], capacity["headroom"], capacity["rotation_recommended"]),
                             (500 - count, state, rotate))
        session = native_session.NativeSession(Mock())
        session.evidence = recorder
        result = {"input_delivery": "completed", "input_id": "one"}
        session.record_evidence({"action": "input"}, result)
        self.assertEqual(result["input_delivery"], "completed")
        self.assertEqual(result["evidence_capacity"]["headroom"], "truncated")
        self.assertEqual(result["evidence_capacity"]["artifact"], str(recorder.path))
        self.assertIn("same recording", result["evidence_capacity"]["warning"])
        self.assertEqual(observations.compact_capture(result)["evidence_capacity"], result["evidence_capacity"])

    def test_new_segments_and_same_recording_closeouts_have_different_links(self):
        first, second = [evidence.Recorder(str(self.home), "g") for _ in range(2)]
        first.finish({})
        second.finish({})
        with patch("desktop.qa_artifacts.new_bundle", side_effect=lambda output: self.new_folder(output)):
            a = evidence.closeout(str(first.path), extras=[], derivatives=[], output="a")
            b = evidence.closeout(str(first.path), extras=[], derivatives=[], output="b", previous=a["artifact"])
            self.assertEqual(b["previous_receipt"]["artifact"], a["artifact"])
            with self.assertRaisesRegex(ValueError, "SAME recording.*checkpoint --artifact"):
                evidence.closeout(str(second.path), extras=[], derivatives=[], previous=a["artifact"])

    def test_start_reuses_finished_recording_project_with_current_helper_generation(self):
        project = self.new_folder("project with spaces")
        previous = evidence.Recorder(str(project), "previous-generation")
        previous.finish({})
        original = previous.path.read_bytes()
        parser = argparse.ArgumentParser()
        desktop_cli.add_desktop_subparser(parser.add_subparsers())
        for backend, flag in ((desktop_cli.runtime, []), (native_session, ["--native"])):
            with (self.subTest(backend=backend.__name__),
                  patch.object(backend, "status", return_value={"generation": "current"}) as status,
                  patch.object(backend, "request", return_value={"artifact": "/private/next.json", "recording": True}) as request,
                  redirect_stdout(output := StringIO())):
                args = parser.parse_args(["desktop", *flag, "evidence", "start",
                                          "--project-from-recording", str(previous.path), "--json"])
                self.assertEqual(desktop_cli.run_desktop_command(args), 0)
                status.assert_called_once_with()
                request.assert_called_once_with({"action": "evidence", "operation": "start",
                                                 "generation": "current", "project": str(project)})
                self.assertEqual(json.loads(output.getvalue())["project"], str(project))
        self.assertEqual(previous.path.read_bytes(), original)

    def test_start_rejects_unusable_project_before_contacting_helper(self):
        project = self.new_folder("project")
        previous = evidence.Recorder(str(project), "g")
        parser = argparse.ArgumentParser()
        desktop_cli.add_desktop_subparser(parser.add_subparsers())
        base = ["desktop", "--native", "evidence", "start"]
        inherit = ["--project-from-recording", str(previous.path)]
        with (patch.object(native_session, "status") as status, patch.object(native_session, "request") as request):
            def refuse(options, detail):
                with redirect_stdout(output := StringIO()):
                    self.assertEqual(desktop_cli.run_desktop_command(parser.parse_args([*base, *options])), 2)
                self.assertIn(detail, json.loads(output.getvalue())["error"])

            refuse(inherit, "Finish the previous recording")
            previous.finish({})
            project.rmdir()
            refuse(inherit, str(project))
            refuse(["--project", str(project)], str(project))
            refuse(["--project", ""], "non-empty")
            project.symlink_to(self.new_folder("other project"), target_is_directory=True)
            refuse(inherit, "resolves to a different location")
            status.assert_not_called()
            request.assert_not_called()
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            parser.parse_args([*base, *inherit, "--project", str(project)])

    def new_folder(self, name):
        folder = self.home / name
        folder.mkdir()
        return folder

    def test_saved_sequence_preview_keeps_latest_headroom_and_recording_errors(self):
        recorder = evidence.Recorder(str(self.home), "g")
        recorder.data["events"] = [{"action": "input"}] * 449
        first = recorder.capacity()
        recorder.data["events"].append({"action": "screenshot"})
        last = recorder.capacity()
        result = {"completed": 1, "results": [{"input_delivery": "completed", "evidence_capacity": first,
                   "evidence_error": "Recording checkpoint failed"}], "error": "capture unavailable",
                  "failed_action_index": 1, "failed_action": {"error": "capture unavailable", "evidence_capacity": last}}
        short = observations.save_observation(result)
        self.addCleanup(shutil.rmtree, Path(short["artifact"]).parent)
        self.assertEqual(short["evidence_capacity"]["remaining_events"], 50)
        self.assertTrue(short["evidence_capacity"]["rotation_recommended"])
        self.assertIn("safe checkpoint", short["evidence_capacity"]["warning"])
        self.assertEqual(short["evidence_error"], "Recording checkpoint failed")
        self.assertEqual(json.loads(Path(short["artifact"]).read_text()), result)
        standalone = observations.save_observation({"evidence_capacity": last})
        self.addCleanup(shutil.rmtree, Path(standalone["artifact"]).parent)
        self.assertEqual(standalone["evidence_capacity"], last)

    def test_failed_capture_returns_capacity_and_original_failure_receipt(self):
        session = native_session.NativeSession(Mock())
        session.evidence = recorder = evidence.Recorder(str(self.home), "g")
        recorder.data["events"] = [{"action": "input"}] * 449
        original = {"error": "Capture was cancelled", "reason": "capture-cancelled", "output": "/private/failed.png"}
        with patch.object(session, "_handle", side_effect=DesktopRequestError(original)), \
                self.assertRaises(DesktopRequestError) as failure:
            session.handle({"action": "screenshot"})
        self.assertEqual(failure.exception.receipt["reason"], "capture-cancelled")
        self.assertEqual(failure.exception.receipt["error"], original["error"])
        self.assertEqual(failure.exception.receipt["evidence_capacity"]["remaining_events"], 50)
        self.assertEqual(recorder.data["events"][-1]["action"], "screenshot")

    def test_recording_save_failure_cannot_erase_input_delivery_or_warning(self):
        for refused in (False, True):
            with self.subTest(refused=refused):
                session = native_session.NativeSession(Mock())
                session.evidence = recorder = evidence.Recorder(str(self.home), "g")
                recorder.data["events"] = [{"action": "input"}] * 449
                receipt = {"input_delivery": "not-sent" if refused else "completed", "input_id": "one"}
                payload = {"action": "input"}
                with patch.object(recorder, "save", side_effect=OSError("disk full")), \
                        patch.object(session, "_handle", return_value=dict(receipt),
                                     side_effect=DesktopRequestError({**receipt, "error": "expired"}) if refused else None):
                    if refused:
                        with self.assertRaises(DesktopRequestError) as failure:
                            session.handle(payload)
                        result = failure.exception.receipt
                    else:
                        result = session.handle(payload)
                self.assertEqual(result["input_delivery"], receipt["input_delivery"])
                self.assertEqual(result["input_id"], payload["input_id"] if refused else "one")
                self.assertIn("checkpoint failed", result["evidence_error"])
                self.assertEqual(result["evidence_capacity"]["remaining_events"], 50)


class RecipeTransportTests(unittest.TestCase):
    def test_debian_observation_reads_do_not_reset_the_controller_deadline(self):
        for action in ("status", "screenshot"):
            with self.subTest(action=action):
                now = [100.0]
                def receive(_size):
                    now[0] += .75
                    return b'{"state":'
                connection = Mock()
                connection.recv.side_effect = receive
                with patch.object(client.runtime, "configuration"), \
                        patch.object(client.runtime, "runtime_directory", return_value=Path("/fixture")), \
                        patch.object(client.runtime.socket, "socket") as socket, \
                        patch.object(client.runtime.time, "monotonic", side_effect=lambda: now[0]):
                    socket.return_value.__enter__.return_value = connection
                    with self.assertRaisesRegex(ValueError, "request deadline expired"):
                        client.runtime.request({"action": action, "deadline": 100.5})
                connection.recv.assert_called_once()
                self.assertTrue(all(0 < call.args[0] <= .5 for call in connection.settimeout.call_args_list))

    def test_debian_status_forwards_budget_and_invalid_deadlines_never_connect(self):
        with patch.object(client.runtime, "configuration"), patch.object(client.runtime, "request", return_value={"state": "running"}) as request:
            client.runtime.status(deadline=123)
            request.assert_called_once_with({"action": "status", "deadline": 123})
        for value in (True, "123", float("nan"), float("inf"), 10 ** 1000):
            with self.subTest(value=value), patch.object(client.runtime.socket, "socket") as socket, \
                    self.assertRaisesRegex(ValueError, "finite monotonic"):
                client.runtime.request({"action": "screenshot", "deadline": value})
            socket.assert_not_called()


class WindowFollowupRecipeTests(unittest.TestCase):
    process_clock = {"boot_id": "11111111-1111-1111-1111-111111111111", "monotonic_scope": "linux-boot"}

    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.parser = argparse.ArgumentParser()
        desktop_cli.add_desktop_subparser(self.parser.add_subparsers())
        self.current = {"state": "running", "generation": "g", "control_revision": "r",
                        "input_capabilities": ["observation-input"]}
        self.completed = {"input_delivery": "completed", "input_id": "one",
                          "input_completed_monotonic_ns": 42, "geometry": [800, 600]}
        self.capture = {"generation": "g", "control_revision": "r", "geometry": [800, 600],
                        "window": {"id": "0x2a", "geometry": [400, 300], "origin": [100, 80]},
                        "image_geometry": [400, 300], "image_origin": [100, 80],
                        "coordinate_space": "desktop", "observation_id": "b" * 32,
                        "output": str(self.home / "next.png"), "unneeded_status": "omit"}

    def args(self, *extra, native=False):
        return self.parser.parse_args(["desktop", *(["--native"] if native else []), "input",
            "--generation", "g", "--geometry", "800", "600", "--observation", "a" * 32,
            "click", "--x", "120", "--y", "110", *extra])

    def process_receipt(self, pid, ticks, *, clock=None):
        path = self.home / f"{pid}.json"
        path.write_text(json.dumps({"schema_version": 1, "kind": "process-identity",
                                    "clock": self.process_clock if clock is None else clock,
                                    "process": {"pid": pid, "start_ticks": ticks}}))
        return str(path)

    def test_quit_waits_for_the_task_on_both_backends_without_a_capture(self):
        for backend, generation in ((client.runtime, "g"), (native_session, None)):
            for state, timed_out in (("completed", False), ("running", True), ("failed", False)):
                with self.subTest(backend=backend, state=state), \
                        patch.object(backend, "status", return_value=self.current), \
                        patch.object(backend, "request", return_value=self.completed) as request, \
                        patch.object(development, "task_status", return_value={
                            "state": "running", "ok": True, "session_generation": generation}) as status, \
                        patch.object(development, "wait_task", return_value={
                            "state": state, "ok": state == "completed", "wait_timed_out": timed_out}) as wait, \
                        patch.object(client, "artifact_path") as allocate, redirect_stdout(out := StringIO()):
                    task_id = "1" * 32
                    args = self.args("--observe-task", task_id, "--task-timeout", "3", native=backend is native_session)
                    self.assertEqual(desktop_cli.run_desktop_command(args), int(state != "completed"))
                    result = json.loads(out.getvalue())
                    self.assertEqual(result["input_delivery"], "completed")
                    self.assertEqual(result["task_observation"]["state"], state)
                    self.assertNotIn("observation", result)
                    request.assert_called_once()
                    self.assertEqual(request.call_args.args[0]["action"], "input")
                    status.assert_called_once_with(task_id)
                    wait.assert_called_once_with(task_id, timeout=3)
                    allocate.assert_not_called()

    def test_other_desktop_task_is_refused_before_quit_input(self):
        with patch.object(client.runtime, "status", return_value=self.current), \
                patch.object(client.runtime, "request") as request, \
                patch.object(development, "task_status", return_value={
                    "state": "running", "ok": True, "session_generation": "other"}), \
                patch.object(development, "wait_task") as wait, redirect_stdout(out := StringIO()):
            self.assertEqual(desktop_cli.run_desktop_command(self.args("--observe-task", "1" * 32)), 2)
        self.assertEqual(json.loads(out.getvalue())["input_delivery"], "not-sent")
        request.assert_not_called()
        wait.assert_not_called()

    def test_process_quit_observes_both_identities_after_one_input_without_capture(self):
        for backend in (client.runtime, native_session):
            with self.subTest(backend=backend), patch.object(backend, "status", return_value=self.current), \
                    patch.object(backend, "request", return_value=self.completed) as request, \
                    patch.object(qa_artifacts, "clock", return_value=self.process_clock), \
                    patch.object(qa_artifacts, "process_identity") as identity, \
                    patch.object(client, "artifact_path") as allocate, redirect_stdout(out := StringIO()):
                def sample(pid):
                    if not request.called:
                        return {"pid": pid, "start_ticks": str(pid * 10)}
                    if pid == 123:
                        raise ProcessLookupError
                    if pid == 234:
                        return {"pid": pid, "start_ticks": "9999"}
                    raise qa_artifacts.ProcessExited({"pid": pid, "start_ticks": "3450"})

                identity.side_effect = sample
                args = self.args("--observe-process", self.process_receipt(123, "1230"),
                                 "--observe-process", self.process_receipt(234, "2340"),
                                 "--observe-process", self.process_receipt(345, "3450"),
                                 native=backend is native_session)
                self.assertEqual(desktop_cli.run_desktop_command(args), 0)
                result = json.loads(out.getvalue())
                self.assertEqual({key: result[key] for key in self.completed}, self.completed)
                observation = result["process_observation"]
                self.assertTrue(observation["ok"])
                self.assertEqual(observation["timeout_seconds"], 10)
                self.assertEqual([(row["pid"], row["state"], row["normal_exit"]) for row in observation["processes"]],
                                 [(123, "identity-not-present", "unknown"), (234, "identity-replaced", "unknown"),
                                  (345, "identity-exited", "unknown")])
                request.assert_called_once()
                self.assertEqual(request.call_args.args[0]["action"], "input")
                allocate.assert_not_called()
                self.assertNotIn("observation", result)

    def test_process_identity_checks_unreaped_process_start_ticks(self):
        open_file = os.open
        with tempfile.TemporaryDirectory() as folder:
            process_stat = Path(folder) / "stat"
            prefix = b"123 (game ) wrapper) Z " + b"0 " * 16
            process_stat.write_bytes(prefix + b"1 0 1230\n")

            def open_proc(path, flags, **kwargs):
                return open_file(folder if path == "/proc/123" else path, flags, **kwargs)

            with patch.object(qa_artifacts.os, "open", side_effect=open_proc):
                self.assertEqual(qa_artifacts.observe_process_identity(123, "1230"), {
                    "pid": 123, "expected_start_ticks": "1230", "normal_exit": "unknown", "state": "identity-exited",
                    "observed": {"pid": 123, "start_ticks": "1230"},
                })
                self.assertEqual(qa_artifacts.observe_process_identity(123, "1229")["state"], "identity-replaced")
                with self.assertRaises(ValueError):
                    qa_artifacts.process_identity(123)
                process_stat.write_bytes(prefix + b"2 0 1230\n")
                self.assertEqual(qa_artifacts.observe_process_identity(123, "1230")["state"], "unavailable")

    def test_process_quit_timeout_retains_completed_input_and_partial_observation(self):
        now = [100.0]

        def advance(delay):
            now[0] += delay

        with patch.object(native_session, "status", return_value=self.current), \
                patch.object(native_session, "request", return_value=self.completed) as request, \
                patch.object(qa_artifacts, "clock", return_value=self.process_clock), \
                patch.object(qa_artifacts, "process_identity") as identity, \
                patch.object(client.time, "monotonic", side_effect=lambda: now[0]), \
                patch.object(client.time, "sleep", side_effect=advance), \
                redirect_stdout(out := StringIO()):
            def sample(pid):
                if request.called and pid == 123:
                    raise FileNotFoundError
                if request.called and pid == 345:
                    raise PermissionError("proc stat unreadable")
                return {"pid": pid, "start_ticks": str(pid * 10)}

            identity.side_effect = sample
            args = self.args("--observe-process", self.process_receipt(123, "1230"),
                             "--observe-process", self.process_receipt(234, "2340"),
                             "--observe-process", self.process_receipt(345, "3450"),
                             "--process-timeout", "1", native=True)
            self.assertEqual(desktop_cli.run_desktop_command(args), 1)
        result = json.loads(out.getvalue())
        self.assertEqual({key: result[key] for key in self.completed}, self.completed)
        self.assertEqual(result["dispatch_stage"], "post-input-process-observation")
        observation = result["process_observation"]
        self.assertTrue(observation["wait_timed_out"])
        self.assertEqual([row["state"] for row in observation["processes"]],
                         ["identity-not-present", "running-preserve", "unavailable"])
        self.assertEqual(now[0], 101.0)
        self.assertEqual([call.args for call in identity.call_args_list].count((123,)), 2)
        request.assert_called_once()
        self.assertEqual(request.call_args.args[0]["action"], "input")

    def test_process_quit_refuses_a_stale_identity_and_skips_the_wait_on_unconfirmed_delivery(self):
        with patch.object(native_session, "status", return_value=self.current), \
                patch.object(native_session, "request", return_value={"input_delivery": "unknown"}) as request, \
                patch.object(qa_artifacts, "clock", return_value=self.process_clock), \
                patch.object(qa_artifacts, "process_identity", return_value={"pid": 123, "start_ticks": "1230"}) as identity:
            with redirect_stdout(out := StringIO()):
                path = self.process_receipt(123, "old")
                self.assertEqual(desktop_cli.run_desktop_command(self.args("--observe-process", path, native=True)), 2)
            self.assertEqual(json.loads(out.getvalue())["input_delivery"], "not-sent")
            with redirect_stdout(out := StringIO()):
                path = self.process_receipt(123, "1230", clock={"boot_id": "22222222-2222-2222-2222-222222222222"})
                self.assertEqual(desktop_cli.run_desktop_command(self.args("--observe-process", path, native=True)), 2)
            self.assertIn("current Linux boot", json.loads(out.getvalue())["error"])
            with redirect_stdout(out := StringIO()):
                path = self.process_receipt(123, "1229")
                self.assertEqual(desktop_cli.run_desktop_command(self.args("--observe-process", path, native=True)), 2)
            self.assertEqual(json.loads(out.getvalue())["input_delivery"], "not-sent")
            request.assert_not_called()
            identity.assert_called_once_with(123)
            identity.reset_mock()
            with redirect_stdout(out := StringIO()):
                path = self.process_receipt(123, "1230")
                self.assertEqual(desktop_cli.run_desktop_command(self.args("--observe-process", path, native=True)), 1)
            result = json.loads(out.getvalue())
            self.assertEqual(result["reason"], "input-completion-unverified")
            self.assertNotIn("process_observation", result)
            request.assert_called_once()
            identity.assert_called_once_with(123)

    @unittest.skipUnless(Path("/usr/bin/ffmpeg").is_file(), "ffmpeg is unavailable")
    def test_preview_retains_original_and_maps_odd_size_window_from_receipt(self):
        original = Path(self.capture["output"])
        body = png_regions.encode(13, 7, b"\xa0\xb0\xc0" * 13 * 7)
        original.write_bytes(body)
        capture = {**self.capture, "image_geometry": [13, 7], "artifact_sha256": qa_artifacts.file_digest(original)}
        live = {**self.current, **capture, "capture_freshness": {"fresh": True}}
        with patch.object(client.runtime, "status", return_value=live), \
                patch.object(client.runtime, "request", return_value=capture) as request, \
                patch.object(capture_preview, "new_bundle", side_effect=lambda: qa_artifacts.new_bundle(str(self.home / "preview"))):
            args = self.parser.parse_args(["desktop", "screenshot", "--compact", "--preview-width", "5", "--output", str(original)])
            with redirect_stdout(out := StringIO()):
                self.assertEqual(desktop_cli.run_desktop_command(args), 0)
            saved = self.home / "frame.json"
            saved.write_text(out.getvalue())
            receipt = json.loads(out.getvalue())
            preview = receipt["preview"]
            self.assertEqual(preview["geometry"], [5, 2])
            self.assertEqual(png_regions.decode(Path(preview["output"]).read_bytes())[:2], (5, 2))
            self.assertEqual(preview["source_sha256"], capture["artifact_sha256"])
            self.assertEqual(preview["artifact_sha256"], qa_artifacts.file_digest(Path(preview["output"])))
            self.assertEqual(original.read_bytes(), body)
            args = self.parser.parse_args(["desktop", "map-point", "--capture", str(saved),
                                           "--preview-coordinates", "--x", "3", "--y", "1"])
            with redirect_stdout(out := StringIO()):
                self.assertEqual(desktop_cli.run_desktop_command(args), 0)
            mapping = json.loads(out.getvalue())
        self.assertEqual(mapping["input_coordinates"], {"x": 107, "y": 83})
        self.assertEqual(mapping["observation_id"], capture["observation_id"])
        self.assertEqual(mapping["scale"], preview["mapping"]["scale"])
        self.assertEqual(mapping["point_space"], "preview-image")
        request.assert_called_once()

    def test_failed_followup_preview_retains_original_observation_and_input(self):
        with patch.object(client.runtime, "status", return_value=self.current), \
                patch.object(client.runtime, "request", side_effect=[self.completed, self.capture]) as request, \
                patch.object(client.time, "sleep"), \
                patch.object(capture_preview, "make_preview", side_effect=RuntimeError("Resize failed")), \
                redirect_stdout(out := StringIO()):
            args = self.args("--observe", "--preview-width", "400", "--output", self.capture["output"])
            self.assertEqual(desktop_cli.run_desktop_command(args), 1)
        receipt = json.loads(out.getvalue())
        self.assertEqual(receipt["input_delivery"], "completed")
        self.assertEqual(receipt["observation"]["observation_id"], self.capture["observation_id"])
        self.assertEqual(receipt["observation"]["output"], self.capture["output"])
        self.assertEqual(receipt["observation"]["preview_error"], "Resize failed")
        self.assertEqual([call.args[0]["action"] for call in request.call_args_list], ["input", "screenshot"])

    def test_failed_standalone_preview_retains_the_original_mapping_receipt(self):
        capture = {**self.capture, "ok": True, "error": None}

        def save_capture(payload):
            width, height = capture["image_geometry"]
            Path(payload["output"]).write_bytes(png_regions.encode(width, height, bytes(width * height * 3)))
            return capture

        with patch.object(Path, "home", return_value=self.home), \
                patch.object(client.runtime, "status", return_value=self.current), \
                patch.object(client.runtime, "request", side_effect=save_capture), \
                patch.object(capture_preview, "make_preview", side_effect=RuntimeError("Resize failed")), \
                redirect_stdout(out := StringIO()):
            args = self.parser.parse_args(["desktop", "screenshot", "--compact", "--preview-width", "200",
                                           "--output", capture["output"]])
            self.assertEqual(desktop_cli.run_desktop_command(args), 1)
        result = json.loads(out.getvalue())
        self.assertEqual(result["stage"], "capture_preview")
        self.assertEqual(result["effect"], "applied")
        self.assertEqual(result["error"], "Resize failed")
        self.assertEqual([item["kind"] for item in result["artifacts"]], ["screenshot", "capture_receipt"])
        self.assertTrue(Path(result["artifacts"][0]["path"]).is_file())
        mapping_receipt = json.loads(Path(result["artifacts"][1]["path"]).read_text())
        self.assertEqual(mapping_receipt["observation_id"], capture["observation_id"])

    def test_selected_window_followup_keeps_mapping_and_completed_input(self):
        with patch.object(client.runtime, "status", return_value=self.current), \
                patch.object(client.runtime, "request", side_effect=[self.completed, self.capture]) as request, \
                patch.object(client.time, "sleep"), redirect_stdout(out := StringIO()):
            code = desktop_cli.run_desktop_command(self.args("--observe", "--observe-window", "42",
                "--output", self.capture["output"]))
        self.assertEqual(code, 0)
        result = json.loads(out.getvalue())
        self.assertEqual(result["input_id"], "one")
        self.assertEqual(result["input_completed_monotonic_ns"], 42)
        self.assertEqual(result["geometry"], [800, 600])
        followup = result["observation"]
        for key in ("geometry", "image_geometry", "image_origin", "window", "coordinate_space", "observation_id"):
            self.assertEqual(followup[key], self.capture[key])
        self.assertNotIn("unneeded_status", followup)
        sent, captured = [call.args[0] for call in request.call_args_list]
        self.assertEqual(sent["action"], "input")
        self.assertEqual(sent["observation_id"], "a" * 32)
        self.assertNotIn("window", sent)
        self.assertEqual(captured, {"action": "screenshot", "generation": "g", "control_revision": "r",
                                    "output": self.capture["output"], "window": "0x2a"})

    def test_invalid_window_options_are_refused_before_input_or_output_allocation(self):
        cases = [(False, ["--observe-window", "42"], "require --observe"),
                 (True, ["--observe", "--observe-window", "42"], "requires the Debian desktop"),
                 (False, ["--observe", "--observe-window", "title"], "decimal or hexadecimal")]
        for native, extra, message in cases:
            with self.subTest(native=native, extra=extra), \
                    patch.object(client.runtime, "status", return_value=self.current), \
                    patch.object(native_session, "status", return_value=self.current), \
                    patch.object(client.runtime, "request") as debian_request, \
                    patch.object(native_session, "request") as native_request, \
                    patch.object(client, "artifact_path") as allocate, \
                    redirect_stdout(out := StringIO()):
                self.assertEqual(desktop_cli.run_desktop_command(self.args(*extra, native=native)), 2)
                result = json.loads(out.getvalue())
                self.assertIn(message, result["error"])
                self.assertEqual(result["input_delivery"], "not-sent")
                self.assertEqual(result["dispatch_stage"], "before-input")
                debian_request.assert_not_called()
                native_request.assert_not_called()
                allocate.assert_not_called()

    def test_capture_failure_or_wrong_window_never_replays_or_falls_back(self):
        for capture in (RuntimeError("Window is no longer visible"),
                        {**self.capture, "window": None}, {**self.capture, "window": {"id": "0x99"}}):
            output = Path(self.capture["output"])

            def request_capture(payload):
                if payload["action"] == "input":
                    return self.completed
                if isinstance(capture, RuntimeError):
                    raise capture
                output.write_bytes(png_regions.encode(1, 1, b"\x01\x02\x03"))
                return capture

            with self.subTest(capture=capture), patch.object(client.runtime, "status", return_value=self.current), \
                    patch.object(client.runtime, "request", side_effect=request_capture) as request, \
                    patch.object(client.time, "sleep"), redirect_stdout(out := StringIO()):
                self.assertEqual(desktop_cli.run_desktop_command(self.args("--observe", "--observe-window", "0x2A",
                    "--output", self.capture["output"])), 1)
                result = json.loads(out.getvalue())
                self.assertEqual(result["input_id"], "one")
                self.assertEqual(result["input_completed_monotonic_ns"], 42)
                self.assertEqual(result["input_delivery"], "completed")
                self.assertEqual(result["dispatch_stage"], "post-input-observation")
                self.assertIn("Do not replay input", result["error"])
                self.assertNotIn("observation", result)
                if output.is_file():
                    self.assertFalse(result["observation_failure"]["ok"])
                    self.assertEqual(result["observation_failure"]["output"], str(output))
                    self.assertNotIn("observation_id", result["observation_failure"])
                    self.assertEqual(result["artifacts"], [
                        {"kind": "screenshot", "path": str(output), "media_type": "image/png"}])
                    output.unlink()
                else:
                    self.assertNotIn("artifacts", result)
                self.assertEqual([call.args[0]["action"] for call in request.call_args_list], ["input", "screenshot"])
                self.assertEqual(request.call_args.args[0]["window"], "0x2a")

    def test_human_control_change_prevents_followup_capture(self):
        with patch.object(client.runtime, "status", side_effect=[self.current, {**self.current, "control_revision": "new"}]), \
                patch.object(client.runtime, "request", return_value=self.completed) as request, \
                patch.object(client.time, "sleep"), redirect_stdout(out := StringIO()):
            self.assertEqual(desktop_cli.run_desktop_command(self.args("--observe", "--observe-window", "42",
                "--output", self.capture["output"])), 1)
        result = json.loads(out.getvalue())
        self.assertEqual(result["input_delivery"], "completed")
        self.assertIn("control changed", result["error"])
        request.assert_called_once()
        self.assertEqual(request.call_args.args[0]["action"], "input")


class EvidenceDirectoryRecipeTests(unittest.TestCase):
    def test_parent_errors_explain_remedy_without_creating_or_changing_parents(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            root.chmod(0o700)
            missing = root / "missing"
            regular = root / "file"
            regular.write_text("keep")
            public = root / "public"
            public.mkdir(mode=0o755)
            public.chmod(0o755)
            for parent, reason in ((missing, "does not exist"), (regular, "not a directory"),
                                   (regular / "nested", "not a directory"), (public, "not private")):
                with self.subTest(parent=parent), self.assertRaisesRegex(ValueError, reason) as failure:
                    qa_artifacts.new_bundle(str(parent / "new-bundle"))
                self.assertIn("mode 0700 outside Git", str(failure.exception))
                self.assertIn("omit --output when optional", str(failure.exception))
            self.assertFalse(missing.exists())
            self.assertEqual(regular.read_text(), "keep")
            self.assertEqual(public.stat().st_mode & 0o777, 0o755)
            self.assertEqual(list(public.iterdir()), [])
            with patch.object(qa_artifacts.os, "getuid", return_value=root.stat().st_uid + 1), \
                    self.assertRaisesRegex(ValueError, "not owned by the current account"):
                qa_artifacts.new_bundle(str(root / "foreign-output"))
            self.assertFalse((root / "foreign-output").exists())
            bundle = qa_artifacts.new_bundle(str(root / "valid-output"))
            self.assertEqual(bundle.stat().st_mode & 0o777, 0o700)


class PublishedRecipeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parser = argparse.ArgumentParser()
        desktop_cli.add_desktop_subparser(cls.parser.add_subparsers())

    def test_malformed_sequence_input_kind_returns_validation_without_dispatch(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "steps.json"
            for backend, route in ((client.runtime, []), (native_session, ["--native"])):
                for kind in ([], {}, None, "unsupported"):
                    with self.subTest(route=route, kind=kind), \
                            patch.object(backend, "status") as status, patch.object(backend, "request") as request:
                        path.write_text(json.dumps([{"action": "input", "kind": "key", "key": "a", "geometry": [800, 600]},
                                                    {"action": "input", "kind": kind, "geometry": [800, 600]}]))
                        for mode in ([], ["--json"]):
                            args = self.parser.parse_args(["desktop", *route, "sequence", str(path), "--generation", "g", *mode])
                            with redirect_stdout(out := StringIO()):
                                self.assertEqual(desktop_cli.run_desktop_command(args), 2)
                            result = json.loads(out.getvalue())
                            self.assertEqual(result["issue"], "invalid_input")
                            self.assertEqual(result["stage"], "validation")
                            self.assertEqual(result["effect"], "none")
                            self.assertIn("Sequence input kind", result["error"])
                        status.assert_not_called()
                        request.assert_not_called()

    def test_unverified_sequence_png_is_reachable_with_completed_input_receipt(self):
        current = {"state": "running", "generation": "g", "control_revision": "r", "agent_control": "enabled"}
        completed = {"input_id": "one", "input_delivery": "completed", "ok": True, "error": None}

        def capture_or_input(payload):
            if payload["action"] == "input":
                return completed
            Path(payload["output"]).write_bytes(png_regions.encode(1, 1, b"\x01\x02\x03"))
            return {"output": payload["output"], "observation_id": "b" * 32, "ok": True,
                    "initial_frame_skipped": False}

        with tempfile.TemporaryDirectory() as folder, patch.object(tempfile, "tempdir", folder), \
                patch.object(native_session, "status", return_value=current), \
                patch.object(native_session, "request", side_effect=capture_or_input) as request, \
                redirect_stdout(out := StringIO()):
            path = Path(folder) / "steps.json"
            image = str(Path(folder) / "frame.png")
            path.write_text(json.dumps([{"action": "input", "kind": "key", "key": "a", "geometry": [800, 600]},
                                        {"action": "screenshot", "output": image, "next_frame": True}, {"action": "windows"}]))
            args = self.parser.parse_args(["desktop", "--native", "sequence", str(path), "--generation", "g", "--save-output"])
            self.assertEqual(desktop_cli.run_desktop_command(args), 1)
            summary = json.loads(out.getvalue())
            result = json.loads(Path(summary["artifact"]).read_text())
            self.assertTrue(Path(image).is_file())
        self.assertEqual([call.args[0]["action"] for call in request.call_args_list], ["input", "screenshot"])
        self.assertEqual(summary["completed"], 1)
        self.assertEqual(summary["stage"], "capture_verification")
        self.assertEqual(summary["effect"], "partial")
        self.assertEqual(result["results"][0]["input_id"], "one")
        self.assertEqual(result["failed_action"]["output"], image)
        self.assertFalse(result["failed_action"]["ok"])
        self.assertEqual(summary["artifacts"][1:], [{"kind": "screenshot", "path": image, "media_type": "image/png"}])
        self.assertEqual(result["artifacts"], summary["artifacts"][1:])

    def test_compact_next_frame_capture_keeps_the_original_backend_failure(self):
        current = {"state": "running", "generation": "g", "control_revision": "r", "agent_control": "enabled"}
        with tempfile.TemporaryDirectory() as folder:
            image = str(Path(folder) / "frame.png")
            failure = {"ok": False, "error": "PipeWire timed out", "output": image, "stage": "acquire_frame", "effect": "none"}
            with patch.object(native_session, "status", return_value=current), \
                    patch.object(native_session, "request", return_value=failure), redirect_stdout(out := StringIO()):
                args = self.parser.parse_args(["desktop", "--native", "screenshot", "--next-frame", "--compact", "--output", image])
                self.assertEqual(desktop_cli.run_desktop_command(args), 1)
            result = json.loads(out.getvalue())
            self.assertEqual(result["error"], failure["error"])
            self.assertEqual(result["stage"], failure["stage"])
            self.assertEqual(result["effect"], failure["effect"])
            self.assertNotIn("artifacts", result)

    def test_published_linux_desktop_recipes_parse_without_execution(self):
        files = [ROOT / "docs/workstations" / name for name in
                 ("desktop-observations.md", "desktop-evaluation.md", "desktop-control.md", "cachyos-desktop.md",
                  "short-observation-sequences.md")]
        files += [ROOT / "common/agent_skills" / name for name in
                  ("basaltwater-cachyos-desktop/references/native-control.md",
                   "basaltwater-cachyos-desktop/references/native-control-recipes.md",
                   "basaltwater-desktop/references/windows.md")]
        files.append(ROOT / "common/agent_skills/basaltwater-desktop/references/session.md")
        files.append(ROOT / "docs/plans/game-testing-handoffs/next-cycle-handoff-contract.md")
        count = 0
        replacements = {"WIDTH": "2400", "HEIGHT": "1600", "X": "100", "Y": "100"}
        for name in files:
            source = name.read_text()
            for language, block in re.findall(r"```([^\n]*)\n(.*?)```", source, re.S):
                if language not in ("bash", "fish", "text"):
                    continue
                for line in block.replace("\\\n", " ").splitlines():
                    if not line.lstrip().startswith("basaltw desktop "):
                        continue
                    words = shlex.split(line, comments=True)
                    if words[:2] != ["basaltw", "desktop"]:
                        continue
                    # Shell redirects are not command arguments. Paths and IDs
                    # stay fictional, while documented numeric placeholders bind.
                    if ">" in words:
                        words = words[:words.index(">")]
                    words = [replacements.get(word, word) for word in words[1:]]
                    with self.subTest(file=name, command=line):
                        self.parser.parse_args(words)
                    count += 1
        self.assertGreater(count, 0)

    def test_published_pair_json_executes_and_saves_partial_evidence(self):
        source = (ROOT / "docs/workstations/short-observation-sequences.md").read_text()
        steps = json.loads(re.search(r"```json\n(.*?)```", source, re.S).group(1))
        current = {"state": "running", "generation": "g", "control_revision": "r", "agent_control": "enabled",
                   "input_capabilities": ["observation-input"]}
        completed = {"input_id": "one", "input_delivery": "completed"}
        with tempfile.TemporaryDirectory() as folder, patch.object(Path, "home", return_value=Path(folder)), \
                patch.object(native_session, "status", return_value=current), patch.object(client.time, "sleep"), \
                patch.object(native_session, "request", side_effect=[completed, {"initial_frame_skipped": True},
                                                                    RuntimeError("second capture failed")]) as request:
            path = Path(folder) / "steps.json"
            path.write_text(json.dumps(steps))
            with redirect_stdout(out := StringIO()):
                code = desktop_cli.run_desktop_command(self.parser.parse_args(
                    ["desktop", "--native", "sequence", str(path), "--generation", "g", "--save-output"]))
            self.assertEqual(code, 1)
            short = json.loads(out.getvalue())
            saved = json.loads(Path(short["artifact"]).read_text())
            receipt = saved["results"][0]
            self.assertEqual({key: receipt[key] for key in completed}, completed)
            self.assertEqual(receipt["timing"]["worker_mode"], "resident")
            self.assertGreaterEqual(receipt["timing"]["input_round_trip_ms"], 0)
            self.assertEqual(short["completed"], 2)
            self.assertEqual(short["failed_action_index"], 2)
            self.assertEqual(len(saved["timings"]), 3)
            self.assertEqual(request.call_count, 3)

    def test_failed_result_save_keeps_completed_input_and_capture_receipts(self):
        full = {"generation": "g", "completed": 2, "results": [
            {"input_delivery": "completed", "input_id": "one"}, {"output": "/private/frame.png"}],
            "error": "second capture failed", "failed_action_index": 2, "failed_action": {"error": "capture failed"}}
        with patch.object(native_session, "status", return_value={"state": "running", "generation": "g", "control_revision": "r"}), \
                patch.object(client, "run_sequence", return_value=full), \
                patch.object(observations, "save_observation", side_effect=OSError("disk full")), redirect_stdout(out := StringIO()):
            args = self.parser.parse_args(["desktop", "--native", "sequence", "/private/steps.json", "--generation", "g", "--save-output"])
            self.assertEqual(desktop_cli.run_desktop_command(args), 1)
        result = json.loads(out.getvalue())
        self.assertEqual(result["results"], full["results"])
        self.assertEqual(result["error"], "second capture failed")
        self.assertEqual(result["output_save_error"], "disk full")

    def test_handoff_native_route_is_separate_from_permission_and_xrdp(self):
        for mode in ("enabled", "human-override", "disabled"):
            with tempfile.TemporaryDirectory() as folder, patch.object(Path, "home", return_value=Path(folder)), \
                    patch.object(native_session, "check_session"), patch.object(native_session, "start") as start, \
                    patch.object(desktop_cli.runtime, "status", side_effect=AssertionError("XRDP must not be used")), \
                    patch.object(desktop_cli.subprocess, "Popen", return_value=Mock(pid=123)) as spawn:
                before = control_state.change(mode, human_approved=True)
                with redirect_stdout(out := StringIO()):
                    code = desktop_cli.run_desktop_command(self.parser.parse_args(["desktop", "--native", "handoff"]))
                result = json.loads(out.getvalue())
                self.assertEqual(code, 0)
                self.assertFalse(result["permission_changed"])
                self.assertFalse(result["ownership_transferred"])
                self.assertEqual(result["window_visibility"], "unverified")
                self.assertEqual(control_state.status(), before)
                self.assertTrue(spawn.call_args.args[0][1].endswith("native_handoff.py"))
                start.assert_not_called()

    def test_actionable_route_and_mapping_errors_do_not_send_requests(self):
        with patch.object(desktop_cli.runtime, "status", side_effect=RuntimeError("Shared desktop is not configured")), \
                patch.object(native_session, "request") as request, redirect_stdout(out := StringIO()):
            self.assertEqual(desktop_cli.run_desktop_command(self.parser.parse_args(["desktop", "handoff"])), 1)
            self.assertIn("desktop --native handoff", json.loads(out.getvalue())["hint"])
            request.assert_not_called()
        for extra in (["--geometry", "2400", "1600"], ["--crop", "0", "0", "10", "10"]):
            with patch.object(native_session, "status") as status, redirect_stdout(out := StringIO()):
                args = self.parser.parse_args(["desktop", "--native", "map-point", "--capture", "/absent/frame.json",
                    "--view-geometry", "1200", "800", "--x", "1", "--y", "1", *extra])
                self.assertEqual(desktop_cli.run_desktop_command(args), 2)
                self.assertIn("omit --geometry and --crop", json.loads(out.getvalue())["error"])
                status.assert_not_called()
        with redirect_stderr(message := StringIO()), self.assertRaises(SystemExit) as error:
            self.parser.parse_args(["desktop", "handoff", "--native"])
        self.assertEqual(error.exception.code, 2)
        self.assertIn("Place --native before handoff", message.getvalue())

    def test_sequence_returns_same_partial_receipts_in_both_json_modes(self):
        before = {"completed": 1, "results": [{"input_delivery": "completed"}], "error": "Frame unavailable",
                  "failed_action": {"input_delivery": "not-sent"}}
        results = []
        for options in ([], ["--json"]):
            with patch.object(client, "run_sequence", return_value=before), redirect_stdout(out := StringIO()):
                args = self.parser.parse_args(["desktop", "sequence", "/private/steps.json", "--generation", "g", *options])
                self.assertEqual(desktop_cli.run_desktop_command(args), 1)
            results.append(json.loads(out.getvalue()))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0]["results"], before["results"])
        self.assertEqual(results[0]["effect"], "partial")

    def test_debian_handoff_uses_fixed_ui_action_while_ordinary_exec_stays_blocked(self):
        for mode in ("enabled", "human-override", "disabled"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder, \
                    patch.object(Path, "home", return_value=Path(folder)), \
                    patch.object(client.runtime.subprocess, "Popen", return_value=Mock(pid=123)) as spawn:
                before = control_state.change(mode, seconds=None, human_approved=True)
                process = Mock(pid=456)
                process.poll.return_value = None
                session = client.runtime.DesktopSession({"desktop": "xfce"}, process)
                current = {"state": "running", "generation": session.generation, **before}
                with patch.object(client.runtime, "status", return_value=current), \
                        patch.object(client.runtime, "request", side_effect=session.handle) as request, \
                        redirect_stdout(out := StringIO()):
                    code = desktop_cli.run_desktop_command(self.parser.parse_args(["desktop", "handoff"]))
                result = json.loads(out.getvalue())
                self.assertEqual(code, 0)
                self.assertEqual(request.call_args.args[0]["action"], "handoff")
                self.assertNotIn("argv", request.call_args.args[0])
                self.assertFalse(result["permission_changed"])
                self.assertFalse(result["ownership_transferred"])
                self.assertEqual(result["window_visibility"], "unverified")
                self.assertEqual(control_state.status(), before)
                self.assertTrue(spawn.call_args.args[0][1].endswith("handoff.py"))
                self.assertEqual(spawn.call_args.kwargs["process_group"], 456)
                self.assertEqual(session.children, [spawn.return_value])
                if mode != "enabled":
                    with self.assertRaisesRegex(RuntimeError, "Agent desktop control"):
                        session.handle({"action": "exec", "generation": session.generation, **before, "argv": ["unavailable"]})
                    spawn.assert_called_once()

    def test_old_debian_handoff_suggests_launcher_without_replaying_exec(self):
        with patch.object(client.runtime, "status", return_value={"state": "running", "generation": "g", "control_revision": "r"}), \
                patch.object(client.runtime, "request", side_effect=ValueError("Unknown desktop action")) as request, \
                redirect_stdout(out := StringIO()):
            self.assertEqual(desktop_cli.run_desktop_command(self.parser.parse_args(["desktop", "handoff"])), 1)
        result = json.loads(out.getvalue())
        self.assertIn("Shared Desktop Control launcher", result["hint"])
        self.assertNotIn("--native", result["hint"])
        request.assert_called_once()

    def test_png_and_directory_are_not_mistaken_for_json_mapping_receipts(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(native_session, "status") as status:
            image = Path(folder) / "frame.png"
            image.write_bytes(b"\x89PNG\r\n\x1a\n")
            for path in (str(image), folder):
                with self.subTest(path=path), redirect_stdout(out := StringIO()):
                    args = self.parser.parse_args(["desktop", "--native", "map-point", "--capture", path,
                        "--view-geometry", "1200", "800", "--x", "1", "--y", "1"])
                    self.assertEqual(desktop_cli.run_desktop_command(args), 2)
                    self.assertIn("saved JSON receipt", json.loads(out.getvalue())["error"])
            status.assert_not_called()


if __name__ == "__main__":
    unittest.main()
