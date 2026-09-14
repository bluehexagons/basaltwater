"""Tests for lib/logging_utils.py: rotating logger, service logger, log messaging."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.logging_utils import (
    get_service_logger,
    get_standard_formatter,
    get_rotating_logger,
    log_event,
    log_message,
    log_subprocess_result,
    ensure_log_directory,
)


class TestGetStandardFormatter(unittest.TestCase):
    def test_returns_formatter(self):
        fmt = get_standard_formatter()
        self.assertIsInstance(fmt, logging.Formatter)

    def test_format_string(self):
        fmt = get_standard_formatter()
        self.assertIn('%(asctime)s', fmt._fmt)
        self.assertIn('%(levelname)', fmt._fmt)


class TestGetRotatingLogger(unittest.TestCase):
    def test_creates_logger(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = os.path.join(tmpdir, 'test.log')
            logger = get_rotating_logger('test_logger_1', log_file)
            self.assertIsInstance(logger, logging.Logger)
            self.assertGreater(len(logger.handlers), 0)

    def test_idempotent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = os.path.join(tmpdir, 'test.log')
            logger1 = get_rotating_logger('test_logger_idempotent', log_file)
            handler_count = len(logger1.handlers)
            logger2 = get_rotating_logger('test_logger_idempotent', log_file)
            self.assertEqual(len(logger2.handlers), handler_count)

    def test_writes_to_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = os.path.join(tmpdir, 'test.log')
            logger = get_rotating_logger('test_logger_write', log_file)
            logger.info('test message')
            # Flush handlers
            for h in logger.handlers:
                h.flush()
            with open(log_file, 'r') as f:
                content = f.read()
            self.assertIn('test message', content)

    def test_fallback_on_bad_path(self):
        # /proc is not writable, so the logger should fallback to stderr
        with redirect_stderr(StringIO()), patch.dict(os.environ, {"INFRA_TOOLS_TEST": "1"}):
            logger = get_rotating_logger('test_logger_fallback', '/proc/nonexistent/test.log')
        self.assertIsInstance(logger, logging.Logger)
        # Should have a fallback handler
        self.assertGreater(len(logger.handlers), 0)

    def test_fallback_does_not_write_to_console_in_test_mode(self):
        output = StringIO()
        with patch.dict(os.environ, {"INFRA_TOOLS_TEST": "1"}), redirect_stderr(output):
            logger = get_rotating_logger(
                'test_logger_quiet_fallback', '/proc/nonexistent/test.log'
            )
            logger.error("should stay out of the test log")

        self.assertEqual(output.getvalue(), "")

    def test_non_root_does_not_touch_var_log(self):
        log_file = '/var/log/infra_tools/test/non_root.log'
        with patch('lib.logging_utils.os.geteuid', return_value=1000):
            logger = get_rotating_logger('test_logger_non_root_var_log', log_file)
        self.assertIsInstance(logger, logging.Logger)
        self.assertGreater(len(logger.handlers), 0)
        self.assertFalse(os.path.exists(log_file))


class TestGetServiceLogger(unittest.TestCase):
    def test_test_mode_does_not_configure_syslog(self):
        with (
            patch.dict(os.environ, {"INFRA_TOOLS_TEST": "1"}),
            patch("lib.logging_utils.SysLogHandler") as syslog_handler,
        ):
            get_service_logger(
                "test_service_no_syslog",
                use_syslog=True,
                console_output=False,
            )

        syslog_handler.assert_not_called()

    def test_systemd_journal_uses_one_console_stream_without_syslog_duplicate(self):
        with (
            patch.dict(
                os.environ,
                {"JOURNAL_STREAM": "8:9", "INFRA_TOOLS_TEST": "0"},
            ),
            patch("lib.logging_utils.SysLogHandler") as syslog_handler,
        ):
            logger = get_service_logger(
                "test_service_journal_once",
                use_syslog=True,
                console_output=True,
            )

        try:
            stream_handlers = [
                handler
                for handler in logger.handlers
                if isinstance(handler, logging.StreamHandler)
                and getattr(handler, "stream", None) is sys.stdout
            ]
            self.assertEqual(len(stream_handlers), 1)
            self.assertIs(stream_handlers[0].stream, sys.stdout)
            syslog_handler.assert_not_called()
        finally:
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()


class TestLogMessage(unittest.TestCase):
    def test_log_message(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = os.path.join(tmpdir, 'test.log')
            logger = get_rotating_logger('test_log_msg', log_file)
            log_message(logger, 'hello world')
            for h in logger.handlers:
                h.flush()
            with open(log_file, 'r') as f:
                content = f.read()
            self.assertIn('hello world', content)

    def test_log_event_appends_sorted_context(self):
        logger = logging.getLogger('test_log_event_context')
        with self.assertLogs(logger, level='INFO') as logs:
            log_event(logger, 'webhook received', repo='repo1', branch='main')
        self.assertIn("webhook received | branch='main' repo='repo1'", logs.output[0])

    def test_log_event_omits_none_values(self):
        logger = logging.getLogger('test_log_event_none')
        with self.assertLogs(logger, level='INFO') as logs:
            log_event(logger, 'executor started', job=None, worker='webhook')
        self.assertIn("executor started | worker='webhook'", logs.output[0])
        self.assertNotIn("job=", logs.output[0])


class TestEnsureLogDirectory(unittest.TestCase):
    def test_creates_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('lib.logging_utils.DEFAULT_LOG_DIR', os.path.join(tmpdir, 'logs')):
                result = ensure_log_directory('sub')
                self.assertTrue(result.exists())

    def test_no_subdir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('lib.logging_utils.DEFAULT_LOG_DIR', os.path.join(tmpdir, 'logs')):
                result = ensure_log_directory()
                self.assertTrue(result.exists())


class TestLogSubprocessResult(unittest.TestCase):
    def test_success(self):
        logger = logging.getLogger('test_log_subprocess_result_success')
        with self.assertLogs(logger, level='INFO') as logs:
            ok = log_subprocess_result(
                logger,
                "Did thing",
                subprocess.CompletedProcess(args=['x'], returncode=0, stdout='ok', stderr='')
            )
        self.assertTrue(ok)
        self.assertIn("✓ Did thing", logs.output[0])

    def test_failure_uses_stderr(self):
        logger = logging.getLogger('test_log_subprocess_result_failure')
        with self.assertLogs(logger, level='WARNING') as logs:
            ok = log_subprocess_result(
                logger,
                "Did thing",
                subprocess.CompletedProcess(args=['x'], returncode=1, stdout='', stderr='error line\nmore\nthird\nfourth')
            )
        self.assertFalse(ok)
        self.assertIn("⚠ Did thing failed: error line | more | third | ...", logs.output[0])


if __name__ == '__main__':
    unittest.main()
