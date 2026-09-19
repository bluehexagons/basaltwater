"""Tests for game/antistatic_steps.py."""

from __future__ import annotations

import json
import os
import shlex
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

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
    def test_domain_only(self):
        domain, port = parse_antistatic_spec("lobby.example.com")
        self.assertEqual(domain, "lobby.example.com")
        self.assertEqual(port, DEFAULT_INTERNAL_PORT)

    def test_domain_with_port(self):
        domain, port = parse_antistatic_spec("lobby.example.com:9090")
        self.assertEqual(domain, "lobby.example.com")
        self.assertEqual(port, 9090)

    def test_domain_with_invalid_port_falls_back(self):
        domain, port = parse_antistatic_spec("lobby.example.com:notaport")
        self.assertEqual(domain, "lobby.example.com")
        self.assertEqual(port, DEFAULT_INTERNAL_PORT)

    def test_subdomain_with_port(self):
        domain, port = parse_antistatic_spec("game.lobby.mysite.io:8181")
        self.assertEqual(domain, "game.lobby.mysite.io")
        self.assertEqual(port, 8181)

    def test_port_only(self):
        domain, port = parse_antistatic_spec(":8080")
        self.assertEqual(domain, "")
        self.assertEqual(port, 8080)

    def test_numeric_spec_is_port_only(self):
        domain, port = parse_antistatic_spec("8080")
        self.assertEqual(domain, "")
        self.assertEqual(port, 8080)

    def test_strict_mode_rejects_invalid_port(self):
        with self.assertRaisesRegex(ValueError, "Invalid Antistatic server port"):
            parse_antistatic_spec("lobby.example.com:notaport", strict=True)

    def test_strict_mode_rejects_out_of_range_port(self):
        with self.assertRaisesRegex(ValueError, "between 1 and 65535"):
            parse_antistatic_spec(":70000", strict=True)


class TestParseAntistaticDbSpec(unittest.TestCase):
    def test_domain_only(self):
        domain, port = parse_antistatic_db_spec("api.example.com")
        self.assertEqual(domain, "api.example.com")
        self.assertEqual(port, DEFAULT_DB_INTERNAL_PORT)

    def test_domain_with_port(self):
        domain, port = parse_antistatic_db_spec("api.example.com:9091")
        self.assertEqual(domain, "api.example.com")
        self.assertEqual(port, 9091)

    def test_domain_with_invalid_port_falls_back(self):
        domain, port = parse_antistatic_db_spec("api.example.com:nope")
        self.assertEqual(domain, "api.example.com")
        self.assertEqual(port, DEFAULT_DB_INTERNAL_PORT)

    def test_port_only(self):
        domain, port = parse_antistatic_db_spec(":8081")
        self.assertEqual(domain, "")
        self.assertEqual(port, 8081)

    def test_numeric_spec_is_port_only(self):
        domain, port = parse_antistatic_db_spec("8081")
        self.assertEqual(domain, "")
        self.assertEqual(port, 8081)


