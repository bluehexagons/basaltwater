"""Tests for web.gogs_steps."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import common.common_steps as common_steps
from lib.config import SetupConfig
from lib.arg_parser import add_setup_arguments
from web import gogs_steps


class TestParseGogsSpec(unittest.TestCase):
    def test_domain_defaults_port(self):
        domain, port = gogs_steps.parse_gogs_spec("git.example.com")
        self.assertEqual((domain, port), ("git.example.com", 3000))

    def test_domain_and_port(self):
        domain, port = gogs_steps.parse_gogs_spec("git.example.com:8080", strict=True)
        self.assertEqual((domain, port), ("git.example.com", 8080))

    def test_hostless_port(self):
        domain, port = gogs_steps.parse_gogs_spec("8080", strict=True)
        self.assertEqual((domain, port), ("", 8080))

    def test_invalid_port_rejected_in_strict_mode(self):
        with self.assertRaisesRegex(ValueError, "Invalid Gogs port: nope"):
            gogs_steps.parse_gogs_spec("git.example.com:nope", strict=True)

    def test_ssl_uses_dedicated_loopback_backend_port(self):
        config = SetupConfig(
            host="192.168.0.51",
            username="admin",
            system_type="server_web",
            gogs=[":3000"],
            enable_ssl=True,
        )

        self.assertEqual(gogs_steps._gogs_backend_port(config, 3000), 13000)

    def test_ssl_backend_avoids_same_public_port(self):
        config = SetupConfig(
            host="192.168.0.51",
            username="admin",
            system_type="server_web",
            gogs=[":13000"],
            enable_ssl=True,
        )

        self.assertEqual(gogs_steps._gogs_backend_port(config, 13000), 13001)

    def test_parser_and_config_preserve_repeatable_sources(self):
        parser = argparse.ArgumentParser()
        add_setup_arguments(parser, for_remote=True, include_host=False)
        args = parser.parse_args(
            [
                "--gogs",
                ":3000",
                "/srv/gogs",
                "--gogs-source",
                "192.168.0.0/24",
                "--gogs-source",
                "10.0.0.5",
            ]
        )
        args.host = "10.0.0.41"
        args.username = "admin"
        config = SetupConfig.from_args(args, "server_web")

        self.assertEqual(config.gogs_sources, ["192.168.0.0/24", "10.0.0.5"])
        self.assertIn("--gogs-source 192.168.0.0/24", config.to_remote_args())
        self.assertIn("--gogs-source 10.0.0.5", config.to_setup_command())


class TestFetchPreferredGogsRelease(unittest.TestCase):
    @patch("lib.release_management.run")
    def test_unknown_release_architecture_is_rejected(self, mock_run):
        mock_run.return_value = SimpleNamespace(returncode=0, stdout="riscv64\n", stderr="")
        with self.assertRaisesRegex(RuntimeError, "Unsupported release architecture: riscv64"):
            gogs_steps.detect_release_arch()

    @patch("lib.release_management.run")
    def test_prefers_aged_release_when_available(self, mock_run):
        # Dates are relative to now so the test stays stable over time: the
        # newest release is within the 7-day min-age window (too fresh) and
        # the older one is well past it (aged, hence preferred).
        now = datetime.now(timezone.utc)
        fresh = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        aged = (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        payload = [
            {
                "tag_name": "v2.0.0",
                "published_at": fresh,
                "draft": False,
                "prerelease": False,
                "assets": [
                    {
                        "name": "gogs_v2.0.0_linux_amd64.tar.gz",
                        "browser_download_url": "https://example.com/v2.0.0.tgz",
                        "digest": f"sha256:{'b' * 64}",
                    }
                ],
            },
            {
                "tag_name": "v1.9.0",
                "published_at": aged,
                "draft": False,
                "prerelease": False,
                "assets": [
                    {
                        "name": "gogs_v1.9.0_linux_amd64.tar.gz",
                        "browser_download_url": "https://example.com/v1.9.0.tgz",
                        "digest": f"sha256:{'a' * 64}",
                    }
                ],
            },
        ]
        mock_run.return_value = SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

        with patch.dict(os.environ, {"BASALTWATER_DEPENDENCY_MIN_AGE_DAYS": "7"}):
            tag_name, download_url, digest = gogs_steps.fetch_preferred_gogs_release("amd64")

        self.assertEqual(tag_name, "v1.9.0")
        self.assertEqual(download_url, "https://example.com/v1.9.0.tgz")
        self.assertEqual(digest, "a" * 64)

    @patch("lib.release_management.run")
    def test_release_without_publisher_digest_is_rejected(self, mock_run):
        payload = [
            {
                "tag_name": "v1.9.0",
                "published_at": "2020-01-01T00:00:00Z",
                "draft": False,
                "prerelease": False,
                "assets": [
                    {
                        "name": "gogs_v1.9.0_linux_amd64.tar.gz",
                        "browser_download_url": "https://example.com/v1.9.0.tgz",
                    }
                ],
            }
        ]
        mock_run.return_value = SimpleNamespace(
            returncode=0, stdout=json.dumps(payload), stderr=""
        )

        with self.assertRaisesRegex(RuntimeError, "publisher-provided SHA-256"):
            gogs_steps.fetch_preferred_gogs_release("amd64")


class TestInstallGogsRelease(unittest.TestCase):
    @patch("web.gogs_steps.run")
    @patch("web.gogs_steps.os.path.exists", return_value=True)
    @patch(
        "web.gogs_steps.os.path.realpath",
        return_value=f"/opt/gogs/releases/v1.2.4-{'a' * 12}",
    )
    @patch(
        "web.gogs_steps._load_gogs_state",
        return_value={"tag_name": "v1.2.4", "archive_sha256": "a" * 64},
    )
    @patch(
        "web.gogs_steps.fetch_preferred_gogs_release",
        return_value=("v1.2.4", "https://example.com/gogs-v1.2.4.tar.gz", "a" * 64),
    )
    @patch("web.gogs_steps.detect_release_arch", return_value="amd64")
    def test_skips_only_when_installed_digest_matches(
        self,
        _arch,
        _fetch,
        _state,
        _realpath,
        _exists,
        mock_run,
    ):
        self.assertEqual(
            gogs_steps.install_or_update_gogs_release(),
            ("v1.2.4", False, "a" * 64),
        )
        mock_run.assert_not_called()

    @patch("web.gogs_steps.run")
    @patch("web.gogs_steps.os.path.exists", return_value=True)
    @patch(
        "web.gogs_steps.os.path.realpath",
        return_value=f"/opt/gogs/releases/v1.2.4-{'a' * 12}",
    )
    @patch(
        "web.gogs_steps._load_gogs_state",
        return_value={"tag_name": "v1.2.4", "archive_sha256": "b" * 64},
    )
    @patch(
        "web.gogs_steps.fetch_preferred_gogs_release",
        return_value=("v1.2.4", "https://example.com/gogs-v1.2.4.tar.gz", "a" * 64),
    )
    @patch("web.gogs_steps.detect_release_arch", return_value="amd64")
    def test_refuses_to_delete_active_release_when_state_digest_disagrees(
        self,
        _arch,
        _fetch,
        _state,
        _realpath,
        _exists,
        mock_run,
    ):
        with self.assertRaisesRegex(RuntimeError, "Refusing to replace the active"):
            gogs_steps.install_or_update_gogs_release()

        mock_run.assert_not_called()

    @patch("web.gogs_steps.run")
    @patch(
        "web.gogs_steps.fetch_preferred_gogs_release",
        return_value=("../../escape", "https://example.com/gogs.tar.gz", "a" * 64),
    )
    @patch("web.gogs_steps.detect_release_arch", return_value="amd64")
    def test_rejects_release_tag_path_traversal(self, _arch, _fetch, mock_run):
        with self.assertRaisesRegex(ValueError, "Invalid release tag"):
            gogs_steps.install_or_update_gogs_release()

        mock_run.assert_not_called()

    @patch("web.gogs_steps.run")
    @patch("web.gogs_steps.os.path.exists", return_value=False)
    @patch("web.gogs_steps.read_installed_gogs_release", return_value="v1.2.3")
    @patch(
        "web.gogs_steps.fetch_preferred_gogs_release",
        return_value=("v1.2.4", "https://example.com/gogs-v1.2.4.tar.gz", "a" * 64),
    )
    @patch("web.gogs_steps.detect_release_arch", return_value="amd64")
    def test_validates_extracted_binary_before_activating_release(
        self,
        _arch,
        _fetch,
        _installed,
        _exists,
        mock_run,
    ):
        def result_for(command: str, **_kwargs):
            stdout = f"{'a' * 64}  archive\n" if command.startswith("sha256sum ") else "v1.2.4"
            return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

        mock_run.side_effect = result_for

        tag_name, changed, digest = gogs_steps.install_or_update_gogs_release()

        self.assertEqual((tag_name, changed), ("v1.2.4", True))
        self.assertEqual(digest, "a" * 64)
        calls = mock_run.call_args_list
        version_index = next(
            index for index, call in enumerate(calls) if call.args[0].endswith("/gogs --version")
        )
        activate_index = next(
            index
            for index, call in enumerate(calls)
            if call.args[0].startswith(
                f"ln -sfn /opt/gogs/releases/v1.2.4-{'a' * 12} /opt/gogs/current"
            )
        )
        self.assertLess(version_index, activate_index)
        self.assertTrue(calls[version_index].kwargs["check"])
        self.assertTrue(calls[version_index].kwargs["capture_output"])
        self.assertEqual(
            calls[version_index].kwargs["cwd"],
            f"/opt/gogs/releases/v1.2.4-{'a' * 12}",
        )
        download_command = next(
            call.args[0]
            for call in calls
            if call.args[0].startswith("curl -fL ")
        )
        self.assertIn("/basaltwater-gogs-release-", download_command)
        self.assertNotIn("-o /tmp/gogs_", download_command)
        self.assertIn("--proto-redir '=https'", download_command)
        checksum_index = next(
            index for index, call in enumerate(calls) if call.args[0].startswith("sha256sum ")
        )
        extract_index = next(
            index for index, call in enumerate(calls) if call.args[0].startswith("tar -xzf ")
        )
        self.assertLess(checksum_index, extract_index)

    @patch("web.gogs_steps.run")
    @patch("web.gogs_steps.os.path.exists", return_value=False)
    @patch("web.gogs_steps.read_installed_gogs_release", return_value="v1.2.3")
    @patch(
        "web.gogs_steps.fetch_preferred_gogs_release",
        return_value=("v1.2.4", "https://example.com/gogs-v1.2.4.tar.gz", "a" * 64),
    )
    @patch("web.gogs_steps.detect_release_arch", return_value="amd64")
    def test_checksum_failure_stops_before_extraction_or_activation(
        self,
        _arch,
        _fetch,
        _installed,
        _exists,
        mock_run,
    ):
        def result_for(command: str, **_kwargs):
            stdout = f"{'b' * 64}  archive\n" if command.startswith("sha256sum ") else ""
            return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

        mock_run.side_effect = result_for

        with self.assertRaisesRegex(RuntimeError, "checksum"):
            gogs_steps.install_or_update_gogs_release()

        commands = [call.args[0] for call in mock_run.call_args_list]
        self.assertFalse(any(command.startswith("tar -xzf ") for command in commands))
        self.assertFalse(any("/opt/gogs/current" in command for command in commands))


class TestGenerateGogsConfig(unittest.TestCase):
    @patch("web.gogs_steps.run")
    @patch("web.gogs_steps._reject_symlinked_gogs_path")
    def test_data_directory_setup_prepares_all_writable_runtime_paths(
        self,
        _reject_symlinks,
        mock_run,
    ):
        config_path = gogs_steps._ensure_gogs_data_dirs("/srv/gogs")

        self.assertEqual(config_path, "/srv/gogs/custom/conf/app.ini")
        commands = [call.args[0] for call in mock_run.call_args_list]
        for path in (
            "/srv/gogs/data/tmp/uploads",
            "/srv/gogs/data/attachments",
            "/srv/gogs/data/avatars",
            "/srv/gogs/data/repo-avatars",
            "/srv/gogs/data/sessions",
        ):
            self.assertIn(f"mkdir -p {path}", commands)

    @patch("web.gogs_steps.run")
    def test_data_directory_setup_rejects_symlinked_subpath(self, mock_run):
        with tempfile.TemporaryDirectory() as directory:
            outside = os.path.join(directory, "outside")
            os.mkdir(outside)
            os.symlink(outside, os.path.join(directory, "custom"))

            with self.assertRaisesRegex(RuntimeError, "symlinked Gogs data path"):
                gogs_steps._ensure_gogs_data_dirs(directory)

        self.assertFalse(
            any("custom" in call.args[0] for call in mock_run.call_args_list)
        )

    def test_generate_app_ini_enables_external_ssh(self):
        config = SetupConfig(
            host="host.example.com",
            username="admin",
            system_type="server_web",
            enable_ssl=True,
            gogs=["git.example.com:3000", "/srv/gogs"],
        )

        with patch("web.gogs_steps._load_or_create_gogs_secret_key", return_value="secret-key"):
            content = gogs_steps.generate_gogs_app_ini(
                config,
                git_home="/home/git",
                data_path="/srv/gogs",
                domain="git.example.com",
                port=3000,
                backend_port=13000,
            )

        self.assertIn("EXTERNAL_URL = https://git.example.com:3000/", content)
        self.assertIn("HTTP_ADDR = 127.0.0.1", content)
        self.assertIn("HTTP_PORT = 13000", content)
        self.assertIn("LOCAL_ROOT_URL = http://127.0.0.1:13000/", content)
        self.assertIn("SSH_DOMAIN = git.example.com", content)
        self.assertIn("SSH_ROOT_PATH = /home/git/.ssh", content)
        self.assertIn("ROOT = /srv/gogs/repositories", content)
        self.assertIn("PATH = /srv/gogs/data/gogs.db", content)
        self.assertIn("[lfs]", content)
        self.assertIn("STORAGE = local", content)
        self.assertIn("OBJECTS_PATH = /srv/gogs/data/lfs-objects", content)
        self.assertIn("OBJECTS_TEMP_PATH = /srv/gogs/data/tmp/lfs-objects", content)
        self.assertIn("TEMP_PATH = /srv/gogs/data/tmp/uploads", content)
        self.assertIn("PATH = /srv/gogs/data/attachments", content)
        self.assertIn("AVATAR_UPLOAD_PATH = /srv/gogs/data/avatars", content)
        self.assertIn(
            "REPOSITORY_AVATAR_UPLOAD_PATH = /srv/gogs/data/repo-avatars",
            content,
        )
        self.assertIn("PROVIDER_CONFIG = /srv/gogs/data/sessions", content)
        self.assertIn("COOKIE_SECURE = true", content)
        self.assertIn("BRAND_NAME = Gogs", content)
        self.assertNotIn("APP_NAME =", content)
        self.assertIn("[log]\nROOT_PATH = /srv/gogs/log", content)
        self.assertIn("DISABLE_REGISTRATION = true", content)

    def test_hostless_app_ini_is_loopback_only_by_default(self):
        config = SetupConfig(
            host="10.0.0.41",
            username="admin",
            system_type="server_web",
            gogs=[":3000", "/srv/gogs"],
        )
        with patch("web.gogs_steps._load_or_create_gogs_secret_key", return_value="secret"):
            content = gogs_steps.generate_gogs_app_ini(
                config,
                git_home="/home/git",
                data_path="/srv/gogs",
                domain="",
                port=3000,
            )

        self.assertIn("HTTP_ADDR = 127.0.0.1", content)
        self.assertIn("EXTERNAL_URL = http://127.0.0.1:3000/", content)
        self.assertIn("COOKIE_SECURE = false", content)

    def test_hostless_private_source_app_ini_binds_for_firewalled_access(self):
        config = SetupConfig(
            host="10.0.0.41",
            username="admin",
            system_type="server_web",
            gogs=[":3000", "/srv/gogs"],
            gogs_sources=["192.168.0.0/24"],
        )
        with patch("web.gogs_steps._load_or_create_gogs_secret_key", return_value="secret"):
            content = gogs_steps.generate_gogs_app_ini(
                config,
                git_home="/home/git",
                data_path="/srv/gogs",
                domain="",
                port=3000,
            )

        self.assertIn("HTTP_ADDR = 0.0.0.0", content)
        self.assertIn("EXTERNAL_URL = http://10.0.0.41:3000/", content)

    def test_hostless_ssl_uses_public_tls_url_and_loopback_backend(self):
        config = SetupConfig(
            host="10.0.0.41",
            username="admin",
            system_type="server_web",
            gogs=[":3000", "/srv/gogs"],
            gogs_sources=["192.168.0.0/24"],
            enable_ssl=True,
        )
        with patch(
            "web.gogs_steps._load_or_create_gogs_secret_key",
            return_value="secret",
        ):
            content = gogs_steps.generate_gogs_app_ini(
                config,
                git_home="/home/git",
                data_path="/srv/gogs",
                domain="",
                port=3000,
                backend_port=13000,
            )

        self.assertIn("EXTERNAL_URL = https://10.0.0.41:3000/", content)
        self.assertIn("HTTP_ADDR = 127.0.0.1", content)
        self.assertIn("HTTP_PORT = 13000", content)
        self.assertIn("COOKIE_SECURE = true", content)

    def test_generic_ipv6_source_does_not_expose_ipv4_gogs_listener(self):
        config = SetupConfig(
            host="10.0.0.41",
            username="admin",
            system_type="server_web",
            gogs=[":3000", "/srv/gogs"],
            access_sources=["fc00::/7"],
        )
        with patch(
            "web.gogs_steps._load_or_create_gogs_secret_key",
            return_value="secret",
        ):
            content = gogs_steps.generate_gogs_app_ini(
                config,
                git_home="/home/git",
                data_path="/srv/gogs",
                domain="",
                port=3000,
            )

        self.assertIn("HTTP_ADDR = 127.0.0.1", content)
        self.assertIn("EXTERNAL_URL = http://127.0.0.1:3000/", content)

    def test_generate_service_uses_explicit_config_path(self):
        content = gogs_steps.generate_gogs_service("/srv/gogs/custom/conf/app.ini")
        self.assertIn("WorkingDirectory=/opt/gogs/current", content)
        self.assertIn("ExecStart=/opt/gogs/current/gogs web --config /srv/gogs/custom/conf/app.ini", content)
        self.assertIn("Environment=GOGS_WORK_DIR=/opt/gogs/current", content)
        self.assertIn("Environment=GOGS_CUSTOM=/srv/gogs/custom", content)
        self.assertIn("User=git", content)

    def test_direct_nginx_http_redirects_to_https(self):
        content = gogs_steps.generate_gogs_nginx_config(
            "git.example.test",
            13000,
            public_tls_port=3000,
            forwarded_proto="$scheme",
            redirect_plain_http=True,
        )

        self.assertEqual(content.count("listen 80;"), 1)
        self.assertIn("return 301 https://$host:3000$request_uri;", content)
        self.assertIn("listen 3000 ssl;", content)
        self.assertNotIn("listen 443 ssl;", content)
        self.assertIn("proxy_pass http://127.0.0.1:13000;", content)

    def test_cloudflare_http_origin_proxies_without_redirect(self):
        content = gogs_steps.generate_gogs_nginx_config(
            "git.example.test",
            3000,
            public_tls_port=None,
            forwarded_proto="https",
            cloudflare_origin=True,
        )

        self.assertIn("listen 80;", content)
        self.assertNotIn("listen 443", content)
        self.assertNotIn("return 301", content)
        self.assertIn("proxy_set_header X-Forwarded-Proto https;", content)

    def test_hostless_ssl_listens_on_requested_ipv4_port(self):
        content = gogs_steps.generate_gogs_nginx_config(
            "10.0.0.41",
            13000,
            public_tls_port=3000,
            forwarded_proto="$scheme",
            enable_ipv6=False,
        )

        self.assertIn("listen 0.0.0.0:3000 ssl;", content)
        self.assertNotIn("listen 443", content)
        self.assertNotIn("listen [::]", content)
        self.assertIn("proxy_pass http://127.0.0.1:13000;", content)

    def test_nginx_throttles_login_and_records_only_auth_failures(self):
        content = gogs_steps.generate_gogs_nginx_config(
            "git.example.test",
            3000,
            public_tls_port=None,
            forwarded_proto="https",
            client_ip="$http_cf_connecting_ip",
            cloudflare_origin=True,
        )

        self.assertIn("rate=5r/m", content)
        self.assertIn("/api/web/user/", content)
        self.assertIn("user/login", content)
        self.assertIn("limit_req_status 429;", content)
        self.assertIn("$http_authorization:$status", content)
        self.assertIn("basaltwater-auth-failure", content)
        self.assertIn(
            "log_format basaltwater_gogs_auth_",
            content,
        )
        self.assertNotIn("$http_authorization [$time_local]", content)
        self.assertIn(
            "proxy_set_header X-Real-IP $http_cf_connecting_ip;",
            content,
        )

    def test_redacted_admin_create_user_command_hides_password(self):
        command = gogs_steps._redacted_admin_create_user_command(
            "/srv/gogs/custom/conf/app.ini",
            "admin",
            "admin@localhost",
        )
        self.assertIn("[REDACTED]", command)
        self.assertNotIn("supersecret", command)

    @patch("web.gogs_steps._get_git_home", return_value="/home/git")
    def test_admin_command_places_config_after_subcommand(self, _git_home):
        command = gogs_steps.build_gogs_admin_command(
            ["admin", "create-user", "--name", "admin"],
            "/srv/gogs/custom/conf/app.ini",
        )

        self.assertEqual(
            command,
            "cd /opt/gogs/current && "
            "runuser -u git -- env HOME=/home/git GOGS_WORK_DIR=/opt/gogs/current "
            "GOGS_CUSTOM=/srv/gogs/custom /opt/gogs/current/gogs "
            "admin create-user --config /srv/gogs/custom/conf/app.ini --name admin",
        )

    def test_admin_command_requires_subcommand(self):
        with self.assertRaisesRegex(ValueError, "requires 'admin' and a subcommand"):
            gogs_steps.build_gogs_admin_command(
                ["admin"],
                "/srv/gogs/custom/conf/app.ini",
            )

    @patch("web.gogs_steps.run")
    def test_wait_for_gogs_ready_retries_local_http(self, mock_run):
        mock_run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")

        gogs_steps._wait_for_gogs_ready(3000)

        command = mock_run.call_args.args[0]
        self.assertIn("--retry-all-errors", command)
        self.assertIn("--retry-max-time 30", command)
        self.assertIn("http://127.0.0.1:3000/", command)

    @patch("web.gogs_steps.run")
    def test_wait_for_gogs_ready_fails_when_http_never_responds(self, mock_run):
        mock_run.return_value = SimpleNamespace(returncode=7, stdout="", stderr="failed")

        with self.assertRaisesRegex(RuntimeError, "did not become HTTP-ready"):
            gogs_steps._wait_for_gogs_ready(3000)

    @patch("web.gogs_steps.get_ssl_cert_path", return_value=("/cert.pem", "/key.pem"))
    @patch("web.gogs_steps.run")
    def test_frontend_health_checks_requested_tls_port(self, mock_run, _cert_path):
        mock_run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
        config = SetupConfig(
            host="192.168.0.51",
            username="admin",
            system_type="server_web",
            gogs=[":3000"],
            gogs_sources=["192.168.0.0/24"],
            enable_ssl=True,
        )

        gogs_steps._wait_for_gogs_frontend(config, "", 3000)

        command = mock_run.call_args.args[0]
        self.assertIn("--cacert /cert.pem", command)
        self.assertIn("--resolve 192.168.0.51:3000:127.0.0.1", command)
        self.assertIn("https://192.168.0.51:3000/", command)

    @patch("web.gogs_steps.get_ssl_cert_path", return_value=("/cert.pem", "/key.pem"))
    @patch("web.gogs_steps.run")
    def test_loopback_frontend_health_uses_certificate_loopback_san(
        self, mock_run, _cert_path
    ):
        mock_run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
        config = SetupConfig(
            host="192.168.0.51",
            username="admin",
            system_type="server_web",
            gogs=[":3000"],
            enable_ssl=True,
        )

        gogs_steps._wait_for_gogs_frontend(config, "", 3000)

        command = mock_run.call_args.args[0]
        self.assertIn("--resolve 127.0.0.1:3000:127.0.0.1", command)
        self.assertIn("https://127.0.0.1:3000/", command)

    @patch("web.gogs_steps.run")
    def test_cloudflare_frontend_health_checks_private_origin(self, mock_run):
        mock_run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
        config = SetupConfig(
            host="192.168.0.51",
            username="admin",
            system_type="server_web",
            gogs=["git.example.test:3000"],
            enable_cloudflare=True,
        )

        gogs_steps._wait_for_gogs_frontend(config, "git.example.test", 3000)

        command = mock_run.call_args.args[0]
        self.assertIn("Host: git.example.test", command)
        self.assertIn("http://127.0.0.1/", command)

    @patch("web.gogs_steps.run")
    def test_frontend_health_failure_stops_setup(self, mock_run):
        mock_run.return_value = SimpleNamespace(returncode=60, stdout="", stderr="")
        config = SetupConfig(
            host="192.168.0.51",
            username="admin",
            system_type="server_web",
            gogs=[":3000"],
            enable_ssl=True,
        )

        with self.assertRaisesRegex(RuntimeError, "public web endpoint"):
            gogs_steps._wait_for_gogs_frontend(config, "", 3000)


class TestGogsHostlessFirewall(unittest.TestCase):
    def test_source_exposure_requires_active_ufw(self):
        config = SetupConfig(
            host="10.0.0.41",
            username="admin",
            system_type="server_web",
            gogs=[":3000"],
            gogs_sources=["192.168.0.0/24"],
        )
        inactive = SimpleNamespace(returncode=1, stdout="", stderr="")
        with patch("web.gogs_steps.run", return_value=inactive):
            with self.assertRaisesRegex(RuntimeError, "requires an active UFW"):
                gogs_steps._reconcile_gogs_direct_firewall(config, 3000)

    def test_source_rules_are_verified_before_old_managed_rule_is_removed(self):
        config = SetupConfig(
            host="10.0.0.41",
            username="admin",
            system_type="server_web",
            gogs=[":3000"],
            gogs_sources=["192.168.0.0/24"],
        )
        active = SimpleNamespace(returncode=0, stdout="", stderr="")
        initial = SimpleNamespace(
            returncode=0,
            stdout=(
                "[ 1] 3000/tcp ALLOW IN Anywhere # gogs direct HTTP\n"
                "[ 2] 4000/tcp ALLOW IN 192.168.0.0/24 "
                "# basaltwater Gogs 4000/tcp source 192.168.0.0/24\n"
            ),
            stderr="",
        )
        updated = SimpleNamespace(
            returncode=0,
            stdout=(
                "[ 1] 3000/tcp ALLOW IN Anywhere # gogs direct HTTP\n"
                "[ 2] 4000/tcp ALLOW IN 192.168.0.0/24 "
                "# basaltwater Gogs 4000/tcp source 192.168.0.0/24\n"
                "[ 3] 3000/tcp ALLOW IN 192.168.0.0/24 "
                "# basaltwater Gogs 3000/tcp source 192.168.0.0/24\n"
            ),
            stderr="",
        )
        with patch(
            "web.gogs_steps.run",
            side_effect=[active, initial, active, updated, active, active],
        ) as runner:
            gogs_steps._reconcile_gogs_direct_firewall(config, 3000)

        commands = [call.args[0] for call in runner.call_args_list]
        self.assertIn("ufw allow from 192.168.0.0/24", commands[2])
        self.assertEqual(commands[-2:], ["ufw --force delete 2", "ufw --force delete 1"])

    def test_hostname_transition_removes_old_direct_and_web_rules(self):
        config = SetupConfig(
            host="git.example.test",
            username="admin",
            system_type="server_web",
            gogs=["git.example.test:3000"],
            enable_ssl=True,
        )
        active = SimpleNamespace(returncode=0, stdout="", stderr="")
        existing = SimpleNamespace(
            returncode=0,
            stdout=(
                "[ 1] 3000/tcp ALLOW IN 192.168.0.0/24 "
                "# basaltwater Gogs 3000/tcp source 192.168.0.0/24\n"
                "[ 2] 443/tcp ALLOW IN Anywhere # gogs web\n"
            ),
            stderr="",
        )
        with patch(
            "web.gogs_steps.run",
            side_effect=[active, existing, existing, active, active],
        ) as runner:
            gogs_steps._reconcile_gogs_direct_firewall(config, 3000)

        self.assertEqual(
            [call.args[0] for call in runner.call_args_list][-2:],
            ["ufw --force delete 2", "ufw --force delete 1"],
        )

    def test_inactive_ufw_does_not_retain_dormant_managed_rules(self):
        config = SetupConfig(
            host="git.example.test",
            username="admin",
            system_type="server_web",
            gogs=["git.example.test:3000"],
            enable_ssl=True,
        )
        inactive = SimpleNamespace(returncode=1, stdout="", stderr="")
        available = SimpleNamespace(returncode=0, stdout="/usr/sbin/ufw\n", stderr="")
        existing = SimpleNamespace(
            returncode=0,
            stdout="[ 1] 3000/tcp ALLOW IN Anywhere # gogs direct HTTP\n",
            stderr="",
        )
        deleted = SimpleNamespace(returncode=0, stdout="", stderr="")
        with patch(
            "web.gogs_steps.run",
            side_effect=[inactive, available, existing, deleted],
        ) as runner:
            gogs_steps._reconcile_gogs_direct_firewall(config, 3000)

        self.assertEqual(runner.call_args_list[-1].args[0], "ufw --force delete 1")


class TestGogsNginx(unittest.TestCase):
    def test_invalid_nginx_configuration_fails_setup(self):
        config = SetupConfig(
            host="git.example.test",
            username="admin",
            system_type="server_web",
            gogs=["git.example.test:3000"],
            enable_ssl=True,
        )
        completed = SimpleNamespace(returncode=0, stdout="", stderr="")
        invalid = SimpleNamespace(returncode=1, stdout="", stderr="invalid")
        with (
            patch("web.gogs_steps.generate_self_signed_cert"),
            patch("web.gogs_steps._remove_stale_gogs_nginx_sites"),
            patch("web.gogs_steps.os.path.exists", return_value=True),
            patch("web.gogs_steps.run", side_effect=[completed, invalid]),
            patch.object(gogs_steps, "open", create=True),
            patch.object(
                gogs_steps,
                "generate_gogs_nginx_config",
                return_value="server {}\n",
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "nginx configuration test"):
                gogs_steps._write_gogs_nginx_config(
                    config,
                    "git.example.test",
                    3000,
                    13000,
                )

    def test_failed_certificate_issuance_fails_setup(self):
        config = SetupConfig(
            host="git.example.test",
            username="admin",
            system_type="server_web",
            gogs=["git.example.test:3000"],
            enable_ssl=True,
        )
        completed = SimpleNamespace(returncode=0, stdout="", stderr="")
        with (
            patch("web.gogs_steps.generate_self_signed_cert"),
            patch("web.gogs_steps._remove_stale_gogs_nginx_sites"),
            patch("web.gogs_steps.os.path.exists", return_value=True),
            patch("web.gogs_steps.run", return_value=completed),
            patch("web.gogs_steps.install_certbot"),
            patch(
                "web.gogs_steps.obtain_letsencrypt_certificate",
                return_value=False,
            ),
            patch.object(gogs_steps, "open", create=True),
        ):
            with self.assertRaisesRegex(RuntimeError, "TLS certificate"):
                gogs_steps._write_gogs_nginx_config(
                    config,
                    "git.example.test",
                    3000,
                    13000,
                )


class TestGogsRequiredSetupSteps(unittest.TestCase):
    @patch("web.gogs_steps._write_admin_credentials")
    @patch("web.gogs_steps.generate_password")
    @patch("web.gogs_steps._gogs_admin_user_exists", return_value=False)
    @patch("web.gogs_steps.run")
    def test_initial_admin_uses_matching_runtime_credential(
        self,
        mock_run,
        _user_exists,
        mock_generate_password,
        mock_write_credentials,
    ):
        mock_run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
        config = SetupConfig(
            host="host",
            username="gitadmin",
            system_type="server_web",
            gogs=[":3000", "/srv/gogs"],
            share_credentials=[["gitadmin", "chosen-password"]],
        )

        gogs_steps._ensure_gogs_admin_account(
            config,
            "/srv/gogs/custom/conf/app.ini",
            "/srv/gogs",
        )

        mock_generate_password.assert_not_called()
        command = mock_run.call_args.args[0]
        display_command = mock_run.call_args.kwargs["display_cmd"]
        self.assertIn("--password chosen-password", command)
        self.assertNotIn("chosen-password", display_command)
        mock_write_credentials.assert_called_once_with("gitadmin", "chosen-password")

    @patch("web.gogs_steps._write_admin_credentials")
    @patch("web.gogs_steps.generate_password", return_value="generated-password")
    @patch("web.gogs_steps._gogs_admin_user_exists", return_value=False)
    @patch("web.gogs_steps.run")
    def test_initial_admin_generates_password_without_matching_credential(
        self,
        mock_run,
        _user_exists,
        mock_generate_password,
        mock_write_credentials,
    ):
        mock_run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
        config = SetupConfig(
            host="host",
            username="gitadmin",
            system_type="server_web",
            gogs=[":3000", "/srv/gogs"],
        )

        gogs_steps._ensure_gogs_admin_account(
            config,
            "/srv/gogs/custom/conf/app.ini",
            "/srv/gogs",
        )

        mock_generate_password.assert_called_once_with(24)
        self.assertIn("--password generated-password", mock_run.call_args.args[0])
        mock_write_credentials.assert_called_once_with("gitadmin", "generated-password")

    @patch("web.gogs_steps._write_admin_credentials")
    @patch("web.gogs_steps.run")
    @patch("web.gogs_steps._gogs_admin_user_exists", return_value=True)
    def test_existing_admin_password_is_preserved(
        self,
        _user_exists,
        mock_run,
        mock_write_credentials,
    ):
        config = SetupConfig(
            host="host",
            username="gitadmin",
            system_type="server_web",
            gogs=[":3000", "/srv/gogs"],
            share_credentials=[["gitadmin", "replacement-password"]],
        )

        gogs_steps._ensure_gogs_admin_account(
            config,
            "/srv/gogs/custom/conf/app.ini",
            "/srv/gogs",
        )

        mock_run.assert_not_called()
        mock_write_credentials.assert_not_called()

    def test_ssh_reload_failure_is_fatal(self):
        failed = SimpleNamespace(returncode=1, stdout="", stderr="failed")
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(gogs_steps, "GOGS_SSH_DROPIN_DIR", directory),
            patch.object(
                gogs_steps,
                "GOGS_SSH_DROPIN_FILE",
                os.path.join(directory, "gogs.conf"),
            ),
            patch("web.gogs_steps.run", return_value=failed),
        ):
            with self.assertRaisesRegex(RuntimeError, "Could not reload SSH"):
                gogs_steps._configure_git_ssh_access()

    def test_post_setup_hook_refresh_failure_is_fatal(self):
        failed = SimpleNamespace(returncode=1, stdout="", stderr="failed")
        with patch("web.gogs_steps.run", return_value=failed):
            with self.assertRaisesRegex(RuntimeError, "authorized_keys"):
                gogs_steps._run_gogs_post_setup_commands("/srv/gogs/app.ini")

    @patch("web.gogs_steps._get_git_home", return_value="/home/git")
    def test_post_setup_commands_leave_private_setup_directory(self, _git_home):
        completed = SimpleNamespace(returncode=0, stdout="", stderr="")
        with (
            tempfile.TemporaryDirectory(prefix="gogs release ") as directory,
            patch.object(gogs_steps, "GOGS_CURRENT_DIR", directory),
            patch("web.gogs_steps.run", return_value=completed) as runner,
        ):
            config_path = "/srv/git data/custom/conf/app.ini"
            gogs_steps._run_gogs_post_setup_commands(config_path)

        self.assertEqual(runner.call_count, 2)
        for call, subcommand in zip(
            runner.call_args_list, ("rewrite-authorized-keys", "resync-hooks")
        ):
            tokens = shlex.split(call.args[0])
            self.assertEqual(
                tokens[:8],
                ["cd", directory, "&&", "runuser", "-u", "git", "--", "env"],
            )
            self.assertEqual(tokens[-4:], ["admin", subcommand, "--config", config_path])


class TestGogsStorageHealth(unittest.TestCase):
    def test_reports_local_capacity_and_usage(self):
        with tempfile.TemporaryDirectory() as data_path:
            for relative in (
                "data/lfs-objects",
                "data/attachments",
                "repositories",
                "log",
            ):
                os.makedirs(os.path.join(data_path, relative), exist_ok=True)
            open(os.path.join(data_path, "data", "gogs.db"), "a", encoding="utf-8").close()

            def result_for(command: str, **_kwargs):
                if command.startswith("findmnt "):
                    stdout = "/dev/vdb1 ext4 /srv/gogs\n"
                elif command.startswith("sqlite3 "):
                    stdout = "ok\n"
                elif command.startswith("df "):
                    stdout = " Avail IAvail\n 1048576 2048\n"
                elif command.startswith("du "):
                    stdout = "4096 path\n"
                else:
                    stdout = ""
                return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

            with patch("web.gogs_steps.run", side_effect=result_for) as mock_run:
                health = gogs_steps.check_gogs_storage_health(data_path)

            commands = [call.args[0] for call in mock_run.call_args_list]

        self.assertEqual(health["filesystem"], "ext4")
        self.assertEqual(health["free_bytes"], 1048576)
        self.assertEqual(health["usage"]["lfs_objects"], 4096)
        self.assertFalse(
            any("data/tmp/lfs-objects" in command for command in commands)
        )
        self.assertFalse(any("data/tmp/uploads" in command for command in commands))
        repository_check = next(
            command
            for command in commands
            if "runuser -u git" in command and "/repositories" in command
        )
        self.assertIn(" -a -x ", repository_check)

    def test_rejects_cifs_live_data(self):
        result = SimpleNamespace(
            returncode=0,
            stdout="//server/share cifs /srv/gogs\n",
            stderr="",
        )
        with patch("web.gogs_steps.run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "cannot use CIFS"):
                gogs_steps.check_gogs_storage_health("/srv/gogs")


class TestGogsSetupRollback(unittest.TestCase):
    def test_restores_previous_release_and_state_after_failed_activation(self):
        completed = SimpleNamespace(returncode=0, stdout="", stderr="")
        with (
            patch("web.gogs_steps.os.path.exists", return_value=True),
            patch("web.gogs_steps.run", return_value=completed) as runner,
            patch("web.gogs_steps.write_gogs_state") as write_state,
        ):
            gogs_steps._rollback_failed_gogs_setup(
                "v1.2.3",
                "b" * 64,
                "/srv/gogs",
                "/srv/gogs/custom/conf/app.ini",
            )

        commands = [call.args[0] for call in runner.call_args_list]
        self.assertEqual(
            commands,
            [
                f"ln -sfn /opt/gogs/releases/v1.2.3-{'b' * 12} /opt/gogs/current",
                "systemctl restart gogs",
            ],
        )
        write_state.assert_called_once_with(
            "v1.2.3",
            "/srv/gogs",
            "/srv/gogs/custom/conf/app.ini",
            "b" * 64,
        )

    def test_failed_initial_install_is_stopped(self):
        completed = SimpleNamespace(returncode=0, stdout="", stderr="")
        with patch("web.gogs_steps.run", return_value=completed) as runner:
            gogs_steps._rollback_failed_gogs_setup(
                None,
                None,
                "/srv/gogs",
                "/srv/gogs/custom/conf/app.ini",
            )

        runner.assert_called_once_with("systemctl stop gogs", check=False)


class TestConfigureAutoUpdateGogs(unittest.TestCase):
    @patch("common.common_steps.configure_maintenance_timer")
    def test_configures_gogs_auto_update_timer(self, mock_configure):
        config = SetupConfig(host="host", username="user", system_type="server_web", gogs=["git.example.com"])
        common_steps.configure_auto_update_gogs(config)
        mock_configure.assert_called_once_with(
            service_name="auto-update-gogs",
            service_desc="Auto-update Gogs service",
            timer_desc="Auto-update Gogs weekly",
            script_path="/opt/basaltwater/common/service_tools/auto_update_gogs.py",
            schedule="Sun *-*-* 05:30:00",
            check_path="/usr/local/bin/gogs",
            check_name="Gogs",
            purpose="auto-update",
        )


if __name__ == "__main__":
    unittest.main()
