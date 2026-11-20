"""Inference must be useful, reviewable, and must never execute project code."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from lib.manifest_init import (
    add_project_manifest_subparser,
    initialize_manifest,
    propose_manifest,
    run_project_manifest_command,
)
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
        for scripts, selected in (
            ({"compile": "tsc", "test": "test-tool"}, ['test']),
            ({"compile": "tsc", "check": "lint && tsc", "test": "test-tool"}, ['check', 'test']),
            ({"compile": "tsc", "check:ci": "lint && test", "check": "lint && browser-test",
              "test": "interactive-test-tool"}, ['check:ci']),
        ):
            with self.subTest(selected=selected):
                self.package(scripts=scripts)
                with patch('subprocess.run', side_effect=AssertionError('Inference executed code')):
                    result = propose_manifest(str(self.root))
                self.assertEqual(result['kind'], 'node-package')
                manifest = parse_manifest(result['manifest'])
                self.assertEqual(manifest.components, [])
                self.assertEqual(manifest.ci['install'][0].argv, ['npm', 'ci'])
                self.assertEqual(manifest.ci['build'][0].argv, ['npm', 'run', 'compile'])
                self.assertEqual([step.argv for step in manifest.ci['test']],
                                 [['npm', 'run', script] for script in selected])

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
        for index, (declaration, lock, header, command) in enumerate((
            ('', 'package-lock.json', '{}', ['npm', 'ci']),
            ('npm@12.1.0', 'npm-shrinkwrap.json', '{}', ['npm', 'ci']),
            ('', '', '', ['npm', 'install']),
            ('pnpm@10.0.0', '', '', ['pnpm', 'install', '--no-frozen-lockfile']),
            ('', 'pnpm-lock.yaml', 'lockfileVersion: 9', ['pnpm', 'install', '--frozen-lockfile']),
            ('yarn@1.22.22', 'yarn.lock', '# yarn lockfile v1', ['yarn', 'install', '--frozen-lockfile']),
            ('yarn@4.10.0', 'yarn.lock', '__metadata:\n  version: 8', ['yarn', 'install', '--immutable']),
            ('', 'yarn.lock', '# yarn lockfile v1', ['yarn', 'install', '--frozen-lockfile']),
            ('', 'yarn.lock', '__metadata:\n  version: 8', ['yarn', 'install', '--immutable']),
            ('yarn@4.10.0', '', '', ['yarn', 'install', '--no-immutable']),
        )):
            with self.subTest(declaration=declaration, lock=lock):
                directory = str(index)
                self.package(directory=directory, dependencies={'express': '*'},
                             scripts={'start': 'node app.js'}, packageManager=declaration)
                project = self.root / directory
                (project / 'package-lock.json').unlink()
                if lock:
                    (project / lock).write_text(header)
                with patch('subprocess.run', side_effect=AssertionError('Inference executed code')):
                    result = propose_manifest(str(project))['manifest']
                self.assertEqual(result['components'][0]['exec'], f'/usr/bin/env {command[0]} start')
                self.assertEqual(result['ci']['install'][0]['argv'], command)

    def test_package_manager_conflicts_and_unsafe_locks_fail_before_execution(self):
        for index, problem in enumerate(('conflict', 'mismatch', 'unversioned', 'floating',
                                         'null', 'unsupported', 'unknown-yarn', 'symlink', 'fifo')):
            with self.subTest(problem=problem):
                fields = {'packageManager': {
                    'mismatch': 'pnpm@10.0.0', 'unversioned': 'pnpm', 'floating': 'pnpm@latest',
                    'null': None, 'unsupported': 'bun@1.0.0',
                }.get(problem, '')}
                self.package(directory=str(index), **fields)
                project = self.root / str(index)
                if problem == 'conflict':
                    (project / 'pnpm-lock.yaml').write_text('lockfileVersion: 9')
                elif problem in ('unknown-yarn', 'symlink', 'fifo'):
                    lock = project / 'package-lock.json'
                    lock.unlink()
                    if problem == 'unknown-yarn':
                        (project / 'yarn.lock').write_text('not a supported lock header')
                    elif problem == 'symlink':
                        lock.symlink_to(project / 'package.json')
                    else:
                        os.mkfifo(lock)
                with patch('subprocess.run', side_effect=AssertionError('Inference executed code')):
                    with self.assertRaises(ValueError):
                        propose_manifest(str(project))

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


class T3WorktreeTests(unittest.TestCase):
    def setUp(self):
        root = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(root)
        self.commit = "a" * 40
        self.config = b'{ /* config */ "worktreeSparsePatternsPath": "./.t3/sparse", }'
        self.patterns = b"/app/\n!/app/private\n"
        self.files = {"t3.json": "b" * 40, ".t3/sparse": "c" * 40, "app/start script.sh": "d" * 40}
        self.included = b"app/start script.sh\0"
        self.enterContext(patch("lib.t3_project.subprocess.run", side_effect=self.run_git))

    def run_git(self, command, *, input, **kwargs):
        self.assertEqual(command[:4], ["git", "--literal-pathspecs", "-C", str(self.root)])
        args = command[4:]
        if args[0] == "rev-parse":
            self.assertEqual(args, ["rev-parse", "--verify", "--end-of-options", "release^{commit}"])
            output = self.commit.encode() + b"\n"
        elif args[0] == "ls-tree":
            self.assertEqual(args[1:5], ["--full-tree", "-z", self.commit, "--"])
            path = args[5]
            output = f"100644 blob {self.files[path]}\t{path}\0".encode() if path in self.files else b""
        elif args == ["cat-file", "blob", self.files["t3.json"]]:
            output = self.config
        elif args == ["cat-file", "blob", self.files.get(".t3/sparse")]:
            output = self.patterns
        else:
            self.assertEqual(args[:5], ["sparse-checkout", "check-rules", "--no-cone", "-z", "--rules-file"])
            self.assertEqual(Path(args[5]).read_bytes(), self.patterns)
            self.assertEqual(input, b"app/start script.sh\0")
            output = self.included
        return subprocess.CompletedProcess(command, 0, output, b"")

    def check_worktree(self, *files):
        parser = argparse.ArgumentParser()
        add_project_manifest_subparser(parser.add_subparsers(required=True))
        argv = ["manifest", "check-t3-worktree", str(self.root), "--ref", "release", "--json"]
        for file in files:
            argv.extend(["--require-file", file])
        output = StringIO()
        with redirect_stdout(output):
            status = run_project_manifest_command(parser.parse_args(argv))
        return status, json.loads(output.getvalue())

    def test_verifies_committed_rules_and_literal_required_path(self):
        (self.root / "t3.json").write_text("{}")
        status, result = self.check_worktree("app/start script.sh")
        self.assertEqual(status, 0)
        self.assertEqual(result, {
            "status": "sparse", "commit": self.commit, "patterns_path": ".t3/sparse",
            "required_files": ["app/start script.sh"],
        })

    def test_reports_excluded_and_missing_files(self):
        self.included = b""
        status, result = self.check_worktree("app/start script.sh")
        self.assertEqual(status, 1)
        self.assertIn("exclude required files", result["error"])
        _, result = self.check_worktree("missing.sh")
        self.assertIn("Required files missing", result["error"])
        del self.files[".t3/sparse"]
        _, result = self.check_worktree()
        self.assertIn("Sparse patterns file '.t3/sparse' is missing", result["error"])

    def test_full_checkout_and_invalid_paths(self):
        self.config = b'{"$schema":"https://t3.codes/schema/t3.json",}'
        status, result = self.check_worktree("app/start script.sh")
        self.assertEqual(status, 0)
        self.assertEqual(result["status"], "full")
        for value in ("../rules", "/tmp/rules", "C:/rules", "dir/./rules", "a\nb"):
            with self.subTest(value=value):
                self.config = json.dumps({"worktreeSparsePatternsPath": value}).encode()
                self.assertEqual(self.check_worktree()[0], 1)


if __name__ == '__main__':
    unittest.main()
