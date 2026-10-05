"""Project tooling must reject CI-only deploys before host mutations."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from lib.config import SetupConfig
from lib.deployment import DeploymentOrchestrator
from lib.project_manifest import Component, Manifest, WorkflowStep
from lib.setup_common import prepare_deployments


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
