"""Tests for uv installer validation in common/common_steps.py."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from common import common_steps
from common.common_steps import _validate_uv_install_script


class TestValidateUvInstallScript(unittest.TestCase):
    def _create_temp_script(self, content: str, mode: int = 0o644) -> str:
        """Create a temporary script file with given content and permissions."""
        fd, path = tempfile.mkstemp(suffix='.sh')
        os.close(fd)
        with open(path, 'w') as f:
            f.write(content)
        os.chmod(path, mode)
        return path

    def test_valid_uv_install_script(self):
        content = """#!/bin/sh
set -e
curl -LsSf https://astral.sh/uv/install.sh | sh
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
            f.write(content)
            f.flush()
            os.chmod(f.name, 0o644)
            try:
                result = _validate_uv_install_script(f.name)
                self.assertTrue(result)
            finally:
                os.unlink(f.name)

    def test_valid_uv_install_script_github_url(self):
        content = """#!/usr/bin/env sh
# Installer for uv from github.com/astral-sh/uv
curl -LsSf https://github.com/astral-sh/uv/install.sh | sh
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
            f.write(content)
            f.flush()
            os.chmod(f.name, 0o644)
            try:
                result = _validate_uv_install_script(f.name)
                self.assertTrue(result)
            finally:
                os.unlink(f.name)

    def test_missing_shebang(self):
        content = """set -e
curl -LsSf https://astral.sh/uv/install.sh | sh
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
            f.write(content)
            f.flush()
            os.chmod(f.name, 0o644)
            try:
                result = _validate_uv_install_script(f.name)
                self.assertFalse(result)
            finally:
                os.unlink(f.name)

    def test_missing_uv_reference(self):
        content = """#!/bin/sh
set -e
curl -LsSf https://example.com/install.sh | sh
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
            f.write(content)
            f.flush()
            os.chmod(f.name, 0o644)
            try:
                result = _validate_uv_install_script(f.name)
                self.assertFalse(result)
            finally:
                os.unlink(f.name)

    def test_suspicious_rm_rf(self):
        content = """#!/bin/sh
rm -rf / some_file
curl -LsSf https://astral.sh/uv/install.sh | sh
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
            f.write(content)
            f.flush()
            os.chmod(f.name, 0o644)
            try:
                result = _validate_uv_install_script(f.name)
                self.assertFalse(result)
            finally:
                os.unlink(f.name)

    def test_suspicious_chmod_777(self):
        content = """#!/bin/sh
chmod -R 777 /etc
curl -LsSf https://astral.sh/uv/install.sh | sh
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
            f.write(content)
            f.flush()
            os.chmod(f.name, 0o644)
            try:
                result = _validate_uv_install_script(f.name)
                self.assertFalse(result)
            finally:
                os.unlink(f.name)

    def test_suspicious_mkfs(self):
        content = """#!/bin/sh
mkfs.ext4 /dev/sda
curl -LsSf https://astral.sh/uv/install.sh | sh
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
            f.write(content)
            f.flush()
            os.chmod(f.name, 0o644)
            try:
                result = _validate_uv_install_script(f.name)
                self.assertFalse(result)
            finally:
                os.unlink(f.name)

    def test_suspicious_dd_if(self):
        content = """#!/bin/sh
dd if=/dev/zero of=/dev/sda
curl -LsSf https://astral.sh/uv/install.sh | sh
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
            f.write(content)
            f.flush()
            os.chmod(f.name, 0o644)
            try:
                result = _validate_uv_install_script(f.name)
                self.assertFalse(result)
            finally:
                os.unlink(f.name)

    def test_nonexistent_file(self):
        result = _validate_uv_install_script('/nonexistent/path/script.sh')
        self.assertFalse(result)

    def test_file_with_only_whitespace(self):
        content = """   
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
            f.write(content)
            f.flush()
            os.chmod(f.name, 0o644)
            try:
                result = _validate_uv_install_script(f.name)
                self.assertFalse(result)
            finally:
                os.unlink(f.name)


