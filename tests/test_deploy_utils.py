"""Tests for lib/deploy_utils.py: deployment parsing, type detection, and redeploy logic."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.deploy_utils import (
    parse_deploy_spec,
    create_safe_directory_name,
    detect_project_type,
    get_project_root,
    is_ruby_project,
    get_deployment_metadata_path,
    save_deployment_metadata,
    load_deployment_metadata,
    should_redeploy,
)
from lib.state_read import StateReadError


class TestParseDeploySpec(unittest.TestCase):
    def test_local_path(self):
        domain, path = parse_deploy_spec('/var/www')
        self.assertIsNone(domain)
        self.assertEqual(path, '/var/www')

    def test_domain_with_path(self):
        domain, path = parse_deploy_spec('example.com/blog')
        self.assertEqual(domain, 'example.com')
        self.assertEqual(path, '/blog')

    def test_domain_only(self):
        domain, path = parse_deploy_spec('example.com')
        self.assertEqual(domain, 'example.com')
        self.assertEqual(path, '/')

    def test_domain_with_nested_path(self):
        domain, path = parse_deploy_spec('example.com/app/v2')
        self.assertEqual(domain, 'example.com')
        self.assertEqual(path, '/app/v2')


class TestCreateSafeDirectoryName(unittest.TestCase):
    def test_domain_only(self):
        name = create_safe_directory_name('example.com', '/')
        self.assertEqual(name, 'example_com')

    def test_domain_with_path(self):
        name = create_safe_directory_name('example.com', '/blog')
        self.assertEqual(name, 'example_com__blog')

    def test_local_path_root(self):
        name = create_safe_directory_name(None, '/')
        self.assertEqual(name, 'root')

    def test_local_path_subdir(self):
        name = create_safe_directory_name(None, '/var/www')
        self.assertEqual(name, 'var_www')

    def test_no_dots_in_domain(self):
        name = create_safe_directory_name('localhost', '/')
        self.assertEqual(name, 'localhost')


class TestDetectProjectType(unittest.TestCase):
    def test_ruby_markers_are_unsupported_not_a_project_type(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, '.ruby-version'), 'w') as f:
                f.write('3.2.0')
            self.assertTrue(is_ruby_project(tmpdir))
            self.assertEqual(detect_project_type(tmpdir), 'unknown')

    def test_gemfile_is_detected_by_unsupported_guard(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, 'Gemfile'), 'w') as f:
                f.write("gem 'rails'\n")
            self.assertTrue(is_ruby_project(tmpdir))
            self.assertEqual(detect_project_type(tmpdir), 'unknown')

    def test_config_ru_is_detected_by_unsupported_guard(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, 'config.ru'), 'w') as f:
                f.write('run Rails.application')
            self.assertTrue(is_ruby_project(tmpdir))
            self.assertEqual(detect_project_type(tmpdir), 'unknown')

    def test_node_project(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, 'package.json'), 'w') as f:
                f.write('{}')
            self.assertEqual(detect_project_type(tmpdir), 'node')

    def test_static_project(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, 'index.html'), 'w') as f:
                f.write('<html></html>')
            self.assertEqual(detect_project_type(tmpdir), 'static')

    def test_unknown_project(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertEqual(detect_project_type(tmpdir), 'unknown')

    def test_public_directory_is_static_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, 'public'))
            self.assertEqual(detect_project_type(tmpdir), 'unknown')


class TestGetProjectRoot(unittest.TestCase):
    def test_node_with_dist(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dist_dir = os.path.join(tmpdir, 'dist')
            os.makedirs(dist_dir)
            self.assertEqual(get_project_root(tmpdir, 'node'), dist_dir)

    def test_node_with_build(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            build_dir = os.path.join(tmpdir, 'build')
            os.makedirs(build_dir)
            self.assertEqual(get_project_root(tmpdir, 'node'), build_dir)

    def test_static_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertEqual(get_project_root(tmpdir, 'static'), tmpdir)

class TestDeploymentMetadata(unittest.TestCase):
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            save_deployment_metadata(tmpdir, 'https://git.example.com/repo.git', 'abc123')
            metadata = load_deployment_metadata(tmpdir)
            self.assertIsNotNone(metadata)
            self.assertEqual(metadata['git_url'], 'https://git.example.com/repo.git')
            self.assertEqual(metadata['commit_hash'], 'abc123')

    def test_load_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertIsNone(load_deployment_metadata(tmpdir))

    def test_metadata_path(self):
        path = get_deployment_metadata_path('/var/www/app')
        self.assertTrue(path.endswith('.deploy_metadata.json'))

    def test_invalid_metadata_blocks_incremental_redeploy_and_preserves_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = get_deployment_metadata_path(directory)
            for content in ('invalid', 'null', '[]', '{}', '{"git_url":1,"commit_hash":"abc"}', '{"git_url":"url","commit_hash":[]}'):
                with self.subTest(content=content):
                    with open(path, 'w') as stream:
                        stream.write(content)
                    with self.assertRaises(StateReadError):
                        should_redeploy(directory, 'url', 'hash', full_deploy=False)
                    with open(path) as stream:
                        self.assertEqual(stream.read(), content)

    def test_metadata_links_and_special_files_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = get_deployment_metadata_path(directory)
            original = os.path.join(directory, 'original.json')
            with open(original, 'w') as stream:
                stream.write('{"git_url":"url","commit_hash":"hash"}')
            os.symlink(original, path)
            with self.assertRaises(StateReadError):
                load_deployment_metadata(directory)
            os.unlink(path)
            os.mkfifo(path)
            with self.assertRaises(StateReadError):
                load_deployment_metadata(directory)

    def test_null_commit_metadata_is_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            save_deployment_metadata(directory, 'url', None)
            self.assertEqual(load_deployment_metadata(directory)['commit_hash'], None)


class TestShouldRedeploy(unittest.TestCase):
    def test_full_deploy_always_true(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertTrue(should_redeploy(tmpdir, 'url', 'hash', full_deploy=True))

    def test_no_existing_deployment(self):
        self.assertTrue(should_redeploy('/nonexistent/path', 'url', 'hash', full_deploy=False))

    def test_no_commit_hash(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertTrue(should_redeploy(tmpdir, 'url', None, full_deploy=False))

    def test_no_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertTrue(should_redeploy(tmpdir, 'url', 'hash', full_deploy=False))

    def test_same_commit_skip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            save_deployment_metadata(tmpdir, 'url', 'hash123')
            self.assertFalse(should_redeploy(tmpdir, 'url', 'hash123', full_deploy=False))

    def test_different_commit_redeploy(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            save_deployment_metadata(tmpdir, 'url', 'old_hash')
            self.assertTrue(should_redeploy(tmpdir, 'url', 'new_hash', full_deploy=False))

    def test_different_git_url_redeploy(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            save_deployment_metadata(tmpdir, 'old_url', 'hash')
            self.assertTrue(should_redeploy(tmpdir, 'new_url', 'hash', full_deploy=False))


if __name__ == '__main__':
    unittest.main()
