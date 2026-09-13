"""Autonomous expiry for silent T3 Connect processes."""

from __future__ import annotations

import threading
import os
import unittest
from unittest.mock import Mock, patch

from common.service_tools import device_pairing_service as pairing


class TestConnectDeadline(unittest.TestCase):
    def test_full_input_pipe_does_not_block_expiry(self):
        reader, writer = os.pipe()
        self.addCleanup(os.close, reader)
        self.addCleanup(os.close, writer)
        os.set_blocking(writer, False)
        while True:
            try:
                os.write(writer, b'x' * 4096)
            except BlockingIOError:
                break
        os.set_blocking(writer, True)
        process = Mock(pid=12345)
        process.stdin.fileno.return_value = writer
        job = pairing.ConnectJob({})
        job._process = process
        done = threading.Event()
        errors = []

        def send():
            try:
                job.send_input('response')
            except pairing.PairingError as exc:
                errors.append(str(exc))
            finally:
                done.set()

        thread = threading.Thread(target=send, daemon=True)
        thread.start()
        try:
            self.assertTrue(done.wait(1), 'input blocked while holding the job lock')
            self.assertIn('not accepting input', errors[0])
            with patch.object(pairing.os, 'killpg') as kill:
                job._expire(process)
                kill.assert_called_once()
        finally:
            os.read(reader, 4096)
            thread.join(timeout=1)

    def test_silent_process_expires_without_snapshot_requests(self):
        killed = threading.Event()
        finished = threading.Event()
        process = Mock(pid=12345)

        def read(_size):
            if not killed.wait(2):
                raise OSError('test deadline exceeded')
            return ''

        process.stdout.read.side_effect = read
        process.wait.return_value = -9
        job = pairing.ConnectJob({'link_command': ['/mock/provider'], 'restart_request': '/unused'})
        watch = job._watch

        def watched(*args):
            try:
                watch(*args)
            finally:
                finished.set()

        with patch.object(pairing, 'CONNECT_JOB_TTL_SECONDS', 0.02), patch.object(pairing.subprocess, 'Popen', return_value=process), patch.object(pairing.os, 'killpg', side_effect=lambda *_: killed.set()) as kill, patch.object(job, '_watch', side_effect=watched):
            job.start()
            self.assertTrue(finished.wait(2), 'silent provider was not expired')
            kill.assert_called_once_with(12345, pairing.signal.SIGKILL)
            process.wait.assert_called()
            snapshot = job.snapshot()
            self.assertFalse(snapshot['active'])
            self.assertIn('expired', snapshot['error'])

    def test_old_deadline_cannot_kill_replacement_process(self):
        job = pairing.ConnectJob({})
        job._process = Mock(pid=12346)
        with patch.object(pairing.os, 'killpg') as kill:
            job._expire(Mock(pid=12345))
            kill.assert_not_called()
