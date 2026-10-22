"""Project tooling must reject CI-only deploys before host mutations."""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import remote_setup
from lib.config import SetupConfig
from lib.deployment import DeploymentOrchestrator
from lib.project_manifest import Component, Manifest, WorkflowStep, parse_manifest
from lib.setup_common import prepare_deployments
from lib.node_toolchain import add_node_subparser, run_node_command


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
    @staticmethod
    def run_shared_node_cli(command, **_options):
        """Exercise the actual Node CLI behind the mocked build-user boundary."""
        arguments = shlex.split(command)
        parser = argparse.ArgumentParser()
        add_node_subparser(parser.add_subparsers(dest='command'))
        args = parser.parse_args(arguments[arguments.index('node'):])
        environment = dict(argument.split('=', 1) for argument in arguments if argument.startswith(('HOME=', 'NVM_DIR=')))
        output, error = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, environment), redirect_stdout(output), redirect_stderr(error):
            code = run_node_command(args)
        return subprocess.CompletedProcess(command, code, output.getvalue(), error.getvalue())

    def test_unsupported_direct_and_nested_pins_precede_tool_installation(self):
        for nested in (False, True):
            for pin in ('22', '27', '24.22.0', '26.12.0'):
                with self.subTest(nested=nested, pin=pin), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    (root / '.git').mkdir()
                    (root / 'package.json').write_text('{}')
                    (root / '.node-version').write_text('24.22.1' if nested else pin)
                    (root / '.nvmrc').write_text('24')
                    if nested:
                        frontend = root / 'frontend'
                        frontend.mkdir()
                        (frontend / 'package.json').write_text('{}')
                        (frontend / '.node-version').write_text(pin)
                        (frontend / '.nvmrc').write_text('24')
                    home = root / 'build-home'
                    home.mkdir()
                    orchestrator = DeploymentOrchestrator()
                    with patch.object(orchestrator, '_build_home', return_value=str(home)), \
                            patch('lib.deployment.run', side_effect=self.run_shared_node_cli) as run, \
                            patch('common.common_steps.install_node_for_user') as install, \
                            patch('lib.node_toolchain.fetch_node_releases') as fetch, \
                            patch('lib.node_toolchain.subprocess.run') as mutation, \
                            self.assertRaisesRegex(RuntimeError, 'Node.js version resolution failed'):
                        orchestrator._prepare_build_toolchain(directory, 'build-example', ['node'])
                    install.assert_not_called()
                    fetch.assert_not_called()
                    mutation.assert_not_called()
                    self.assertTrue(all('node resolve' in call.args[0] for call in run.call_args_list))
                    self.assertTrue(all('runuser -u build-example -- env ' in call.args[0] for call in run.call_args_list))

    def test_direct_build_cannot_execute_project_code_with_an_unsupported_pin(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.git').mkdir()
            (root / '.node-version').write_text('27')
            home = root / 'home'
            home.mkdir()
            component = Component('site', 'static', 'example.com', build=['touch forbidden'], output='dist')

            def execute_cli(command, **_options):
                # The build shell contains a literal shared Node exec command.
                outer = shlex.split(command)
                shell = outer[-1]
                start = shell.index('/usr/bin/python3 -I ')
                return self.run_shared_node_cli(shell[start:])

            orchestrator = DeploymentOrchestrator()
            with patch.object(orchestrator, '_build_home', return_value=str(home)), \
                    patch.dict(os.environ, {'NVM_DIR': str(home / '.nvm')}), \
                    patch('lib.deployment.run', side_effect=execute_cli), \
                    patch('lib.node_toolchain.shutil.which', return_value=None), \
                    patch('lib.node_toolchain.subprocess.run') as execute, \
                    self.assertRaisesRegex(RuntimeError, "Component 'site' build failed"):
                orchestrator._run_component_build(component, directory, 'build-example', ['node'])
            execute.assert_not_called()
            self.assertFalse((root / 'forbidden').exists())

    def test_nested_nvm_use_routes_through_policy_and_node_version_precedence(self):
        for nested_pin in ('24.22.1', '27'):
            with self.subTest(nested_pin=nested_pin), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / '.git').mkdir()
                (root / '.nvmrc').write_text('26.12.1')
                frontend = root / 'frontend'
                frontend.mkdir()
                (frontend / '.node-version').write_text(nested_pin)
                (frontend / '.nvmrc').write_text('27')
                home = root / 'build-home'
                for version in ('24.22.1', '26.12.1'):
                    binary = home / '.nvm' / 'versions' / 'node' / ('v' + version) / 'bin' / 'node'
                    binary.parent.mkdir(parents=True)
                    binary.write_text('#!/bin/sh\nprintf "v' + version + '\\n"\n')
                    binary.chmod(0o755)
                alias = home / '.nvm' / 'alias' / 'default'
                alias.parent.mkdir()
                alias.write_text('26.12.1')
                # Invoke the actual selector, without loading the unrelated full
                # application CLI or requiring its system Python dependencies.
                cli = root / 'node-cli.py'
                cli.write_text(
                    'import argparse, sys\n'
                    f'sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n'
                    'from lib.node_toolchain import add_node_subparser, run_node_command\n'
                    'parser = argparse.ArgumentParser()\n'
                    'add_node_subparser(parser.add_subparsers(dest="command"))\n'
                    'sys.exit(run_node_command(parser.parse_args()))\n'
                )
                component = Component('site', 'static', 'example.com', build=[
                    'node --version && cd frontend && '
                    'if command -v nvm >/dev/null 2>&1; then nvm use; fi && '
                    'node --version && touch built',
                ], output='dist')
                results = []

                def execute_payload(command, **_options):
                    arguments = shlex.split(command)
                    self.assertEqual(arguments[:4], ['runuser', '-u', 'build-example', '--'])
                    result = subprocess.run(arguments[4:], cwd=home, capture_output=True,
                                            text=True, check=False, timeout=10)
                    results.append(result)
                    return result

                orchestrator = DeploymentOrchestrator()
                with patch.object(orchestrator, '_build_home', return_value=str(home)), \
                        patch.object(orchestrator, '_node_cli', return_value=[sys.executable, '-I', str(cli), 'node']), \
                        patch('lib.deployment.run', side_effect=execute_payload):
                    if nested_pin == '27':
                        with self.assertRaisesRegex(RuntimeError, "Component 'site' build failed"):
                            orchestrator._run_component_build(component, directory, 'build-example', ['node'])
                    else:
                        orchestrator._run_component_build(component, directory, 'build-example', ['node'])
                self.assertEqual(results[0].stdout.splitlines(),
                                 ['v26.12.1', 'v24.22.1'] if nested_pin != '27' else ['v26.12.1'])
                self.assertEqual((frontend / 'built').exists(), nested_pin != '27')

    def test_explicit_go_build_skips_tools_and_existing_nvm_activation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('package.json', 'pyproject.toml', '.nvmrc'):
                (root / name).write_text('development-only tooling', encoding='utf-8')
            home = root / 'build home'
            (home / '.nvm').mkdir(parents=True)
            (home / '.nvm/nvm.sh').write_text('echo unexpected-node >&2; exit 99\n', encoding='utf-8')
            # A compound build must retain settings such as GOFLAGS/GOGC after
            # its initial mkdir, and values must remain literal shell data.
            component = Component('api', 'service', 'example.com',
                                  build=['true && printf "%s\\n" "$BUILD_NOTE"'],
                                  env={'BUILD_NOTE': 'go-only $(false)'}, binary='app', port=8080)

            def execute_payload(command, **_kwargs):
                arguments = shlex.split(command)
                result = subprocess.run(arguments[4:], capture_output=True, text=True,
                                        check=False, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, 'go-only $(false)\n')
                return result

            orchestrator = DeploymentOrchestrator()
            with patch.object(orchestrator, '_build_home', return_value=str(home)), patch(
                'common.common_steps.install_node_for_user'
            ) as node, patch('common.common_steps.install_or_update_uv') as uv, patch(
                'lib.deployment.run', side_effect=execute_payload,
            ) as run:
                for runtimes in (['go'], []):
                    orchestrator._prepare_build_toolchain(directory, 'build-example', runtimes)
                    orchestrator._run_component_build(component, directory, 'build-example', runtimes)
                node.assert_not_called()
                uv.assert_not_called()
                self.assertEqual(run.call_count, 2)

    def test_explicit_tools_are_provisioned_without_source_markers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / 'build home'
            (home / '.nvm').mkdir(parents=True)
            (home / '.nvm/nvm.sh').write_text('# managed NVM', encoding='utf-8')
            (root / '.nvmrc').write_text('24', encoding='utf-8')
            orchestrator = DeploymentOrchestrator()
            with patch.object(orchestrator, '_build_home', return_value=str(home)), patch(
                'lib.deployment.run', return_value=subprocess.CompletedProcess([], 0, 'v24.22.1', ''),
            ) as run, patch('common.common_steps.install_or_update_uv', return_value=True) as uv:
                orchestrator._prepare_build_toolchain(directory, 'build-example', ['node', 'python'])
            self.assertEqual(run.call_count, 2)
            self.assertIn('node install', run.call_args.args[0])
            self.assertIn('--version v24.22.1', run.call_args.args[0])
            uv.assert_called_once_with(str(home), username='build-example')

    def test_build_and_toolchain_shells_discard_the_callers_environment(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as caller_home:
            root = Path(directory)
            frontend = root / 'frontend'
            frontend.mkdir()
            (frontend / 'package.json').write_text('{}', encoding='utf-8')
            (frontend / '.nvmrc').write_text('24', encoding='utf-8')
            home = root / 'build home'
            (home / '.nvm').mkdir(parents=True)
            (home / '.nvm/nvm.sh').write_text('echo shell-loading-bypass >&2; exit 99\n', encoding='utf-8')
            hook = Path(caller_home) / 'shell-env.sh'
            hook.write_text("printf 'foreign shell hook was loaded\\n' >&2\n", encoding='utf-8')
            hook.chmod(0o000)
            environment = {
                'HOME': caller_home, 'PATH': '/usr/bin:/bin', 'BASH_ENV': str(hook),
                'ENV': str(hook), 'NVM_DIR': caller_home + '/.nvm',
                'NVM_BIN': caller_home + '/.nvm/bin', 'NVM_INC': caller_home + '/.nvm/include',
            }

            def execute_payload(command, **_kwargs):
                arguments = shlex.split(command)
                self.assertEqual(arguments[:4], ['runuser', '-u', 'build-example', '--'])
                result = subprocess.run(
                    arguments[4:], env=environment, cwd=home, capture_output=True,
                    text=True, check=False, timeout=10,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, '')
                return result

            checker = (
                'import os, sys, subprocess; '
                'assert os.environ["NVM_DIR"] == os.environ["HOME"] + "/.nvm"; '
                'assert not any(os.environ.get(key) for key in ("NVM_BIN", "NVM_INC", "BASH_ENV", "ENV")); '
                'mode = sys.argv[1]; '
                'print("v24.22.1") if mode == "resolve" else '
                'sys.exit(subprocess.run(sys.argv[sys.argv.index("--") + 1:]).returncode) if mode == "exec" else None'
            )
            component = Component('site', 'static', 'example.com',
                                  build=['printf "own build environment\\n"'], output='dist')
            orchestrator = DeploymentOrchestrator()
            with patch.object(orchestrator, '_build_home', return_value=str(home)), patch(
                'lib.deployment.run', side_effect=execute_payload,
            ) as run, patch.object(orchestrator, '_node_cli', return_value=[sys.executable, '-I', '-c', checker]):
                orchestrator._run_component_build(component, directory, 'build-example')
                orchestrator._prepare_build_toolchain(directory, 'build-example')
            self.assertEqual(run.call_count, 5)
            self.assertEqual(hook.stat().st_mode & 0o777, 0o000)

    def test_publishing_metadata_survives_port_resolution_and_cannot_deploy_alone(self):
        with tempfile.TemporaryDirectory() as directory:
            metadata = {"languages": {"source": "en", "supported": ["en", "es"]}}
            manifest = Manifest(1, [], publishing=metadata, build_runtimes=['go'])
            orchestrator = DeploymentOrchestrator(base_dir=directory)
            with patch.object(orchestrator, "_get_used_ports", return_value=set()):
                resolved = orchestrator._resolve_manifest_ports(manifest, directory)
                self.assertEqual(resolved.publishing, metadata)
                self.assertEqual(resolved.build_runtimes, ['go'])
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
            (root / '.nvmrc').write_text('26.12.1')
            home = root / 'build-home'
            (home / '.nvm').mkdir(parents=True)
            (home / '.nvm/nvm.sh').write_text('# managed NVM')
            component = Component('site', 'static', 'example.com', build=['npm run build'], output='dist')
            with patch.object(DeploymentOrchestrator, '_build_home', return_value=str(home)), patch(
                'lib.deployment.run', return_value=subprocess.CompletedProcess([], 0)
            ) as run:
                DeploymentOrchestrator()._run_component_build(component, directory, 'build-example')
            command = run.call_args.args[0]
            self.assertIn('node exec --project', command)
            self.assertLess(command.index('node exec'), command.index('npm run build'))
            self.assertIn('node env', command)

    def test_direct_toolchain_provisions_nested_frontend_pin(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frontend = root / 'frontend'
            frontend.mkdir()
            (frontend / 'package.json').write_text('{}')
            (frontend / '.nvmrc').write_text('24')
            home = root / 'build-home'
            (home / '.nvm').mkdir(parents=True)
            (home / '.nvm/nvm.sh').write_text('# managed NVM')
            with patch.object(DeploymentOrchestrator, '_build_home', return_value=str(home)), patch(
                'lib.deployment.run', return_value=subprocess.CompletedProcess([], 0, 'v24.22.1', '')
            ) as run:
                DeploymentOrchestrator()._prepare_build_toolchain(directory, 'build-example')
            self.assertEqual(run.call_count, 4)
            self.assertIn(str(frontend), run.call_args.args[0])
            self.assertIn('node install', run.call_args.args[0])


class DeclaredBuildRuntimeTests(unittest.TestCase):
    def test_target_selection_overrides_markers_and_combines_repositories_and_flags(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'go.mod').write_text('module example.com/app\n\ngo 1.27.3\n', encoding='utf-8')
            (root / 'package.json').write_text('{}', encoding='utf-8')
            (root / 'pyproject.toml').write_text('', encoding='utf-8')
            frontend = root / 'frontend'
            frontend.mkdir()
            (frontend / 'package.json').write_text('{}', encoding='utf-8')
            base = {'version': 1, 'components': [{'name': 'api', 'type': 'service',
                    'domain': 'example.com', 'binary': 'app', 'port': 'auto'}]}
            cases = ((None, (True, True, True)), (['go'], (True, False, False)),
                     ([], (False, False, False)), (['node'], (False, True, False)))
            for selection, expected in cases:
                with self.subTest(selection=selection), patch.dict(os.environ):
                    data = dict(base)
                    if selection is not None:
                        data['build_runtimes'] = selection
                    (root / 'basaltwater.json').write_text(json.dumps(data), encoding='utf-8')
                    config = SetupConfig('server_lite', 'localhost', 'deploy',
                                         deploy_specs=[['example.com', 'https://example.com/app.git']])
                    with patch.object(remote_setup, 'repository_stage_name', return_value=directory):
                        remote_setup.enable_detected_build_runtimes(config)
                    self.assertEqual((config.install_go, config.install_node, config.install_python), expected)
                    if selection == ['go']:
                        self.assertEqual(os.environ['BASALTWATER_GO_VERSION'], '1.27.3')

            (root / 'basaltwater.json').write_text(json.dumps({**base, 'build_runtimes': ['go']}))
            (frontend / 'basaltwater.json').write_text(json.dumps({**base, 'build_runtimes': ['node']}))
            config = SetupConfig('server_lite', 'localhost', 'deploy', install_python=True,
                                 deploy_specs=[['api.example.com', 'https://example.com/api.git'],
                                               ['web.example.com', 'https://example.com/web.git']])
            with patch.dict(os.environ), patch.object(remote_setup, 'repository_stage_name',
                                                     side_effect=[directory, str(frontend)]):
                remote_setup.enable_detected_build_runtimes(config)
            self.assertTrue(config.install_go)
            self.assertTrue(config.install_node)
            self.assertTrue(config.install_python)

    def test_target_installs_declared_runtime_without_a_marker(self):
        manifest = parse_manifest({'version': 1, 'build_runtimes': ['go'], 'components': [
            {'name': 'api', 'type': 'service', 'domain': 'example.com', 'binary': 'app', 'port': 'auto'}]})
        config = SetupConfig('server_lite', 'localhost', 'deploy',
                             deploy_specs=[['example.com', 'https://example.com/app.git']])
        with patch.object(remote_setup, 'load_manifest', return_value=manifest), patch.object(
            remote_setup, '_find_project_runtime_files', return_value={'go': [], 'node': [], 'python': []},
        ):
            remote_setup.enable_detected_build_runtimes(config)
        self.assertTrue(config.install_go)


if __name__ == '__main__':
    unittest.main()
