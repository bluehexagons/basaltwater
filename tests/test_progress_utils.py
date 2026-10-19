#!/usr/bin/env python3
"""Tests for progress tracking utilities."""

from __future__ import annotations

import unittest
import time
from unittest.mock import Mock
from unittest.mock import patch
from lib.progress_utils import (
    ProgressTracker,
    ProgressMessage,
    format_bytes,
    format_duration,
    format_file_count,
    log_progress_if_due
)


class TestFormatBytes(unittest.TestCase):
    """Tests for format_bytes function."""
    
    def test_bytes(self) -> None:
        """Test formatting bytes."""
        self.assertEqual(format_bytes(0), "0 B")
        self.assertEqual(format_bytes(512), "512 B")
        self.assertEqual(format_bytes(1023), "1023 B")
    
    def test_kilobytes(self) -> None:
        """Test formatting kilobytes."""
        self.assertEqual(format_bytes(1024), "1.00 KB")
        self.assertEqual(format_bytes(1536), "1.50 KB")
        self.assertEqual(format_bytes(1024 * 999), "999.00 KB")
    
    def test_megabytes(self) -> None:
        """Test formatting megabytes."""
        self.assertEqual(format_bytes(1024 * 1024), "1.00 MB")
        self.assertEqual(format_bytes(int(1024 * 1024 * 2.5)), "2.50 MB")
        self.assertEqual(format_bytes(1024 * 1024 * 999), "999.00 MB")
    
    def test_gigabytes(self) -> None:
        """Test formatting gigabytes."""
        self.assertEqual(format_bytes(1024 * 1024 * 1024), "1.00 GB")
        self.assertEqual(format_bytes(int(1024 * 1024 * 1024 * 3.75)), "3.75 GB")
        self.assertEqual(format_bytes(1024 * 1024 * 1024 * 1000), "1000.00 GB")


class TestFormatDuration(unittest.TestCase):
    """Tests for format_duration function."""
    
    def test_seconds(self) -> None:
        """Test formatting seconds."""
        self.assertEqual(format_duration(0), "0s")
        self.assertEqual(format_duration(30), "30s")
        self.assertEqual(format_duration(59), "59s")
        self.assertEqual(format_duration(59.9), "60s")
    
    def test_minutes(self) -> None:
        """Test formatting minutes."""
        self.assertEqual(format_duration(60), "1m 0s")
        self.assertEqual(format_duration(90), "1m 30s")
        self.assertEqual(format_duration(3599), "59m 59s")
    
    def test_hours(self) -> None:
        """Test formatting hours."""
        self.assertEqual(format_duration(3600), "1h 0m")
        self.assertEqual(format_duration(3660), "1h 1m")
        self.assertEqual(format_duration(7384), "2h 3m")
        self.assertEqual(format_duration(86400), "24h 0m")


class TestFormatFileCount(unittest.TestCase):
    """Tests for format_file_count function."""
    
    def test_current_only(self) -> None:
        """Test formatting current count without total."""
        self.assertEqual(format_file_count(0), "0")
        self.assertEqual(format_file_count(42), "42")
        self.assertEqual(format_file_count(1234), "1234")
    
    def test_current_with_total(self) -> None:
        """Test formatting current and total counts."""
        self.assertEqual(format_file_count(0, 100), "0/100")
        self.assertEqual(format_file_count(42, 100), "42/100")
        self.assertEqual(format_file_count(100, 100), "100/100")
    
    def test_zero_total(self) -> None:
        """Test that zero total is treated as no total."""
        self.assertEqual(format_file_count(42, 0), "42")
    
    def test_none_total(self) -> None:
        """Test that None total shows only current."""
        self.assertEqual(format_file_count(42, None), "42")


class TestProgressMessage(unittest.TestCase):
    """Tests for ProgressMessage builder."""
    
    def test_empty_message(self) -> None:
        """Test building empty progress message."""
        msg = ProgressMessage().build()
        self.assertEqual(msg, "Progress:")
    
    def test_files_current_only(self) -> None:
        """Test adding file count without total."""
        msg = ProgressMessage().add_files(42).build()
        self.assertEqual(msg, "Progress:, 42 files")
    
    def test_combined_message(self) -> None:
        """Test building message with multiple components."""
        msg = (ProgressMessage("Syncing")
               .add_percentage(75)
               .add_files(42, 100)
               .add_bytes(1024 * 1024 * 50)
               .add_duration(125)
               .build())
        self.assertEqual(msg, "Syncing:, 75% complete, 42/100 files, 50.00 MB transferred, 2m 5s elapsed")

    def test_custom_labels_and_text(self) -> None:
        msg = (ProgressMessage("Syncing")
               .add_files(42, label="documents")
               .add_bytes(1024 * 1024 * 50, label="processed")
               .add_duration(125, label="remaining")
               .add_custom("almost done")
               .build())
        self.assertEqual(msg, "Syncing:, 42 documents, 50.00 MB processed, 2m 5s remaining, almost done")


