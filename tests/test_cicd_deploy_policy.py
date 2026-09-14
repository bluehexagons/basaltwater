"""Tests for the structured nginx privilege boundary."""

from __future__ import annotations

from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lib.cicd_deploy_policy import validate_nginx_deployment, validate_nginx_path
from web.service_tools import deploy_admin


SITE = {'domain': 'example.com', 'path': '/', 'serve_path': '/var/www/site', 'project_type': 'static'}
OPERATION_ID = "0123456789abcdef0123456789abcdef"


class TestStructuredSiteValidation(unittest.TestCase):
    def test_valid_static_and_spa_routes(self) -> None:
        for kind in ('static', 'node', 'unknown'):
            request = {**SITE, 'path': '/docs/', 'project_type': kind}
            self.assertEqual(validate_nginx_deployment(request), request)

    def test_rejects_injection_and_additional_authority(self) -> None:
        for request in (
            'server {}', [], {**SITE, 'access_log': '/root/file'},
            {**SITE, 'needs_proxy': True}, {**SITE, 'domain': 'x; include /tmp/x;'},
            {**SITE, 'domain': 'example.com\n'}, {**SITE, 'domain': '../root'},
            {**SITE, 'path': '/x { return 200; }'}, {**SITE, 'path': '/$uri'},
            {**SITE, 'serve_path': '/var/www/x;'}, {**SITE, 'serve_path': '/var/www/../etc'},
            {**SITE, 'project_type': 'proxy'}, {**SITE, 'project_type': []},
        ):
            with self.subTest(request=request), self.assertRaises(ValueError):
                validate_nginx_deployment(request)

    def test_paths_reject_aliases_controls_and_nginx_metacharacters(self) -> None:
        for path in ('', '.', '/.', '/..', '/a/../b', '/a//b', '//a', '/a\n', '/a"', '/a#', '/a\\', '/a b'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_nginx_path(path)


class TestPrivilegedSiteRendering(unittest.TestCase):
    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.available = self.root / 'available'
        self.enabled = self.root / 'enabled'
        self.sites = self.root / 'sites'
        self.sites.mkdir()
        self.prefix = str(self.root / 'staged-')
        self.stack.enter_context(patch.object(deploy_admin, 'NGINX_AVAILABLE_DIR', str(self.available)))
        self.stack.enter_context(patch.object(deploy_admin, 'NGINX_ENABLED_DIR', str(self.enabled)))
        self.stack.enter_context(patch.object(deploy_admin, 'STAGED_CONFIG_PREFIX', self.prefix))
        self.stack.enter_context(patch.object(deploy_admin, '_allowed_site_roots', return_value=[str(self.sites)]))
        self.stack.enter_context(patch.dict(os.environ, {'SUDO_UID': str(os.getuid())}))
        self.run = self.stack.enter_context(patch.object(deploy_admin, '_run_checked'))
        self.cert = self.stack.enter_context(patch('lib.nginx_config.get_ssl_cert_path', return_value=('/target/site.crt', '/target/site.key')))

    def install(self, request) -> None:
        Path(
            self.prefix + f'example_com-{OPERATION_ID}.json'
        ).write_text(json.dumps(request))
        deploy_admin.install_nginx_config('example_com', OPERATION_ID)

    def test_renders_on_target_with_fixed_security_policy(self) -> None:
        self.install({**SITE, 'serve_path': str(self.sites / 'app')})
        content = (self.available / 'example_com').read_text()
        self.assertIn('ssl_certificate /target/site.crt;', content)
        self.assertEqual(content.count('disable_symlinks on;'), 2)
        self.assertIn('server_name example.com;', content)
        self.assertNotIn('proxy_pass', content)
        self.cert.assert_called_once_with('example.com')
        self.run.assert_called_once_with([deploy_admin.NGINX_BINARY, '-t'])

    def test_invalid_requests_do_not_touch_active_site_or_run_nginx(self) -> None:
        self.available.mkdir()
        active = self.available / 'example_com'
        active.write_text('previous site')
        for request in (
            'server { access_log /root/file; }',
            {**SITE, 'serve_path': str(self.sites)},
            {**SITE, 'serve_path': str(self.root / 'outside')},
            {**SITE, 'serve_path': str(self.sites / 'app'), 'domain': 'other.example'},
        ):
            with self.subTest(request=request), self.assertRaises(ValueError):
                self.install(request)
        self.assertEqual(active.read_text(), 'previous site')
        self.run.assert_not_called()
        self.cert.assert_not_called()

    def test_symlink_escape_is_rejected(self) -> None:
        (self.sites / 'app').symlink_to(self.root)
        with self.assertRaisesRegex(ValueError, 'allowed deployment base'):
            self.install({**SITE, 'serve_path': str(self.sites / 'app')})
        self.run.assert_not_called()


class TestRootOwnedSitePolicy(unittest.TestCase):
    def test_default_and_custom_roots(self) -> None:
        with patch.object(deploy_admin, '_read_regular_file', side_effect=FileNotFoundError):
            self.assertEqual(deploy_admin._allowed_site_roots(), ['/var/www'])
        with patch.object(deploy_admin, '_read_regular_file', return_value=(b'{"allowed_base_dirs":["/srv/sites"]}', 0o644, 0)):
            self.assertEqual(deploy_admin._allowed_site_roots(), ['/srv/sites'])

    def test_rejects_untrusted_or_overbroad_policy(self) -> None:
        for content, mode, owner in (
            (b'{"allowed_base_dirs":["/srv/sites"]}', 0o666, 0),
            (b'{"allowed_base_dirs":["/srv/sites"]}', 0o644, 1000),
            (b'{"allowed_base_dirs":["/"]}', 0o644, 0),
            (b'{"allowed_base_dirs":[]}', 0o644, 0),
            (b'{}', 0o644, 0),
        ):
            with self.subTest(content=content, mode=mode, owner=owner), patch.object(deploy_admin, '_read_regular_file', return_value=(content, mode, owner)), self.assertRaises(ValueError):
                deploy_admin._allowed_site_roots()
