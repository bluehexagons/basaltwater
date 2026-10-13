"""Project tooling must reject CI-only deploys before host mutations."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from lib.config import SetupConfig
from lib.deployment import DeploymentOrchestrator
from lib.project_manifest import Component, Manifest, WorkflowStep
from lib.setup_common import prepare_deployments


class BuildUserHomeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name) / 'state'
        self.build_root = self.state / 'build-users'
        self.home = self.build_root / 'build-example'
        self.home.mkdir(parents=True)
        self.state.chmod(0o700)
        self.home.chmod(0o700)
        self.orchestrator = DeploymentOrchestrator()
        home = patch.object(self.orchestrator, '_build_home', return_value=str(self.home))
        home.start()
        self.addCleanup(home.stop)
        executable = patch('lib.deployment.shutil.which', return_value='/usr/bin/setfacl')
        executable.start()
        self.addCleanup(executable.stop)

    @patch('lib.deployment.run')
    def test_existing_account_restores_traversal_and_preserves_private_files(self, run):
        run.return_value = subprocess.CompletedProcess([], 0)
        key = self.home / 'deploy-key'
        key.write_text('private fixture', encoding='utf-8')
        key.chmod(0o600)
        self.orchestrator._ensure_build_user('build-example')

        commands = [invocation.args[0] for invocation in run.call_args_list]
        self.assertFalse(any(isinstance(command, str) and command.startswith('useradd ') for command in commands))
        self.assertIn(['setfacl', '-m', 'u:build-example:--x', '--', str(self.state)], commands)
        self.assertIn(['chown', 'root:root', '--', str(self.build_root)], commands)
        self.assertIn(['chmod', '0711', '--', str(self.build_root)], commands)
        self.assertIn(['chown', 'build-example:build-example', '--', str(self.home)], commands)
        self.assertIn(['chmod', '0700', '--', str(self.home)], commands)
        for permission in ('-x', '-w'):
            self.assertIn(
                ['runuser', '-u', 'build-example', '--', '/usr/bin/test', permission, str(self.home)],
                commands,
            )
        self.assertFalse(any(
            isinstance(command, list) and command[0] in {'chmod', 'chown'}
            and ('-R' in command or str(self.state) in command)
            for command in commands
        ))
        self.assertEqual(os.stat(self.state).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(key).st_mode & 0o777, 0o600)
        self.assertEqual(key.read_text(encoding='utf-8'), 'private fixture')

    @patch('lib.deployment.run')
    def test_new_account_gets_home_access_before_toolchain_setup(self, run):
        def result(command, **_kwargs):
            return subprocess.CompletedProcess(command, 1 if isinstance(command, str) and command.startswith('id ') else 0)

        run.side_effect = result
        self.orchestrator._ensure_build_user('build-example')
        commands = [invocation.args[0] for invocation in run.call_args_list]
        creation = [command for command in commands if isinstance(command, str) and command.startswith('useradd ')]
        self.assertEqual(len(creation), 1)
        self.assertIn(f'--home-dir {self.home}', creation[0])
        self.assertIn('--shell /usr/sbin/nologin', creation[0])
        grant = next(index for index, command in enumerate(commands) if isinstance(command, list) and command[0] == 'setfacl')
        probe = next(index for index, command in enumerate(commands) if isinstance(command, list) and command[0] == 'runuser')
        self.assertLess(grant, probe)

    @patch('lib.deployment.run', return_value=subprocess.CompletedProcess([], 0))
    def test_missing_acl_tools_are_installed_before_granting_access(self, run):
        with patch('lib.deployment.shutil.which', return_value=None):
            self.orchestrator._ensure_build_user('build-example')
        commands = [invocation.args[0] for invocation in run.call_args_list]
        package = next(index for index, command in enumerate(commands) if isinstance(command, list) and command[0] == 'apt-get')
        grant = next(index for index, command in enumerate(commands) if isinstance(command, list) and command[0] == 'setfacl')
        self.assertLess(package, grant)
        self.assertEqual(commands[package][-1], 'acl')

    @patch('lib.deployment.run')
    def test_inaccessible_home_fails_with_account_and_path(self, run):
        def result(command, **_kwargs):
            return subprocess.CompletedProcess(command, 1 if isinstance(command, list) and command[0] == 'runuser' else 0, '', 'denied')

        run.side_effect = result
        with self.assertRaisesRegex(RuntimeError, f'Build user build-example cannot access its home {self.home}'):
            self.orchestrator._ensure_build_user('build-example')

    @patch('lib.deployment.run')
    def test_invalid_username_is_rejected_before_mutations(self, run):
        with self.assertRaisesRegex(ValueError, 'Invalid build username'):
            self.orchestrator._ensure_build_user('../other')
        run.assert_not_called()

    @patch('lib.deployment.run')
    def test_symlinked_home_or_parent_is_rejected_before_mutations(self, run):
        for directory in (self.home, self.build_root, self.state):
            with self.subTest(directory=directory):
                relocated = directory.with_name(directory.name + '-original')
                directory.rename(relocated)
                directory.symlink_to(relocated, target_is_directory=True)
                try:
                    with self.assertRaisesRegex(ValueError, 'cannot contain a symlink'):
                        self.orchestrator._ensure_build_user('build-example')
                    run.assert_not_called()
                finally:
                    directory.unlink()
                    relocated.rename(directory)


class DeploymentProjectToolingTests(unittest.TestCase):
    def test_publishing_metadata_survives_port_resolution_and_cannot_deploy_alone(self):
        with tempfile.TemporaryDirectory() as directory:
            metadata = {"languages": {"source": "en", "supported": ["en", "es"]}}
            manifest = Manifest(1, [], publishing=metadata)
            orchestrator = DeploymentOrchestrator(base_dir=directory)
            with patch.object(orchestrator, "_get_used_ports", return_value=set()):
                self.assertEqual(orchestrator._resolve_manifest_ports(manifest, directory).publishing, metadata)
            with patch("lib.deployment.run") as run, self.assertRaisesRegex(ValueError, "metadata-only"):
                orchestrator.deploy_manifest(manifest, directory, "example.test", "/", "https://example.test/game.git", None)
            run.assert_not_called()

    def test_ci_only_manifest_cannot_replace_a_release(self):
        manifest = Manifest(1, [], {'test': [WorkflowStep(['true'])]})
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / 'deployments'
            orchestrator = DeploymentOrchestrator(base_dir=str(base))
            with patch('lib.deployment.run') as run, self.assertRaisesRegex(ValueError, 'CI workflows only'):
                orchestrator.deploy_manifest(manifest, directory, 'example.com', '/', 'https://example.com/site.git', None)
            run.assert_not_called()
            self.assertFalse(base.exists())

    def test_controller_rejects_ci_only_manifest_before_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'basaltwater.json'
            path.write_text(json.dumps({'version': 1, 'components': [], 'ci': {'test': [{'argv': ['true']}]}}))
            config = SetupConfig('server_web', 'example.com', 'deploy',
                                 deploy_specs=[['example.com', 'https://example.com/site.git']])
            with patch('lib.setup_common.clone_repository', return_value=(directory, None)), self.assertRaisesRegex(RuntimeError, 'CI workflows only'):
                prepare_deployments(config, directory)

    def test_direct_component_build_activates_root_pin(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.nvmrc').write_text('26.10.0')
            home = root / 'build-home'
            (home / '.nvm').mkdir(parents=True)
            (home / '.nvm/nvm.sh').write_text('# managed NVM')
            component = Component('site', 'static', 'example.com', build=['npm run build'], output='dist')
            with patch.object(DeploymentOrchestrator, '_build_home', return_value=str(home)), patch(
                'lib.deployment.run', return_value=subprocess.CompletedProcess([], 0)
            ) as run:
                DeploymentOrchestrator()._run_component_build(component, directory, 'build-example')
            command = run.call_args.args[0]
            self.assertIn('nvm use', command)
            self.assertLess(command.index('nvm use'), command.index('npm run build'))

    def test_direct_toolchain_provisions_nested_frontend_pin(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frontend = root / 'frontend'
            frontend.mkdir()
            (frontend / 'package.json').write_text('{}')
            (frontend / '.nvmrc').write_text('22')
            home = root / 'build-home'
            (home / '.nvm').mkdir(parents=True)
            (home / '.nvm/nvm.sh').write_text('# managed NVM')
            with patch.object(DeploymentOrchestrator, '_build_home', return_value=str(home)), patch(
                'lib.deployment.run', return_value=subprocess.CompletedProcess([], 0)
            ) as run:
                DeploymentOrchestrator()._prepare_build_toolchain(directory, 'build-example')
            self.assertEqual(run.call_count, 1)
            self.assertIn(str(frontend), run.call_args.args[0])
            self.assertIn('nvm install', run.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