class TestGenerateAntistaticService(unittest.TestCase):
    def test_contains_required_sections(self):
        content = generate_antistatic_service(8080)
        self.assertIn("[Unit]", content)
        self.assertIn("[Service]", content)
        self.assertIn("[Install]", content)
        self.assertIn("WantedBy=multi-user.target", content)

    def test_port_substituted(self):
        content = generate_antistatic_service(9999)
        self.assertIn("-port 9999", content)

    def test_stun_port_enabled_by_default(self):
        content = generate_antistatic_service(8080)
        self.assertIn(f"-stun-port {DEFAULT_STUN_PORT}", content)

    def test_trust_proxy_flag_present(self):
        content = generate_antistatic_service(8080)
        self.assertIn("-trust-proxy", content)

    def test_proxy_mode_binds_loopback_and_limits_trusted_proxies(self):
        content = generate_antistatic_service(
            8080,
            host=PROXY_LISTEN_HOST,
            trust_proxy=True,
        )
        self.assertIn(f"-host {PROXY_LISTEN_HOST}", content)
        self.assertIn(f"-trusted-proxy-cidrs {TRUSTED_NGINX_PROXY_CIDRS}", content)

    def test_hostless_direct_mode_listens_on_requested_port_without_proxy_trust(self):
        content = generate_antistatic_service(8080, host="", trust_proxy=False)
        self.assertIn(f"ExecStart={ANTISTATIC_BINARY} -port 8080", content)
        self.assertNotIn("-host", content)
        self.assertNotIn("-trust-proxy", content)
        self.assertNotIn("-trusted-proxy-cidrs", content)

    def test_listen_options_use_loopback_for_domain_proxy_mode(self):
        host, trust_proxy = _antistatic_service_listen_options("lobby.example.com")
        self.assertEqual(host, PROXY_LISTEN_HOST)
        self.assertTrue(trust_proxy)

    def test_listen_options_use_all_interfaces_for_hostless_direct_mode(self):
        host, trust_proxy = _antistatic_service_listen_options("")
        self.assertEqual(host, "")
        self.assertFalse(trust_proxy)

    def test_correct_user(self):
        content = generate_antistatic_service(8080)
        self.assertIn(f"User={ANTISTATIC_USER}", content)
        self.assertIn(f"Group={ANTISTATIC_USER}", content)

    def test_binary_path(self):
        content = generate_antistatic_service(8080)
        self.assertIn(f"ExecStart={ANTISTATIC_BINARY}", content)

    def test_restart_on_failure(self):
        content = generate_antistatic_service(8080)
        self.assertIn("Restart=on-failure", content)

    def test_security_hardening_directives(self):
        content = generate_antistatic_service(8080)
        self.assertIn("NoNewPrivileges=yes", content)
        self.assertIn("PrivateTmp=yes", content)
        self.assertIn("ProtectSystem=strict", content)
        self.assertIn("ProtectHome=yes", content)

    def test_persistent_report_storage_and_health_check(self):
        content = generate_antistatic_service(8080)
        self.assertIn("StateDirectory=antistatic", content)
        self.assertIn(f"WorkingDirectory={ANTISTATIC_DATA_DIR}", content)
        self.assertIn(f"Environment=ANTISTATIC_DATA_DIR={ANTISTATIC_DATA_DIR}", content)
        self.assertIn("EnvironmentFile=-/etc/antistatic/server.env", content)
        self.assertIn("http://127.0.0.1:8080/health", content)
        self.assertIn("TimeoutStopSec=40s", content)
        self.assertIn("UMask=0077", content)

    def test_start_limit_burst(self):
        content = generate_antistatic_service(8080)
        self.assertIn("StartLimitBurst=3", content)


