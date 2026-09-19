"""Keep local renewal scheduling separate from token-expiry readiness."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
import unittest

from lib.agent_credentials import inspect_codex_auth_payload, codex_auth_is_healthy
from lib.agent_cli import _selected_tools_readiness_healthy


class CredentialFreshnessTests(unittest.TestCase):
    def inspect(self, expiry):
        claims = {} if expiry is None else {'exp': expiry}
        encoded = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=')
        payload = json.dumps({'auth_mode': 'chatgpt', 'last_refresh': '2026-09-11T05:03:40Z',
                              'tokens': {'access_token': f'e30.{encoded}.fixture', 'refresh_token': 'private-fixture'}}).encode()
        return inspect_codex_auth_payload(payload, now=datetime(2026, 9, 19, 18, tzinfo=timezone.utc))

    def test_old_refresh_with_unexpired_access_is_due_but_does_not_fail_update(self):
        expiry = datetime(2026, 9, 21, tzinfo=timezone.utc).timestamp()
        metadata = self.inspect(expiry)
        self.assertEqual(metadata['status'], 'refresh_due')
        self.assertIn('refresh_overdue', metadata['warnings'])
        self.assertTrue(codex_auth_is_healthy(metadata))
        record = {'tools': [{'tool': 'codex', 'installed': True,
                             'credential_healthy': codex_auth_is_healthy(metadata)}]}
        self.assertTrue(_selected_tools_readiness_healthy(record, ['codex']))

    def test_expired_or_unknown_expiry_still_requires_refresh(self):
        for expiry in (1, None):
            with self.subTest(expiry=expiry):
                metadata = self.inspect(expiry)
                self.assertEqual(metadata['status'], 'refresh_required')
                self.assertFalse(codex_auth_is_healthy(metadata))
