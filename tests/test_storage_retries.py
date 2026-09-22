"""Repeated operational failures back off without erasing completed cadence."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from sync.service_tools import storage_ops as ops


class StorageRetryTests(unittest.TestCase):
    def test_retry_delays_grow_and_cap_without_changing_completion(self):
        state = {'job': 1.0}
        with patch.object(ops.time, 'time', return_value=1_000_000.0) as clock:
            for delay in (3600, 7200, 14400, 28800, 57600, 86400, 86400):
                ops.record_attempt(state, 'job', False)
                self.assertEqual(ops.retry_delay_remaining(state, 'job'), delay)
                self.assertFalse(ops.is_operation_due(state, 'job', 'hourly'))
                self.assertEqual(state['job'], 1.0)
                clock.return_value += delay
                self.assertTrue(ops.is_operation_due(state, 'job', 'hourly'))
            ops.record_attempt(state, 'job', True)
        self.assertNotIn('_attempts', state)

    def test_corrupt_or_clock_reversed_retry_does_not_stall_forever(self):
        with patch.object(ops.time, 'time', return_value=10000):
            for value in ([], {'job': []}, {'job': {'failed_at': 20000, 'retry_after': 22000}},
                          {'job': {'failed_at': 1, 'retry_after': float('inf')}},
                          {'job': {'failed_at': 1, 'retry_after': 1_000_000}}):
                self.assertEqual(ops.retry_delay_remaining({'_attempts': value}, 'job'), 0)

    def test_waiting_full_scrub_does_not_fall_back_to_fast_parity(self):
        state = {'scrub:/data:.db': 1.0}
        with patch.object(ops.time, 'time', return_value=1_000_000.0):
            ops.record_attempt(state, 'scrub:/data:.db', False)
            with patch.object(ops, 'load_setup_config', return_value={'scrub_specs': [['/data', '.db', '10%', 'weekly']]}), patch.object(ops, 'load_last_run', return_value=state), patch.object(ops, 'save_last_run'), patch.object(ops, 'parse_notification_args', return_value=[]), patch.object(ops, 'get_service_logger'), patch.object(ops, 'run_scrub') as scrub:
                result = ops.execute_storage_operations()
        scrub.assert_not_called()
        self.assertEqual(result['scrubs'], [])
        self.assertEqual(result['parity_updates'], [])
