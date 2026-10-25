"""Kiosk startup waiting stays bounded and never fetches a saved URL."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import subprocess
import threading
import unittest
from unittest.mock import Mock, patch

from desktop import kiosk_network as network


class KioskNetworkTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.stopped = threading.Event()
        self.enterContext(patch.object(network.time, "monotonic", side_effect=lambda: self.now))
        self.wait = self.enterContext(patch.object(self.stopped, "wait", side_effect=self.advance))

    def advance(self, seconds):
        self.now += seconds
        return self.stopped.is_set()

    def test_only_validated_authority_reaches_probe(self):
        for url, host, port in (
            ("http://pve.example/?token=private#secret", "pve.example", 80),
            ("https://pve.example/", "pve.example", 443),
            ("https://[::1]:8006/path?a=b", "::1", 8006),
        ):
            with self.subTest(url=url), patch.object(network, "_attempt", return_value=True) as attempt:
                self.assertTrue(network.wait_for_endpoint(url, self.stopped))
                attempt.assert_called_once_with(host, port, self.stopped, network.PROBE_SECONDS)
        self.wait.assert_not_called()
        for url in ("file:///etc/passwd", "https://user:secret@pve.example/"):
            with self.subTest(url=url), patch.object(network, "_attempt") as attempt, self.assertRaises(ValueError):
                network.wait_for_endpoint(url, self.stopped)
            attempt.assert_not_called()

    def test_transient_failure_retries_and_returns_as_soon_as_connected(self):
        with patch.object(network, "_attempt", side_effect=[False, False, True]) as attempt:
            self.assertTrue(network.wait_for_endpoint("http://localhost/", self.stopped))
        self.assertEqual(attempt.call_count, 3)
        self.assertEqual(self.now, network.RETRY_SECONDS * 2)

    def test_offline_and_slow_probes_share_one_total_deadline(self):
        def unavailable(_host, _port, _stopped, seconds):
            self.assertGreater(seconds, 0)
            self.assertLessEqual(seconds, network.PROBE_SECONDS)
            self.now += seconds
            return False

        with patch.object(network, "_attempt", side_effect=unavailable) as attempt:
            self.assertFalse(network.wait_for_endpoint("http://localhost/", self.stopped))
        self.assertEqual(self.now, network.STARTUP_SECONDS)
        self.assertGreater(attempt.call_count, 1)

    def test_stopped_wait_never_spawns_and_cancellation_during_retry_prevents_next_probe(self):
        self.stopped.set()
        with patch.object(network, "_attempt", return_value=False) as attempt:
            self.assertFalse(network.wait_for_endpoint("http://localhost/", self.stopped))
            attempt.assert_not_called()
            self.stopped.clear()
            self.wait.side_effect = lambda _seconds: self.stopped.set()
            self.assertFalse(network.wait_for_endpoint("http://localhost/", self.stopped))
            attempt.assert_called_once()

    def test_missing_probe_interpreter_falls_back_without_waiting_out_budget(self):
        with patch.object(network, "_attempt", side_effect=FileNotFoundError("python")):
            self.assertFalse(network.wait_for_endpoint("http://localhost/", self.stopped))
        self.wait.assert_not_called()

    def test_probe_result_is_checked_and_worker_has_no_url_or_output(self):
        for result, connected in ((0, True), (1, False), (-15, False)):
            process = Mock()
            process.poll.return_value = result
            with self.subTest(result=result), patch.object(network.subprocess, "Popen", return_value=process) as popen:
                self.assertEqual(network._attempt("::1", 8006, self.stopped, 2), connected)
                argv = popen.call_args.args[0]
                self.assertEqual(argv[-4:], ["--host", "::1", "--port", "8006"])
                self.assertEqual(argv[1], "-I")
                for stream in ("stdin", "stdout", "stderr"):
                    self.assertEqual(popen.call_args.kwargs[stream], subprocess.DEVNULL)
                process.kill.assert_not_called()
                process.wait.assert_called_once_with(timeout=1)

    def test_dns_or_connect_hang_is_killed_and_reaped_at_probe_deadline(self):
        process = Mock()
        process.poll.return_value = None
        with patch.object(network.subprocess, "Popen", return_value=process):
            self.assertFalse(network._attempt("pve.example", 80, self.stopped, 0.25))
        self.assertAlmostEqual(self.now, 0.25)
        process.kill.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=1)

    def test_stop_during_probe_kills_and_reaps_without_waiting_for_dns(self):
        process = Mock()
        process.poll.return_value = None
        self.wait.side_effect = lambda _seconds: self.stopped.set()
        with patch.object(network.subprocess, "Popen", return_value=process):
            self.assertFalse(network._attempt("pve.example", 80, self.stopped, 2))
        self.assertEqual(self.wait.call_count, 1)
        process.kill.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=1)

    def test_exit_race_is_reaped_and_reaping_failure_is_not_hidden(self):
        process = Mock()
        process.poll.return_value = None
        process.kill.side_effect = ProcessLookupError
        self.stopped.set()
        with patch.object(network.subprocess, "Popen", return_value=process):
            self.assertFalse(network._attempt("pve.example", 80, self.stopped, 2))
            process.wait.side_effect = subprocess.TimeoutExpired("probe", 1)
            with self.assertRaises(subprocess.TimeoutExpired):
                network._attempt("pve.example", 80, self.stopped, 2)

    def test_worker_validates_host_and_port_before_opening_and_closes_connection(self):
        with patch.object(network.socket, "create_connection") as connect:
            self.assertEqual(network.main(["--host", "::1", "--port", "8006"]), 0)
            connect.assert_called_once_with(("::1", 8006), timeout=1)
            connect.return_value.__exit__.assert_called_once()
            connect.reset_mock()
            for host, port in (("bad host", "80"), ("pve.example", "0"), ("pve.example", "65536"),
                               ("pve.example/secret?token=x", "80")):
                with self.subTest(host=host, port=port):
                    self.assertEqual(network.main(["--host", host, "--port", port]), 1)
            connect.assert_not_called()

    def test_worker_connect_failure_is_quiet(self):
        with patch.object(network.socket, "create_connection", side_effect=OSError("private host")), \
             redirect_stdout(io.StringIO()) as output:
            self.assertEqual(network.main(["--host", "pve.example", "--port", "80"]), 1)
        self.assertEqual(output.getvalue(), "")
