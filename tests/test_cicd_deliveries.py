"""Receipt persistence, replay suppression, queue admission, and build logs."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
from io import BytesIO
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from web.service_tools import cicd_deliveries as deliveries
from web.service_tools import cicd_executor as executor
from web.service_tools import webhook_receiver as receiver


class TestDeliveries(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.jobs = str(self.root / 'jobs')
        self.ledger = str(self.root / 'receipts.sqlite3')
        self.key = 'a' * 64
        self.payload = {'repo_url': 'https://example.test/org/repo.git',
                        'ref': 'refs/heads/main', 'commit_sha': 'b' * 40,
                        'pusher': 'alice'}

    def enqueue(self, key=None):
        deliveries.enqueue(self.ledger, self.jobs, key or self.key, self.payload)

    def job(self):
        return next(Path(self.jobs).glob('*.json'))

    def test_concurrent_duplicate_delivery_creates_one_job(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [pool.submit(self.enqueue) for _ in range(2)]
            for result in results:
                result.result(timeout=10)
        self.assertEqual(len(list(Path(self.jobs).glob('*.json'))), 1)
        with sqlite3.connect(self.ledger) as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM receipts').fetchone()[0], 1)

    def test_restart_recovers_reservation_without_replaying_claimed_job(self):
        with patch.object(deliveries, 'write_json_atomic', side_effect=OSError('interrupted publication')):
            with self.assertRaises(OSError):
                self.enqueue()
        self.assertEqual(list(Path(self.jobs).glob('*.json')), [])
        deliveries.recover_pending(self.ledger, self.jobs)
        job = self.job()
        payload = json.loads(job.read_text())
        self.assertTrue(deliveries.claim(self.ledger, str(job), payload))
        # Simulate a crash immediately after claiming, before job removal.
        self.assertFalse(deliveries.claim(self.ledger, str(job), payload))
        job.unlink()
        deliveries.recover_pending(self.ledger, self.jobs)
        self.enqueue()
        self.assertFalse(job.exists())

    def test_queue_and_disk_budgets_reject_new_jobs_but_allow_duplicates(self):
        with patch.object(deliveries, 'MAX_PENDING_JOBS', 1):
            self.enqueue()
            self.enqueue()
            with self.assertRaisesRegex(ValueError, 'full'):
                self.enqueue('c' * 64)
        with patch.object(deliveries.shutil, 'disk_usage', return_value=SimpleNamespace(free=0)):
            with self.assertRaisesRegex(ValueError, 'free space'):
                self.enqueue('d' * 64)
        self.assertEqual(len(list(Path(self.jobs).glob('*.json'))), 1)

    def test_reserved_and_legacy_jobs_share_one_capacity_budget(self):
        with patch.object(deliveries, 'write_json_atomic', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                self.enqueue()
        (Path(self.jobs) / 'legacy.json').write_text('{}')
        with patch.object(deliveries, 'MAX_PENDING_JOBS', 2):
            with self.assertRaisesRegex(ValueError, 'full'):
                self.enqueue('c' * 64)

    def test_malformed_signature_fails_without_an_exception(self):
        for signature in ('sha256=é', 'sha256=' + 'g' * 64, 'sha256=' + 'a' * 63):
            self.assertFalse(receiver.verify_github_signature('secret', b'{}', signature))

    def test_stale_delivery_expires_before_build(self):
        with patch.object(deliveries.time, 'time', return_value=1):
            self.enqueue()
        job = self.job()
        self.assertFalse(deliveries.claim(self.ledger, str(job), json.loads(job.read_text())))

    def test_recovered_jobs_keep_acceptance_order(self):
        with patch.object(deliveries.time, 'time', return_value=1), patch.object(deliveries, 'write_json_atomic', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                self.enqueue('f' * 64)
        with patch.object(deliveries.time, 'time', return_value=2):
            self.enqueue('a' * 64)
        deliveries.recover_pending(self.ledger, self.jobs)
        jobs = deliveries.pending_job_files(self.ledger, self.jobs)
        self.assertEqual([job.name for job in jobs], ['delivery-' + 'f' * 64 + '.json',
                                                     'delivery-' + 'a' * 64 + '.json'])

    def test_receipt_retention_is_bounded_and_expires_terminal_entries(self):
        self.enqueue()
        job = self.job()
        deliveries.claim(self.ledger, str(job), json.loads(job.read_text()))
        job.unlink()
        with patch.object(deliveries, 'MAX_RECEIPTS', 1):
            with self.assertRaisesRegex(ValueError, 'full'):
                self.enqueue('c' * 64)
            with patch.object(deliveries, 'RECEIPT_TTL_SECONDS', -1):
                self.enqueue('c' * 64)

    def test_signed_body_replay_cannot_bypass_dedup_by_changing_header(self):
        body = json.dumps({'repository': {'clone_url': self.payload['repo_url']},
                           'ref': self.payload['ref'], 'after': self.payload['commit_sha'],
                           'pusher': {'name': 'alice'}}).encode()
        signature = 'sha256=' + hmac.new(b'secret', body, hashlib.sha256).hexdigest()
        with patch.object(receiver, 'JOBS_DIR', self.jobs), patch.object(receiver, 'DELIVERIES_FILE', self.ledger), patch.object(receiver, 'load_config', return_value={'repositories': [{'url': self.payload['repo_url']}]}), patch.dict(os.environ, WEBHOOK_SECRET='secret'):
            for delivery_id in ('original', 'changed-header'):
                handler = object.__new__(receiver.WebhookHandler)
                handler.path = '/webhook'
                handler.headers = {'Content-Length': str(len(body)), 'X-Hub-Signature-256': signature,
                                   'X-GitHub-Event': 'push', 'X-GitHub-Delivery': delivery_id}
                handler.rfile = BytesIO(body)
                handler.wfile = BytesIO()
                handler.send_response = Mock()
                handler.send_header = Mock()
                handler.end_headers = Mock()
                handler.send_error = Mock()
                handler.do_POST()
                handler.send_error.assert_not_called()
                handler.send_response.assert_called_once_with(202)
        self.assertEqual(len(list(Path(self.jobs).glob('*.json'))), 1)

    def test_executor_retains_job_when_receipt_is_unavailable(self):
        self.enqueue()
        job = self.job()
        with patch.object(executor, 'DELIVERIES_FILE', str(self.root / 'missing')), patch.object(executor, 'load_config', return_value={'repositories': []}), patch.object(executor, 'clone_or_update_repo') as clone:
            self.assertFalse(executor.process_job(str(job)))
            clone.assert_not_called()
        self.assertTrue(job.exists())

    def test_same_commit_attempts_keep_separate_logs_and_job_mapping(self):
        config = {'repositories': [{'url': self.payload['repo_url']}]}
        logs = self.root / 'logs'
        with patch.object(executor, 'LOGS_DIR', str(logs)), patch.object(executor, 'load_config', return_value=config), patch.object(executor, 'clone_or_update_repo', return_value=True), patch.object(executor, 'load_notification_configs_from_state', return_value=[]):
            for index in range(2):
                job = self.root / f'manual-{index}.json'
                job.write_text(json.dumps(self.payload))
                self.assertTrue(executor.process_job(str(job)))
        files = list(logs.glob('*.log'))
        self.assertEqual(len(files), 2)
        content = '\n'.join(path.read_text() for path in files)
        self.assertIn('Job: manual-0.json', content)
        self.assertIn('Job: manual-1.json', content)
