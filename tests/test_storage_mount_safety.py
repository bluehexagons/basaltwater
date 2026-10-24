"""Unavailable configured mounts cannot expose mirror contents to deletion."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.runtime_config import RuntimeConfig
from lib.task_utils import unavailable_sync_subtrees, validate_configured_storage_mounts
from sync import sync_steps
from sync.service_tools import storage_ops, sync_rsync


class StorageMountSafetyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source, self.destination = self.root / 'source', self.root / 'backup'
        self.source.mkdir()
        self.destination.mkdir()

    def config(self, mount, *, smb=False):
        spec = {'smb_mounts': [[str(mount), 'server', 'credentials', 'share']]} if smb else {
            'storage_mounts': [['data', str(mount)]],
        }
        return RuntimeConfig.from_dict(spec)

    def test_alias_cannot_bypass_required_endpoint_mount(self):
        alias = self.root / 'alias'
        alias.symlink_to(self.source, target_is_directory=True)
        for smb in (False, True):
            config = self.config(self.source, smb=smb)
            with self.subTest(smb=smb), patch('lib.task_utils.os.path.ismount', return_value=False):
                for endpoint in (self.source, alias, alias / 'new-child'):
                    with self.assertRaisesRegex(RuntimeError, 'Required configured mount'):
                        validate_configured_storage_mounts(str(endpoint), config)
                    valid, _ = storage_ops.validate_mounts_for_operation([str(endpoint)], config, 'sync')
                    self.assertFalse(valid)
            with patch('lib.task_utils.os.path.ismount', return_value=True):
                validate_configured_storage_mounts(str(alias), config)

    def test_symlinked_mount_declaration_is_rejected_through_both_names(self):
        declaration = self.root / 'declared-alias'
        declaration.symlink_to(self.source, target_is_directory=True)
        config = self.config(declaration)
        with patch('lib.task_utils.os.path.ismount', return_value=True):
            for endpoint in (declaration, self.source):
                with self.assertRaisesRegex(RuntimeError, 'Required configured mount'):
                    validate_configured_storage_mounts(str(endpoint), config)

    def test_offline_nested_mounts_protect_only_their_subtrees(self):
        alias = self.root / 'alias'
        alias.symlink_to(self.source, target_is_directory=True)
        for parent in (self.source, self.destination):
            mount = parent / 'disk'
            mount.mkdir()
            for smb in (False, True):
                config = self.config(mount, smb=smb)
                with self.subTest(parent=parent, smb=smb), patch('lib.task_utils.os.path.ismount', return_value=False):
                    # The endpoint itself remains available for unrelated files.
                    self.assertTrue(storage_ops.validate_mounts_for_operation([str(alias), str(self.destination)], config, 'sync')[0])
                    integrity = storage_ops.validate_sync_integrity(str(alias), str(self.destination), config)
                    self.assertEqual(integrity.excluded_paths, ('disk',))
                    self.assertFalse(integrity.copy_new_only)
                    self.assertIn('unavailable configured mounts', integrity.warning)
                    self.assertEqual(unavailable_sync_subtrees(str(self.root / 'unrelated'), str(self.root / 'another'), config), ())
                with patch('lib.task_utils.os.path.ismount', return_value=True):
                    self.assertEqual(unavailable_sync_subtrees(str(alias), str(self.destination), config), ())

    def test_scheduled_backup_passes_nested_mount_protection_and_records_completion(self):
        mount = self.source / 'disk'
        mount.mkdir()
        config = {'backup_specs': [[str(self.source), str(self.destination), 'daily']],
                  'storage_mounts': [['data', str(mount)]]}
        with patch.object(storage_ops, 'load_setup_config', return_value=config), patch.object(storage_ops, 'STATE_FILE', str(self.root / 'state.json')), patch.object(storage_ops, 'get_service_logger'), patch.object(storage_ops, 'parse_notification_args', return_value=[]), patch('lib.task_utils.os.path.ismount', return_value=False), patch.object(sync_rsync, 'run_rsync_with_notifications', return_value=0) as run:
            result = storage_ops.execute_storage_operations()
            state = storage_ops.load_last_run()
        self.assertTrue(result['success'])
        self.assertIn('unavailable configured mounts', result['syncs'][0]['warning'])
        run.assert_called_once_with(str(self.source), str(self.destination), suppress_notifications=True,
                                    excluded_paths=('disk',), copy_new_only=False,
                                    config=RuntimeConfig.from_dict(config),
                                    precautions=sync_rsync.SyncPrecautions())
        self.assertIn(storage_ops.get_sync_op_id(str(self.source), str(self.destination)), state)

    def test_inline_sync_preserves_offline_nested_mounts(self):
        config = self.config(self.source / 'disk')
        config.username = 'agent'
        with patch.object(sync_steps, 'create_operation_logger'), patch.object(sync_steps, 'validate_mount_for_sync', return_value=True), patch.object(sync_steps, 'get_disk_usage_details', return_value={'usage_percent': 0}), patch('lib.task_utils.os.path.ismount', return_value=False), patch.object(sync_rsync, 'run_rsync_with_notifications', return_value=0) as run:
            sync_steps.create_sync_service(config, [str(self.source), str(self.destination), 'daily'])
        run.assert_called_once_with(str(self.source), str(self.destination), suppress_notifications=True,
                                    excluded_paths=('disk',), config=config, initial_setup=True)
