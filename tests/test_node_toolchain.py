"""Project runtime selection across installed Node majors and stable ranges."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from lib.node_toolchain import inspect_project_node, run_node_command, select_node, satisfies
from lib.node_runtime_ownership import runtime_owner


class NodeToolchainTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        (self.project / '.git').mkdir()
        self.nvm = self.root / 'nvm'
        for version in ('20.20.2', '22.23.2', '24.21.0', '26.10.0'):
            binary = self.nvm / 'versions/node' / ('v' + version) / 'bin/node'
            binary.parent.mkdir(parents=True)
            binary.write_text('fake node')
            binary.chmod(0o755)
        (self.nvm / 'alias').mkdir()
        (self.nvm / 'alias/default').write_text('24')

    def select(self, **kwargs):
        return select_node(str(self.project), nvm_dir=str(self.nvm), **kwargs)

    def test_pin_beats_newer_installed_runtime(self):
        (self.project / '.nvmrc').write_text('22')
        selection = self.select()
        self.assertEqual(selection.version, '22.23.2')
        self.assertEqual(selection.runtime_source, 'nvm')
        self.assertTrue(selection.source.endswith('.nvmrc'))
        (self.project / '.node-version').write_text('20.20.2')
        self.assertEqual(self.select().version, '20.20.2')
        self.assertEqual(self.select(version='26.10.0').version, '26.10.0')

    def test_nested_engines_and_nearest_pin(self):
        (self.project / '.nvmrc').write_text('20')
        child = self.project / 'frontend'
        child.mkdir()
        (child / '.node-version').write_text('22')
        (child / 'package.json').write_text(json.dumps({'engines': {'node': '^20.19.0 || >=22.12.0'}}))
        selection = select_node(str(child), nvm_dir=str(self.nvm))
        self.assertEqual(selection.version, '22.23.2')

    def test_pin_must_also_satisfy_engines(self):
        (self.project / '.node-version').write_text('20')
        (self.project / 'package.json').write_text(json.dumps({'engines': {'node': '>=24'}}))
        with patch('lib.node_toolchain.shutil.which', return_value=None):
            with self.assertRaisesRegex(ValueError, 'No installed Node matches'):
                self.select()

    def test_default_and_compatible_system_node(self):
        self.assertEqual(self.select().version, '24.21.0')
        (self.nvm / 'alias/default').unlink()
        with patch('lib.node_toolchain.shutil.which', return_value='/usr/bin/node'), patch(
            'lib.node_toolchain.subprocess.run', return_value=subprocess.CompletedProcess([], 0, 'v22.23.2\n', '')
        ):
            self.assertEqual(self.select().version, '22.23.2')

    def test_path_does_not_leak_tools_from_other_node_versions(self):
        selected = self.select(version='26')
        old = str(self.nvm / 'versions/node/v24.21.0/bin')
        environment = selected.environment({'PATH': old + os.pathsep + '/usr/bin', 'VALUE': 'kept'})
        self.assertNotIn(old, environment['PATH'])
        self.assertTrue(environment['PATH'].startswith(str(Path(selected.executable).parent)))
        self.assertEqual(environment['VALUE'], 'kept')

    def test_missing_nvm_pin_reports_compatible_path_origin_and_pin_separately(self):
        (self.project / '.nvmrc').write_text('27')
        (self.project / 'package.json').write_text(json.dumps({'engines': {'node': '>=27'}}))
        with patch.dict(os.environ, {'NVM_DIR': str(self.nvm)}), \
                patch('lib.node_toolchain.shutil.which', return_value='/usr/bin/node'), \
                patch('lib.node_toolchain.subprocess.run',
                      return_value=subprocess.CompletedProcess([], 0, 'v27.1.0\n')):
            selection = self.select()
            self.assertEqual(selection.runtime_source, 'path')
            self.assertEqual(selection.source, str(self.project / '.nvmrc'))
            for json_output in (False, True):
                with self.subTest(json=json_output), patch('sys.stdout', new_callable=io.StringIO) as output:
                    args = argparse.Namespace(node_command='status', project=str(self.project),
                                              version=None, json=json_output)
                    self.assertEqual(run_node_command(args), 0)
                    if json_output:
                        report = json.loads(output.getvalue())
                        self.assertEqual(report['runtime_source'], 'path')
                        self.assertEqual(report['source'], str(self.project / '.nvmrc'))
                    else:
                        self.assertIn('Runtime source: PATH', output.getvalue())
                        self.assertIn('Selected by ' + str(self.project / '.nvmrc'), output.getvalue())

    def test_stable_range_cases(self):
        cases = (
            ('22.23.2', '^20.19.0 || >=22.12.0', True), ('21.1.0', '^20.19.0 || >=22.12.0', False),
            ('26.10.0', '>=26.4.0 <27', True), ('27.0.0', '>=26.4.0 <27', False),
            ('22.23.2', '22.x', True), ('24.0.0', '22', False),
            ('22.23.2', '~22.23', True), ('22.24.0', '~22.23.0', False),
            ('22.23.2', '20 - 22', True), ('23.0.0', '20 - 22', False),
            ('0.2.3', '^0.2.1', True), ('0.3.0', '^0.2.1', False),
            ('0.0.2', '^0.0.1', False), ('0.2.0', '^0', True),
            ('26.10.0', '*', True), ('26.10.0', '^*', True), ('26.10.0', '~*', True),
            ('26.10.0', '<=*', True), ('26.10.0', '>*', False), ('26.10.0', '20 - *', True),
            ('22.0.0', '>20', True), ('20.20.2', '>20', False), ('22.23.2', '<=22', True),
        )
        for actual, requirement, expected in cases:
            with self.subTest(actual=actual, requirement=requirement):
                self.assertEqual(satisfies(tuple(map(int, actual.split('.'))), requirement), expected)
        for requirement in ('>=22banana', '22.0.0-beta.1', '22.23.2.4', '>=20<22', 'file:node', '22\n', ''):
            with self.subTest(requirement=requirement), self.assertRaises(ValueError):
                satisfies((22, 23, 2), requirement)

    def test_install_package_managers_use_selected_runtime_and_validate_before_mutation(self):
        (self.nvm / 'nvm.sh').write_text('# managed NVM')
        (self.project / 'package.json').write_text(json.dumps({'engines': {'node': '>=26'}}))
        args = argparse.Namespace(node_command='install', project=str(self.project), version='26.10.0',
                                  package_manager=['pnpm@12.6.0'])
        with patch.dict(os.environ, {'NVM_DIR': str(self.nvm)}), patch(
            'lib.node_toolchain.subprocess.run', side_effect=[
                subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 0, '12.1.0'),
                subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 0, '12.6.0'),
            ]
        ) as run:
            self.assertEqual(run_node_command(args), 0)
            self.assertEqual(runtime_owner(self.nvm, "26.10.0"), "project")
            installation = run.call_args_list[2]
            self.assertEqual(installation.args[0], [str(self.nvm / 'versions/node/v26.10.0/bin/npm'),
                                                  'install', '--global', '--prefix',
                                                  str(self.nvm / 'versions/node/v26.10.0'),
                                                  '--ignore-scripts=false', '--allow-scripts=pnpm', 'pnpm@12.6.0'])
            self.assertEqual(installation.kwargs['env']['npm_config_engine_strict'], 'true')
            self.assertNotEqual(installation.kwargs['cwd'], str(self.project))
            run.reset_mock()
            args.package_manager = ['pnpm@latest; touch injected']
            self.assertEqual(run_node_command(args), 1)
            run.assert_not_called()

    def test_install_prepares_missing_nvm_on_cachyos_then_installs_the_project_pin(self):
        (self.project / '.nvmrc').write_text('22')
        args = argparse.Namespace(node_command='install', project=str(self.project), version=None,
                                  package_manager=[])

        def prepare():
            script = self.nvm / 'nvm.sh'
            script.write_text('# prepared NVM')
            return script

        with patch.dict(os.environ, {'NVM_DIR': str(self.nvm)}), \
                patch('lib.cachyos.is_cachyos', return_value=True), \
                patch('common.cachyos_development.prepare_project_node_versions', side_effect=prepare) as bootstrap, \
                patch('lib.node_toolchain.subprocess.run', return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertEqual(run_node_command(args), 0)
        bootstrap.assert_called_once_with()
        self.assertEqual(run.call_args.args[0][-2:], [str(self.nvm / 'nvm.sh'), '22'])
        self.assertEqual(runtime_owner(self.nvm, '22.23.2'), 'project')

    def test_invalid_install_inputs_never_prepare_nvm_or_run_commands(self):
        args = argparse.Namespace(node_command='install', project=str(self.project), version='22',
                                  package_manager=[])
        with patch.dict(os.environ, {'NVM_DIR': str(self.nvm)}), \
                patch('common.cachyos_development.prepare_project_node_versions') as bootstrap, \
                patch('lib.node_toolchain.subprocess.run') as run:
            for version, managers, engines in (
                ('22; injected', [], '>=22'),
                ('22', ['pnpm@latest'], '>=22'),
                ('22', [], 'invalid'),
            ):
                with self.subTest(version=version, managers=managers, engines=engines):
                    args.version, args.package_manager = version, managers
                    (self.project / 'package.json').write_text(json.dumps({'engines': {'node': engines}}))
                    self.assertEqual(run_node_command(args), 1)
            bootstrap.assert_not_called()
            run.assert_not_called()

    def test_incompatible_exact_pin_never_prepares_nvm_or_installs_runtime(self):
        (self.project / '.node-version').write_text('20.20.2')
        (self.project / 'package.json').write_text(json.dumps({'engines': {'node': '>=22'}}))
        for installed_nvm in (False, True):
            if installed_nvm:
                (self.nvm / 'nvm.sh').write_text('# existing NVM')
            for version in (None, '20.20.2', 'v20.20.2'):
                args = argparse.Namespace(node_command='install', project=str(self.project), version=version,
                                          package_manager=[])
                with self.subTest(installed_nvm=installed_nvm, version=version), \
                        patch.dict(os.environ, {'NVM_DIR': str(self.nvm)}), \
                        patch('lib.cachyos.is_cachyos', return_value=True), \
                        patch('lib.node_toolchain.shutil.which', return_value=None), \
                        patch('common.cachyos_development.prepare_project_node_versions',
                              return_value=self.nvm / 'nvm.sh') as bootstrap, \
                        patch('lib.node_toolchain.subprocess.run',
                              return_value=subprocess.CompletedProcess([], 0)) as run:
                    self.assertEqual(run_node_command(args), 1)
                    bootstrap.assert_not_called()
                    run.assert_not_called()
                    self.assertIsNone(runtime_owner(self.nvm, '20.20.2'))

    def test_missing_nvm_on_other_hosts_and_preparation_failure_stop_runtime_install(self):
        args = argparse.Namespace(node_command='install', project=str(self.project), version='22',
                                  package_manager=[])
        for cachyos, failure in ((False, None), (True, RuntimeError('NVM installation failed'))):
            with self.subTest(cachyos=cachyos), patch.dict(os.environ, {'NVM_DIR': str(self.nvm)}), \
                    patch('lib.cachyos.is_cachyos', return_value=cachyos), \
                    patch('common.cachyos_development.prepare_project_node_versions', side_effect=failure) as bootstrap, \
                    patch('lib.node_toolchain.subprocess.run') as run:
                self.assertEqual(run_node_command(args), 1)
                self.assertEqual(bootstrap.call_count, int(cachyos))
                run.assert_not_called()

    def test_existing_custom_nvm_and_read_only_commands_never_prepare_nvm(self):
        (self.project / '.nvmrc').write_text('22')
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {'NVM_DIR': str(self.nvm)}))
            bootstrap = stack.enter_context(patch('common.cachyos_development.prepare_project_node_versions'))
            stack.enter_context(patch('lib.node_toolchain.subprocess.run',
                                     return_value=subprocess.CompletedProcess([], 0, 'v22.23.2')))
            for command in ('status', 'env', 'doctor', 'exec'):
                args = argparse.Namespace(node_command=command, project=str(self.project), version=None,
                                          json=True, argv=['--', 'node', '--version'])
                self.assertEqual(run_node_command(args), 0)
            (self.nvm / 'nvm.sh').write_text('# existing custom NVM')
            args = argparse.Namespace(node_command='install', project=str(self.project), version=None,
                                      package_manager=[])
            self.assertEqual(run_node_command(args), 0)
            bootstrap.assert_not_called()

    def test_project_doctor_uses_selected_tools_without_inherited_code_or_project_policy(self):
        (self.project / '.nvmrc').write_text('22')
        (self.project / 'package.json').write_text(json.dumps({'engines': {'npm': '>=11.18.0'}}))
        probes = []

        def probe(command, **options):
            probes.append(options['cwd'])
            self.assertTrue(Path(options['cwd']).is_dir())
            self.assertNotEqual(options['cwd'], str(self.project))
            self.assertIn('v22.23.2/bin', command[0])
            self.assertEqual(command[1:], ['--version'])
            self.assertEqual(options['env']['COREPACK_ENABLE_NETWORK'], '0')
            user_config = Path(options['env']['NPM_CONFIG_USERCONFIG'])
            global_config = Path(options['env']['NPM_CONFIG_GLOBALCONFIG'])
            self.assertNotEqual(user_config, global_config)
            for config in (user_config, global_config):
                self.assertEqual(config.parent, Path(options['cwd']))
                self.assertEqual(config.read_text(), '')
            self.assertEqual(options['env']['HOME'], options['cwd'])
            self.assertEqual(options['env']['NPM_CONFIG_UPDATE_NOTIFIER'], 'false')
            self.assertNotIn('NODE_OPTIONS', options['env'])
            self.assertNotIn('PRIVATE_TOKEN', options['env'])
            return subprocess.CompletedProcess(command, 0, 'v22.23.2' if Path(command[0]).name == 'node' else '11.19.0')

        with patch.dict(os.environ, {'NVM_DIR': str(self.nvm), 'NODE_OPTIONS': '--require=./project-code.js', 'PRIVATE_TOKEN': 'secret'}), \
                patch('lib.node_toolchain.subprocess.run', side_effect=probe) as run:
            result = inspect_project_node(str(self.project))
        self.assertTrue(result['healthy'])
        self.assertEqual(result['selection']['version'], '22.23.2')
        self.assertEqual(result['npm_requirement'], '>=11.18.0')
        self.assertEqual(run.call_count, 2)
        self.assertTrue(all(not Path(directory).exists() for directory in probes))

    def test_project_doctor_detects_wrong_binary_npm_engines_and_failed_probes(self):
        (self.project / '.nvmrc').write_text('22')
        (self.project / 'package.json').write_text(json.dumps({'engines': {'npm': '>=11.18.0'}}))
        with patch.dict(os.environ, {'NVM_DIR': str(self.nvm)}), patch(
            'lib.node_toolchain.subprocess.run', side_effect=[
                subprocess.CompletedProcess([], 0, 'v24.21.0'), subprocess.CompletedProcess([], 0, '10.9.0'),
            ]
        ):
            result = inspect_project_node(str(self.project))
        self.assertFalse(result['healthy'])
        self.assertEqual(result['issues'], ['project_node_version_mismatch', 'project_npm_engine_mismatch'])
        with patch.dict(os.environ, {'NVM_DIR': str(self.nvm)}), patch(
            'lib.node_toolchain.subprocess.run', side_effect=FileNotFoundError
        ):
            result = inspect_project_node(str(self.project))
        self.assertEqual(result['issues'], ['project_node_unusable', 'project_npm_unusable'])

    def test_project_doctor_system_selection_also_probes_outside_inherited_configuration(self):
        (self.nvm / 'alias/default').unlink()

        def probe(command, **options):
            self.assertNotEqual(options['cwd'], str(self.project))
            self.assertTrue(Path(options['cwd']).is_dir())
            self.assertNotIn('NODE_OPTIONS', options['env'])
            self.assertNotIn('LD_PRELOAD', options['env'])
            return subprocess.CompletedProcess(command, 0, 'v22.23.2' if command[0].endswith('/node') else '11.19.0')

        with patch.dict(os.environ, {'NVM_DIR': str(self.nvm), 'NODE_OPTIONS': '--require=project.js', 'LD_PRELOAD': '/private'}), \
                patch('lib.node_toolchain.shutil.which', return_value='/usr/bin/node'), \
                patch('lib.node_toolchain.subprocess.run', side_effect=probe) as run, \
                patch('lib.node_toolchain.runtime_owner', return_value='automatic') as owner:
            result = inspect_project_node(str(self.project))
        self.assertTrue(result['healthy'])
        self.assertEqual(result['selection']['runtime_source'], 'path')
        self.assertIsNone(result['maintenance_owner'])
        owner.assert_not_called()
        self.assertEqual(run.call_count, 3)


if __name__ == '__main__':
    unittest.main()
