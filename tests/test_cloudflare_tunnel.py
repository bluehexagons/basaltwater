"""Tests for the Cloudflare Tunnel provisioning helper."""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import call, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from web.service_tools import setup_cloudflare_tunnel as tunnel_setup


class TestCloudflareTunnelState(unittest.TestCase):
    def test_missing_state_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(tunnel_setup, "STATE_FILE", os.path.join(tmp, "missing.json")):
                self.assertIsNone(tunnel_setup.load_state())

    def test_malformed_state_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = os.path.join(tmp, "tunnel-state.json")
            with open(state_path, "w", encoding="utf-8") as state_file:
                state_file.write("not-json")
            with patch.object(tunnel_setup, "STATE_FILE", state_path):
                with self.assertRaisesRegex(ValueError, "Could not load"):
                    tunnel_setup.load_state()

    def test_generated_yaml_quotes_values_and_validates_hosts(self):
        content = tunnel_setup.generate_config_yml(
            {
                "name": "basaltwater",
                "id": "tunnel-id",
                "credentials_file": "/etc/cloudflared/tunnel.json",
            },
            [{"hostname": "example.com", "service": "http://localhost:80"}],
        )
        self.assertIn('tunnel: "tunnel-id"', content)
        self.assertIn('credentials-file: "/etc/cloudflared/tunnel.json"', content)
        self.assertIn('hostname: "example.com"', content)

        with self.assertRaisesRegex(ValueError, "Invalid tunnel hostname"):
            tunnel_setup.generate_config_yml(
                {
                    "id": "tunnel-id",
                    "credentials_file": "/etc/cloudflared/tunnel.json",
                },
                [{"hostname": "bad;hostname", "service": "http://localhost:80"}],
            )

    def test_state_validation_rejects_invalid_shapes(self):
        cases = (
            ([], "JSON object"),
            ({"tunnel": {}, "sites": []}, "invalid name"),
            (
                {
                    "tunnel": {
                        "name": "basaltwater",
                        "id": "tunnel-id",
                        "credentials_file": "relative.json",
                    },
                    "sites": [],
                },
                "credentials path must be absolute",
            ),
            (
                {
                    "tunnel": {
                        "name": "basaltwater",
                        "id": "tunnel-id",
                        "credentials_file": "/etc/cloudflared/tunnel.json",
                    },
                    "sites": "not-a-list",
                },
                "missing its sites list",
            ),
            (
                {
                    "tunnel": {
                        "name": "basaltwater",
                        "id": "tunnel-id",
                        "credentials_file": "/etc/cloudflared/tunnel.json",
                    },
                    "sites": [{"hostname": "example.com", "service": "https://origin"}],
                },
                "unsupported origin",
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            state_path = os.path.join(tmp, "tunnel-state.json")
            for state, message in cases:
                with self.subTest(message=message):
                    with open(state_path, "w", encoding="utf-8") as state_file:
                        json.dump(state, state_file)
                    with patch.object(tunnel_setup, "STATE_FILE", state_path):
                        with self.assertRaisesRegex(ValueError, message):
                            tunnel_setup.load_state()

    def test_discovery_deduplicates_and_skips_invalid_hosts(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "site.conf"), "w", encoding="utf-8") as config:
                config.write(
                    "server_name example.com example.com invalid_host _;\n"
                )
            with patch.object(tunnel_setup, "NGINX_SITES_DIR", tmp):
                sites = tunnel_setup.discover_nginx_sites()
        self.assertEqual(
            sites,
            [{"hostname": "example.com", "service": "http://localhost:80"}],
        )

    def test_generate_config_rejects_unsupported_origin(self):
        with self.assertRaisesRegex(ValueError, "Unsupported tunnel origin"):
            tunnel_setup.generate_config_yml(
                {"id": "tunnel-id", "credentials_file": "/etc/cloudflared/tunnel.json"},
                [{"hostname": "example.com", "service": "https://origin"}],
            )


class TestCloudflareTunnelRefresh(unittest.TestCase):
    def test_write_config_validation_failure_preserves_existing_file_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = os.path.join(tmp, "config.yml")
            with open(config_path, "w", encoding="utf-8") as config_file:
                config_file.write("old config\n")
            with patch.object(
                tunnel_setup,
                "run_command",
                return_value=SimpleNamespace(
                    returncode=1, stdout="", stderr="invalid ingress"
                ),
            ) as run_command:
                with self.assertRaisesRegex(ValueError, "invalid ingress"):
                    tunnel_setup._write_config_file(config_path, "new config\n")

            with open(config_path, encoding="utf-8") as config_file:
                self.assertEqual(config_file.read(), "old config\n")
            temporary_files = [
                name for name in os.listdir(tmp) if name.startswith(".cloudflared-config-")
            ]
            self.assertEqual(temporary_files, [])
            self.assertEqual(run_command.call_count, 1)
            self.assertNotEqual(run_command.call_args.args[0][-1], config_path)

    def test_write_config_success_replaces_file_with_private_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = os.path.join(tmp, "config.yml")
            with patch.object(
                tunnel_setup,
                "run_command",
                return_value=SimpleNamespace(returncode=0, stdout="", stderr=""),
            ):
                tunnel_setup._write_config_file(config_path, "new config\n")

            with open(config_path, encoding="utf-8") as config_file:
                self.assertEqual(config_file.read(), "new config\n")
            self.assertEqual(stat.S_IMODE(os.stat(config_path).st_mode), 0o600)

    def test_existing_config_validation_distinguishes_missing_invalid_and_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = os.path.join(tmp, "config.yml")
            with patch.object(tunnel_setup, "run_command") as run_command:
                self.assertFalse(tunnel_setup._config_is_valid(config_path))
                run_command.assert_not_called()

            with open(config_path, "w", encoding="utf-8") as config_file:
                config_file.write("config\n")
            for returncode, expected in ((1, False), (0, True)):
                with self.subTest(returncode=returncode):
                    with patch.object(
                        tunnel_setup,
                        "run_command",
                        return_value=SimpleNamespace(
                            returncode=returncode,
                            stdout="",
                            stderr="invalid" if returncode else "",
                        ),
                    ) as run_command:
                        self.assertEqual(tunnel_setup._config_is_valid(config_path), expected)
                    run_command.assert_called_once()

    def test_ensure_service_running_accepts_active_service_without_starting(self):
        with patch.object(
            tunnel_setup,
            "run_command",
            return_value=SimpleNamespace(returncode=0, stdout="active", stderr=""),
        ) as run_command:
            self.assertTrue(tunnel_setup._ensure_service_running())

        run_command.assert_called_once_with(
            ["systemctl", "is-active", "cloudflared"],
            check=False,
            capture_output=True,
        )

    def test_ensure_service_running_reports_start_and_activation_failures(self):
        cases = (
            (
                [
                    SimpleNamespace(returncode=3, stdout="", stderr="inactive"),
                    SimpleNamespace(returncode=1, stdout="", stderr="denied"),
                ],
                False,
            ),
            (
                [
                    SimpleNamespace(returncode=3, stdout="", stderr="inactive"),
                    SimpleNamespace(returncode=0, stdout="", stderr=""),
                    SimpleNamespace(returncode=3, stdout="", stderr="still inactive"),
                ],
                False,
            ),
            (
                [
                    SimpleNamespace(returncode=3, stdout="", stderr="inactive"),
                    SimpleNamespace(returncode=0, stdout="", stderr=""),
                    SimpleNamespace(returncode=0, stdout="active", stderr=""),
                ],
                True,
            ),
        )
        for results, expected in cases:
            with self.subTest(expected=expected, calls=len(results)):
                with patch.object(tunnel_setup, "run_command", side_effect=results) as run_command:
                    self.assertEqual(tunnel_setup._ensure_service_running(), expected)
                self.assertEqual(run_command.call_count, len(results))

    def test_close_public_web_ports_installs_ufw_when_missing(self):
        with patch.object(tunnel_setup.shutil, "which", return_value=None), patch.object(
            tunnel_setup, "run_command"
        ) as run_command:
            tunnel_setup._close_public_web_ports()

        self.assertEqual(
            run_command.call_args_list[:2],
            [call(["apt-get", "update"]), call(["apt-get", "install", "-y", "ufw"])],
        )
        self.assertIn(
            call(["ufw", "--force", "enable"]),
            run_command.call_args_list,
        )

    def test_non_interactive_refresh_returns_false_when_no_sites_are_discovered(self):
        with patch.object(tunnel_setup, "check_root"), patch.object(
            tunnel_setup, "load_state", return_value={"tunnel": {}, "sites": []}
        ), patch.object(tunnel_setup, "install_cloudflared") as install_cloudflared, patch.object(
            tunnel_setup, "discover_nginx_sites", return_value=[]
        ):
            self.assertFalse(tunnel_setup.main(interactive=False))

        install_cloudflared.assert_called_once()

    def test_non_interactive_refresh_skips_config_write_when_nothing_changed(self):
        state = {
            "tunnel": {
                "name": "basaltwater",
                "id": "tunnel-id",
                "credentials_file": "/etc/cloudflared/tunnel.json",
            },
            "sites": [{"hostname": "example.com", "service": "http://localhost:80"}],
        }
        with patch.object(tunnel_setup, "check_root"), patch.object(
            tunnel_setup, "load_state", return_value=state
        ), patch.object(tunnel_setup, "install_cloudflared"), patch.object(
            tunnel_setup,
            "discover_nginx_sites",
            return_value=[{"hostname": "example.com", "service": "http://localhost:80"}],
        ), patch.object(tunnel_setup, "_config_is_valid", return_value=True), patch.object(
            tunnel_setup.os.path, "isfile", return_value=True
        ), patch.object(tunnel_setup, "_write_config_file") as write_config, patch.object(
            tunnel_setup, "save_state"
        ) as save_state, patch.object(
            tunnel_setup, "_ensure_service_running", return_value=True
        ):
            self.assertTrue(tunnel_setup.main(interactive=False))

        write_config.assert_not_called()
        save_state.assert_not_called()

    def test_non_interactive_update_converts_setup_errors_to_false(self):
        with patch.object(tunnel_setup, "main", side_effect=RuntimeError("broken setup")):
            self.assertFalse(tunnel_setup.run_non_interactive_update())

    def test_non_interactive_refresh_writes_validated_config_and_checks_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = os.path.join(tmp, "cloudflared")
            nginx_dir = os.path.join(tmp, "sites-enabled")
            os.makedirs(nginx_dir)
            with open(os.path.join(nginx_dir, "site.conf"), "w", encoding="utf-8") as config:
                config.write("server_name example.com;\n")
            credentials = os.path.join(config_dir, "tunnel.json")
            os.makedirs(config_dir)
            with open(credentials, "w", encoding="utf-8") as credential_file:
                credential_file.write("{}")
            state_path = os.path.join(config_dir, "tunnel-state.json")
            with open(state_path, "w", encoding="utf-8") as state_file:
                json.dump(
                    {
                        "tunnel": {
                            "name": "basaltwater",
                            "id": "tunnel-id",
                            "credentials_file": credentials,
                        },
                        "sites": [],
                    },
                    state_file,
                )

            command_results = [
                SimpleNamespace(returncode=0, stdout="", stderr=""),
                SimpleNamespace(returncode=0, stdout="", stderr=""),
            ]
            with patch.object(tunnel_setup, "STATE_FILE", state_path), \
                    patch.object(tunnel_setup, "CONFIG_DIR", config_dir), \
                    patch.object(tunnel_setup, "NGINX_SITES_DIR", nginx_dir), \
                    patch.object(tunnel_setup, "check_root"), \
                    patch.object(tunnel_setup, "install_cloudflared"), \
                    patch.object(tunnel_setup, "run_command", side_effect=command_results):
                self.assertTrue(tunnel_setup.main(interactive=False))

            with open(os.path.join(config_dir, "config.yml"), encoding="utf-8") as config:
                self.assertIn("example.com", config.read())
            with open(state_path, encoding="utf-8") as state_file:
                self.assertEqual(json.load(state_file)["sites"][0]["hostname"], "example.com")

    def test_cloudflared_install_uses_signed_apt_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            keyring = os.path.join(tmp, "cloudflare-main.gpg")
            source = os.path.join(tmp, "cloudflared.list")
            with patch.object(tunnel_setup.shutil, "which", return_value=None), \
                    patch.object(tunnel_setup, "CLOUDFLARE_APT_KEYRING", keyring), \
                    patch.object(tunnel_setup, "CLOUDFLARE_APT_SOURCE", source), \
                    patch.object(
                        tunnel_setup,
                        "run_command",
                        return_value=SimpleNamespace(returncode=0, stdout="", stderr=""),
                    ) as mock_run:
                tunnel_setup.install_cloudflared()

            commands = [mock_call.args[0] for mock_call in mock_run.call_args_list]
            self.assertIn(["apt-get", "install", "-y", "cloudflared"], commands)
            self.assertFalse(any(command[:1] == ["dpkg"] for command in commands))
            with open(source, encoding="utf-8") as source_file:
                self.assertIn("signed-by=", source_file.read())


if __name__ == "__main__":
    unittest.main()
