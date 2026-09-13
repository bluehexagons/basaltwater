"""Setup process streaming without executing commands on the test host."""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from lib.streamed_process import run_streamed
from lib.remote_utils import CommandTimeoutError


class TestSetupStream(unittest.TestCase):
    def _pipe(self):
        reader, writer = os.pipe()
        streams = os.fdopen(reader, "rb", buffering=0), os.fdopen(writer, "wb", buffering=0)
        for stream in streams:
            self.addCleanup(stream.close)
        return streams

    def test_blocked_upload_and_inherited_output_pipe_share_deadline(self):
        for blocked_input in (False, True):
            with self.subTest(blocked_input=blocked_input):
                output, held_output = self._pipe()
                input_reader, input_writer = self._pipe()
                process = MagicMock(stdout=output, stdin=input_writer if blocked_input else None)
                process.wait.return_value = 0
                with (
                    patch("lib.streamed_process.subprocess.Popen", return_value=process) as popen,
                    patch("lib.streamed_process._terminate_timed_out_process") as terminate,
                ):
                    with self.assertRaises(CommandTimeoutError):
                        run_streamed(
                            ["fake-setup"], timeout=0.02, on_output=lambda line: None,
                            input_data=b"x" * (1024 * 1024) if blocked_input else None,
                        )
                terminate.assert_called_once_with(process)
                self.assertTrue(popen.call_args.kwargs["start_new_session"])
                self.assertTrue(output.closed)
                if blocked_input:
                    self.assertTrue(input_writer.closed)

    def test_output_is_relayed_and_input_delivered_before_wait(self):
        output, output_writer = self._pipe()
        input_reader, input_writer = self._pipe()
        output_writer.write("ready ✓\nlast line".encode())
        output_writer.close()
        process = MagicMock(stdout=output, stdin=input_writer)
        process.wait.return_value = 7
        chunks = []
        with patch("lib.streamed_process.subprocess.Popen", return_value=process):
            result = run_streamed(["fake-setup"], timeout=1, on_output=chunks.append, input_data=b"archive")
        self.assertEqual(result, 7)
        self.assertEqual(chunks, ["ready ✓\n", "last line"])
        self.assertEqual(input_reader.read(), b"archive")
        self.assertGreater(process.wait.call_args.kwargs["timeout"], 0)

    def test_callback_failure_terminates_child_and_closes_pipe(self):
        output, output_writer = self._pipe()
        output_writer.write(b"ready\n")
        process = MagicMock(stdout=output, stdin=None)
        with (
            patch("lib.streamed_process.subprocess.Popen", return_value=process),
            patch("lib.streamed_process._terminate_timed_out_process") as terminate,
        ):
            with self.assertRaises(KeyboardInterrupt):
                run_streamed(["fake-setup"], timeout=1, on_output=MagicMock(side_effect=KeyboardInterrupt))
        terminate.assert_called_once_with(process)
        self.assertTrue(output.closed)