class TestGenerateAntistaticDbService(unittest.TestCase):
    def test_contains_required_sections(self):
        content = generate_antistatic_db_service(8081)
        self.assertIn("[Unit]", content)
        self.assertIn("[Service]", content)
        self.assertIn("[Install]", content)
        self.assertIn("WantedBy=multi-user.target", content)

    def test_port_and_db_path_substituted(self):
        content = generate_antistatic_db_service(9091)
        self.assertIn("-port 9091", content)
        self.assertIn(f"-db {ANTISTATIC_DB_PATH}", content)

    def test_binds_to_loopback_and_trusts_proxy(self):
        content = generate_antistatic_db_service(8081)
        self.assertIn("-host 127.0.0.1", content)
        self.assertIn("-trust-proxy", content)

    def test_hostless_db_can_bind_all_interfaces(self):
        content = generate_antistatic_db_service(8081, host="")
        self.assertIn(f"ExecStart={ANTISTATIC_DB_BINARY} -port 8081", content)
        self.assertNotIn("-host", content)

    def test_correct_user_and_binary(self):
        content = generate_antistatic_db_service(8081)
        self.assertIn(f"User={ANTISTATIC_DB_USER}", content)
        self.assertIn(f"Group={ANTISTATIC_DB_USER}", content)
        self.assertIn(f"ExecStart={ANTISTATIC_DB_BINARY}", content)

    def test_state_directory_and_working_directory(self):
        content = generate_antistatic_db_service(8081)
        self.assertIn("StateDirectory=antistatic-db", content)
        self.assertIn(f"WorkingDirectory={ANTISTATIC_DB_DATA_DIR}", content)

    def test_security_hardening_directives(self):
        content = generate_antistatic_db_service(8081)
        self.assertIn("NoNewPrivileges=yes", content)
        self.assertIn("PrivateTmp=yes", content)
        self.assertIn("ProtectSystem=strict", content)
        self.assertIn("ProtectHome=yes", content)


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
        ) as configure_ban, patch(
            "builtins.open", unittest.mock.mock_open()
        ) as mock_file:
            _configure_nginx_proxy(config, "lobby.example.com", 8080)

        final_content = mock_file().write.call_args_list[-1].args[0]
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
        ), patch("builtins.open", unittest.mock.mock_open()) as mock_file:
            _configure_nginx_proxy(config, "lobby.example.com", 8080)

        content = mock_file().write.call_args.args[0]
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
        ), patch("builtins.open", unittest.mock.mock_open()), self.assertRaisesRegex(
            RuntimeError,
            "Failed to obtain a trusted certificate",
        ):
            _configure_nginx_proxy(config, "lobby.example.com", 8080)


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
    def test_fetch_latest_release_prefers_newest_release(self):
        release_payload = [
            {
                "tag_name": "v1.2.4",
                "published_at": "2026-05-17T12:00:00Z",
                "draft": False,
                "prerelease": False,
                "assets": [
                    {
                        "name": "antistatic-server-linux-amd64",
                        "browser_download_url": "https://example.invalid/antistatic-server-linux-amd64-fresh",
                    }
                ],
            },
            {
                "tag_name": "v1.2.3",
                "published_at": "2026-05-01T12:00:00Z",
                "draft": False,
                "prerelease": False,
                "assets": [
                    {
                        "name": "antistatic-server-linux-amd64",
                        "browser_download_url": "https://example.invalid/antistatic-server-linux-amd64",
                    }
                ],
            },
        ]

        with patch(
            "lib.release_management.run",
            return_value=MagicMock(returncode=0, stdout=json.dumps(release_payload)),
        ), patch.dict(os.environ, {"BASALTWATER_DEPENDENCY_MIN_AGE_DAYS": "7"}):
            tag_name, download_url = _fetch_latest_antistatic_release("amd64")

        self.assertEqual(tag_name, "v1.2.4")
        self.assertEqual(
            download_url,
            "https://example.invalid/antistatic-server-linux-amd64-fresh",
        )

    def test_fetch_latest_release_requires_matching_asset(self):
        release_payload = [
            {
                "tag_name": "v1.2.3",
                "published_at": "2026-05-01T12:00:00Z",
                "draft": False,
                "prerelease": False,
                "assets": [
                    {
                        "name": "antistatic-server-linux-arm64",
                        "browser_download_url": "https://example.invalid/antistatic-server-linux-arm64",
                    }
                ],
            }
        ]

        with patch(
            "lib.release_management.run",
            return_value=MagicMock(returncode=0, stdout=json.dumps(release_payload)),
        ):
            with self.assertRaises(RuntimeError):
                _fetch_latest_antistatic_release("amd64")

    @patch("game.antistatic_steps._write_installed_antistatic_release")
    @patch("lib.release_management.run")
    @patch("lib.release_management.os.path.exists", return_value=False)
    @patch("game.antistatic_steps._read_installed_antistatic_release", return_value="v1.2.2")
    @patch(
        "game.antistatic_steps._fetch_latest_antistatic_release",
        return_value=("v1.2.3", "https://example.invalid/antistatic-server-linux-amd64"),
    )
    def test_download_binary_reinstalls_when_new_release_available(
        self,
        _fetch_latest,
        _read_installed,
        _exists,
        mock_run,
        mock_write_release,
    ):
        mock_run.return_value = SimpleNamespace(returncode=0)
        tag_name = _download_antistatic_binary("amd64")

        self.assertEqual(tag_name, "v1.2.3")
        download_parts = shlex.split(mock_run.call_args_list[0].args[0])
        temporary_path = download_parts[download_parts.index("-o") + 1]
        self.assertEqual(
            os.path.basename(temporary_path),
            "antistatic-server-linux-amd64",
        )
        self.assertNotEqual(os.path.dirname(temporary_path), "/tmp")
        mock_run.assert_has_calls(
            [
                call(
                    "curl -fL --proto '=https' --proto-redir '=https' "
                    f"-o {temporary_path} https://example.invalid/antistatic-server-linux-amd64",
                    check=True,
                    display_cmd=(
                        "curl -fL --proto '=https' --proto-redir '=https' "
                        f"-o {temporary_path} <release URL>"
                    ),
                ),
                call(f"chmod +x {temporary_path}", check=True),
                call(
                    f"mv {temporary_path} /usr/local/bin/antistatic-server",
                    check=True,
                ),
            ]
        )
        mock_write_release.assert_called_once_with("v1.2.3")

    @patch("game.antistatic_steps._write_installed_antistatic_release")
    @patch("lib.release_management.run")
    @patch("lib.release_management.os.path.exists", return_value=False)
    @patch("game.antistatic_steps._read_installed_antistatic_release", return_value="v0.10.0")
    @patch(
        "game.antistatic_steps._fetch_latest_antistatic_release",
        return_value=("v0.10.1", "https://example.invalid/antistatic-server-linux-amd64"),
    )
    def test_download_failure_does_not_record_release_as_installed(
        self,
        _fetch_latest,
        _read_installed,
        _exists,
        mock_run,
        mock_write_release,
    ):
        mock_run.return_value = SimpleNamespace(returncode=1)

        with self.assertRaisesRegex(RuntimeError, "Failed to download"):
            _download_antistatic_binary("amd64")

        mock_write_release.assert_not_called()

    @patch("game.antistatic_steps._write_installed_antistatic_release")
    @patch("lib.release_management.run")
    @patch("lib.release_management.os.path.exists", return_value=True)
    @patch("game.antistatic_steps._read_installed_antistatic_release", return_value="v0.9.2")
    @patch(
        "game.antistatic_steps._fetch_latest_antistatic_release",
        return_value=("v0.9.2", "https://example.invalid/antistatic-server-linux-amd64"),
    )
    def test_incompatible_release_is_rejected_before_install(
        self,
        _fetch_latest,
        _read_installed,
        _exists,
        mock_run,
        mock_write_release,
    ):
        with self.assertRaisesRegex(RuntimeError, "v0.10.0 or newer is required"):
            _download_antistatic_binary("amd64")

        mock_run.assert_not_called()
        mock_write_release.assert_not_called()

    @patch("game.antistatic_steps._write_installed_antistatic_release")
    @patch("lib.release_management.run")
    @patch("lib.release_management.os.path.exists", return_value=True)
    @patch("game.antistatic_steps._read_installed_antistatic_release", return_value="v1.2.3")
    @patch(
        "game.antistatic_steps._fetch_latest_antistatic_release",
        return_value=("v1.2.3", "https://example.invalid/antistatic-server-linux-amd64"),
    )
    def test_download_binary_skips_reinstall_when_latest_release_already_present(
        self,
        _fetch_latest,
        _read_installed,
        _exists,
        mock_run,
        mock_write_release,
    ):
        tag_name = _download_antistatic_binary("amd64")

        self.assertEqual(tag_name, "v1.2.3")
        mock_run.assert_not_called()
        mock_write_release.assert_not_called()


