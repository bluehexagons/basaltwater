"""Regression tests for preflight failures in remote CI/CD deployments."""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from web.service_tools import cicd_executor


class TestDeploymentPreflight(unittest.TestCase):
    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch('lib.remote_deploy.get_deploy_target', return_value={
            'host': 'app.example', 'base_dir': '/var/www',
        }))
        self.push = self.stack.enter_context(patch('lib.remote_deploy.push_artifact', return_value=True))
        self.nginx = self.stack.enter_context(patch('lib.remote_deploy.push_nginx_config', return_value=True))
        self.run = self.stack.enter_context(patch.object(cicd_executor, 'run_command', return_value=
            subprocess.CompletedProcess([], 0, '', '')))
        self.stack.enter_context(patch.object(cicd_executor, 'log_event'))

    def deploy(self, spec=None, scripts=None):
        return cicd_executor.perform_remote_deployment(
            self.root, 'app.example', spec, 'https://example/repo.git',
            'a' * 40, self.root + '/build.log', {'scripts': scripts or {}},
        )

    def test_base_directory_aliases_fail_before_transfer(self) -> None:
        for spec in ('/.', '/..'):
            with self.subTest(spec=spec):
                self.assertFalse(self.deploy(spec))
        self.push.assert_not_called()
        self.nginx.assert_not_called()
        self.run.assert_not_called()

    def test_missing_or_directory_script_fails_before_transfer(self) -> None:
        for script in ('missing.sh', self.root):
            with self.subTest(script=script):
                self.assertFalse(self.deploy(scripts={'deploy': script}))
        self.push.assert_not_called()
        self.nginx.assert_not_called()
        self.run.assert_not_called()

    def test_script_content_is_retained_before_transfer(self) -> None:
        script = Path(self.root, 'deploy.sh')
        script.write_text('echo deployed\n')
        def transfer(*args):
            script.unlink()
            return True
        self.push.side_effect = transfer
        self.assertTrue(self.deploy(scripts={'deploy': 'deploy.sh'}))
        self.assertEqual(self.run.call_args.kwargs['input'], 'echo deployed\n')

    def test_script_failure_propagates(self) -> None:
        Path(self.root, 'deploy.sh').write_text('exit 1\n')
        self.run.return_value = subprocess.CompletedProcess([], 1, '', 'failed')
        self.assertFalse(self.deploy(scripts={'deploy': 'deploy.sh'}))

    def test_oversized_script_fails_before_transfer(self) -> None:
        Path(self.root, 'deploy.sh').write_text('x' * (1024 * 1024 + 1))
        self.assertFalse(self.deploy(scripts={'deploy': 'deploy.sh'}))
        self.push.assert_not_called()
        self.run.assert_not_called()

    def test_script_may_be_omitted(self) -> None:
        self.assertTrue(self.deploy())
        self.push.assert_called_once()
        self.run.assert_not_called()
