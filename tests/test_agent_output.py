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
from unittest.mock import Mock, patch


SCRIPT = (Path(__file__).resolve().parents[1]
          / "common/agent_skills/basaltwater-project-development/scripts/capture_output.py")
SPEC = importlib.util.spec_from_file_location("capture_output", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
capture_output = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture_output)
READER_SPEC = importlib.util.spec_from_file_location("read_output", SCRIPT.with_name("read_output.py"))
assert READER_SPEC is not None and READER_SPEC.loader is not None
read_output = importlib.util.module_from_spec(READER_SPEC)
READER_SPEC.loader.exec_module(read_output)


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
        result = json.loads(lines[0])
        path = Path(result["summary_path"])
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), result)
        if os.name == "posix":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        return result

    def test_streams_both_outputs_without_shell_or_echoing_content(self) -> None:
        captured = {}
        summary_path = self.root / "artifacts/summary.json"

        class FakeProcess:
            pid = 321

            def __init__(self, command, **kwargs):
                self.command = command
                self.kwargs = kwargs
                captured["command"] = command
                captured["kwargs"] = kwargs
                captured["initial"] = json.loads(summary_path.read_text(encoding="utf-8"))

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
        self.assertEqual(captured["initial"]["status"], "in_progress")
        self.assertIsNone(captured["initial"]["return_code"])
        self.assertNotIn("unsafe", json.dumps(captured["initial"]))
        if os.name == "posix":
            self.assertEqual(Path(summary["stdout_path"]).parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(Path(summary["stdout_path"]).stat().st_mode & 0o777, 0o600)
            self.assertEqual(Path(summary["stderr_path"]).stat().st_mode & 0o777, 0o600)

    def test_selected_directory_is_created_and_never_reused_or_overwritten(self) -> None:
        directory = self.root / "selected output with spaces"
        with (patch.object(capture_output.subprocess, "Popen") as popen,
              patch("sys.stdout", new_callable=io.StringIO) as output):
            popen.return_value.wait.return_value = 0
            self.assertEqual(capture_output.main([
                "--output-directory", str(directory), "--", "program",
            ]), 0)
            self.assertEqual(Path(self.summary(output.getvalue())["summary_path"]), directory / "summary.json")
            original = (directory / "summary.json").read_bytes()
            regular_file = self.root / "existing-file"
            regular_file.write_text("keep", encoding="utf-8")
            for invalid in (directory, regular_file, self.root / "missing-parent/output"):
                popen.reset_mock()
                with self.subTest(path=invalid), patch("sys.stderr", new_callable=io.StringIO), \
                        self.assertRaises(SystemExit) as failure:
                    capture_output.main(["--output-directory", str(invalid), "--", "program"])
                self.assertEqual(failure.exception.code, 2)
                popen.assert_not_called()
            self.assertEqual((directory / "summary.json").read_bytes(), original)
            self.assertEqual(regular_file.read_text(encoding="utf-8"), "keep")

    def test_final_summary_survives_a_lost_terminal(self) -> None:
        with (patch.object(capture_output.subprocess, "Popen") as popen,
              patch.object(capture_output, "_emit", side_effect=BrokenPipeError)):
            popen.return_value.wait.return_value = 23
            with self.assertRaises(BrokenPipeError):
                capture_output.main(["--", "program"])
        saved = json.loads((self.root / "artifacts/summary.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["status"], "completed")
        self.assertEqual(saved["return_code"], 23)

    def test_failed_summary_replace_preserves_previous_status_and_removes_temporary_file(self) -> None:
        path = self.root / "artifacts/summary.json"
        capture_output._save_summary(path, {"status": "in_progress"})
        with patch.object(capture_output.os, "replace", side_effect=OSError("fixture failure")):
            with self.assertRaises(OSError):
                capture_output._save_summary(path, {"status": "completed"})
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"status": "in_progress"})
        self.assertEqual(list(path.parent.iterdir()), [path])

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

    def test_windows_cleanup_kills_the_tree_before_reaping_the_parent(self) -> None:
        process = Mock(pid=321)
        with (patch.object(capture_output.os, "name", "nt"),
              patch.object(capture_output.subprocess, "run", return_value=Mock(returncode=0)) as run):
            result = capture_output._terminate_group(process)
        self.assertIsNone(result)
        self.assertEqual(run.call_args.args[0][1:], ["/PID", "321", "/T", "/F"])
        self.assertFalse(run.call_args.kwargs["check"])
        self.assertEqual(run.call_args.kwargs["timeout"], capture_output._CLEANUP_SECONDS)
        process.kill.assert_not_called()
        process.terminate.assert_not_called()
        process.wait.assert_called_once_with(timeout=capture_output._CLEANUP_SECONDS)

    def test_windows_cleanup_reports_failure_and_bounds_the_parent_fallback(self) -> None:
        for failure in (Mock(returncode=5), OSError("private failure"), subprocess.TimeoutExpired("secret argv", 2)):
            process = Mock(pid=321)
            options = {"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure}
            with (self.subTest(failure=type(failure).__name__),
                  patch.object(capture_output.os, "name", "nt"),
                  patch.object(capture_output.subprocess, "run", **options)):
                result = capture_output._terminate_group(process)
            self.assertIn("Windows process-tree cleanup failed", result)
            self.assertNotIn("private", result)
            self.assertNotIn("secret", result)
            process.kill.assert_called_once_with()
            process.wait.assert_called_once_with(timeout=capture_output._CLEANUP_SECONDS)

    def test_timeout_summary_exposes_uncertain_cleanup(self) -> None:
        with (patch.object(capture_output.subprocess, "Popen") as popen,
              patch.object(capture_output, "_terminate_group", return_value="Windows cleanup unconfirmed"),
              patch("sys.stdout", new_callable=io.StringIO) as output):
            popen.return_value.wait.side_effect = subprocess.TimeoutExpired("redacted", 1)
            result = capture_output.main(["--timeout", "1", "--", "program"])
        self.assertEqual(result, 124)
        self.assertEqual(self.summary(output.getvalue())["cleanup_error"], "Windows cleanup unconfirmed")

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


class AgentOutputReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.root / "summary.json"
        self.record = {"schema_version": 1, "status": "completed", "return_code": 23,
                       "duration_seconds": 1.5, "stdout_bytes": 0, "stderr_bytes": 0}
        self.stdout = self.root / "stdout.log"
        self.stderr = self.root / "stderr.log"
        self.stdout.write_bytes(b"")
        self.stderr.write_bytes(b"")

    def inspect(self, *arguments):
        self.path.write_text(json.dumps(self.record), encoding="utf-8")
        with patch("sys.stdout", new_callable=io.StringIO) as output, patch("sys.stderr", new_callable=io.StringIO) as error:
            code = read_output.main([str(self.path), *arguments])
        return code, json.loads(output.getvalue()) if code == 0 else json.loads(error.getvalue())

    def test_success_and_unfinished_defaults_read_summary_only_without_guessing_liveness(self):
        self.stdout.unlink()
        self.stderr.unlink()
        self.record.update(return_code=0, unrelated_inventory="do-not-load")
        code, value = self.inspect()
        self.assertEqual(code, 0)
        self.assertEqual(value["return_code"], 0)
        self.assertNotIn("streams", value)
        self.assertNotIn("unrelated_inventory", value)
        self.record.update(status="in_progress", return_code=None, duration_seconds=None)
        code, value = self.inspect()
        self.assertEqual(code, 0)
        self.assertEqual(value["status"], "in_progress")
        self.assertIsNone(value["return_code"])
        self.assertNotIn("streams", value)
        self.assertNotIn("running", value)

    def test_failed_command_retains_exit_and_cleanup_evidence_with_shared_unicode_budget(self):
        self.stdout.write_text("prefix\n" + "日本語" * 10000, encoding="utf-8")
        self.stderr.write_text("failure\n" + "é" * 10000, encoding="utf-8")
        self.record.update(stdout_bytes=self.stdout.stat().st_size, stderr_bytes=self.stderr.stat().st_size,
                           cleanup_error="cleanup uncertain")
        code, value = self.inspect("--max-bytes", "65", "--scan-bytes", "128")
        self.assertEqual(code, 0)  # Inspection succeeds; the captured command failed.
        self.assertEqual(value["return_code"], 23)
        self.assertEqual(value["cleanup_error"], "cleanup uncertain")
        streams = value["streams"]
        self.assertLessEqual(sum(len(item["text"].encode("utf-8")) for item in streams.values()), 65)
        self.assertTrue(streams["stdout"]["utf8_replaced"])
        self.assertFalse(streams["stderr"]["utf8_replaced"])
        for item in streams.values():
            self.assertTrue(item["text"])
            self.assertEqual(item["scan_bytes"], 128)
            self.assertTrue(item["scan_truncated"])
            self.assertTrue(item["excerpt_truncated"])
            self.assertFalse(item["changed_during_read"])

    def test_literal_filters_use_only_the_bounded_window_and_preserve_long_line_hits(self):
        self.stdout.write_text("FAILED old evidence\n" + "noise\n" * 2000 + "FAILED current\ntrace end\n", encoding="utf-8")
        code, value = self.inspect("--stream", "stdout", "--match", "FAILED", "--match", "trace", "--scan-bytes", "128")
        self.assertEqual(code, 0)
        stream = value["streams"]["stdout"]
        self.assertEqual(stream["text"], "FAILED current\ntrace end\n")
        self.assertEqual(stream["matched_lines"], 2)
        self.assertTrue(stream["scan_truncated"])
        self.assertFalse(stream["excerpt_truncated"])
        self.stdout.write_text("x" * 100 + "FAILED " + "z" * 500, encoding="utf-8")
        code, value = self.inspect("--stream", "stdout", "--match", "FAILED", "--max-bytes", "32")
        self.assertEqual(code, 0)
        self.assertTrue(value["streams"]["stdout"]["text"].startswith("FAILED "))
        self.assertTrue(value["streams"]["stdout"]["excerpt_truncated"])

    def test_empty_search_is_scoped_and_small_stream_surrenders_unused_budget(self):
        self.stdout.write_text("ok", encoding="utf-8")
        self.stderr.write_text("x" * 100, encoding="utf-8")
        code, value = self.inspect("--max-bytes", "32")
        self.assertEqual(code, 0)
        self.assertEqual(value["streams"]["stdout"]["text"], "ok")
        self.assertEqual(len(value["streams"]["stderr"]["text"]), 30)
        code, value = self.inspect("--match", "absent", "--scan-bytes", "10")
        self.assertEqual(code, 0)
        self.assertEqual(value["streams"]["stderr"]["matched_lines"], 0)
        self.assertTrue(value["streams"]["stderr"]["scan_truncated"])
        self.assertEqual(value["return_code"], 23)

    def test_copied_artifact_uses_adjacent_logs_and_explicit_summary_only_skips_logs(self):
        self.record["stdout_path"] = str(self.root / "unrelated-private-file")
        self.record["stderr_path"] = "../unrelated-file"
        self.stdout.write_text("adjacent evidence", encoding="utf-8")
        code, value = self.inspect("--stream", "stdout")
        self.assertEqual(code, 0)
        self.assertEqual(value["streams"]["stdout"]["text"], "adjacent evidence")
        self.stdout.unlink()
        self.stderr.unlink()
        code, value = self.inspect("--summary-only")
        self.assertEqual(code, 0)
        self.assertEqual(value["return_code"], 23)
        self.assertNotIn("streams", value)

    def test_invalid_summary_and_missing_stream_fail_without_echoing_raw_payload(self):
        for fields in ({"schema_version": True}, {"schema_version": 2}, {"status": "unknown"},
                       {"return_code": True}, {"duration_seconds": float("nan")}, {"stderr_bytes": -1},
                       {"status": "in_progress"}, {"cleanup_error": {"private": "do-not-echo"}}):
            with self.subTest(fields=fields):
                original = self.record.copy()
                self.record.update(fields)
                code, value = self.inspect()
                self.assertEqual(code, 1)
                self.assertIn("inspection_error", value)
                self.assertNotIn("do-not-echo", json.dumps(value))
                self.record = original
        self.stdout.unlink()
        self.assertEqual(self.inspect("--stream", "stdout")[0], 1)

    def test_oversized_malformed_and_symlink_artifacts_are_refused(self):
        for payload in (b"x" * 65537, b"not JSON", b"\xff"):
            self.path.write_bytes(payload)
            with self.subTest(size=len(payload)), patch("sys.stderr", new_callable=io.StringIO) as error:
                self.assertEqual(read_output.main([str(self.path)]), 1)
            self.assertLess(len(error.getvalue()), 1024)
        target = self.root / "real-summary.json"
        target.write_text(json.dumps(self.record), encoding="utf-8")
        self.path.unlink()
        try:
            self.path.symlink_to(target)
        except OSError:
            self.skipTest("symlink creation unavailable for this account")
        with patch("sys.stderr", new_callable=io.StringIO) as error:
            self.assertEqual(read_output.main([str(self.path)]), 1)
        self.assertIn("symlink", error.getvalue())

    def test_argument_limits_are_enforced_before_reading_artifacts(self):
        for arguments in (("--max-bytes", "0"), ("--max-bytes", "65537"), ("--scan-bytes", "1048577"),
                          ("--match", ""), ("--match", "x" * 257), ("--match", "line\n"),
                          ("--summary-only", "--match", "error")):
            with self.subTest(arguments=arguments), patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit) as failure:
                read_output.main([str(self.path), *arguments])
            self.assertEqual(failure.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
