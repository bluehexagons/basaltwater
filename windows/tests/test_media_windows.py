"""Offline Windows tests; temporary files, mocked media and owned Python children."""
from __future__ import annotations

import _thread
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lib import media_cli as cli
from lib import media_windows as native


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="media 音 ")
        self.root = Path(self.temporary.name)
        self.source = self.root / "音 sample.wav"
        self.source.write_bytes(b"RIFF" + b"\0" * 4 + b"WAVE" + b"\0" * 32)

    def tearDown(self):
        self.temporary.cleanup()

    def test_snapshot_and_ads(self):
        self.assertEqual(native.snapshot(str(self.source), 128), self.source.read_bytes())
        with native.pinned(self.source, file=True):
            with self.assertRaises(OSError):
                self.source.write_bytes(b"changed")
        with self.assertRaises(ValueError):
            native.snapshot(str(self.source), 4)
        Path(str(self.source) + ":private").write_bytes(b"stream")
        with self.assertRaisesRegex(ValueError, "Alternate"):
            native.snapshot(str(self.source), 128)

    def test_reparse_and_paths(self):
        for name in (r"\\server\share\x", r"C:\x:ads", r"C:\NUL.wav", r"C:\a\..\b"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                native.validate_path(name)
        linked = self.root / "junction"
        # Directory junction creation needs no administrator or developer mode.
        result = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(linked), str(self.root)],
                                capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        try:
            with self.assertRaisesRegex(ValueError, "Reparse"):
                native.validate_path(str(linked / "x"))
        finally:
            linked.rmdir()

    def test_private_acl_lock_and_overwrite(self):
        folder = self.root / "bundle"
        native.private_directory(folder)
        # Query the protected bit directly instead of treating chmod as an ACL.
        descriptor = ctypes.c_void_p()
        code = native.api(native.a, "GetNamedSecurityInfoW", native.w.DWORD,
                          native.w.LPCWSTR, ctypes.c_int, native.w.DWORD,
                          ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(
                              str(folder), 1, 4, None, None, None, None, ctypes.byref(descriptor))
        self.assertEqual(code, 0)
        try:
            control, revision = native.w.WORD(), native.w.DWORD()
            native.checked(native.api(native.a, "GetSecurityDescriptorControl", native.w.BOOL,
                                      ctypes.c_void_p, ctypes.POINTER(native.w.WORD), ctypes.POINTER(native.w.DWORD))(
                                          descriptor, ctypes.byref(control), ctypes.byref(revision)))
            self.assertTrue(control.value & 0x1000)
        finally:
            native.api(native.k, "LocalFree", ctypes.c_void_p, ctypes.c_void_p)(descriptor)
        with self.assertRaises(OSError):
            native.private_directory(folder)
        with native.parent_lock(self.root):
            with self.assertRaises(OSError):
                with native.parent_lock(self.root):
                    self.fail("Concurrent lock admitted")
        with native.parent_lock(self.root):
            pass

    def test_missing_and_refusal_receipts(self):
        with patch.object(cli, "discover", return_value={"ffmpeg": None, "ffprobe": None}):
            result = cli.inspect(str(self.source), str(self.root / "missing"))
        self.assertEqual(result["status"], "incomplete")
        self.assertIn("Missing tools", result["errors"][0])
        self.assertEqual(result["kind"], "file-media-analysis")
        self.assertEqual(len(result["source"]["sha256"]), 64)
        self.source.write_bytes(b"#EXTM3U\nfile:///secret\n")
        result = cli.inspect(str(self.source), str(self.root / "playlist"))
        self.assertEqual(result["status"], "unsupported")
        self.assertEqual(json.loads((self.root / "playlist/manifest.json").read_text())["status"], "unsupported")

    def test_mocked_audio_contract_and_no_linux_imports(self):
        metadata = {"format": {"duration": "2"}, "streams": [
            {"index": 0, "codec_type": "audio", "channels": 1, "sample_rate": "48000"}]}
        logs = "[Parsed_astats_0] Channel: 1\n[Parsed_astats_0] Peak level dB: -inf\n[Parsed_astats_0] RMS level dB: -inf\n[Parsed_astats_0] Overall\n[Parsed_astats_0] Peak level dB: -inf\n[Parsed_astats_0] RMS level dB: -inf\n I: -70 LUFS\nsilence_start: 0\nsilence_end: 2\n"
        def command(argv, **kwargs):
            self.assertNotIn("prlimit", argv)
            self.assertNotIn("shell", kwargs)
            out, err = "version", ""
            if "-show_streams" in argv:
                out = json.dumps(metadata)
                self.assertIn("pipe", argv)
                self.assertEqual(kwargs["input_data"], self.source.read_bytes())
            elif "-af" in argv:
                err = logs
            return subprocess.CompletedProcess(argv, 0, out, err)
        with patch.object(cli, "discover", return_value={"ffmpeg": "ffmpeg.exe", "ffprobe": "ffprobe.exe"}), patch.object(cli, "run_captured", side_effect=command):
            result = cli.inspect(str(self.source), str(self.root / "audio"))
        self.assertEqual(result["status"], "complete", result)
        self.assertEqual(result["video"]["status"], "missing")
        self.assertIsNone(result["audio"]["measurements"]["overall"]["rms_dbfs"])
        self.assertNotIn("lib.streamed_process", sys.modules)

    def test_owned_process_limits_and_cancel(self):
        def run(code, **kwargs):
            return native.run_captured([sys.executable, "-c", code], cwd=str(self.root), **kwargs)
        self.assertEqual(run("print('音')").returncode, 0)
        self.assertEqual(run("print('early input close')", input_data=b"x" * 1024 * 1024).returncode, 0)
        with self.assertRaisesRegex(RuntimeError, "output exceeded"):
            run("import sys; sys.stdout.write('x'*100000)", max_bytes=1000)
        with self.assertRaises(TimeoutError):
            run("import time; time.sleep(30)", timeout=.1)
        # A real grandchild inherits the Job; its PID is evidence for cleanup.
        code = "import subprocess,sys,time,pathlib; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); pathlib.Path('child.pid').write_text(str(p.pid)); time.sleep(30)"
        with self.assertRaises(TimeoutError):
            run(code, timeout=1)
        pid = int((self.root / "child.pid").read_text())
        handle = native.api(native.k, "OpenProcess", native.w.HANDLE, native.w.DWORD, native.w.BOOL, native.w.DWORD)(0x1000, False, pid)
        if handle:
            try:
                exit_code = native.w.DWORD()
                native.api(native.k, "GetExitCodeProcess", native.w.BOOL, native.w.HANDLE, ctypes.POINTER(native.w.DWORD))(handle, ctypes.byref(exit_code))
                self.assertNotEqual(exit_code.value, 259, "Owned descendant survived")
            finally:
                native.close(handle)
        timer = threading.Timer(.2, _thread.interrupt_main)
        timer.start()
        try:
            with self.assertRaises(KeyboardInterrupt):
                run("import time; time.sleep(30)")
        finally:
            timer.join()

    def test_video_pts_and_incomplete_decode(self):
        metadata = {"format": {"duration": "6", "start_time": "0"}, "streams": [
            {"index": 0, "codec_type": "video", "width": 320, "height": 180}],
            "packets": [{"stream_index": 0, "pts_time": str(n), "duration_time": ".1"}
                        for n in (1, 1.5, 2)]}
        broken = False

        def command(argv, **kwargs):
            if "-show_streams" in argv:
                return subprocess.CompletedProcess(argv, 0, json.dumps(metadata), "")
            if "-frames:v" in argv:
                if broken:
                    return subprocess.CompletedProcess(argv, 1, "", "fixture decode failure")
                Path(argv[-1]).write_bytes(b"synthetic mocked PNG")
                return subprocess.CompletedProcess(argv, 0, "", "n: 0 pts_time:1.5")
            return subprocess.CompletedProcess(argv, 0, "fixture version", "")

        with patch.object(cli, "discover", return_value={"ffmpeg": "ffmpeg.exe", "ffprobe": "ffprobe.exe"}), patch.object(cli, "run_captured", side_effect=command):
            result = cli.inspect(str(self.source), str(self.root / "video"))
            self.assertEqual(result["status"], "complete", result)
            self.assertEqual(result["audio"]["status"], "missing")
            self.assertEqual(result["video"]["packet_pts_range_seconds"], [1, 2])
            self.assertEqual(len(result["frames"]), 3)
            self.assertTrue(all(f["requested_seconds"] <= 2 for f in result["frames"]))
            self.assertTrue((self.root / "video/contact-sheet.svg").is_file())
            broken = True
            failed = cli.inspect(str(self.source), str(self.root / "bad-video"))
            self.assertEqual(failed["status"], "incomplete")
            self.assertIn("decode failure", failed["errors"][0])


if __name__ == "__main__":
    unittest.main()
