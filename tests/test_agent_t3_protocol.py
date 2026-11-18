"""Reviewed launcher states support repair while unknown maintenance fails closed."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import t3code_steps
from common.service_tools import user_cache_maintenance as maintenance


class T3ProtocolFourTests(unittest.TestCase):
    def test_failed_update_selection_and_retention_agree_on_reviewed_protocols(self):
        with tempfile.TemporaryDirectory() as home:
            root = Path(home, '.t3/runtime')
            binary = root / 'versions/0.1.10/t3'
            binary.parent.mkdir(parents=True)
            binary.write_text('fixture')
            binary.chmod(0o700)
            path = root / 'service-state.json'
            context = maintenance.UserContext('fixture', home, os.getuid())
            for protocol in (2, 3, 4, 5, True):
                with self.subTest(protocol=protocol):
                    state = {
                        'protocol': protocol, 'activeVersion': '0.1.9',
                        'update': {'id': 'fixture-update', 'fromVersion': '0.1.9',
                                   'targetVersion': '0.1.10', 'status': 'rolled-back'},
                    }
                    path.write_text(json.dumps(state))
                    with patch.object(maintenance, 'log_event'):
                        if type(protocol) is int and protocol in (2, 3, 4):
                            self.assertEqual(t3code_steps._retained_failed_t3_binary(home),
                                             ('0.1.10', str(binary)))
                            self.assertEqual(maintenance._t3_state(context, str(path)), state)
                        else:
                            self.assertIsNone(t3code_steps._retained_failed_t3_binary(home))
                            with self.assertRaisesRegex(ValueError, 'state protocol'):
                                maintenance._t3_state(context, str(path))

    def test_protocol_four_retention_rejects_inconsistent_update_before_cleanup(self):
        with tempfile.TemporaryDirectory() as home:
            path = Path(home, 'service-state.json')
            path.write_text(json.dumps({
                'protocol': 4, 'activeVersion': '0.1.9',
                'update': {'fromVersion': '0.1.9', 'targetVersion': '0.1.10',
                           'status': 'committed'},
            }))
            context = maintenance.UserContext('fixture', home, os.getuid())
            with self.assertRaisesRegex(ValueError, 'inconsistent T3 update state'):
                maintenance._t3_state(context, str(path))