class TestAntistaticDbReleaseDownloads(unittest.TestCase):
    def test_fetch_latest_release_returns_tag_and_asset_url(self):
        release_payload = [
            {
                "tag_name": "v0.1.0",
                "published_at": "2026-05-01T12:00:00Z",
                "draft": False,
                "prerelease": False,
                "assets": [
                    {
                        "name": "antistatic-db-linux-amd64",
                        "browser_download_url": "https://example.invalid/antistatic-db-linux-amd64",
                    }
                ],
            }
        ]

        with patch(
            "lib.release_management.run",
            return_value=MagicMock(returncode=0, stdout=json.dumps(release_payload)),
        ):
            tag_name, download_url = _fetch_latest_antistatic_db_release("amd64")

        self.assertEqual(tag_name, "v0.1.0")
        self.assertEqual(
            download_url,
            "https://example.invalid/antistatic-db-linux-amd64",
        )

    def test_fetch_latest_release_requires_matching_asset(self):
        release_payload = [
            {
                "tag_name": "v0.1.0",
                "published_at": "2026-05-01T12:00:00Z",
                "draft": False,
                "prerelease": False,
                "assets": [
                    {
                        "name": "antistatic-db-linux-arm64",
                        "browser_download_url": "https://example.invalid/antistatic-db-linux-arm64",
                    }
                ],
            }
        ]

        with patch(
            "lib.release_management.run",
            return_value=MagicMock(returncode=0, stdout=json.dumps(release_payload)),
        ):
            with self.assertRaises(RuntimeError):
                _fetch_latest_antistatic_db_release("amd64")

    @patch("game.antistatic_steps._write_installed_antistatic_db_release")
    @patch("lib.release_management.run")
    @patch("lib.release_management.os.path.exists", return_value=False)
    @patch("game.antistatic_steps._read_installed_antistatic_db_release", return_value="v0.0.9")
    @patch(
        "game.antistatic_steps._fetch_latest_antistatic_db_release",
        return_value=("v0.1.0", "https://example.invalid/antistatic-db-linux-amd64"),
    )
    def test_download_binary_reinstalls_when_new_release_available(
        self,
        _fetch_latest,
        _read_installed,
        _exists,
        mock_run,
        mock_write_release,
    ):
        mock_run.return_value = SimpleNamespace(returncode=0)
        tag_name = _download_antistatic_db_binary("amd64")

        self.assertEqual(tag_name, "v0.1.0")
        download_parts = shlex.split(mock_run.call_args_list[0].args[0])
        temporary_path = download_parts[download_parts.index("-o") + 1]
        self.assertEqual(
            os.path.basename(temporary_path),
            "antistatic-db-linux-amd64",
        )
        self.assertNotEqual(os.path.dirname(temporary_path), "/tmp")
        mock_run.assert_has_calls(
            [
                call(
                    "curl -fL --proto '=https' --proto-redir '=https' "
                    f"-o {temporary_path} https://example.invalid/antistatic-db-linux-amd64",
                    check=True,
                    display_cmd=(
                        "curl -fL --proto '=https' --proto-redir '=https' "
                        f"-o {temporary_path} <release URL>"
                    ),
                ),
                call(f"chmod +x {temporary_path}", check=True),
                call(
                    f"mv {temporary_path} /usr/local/bin/antistatic-db",
                    check=True,
                ),
            ]
        )
        mock_write_release.assert_called_once_with("v0.1.0")

    @patch("game.antistatic_steps._write_installed_antistatic_db_release")
    @patch("lib.release_management.run")
    @patch("lib.release_management.os.path.exists", return_value=True)
    @patch("game.antistatic_steps._read_installed_antistatic_db_release", return_value="v0.1.0")
    @patch(
        "game.antistatic_steps._fetch_latest_antistatic_db_release",
        return_value=("v0.1.0", "https://example.invalid/antistatic-db-linux-amd64"),
    )
    def test_download_binary_skips_reinstall_when_latest_release_already_present(
        self,
        _fetch_latest,
        _read_installed,
        _exists,
        mock_run,
        mock_write_release,
    ):
        tag_name = _download_antistatic_db_binary("amd64")

        self.assertEqual(tag_name, "v0.1.0")
        mock_run.assert_not_called()
        mock_write_release.assert_not_called()


if __name__ == "__main__":
    unittest.main()
