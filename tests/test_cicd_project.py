"""Manifest CI commands preserve the build boundary and explicit overrides."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from lib.cicd_project import prepare_project, run_stage
from lib.project_manifest import WorkflowStep
from lib.cicd_project import run_step
from web.service_tools import cicd_executor


class CicdProjectTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_prepare_infers_missing_and_rejects_invalid_manifest(self):
        self.assertEqual(prepare_project(str(self.root))['status'], 'unrecognized')
        (self.root / 'index.html').write_text('<html></html>')
        self.assertEqual(prepare_project(str(self.root))['status'], 'created')
        self.assertEqual(prepare_project(str(self.root))['status'], 'existing')
        (self.root / 'basaltwater.json').write_text('broken')
        with self.assertRaises(ValueError):
            prepare_project(str(self.root))

    def test_stage_literal_argv_directory_environment_and_failure(self):
        (self.root / 'frontend').mkdir()
        manifest = {'version': 1, 'components': [], 'ci': {'test': [
            {'argv': ['test-tool', 'literal;$(no shell)'], 'directory': 'frontend', 'env': {'EXAMPLE': 'value'}},
            {'argv': ['never-run']},
        ]}}
        (self.root / 'basaltwater.json').write_text(json.dumps(manifest))
        with patch('lib.cicd_project._environment', return_value={'EXAMPLE': 'value'}), patch(
            'lib.cicd_project.subprocess.run', return_value=subprocess.CompletedProcess([], 7)
        ) as run:
            self.assertEqual(run_stage(str(self.root), 'test'), 7)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0], ['test-tool', 'literal;$(no shell)'])
        self.assertEqual(run.call_args.kwargs['cwd'], self.root / 'frontend')
        self.assertEqual(run.call_args.kwargs['env'], {'EXAMPLE': 'value'})

    def test_workflow_directory_cannot_follow_symlink_outside(self):
        (self.root / 'outside').symlink_to(self.root.parent, target_is_directory=True)
        with patch('lib.cicd_project.subprocess.run') as run, self.assertRaises(ValueError):
            run_step(self.root, WorkflowStep(['true'], directory='outside'))
        run.assert_not_called()

    def test_broker_uses_isolated_unprivileged_runner(self):
        log = self.root / 'job.log'
        with patch.object(cicd_executor, 'run_build_command', return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertTrue(cicd_executor.run_manifest_workflow(str(self.root), str(log)))
            self.assertTrue(cicd_executor.run_manifest_workflow(str(self.root), str(log), 'build'))
        command = run.call_args.args[0]
        self.assertEqual(command[:2], ['/usr/bin/python3', '-I'])
        self.assertEqual(command[-2:], ['--stage', 'build'])
        self.assertNotIn('env', run.call_args.kwargs)

    def test_explicit_stage_overrides_manifest_and_preflight_failure_stops_all(self):
        url = 'https://example.com/project.git'
        config = {'repositories': [{'url': url, 'scripts': {'build': 'build.sh'}}]}
        payload = {'repo_url': url, 'ref': 'refs/heads/main', 'commit_sha': 'a' * 40, 'pusher': 'user'}
        with (
            patch.object(cicd_executor, 'LOGS_DIR', str(self.root)),
            patch.object(cicd_executor, 'load_config', return_value=config),
            patch.object(cicd_executor, 'clone_or_update_repo', return_value=True),
            patch.object(cicd_executor, 'get_repo_workspace', return_value=str(self.root)),
            patch.object(cicd_executor, 'load_notification_configs_from_state', return_value=[]),
            patch.object(cicd_executor, 'run_script', return_value=True) as script,
            patch.object(cicd_executor, 'run_manifest_workflow', return_value=True) as workflow,
        ):
            job = self.root / 'job.json'
            job.write_text(json.dumps(payload))
            self.assertTrue(cicd_executor.process_job(str(job)))
            self.assertEqual(script.call_count, 1)
            self.assertEqual(script.call_args.args[0], 'build.sh')
            self.assertEqual([item.args[2:] for item in workflow.call_args_list], [(), ('install',), ('test',)])
            script.reset_mock()
            workflow.reset_mock()
            workflow.return_value = False
            job.write_text(json.dumps(payload))
            self.assertFalse(cicd_executor.process_job(str(job)))
            script.assert_not_called()
            self.assertEqual(workflow.call_count, 1)

    def test_remote_ci_only_manifest_never_pushes_source(self):
        (self.root / 'basaltwater.json').write_text(json.dumps(
            {'version': 1, 'components': [], 'ci': {'test': [{'argv': ['true']}]}}
        ))
        log = self.root / 'job.log'
        with patch('lib.remote_deploy.get_deploy_target', return_value={'base_dir': '/var/www'}), patch(
            'lib.remote_deploy.push_artifact'
        ) as push:
            self.assertFalse(cicd_executor.perform_remote_deployment(
                str(self.root), 'app', 'example.com', 'https://example.com/repo.git', 'a' * 40, str(log), {}
            ))
        push.assert_not_called()

    def test_remote_manifest_uses_declared_output_and_rejects_missing_output(self):
        output = self.root / 'custom-output'
        output.mkdir()
        manifest = {'version': 1, 'components': [
            {'name': 'site', 'type': 'static', 'domain': '{{domain}}', 'output': 'custom-output'}
        ]}
        (self.root / 'basaltwater.json').write_text(json.dumps(manifest))
        log = self.root / 'job.log'
        with (
            patch('lib.remote_deploy.get_deploy_target', return_value={'base_dir': '/var/www'}),
            patch('lib.remote_deploy.push_artifact', return_value=True) as push,
        ):
            self.assertTrue(cicd_executor.perform_remote_deployment(
                str(self.root), 'app', None, 'https://example.com/repo.git', 'a' * 40, str(log), {}
            ))
            self.assertEqual(push.call_args.args[0], str(output))
            push.reset_mock()
            output.rmdir()
            self.assertFalse(cicd_executor.perform_remote_deployment(
                str(self.root), 'app', None, 'https://example.com/repo.git', 'a' * 40, str(log), {}
            ))
            push.assert_not_called()


if __name__ == '__main__':
    unittest.main()