class TestInstallOrUpdateUv(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = temporary.name
        self.uv = Path(self.home) / '.local/bin/uv'
        self.uv.parent.mkdir(parents=True)
        for patcher in (patch('common.common_steps.is_dry_run', return_value=False),
                        patch('common.common_steps.record_installer')):
            patcher.start()
            self.addCleanup(patcher.stop)

    def create_uv(self, mode=0o755):
        self.uv.write_text('mock uv')
        self.uv.chmod(mode)

    @patch("common.common_steps.run")
    def test_user_uv_update_repairs_ownership_and_sets_login_env(self, mock_run):
        self.create_uv()
        mock_run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")

        result = common_steps.install_or_update_uv(self.home, username="user")

        self.assertTrue(result)
        commands = [args[0] for args, _ in mock_run.call_args_list]
        self.assertIn(f"chown -R user:user {self.home}/.local", commands)
        self.assertTrue(
            any(
                f"HOME={self.home} USER=user LOGNAME=user" in command
                and f"{self.uv} self update" in command
                for command in commands
            )
        )
        self.assertEqual(sum(f'{self.uv} --version' in command for command in commands), 2)

    @patch('common.common_steps.run')
    def test_usable_existing_uv_skips_download_and_update(self, run):
        self.create_uv()
        run.return_value = SimpleNamespace(returncode=0)
        self.assertTrue(common_steps.install_or_update_uv(self.home, update=False))
        self.assertEqual(run.call_count, 1)
        self.assertIn(f'{self.uv} --version', run.call_args.args[0])

    @patch('common.common_steps.run')
    def test_directory_is_preserved_and_fails_without_download(self, run):
        self.uv.mkdir()
        marker = self.uv / 'keep'
        marker.write_text('preserve')
        self.assertFalse(common_steps.install_or_update_uv(self.home, update=False))
        self.assertEqual(marker.read_text(), 'preserve')
        run.assert_not_called()

    def test_invalid_existing_entries_are_reinstalled_and_verified(self):
        for entry in ('nonexecutable', 'broken-executable', 'dangling-link', 'broken-link-target'):
            with self.subTest(entry=entry):
                target = Path(self.home) / 'target'
                if entry == 'dangling-link':
                    self.uv.symlink_to(target)
                elif entry == 'broken-link-target':
                    target.write_text('preserve target')
                    target.chmod(0o755)
                    self.uv.symlink_to(target)
                else:
                    self.create_uv(0o644 if entry == 'nonexecutable' else 0o755)
                installed = False

                def run(command, **kwargs):
                    nonlocal installed
                    if isinstance(command, list):
                        Path(command[-1]).write_text('#!/bin/sh\n# astral.sh/uv\n')
                    elif ' sh ' in command:
                        self.assertFalse(os.path.lexists(self.uv))
                        self.create_uv()
                        installed = True
                    elif '--version' in command:
                        return SimpleNamespace(returncode=0 if installed else 1)
                    return SimpleNamespace(returncode=0)

                with patch('common.common_steps.run', side_effect=run):
                    self.assertTrue(common_steps.install_or_update_uv(self.home, update=False))
                self.assertTrue(installed)
                if target.exists():
                    self.assertEqual(target.read_text(), 'preserve target')
                    target.unlink()
                self.uv.unlink()

    def test_successful_installer_must_produce_usable_uv(self):
        for outcome in ('missing', 'directory', 'nonexecutable', 'failed-version'):
            with self.subTest(outcome=outcome):
                def run(command, **kwargs):
                    if isinstance(command, list):
                        Path(command[-1]).write_text('#!/bin/sh\n# astral.sh/uv\n')
                    elif ' sh ' in command:
                        if outcome == 'directory':
                            self.uv.mkdir()
                        elif outcome != 'missing':
                            self.create_uv(0o644 if outcome == 'nonexecutable' else 0o755)
                    return SimpleNamespace(returncode=1 if '--version' in command else 0)

                with patch('common.common_steps.run', side_effect=run):
                    self.assertFalse(common_steps.install_or_update_uv(self.home, update=False))
                if self.uv.is_dir():
                    self.uv.rmdir()
                elif self.uv.exists():
                    self.uv.unlink()

    def test_failed_download_or_validation_preserves_invalid_entry(self):
        self.create_uv(0o644)
        for download_status in (0, 1):
            with self.subTest(download_status=download_status), patch(
                'common.common_steps.run', return_value=SimpleNamespace(returncode=download_status)
            ):
                self.assertFalse(common_steps.install_or_update_uv(self.home, update=False))
                self.assertEqual(self.uv.read_text(), 'mock uv')

    @patch('common.common_steps.run')
    def test_update_success_cannot_hide_broken_result(self, run):
        self.create_uv()
        run.side_effect = [SimpleNamespace(returncode=0), SimpleNamespace(returncode=0),
                           SimpleNamespace(returncode=1)]
        self.assertFalse(common_steps.install_or_update_uv(self.home))

    @patch('common.common_steps.run')
    def test_failed_update_can_retain_usable_uv(self, run):
        self.create_uv()
        run.side_effect = [SimpleNamespace(returncode=0), SimpleNamespace(returncode=1),
                           SimpleNamespace(returncode=0)]
        self.assertTrue(common_steps.install_or_update_uv(self.home))


if __name__ == '__main__':
    unittest.main()
