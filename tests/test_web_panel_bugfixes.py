"""Integration regressions for audit applicability and certificate wiring."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import web_panel_events
from common.service_tools import web_panel_audit_export as exporter
from common.service_tools import web_panel_ca_install as installers
from common.service_tools import web_panel_service as panel
from lib.machine_state import can_manage_auditd, machine_type_context


class PanelBugfixTests(unittest.TestCase):
    def test_audit_applicability_ignores_missing_or_stale_snapshots_only_when_expected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'audit.json'
            for machine in ('unprivileged', 'privileged', 'oci', 'wsl', 'vm', 'hardware'):
                with self.subTest(machine=machine), machine_type_context(machine), patch('lib.machine_state.detect_machine_type', return_value=machine):
                    applicable = machine in ('vm', 'hardware')
                    self.assertEqual(can_manage_auditd(), applicable)
                    for content in (None, json.dumps({'version': 1, 'status': 'ok',
                            'generated_at': '2000-01-01T00:00:00+00:00', 'events': []})):
                        if content:
                            path.write_text(content)
                        elif path.exists():
                            path.unlink()
                        snapshot = web_panel_events.load_audit_snapshot(str(path))
                        if applicable:
                            self.assertIn(snapshot['status'], ('unavailable', 'degraded'))
                            self.assertTrue(snapshot['issues'])
                        else:
                            self.assertEqual(snapshot['status'], 'not_applicable')
                            self.assertEqual(snapshot['issues'], [])
                            rendered = panel._render_audit_section(SimpleNamespace(audit_snapshot=lambda: snapshot))
                            self.assertIn('Not applicable', rendered)
                            self.assertNotIn('needs attention', rendered)
                            self.assertNotIn('No matching audit events were recorded', rendered)

    def test_exporter_skips_tools_only_for_inapplicable_machines(self):
        for machine in ('unprivileged', 'vm'):
            with self.subTest(machine=machine), machine_type_context(machine), patch.object(exporter, '_audit_tool', return_value=None) as tool:
                snapshot = exporter.collect_audit_snapshot()
                self.assertEqual(snapshot['status'], 'unavailable' if machine == 'vm' else 'not_applicable')
                self.assertEqual(tool.call_count, int(machine == 'vm'))

    def test_service_uses_authoritative_browser_specific_installers(self):
        self.assertIs(panel._certificate_scripts, installers._certificate_scripts)
        for host_command in ('basaltwater-web ca', 'sha256sum /example/public-ca.crt'):
            rendered = panel._render_certificate_trust({'url': 'https://example.test/basaltwater-ca.crt',
                'sha256': 'a' * 64, 'host_command': host_command})
            heading, scripts = rendered.split('<details class="trust-disclosure">', 1)
            self.assertIn('id="trust-heading"', heading)
            self.assertIn('data-trust-preset="arch:fish:chromium"', heading)
            self.assertIn('data-trust-browser', scripts)
            self.assertIn('certutil -A', scripts)
            self.assertIn('basaltwater-ca-' + 'a' * 64 + '.crt', scripts)
        for trust in ({'publicly_trusted': True}, {'status': 'unknown'}):
            self.assertNotIn('data-trust-preset', panel._render_certificate_trust(trust))
