"""Focused tests for the standalone agent output capture helper."""

from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = (Path(__file__).resolve().parents[1]
          / "common/agent_skills/basaltwater-project-development/scripts/capture_output.py")
SPEC = importlib.util.spec_from_file_location("capture_output", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
capture_output = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture_output)


class AgentOutputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.addCleanup(patch.stopall)
        patch.object(capture_output.tempfile, "mkdtemp", return_value=str(self.root / "artifacts")).start()
        (self.root / "artifacts").mkdir(mode=0o700)

    def summary(self, output: str) -> dict[str, object]:
        lines = output.splitlines()
        self.assertEqual(len(lines), 1)
        return json.loads(lines[0])

    def test_streams_both_outputs_without_shell_or_echoing_content(self) -> None:
        captured = {}

        class FakeProcess:
            pid = 321

            def __init__(self, command, **kwargs):
                self.command = command
                self.kwargs = kwargs
                captured["command"] = command
                captured["kwargs"] = kwargs

            def wait(self, timeout=None):
                self.kwargs["stdout"].write(b"private out\n")
                self.kwargs["stderr"].write(b"private err\n")
                return 0

        with (patch.object(capture_output.subprocess, "Popen", FakeProcess),
              patch("sys.stdout", new_callable=io.StringIO) as output):
            result = capture_output.main(["--timeout", "3", "--", "program", "a; echo unsafe"])
        self.assertEqual(result, 0)
        summary = self.summary(output.getvalue())
        self.assertEqual(summary["status"], "completed")
        self.assertEqual(Path(summary["stdout_path"]).read_bytes(), b"private out\n")
        self.assertEqual(Path(summary["stderr_path"]).read_bytes(), b"private err\n")
        self.assertEqual((summary["stdout_bytes"], summary["stderr_bytes"]), (12, 12))
        self.assertNotIn("private", output.getvalue())
        self.assertNotIn("unsafe", output.getvalue())
        self.assertEqual(captured["command"], ["program", "a; echo unsafe"])
        self.assertIs(captured["kwargs"]["shell"], False)
        self.assertEqual(Path(summary["stdout_path"]).parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(Path(summary["stdout_path"]).stat().st_mode & 0o777, 0o600)
        self.assertEqual(Path(summary["stderr_path"]).stat().st_mode & 0o777, 0o600)

    def test_preserves_child_failure_code(self) -> None:
        with (patch.object(capture_output.subprocess, "Popen") as popen,
              patch("sys.stdout", new_callable=io.StringIO) as output):
            popen.return_value.wait.return_value = 23
            result = capture_output.main(["--", "program"])
        self.assertEqual(result, 23)
        self.assertEqual(self.summary(output.getvalue())["return_code"], 23)
        options = popen.call_args.kwargs
        self.assertIs(options["shell"], False)
        self.assertIs(options["stdin"], subprocess.DEVNULL)
        if os.name == "posix":
            self.assertIs(options["start_new_session"], True)

    @unittest.skipUnless(os.name == "posix", "process groups are POSIX-specific")
    def test_timeout_terminates_then_kills_the_process_group(self) -> None:
        class FakeProcess:
            pid = 654

            def __init__(self, *args, **kwargs):
                self.waits = 0

            def wait(self, timeout=None):
                self.waits += 1
                if self.waits == 1:
                    raise subprocess.TimeoutExpired("redacted", timeout)
                return -signal.SIGKILL

        with (patch.object(capture_output.subprocess, "Popen", FakeProcess),
              patch.object(capture_output.os, "killpg") as killpg,
              patch("sys.stdout", new_callable=io.StringIO) as output):
            result = capture_output.main(["--timeout", "0.01", "--", "program", "secret-arg"])
        self.assertEqual(result, 124)
        self.assertEqual([call.args for call in killpg.call_args_list],
                         [(654, signal.SIGTERM), (654, signal.SIGKILL)])
        summary = self.summary(output.getvalue())
        self.assertEqual(summary["status"], "timeout")
        self.assertEqual(summary["return_code"], 124)
        self.assertNotIn("secret-arg", output.getvalue())

    def test_launch_failure_retains_artifacts_without_recording_arguments(self) -> None:
        with (patch.object(capture_output.subprocess, "Popen", side_effect=FileNotFoundError(2, "not found")),
              patch("sys.stdout", new_callable=io.StringIO) as output):
            result = capture_output.main(["--", "missing-secret-command"])
        self.assertEqual(result, 127)
        summary = self.summary(output.getvalue())
        self.assertEqual(summary["status"], "launch_error")
        self.assertTrue(Path(summary["stdout_path"]).is_file())
        self.assertTrue(Path(summary["stderr_path"]).is_file())
        self.assertNotIn("missing-secret-command", output.getvalue())

    @unittest.skipUnless(os.name == "posix", "process groups are POSIX-specific")
    def test_interruption_cleans_up_and_reports_status(self) -> None:
        class FakeProcess:
            pid = 987

            def __init__(self, *args, **kwargs):
                self.waits = 0

            def wait(self, timeout=None):
                self.waits += 1
                if self.waits == 1:
                    raise KeyboardInterrupt
                return 0

        with (patch.object(capture_output.subprocess, "Popen", FakeProcess),
              patch.object(capture_output.os, "killpg") as killpg,
              patch("sys.stdout", new_callable=io.StringIO) as output):
            result = capture_output.main(["--", "program"])
        self.assertEqual(result, 130)
        self.assertEqual([call.args for call in killpg.call_args_list],
                         [(987, signal.SIGTERM), (987, signal.SIGKILL)])
        self.assertEqual(self.summary(output.getvalue())["status"], "interrupted")


if __name__ == "__main__":
    unittest.main()