class TestProgressTracker(unittest.TestCase):
    """Tests for ProgressTracker class."""
    
    def test_get_elapsed_seconds(self) -> None:
        """Test elapsed time calculation."""
        tracker = ProgressTracker()
        tracker.start_time = time.time() - 10  # Started 10 seconds ago
        elapsed = tracker.get_elapsed_seconds()
        self.assertGreaterEqual(elapsed, 9.9)
        self.assertLessEqual(elapsed, 10.1)
    
    def test_log_if_due_skips(self) -> None:
        """Test log_if_due skips when interval not reached."""
        mock_logger = Mock()
        tracker = ProgressTracker(interval_seconds=60, logger=mock_logger)
        tracker.last_log_time = time.time()  # Just logged
        
        logged = tracker.log_if_due("Test message")
        self.assertFalse(logged)
        mock_logger.info.assert_not_called()
    
    def test_log_if_due_logs(self) -> None:
        """Test log_if_due logs when interval reached."""
        mock_logger = Mock()
        tracker = ProgressTracker(interval_seconds=1, logger=mock_logger)
        tracker.last_log_time = time.time() - 2  # 2 seconds ago
        
        logged = tracker.log_if_due("Test message")
        self.assertTrue(logged)
        mock_logger.info.assert_called_once_with("Test message")
    
    def test_force_log(self) -> None:
        """Test force_log bypasses interval check."""
        mock_logger = Mock()
        tracker = ProgressTracker(interval_seconds=60, logger=mock_logger)
        tracker.last_log_time = time.time()  # Just logged
        
        tracker.force_log("Forced message")
        mock_logger.info.assert_called_once_with("Forced message")
    
    def test_log_func_overrides_logger(self) -> None:
        """Test that log_func takes precedence over logger."""
        mock_logger = Mock()
        mock_log_func = Mock()
        tracker = ProgressTracker(interval_seconds=1, logger=mock_logger, log_func=mock_log_func)
        tracker.last_log_time = time.time() - 2  # 2 seconds ago
        
        tracker.log_if_due("Test message")
        mock_log_func.assert_called_once_with("Test message")
        mock_logger.info.assert_not_called()
    
    def test_log_without_logger_or_func(self) -> None:
        """Test logging falls back to print when no logger/func provided."""
        tracker = ProgressTracker(interval_seconds=1)
        tracker.last_log_time = time.time() - 2  # 2 seconds ago

        with patch("builtins.print") as mock_print:
            self.assertTrue(tracker.log_if_due("Test message"))

        mock_print.assert_called_once_with("Test message", flush=True)


class TestLogProgressIfDue(unittest.TestCase):
    """Tests for log_progress_if_due convenience function."""
    
    def test_skips_when_not_due(self) -> None:
        """Test function skips logging when interval not reached."""
        mock_logger = Mock()
        last_log = time.time()  # Just logged
        
        was_logged, new_last_log = log_progress_if_due(
            last_log, "Test message", logger=mock_logger, interval_seconds=60
        )
        
        self.assertFalse(was_logged)
        self.assertEqual(new_last_log, last_log)
        mock_logger.info.assert_not_called()
    
    def test_logs_when_due(self) -> None:
        """Test function logs when interval reached."""
        mock_logger = Mock()
        last_log = time.time() - 70  # 70 seconds ago
        
        was_logged, new_last_log = log_progress_if_due(
            last_log, "Test message", logger=mock_logger, interval_seconds=60
        )
        
        self.assertTrue(was_logged)
        self.assertGreater(new_last_log, last_log)
        mock_logger.info.assert_called_once_with("Test message")
    
    def test_with_custom_function(self) -> None:
        """Test function with custom log function."""
        mock_log_func = Mock()
        last_log = time.time() - 70  # 70 seconds ago
        
        was_logged, new_last_log = log_progress_if_due(
            last_log, "Test message", log_func=mock_log_func, interval_seconds=60
        )
        
        self.assertTrue(was_logged)
        self.assertGreater(new_last_log, last_log)
        mock_log_func.assert_called_once_with("Test message")
    
    def test_custom_interval(self) -> None:
        """Test function with custom interval."""
        mock_logger = Mock()
        last_log = time.time() - 5  # 5 seconds ago
        
        # Should log with 3 second interval
        was_logged, _ = log_progress_if_due(
            last_log, "Test message", logger=mock_logger, interval_seconds=3
        )
        self.assertTrue(was_logged)
        
        # Should not log with 10 second interval
        mock_logger.reset_mock()
        was_logged, _ = log_progress_if_due(
            last_log, "Test message", logger=mock_logger, interval_seconds=10
        )
        self.assertFalse(was_logged)


if __name__ == '__main__':
    unittest.main()
