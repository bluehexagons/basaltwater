"""Agent CPU qualification gates under optimized Python; no real media jobs."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "qualify_media.py"


class CPUQualificationTests(unittest.TestCase):
    def run_qualification(self, optimization, fault=None):
        namespace = {"__name__": "qualification_test", "__file__": str(SOURCE)}
        with patch.object(sys, "path", list(sys.path)):
            exec(compile(SOURCE.read_text(), str(SOURCE), "exec", optimize=optimization), namespace)

        class Wav:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def getnframes(self):
                return 96000

            def readframes(self, count):
                return (1000 if fault == "fade" else 0).to_bytes(2, "little", signed=True) * count

        class Process:
            pid = 99999999
            done = False

            def __init__(self, args, **kwargs):
                output = Path(args[args.index("--output") + 1])
                output.mkdir()
                status = "complete" if fault == "cancel_status" else "cancelled"
                (output / "manifest.json").write_text(json.dumps({"status": status}))

            def poll(self):
                return 0 if self.done or fault == "no_decoder" else None

            def communicate(self, **kwargs):
                self.done = True
                return b"", b""

            def send_signal(self, signum):
                pass

            def kill(self):
                self.done = True

        def execute(args, **kwargs):
            if args[0] == "ffprobe":
                return subprocess.CompletedProcess(args, 0, json.dumps({"packets": [
                    {"pts_time": str(t)} for t in (0, .04, .16)]}).encode(), b"")
            if args[-1] == "pipe:1":
                size = 1 if fault == "frame_size" else 320 * 180 * 3
                return subprocess.CompletedProcess(args, 0, b"\0" * size, b"")
            Path(args[-1]).write_bytes(b"synthetic source")
            return subprocess.CompletedProcess(args, 0, b"", b"")

        def analyze(source, output, **kwargs):
            name = Path(source).name
            if kwargs.get("timeout") == .001:
                return {"status": "complete" if fault == "deadline" else "incomplete", "elapsed_seconds": 0}
            out = Path(output)
            out.mkdir()
            (out / "frame-0.png").write_bytes(b"\0" * 16 + (10).to_bytes(4, "big") + (20).to_bytes(4, "big"))
            return {
                "status": "unsupported" if name in ("malformed.mp4", "playlist.m3u8", "truncated.mp4") else "complete",
                "errors": [], "elapsed_seconds": 0,
                "audio": {"status": "missing" if name == "no-audio.mp4" else "complete",
                          "measurements": {"overall": {"rms_dbfs": None if name == "silent.wav" else -20},
                                           "silence_intervals": [{"start_seconds": 0, "end_seconds": 2}]}},
                "metadata": {"streams": [{"side_data_list": [{"rotation": 90}]}, {"start_time": ".5"}]},
                "video": {"packet_pts_range_seconds": [1 if name == "delayed-video.mp4" else 0, 2]},
                "frames": [{"requested_seconds": 1, "decoded_pts_seconds": 1}],
                "timing": {"duration_seconds": 99 if fault == "duration" else (4 if name == "delivery.mp4" else 2),
                           "duration_basis": "packet span" if name == "unknown.webm" else "container"},
            }

        original_text, original_bytes, original_exists = Path.read_text, Path.read_bytes, Path.exists

        def read_text(path, *args, **kwargs):
            if str(path) == "/proc/99999999/task/99999999/children":
                return "99999998"
            return original_text(path, *args, **kwargs)

        def read_bytes(path, *args, **kwargs):
            if str(path) == "/proc/99999998/cmdline":
                return b"ffmpeg\0-i\0input\0null\0"
            return original_bytes(path, *args, **kwargs)

        def exists(path):
            if str(path) == "/proc/99999998":
                return fault == "decoder_survived"
            return original_exists(path)

        with tempfile.TemporaryDirectory() as parent:
            output = Path(parent) / "new"
            with patch.object(namespace["subprocess"], "run", side_effect=execute), \
                    patch.object(namespace["subprocess"], "Popen", side_effect=Process), \
                    patch.object(namespace["wave"], "open", return_value=Wav()), \
                    patch.dict(namespace, inspect=analyze), \
                    patch.object(Path, "read_text", read_text), \
                    patch.object(Path, "read_bytes", read_bytes), patch.object(Path, "exists", exists):
                try:
                    result = namespace["qualify"](str(output))
                except RuntimeError:
                    self.assertFalse((output / "qualification.json").exists())
                    raise
            self.assertEqual(json.loads((output / "qualification.json").read_text()), result)
            return result

    def test_valid_evidence_qualifies_in_all_optimization_modes(self):
        for optimization in (0, 1, 2):
            with self.subTest(optimization=optimization):
                result = self.run_qualification(optimization)
                self.assertEqual(len(result["cases"]), 18)
                self.assertEqual(result["deadline_status"], "incomplete")
                self.assertTrue(result["cancellation_cleanup_confirmed"])

    def test_invalid_evidence_never_produces_a_qualification_report(self):
        for optimization in (0, 1, 2):
            for fault, message in (("fade", "fade-in"), ("frame_size", "frame size"),
                                   ("duration", "duration mismatch"), ("deadline", "Deadline probe"),
                                   ("no_decoder", "active decoder"), ("cancel_status", "cancelled manifest"),
                                   ("decoder_survived", "Decoder survived")):
                with self.subTest(optimization=optimization, fault=fault), self.assertRaisesRegex(RuntimeError, message):
                    self.run_qualification(optimization, fault)
