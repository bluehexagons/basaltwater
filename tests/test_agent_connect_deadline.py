"""Autonomous expiry for silent T3 Connect processes."""

from __future__ import annotations

import threading
import unittest
from unittest.mock import Mock, patch

from common.service_tools import device_pairing_service as pairing


class TestConnectDeadline(unittest.TestCase):
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
