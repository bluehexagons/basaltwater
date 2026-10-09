"""Tests for lib/nginx_config.py: SSL paths and config generation."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.nginx_config import (
    certificate_is_usable,
    get_ssl_cert_path,
    _make_cache_maps,
    _make_proxy_location,
    _make_static_location,
    _reconcile_deployment_sites,
    create_nginx_sites_for_groups,
    GENERATED_CONFIG_MARKER,
    generate_self_signed_cert,
    generate_merged_nginx_config,
    SSL_PROTOCOLS,
)
from lib import nginx_config as nginx
from lib.operation_state import OperationStateError, OperationStateStore
from lib.remote_utils import CommandTimeoutError


class TestGetSslCertPath(unittest.TestCase):
    def test_no_domain(self):
        cert, key = get_ssl_cert_path(None)
        self.assertIn('default', cert)
        self.assertIn('default', key)

    def test_domain_no_letsencrypt(self):
        cert, key = get_ssl_cert_path('example.com')
        self.assertIn('example.com', cert)
        self.assertIn('example.com', key)
        self.assertTrue(cert.endswith('.crt'))
        self.assertTrue(key.endswith('.key'))

    def test_letsencrypt_exists(self):
        domain = 'example.com'
        le_cert = f'/etc/letsencrypt/live/{domain}/fullchain.pem'
        le_key = f'/etc/letsencrypt/live/{domain}/privkey.pem'

        real_exists = os.path.exists

        def fake_exists(path):
            if path in (le_cert, le_key):
                return True
            return real_exists(path)

        with (
            patch('os.path.exists', side_effect=fake_exists),
            patch('lib.nginx_config.certificate_is_usable', return_value=True),
        ):
            cert, key = get_ssl_cert_path(domain)

        self.assertEqual(cert, le_cert)
        self.assertEqual(key, le_key)

    @patch("lib.nginx_config.certificate_is_usable", return_value=True)
    @patch("lib.nginx_config.run")
    @patch("lib.nginx_config.os.path.exists", return_value=False)
    def test_self_signed_ip_certificate_includes_ip_san(
        self, _exists, mock_run, _usable
    ):
        generate_self_signed_cert("192.168.0.51")

        command = mock_run.call_args_list[-1].args[0]
        self.assertIn("-subj /CN=192.168.0.51", command)
        self.assertIn("-addext subjectAltName=IP:192.168.0.51", command)

    @patch("lib.nginx_config.certificate_is_usable", return_value=True)
    @patch("lib.nginx_config.run")
    @patch("lib.nginx_config.os.path.exists", return_value=False)
    def test_self_signed_domain_certificate_includes_dns_san(
        self, _exists, mock_run, _usable
    ):
        generate_self_signed_cert("git.example.test")

        command = mock_run.call_args_list[-1].args[0]
        self.assertIn("-addext subjectAltName=DNS:git.example.test", command)

    @patch("lib.nginx_config.certificate_is_usable", return_value=True)
    @patch("lib.nginx_config.run")
    @patch("lib.nginx_config.os.path.exists", return_value=False)
    def test_self_signed_certificate_includes_additional_ip_san(
        self, _exists, mock_run, _usable
    ):
        generate_self_signed_cert("192.168.0.51", ["127.0.0.1"])

        command = mock_run.call_args_list[-1].args[0]
        self.assertIn(
            "-addext subjectAltName=IP:192.168.0.51,IP:127.0.0.1",
            command,
        )

    @patch("lib.nginx_config.certificate_is_usable", side_effect=[False, True])
    @patch("lib.nginx_config.run")
    @patch("lib.nginx_config.os.path.exists", return_value=True)
    def test_expiring_self_signed_certificate_is_replaced(
        self, _exists, mock_run, _usable
    ):
        generate_self_signed_cert("192.168.0.51")

        self.assertTrue(any("openssl req -x509" in call.args[0] for call in mock_run.call_args_list))


class TestCertificateIsUsable(unittest.TestCase):
    @patch("lib.nginx_config.run")
    def test_ip_identity_uses_openssl_checkip(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout="digest\n"),
            MagicMock(returncode=0, stdout="digest\n"),
        ]

        self.assertTrue(
            certificate_is_usable("cert.pem", "key.pem", ["192.168.0.51"])
        )
        self.assertIn("-checkip 192.168.0.51", mock_run.call_args_list[1].args[0])


class TestMakeCacheMaps(unittest.TestCase):
    def test_returns_maps_and_vars(self):
        maps, expires_var, cc_var = _make_cache_maps('example_com')
        self.assertIn('example_com', expires_var)
        self.assertIn('example_com', cc_var)
        self.assertIn('map', maps)
        self.assertIn('css', maps)
        self.assertIn('js', maps)


class TestMakeProxyLocation(unittest.TestCase):
    def test_root_location(self):
        result = _make_proxy_location('/', 3000, '# Backend')
        self.assertIn('proxy_pass http://127.0.0.1:3000', result)
        self.assertIn('location /', result)

    def test_subpath_location(self):
        result = _make_proxy_location('/api', 4000, '# API')
        self.assertIn('proxy_pass http://127.0.0.1:4000/', result)
        self.assertIn('location /api/', result)
        self.assertIn('return 301 /api/', result)

    def test_websocket_support(self):
        result = _make_proxy_location('/', 3000, '# WS', enable_websocket=True)
        self.assertIn('Upgrade', result)
        self.assertIn('upgrade', result)

    def test_subpath_location_can_proxy_exact_path_without_redirect(self):
        result = _make_proxy_location('/api', 4000, '# API', forwarded_proto='https', enable_path_redirect=False)
        self.assertIn('location = /api', result)
        self.assertIn('proxy_pass http://127.0.0.1:4000/', result)
        self.assertIn('proxy_set_header X-Forwarded-Proto https', result)
        self.assertNotIn('return 301 /api/', result)


class TestMakeStaticLocation(unittest.TestCase):
    def test_root_static(self):
        result = _make_static_location('/', '/var/www/html', 'index.html', '$uri =404', '# Static')
        self.assertIn('root /var/www/html', result)
        self.assertIn('location /', result)

    def test_subpath_static(self):
        result = _make_static_location('/blog', '/var/www/blog/', 'index.html', '$uri =404', '# Blog')
        self.assertIn('alias /var/www/blog/', result)
        self.assertIn('location /blog', result)


class TestGenerateMergedNginxConfig(unittest.TestCase):
    def test_basic_static_config(self):
        deployments = [{
            'path': '/',
            'needs_proxy': False,
            'serve_path': '/var/www/html',
            'project_type': 'static',
        }]
        config = generate_merged_nginx_config('example.com', deployments)
        self.assertIn(GENERATED_CONFIG_MARKER, config)
        self.assertIn('server_name example.com', config)
        self.assertIn('listen 80', config)
        self.assertIn('listen 443 ssl', config)
        self.assertIn(SSL_PROTOCOLS, config)

    def test_no_domain(self):
        deployments = [{
            'path': '/',
            'needs_proxy': False,
            'serve_path': '/var/www/html',
            'project_type': 'static',
        }]
        config = generate_merged_nginx_config(None, deployments, is_default=True)
        self.assertIn('server_name _', config)
        self.assertIn('default_server', config)

    def test_proxy_config(self):
        deployments = [{
            'path': '/',
            'needs_proxy': True,
            'proxy_port': 3000,
        }]
        config = generate_merged_nginx_config('example.com', deployments)
        self.assertIn('proxy_pass', config)

    def test_proxy_uses_backend_port_when_proxy_port_missing(self):
        deployments = [{
            'path': '/',
            'needs_proxy': True,
            'backend_port': 3007,
            'frontend_port': None,
            'frontend_serve_path': None,
        }]
        config = generate_merged_nginx_config('example.com', deployments)
        self.assertIn('proxy_pass http://127.0.0.1:3007', config)

    def test_hidden_files_denied(self):
        deployments = [{
            'path': '/',
            'needs_proxy': False,
            'serve_path': '/var/www/html',
            'project_type': 'static',
        }]
        config = generate_merged_nginx_config('example.com', deployments)
        self.assertIn('deny all', config)

    def test_acme_challenge(self):
        deployments = [{
            'path': '/',
            'needs_proxy': False,
            'serve_path': '/var/www/html',
            'project_type': 'static',
        }]
        config = generate_merged_nginx_config('example.com', deployments)
        self.assertIn('acme-challenge', config)

    def test_http2_directive_not_deprecated(self):
        """http2 should use the standalone directive, not the deprecated listen parameter."""
        deployments = [{
            'path': '/',
            'needs_proxy': False,
            'serve_path': '/var/www/html',
            'project_type': 'static',
        }]
        config = generate_merged_nginx_config('example.com', deployments)
        self.assertNotIn('listen 443 ssl http2', config)
        self.assertNotIn('listen [::]:443 ssl http2', config)
        self.assertIn('http2 on', config)


    def test_http_redirects_to_https(self):
        deployments = [{
            'path': '/',
            'needs_proxy': False,
            'serve_path': '/var/www/html',
            'project_type': 'static',
        }]
        config = generate_merged_nginx_config('example.com', deployments)
        self.assertIn('return 301 https://$host$request_uri', config)
        # ACME challenge must remain reachable on plaintext HTTP for renewals.
        http_block_end = config.find('}', config.find('listen 80'))
        http_block = config[: http_block_end]
        self.assertIn('acme-challenge', http_block)

    def test_cloudflare_tunnel_disables_http_https_redirect(self):
        deployments = [{
            'path': '/',
            'needs_proxy': False,
            'serve_path': '/var/www/html',
            'project_type': 'static',
        }]
        config = generate_merged_nginx_config('example.com', deployments, enable_https_redirect=False)
        self.assertNotIn('return 301 https://$host$request_uri', config)
        http_block_end = config.find('server {\n    listen 443 ssl')
        http_block = config[: http_block_end]
        self.assertIn('root /var/www/html', http_block)
        self.assertIn('acme-challenge', http_block)

    def test_hsts_header_present(self):
        deployments = [{
            'path': '/',
            'needs_proxy': False,
            'serve_path': '/var/www/html',
            'project_type': 'static',
        }]
        config = generate_merged_nginx_config('example.com', deployments)
        self.assertIn('Strict-Transport-Security', config)
        self.assertIn('max-age=63072000', config)


class TestReconcileDeploymentSites(unittest.TestCase):
    def setUp(self):
        for target, value in (("is_dry_run", False), ("_is_legacy_rails_site", False)):
            mocked = patch.object(nginx, target, return_value=value)
            mocked.start()
            self.addCleanup(mocked.stop)

    def test_preserves_legacy_rails_site_owned_by_existing_unit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            available = os.path.join(temp_dir, 'sites-available')
            enabled = os.path.join(temp_dir, 'sites-enabled')
            os.makedirs(available)
            os.makedirs(enabled)
            legacy_site = os.path.join(available, 'legacy_example_com')
            with open(legacy_site, 'w', encoding='utf-8') as handle:
                handle.write(f"{GENERATED_CONFIG_MARKER}\n")

            with patch('lib.nginx_config.NGINX_SITES_AVAILABLE_DIR', available), \
                 patch('lib.nginx_config.NGINX_SITES_ENABLED_DIR', enabled), \
                 patch('lib.nginx_config._is_legacy_rails_site', return_value=True):
                _reconcile_deployment_sites(set())

            self.assertTrue(os.path.exists(legacy_site))

    def test_removes_stale_deployment_sites_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            available = os.path.join(temp_dir, 'sites-available')
            enabled = os.path.join(temp_dir, 'sites-enabled')
            os.makedirs(available)
            os.makedirs(enabled)

            names = (
                'current_com', 'api_old_com', 'default',
                'antistatic_game_com', 'gogs_git_com', 'manual_site',
            )
            legacy_generated = """
