"""Regression coverage for CI/CD config identity, validation, and secrets."""

from __future__ import annotations

import copy
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from web import cicd_steps
from web.service_tools import cicd_config, cicd_executor, webhook_receiver


class TestCicdConfig(unittest.TestCase):
    def test_rejects_wrong_shapes_and_unsafe_repository_settings(self):
        valid = {'repositories': [{'url': 'https://example.com/repo.git'}]}
        invalid = [[], {}, {'repositories': {}}, {'version': True, 'repositories': []}]
        for field, value in (
            ('url', 'file:///etc'), ('url', 'https://bad host/repo'),
            ('url', 'https://token@example.com/repo'), ('branches', 'main'),
            ('branches', [42]), ('branches', ['main..other']),
            ('scripts', []), ('scripts', {'build': '../outside'}),
            ('scripts', {'build': '/bin/sh'}), ('deploy_target', []),
        ):
            config = copy.deepcopy(valid)
            config['repositories'][0][field] = value
            invalid.append(config)
        for config in invalid:
            with self.subTest(config=config), self.assertRaises(ValueError):
                cicd_config.validate_config(config)
        self.assertEqual(cicd_config.validate_config(valid), valid)

    def test_config_is_readable_and_owned_before_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'config.json')
            config = {'repositories': []}
            real_replace = os.replace
            def replace(source, target):
                self.assertEqual(os.stat(source).st_mode & 0o777, 0o640)
                chown.assert_called_once()
                self.assertEqual(chown.call_args.args[1:], (0, 1234))
                real_replace(source, target)
            with patch.object(cicd_config.grp, 'getgrnam', return_value=SimpleNamespace(gr_gid=1234)), patch('lib.atomic_io.os.fchown') as chown, patch('lib.atomic_io.os.replace', side_effect=replace):
                cicd_config.save_config_file(path, config)
            self.assertEqual(cicd_config.load_config_file(path), config)

    def test_invalid_config_keeps_job_and_fails_health(self):
        with tempfile.TemporaryDirectory() as directory:
            config = os.path.join(directory, 'config.json')
            Path(config).write_text('[]')
            job = os.path.join(directory, 'job.json')
            Path(job).write_text('{}')
            with patch.object(cicd_executor, 'CONFIG_FILE', config):
                self.assertFalse(cicd_executor.process_job(job))
            self.assertTrue(os.path.exists(job))
            handler = object.__new__(webhook_receiver.WebhookHandler)
            handler.path = '/health'
            handler.send_error = Mock()
            with patch.object(webhook_receiver, 'CONFIG_FILE', config):
                handler.do_GET()
            self.assertEqual(handler.send_error.call_args.args[0], 503)

    def test_unsafe_config_file_keeps_job_and_fails_health(self):
        with tempfile.TemporaryDirectory() as directory:
            config = os.path.join(directory, 'config.json')
            job = os.path.join(directory, 'job.json')
            Path(job).write_text('{}')
            os.mkfifo(config)
            handler = object.__new__(webhook_receiver.WebhookHandler)
            handler.path = '/health'
            handler.send_error = Mock()
            with patch.object(cicd_executor, 'CONFIG_FILE', config), patch.object(webhook_receiver, 'CONFIG_FILE', config):
                self.assertFalse(cicd_executor.process_job(job))
                handler.do_GET()
            self.assertTrue(os.path.exists(job))
            self.assertEqual(handler.send_error.call_args.args[0], 503)

    def test_existing_secret_reconciles_environment_and_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            secret = Path(directory) / 'secret'
            env = Path(directory) / 'env'
            secret.write_text('canonical-secret\n')
            env.write_text('WEBHOOK_SECRET=wrong\n')
            with patch.object(cicd_steps, 'SECRET_FILE', str(secret)), patch.object(cicd_steps, 'ENV_FILE', str(env)), patch('lib.atomic_io.os.fchown'):
                self.assertEqual(cicd_steps.generate_webhook_secret(Mock()), 'canonical-secret')
                self.assertEqual(env.read_text(), 'WEBHOOK_SECRET=canonical-secret\nWEBHOOK_PORT=8765\n')
                self.assertEqual(secret.stat().st_mode & 0o777, 0o600)
                self.assertEqual(env.stat().st_mode & 0o777, 0o600)
                for value in ('', 'first\nSECOND=value', 'x' * 514):
                    secret.write_text(value)
                    with self.assertRaises(ValueError):
                        cicd_steps.generate_webhook_secret(Mock())

    def test_escaped_script_never_executes(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / 'workspace'
            workspace.mkdir()
            outside = Path(directory) / 'outside.sh'
            outside.write_text('echo outside')
            (workspace / 'build.sh').symlink_to(outside)
            with patch.object(cicd_executor.subprocess, 'run') as run:
                self.assertFalse(cicd_executor.run_script('build.sh', str(workspace), str(Path(directory) / 'log')))
                run.assert_not_called()
