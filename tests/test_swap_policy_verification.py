"""Swap policy must remain safe and match observed kernel state."""

from __future__ import annotations

from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common.swap_steps import _assert_safe_swap_parent, _configure_zswap


class TestSwapParentTrust(unittest.TestCase):
    def test_every_existing_ancestor_must_be_trusted(self):
        for unsafe in (SimpleNamespace(st_uid=1000, st_mode=stat.S_IFDIR | 0o755),
                       SimpleNamespace(st_uid=0, st_mode=stat.S_IFDIR | 0o775)):
            with self.subTest(unsafe=unsafe):
                def inspect(path):
                    return unsafe if str(path) == '/srv/user' else SimpleNamespace(
                        st_uid=0, st_mode=stat.S_IFDIR | 0o755)
                with patch('common.swap_steps.Path.exists', return_value=True), patch(
                    'common.swap_steps.Path.stat', autospec=True, side_effect=inspect
                ):
                    with self.assertRaisesRegex(RuntimeError, '/srv/user'):
                        _assert_safe_swap_parent('/srv/user/root-owned')

    def test_missing_descendants_can_use_trusted_ancestors(self):
        observed = []
        def inspect(path):
            if str(path) not in {'/', '/srv'}:
                raise FileNotFoundError(str(path))
            observed.append(str(path))
            return SimpleNamespace(st_uid=0, st_mode=stat.S_IFDIR | 0o755)
        with patch('common.swap_steps.Path.exists', autospec=True,
                   side_effect=lambda path: str(path) in {'/', '/srv'}), patch(
            'common.swap_steps.Path.stat', autospec=True, side_effect=inspect
        ):
            _assert_safe_swap_parent('/srv/new/swap')
        self.assertEqual(observed, ['/srv', '/'])


class TestZswapPolicyVerification(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.service = root / 'zswap.service'
        self.kernel = {'enabled': 'N', 'max_pool_percent': '20'}
        self.apply = True
        self.unreadable = False
        self.events = []
        self.enterContext(patch('common.swap_steps.ZSWAP_SERVICE_PATH', str(self.service)))
        self.enterContext(patch('common.swap_steps.os.path.exists', return_value=True))
        read = Path.read_text
        def read_text(path, **kwargs):
            if str(path).startswith('/sys/module/zswap/parameters/'):
                if self.unreadable:
                    raise PermissionError('kernel observation unavailable')
                return self.kernel[path.name]
            return read(path, **kwargs)
        self.enterContext(patch('common.swap_steps.Path.read_text', autospec=True, side_effect=read_text))
        def command(argv):
            self.events.append(argv)
            if 'restart' in argv and self.apply:
                self.kernel.update(enabled='Y', max_pool_percent='30')
        self.enterContext(patch('common.swap_steps.run', side_effect=command))

    def test_successful_command_cannot_hide_unchanged_kernel_policy(self):
        self.apply = False
        for kernel in ({'enabled': 'N', 'max_pool_percent': '30'},
                       {'enabled': 'Y', 'max_pool_percent': '20'}):
            with self.subTest(kernel=kernel):
                self.kernel.update(kernel)
                with self.assertRaisesRegex(RuntimeError, 'zswap.*policy'):
                    _configure_zswap(True, 30)
                self.assertTrue(self.service.is_file())

    def test_applied_policy_and_unchanged_rerun(self):
        _configure_zswap(True, 30)
        self.events.clear()
        _configure_zswap(True, 30)
        self.assertNotIn('systemctl restart basaltwater-zswap.service', self.events)
        self.assertIn('systemctl start basaltwater-zswap.service', self.events)

    def test_unknown_observation_after_start_fails(self):
        def command(argv):
            if 'restart' in argv:
                self.unreadable = True
        with patch('common.swap_steps.run', side_effect=command):
            with self.assertRaises(PermissionError):
                _configure_zswap(True, None)
        self.assertTrue(self.service.is_file())
