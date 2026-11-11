"""Offline agent media contract tests; no real decoder or system mutations."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from lib import media_cli


class MediaTests(unittest.TestCase):
    def test_snapshot_and_container_guards(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "音 sample.wav"
            source.write_bytes(b"RIFF1234WAVEdata")
            self.assertEqual(media_cli.demuxer(media_cli.snapshot(str(source), 32)), "wav")
            with self.assertRaises(media_cli.UnsupportedMedia):
                media_cli.snapshot(str(source), 4)
            link = Path(root) / "link"
            link.symlink_to(source)
            with self.assertRaises(OSError):
                media_cli.snapshot(str(link), 32)
            with self.assertRaises(media_cli.UnsupportedMedia):
                media_cli.demuxer(b"#EXTM3U\nfile:///private")

    def run_analysis(self, metadata, *, log="", fail=None, **kwargs):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "音 sample.wav"
            source.write_bytes(b"RIFF1234WAVEdata")
            output = Path(root) / "result"

            def execute(argv, **options):
                self.assertLessEqual(options["timeout"], 180)
                if "-version" in argv:
                    return subprocess.CompletedProcess(argv, 0, "synthetic version\n", "")
                self.assertEqual(argv[0], "/usr/bin/prlimit")
                self.assertIn("--as=1610612736", argv)
                self.assertEqual(argv[argv.index("-protocol_whitelist") + 1], "pipe")
                self.assertEqual(options["input_data"], source.read_bytes())
                if fail:
                    raise fail
                if "/usr/bin/ffprobe" in argv:
                    return subprocess.CompletedProcess(argv, 0, json.dumps(metadata), "")
                if str(argv[-1]).endswith(".png"):
                    Path(argv[-1]).write_bytes(b"synthetic png")
                    return subprocess.CompletedProcess(argv, 0, "", " n: 0 pts:0 pts_time:0.5")
                return subprocess.CompletedProcess(argv, 0, "", log)

            with patch.object(media_cli.shutil, "which", side_effect=lambda n: "/usr/bin/" + n), \
                    patch.object(media_cli, "run_captured", side_effect=execute):
                result = media_cli.inspect(str(source), str(output), **kwargs)
            receipt = json.loads((output / "manifest.json").read_text())
            self.assertEqual(receipt, result)
            self.assertEqual(output.stat().st_mode & 0o777, 0o700)
            self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in output.iterdir()))
            with self.assertRaises(FileExistsError):
                media_cli.inspect(str(source), str(output))
            return result

    def test_silent_audio_and_missing_video(self):
        metadata = {"streams": [{"index": 0, "codec_type": "audio", "channels": 1, "sample_rate": "48000"}],
                    "packets": [{"pts_time": "0", "duration_time": "2"}]}
        log = ("[Parsed_astats_0] Channel: 1\n[Parsed_astats_0] Peak level dB: -inf\n"
               "[Parsed_astats_0] RMS level dB: -inf\n"
               "[Parsed_astats_0] Overall\n[Parsed_astats_0] Peak level dB: -inf\n"
               "[Parsed_astats_0] RMS level dB: -inf\nsilence_start: 0\nsilence_end: 2\n I: -70.0 LUFS\n")
        result = self.run_analysis(metadata, log=log)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["video"]["status"], "missing")
        self.assertIsNone(result["audio"]["measurements"]["overall"]["rms_dbfs"])
        self.assertEqual(result["audio"]["measurements"]["silence_intervals"],
                         [{"start_seconds": 0, "end_seconds": 2}])

    def test_missing_channel_measurements_are_incomplete(self):
        overall = ("[Parsed_astats_0] Overall\n[Parsed_astats_0] Peak level dB: -1\n"
                   "[Parsed_astats_0] RMS level dB: -6\n I: -20.0 LUFS\n")
        channel = "[Parsed_astats_0] Channel: 1\n[Parsed_astats_0] Peak level dB: -1\n"
        for channels, log in ((1, overall), (1, channel + overall),
                              (2, channel + "[Parsed_astats_0] RMS level dB: -6\n" + overall)):
            with self.subTest(channels=channels, log=log):
                result = self.run_analysis({"format": {"duration": "2"}, "streams": [
                    {"index": 0, "codec_type": "audio", "channels": channels, "sample_rate": "48000"}]}, log=log)
                self.assertEqual(result["status"], "incomplete")
                self.assertIn("measurement summary incomplete", result["errors"][0])

    def test_decoded_timestamps_and_missing_audio(self):
        result = self.run_analysis({"format": {"duration": "2"}, "streams": [
            {"index": 0, "codec_type": "video", "width": 640, "height": 360}],
            "packets": [{"stream_index": 0, "pts_time": "0"}, {"stream_index": 0, "pts_time": "1.9"}]})
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["frames"][0]["decoded_pts_seconds"], .5)
        self.assertEqual(result["frames"][0]["requested_seconds"], 0)
        self.assertEqual(result["audio"]["status"], "missing")

    def test_sampling_uses_selected_video_span_and_normalizes_offsets(self):
        for first, last in ((10, 11), (12, 12), (12, 13)):
            with self.subTest(first=first, last=last):
                result = self.run_analysis({"format": {"duration": "20", "start_time": "10"}, "streams": [
                    {"index": 0, "codec_type": "video", "width": 640, "height": 360},
                    {"index": 1, "codec_type": "video", "width": 640, "height": 360}], "packets": [
                        {"stream_index": 0, "pts_time": "10"}, {"stream_index": 0, "pts_time": "29"},
                        {"stream_index": 1, "pts_time": str(first)}, {"stream_index": 1, "pts_time": str(last)},
                    ]}, video=1)
                self.assertEqual(result["status"], "complete")
                self.assertEqual(result["video"]["packet_pts_range_seconds"], [first - 10, last - 10])
                targets = [frame["requested_seconds"] for frame in result["frames"]]
                self.assertTrue(all(first - 10 - .000002 <= target <= last - 10 for target in targets))
                self.assertEqual(len(targets), 3)

    def test_incomplete_and_unsupported(self):
        for metadata, kwargs, status in (
            ({"streams": []}, {}, "unsupported"),
            ({"format": {"duration": "121"}}, {}, "unsupported"),
            ({"format": {"duration": "2"}, "streams": []}, {"audio": 1}, "unsupported"),
            ({"format": {"duration": "2"}, "streams": [
                {"index": 0, "codec_type": "video", "width": 640, "height": 360}]}, {}, "unsupported"),
            ({}, {"fail": subprocess.TimeoutExpired("ffprobe", 1)}, "incomplete"),
            ({"format": {"duration": "2"}, "streams": [
                {"index": 0, "codec_type": "audio", "channels": 1, "sample_rate": "48000"}]}, {}, "incomplete"),
        ):
            with self.subTest(status=status, metadata=metadata):
                result = self.run_analysis(metadata, **kwargs)
                self.assertEqual(result["status"], status)
                self.assertTrue(result["errors"])

    def test_missing_tools(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "source.wav"
            source.write_bytes(b"RIFF1234WAVEdata")
            with patch.object(media_cli.shutil, "which", return_value=None):
                result = media_cli.inspect(str(source), str(Path(root) / "result"))
            self.assertEqual(result["status"], "incomplete")
            self.assertIn("Missing tools", result["errors"][0])

    def test_changed_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "input.wav"
            source.write_bytes(b"RIFF1234WAVEdata")
            before = source.stat()
            after = SimpleNamespace(st_dev=before.st_dev, st_ino=before.st_ino,
                                    st_size=before.st_size, st_mtime_ns=before.st_mtime_ns + 1,
                                    st_ctime_ns=before.st_ctime_ns)
            with patch.object(media_cli.os, "fstat", side_effect=[before, after]):
                with self.assertRaisesRegex(ValueError, "changed"):
                    media_cli.snapshot(str(source), 1024)

    def test_disk_and_concurrency_refusal(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(media_cli.shutil, "disk_usage", return_value=SimpleNamespace(free=0)):
                result = media_cli.inspect("unused", str(Path(root) / "disk"))
                self.assertIn("free output space", result["errors"][0])
            with media_cli._parent_lock(Path(root)):
                result = media_cli.inspect("unused", str(Path(root) / "busy"))
                self.assertEqual(result["status"], "incomplete")

    def test_interruption_receipt(self):
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "cancelled"
            with patch.object(media_cli, "snapshot", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    media_cli.inspect("unused", str(output))
            self.assertEqual(json.loads((output / "manifest.json").read_text())["status"], "cancelled")

    def test_nonfinite_limits(self):
        for options in ({"timeout": float("nan")}, {"max_seconds": float("inf")}, {"video": -1}):
            with self.assertRaises(ValueError):
                media_cli.inspect("unused", "unused", **options)
