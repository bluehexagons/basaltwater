"""Offline contracts for private QA media, selected audio links and clocks."""

from __future__ import annotations

import json
import argparse
import io
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
from unittest.mock import Mock
from contextlib import ExitStack

from desktop import audio_analysis, audio_capture, audio_capture_worker, evidence_media, qa_artifacts, qa_timeline, preflight, run_checkpoint, evidence
from lib import desktop_cli


class QAMediaTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.root.chmod(0o700)

    def test_instruction_pages_reconstruct_unicode_and_refuse_changed_files(self):
        source = self.root / "instructions.md"
        text = "詳しい instructions\n" * 1000
        source.write_text(text)
        first = preflight.instruction_page(str(source))
        self.assertEqual(first["unread_ranges"], [[4000, len(text)]])
        parts, start = [], 0
        while start < len(text):
            page = preflight.instruction_page(str(source), start=start, sha256=first["sha256"])
            parts.append(page["text"])
            start = page["range"][1]
        self.assertEqual("".join(parts), text)
        source.write_text("changed")
        with self.assertRaisesRegex(ValueError, "digest changed"):
            preflight.instruction_page(str(source), sha256=first["sha256"])

    def test_run_continuation_wait_policy_and_late_reviews_preserve_original_clocks(self):
        domain = {"boot_id": "11111111-2222-3333-4444-555555555555", "monotonic_scope": "linux-boot"}
        def at(seconds):
            return {"at": "2026-01-01T00:00:00+00:00", "monotonic_ns": seconds * 10**9}
        with patch.object(run_checkpoint, "clock", return_value=domain), patch.object(run_checkpoint, "stamp") as stamp:
            stamp.return_value = at(1)
            started = run_checkpoint.start("run-a", "match-a", "play", output=str(self.root / "run"), review_seconds=30, hard_seconds=120)
            path = started["artifact"]
            self.assertEqual(started["checkpoint_due_monotonic_ns"], 31 * 10**9)
            self.assertEqual(started["checkpoint_remaining_seconds"], 30)
            stamp.return_value = at(41)
            paused = run_checkpoint.update(path, "suspend", note="Awaiting owner decision")
            self.assertEqual(paused["checkpoint_late_seconds"], 10)
            self.assertEqual(paused["checkpoint_remaining_seconds"], 0)
            stamp.return_value = at(141)
            segments = ["/private/segment-a.json", "/private/segment-b.json"]
            continued = run_checkpoint.update(path, "context", context="new-context", artifacts=segments)
            self.assertEqual(continued["match_id"], "match-a")
            self.assertEqual(continued["latest"]["artifacts"], segments)
            self.assertTrue(continued["hard_limit_reached"])
            self.assertEqual(continued["owner_wait_seconds"], 100)
            stamp.return_value = at(241)
            run_checkpoint.update(path, "resume")
            stamp.return_value = at(251)
            run_checkpoint.update(path, "policy", hard_seconds=500, note="Owner extended same match in current thread")
            stamp.return_value = at(261)
            reviewed = run_checkpoint.update(path, "checkpoint", phase="results", game_clock="05:17")
            self.assertEqual(reviewed["latest"]["checkpoint_late_seconds"], 230)
            self.assertEqual(reviewed["checkpoint_late_seconds"], 0)
            stamp.return_value = at(301)
            finished = run_checkpoint.update(path, "finish")
            self.assertEqual(finished["wall_seconds"], 300)
            self.assertEqual(finished["owner_wait_seconds"], 200)
            self.assertEqual(finished["declared_active_wall_seconds"], 100)
            self.assertEqual(finished["started"], at(1))
            self.assertIsNone(finished["model_reasoning_seconds"])
            self.assertIsNone(finished["observed_active_work_seconds"])
            self.assertFalse(finished["hard_limit_reached"])
            with self.assertRaisesRegex(ValueError, "finished"):
                run_checkpoint.update(path, "context", context="another")
            before = Path(path).read_bytes()
            with patch.object(run_checkpoint, "clock", return_value={**domain, "boot_id": None}):
                self.assertEqual(run_checkpoint.update(path)["timing"], "unsupported-clock")
                with self.assertRaisesRegex(ValueError, "comparable boot clock"):
                    run_checkpoint.update(path, "checkpoint")
            self.assertEqual(Path(path).read_bytes(), before)

    def test_run_parser_records_one_bounded_event_without_contacting_desktop(self):
        parser = argparse.ArgumentParser()
        desktop_cli.add_desktop_subparser(parser.add_subparsers())
        with patch("desktop.session_runtime.status") as status, patch("sys.stdout", new_callable=io.StringIO) as out:
            args = parser.parse_args(["desktop", "evidence", "run", "start", "--run-id", "a", "--match-id", "m",
                "--phase", "play", "--review-seconds", "30", "--hard-seconds", "600", "--output", str(self.root / "cli-run")])
            self.assertEqual(desktop_cli.run_desktop_command(args), 0)
            artifact = json.loads(out.getvalue())["artifact"]
            out.truncate(0)
            out.seek(0)
            args = parser.parse_args(["desktop", "evidence", "run", "policy", artifact,
                "--hard-seconds", "1200", "--note", "Owner authorized extension"])
            self.assertEqual(desktop_cli.run_desktop_command(args), 0)
            self.assertEqual(json.loads(out.getvalue())["events"], 2)
            status.assert_not_called()
        before = Path(artifact).read_bytes()
        for changes in ({"event": "resume"}, {"event": "policy", "hard_seconds": 50},
                        {"event": "checkpoint", "hard_seconds": 900}, {"event": "context"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                run_checkpoint.update(artifact, **changes)
        self.assertEqual(Path(artifact).read_bytes(), before)

    def test_run_capacity_corruption_and_duplicate_wait_transitions_preserve_history(self):
        with patch.object(run_checkpoint, "EVENT_LIMIT", 3):
            path = run_checkpoint.start("a", "m", "play", output=str(self.root / "bounded"))["artifact"]
            run_checkpoint.update(path, "suspend", note="Awaiting owner")
            before = Path(path).read_bytes()
            with self.assertRaisesRegex(ValueError, "contradicts"):
                run_checkpoint.update(path, "suspend", note="Duplicate wait")
            with patch.object(run_checkpoint, "MAX_BYTES", len(before) + 20):
                with self.assertRaisesRegex(ValueError, "byte capacity"):
                    run_checkpoint.update(path, "context", context="next")
            self.assertEqual(Path(path).read_bytes(), before)
            run_checkpoint.update(path, "resume")
            before = Path(path).read_bytes()
            with self.assertRaisesRegex(ValueError, "capacity"):
                run_checkpoint.update(path, "checkpoint")
            self.assertEqual(Path(path).read_bytes(), before)
        Path(path).write_text('{"schema_version":999}')
        before = Path(path).read_bytes()
        with self.assertRaisesRegex(ValueError, "unsupported"):
            run_checkpoint.update(path, "checkpoint")
        self.assertEqual(Path(path).read_bytes(), before)

    def test_run_rejects_impossible_saved_history_without_rewriting_it(self):
        path = Path(run_checkpoint.start("a", "m", "play", output=str(self.root / "history"))["artifact"])
        original = json.loads(path.read_text())
        first = original["events"][0]
        def event(kind, **details):
            return {**first, "event": kind, "monotonic_ns": first["monotonic_ns"] + 1,
                    "review_seconds": None, "hard_seconds": None, **details}
        invalid_histories = [
            [event("resume")], [event("start")], [event("suspend")],
            [event("suspend", note="Await owner"), event("suspend", note="Duplicate wait")],
            [event("finish"), event("checkpoint")], [event("context")],
            [event("policy", hard_seconds=100)], [event("checkpoint", hard_seconds=100)],
            [event("checkpoint", at="2026-01-01T00:00:00")],
            [event("checkpoint", at="2026-01-01T00:00:00+02:00")],
            [event("checkpoint", monotonic_ns=-1)], [event("checkpoint", artifacts="/private/evidence")],
            [event("checkpoint", artifacts=["relative/path"])], [event("checkpoint", note=42)],
        ]
        for rows in invalid_histories:
            path.write_text(json.dumps({**original, "events": [first, *rows]}))
            before = path.read_bytes()
            for operation in ("show", "checkpoint"):
                with self.subTest(rows=rows, operation=operation), self.assertRaises(ValueError):
                    run_checkpoint.update(str(path), operation)
                self.assertEqual(path.read_bytes(), before)

    def test_closeout_retains_failure_then_links_new_receipt_after_path_restoration(self):
        with patch.object(Path, "home", return_value=self.root), patch.object(evidence, "revision", return_value={}):
            recorder = evidence.Recorder(str(self.root), "g")
            png = self.root / "capture.png"
            png.write_bytes(b"synthetic PNG bytes")
            png.chmod(0o600)
            digest = qa_artifacts.file_digest(png)
            recorder.event({"action": "screenshot"}, {"output": str(png), "artifact_sha256": digest})
            moved = self.root / "moved.png"
            png.rename(moved)
            before = recorder.path.read_bytes()
            first = evidence.closeout(str(recorder.path), extras=[str(moved)], derivatives=[], output=str(self.root / "closeout-1"))
            self.assertEqual(first["verification"]["counts"]["missing"], 1)
            png.write_bytes(moved.read_bytes())
            png.chmod(0o600)
            second = evidence.closeout(str(recorder.path), extras=[str(moved)], derivatives=[],
                previous=first["artifact"], output=str(self.root / "closeout-2"))
            self.assertTrue(second["verification"]["healthy"])
            self.assertEqual(second["previous_receipt"]["artifact"], first["artifact"])
            self.assertEqual(second["counts"]["recorded_png_paths"], 1)
            self.assertEqual(second["counts"]["extra_png_paths"], 1)
            self.assertEqual(json.loads(Path(first["artifact"]).read_text())["verification"]["counts"]["missing"], 1)
            self.assertEqual(recorder.path.read_bytes(), before)
            identities = [{"pid": 12345, "start_ticks": "987654"}, {"pid": 12345, "start_ticks": "987655"},
                          FileNotFoundError(), ValueError("unavailable")]
            with patch.object(qa_artifacts, "process_identity", side_effect=identities):
                for index, expected in enumerate(("running-preserve", "identity-replaced", "identity-not-present", "unavailable")):
                    result = evidence.closeout(str(recorder.path), extras=[], derivatives=[],
                        processes=[["12345", "987654"]], output=str(self.root / f"process-{index}"))
                    self.assertEqual(result["resources"][0]["state"], expected)
                    self.assertEqual(result["resources"][0]["normal_exit"], "unknown")

    def test_preflight_preserves_unknown_stopped_worker_and_changed_skills(self):
        clean = {"commit": "a" * 40, "dirty": False}
        manifest = {"installation_context": {"invoked": clean, "managed": clean,
                    "drift": {"invoked_managed": False, "invoked_native_worker": False}},
                    "provenance": {"delivered_skills": [{"drift": True}, {"drift": None}]}}
        backend = Mock()
        backend.status.return_value = {"state": "stopped", "helper_running": False,
            "worker_provenance": {"schema_version": 1, "source": clean}, "agent_control": "human-override"}
        with patch("lib.agent_environment.inspect_environment", return_value=manifest), \
                patch("lib.agent_environment.scoped_environment", return_value=manifest):
            result = preflight.preflight(str(self.root), backend=backend, native=True, output=str(self.root / "preflight"),
                                         instructions=[str(self.root / "missing")])
        self.assertEqual(result["checks"]["invoked_managed"]["state"], "observed")
        self.assertEqual(result["checks"]["invoked_native_worker"]["state"], "unavailable")
        self.assertEqual(result["checks"]["delivered_skills"], {"state": "mismatched", "count": 2, "unknown_count": 1})
        self.assertEqual(result["checks"]["human_control"]["value"], "human-override")
        self.assertEqual(result["instructions"][0]["state"], "unavailable")
        backend.request.assert_not_called()
        self.assertTrue(Path(result["artifact"]).exists())

    def test_snapshot_pins_regular_bytes_rejects_links_and_preserves_original(self):
        source = self.root / "原 image.png"
        source.write_bytes(b"original")
        saved = self.root / "copy.png"
        row = qa_artifacts.snapshot(str(source), saved)
        self.assertEqual(saved.read_bytes(), b"original")
        self.assertEqual(source.read_bytes(), b"original")
        self.assertEqual(row["sha256"], qa_artifacts.file_digest(saved))
        self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
        link = self.root / "link"
        link.symlink_to(source)
        with self.assertRaises(OSError):
            qa_artifacts.snapshot(str(link), self.root / "bad")
        with self.assertRaises(FileExistsError):
            qa_artifacts.snapshot(str(source), saved)

    def test_new_bundle_refuses_git_existing_and_public_parent(self):
        (self.root / ".git").mkdir()
        with self.assertRaisesRegex(ValueError, "Git"):
            qa_artifacts.new_bundle(str(self.root / "output"))
        (self.root / ".git").rmdir()
        bundle = qa_artifacts.new_bundle(str(self.root / "output"))
        self.assertEqual(bundle.stat().st_mode & 0o777, 0o700)
        with self.assertRaises(FileExistsError):
            qa_artifacts.new_bundle(str(bundle))
        self.root.chmod(0o755)
        with self.assertRaisesRegex(ValueError, "private"):
            qa_artifacts.new_bundle(str(self.root / "public"))

    def test_crop_bounds_caption_hash_and_portable_review(self):
        source = self.root / "image.png"
        source.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + struct.pack(">II", 8, 6) + b"\x08\x06\x00\x00\x00" + b"crc!")
        def render(command, **kwargs):
            Path(command[-1]).write_bytes(b"literal-crop")
            self.assertIn("crop=3:2:1:2:exact=1", command)
        with patch.object(evidence_media, "media_command", side_effect=render):
            result = evidence_media.crop(str(source), [1, 2, 3, 2], "A [label] <script>", str(self.root / "crop"))
        manifest = json.loads(Path(result["artifact"]).read_text())
        self.assertEqual(manifest["rectangle"], [1, 2, 3, 2])
        self.assertEqual(manifest["source"]["sha256"], qa_artifacts.file_digest(source))
        report = Path(result["report"]).read_text()
        self.assertIn("&#91;label&#93;", report)
        self.assertNotIn("<script>", report)
        exported = evidence_media.export_reviewed(result["artifact"], str(self.root / "reviewed"), "QA reviewer")
        self.assertEqual(exported["review"], "operator-reviewed")
        self.assertEqual(exported["included_originals"], ["source.png"])
        self.assertNotIn(str(self.root), Path(exported["artifact"]).read_text())
        Path(result["artifact"]).parent.joinpath("crop.png").write_bytes(b"tampered")
        with patch.object(Path, "home", return_value=self.root), patch.object(evidence, "revision", return_value={}):
            recorder = evidence.Recorder(str(self.root), "g")
            inventory = evidence.closeout(str(recorder.path), extras=[], derivatives=[result["artifact"]],
                                         output=str(self.root / "derived-closeout"))
        receipt = json.loads(Path(inventory["artifact"]).read_text())
        self.assertEqual(inventory["counts"]["source_copies"], 1)
        self.assertEqual(inventory["counts"]["derived_media"], 1)
        self.assertEqual([row["state"] for row in receipt["derivatives"][0]["media"]], ["verified", "mismatched"])
        with self.assertRaisesRegex(ValueError, "matches"):
            evidence_media.export_reviewed(result["artifact"], str(self.root / "changed"), "QA reviewer")
        with patch.object(evidence_media, "media_command") as command:
            with self.assertRaisesRegex(ValueError, "outside"):
                evidence_media.crop(str(source), [7, 5, 2, 2], "Out of bounds", str(self.root / "bad"))
            command.assert_not_called()

    def test_audio_summary_channels_silence_nonfinite_and_levels(self):
        text = """[Parsed_astats_0 @ x] Channel: 1
[Parsed_astats_0 @ x] Peak level dB: -0.0001
[Parsed_astats_0 @ x] RMS level dB: -3.5
[Parsed_astats_0 @ x] Channel: 2
[Parsed_astats_0 @ x] Peak level dB: -inf
[Parsed_astats_0 @ x] Overall
[Parsed_astats_0 @ x] Peak level dB: -0.0001
[silencedetect @ x] silence_start: 0.2
[silencedetect @ x] silence_end: 0.8 | silence_duration: 0.6
[silencedetect @ x] silence_start: 1.5
    I: -15.2 LUFS
    LRA: 2.1 LU
    Peak: 0.3 dBFS
"""
        data = audio_analysis.parse_analysis(text, 2)
        self.assertEqual(data["channels"]["1"]["rms_dbfs"], -3.5)
        self.assertIsNone(data["channels"]["2"]["sample_peak_dbfs"])
        self.assertEqual(data["integrated_lufs"], -15.2)
        self.assertEqual(data["estimated_true_peak_dbfs"], .3)
        self.assertEqual(data["silence_intervals"], [{"start_seconds": .2, "end_seconds": .8}, {"start_seconds": 1.5, "end_seconds": 2}])
        levels = audio_analysis.parse_levels("[x] frame:0 pts:0 pts_time:0\n[x] lavfi.astats.1.Peak_level=-4.5\n[x] lavfi.astats.Overall.RMS_level=-inf\n")
        self.assertEqual(levels[0]["levels"]["1.Peak_level"], -4.5)
        self.assertIsNone(levels[0]["levels"]["Overall.RMS_level"])
        json.dumps(data, allow_nan=False)

    def test_excerpts_refuse_nonfinite_and_outside_source(self):
        for start, seconds in ((float("nan"), 1), (0, float("inf")), (-1, 1), (0, 11)):
            with self.subTest(start=start, seconds=seconds), self.assertRaises(ValueError):
                evidence_media.excerpt("unused", start, seconds)

    def test_timeline_orders_same_boot_without_estimating_audio_origin(self):
        sources = []
        clock = {"boot_id": "11111111-2222-3333-4444-555555555555", "monotonic_scope": "linux-boot"}
        data = [{"schema_version": 1, "clock": clock, "events": [{"at": "2026-01-01T00:00:02+00:00", "monotonic_ns": 3_000_000_000, "action": "input"}], "events_truncated": True},
                {"schema_version": 1, "kind": "application-audio", "clock": clock, "started": {"at": "2026-01-01T00:00:00+00:00", "monotonic_ns": 1_000_000_000}, "qualified": False},
                {"schema_version": 1, "kind": "accessibility-recording", "clock": clock, "events": [],
                 "status": "failed", "errors": ["worker timed out"], "truncated": True, "truncation_reasons": ["output-bytes"]}]
        for index, row in enumerate(data):
            path = self.root / f"{index}.json"
            path.write_text(json.dumps(row))
            sources.append(str(path))
        result = qa_timeline.timeline(sources, str(self.root / "timeline"))
        report = json.loads(Path(result["artifact"]).read_text())
        self.assertEqual([row["timeline_seconds"] for row in report["events"]], [0, 2])
        self.assertTrue(report["sources"][0]["truncated"])
        self.assertIsNone(report["sources"][1]["audio_origin"])
        self.assertEqual(report["sources"][2]["status"], "failed")
        self.assertEqual(report["sources"][2]["errors"], ["worker timed out"])
        self.assertEqual(report["sources"][2]["truncation_reasons"], ["output-bytes"])
        data[1]["clock"] = {"boot_id": "99999999-2222-3333-4444-555555555555", "monotonic_scope": "linux-boot"}
        Path(sources[1]).write_text(json.dumps(data[1]))
        with self.assertRaisesRegex(ValueError, "different Linux boots"):
            qa_timeline.timeline(sources, str(self.root / "other"))


def node(pid=42, serial=100, identifier=10):
    return {"id": identifier, "type": "PipeWire:Interface:Node", "info": {"props": {
        "application.process.id": str(pid), "client.id": pid, "object.serial": serial, "media.class": "Stream/Output/Audio"}}}


def client(pid=42):
    return {"id": pid, "type": "PipeWire:Interface:Client", "info": {"props": {
        "pipewire.sec.pid": pid, "pipewire.sec.uid": os.getuid()}}}


def port(identifier, node_id, direction, channel):
    return {"id": identifier, "type": "PipeWire:Interface:Port", "info": {"props": {
        "node.id": node_id, "port.direction": direction, "audio.channel": channel}}}


def link(output, target):
    return {"type": "PipeWire:Interface:Link", "info": {"output-port-id": output, "input-port-id": target}}


class AudioSelectionTests(unittest.TestCase):
    def test_independent_recorder_deadline_survives_exec_and_disables_autoconnect(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(audio_capture_worker.signal, "signal"), patch.object(audio_capture_worker.signal, "alarm") as alarm, \
             patch.object(audio_capture_worker.os, "execve") as execute:
            audio_capture_worker.execute(3, "basaltwater-audio-" + "a" * 24, directory + "/audio.wav")
            alarm.assert_called_once_with(18)
            args = execute.call_args.args[1]
            self.assertEqual(args[args.index("--target") + 1], "0")
            self.assertEqual(args[args.index("--sample-count") + 1], "144000")
            self.assertIs(json.loads(args[args.index("--properties") + 1])["node.autoconnect"], False)

    def capture_fixture(self, directory, *, extra_link=False, completion=1, changed_control=False, teardown_gap=False):
        stack = ExitStack()
        self.addCleanup(stack.close)
        source = [client(), node(), port(1, 10, "out", "FL"), port(2, 10, "out", "FR"), link(1, 20), link(2, 21)]
        owned = node(pid=777, serial=101, identifier=11)
        owned["info"]["props"]["node.name"] = "basaltwater-audio-" + "a" * 24
        receiver = [owned, client(777), port(3, 11, "in", "FL"), port(4, 11, "in", "FR")]
        recording = [*source, *receiver, link(1, 3), link(2, 4)]
        if extra_link:
            recording.append(link(99, 3))
        stack.enter_context(patch.object(audio_capture, "graph", side_effect=[source, [*source, *receiver], recording, source, source]))
        stack.enter_context(patch.object(audio_capture.native_session, "check_session"))
        stack.enter_context(patch.object(audio_capture.control_state, "status", return_value={"control_revision": "a"}))
        guard = stack.enter_context(patch.object(audio_capture.control_state, "require_enabled"))
        if changed_control:
            guard.side_effect = [None, None, None, None, RuntimeError("human override")]
        stack.enter_context(patch.object(audio_capture, "process_identity", return_value={"pid": 42, "start_ticks": "100"}))
        stack.enter_context(patch.object(audio_capture.secrets, "token_hex", return_value="a" * 24))
        stack.enter_context(patch.object(audio_capture.time, "sleep"))
        command = stack.enter_context(patch.object(audio_capture, "run_captured", return_value=Mock(returncode=0)))
        stack.enter_context(patch.object(audio_analysis, "audio_metadata", return_value={"duration_seconds": 2, "sample_rate": 48000, "channels": 2, "codec": "pcm_f32le"}))
        process = Mock(pid=777)
        process.returncode = completion
        process.poll.side_effect = [None, None, None if teardown_gap else completion, completion]
        process.wait.return_value = completion
        process.stderr = tempfile.TemporaryFile()
        self.addCleanup(process.stderr.close)
        def launch(*args, **kwargs):
            (Path(directory) / "capture/audio.wav").write_bytes(b"bounded audio")
            return process
        stack.enter_context(patch.object(audio_capture.subprocess, "Popen", side_effect=launch))
        terminate = stack.enter_context(patch.object(audio_capture.os, "killpg"))
        return command, process, terminate

    def test_bounded_capture_retains_raw_exit_verifies_format_and_owned_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            command, process, terminate = self.capture_fixture(directory)
            result = audio_capture.capture(42, 100, 2, directory + "/capture")
            self.assertTrue(result["qualified"])
            self.assertTrue(result["cleanup_confirmed"])
            data = json.loads(Path(result["artifact"]).read_text())
            self.assertEqual(data["recorder_returncode"], 1)
            self.assertEqual(data["exit_interpretation"], "sample-limit-and-format-verified")
            self.assertEqual(data["graph_checks"], 1)
            self.assertIsNone(data["audio_origin"])
            terminate.assert_not_called()
            self.assertEqual(command.call_count, 2)
            for call in command.call_args_list:
                self.assertNotIn("-d", call.args[0])

    def test_foreign_links_or_control_change_stop_owned_recorder_and_mark_unqualified(self):
        for extra, takeover in ((True, False), (False, True)):
            with self.subTest(extra=extra, takeover=takeover), tempfile.TemporaryDirectory() as directory:
                command, process, terminate = self.capture_fixture(directory, extra_link=extra, changed_control=takeover)
                # The process is still running at rejection, then terminates on TERM.
                process.poll.side_effect = [None, None, None]
                result = audio_capture.capture(42, 100, 2, directory + "/capture")
                self.assertFalse(result["qualified"])
                self.assertIn("error", result)
                terminate.assert_called_once_with(777, audio_capture.signal.SIGTERM)

    def test_node_removal_before_process_exit_reconciles_the_owned_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            command, process, terminate = self.capture_fixture(directory, teardown_gap=True)
            result = audio_capture.capture(42, 100, 2, directory + "/capture")
            self.assertTrue(result["qualified"])
            process.wait.assert_called_once_with(timeout=.25)
            terminate.assert_not_called()

    def test_node_selection_fails_closed_on_ambiguity_and_reuse(self):
        self.assertEqual(audio_capture.select_node([client(), node()], 42, 100)["id"], 10)
        for items, pid, serial in (([node(), node(serial=101)], 42, None), ([node(pid=43)], 42, 100), ([node(serial=101)], 42, 100)):
            with self.subTest(pid=pid, serial=serial), self.assertRaises(ValueError):
                audio_capture.select_node([client(), *items], pid, serial)
        spoofed = node()
        spoofed["info"]["props"]["client.id"] = 43
        with self.assertRaises(ValueError):
            audio_capture.select_node([client(43), spoofed], 42, 100)

    def test_channel_mapping_and_exclusive_links_preserve_playback(self):
        items = [port(1, 10, "out", "FL"), port(2, 10, "out", "FR")]
        self.assertEqual(audio_capture.ports(items, 10, "out"), {"FL": 1, "FR": 2})
        with self.assertRaises(ValueError):
            audio_capture.ports([*items, port(3, 10, "out", "FL")], 10, "out")
        source, target = {"FL": 1, "FR": 2}, {"FL": 3, "FR": 4}
        good = [link(1, 3), link(2, 4), link(1, 20)]
        audio_capture.verify_links(good, source, target, {(1, 20)})
        for bad in (good + [link(99, 3)], good[:2], [link(1, 3), link(1, 20)]):
            with self.subTest(bad=bad), self.assertRaises(RuntimeError):
                audio_capture.verify_links(bad, source, target, {(1, 20)})

    def test_capture_checks_control_before_any_graph_or_launch(self):
        with patch.object(audio_capture.native_session, "check_session"), \
             patch.object(audio_capture.control_state, "status", return_value={"control_revision": "a"}), \
             patch.object(audio_capture.control_state, "require_enabled", side_effect=RuntimeError("disabled")), \
             patch.object(audio_capture, "graph") as graph, patch.object(audio_capture.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(RuntimeError, "disabled"):
                audio_capture.capture(42, 100, 2)
            graph.assert_not_called()
            launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
