"""Tests for game/antistatic_steps.py."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import game.antistatic_steps as antistatic_steps
from lib.config import SetupConfig
from game.antistatic_steps import (
    ANTISTATIC_BINARY,
    ANTISTATIC_DATA_DIR,
    ANTISTATIC_DB_BINARY,
    ANTISTATIC_DB_DATA_DIR,
    ANTISTATIC_DB_PATH,
    ANTISTATIC_DB_USER,
    ANTISTATIC_SERVICE,
    ANTISTATIC_USER,
    DEFAULT_DB_INTERNAL_PORT,
    DEFAULT_INTERNAL_PORT,
    DEFAULT_STUN_PORT,
    PROXY_LISTEN_HOST,
    TRUSTED_NGINX_PROXY_CIDRS,
    _antistatic_service_listen_options,
    _configure_antistatic_environment,
    _configure_nginx_proxy,
    _maybe_configure_antistatic_firewall,
    _download_antistatic_binary,
    _download_antistatic_db_binary,
    _fetch_latest_antistatic_db_release,
    _fetch_latest_antistatic_release,
    _maybe_configure_direct_port_firewall,
    _maybe_configure_nginx_proxy,
    _remove_empty_domain_nginx_proxy,
    _require_compatible_antistatic_release,
    generate_antistatic_db_service,
    generate_antistatic_nginx_config,
    generate_antistatic_service,
    parse_antistatic_db_spec,
    parse_antistatic_spec,
)


class TestParseAntistaticSpec(unittest.TestCase):
    def test_domain_and_hostless_specs(self):
        parsers = (
            (parse_antistatic_spec, DEFAULT_INTERNAL_PORT),
            (parse_antistatic_db_spec, DEFAULT_DB_INTERNAL_PORT),
        )
        for parse, default_port in parsers:
            cases = (
                ("lobby.example.com", ("lobby.example.com", default_port)),
                ("api.example.com:9091", ("api.example.com", 9091)),
                ("lobby.example.com:9090", ("lobby.example.com", 9090)),
                ("lobby.example.com:notaport", ("lobby.example.com", default_port)),
                ("game.lobby.mysite.io:8181", ("game.lobby.mysite.io", 8181)),
                (":8080", ("", 8080)),
                ("8080", ("", 8080)),
                (":8081", ("", 8081)),
                ("8081", ("", 8081)),
            )
            for spec, expected in cases:
                with self.subTest(parser=parse.__name__, spec=spec):
                    self.assertEqual(parse(spec), expected)

    def test_strict_server_ports_reject_invalid_values(self):
        for spec, message in (
            ("lobby.example.com:notaport", "Invalid Antistatic server port"),
            (":70000", "between 1 and 65535"),
        ):
            with self.subTest(spec=spec), self.assertRaisesRegex(ValueError, message):
                parse_antistatic_spec(spec, strict=True)


class TestGenerateAntistaticServices(unittest.TestCase):
    def test_service_identity_storage_and_hardening(self):
        services = (
            (generate_antistatic_service, 9999, ANTISTATIC_USER,
             ANTISTATIC_BINARY, "antistatic", ANTISTATIC_DATA_DIR),
            (generate_antistatic_db_service, 9091, ANTISTATIC_DB_USER,
             ANTISTATIC_DB_BINARY, "antistatic-db", ANTISTATIC_DB_DATA_DIR),
        )
        for generate, port, user, binary, state, directory in services:
            with self.subTest(service=state):
                content = generate(port)
                for directive in (
                    "[Unit]", "[Service]", "[Install]",
                    "WantedBy=multi-user.target",
                    f"User={user}", f"Group={user}",
                    f"ExecStart={binary}", f"-port {port}",
                    f"StateDirectory={state}", f"WorkingDirectory={directory}",
                    "Restart=on-failure", "NoNewPrivileges=yes",
                    "PrivateTmp=yes", "ProtectSystem=strict", "ProtectHome=yes",
                ):
                    with self.subTest(directive=directive):
                        self.assertIn(directive, content)

    def test_server_defaults_include_health_check_and_persistent_reports(self):
        content = generate_antistatic_service(8080)
        for directive in (
            f"-stun-port {DEFAULT_STUN_PORT}", "-trust-proxy",
            f"Environment=ANTISTATIC_DATA_DIR={ANTISTATIC_DATA_DIR}",
            "EnvironmentFile=-/etc/antistatic/server.env",
            "http://127.0.0.1:8080/health", "TimeoutStopSec=40s",
            "UMask=0077", "StartLimitBurst=3",
        ):
            with self.subTest(directive=directive):
                self.assertIn(directive, content)

    def test_proxy_modes_bind_loopback_and_constrain_trust(self):
        server = generate_antistatic_service(
            8080, host=PROXY_LISTEN_HOST, trust_proxy=True,
        )
        self.assertIn(f"-host {PROXY_LISTEN_HOST}", server)
        self.assertIn(f"-trusted-proxy-cidrs {TRUSTED_NGINX_PROXY_CIDRS}", server)
        database = generate_antistatic_db_service(8081)
        self.assertIn("-host 127.0.0.1", database)
        self.assertIn("-trust-proxy", database)
        self.assertIn(f"-db {ANTISTATIC_DB_PATH}", database)

    def test_hostless_services_listen_directly(self):
        server = generate_antistatic_service(8080, host="", trust_proxy=False)
        self.assertIn(f"ExecStart={ANTISTATIC_BINARY} -port 8080", server)
        self.assertNotIn("-host", server)
        self.assertNotIn("-trust-proxy", server)
        self.assertNotIn("-trusted-proxy-cidrs", server)
        database = generate_antistatic_db_service(8081, host="")
        self.assertIn(f"ExecStart={ANTISTATIC_DB_BINARY} -port 8081", database)
        self.assertNotIn("-host", database)

    def test_listen_options_follow_domain_presence(self):
        for domain, expected in (
            ("lobby.example.com", (PROXY_LISTEN_HOST, True)),
            ("", ("", False)),
        ):
            with self.subTest(domain=domain):
                self.assertEqual(_antistatic_service_listen_options(domain), expected)


class TestGenerateAntistaticNginxConfig(unittest.TestCase):
    def _make_config(self, domain: str = "lobby.example.com", port: int = 8080) -> str:
        with patch(
            "lib.nginx_config.get_ssl_cert_path",
            return_value=(
                f"/etc/nginx/ssl/{domain}.crt",
                f"/etc/nginx/ssl/{domain}.key",
            ),
        ):
            return generate_antistatic_nginx_config(domain, port)

    def test_server_name(self):
        config = self._make_config("lobby.example.com", 8080)
        self.assertIn("server_name lobby.example.com;", config)

    def test_proxy_pass(self):
        config = self._make_config("lobby.example.com", 8080)
        self.assertIn("proxy_pass http://127.0.0.1:8080;", config)

    def test_acme_challenge_location(self):
        config = self._make_config()
        self.assertIn("/.well-known/acme-challenge/", config)

    def test_forwarded_headers(self):
        config = self._make_config()
        self.assertIn("proxy_set_header X-Forwarded-For $remote_addr;", config)
        self.assertNotIn("$proxy_add_x_forwarded_for", config)
        self.assertIn("X-Forwarded-Proto", config)
        self.assertIn("X-Real-IP", config)

    def test_http_redirects_to_https_by_default(self):
        config = self._make_config()
        self.assertIn("return 301 https://$host$request_uri;", config)

    def test_cloudflare_http_proxy_marks_backend_request_secure(self):
        with patch(
            "lib.nginx_config.get_ssl_cert_path",
            return_value=("/tmp/cert", "/tmp/key"),
        ):
            config = generate_antistatic_nginx_config(
                "lobby.example.com",
                8080,
                enable_https_redirect=False,
                forwarded_proto="https",
                forwarded_client_ip="$http_cf_connecting_ip",
                private_origin=True,
            )
        self.assertNotIn("return 301 https://$host$request_uri;", config)
        self.assertIn("listen 127.0.0.1:80;", config)
        self.assertIn("listen [::1]:443 ssl;", config)
        self.assertNotIn("listen [::]:80;", config)
        self.assertGreaterEqual(config.count("proxy_set_header X-Forwarded-Proto https;"), 2)
        self.assertGreaterEqual(
            config.count("proxy_set_header X-Forwarded-For $http_cf_connecting_ip;"),
            2,
        )

    def test_http2_enabled(self):
        config = self._make_config()
        self.assertIn("http2 on;", config)

    def test_admin_authentication_is_throttled_and_failure_logged(self):
        config = self._make_config()

        self.assertIn("rate=10r/m", config)
        self.assertIn("location = /admin", config)
        self.assertIn("location ^~ /admin/", config)
        self.assertIn("limit_req_status 429;", config)
        self.assertIn("$http_authorization:$status:$uri", config)
        self.assertIn("basaltwater-auth-failure", config)

    def test_listens_on_80_and_443(self):
        config = self._make_config()
        self.assertIn("listen 80;", config)
        self.assertIn("listen 443 ssl;", config)

    def test_requires_domain(self):
        with self.assertRaises(ValueError):
            generate_antistatic_nginx_config("", 8080)


class TestAntistaticHostlessNginxHandling(unittest.TestCase):
    @patch("game.antistatic_steps.os.remove")
    @patch("game.antistatic_steps.os.path.lexists", return_value=True)
    def test_removes_stale_empty_domain_nginx_config(self, _exists, mock_remove):
        _remove_empty_domain_nginx_proxy()
        mock_remove.assert_has_calls([
            call("/etc/nginx/sites-enabled/antistatic_"),
            call("/etc/nginx/sites-available/antistatic_"),
        ])

    @patch("game.antistatic_steps._remove_empty_domain_nginx_proxy")
    @patch("game.antistatic_steps._configure_nginx_proxy")
    def test_hostless_config_skips_nginx_proxy(self, mock_configure, mock_remove):
        config = SimpleNamespace()
        _maybe_configure_nginx_proxy(config, "", 8080, ANTISTATIC_SERVICE)
        mock_configure.assert_not_called()
        mock_remove.assert_called_once_with()

    @patch("game.antistatic_steps._remove_empty_domain_nginx_proxy")
    @patch("game.antistatic_steps._configure_nginx_proxy")
    def test_domain_config_uses_nginx_proxy(self, mock_configure, mock_remove):
        config = SimpleNamespace()
        _maybe_configure_nginx_proxy(config, "lobby.example.com", 8080, ANTISTATIC_SERVICE)
        mock_configure.assert_called_once_with(config, "lobby.example.com", 8080)
        mock_remove.assert_not_called()


class TestAntistaticAdminEnvironment(unittest.TestCase):
    @patch("game.antistatic_steps.os.chown")
    def test_writes_root_only_environment_file_with_escaped_values(self, mock_chown):
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            antistatic_steps,
            "ANTISTATIC_CONFIG_DIR",
            tmpdir,
        ), patch.object(
            antistatic_steps,
            "ANTISTATIC_ENV_FILE",
            os.path.join(tmpdir, "server.env"),
        ):
            config = SetupConfig(
                host="host",
                username="root",
                system_type="server_lite",
                antistatic_admin="operator",
                share_credentials=[["operator", 'secret "value" \\ end']],
            )

            _configure_antistatic_environment(config)

            env_path = os.path.join(tmpdir, "server.env")
            self.assertEqual(os.stat(env_path).st_mode & 0o777, 0o600)
            with open(env_path, "r", encoding="utf-8") as file_obj:
                content = file_obj.read()
            self.assertIn('ANTISTATIC_ADMIN_USERNAME="operator"', content)
            self.assertIn('ANTISTATIC_ADMIN_PASSWORD="secret \\"value\\" \\\\ end"', content)
            mock_chown.assert_called_once_with(tmpdir, 0, 0)

    def test_removes_environment_file_when_admin_is_disabled(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            antistatic_steps,
            "ANTISTATIC_ENV_FILE",
            os.path.join(tmpdir, "server.env"),
        ):
            env_path = os.path.join(tmpdir, "server.env")
            with open(env_path, "w", encoding="utf-8") as file_obj:
                file_obj.write("stale")
            config = SetupConfig(host="host", username="root", system_type="server_lite")

            _configure_antistatic_environment(config)

            self.assertFalse(os.path.exists(env_path))


class TestConfigureAntistaticNginx(unittest.TestCase):
    def setUp(self):
        self.root = self.enterContext(tempfile.TemporaryDirectory())
        self.available = os.path.join(self.root, 'available')
        self.enabled = os.path.join(self.root, 'enabled')
        os.mkdir(self.available)
        os.mkdir(self.enabled)
        self.enterContext(patch.object(antistatic_steps, 'NGINX_SITES_AVAILABLE_DIR', self.available))
        self.enterContext(patch.object(antistatic_steps, 'NGINX_SITES_ENABLED_DIR', self.enabled))
        self.enterContext(patch.object(antistatic_steps.os, 'fchown'))
        self.enterContext(patch.object(antistatic_steps.os, 'lchown'))
        self.write = self.enterContext(patch.object(
            antistatic_steps, 'write_text_atomic', wraps=antistatic_steps.write_text_atomic,
        ))

    @patch("web.ssl_steps.setup_certificate_renewal")
    @patch("web.ssl_steps.obtain_letsencrypt_certificate", return_value=True)
    @patch("web.ssl_steps.install_certbot")
    @patch("lib.nginx_config.generate_self_signed_cert")
    @patch("game.antistatic_steps.run")
    @patch("game.antistatic_steps.os.path.exists", return_value=True)
    def test_first_ssl_setup_regenerates_config_for_letsencrypt_certificate(
        self,
        _mock_exists,
        mock_run,
        _mock_self_signed,
        _mock_install_certbot,
        _mock_obtain_certificate,
        _mock_renewal,
    ):
        mock_run.return_value = SimpleNamespace(returncode=0)
        config = SimpleNamespace(enable_cloudflare=False, enable_ssl=True, ssl_email=None)

        with patch(
            "lib.nginx_config.get_ssl_cert_path",
            side_effect=[
                ("/etc/nginx/ssl/lobby.crt", "/etc/nginx/ssl/lobby.key"),
                (
                    "/etc/letsencrypt/live/lobby.example.com/fullchain.pem",
                    "/etc/letsencrypt/live/lobby.example.com/privkey.pem",
                ),
            ],
        ), patch(
            "game.antistatic_steps.configure_nginx_auth_failure_ban"
        ) as configure_ban:
            _configure_nginx_proxy(config, "lobby.example.com", 8080)

        final_content = self.write.call_args_list[-1].args[1]
        self.assertIn("/etc/letsencrypt/live/lobby.example.com/fullchain.pem", final_content)
        self.assertIn("return 301 https://$host$request_uri;", final_content)
        configure_ban.assert_called_once_with(
            "antistatic",
            "/var/log/nginx/basaltwater-antistatic-auth-failures.log",
        )

    @patch("lib.nginx_config.generate_self_signed_cert")
    @patch("game.antistatic_steps.run")
    @patch("game.antistatic_steps.os.path.exists", return_value=True)
    def test_non_ssl_proxy_does_not_force_self_signed_https(
        self,
        _mock_exists,
        mock_run,
        _mock_self_signed,
    ):
        mock_run.return_value = SimpleNamespace(returncode=0)
        config = SimpleNamespace(enable_cloudflare=False, enable_ssl=False, ssl_email=None)

        with patch(
            "lib.nginx_config.get_ssl_cert_path",
            return_value=("/etc/nginx/ssl/lobby.crt", "/etc/nginx/ssl/lobby.key"),
        ), patch(
            "game.antistatic_steps.configure_nginx_auth_failure_ban"
        ):
            _configure_nginx_proxy(config, "lobby.example.com", 8080)

        content = self.write.call_args.args[1]
        self.assertNotIn("return 301 https://$host$request_uri;", content)

    @patch("web.ssl_steps.setup_certificate_renewal")
    @patch("web.ssl_steps.obtain_letsencrypt_certificate", return_value=False)
    @patch("web.ssl_steps.install_certbot")
    @patch("lib.nginx_config.generate_self_signed_cert")
    @patch("game.antistatic_steps.run")
    @patch("game.antistatic_steps.os.path.exists", return_value=True)
    def test_ssl_setup_fails_when_trusted_certificate_cannot_be_obtained(
        self,
        _mock_exists,
        mock_run,
        _mock_self_signed,
        _mock_install_certbot,
        _mock_obtain_certificate,
        _mock_renewal,
    ):
        mock_run.return_value = SimpleNamespace(returncode=0)
        config = SimpleNamespace(enable_cloudflare=False, enable_ssl=True, ssl_email=None)

        with patch(
            "lib.nginx_config.get_ssl_cert_path",
            return_value=("/etc/nginx/ssl/lobby.crt", "/etc/nginx/ssl/lobby.key"),
        ), self.assertRaisesRegex(
            RuntimeError,
            "Failed to obtain a trusted certificate",
        ):
            _configure_nginx_proxy(config, "lobby.example.com", 8080)

    def test_failed_activation_restores_existing_and_new_site_entries(self):
        for state in ('new', 'disabled', 'link', 'dangling', 'file'):
            for failure in ('test', 'reload', 'exception', 'link'):
                with self.subTest(state=state, failure=failure):
                    target = os.path.join(self.available, 'antistatic_lobby_example_com')
                    enabled = os.path.join(self.enabled, 'antistatic_lobby_example_com')
                    for path in (target, enabled):
                        if os.path.lexists(path):
                            os.unlink(path)
                    if state != 'new':
                        with open(target, 'w') as stream:
                            stream.write('old site')
                        os.chmod(target, 0o640)
                    if state in ('link', 'dangling'):
                        os.symlink(target if state == 'link' else '../missing', enabled)
                    elif state == 'file':
                        with open(enabled, 'w') as stream:
                            stream.write('old enabled config')
                    calls = []

                    def activate(command, **kwargs):
                        calls.append(command)
                        if command == 'nginx -t' and calls.count(command) == 1 and failure == 'test':
                            return SimpleNamespace(returncode=1)
                        if command == 'systemctl reload nginx' and calls.count(command) == 1:
                            if failure == 'reload':
                                return SimpleNamespace(returncode=1)
                            if failure == 'exception':
                                raise OSError('reload interrupted')
                        return SimpleNamespace(returncode=0)

                    real_symlink = os.symlink
                    linked = []

                    def link(source, destination):
                        linked.append(destination)
                        if failure == 'link' and len(linked) == 1:
                            raise OSError('link activation failed')
                        return real_symlink(source, destination)

                    with patch('lib.nginx_config.generate_self_signed_cert'), \
                         patch.object(antistatic_steps, 'generate_antistatic_nginx_config', return_value='new site'), \
                         patch.object(antistatic_steps, 'run', side_effect=activate), \
                         patch.object(antistatic_steps.os, 'symlink', side_effect=link), \
                         patch.object(antistatic_steps, 'configure_nginx_auth_failure_ban') as ban:
                        with self.assertRaises((RuntimeError, OSError)):
                            _configure_nginx_proxy(SimpleNamespace(enable_ssl=False, enable_cloudflare=False),
                                                   'lobby.example.com', 8080)
                    ban.assert_not_called()
                    if state == 'new':
                        self.assertFalse(os.path.lexists(target))
                    else:
                        with open(target) as stream:
                            self.assertEqual(stream.read(), 'old site')
                        self.assertEqual(os.stat(target).st_mode & 0o777, 0o640)
                    if state in ('new', 'disabled'):
                        self.assertFalse(os.path.lexists(enabled))
                    elif state in ('link', 'dangling'):
                        self.assertEqual(os.readlink(enabled), target if state == 'link' else '../missing')
                    else:
                        with open(enabled) as stream:
                            self.assertEqual(stream.read(), 'old enabled config')


class TestAntistaticReleaseCompatibility(unittest.TestCase):
    def test_accepts_privacy_capable_release(self):
        _require_compatible_antistatic_release("v0.10.0")
        _require_compatible_antistatic_release("v1.0.0")

    def test_rejects_older_release(self):
        with self.assertRaisesRegex(RuntimeError, "v0.10.0 or newer is required"):
            _require_compatible_antistatic_release("v0.9.2")

    def test_rejects_invalid_release_tag(self):
        with self.assertRaisesRegex(RuntimeError, "Invalid antistatic-server release tag"):
            _require_compatible_antistatic_release("latest")


class TestAntistaticDirectFirewallHandling(unittest.TestCase):
    @patch("game.antistatic_steps.run")
    def test_hostless_direct_mode_allows_port_when_ufw_active(self, mock_run):
        mock_run.side_effect = [
            SimpleNamespace(returncode=0),
            SimpleNamespace(returncode=0),
        ]

        _maybe_configure_direct_port_firewall("", 8080, ANTISTATIC_SERVICE)

        mock_run.assert_has_calls(
            [
                call("ufw status 2>/dev/null | grep -q 'Status: active'", check=False),
                call(
                    "ufw allow 8080/tcp comment 'antistatic direct port'",
                    check=False,
                ),
            ]
        )

    @patch("game.antistatic_steps.run")
    def test_hostless_direct_mode_skips_port_rule_when_ufw_inactive(self, mock_run):
        mock_run.return_value = SimpleNamespace(returncode=1)

        _maybe_configure_direct_port_firewall("", 8080, ANTISTATIC_SERVICE)

        mock_run.assert_called_once_with(
            "ufw status 2>/dev/null | grep -q 'Status: active'",
            check=False,
        )

    @patch("game.antistatic_steps.run")
    def test_domain_proxy_mode_does_not_open_direct_port(self, mock_run):
        _maybe_configure_direct_port_firewall(
            "lobby.example.com",
            8080,
            ANTISTATIC_SERVICE,
        )

        mock_run.assert_not_called()


class TestAntistaticFirewallHandling(unittest.TestCase):
    @patch("game.antistatic_steps.run")
    def test_hostless_mode_allows_direct_and_stun_ports_when_ufw_active(self, mock_run):
        mock_run.side_effect = [
            SimpleNamespace(returncode=0),
            SimpleNamespace(returncode=0),
            SimpleNamespace(returncode=0),
        ]

        _maybe_configure_antistatic_firewall("", 8080)

        mock_run.assert_has_calls(
            [
                call("ufw status 2>/dev/null | grep -q 'Status: active'", check=False),
                call(
                    "ufw allow 8080/tcp comment 'antistatic direct port'",
                    check=False,
                ),
                call(
                    f"ufw allow {DEFAULT_STUN_PORT}/udp comment 'antistatic STUN'",
                    check=False,
                ),
            ]
        )

    @patch("game.antistatic_steps.run")
    def test_domain_mode_only_allows_stun_port_when_ufw_active(self, mock_run):
        mock_run.side_effect = [
            SimpleNamespace(returncode=0),
            SimpleNamespace(returncode=0),
        ]

        _maybe_configure_antistatic_firewall("lobby.example.com", 8080)

        mock_run.assert_has_calls(
            [
                call("ufw status 2>/dev/null | grep -q 'Status: active'", check=False),
                call(
                    f"ufw allow {DEFAULT_STUN_PORT}/udp comment 'antistatic STUN'",
                    check=False,
                ),
            ]
        )


class TestAntistaticReleaseDownloads(unittest.TestCase):
    SERVICES = (
        ("antistatic-server", _download_antistatic_binary, _fetch_latest_antistatic_release,
         "ANTISTATIC_BINARY", "ANTISTATIC_RELEASE_STATE_FILE"),
        ("antistatic-db", _download_antistatic_db_binary, _fetch_latest_antistatic_db_release,
         "ANTISTATIC_DB_BINARY", "ANTISTATIC_DB_RELEASE_STATE_FILE"),
    )

    @staticmethod
    def release(name, tag, digest):
        return {"tag_name": tag, "assets": [{"name": name, "digest": "sha256:" + digest,
                "browser_download_url": f"https://example.invalid/{tag}/{name}"}]}

    def test_stable_release_requires_valid_digest_for_selected_architecture(self):
        for name, _, fetch, _, _ in self.SERVICES:
            asset = f"{name}-linux-amd64"
            current = self.release(asset, "v1.2.4", "a" * 64)
            earlier = self.release(asset, "v1.2.3", "b" * 64)
            prerelease = {**self.release(asset, "v1.2.5", "c" * 64), "prerelease": True}
            with self.subTest(service=name), patch("lib.release_management.fetch_github_releases",
                                                   return_value=[prerelease, current, earlier]):
                self.assertEqual(fetch("amd64"), ("v1.2.4", current["assets"][0]["browser_download_url"], "a" * 64))
                with self.assertRaises(RuntimeError):
                    fetch("arm64")
                del current["assets"][0]["digest"]
                with self.assertRaisesRegex(RuntimeError, "digest"):
                    fetch("amd64")

    def test_install_and_verified_reuse_for_both_services(self):
        content = b"example release fixture"
        digest = hashlib.sha256(content).hexdigest()
        for name, install, _, binary_constant, state_constant in self.SERVICES:
            with self.subTest(service=name), tempfile.TemporaryDirectory() as root:
                binary, state = Path(root, name), Path(root, "release.json")
                asset = name + "-linux-amd64"
                releases = [self.release(asset, "v1.2.4", digest)]
                def download(command, **kwargs):
                    argv = shlex.split(command)
                    self.assertIn("--proto-redir", argv)
                    self.assertEqual(argv[-1], releases[0]["assets"][0]["browser_download_url"])
                    staged = Path(argv[argv.index("-o") + 1])
                    self.assertEqual((staged.parent.parent, staged.name), (Path(root), asset))
                    staged.write_bytes(content)
                    return SimpleNamespace(returncode=0)
                with (patch.object(antistatic_steps, binary_constant, str(binary)),
                      patch.object(antistatic_steps, state_constant, str(state)),
                      patch("lib.release_management.fetch_github_releases", return_value=releases),
                      patch("lib.release_management.run", side_effect=download) as run):
                    self.assertEqual(install("amd64"), "v1.2.4")
                    self.assertEqual(binary.read_bytes(), content)
                    self.assertEqual(binary.stat().st_mode & 0o777, 0o755)
                    receipt = {"tag_name": "v1.2.4", "sha256": digest}
                    self.assertEqual(json.loads(state.read_text()), receipt)
                    state.write_text(json.dumps({"tag_name": "v1.2.4"}))
                    self.assertEqual(install("amd64"), "v1.2.4")
                    self.assertEqual(json.loads(state.read_text()), receipt)
                    self.assertEqual(install("amd64"), "v1.2.4")
                    self.assertEqual(run.call_count, 1)

    def test_incompatible_server_release_is_rejected_before_install(self):
        with (patch.object(antistatic_steps, "_fetch_latest_antistatic_release",
                           return_value=("v0.9.2", "https://example.invalid/asset", "a" * 64)),
              patch.object(antistatic_steps, "install_binary_release") as install):
            with self.assertRaisesRegex(RuntimeError, "v0.10.0 or newer is required"):
                _download_antistatic_binary("amd64")
            install.assert_not_called()


if __name__ == "__main__":
    unittest.main()
