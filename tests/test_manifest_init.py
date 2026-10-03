"""Inference must be useful, reviewable, and must never execute project code."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lib.manifest_init import initialize_manifest, propose_manifest
from lib.project_manifest import load_manifest, parse_manifest


class ManifestInitTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def package(self, *, directory=".", dependencies=None, scripts=None, **fields):
        path = self.root / directory
        path.mkdir(exist_ok=True)
        data = {"name": "example", "scripts": scripts or {"build": "build-tool", "test": "test-tool"},
                "devDependencies": dependencies or {}, **fields}
        (path / "package.json").write_text(json.dumps(data))
        (path / "package-lock.json").write_text('{}')

    def go(self):
        (self.root / "go.mod").write_text("module example.com/app\ngo 1.26\n")
        directory = self.root / "cmd" / "server"
        directory.mkdir(parents=True)
        (directory / "main.go").write_text("package main\nfunc main() {}\n")

    def test_node_package_is_ci_only(self):
        self.package(scripts={"compile": "tsc", "check": "lint && test"})
        with patch('subprocess.run', side_effect=AssertionError('Inference executed code')):
            result = initialize_manifest(str(self.root))
        self.assertEqual(result['kind'], 'node-package')
        manifest = load_manifest(str(self.root))
        self.assertEqual(manifest.components, [])
        self.assertEqual(manifest.ci['install'][0].argv, ['npm', 'ci'])
        self.assertEqual(manifest.ci['build'][0].argv, ['npm', 'run', 'compile'])
        self.assertEqual(manifest.ci['test'][0].argv, ['npm', 'run', 'check'])

    def test_vite_output_and_dry_run(self):
        self.package(dependencies={'vite': '*'}, scripts={'build': 'vite build --outDir site'})
        result = initialize_manifest(str(self.root), dry_run=True)
        self.assertEqual(result['manifest']['components'][0]['output'], 'site')
        self.assertFalse((self.root / 'basaltwater.json').exists())

    def test_electron_vite_app_is_not_a_static_website(self):
        self.package(dependencies={'electron': '*', 'vite': '*'})
        result = propose_manifest(str(self.root))
        self.assertEqual(result['kind'], 'node-package')
        self.assertEqual(result['manifest']['components'], [])

    def test_static_site(self):
        (self.root / 'public').mkdir()
        (self.root / 'public/index.html').write_text('<html></html>')
        result = initialize_manifest(str(self.root))
        self.assertEqual(result['kind'], 'static')
        self.assertEqual(load_manifest(str(self.root)).components[0].output, 'public')

    def test_go_service_and_full_stack_builds_are_separate(self):
        self.go()
        self.assertEqual(propose_manifest(str(self.root))['kind'], 'go-service')
        self.package(directory='frontend', dependencies={'vite': '*'})
        result = propose_manifest(str(self.root))
        self.assertEqual(result['kind'], 'full-stack')
        manifest = parse_manifest(result['manifest'])
        self.assertIn('./cmd/server', manifest.ci['build'][1].argv)
        api, site = manifest.components
        self.assertEqual(api.domain, 'api.{{domain}}')
        self.assertEqual(site.output, 'frontend/dist')
        self.assertFalse(any('npm' in command for command in api.build))
        self.assertFalse(any('go build' in command for command in site.build))

    def test_node_service_and_full_stack(self):
        self.package(dependencies={'express': '*'}, scripts={'start': 'node app.js'})
        self.assertEqual(propose_manifest(str(self.root))['kind'], 'node-service')
        self.package(directory='web', dependencies={'vite': '*'})
        manifest = parse_manifest(propose_manifest(str(self.root))['manifest'])
        self.assertEqual(manifest.components[0].exec, '/usr/bin/env npm start')
        self.assertEqual(manifest.components[1].output, 'web/dist')

    def test_package_manager_is_detected_from_project_not_cwd(self):
        self.package(dependencies={'express': '*'}, scripts={'start': 'node app.js'})
        (self.root / 'package-lock.json').unlink()
        (self.root / 'pnpm-lock.yaml').write_text('lockfileVersion: 9')
        result = propose_manifest(str(self.root))
        self.assertEqual(result['manifest']['components'][0]['exec'], '/usr/bin/env pnpm start')
        self.assertEqual(result['manifest']['ci']['install'][0]['argv'], ['pnpm', 'install', '--frozen-lockfile'])

    def test_existing_manifest_preserved_and_invalid_not_overwritten(self):
        self.package()
        initialize_manifest(str(self.root))
        path = self.root / 'basaltwater.json'
        before = path.read_bytes()
        self.assertEqual(initialize_manifest(str(self.root))['status'], 'existing')
        self.assertEqual(path.read_bytes(), before)
        path.write_text('{broken')
        with self.assertRaises(ValueError):
            initialize_manifest(str(self.root))
        self.assertEqual(path.read_text(), '{broken')

    def test_symlinks_fifos_and_retired_manifest_fail(self):
        self.package()
        target = self.root / 'target.json'
        target.write_text('{}')
        path = self.root / 'basaltwater.json'
        path.symlink_to(target)
        with self.assertRaises(ValueError):
            initialize_manifest(str(self.root))
        path.unlink()
        os.mkfifo(path)
        with self.assertRaises(ValueError):
            initialize_manifest(str(self.root))
        path.unlink()
        (self.root / 'infra.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'Rename infra.json'):
            initialize_manifest(str(self.root))

    def test_workflows_validate_literal_commands_and_paths(self):
        valid = {'version': 1, 'components': [], 'ci': {'test': [{'argv': ['npm', 'test'], 'directory': 'frontend'}]}}
        self.assertEqual(parse_manifest(valid).ci['test'][0].directory, 'frontend')
        for invalid in (
            {'version': True, 'components': [], 'ci': {'test': [{'argv': ['true']}]}},
            {'version': 1, 'components': []},
            {'version': 1, 'components': [], 'ci': {'deploy': [{'argv': ['true']}] }},
            {'version': 1, 'components': [], 'ci': {'test': [{'argv': 'npm test'}]}},
            {'version': 1, 'components': [], 'ci': {'test': [{'argv': ['true'], 'directory': '../outside'}]}},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                parse_manifest(invalid)


if __name__ == '__main__':
    unittest.main()
