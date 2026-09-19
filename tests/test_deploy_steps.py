"""Tests for deploy.deploy_steps entry-point defaults."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from deploy.deploy_steps import DEPLOY_GROUP, DEPLOY_USER, deploy_repository, ensure_deploy_user


class TestDeployUserDefaults(unittest.TestCase):
    @patch('deploy.deploy_steps.run')
    def test_ensure_deploy_user_creates_locked_down_owner(self, mock_run):
        mock_run.side_effect = [MagicMock(returncode=1), MagicMock(returncode=0), MagicMock(returncode=0)]

        ensure_deploy_user(DEPLOY_USER)

        self.assertEqual(mock_run.call_args_list[0].args[0], ["id", DEPLOY_USER])
        self.assertEqual(
            mock_run.call_args_list[1].args[0],
            ["mkdir", "-p", "/var/lib/basaltwater"],
        )
        create_cmd = mock_run.call_args_list[2].args[0]
        self.assertEqual(create_cmd[:3], ["useradd", "--system", "--user-group"])
        self.assertIn("--shell", create_cmd)
        self.assertIn("/usr/sbin/nologin", create_cmd)
        self.assertIn(DEPLOY_USER, create_cmd)

    @patch('lib.project_manifest.load_manifest', return_value=None)
    @patch('deploy.deploy_steps.DeploymentOrchestrator')
    @patch('deploy.deploy_steps.ensure_deploy_user')
    def test_deploy_repository_defaults_to_web_deploy_owner(
        self, mock_ensure_user, mock_orchestrator_cls, _load_manifest
    ):
        orchestrator = mock_orchestrator_cls.return_value
        orchestrator.deploy_from_archive.return_value = {'dest_path': '/var/www/example_com'}

        result = deploy_repository(
            source_path='/tmp/source',
            deploy_spec='example.com',
            git_url='https://git.example.com/app.git',
        )

        mock_ensure_user.assert_called_once_with(DEPLOY_USER)
        mock_orchestrator_cls.assert_called_once_with(
            base_dir="/var/www",
            deploy_user=DEPLOY_USER,
            deploy_group=DEPLOY_GROUP,
        )
        self.assertEqual(result, [{'dest_path': '/var/www/example_com'}])


if __name__ == '__main__':
    unittest.main()
