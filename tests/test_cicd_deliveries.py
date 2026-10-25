"""Receipt persistence, replay suppression, queue admission, and build logs."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing, contextmanager, nullcontext
import errno
import hashlib
import hmac
from io import BytesIO
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import time
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

    def reserve_then_publish_later_delivery(self):
        first_key, later_key = 'f' * 64, 'a' * 64
        first = Path(self.jobs) / f'delivery-{first_key}.json'
        replace = os.replace

        def interrupt_first_publication(source, target):
            if Path(target) == first:
                raise OSError('interrupted before rename')
            replace(source, target)

        # Equal wall times and reverse digest order must retain acceptance order.
        with patch.object(deliveries.time, 'time', return_value=time.time()), patch(
            'lib.atomic_io.os.replace', side_effect=interrupt_first_publication,
        ):
            with self.assertRaisesRegex(OSError, 'before rename'):
                self.enqueue(first_key)
            deliveries.enqueue(self.ledger, self.jobs, later_key,
                               {**self.payload, 'commit_sha': 'c' * 40})
        self.assertFalse(first.exists())
        return first_key, later_key

    @contextmanager
    def executor_drain(self):
        with ExitStack() as stack:
            for name, value in {
                'STATE_DIR': str(self.root), 'JOBS_DIR': self.jobs,
                'DELIVERIES_FILE': self.ledger, 'LOCK_FILE': str(self.root / 'executor.lock'),
                'WORKSPACES_DIR': str(self.root / 'workspaces'), 'LOGS_DIR': str(self.root / 'logs'),
            }.items():
                stack.enter_context(patch.object(executor, name, value))
            stack.enter_context(patch.object(executor, 'receiver_state', side_effect=nullcontext))
            stack.enter_context(patch.object(executor, 'load_config', return_value={
                'repositories': [{'url': self.payload['repo_url'], 'scripts': {'deploy': 'deploy.sh'}}],
            }))
            stack.enter_context(patch.object(executor, 'run_manifest_workflow', return_value=True))
            stack.enter_context(patch.object(executor, 'load_notification_configs_from_state', return_value=[]))
            mocks = SimpleNamespace()
            for name in ('clone_or_update_repo', 'run_script', 'cleanup_old_build_logs', 'cleanup_stale_workspaces', 'log_event'):
                setattr(mocks, name, stack.enter_context(patch.object(executor, name, return_value=True)))
            mocks.claim = stack.enter_context(patch.object(executor, 'claim', wraps=deliveries.claim))
            yield mocks

    def test_concurrent_duplicate_delivery_creates_one_job(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [pool.submit(self.enqueue) for _ in range(2)]
            for result in results:
                result.result(timeout=10)
        self.assertEqual(len(list(Path(self.jobs).glob('*.json'))), 1)
        with closing(sqlite3.connect(self.ledger)) as connection:
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
        for first_time, later_time in ((1, 2), (1, 1), (2, 1)):
            with self.subTest(times=(first_time, later_time)), tempfile.TemporaryDirectory(dir=self.root) as directory:
                ledger, jobs_dir = str(Path(directory) / 'ledger'), str(Path(directory) / 'jobs')
                with patch.object(deliveries.time, 'time', return_value=first_time), patch.object(deliveries, 'write_json_atomic', side_effect=OSError('interrupted')):
                    with self.assertRaises(OSError):
                        deliveries.enqueue(ledger, jobs_dir, 'f' * 64, self.payload)
                with patch.object(deliveries.time, 'time', return_value=later_time):
                    deliveries.enqueue(ledger, jobs_dir, 'a' * 64, self.payload)
                # Queue selection itself must recover; no caller recovery first.
                jobs = deliveries.pending_job_files(ledger, jobs_dir)
                self.assertEqual([job.name for job in jobs], ['delivery-' + 'f' * 64 + '.json',
                                                             'delivery-' + 'a' * 64 + '.json'])

    def test_queue_enumeration_holds_the_recovery_transaction(self):
        self.reserve_then_publish_later_delivery()
        ordered = deliveries._ordered_job_files

        def enumerate_while_locked(*args):
            with closing(sqlite3.connect(self.ledger, timeout=0)) as competing_receiver:
                with self.assertRaisesRegex(sqlite3.OperationalError, 'locked'):
                    competing_receiver.execute('BEGIN IMMEDIATE')
            return ordered(*args)

        with patch.object(deliveries, '_ordered_job_files', side_effect=enumerate_while_locked):
            self.assertEqual(len(deliveries.pending_job_files(self.ledger, self.jobs)), 2)

    def test_queue_selection_rechecks_a_ledger_created_during_legacy_scan(self):
        ordered = deliveries._ordered_job_files

        def first_receiver_arrives(jobs_dir, accepted):
            if not Path(self.ledger).exists():
                self.reserve_then_publish_later_delivery()
            return ordered(jobs_dir, accepted)

        with patch.object(deliveries, '_ordered_job_files', side_effect=first_receiver_arrives):
            jobs = deliveries.pending_job_files(self.ledger, self.jobs)
        self.assertEqual([path.name for path in jobs], [f'delivery-{key * 64}.json' for key in ('f', 'a')])

    def test_legacy_queue_without_a_ledger_keeps_mtime_order(self):
        Path(self.jobs).mkdir()
        older, newer = Path(self.jobs) / 'z.json', Path(self.jobs) / 'a.json'
        for path, timestamp in ((older, 1), (newer, 2)):
            path.write_text('{}')
            os.utime(path, (timestamp, timestamp))
        self.assertEqual(deliveries.pending_job_files(self.ledger, self.jobs), [older, newer])
        self.assertFalse(Path(self.ledger).exists())

    def test_executor_recovers_before_claiming_or_deploying_later_delivery(self):
        keys = self.reserve_then_publish_later_delivery()
        with self.executor_drain() as calls:
            def clone_after_durable_claim(repo_url, workspace, ref, commit):
                # Every build starts only after the corresponding claim commits.
                with closing(sqlite3.connect(self.ledger)) as connection:
                    states = dict(connection.execute('SELECT key, state FROM receipts'))
                expected = {keys[0]: 'claimed', keys[1]: 'pending' if commit == 'b' * 40 else 'claimed'}
                self.assertEqual(states, expected)
                return True

            calls.clone_or_update_repo.side_effect = clone_after_durable_claim
            self.assertEqual(executor.main(), 0)
            self.assertEqual([call.args[2]['delivery_key'] for call in calls.claim.call_args_list], list(keys))
            self.assertEqual([call.args[3] for call in calls.clone_or_update_repo.call_args_list], ['b' * 40, 'c' * 40])
            deployed_commits = [Path(call.args[2]).read_text() for call in calls.run_script.call_args_list]
            self.assertEqual(len(deployed_commits), 2)
            for content, commit in zip(deployed_commits, ('b' * 40, 'c' * 40)):
                self.assertIn(f'Commit: {commit}', content)
        self.assertEqual(list(Path(self.jobs).glob('*.json')), [])

    def test_executor_stops_drain_until_recovery_publication_succeeds(self):
        keys = self.reserve_then_publish_later_delivery()
        with self.executor_drain() as calls:
            with patch.object(deliveries, 'write_json_atomic', side_effect=OSError('disk unavailable')):
                self.assertEqual(executor.main(), 1)
            calls.claim.assert_not_called()
            calls.clone_or_update_repo.assert_not_called()
            calls.run_script.assert_not_called()
            calls.cleanup_old_build_logs.assert_not_called()
            self.assertFalse(any(call.args[1] == 'CI/CD executor finished' for call in calls.log_event.call_args_list))
            with closing(sqlite3.connect(self.ledger)) as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM receipts WHERE state='pending'").fetchone()[0], 2)
            # The failed drain retains both reservations for a later retry.
            self.assertEqual(executor.main(), 0)
            self.assertEqual([call.args[2]['delivery_key'] for call in calls.claim.call_args_list], list(keys))

    def test_executor_stops_before_later_claim_when_earlier_claim_is_unavailable(self):
        keys = self.reserve_then_publish_later_delivery()
        with self.executor_drain() as calls:
            calls.claim.side_effect = sqlite3.OperationalError('ledger unavailable')
            self.assertEqual(executor.main(), 1)
            self.assertEqual([call.args[2]['delivery_key'] for call in calls.claim.call_args_list], [keys[0]])
            calls.clone_or_update_repo.assert_not_called()
            calls.run_script.assert_not_called()
            self.assertEqual(len(list(Path(self.jobs).glob('*.json'))), 2)
            calls.claim.reset_mock(side_effect=True)
            self.assertEqual(executor.main(), 0)
            self.assertEqual([call.args[2]['delivery_key'] for call in calls.claim.call_args_list], list(keys))

    def test_executor_retains_unreadable_delivery_before_claim_and_retries_in_order(self):
        keys = self.reserve_then_publish_later_delivery()
        first = Path(self.jobs) / f'delivery-{keys[0]}.json'
        load_job = executor._load_job_file

        def transient_read_error(path):
            if Path(path) == first:
                raise OSError(errno.EIO, 'transient job read error')
            return load_job(path)

        with self.executor_drain() as calls:
            with patch.object(executor, '_load_job_file', side_effect=transient_read_error):
                self.assertEqual(executor.main(), 1)
            self.assertTrue(first.exists())
            calls.claim.assert_not_called()
            calls.clone_or_update_repo.assert_not_called()
            calls.run_script.assert_not_called()
            with closing(sqlite3.connect(self.ledger)) as connection:
                self.assertEqual(dict(connection.execute('SELECT key, state FROM receipts')),
                                 dict.fromkeys(keys, 'pending'))

            def clone_after_durable_claim(repo_url, workspace, ref, commit):
                with closing(sqlite3.connect(self.ledger)) as connection:
                    self.assertEqual(dict(connection.execute('SELECT key, state FROM receipts')),
                                     {keys[0]: 'claimed', keys[1]: 'pending' if commit == 'b' * 40 else 'claimed'})
                return True

            calls.clone_or_update_repo.side_effect = clone_after_durable_claim
            self.assertEqual(executor.main(), 0)
            self.assertEqual([call.args[2]['delivery_key'] for call in calls.claim.call_args_list], list(keys))
            self.assertEqual([call.args[3] for call in calls.clone_or_update_repo.call_args_list], ['b' * 40, 'c' * 40])
            self.assertEqual(len(calls.run_script.call_args_list), 2)
            for call, commit in zip(calls.run_script.call_args_list, ('b' * 40, 'c' * 40)):
                self.assertIn(f'Commit: {commit}', Path(call.args[2]).read_text())
        self.assertEqual(list(Path(self.jobs).glob('*.json')), [])

    def test_executor_retains_corrupt_delivery_until_repaired_before_later_claim(self):
        invalid_jobs = {
            'invalid JSON': '{',
            'non-object JSON': '[]',
            'invalid fields': json.dumps({**self.payload, 'delivery_key': 'f' * 64, 'commit_sha': 'bad'}),
            'missing delivery key': json.dumps(self.payload),
        }
        for reason, content in invalid_jobs.items():
            with self.subTest(reason=reason), tempfile.TemporaryDirectory(dir=self.root) as directory, \
                    patch.object(self, 'ledger', str(Path(directory) / 'ledger')), \
                    patch.object(self, 'jobs', str(Path(directory) / 'jobs')):
                keys = self.reserve_then_publish_later_delivery()
                deliveries.recover_pending(self.ledger, self.jobs)
                first = Path(self.jobs) / f'delivery-{keys[0]}.json'
                original = first.read_text()
                first.write_text(content)
                with self.executor_drain() as calls:
                    # A repeat drain must neither discard nor bypass the reservation.
                    for _ in range(2):
                        self.assertEqual(executor.main(), 1)
                        self.assertTrue(first.exists())
                        self.assertEqual(first.read_text(), content)
                    calls.clone_or_update_repo.assert_not_called()
                    calls.run_script.assert_not_called()
                    self.assertTrue(all(call.args[1] == str(first) for call in calls.claim.call_args_list))
                    with closing(sqlite3.connect(self.ledger)) as connection:
                        self.assertEqual(dict(connection.execute('SELECT key, state FROM receipts')),
                                         dict.fromkeys(keys, 'pending'))
                    first.write_text(original)
                    calls.claim.reset_mock()
                    self.assertEqual(executor.main(), 0)
                    self.assertEqual([call.args[2]['delivery_key'] for call in calls.claim.call_args_list], list(keys))
                    self.assertEqual([call.args[3] for call in calls.clone_or_update_repo.call_args_list], ['b' * 40, 'c' * 40])
                    self.assertEqual(calls.run_script.call_count, 2)

    def test_executor_discards_malformed_legacy_job_once_and_drains_later_delivery(self):
        for reason, content in (('read error', '{}'), ('invalid JSON', '{'), ('invalid fields', '{}')):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory(dir=self.root) as directory, \
                    patch.object(self, 'ledger', str(Path(directory) / 'ledger')), \
                    patch.object(self, 'jobs', str(Path(directory) / 'jobs')):
                self.enqueue()
                legacy = Path(self.jobs) / 'legacy.json'
                legacy.write_text(content)
                os.utime(legacy, (1, 1))
                load_job = executor._load_job_file

                def read_job(path):
                    if reason == 'read error' and Path(path) == legacy:
                        raise OSError(errno.EIO, 'legacy job read error')
                    return load_job(path)

                with self.executor_drain() as calls, patch.object(executor, '_load_job_file', side_effect=read_job) as load:
                    self.assertEqual(executor.main(), 1)
                    self.assertFalse(legacy.exists())
                    calls.claim.assert_called_once()
                    calls.clone_or_update_repo.assert_called_once()
                    calls.run_script.assert_called_once()
                    self.assertEqual(executor.main(), 0)
                    self.assertEqual([call.args[0] for call in load.call_args_list].count(str(legacy)), 1)
                    calls.run_script.assert_called_once()

    def test_executor_does_not_replay_delivery_when_claim_commit_result_is_uncertain(self):
        keys = self.reserve_then_publish_later_delivery()

        def commit_then_fail(*args):
            deliveries.claim(*args)
            raise sqlite3.OperationalError('claim result unavailable after commit')

        with self.executor_drain() as calls:
            calls.claim.side_effect = commit_then_fail
            self.assertEqual(executor.main(), 1)
            calls.clone_or_update_repo.assert_not_called()
            calls.run_script.assert_not_called()
            calls.claim.reset_mock(side_effect=True)
            # The first delivery is already claimed and must never start a build.
            self.assertEqual(executor.main(), 1)
            self.assertEqual([call.args[2]['delivery_key'] for call in calls.claim.call_args_list], list(keys))
            calls.clone_or_update_repo.assert_called_once()
            self.assertEqual(calls.clone_or_update_repo.call_args.args[3], 'c' * 40)
            calls.run_script.assert_called_once()
        self.assertEqual(list(Path(self.jobs).glob('*.json')), [])

    def test_executor_continues_after_an_earlier_claimed_attempt_fails(self):
        keys = self.reserve_then_publish_later_delivery()
        with self.executor_drain() as calls:
            calls.clone_or_update_repo.side_effect = [False, True]
            self.assertEqual(executor.main(), 1)
            self.assertEqual([call.args[2]['delivery_key'] for call in calls.claim.call_args_list], list(keys))
            calls.run_script.assert_called_once()
            self.assertIn('Commit: ' + 'c' * 40, Path(calls.run_script.call_args.args[2]).read_text())
        self.assertEqual(list(Path(self.jobs).glob('*.json')), [])

    def test_recovery_completes_only_after_publication_directory_is_synced(self):
        keys = self.reserve_then_publish_later_delivery()
        fsync = os.fsync

        def fail_directory_sync(descriptor):
            if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise OSError('directory sync unavailable')
            fsync(descriptor)

        with self.executor_drain() as calls:
            with patch('lib.atomic_io.os.fsync', side_effect=fail_directory_sync):
                self.assertEqual(executor.main(), 1)
                # Rename succeeded, but even existing files must wait for a
                # successful directory sync on the next recovery attempt.
                self.assertEqual(len(list(Path(self.jobs).glob('*.json'))), 2)
                self.assertEqual(executor.main(), 1)
            calls.claim.assert_not_called()
            calls.clone_or_update_repo.assert_not_called()
            calls.run_script.assert_not_called()
            self.assertFalse(any(call.args[1] == 'CI/CD executor finished' for call in calls.log_event.call_args_list))
            self.assertEqual(executor.main(), 0)
            self.assertEqual([call.args[2]['delivery_key'] for call in calls.claim.call_args_list], list(keys))

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
        with patch.object(executor, 'receiver_state', side_effect=nullcontext), patch.object(executor, 'DELIVERIES_FILE', str(self.root / 'missing')), patch.object(executor, 'load_config', return_value={'repositories': []}), patch.object(executor, 'clone_or_update_repo') as clone:
            self.assertFalse(executor.process_job(str(job)))
            clone.assert_not_called()
        self.assertTrue(job.exists())

    def test_same_commit_attempts_keep_separate_logs_and_job_mapping(self):
        config = {'repositories': [{'url': self.payload['repo_url']}]}
        logs = self.root / 'logs'
        with patch.object(executor, 'run_manifest_workflow', return_value=True), patch.object(executor, 'LOGS_DIR', str(logs)), patch.object(executor, 'load_config', return_value=config), patch.object(executor, 'clone_or_update_repo', return_value=True), patch.object(executor, 'load_notification_configs_from_state', return_value=[]):
            for index in range(2):
                job = self.root / f'manual-{index}.json'
                job.write_text(json.dumps(self.payload))
                self.assertTrue(executor.process_job(str(job)))
        files = list(logs.glob('*.log'))
        self.assertEqual(len(files), 2)
        content = '\n'.join(path.read_text() for path in files)
        self.assertIn('Job: manual-0.json', content)
        self.assertIn('Job: manual-1.json', content)
