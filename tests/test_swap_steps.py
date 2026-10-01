"""Tests for swap setup safety."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from common import swap_steps
from common.swap_steps import configure_swap
from lib.config import SetupConfig


class TestConfigureSwap(unittest.TestCase):
    def test_invalid_or_unsafe_state_blocks_swap_changes(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, 'swap.json')
            for content in ('{broken', '{"schema":true,"areas":[]}',
                            ' ' * (1024 * 1024 + 1)):
                with open(path, 'w') as state_file:
                    state_file.write(content)
                with (self.subTest(content=content[:30]), patch.object(swap_steps, 'SWAP_STATE_FILE', path),
                      patch.object(swap_steps, 'is_dry_run', return_value=False),
                      patch.object(swap_steps, 'can_manage_swap', return_value=True),
                      patch.object(swap_steps, 'run') as run):
                    with self.assertRaises(RuntimeError):
                        configure_swap(SetupConfig(host='vm', username='agent', system_type='server_web', swap_mode='none'))
                    run.assert_not_called()
                    with open(path) as state_file:
                        self.assertEqual(state_file.read(), content)
            link, fifo = os.path.join(root, 'link'), os.path.join(root, 'fifo')
            os.symlink(path, link)
            os.mkfifo(fifo)
            for unsafe in (link, fifo, root):
                with self.subTest(path=unsafe), patch.object(swap_steps, 'SWAP_STATE_FILE', unsafe):
                    with self.assertRaisesRegex(RuntimeError, 'Could not read'):
                        swap_steps._load_state()

    @patch("common.swap_steps.run")
    @patch("common.swap_steps.can_manage_swap")
    def test_dry_run_does_not_inspect_or_mutate_swap(self, mock_can_manage, mock_run):
        with patch("builtins.print") as mock_print:
            configure_swap(
                SetupConfig(
                    username="agent",
                    host="vm",
                    system_type="agent_code_vm",
                    dry_run=True,
                    swap_devices=[["fast", "UUID=abcd1234"]],
                )
            )

        mock_can_manage.assert_not_called()
        mock_run.assert_not_called()
        self.assertIn("declared areas=1", mock_print.call_args.args[0])

    @patch("common.swap_steps.run")
    @patch("common.swap_steps.can_manage_swap")
    def test_proxmox_does_not_create_a_generic_swap_file(self, mock_can_manage, mock_run):
        with patch("builtins.print") as mock_print:
            configure_swap(
                SetupConfig(username="root", host="pve1", system_type="server_proxmox")
            )

        mock_can_manage.assert_not_called()
        mock_run.assert_not_called()
        mock_print.assert_called_once_with(
            "  ✓ Preserving Proxmox host swap layout"
        )


if __name__ == "__main__":
    unittest.main()
