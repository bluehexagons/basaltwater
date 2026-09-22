"""Setup process streaming without executing commands on the test host."""

from __future__ import annotations

import os
import subprocess
import tempfile
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
                terminate.assert_called_once_with(process, isolated=True)
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
        for interactive in (False, True):
            with self.subTest(interactive=interactive):
                output, output_writer = self._pipe()
                output_writer.write(b"ready\n")
                process = MagicMock(stdout=output, stdin=None)
                with (
                    patch("lib.streamed_process.subprocess.Popen", return_value=process),
                    patch("lib.streamed_process._terminate_timed_out_process") as terminate,
                ):
                    with self.assertRaises(KeyboardInterrupt):
                        run_streamed(
                            ["fake-setup"], timeout=None if interactive else 1,
                            on_output=MagicMock(side_effect=KeyboardInterrupt),
                            interactive=interactive,
                        )
                terminate.assert_called_once_with(process, isolated=not interactive)
                self.assertTrue(output.closed)

    def test_interactive_stream_preserves_terminal_without_consuming_payload(self):
        output, output_writer = self._pipe()
        input_reader, input_writer = self._pipe()
        output_writer.write(b"setup output\n")
        output_writer.close()
        process = MagicMock(stdout=output, stdin=input_writer)
        process.wait.return_value = 0
        chunks = []
        with patch("lib.streamed_process.subprocess.Popen", return_value=process) as popen:
            result = run_streamed(
                ["ssh", "root@host"], timeout=None, on_output=chunks.append,
                input_data=b"archive", interactive=True,
            )
        self.assertEqual(result, 0)
        self.assertFalse(popen.call_args.kwargs["start_new_session"])
        self.assertEqual(popen.call_args.kwargs["stdin"], subprocess.PIPE)
        self.assertEqual(input_reader.read(), b"archive")
        self.assertEqual(chunks, ["setup output\n"])
        process.wait.assert_called_once_with(timeout=None)

    def test_automation_cannot_disable_deadline(self):
        with patch("lib.streamed_process.subprocess.Popen") as popen:
            with self.assertRaisesRegex(ValueError, "finite deadline"):
                run_streamed(["fake-setup"], timeout=None, on_output=lambda line: None)
            popen.assert_not_called()


class TestSetupSSHStreaming(unittest.TestCase):
    def test_setup_selects_matching_ssh_and_stream_terminal_policy(self):
        from lib import setup_common
        from lib.config import SetupConfig

        for interactive in (False, True):
            with (
                self.subTest(interactive=interactive),
                tempfile.TemporaryDirectory() as root,
                patch.object(setup_common, "resource_lock"),
                patch.object(setup_common, "get_ssh_control_path", return_value=os.path.join(root, "control")),
                patch("lib.ssh_utils.get_workspace_known_hosts_path", return_value=os.path.join(root, "known_hosts")),
                patch.object(setup_common, "ensure_remote_sudo", return_value=True),
                patch.object(setup_common, "copy_project_files"),
                patch.object(setup_common, "create_tar_from_dir", return_value=b"runtime"),
                patch.object(setup_common, "_create_payload_archive", return_value=b"payload"),
                patch.object(setup_common, "run_streamed", return_value=0) as stream,
                patch.object(setup_common, "finish_network_transition", return_value=0),
                patch("lib.ssh_utils.sys.stdin.isatty", return_value=interactive),
                patch.dict(os.environ, {"BASALTWATER_SETUP_TIMEOUT": "120", "SSH_AUTH_SOCK": "/test/agent"}),
            ):
                config = SetupConfig(
                    host="192.0.2.33", username="admin", system_type="server_web",
                    ssh_key="/keys/protected_key",
                )
                self.assertEqual(setup_common.run_remote_setup(config), 0)
                command = stream.call_args.args[0]
                options = stream.call_args.kwargs
                self.assertIn("BatchMode=no" if interactive else "BatchMode=yes", command)
                self.assertEqual(options["interactive"], interactive)
                self.assertEqual(options["timeout"], None if interactive else 120)
                self.assertEqual(options["input_data"], b"runtimepayload")
                self.assertEqual(options["env"]["SSH_AUTH_SOCK"], "/test/agent")
                self.assertIn("/keys/protected_key", command)
                # Remote execution remains bounded even when the local prompt isn't.
                self.assertIn("timeout --signal=TERM --kill-after=10s 120", command[-1])
