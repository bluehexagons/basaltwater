"""Offline contracts for private QA media, selected audio links and clocks."""

from __future__ import annotations

import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
from unittest.mock import Mock
from contextlib import ExitStack

from desktop import audio_analysis, audio_capture, audio_capture_worker, evidence_media, qa_artifacts, qa_timeline


class QAMediaTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.root.chmod(0o700)

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
        self.assertNotIn(str(self.root), Path(exported["artifact"]).read_text())
        Path(result["artifact"]).parent.joinpath("crop.png").write_bytes(b"tampered")
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
