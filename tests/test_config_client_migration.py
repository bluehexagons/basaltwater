"""Client migration tests isolated from the operator's home and system."""

from __future__ import annotations

import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from lib.workspace import get_setup_cache_dir, get_workspace_dir


class ClientMigrationTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.dict(os.environ, {'HOME': str(self.home)}, clear=True))
        self.enterContext(patch('lib.concurrency.tempfile.gettempdir', return_value=str(self.home)))
        self.old = self.home / '.config/infra_tools'
        self.new = self.home / '.config/basaltwater'
        (self.old / 'setups').mkdir(parents=True)
        self.host = self.old / 'setups/host.json'
        self.host.write_text('{"host":"node.example","password":"infra_tools-private"}')
        self.host.chmod(0o600)

    def test_lookup_migrates_files_once_with_bytes_inode_and_mode_intact(self):
        original = self.host.read_bytes(), self.host.stat().st_ino, self.host.stat().st_mode
        for _ in range(2):
            host = Path(get_setup_cache_dir()) / 'host.json'
            self.assertEqual((host.read_bytes(), host.stat().st_ino, host.stat().st_mode), original)
        self.assertFalse(self.old.exists())

    def test_merges_empty_new_workspace_and_disjoint_historical_spellings(self):
        (self.new / 'setups').mkdir(parents=True)
        other = self.old.with_name('infra-tools')
        other.mkdir()
        (other / 'known_hosts').write_text('trusted-host-key')
        get_workspace_dir()
        self.assertEqual((self.new / 'known_hosts').read_text(), 'trusted-host-key')
        self.assertTrue((self.new / 'setups/host.json').exists())
        self.assertFalse(other.exists())

    def test_conflict_preflight_does_not_move_any_files(self):
        (self.new / 'setups').mkdir(parents=True)
        (self.new / 'setups/host.json').write_text('new config')
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            get_workspace_dir()
        self.assertTrue(self.host.exists())
        self.assertEqual((self.new / 'setups/host.json').read_text(), 'new config')

    def test_explicit_workspace_does_not_migrate_default_data(self):
        os.environ['BASALTWATER_WORKSPACE'] = str(self.home / 'custom')
        self.assertEqual(get_workspace_dir(), str(self.home / 'custom'))
        self.assertTrue(self.host.exists())

    def test_symlinked_destination_is_rejected(self):
        self.new.symlink_to(self.old, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            get_workspace_dir()
        self.assertTrue(self.host.exists())

    def test_cli_list_finds_saved_hosts_and_preserves_credentials(self):
        self.host.write_text(json.dumps({
            'host': 'node.example', 'system_type': 'server_lite',
            'args': {'host': 'node.example', 'username': 'agent',
                     'system_type': 'server_lite', 'friendly_name': 'my-node'},
        }))
        secret = self.old / 'credentials.json'
        secret.write_bytes(b'{"agent":"infra_tools-secret"}')
        secret.chmod(0o600)
        command = [sys.executable, str(Path(__file__).resolve().parents[1] / 'basaltwater.py'), 'list', '--json']
        result = subprocess.run(command, capture_output=True, text=True, check=True,
                                env={**os.environ, 'TMPDIR': str(self.home)})
        self.assertIn('node.example', result.stdout)
        json.loads(result.stdout)
        self.assertNotIn('infra_tools-secret', result.stdout + result.stderr)
        self.assertEqual((self.new / 'credentials.json').read_bytes(), b'{"agent":"infra_tools-secret"}')
        self.assertEqual((self.new / 'credentials.json').stat().st_mode & 0o777, 0o600)

    def test_partial_merge_is_resumed_without_overwriting_moved_files(self):
        self.new.mkdir()
        self.old.chmod(0o700)
        self.new.chmod(0o755)
        (self.old / 'known_hosts').write_text('trusted-key')
        rename = Path.rename
        count = 0
        def interrupted(path, destination):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError('simulated interruption')
            return rename(path, destination)
        with patch.object(Path, 'rename', interrupted):
            with self.assertRaisesRegex(OSError, 'interruption'):
                get_workspace_dir()
        self.assertEqual(self.new.stat().st_mode & 0o777, 0o700)
        get_workspace_dir()
        self.assertTrue((self.new / 'setups/host.json').exists())
        self.assertEqual((self.new / 'known_hosts').read_text(), 'trusted-key')
        self.assertFalse(self.old.exists())