map $uri $assets_expires_api_old_com {
}
map $uri $assets_cc_api_old_com {
}
server {
    location /.well-known/acme-challenge/ {
        root /var/www/letsencrypt;
    }
    add_header Strict-Transport-Security "max-age=63072000" always;
}
"""
            for directory in (available, enabled):
                for name in names:
                    with open(os.path.join(directory, name), 'w', encoding='utf-8') as handle:
                        handle.write(legacy_generated if name == 'api_old_com' else name)

            old_link = os.path.join(enabled, 'old_link_com')
            os.symlink(os.path.join(available, 'api_old_com'), old_link)

            with patch('lib.nginx_config.NGINX_SITES_AVAILABLE_DIR', available), \
                 patch('lib.nginx_config.NGINX_SITES_ENABLED_DIR', enabled):
                _reconcile_deployment_sites({'current_com', 'default'})

            for directory in (available, enabled):
                self.assertTrue(os.path.exists(os.path.join(directory, 'current_com')))
                self.assertTrue(os.path.exists(os.path.join(directory, 'default')))
                self.assertTrue(os.path.exists(os.path.join(directory, 'antistatic_game_com')))
                self.assertTrue(os.path.exists(os.path.join(directory, 'gogs_git_com')))
                self.assertTrue(os.path.exists(os.path.join(directory, 'manual_site')))
                self.assertFalse(os.path.lexists(os.path.join(directory, 'api_old_com')))
            self.assertFalse(os.path.lexists(old_link))

    @patch('lib.nginx_config.generate_self_signed_cert')
    @patch('lib.nginx_config.run')
    def test_create_sites_refuses_unmanaged_same_name(self, mock_run, _mock_cert):
        with tempfile.TemporaryDirectory() as temp_dir:
            available = os.path.join(temp_dir, 'sites-available')
            enabled = os.path.join(temp_dir, 'sites-enabled')
            os.makedirs(available)
            os.makedirs(enabled)

            enabled_link = os.path.join(enabled, 'example_com')
            with open(enabled_link, 'w', encoding='utf-8') as handle:
                handle.write('stale file')

            deployments = [{
                'path': '/',
                'needs_proxy': False,
                'serve_path': '/var/www/html',
                'project_type': 'static',
            }]

            with patch('lib.nginx_config.NGINX_SITES_AVAILABLE_DIR', available), \
                 patch('lib.nginx_config.NGINX_SITES_ENABLED_DIR', enabled):
                with self.assertRaisesRegex(RuntimeError, 'unmanaged Nginx'):
                    create_nginx_sites_for_groups(
                        {'example.com': deployments}, enable_https_redirect=False
                    )

            self.assertFalse(os.path.islink(enabled_link))
            with open(enabled_link, 'r', encoding='utf-8') as handle:
                self.assertEqual(handle.read(), 'stale file')
            mock_run.assert_not_called()

    @patch('lib.nginx_config.generate_self_signed_cert')
    @patch('lib.nginx_config.run')
    def test_create_sites_restores_previous_files_when_validation_fails(
        self, mock_run, _mock_cert
    ):
        with tempfile.TemporaryDirectory() as temp_dir:
            available = os.path.join(temp_dir, 'sites-available')
            enabled = os.path.join(temp_dir, 'sites-enabled')
            os.makedirs(available)
            os.makedirs(enabled)
            config_path = os.path.join(available, 'example_com')
            previous = f"{GENERATED_CONFIG_MARKER}\n# previous\n"
            with open(config_path, 'w', encoding='utf-8') as handle:
                handle.write(previous)
            os.symlink(config_path, os.path.join(enabled, 'example_com'))

            validations = iter((1, 0))

            def run_side_effect(command, *_args, **_kwargs):
                if command == 'nginx -t':
                    return MagicMock(returncode=next(validations))
                return MagicMock(returncode=0)

            mock_run.side_effect = run_side_effect
            deployments = [{
                'path': '/',
                'needs_proxy': False,
                'serve_path': '/var/www/new',
                'project_type': 'static',
            }]

            with patch('lib.nginx_config.NGINX_SITES_AVAILABLE_DIR', available), \
                 patch('lib.nginx_config.NGINX_SITES_ENABLED_DIR', enabled), \
                 patch('lib.nginx_config._self_signed_cert_path', side_effect=lambda name:
                       (os.path.join(temp_dir, name + '.crt'), os.path.join(temp_dir, name + '.key'))):
                with self.assertRaisesRegex(RuntimeError, 'configuration test'):
                    create_nginx_sites_for_groups(
                        {'example.com': deployments},
                        enable_https_redirect=False,
                    )

            with open(config_path, 'r', encoding='utf-8') as handle:
                self.assertEqual(handle.read(), previous)
            self.assertTrue(os.path.islink(os.path.join(enabled, 'example_com')))


class TestNginxTransaction(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.available = self.root / "sites-available"
        self.enabled = self.root / "sites-enabled"
        self.ssl = self.root / "ssl"
        for directory in (self.available, self.enabled, self.ssl):
            directory.mkdir()
        self.config = self.available / "example_com"
        self.old = (GENERATED_CONFIG_MARKER + "\n# previous\n").encode()
        self.config.write_bytes(self.old)
        self.config.chmod(0o640)
        self.link = self.enabled / "example_com"
        self.link.symlink_to(self.config)
        self.stale = self.available / "stale_com"
        self.stale.write_bytes(self.old)
        (self.enabled / "stale_com").symlink_to(self.stale)
        (self.available / "manual").write_text("unmanaged")
        self.cert = self.ssl / "example.com.crt"
        self.key = self.ssl / "example.com.key"
        self.cert.write_bytes(b"previous certificate")
        self.key.write_bytes(b"private previous key")
        self.key.chmod(0o600)
        self.marker = self.root / ".basaltwater-nginx-operation.json"
        self.groups = {"example.com": [{"path": "/", "needs_proxy": False, "serve_path": "/srv/new"}]}
        self.loaded = self.old
        self.commands = []
        self.reloads = 0
        self.fail_reload = set()
        self.fail_validation = False
        for target, kwargs in (
            ("NGINX_SITES_AVAILABLE_DIR", {"new": str(self.available)}),
            ("NGINX_SITES_ENABLED_DIR", {"new": str(self.enabled)}),
            ("is_dry_run", {"return_value": False}),
            ("_is_legacy_rails_site", {"return_value": False}),
            ("_self_signed_cert_path", {"side_effect": self.cert_paths}),
            ("get_ssl_cert_path", {"side_effect": lambda domain: self.cert_paths(domain or "default")}),
            ("generate_self_signed_cert", {"side_effect": self.generate_cert}),
            ("run", {"side_effect": self.run_command}),
        ):
            mocked = patch.object(nginx, target, **kwargs)
            mocked.start()
            self.addCleanup(mocked.stop)

    def cert_paths(self, name):
        return str(self.ssl / (name + ".crt")), str(self.ssl / (name + ".key"))

    def generate_cert(self, name):
        cert, key = self.cert_paths(name)
        Path(cert).write_bytes(b"new certificate")
        Path(key).write_bytes(b"new key")

    def run_command(self, command, **kwargs):
        self.commands.append(command)
        if command == "nginx -t" and self.fail_validation:
            self.fail_validation = False
            return subprocess.CompletedProcess(command, 1, "", "")
        if command == "systemctl reload nginx":
            self.reloads += 1
            self.loaded = self.config.read_bytes() if self.config.exists() else b"new site"
            if self.reloads in self.fail_reload:
                raise CommandTimeoutError(command, 1)
        return subprocess.CompletedProcess(command, 0, "", "")

    def apply(self):
        create_nginx_sites_for_groups(self.groups)

    def assert_restored(self):
        self.assertEqual(self.config.read_bytes(), self.old)
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o640)
        self.assertEqual(self.loaded, self.old)
        self.assertEqual(self.stale.read_bytes(), self.old)
        self.assertEqual(os.readlink(self.link), str(self.config))
        self.assertEqual(self.cert.read_bytes(), b"previous certificate")
        self.assertEqual(self.key.read_bytes(), b"private previous key")
        self.assertEqual(self.key.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.available / "manual").read_text(), "unmanaged")

    def test_success_keeps_new_files_and_result_after_durable_completion(self):
        self.apply()
        self.assertIn(b"/srv/new", self.config.read_bytes())
        self.assertEqual(self.loaded, self.config.read_bytes())
        self.assertFalse(self.marker.exists())
        self.assertFalse(self.stale.exists())
        self.assertFalse(list(self.root.glob(".basaltwater-nginx-*/")))
        result = json.loads(Path(str(self.marker) + ".last.json").read_text())
        self.assertEqual(result["outcome"], "succeeded")

    def test_validation_failure_restores_sites_tls_and_reloads_previous_config(self):
        self.fail_validation = True
        with self.assertRaisesRegex(RuntimeError, "configuration test failed"):
            self.apply()
        self.assert_restored()
        self.assertEqual(self.reloads, 1)
        self.assertFalse(self.marker.exists())

    def test_reload_timeout_after_effect_restores_loaded_previous_config(self):
        self.fail_reload = {1}
        with self.assertRaises(CommandTimeoutError):
            self.apply()
        self.assert_restored()
        self.assertEqual(self.reloads, 2)
        self.assertFalse(self.marker.exists())

    def test_failed_restore_reload_retains_private_snapshot_and_blocks_retry(self):
        self.fail_reload = {1, 2}
        with self.assertRaisesRegex(RuntimeError, "recovery was incomplete"):
            self.apply()
        record = OperationStateStore(str(self.marker)).load()
        self.assertEqual(record.status, "recovery_required")
        snapshot = Path(record.context["backup_dir"]) / "previous.json"
        previous = json.loads(snapshot.read_text())
        self.assertEqual(base64.b64decode(previous[str(self.key)]["content_base64"]), b"private previous key")
        self.assertEqual(snapshot.stat().st_mode & 0o777, 0o600)
        calls = len(self.commands)
        with self.assertRaisesRegex(OperationStateError, "Unfinished"):
            self.apply()
        self.assertEqual(len(self.commands), calls)

    def test_failed_file_restore_does_not_reload_partial_configuration(self):
        self.fail_reload = {1}
        original = nginx.write_bytes_atomic

        def write(path, content, **kwargs):
            if path == str(self.config) and content == self.old:
                raise OSError("restore failed")
            original(path, content, **kwargs)

        with patch.object(nginx, "write_bytes_atomic", side_effect=write):
            with self.assertRaisesRegex(RuntimeError, "recovery was incomplete"):
                self.apply()
        self.assertEqual(self.reloads, 1)
        self.assertEqual(self.key.read_bytes(), b"private previous key")
        self.assertTrue(self.marker.exists())

    def test_failed_finalization_preserves_snapshot_after_rollback(self):
        with patch.object(OperationStateStore, "complete", side_effect=OSError("result storage failed")):
            with self.assertRaisesRegex(OSError, "result storage failed"):
                self.apply()
        self.assert_restored()
        record = OperationStateStore(str(self.marker)).load()
        self.assertTrue((Path(record.context["backup_dir"]) / "previous.json").exists())

    def test_new_sites_and_tls_files_are_removed_on_rollback(self):
        self.groups = {"new.example.com": self.groups["example.com"]}
        self.fail_validation = True
        with self.assertRaises(RuntimeError):
            self.apply()
        self.assert_restored()
        for path in (self.available / "new_example_com", self.enabled / "new_example_com",
                     self.ssl / "new.example.com.crt", self.ssl / "new.example.com.key"):
            self.assertFalse(path.exists())

    def test_process_death_leaves_durable_snapshot_and_guard(self):
        script = '''
import os, sys
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch
from lib import nginx_config as n
root = Path(sys.argv[1])
n.NGINX_SITES_AVAILABLE_DIR = str(root / "sites-available")
n.NGINX_SITES_ENABLED_DIR = str(root / "sites-enabled")
def crash(*args, **kwargs):
    (root / "sites-available" / "example_com").write_bytes(b"interrupted replacement")
    os._exit(9)
with patch.object(n, "run", side_effect=lambda command, **kwargs: CompletedProcess(command, 0)), \\
     patch.object(n, "_self_signed_cert_path", side_effect=lambda name: (str(root / "ssl" / (name + ".crt")), str(root / "ssl" / (name + ".key")))), \\
     patch.object(n, "_write_nginx_sites_for_groups", side_effect=crash):
    n.create_nginx_sites_for_groups({"example.com": []})
'''
        result = subprocess.run([sys.executable, "-c", script, str(self.root)], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 9, result.stderr)
        record = OperationStateStore(str(self.marker)).load()
        self.assertEqual(record.phase, "applying")
        previous = json.loads((Path(record.context["backup_dir"]) / "previous.json").read_text())
        self.assertEqual(base64.b64decode(previous[str(self.config)]["content_base64"]), self.old)
        with self.assertRaises(OperationStateError):
            self.apply()

    def test_invalid_domains_special_files_and_tls_links_fail_before_mutation(self):
        for domain in ("../escape", "example.com\nother"):
            with self.subTest(domain=domain), self.assertRaises(ValueError):
                create_nginx_sites_for_groups({domain: []})
        with self.assertRaisesRegex(ValueError, "duplicate"):
            create_nginx_sites_for_groups({None: [], "default": []})
        invalid_path = self.available / "invalid\nname"
        invalid_path.write_bytes(self.old)
        with self.assertRaisesRegex(ValueError, "control"):
            self.apply()
        invalid_path.unlink()
        fifo = self.available / "pipe"
        os.mkfifo(fifo)
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            self.apply()
        fifo.unlink()
        self.key.unlink()
        self.key.symlink_to(self.cert)
        with self.assertRaisesRegex(ValueError, "must not be a symlink"):
            self.apply()
        self.assertEqual(self.config.read_bytes(), self.old)
        self.assertEqual(self.reloads, 0)

    def test_dry_run_creates_no_recovery_artifacts(self):
        with patch.object(nginx, "is_dry_run", return_value=True):
            self.apply()
        self.assert_restored()
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.commands, [])


if __name__ == '__main__':
    unittest.main()
