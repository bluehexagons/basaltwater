"""Tests for the Cloudflare Tunnel provisioning helper."""

from __future__ import annotations

import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import call, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from web.service_tools import setup_cloudflare_tunnel as tunnel_setup


class TestCloudflareTunnelState(unittest.TestCase):
    def test_installed_symlink_resolves_runtime_imports(self):
        with tempfile.TemporaryDirectory() as tmp:
            helper = os.path.join(tmp, "setup-cloudflare-tunnel")
            os.symlink(tunnel_setup.__file__, helper)
            result = subprocess.run(
                [sys.executable, "-I", helper, "--help"],
                cwd=tmp, capture_output=True, text=True, timeout=10,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Cloudflare Tunnel Setup", result.stdout)

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
    def test_initial_creation_uses_suggested_name_and_only_closes_ports_after_activation(self):
        tunnel = {"name": "example_droplet", "id": "id", "credentials_file": "/etc/cloudflared/id.json"}
        sites = [{"hostname": "example.com", "service": "http://localhost:80"}]
        with patch.object(tunnel_setup, "check_root"), patch.object(
            tunnel_setup, "load_state", return_value=None
        ), patch.object(tunnel_setup, "install_cloudflared"), patch.object(
            tunnel_setup, "authenticate_cloudflare"
        ), patch.object(tunnel_setup, "setup_input", side_effect=["", "y"]), patch.object(
            tunnel_setup, "create_tunnel", return_value=tunnel
        ) as create, patch.object(tunnel_setup, "save_state"), patch.object(
            tunnel_setup, "discover_nginx_sites", return_value=sites
        ), patch.object(tunnel_setup, "_write_config_file"), patch.object(
            tunnel_setup, "install_and_start_service", side_effect=[False, True]
        ), patch.object(
            tunnel_setup, "_close_public_web_ports"
        ) as close:
            with self.assertRaisesRegex(RuntimeError, "did not become active"):
                tunnel_setup.main(tunnel_name="example_droplet")
            close.assert_not_called()
            with patch.object(tunnel_setup, "setup_input", side_effect=["", "y"]):
                self.assertTrue(tunnel_setup.main(tunnel_name="example_droplet"))
            create.assert_called_with("example_droplet")
            close.assert_called_once()

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
            command = run_command.call_args.args[0]
            self.assertEqual(command[:3], ["cloudflared", "tunnel", "--config"])
            self.assertNotEqual(command[3], config_path)
            self.assertEqual(command[4:], ["ingress", "validate"])

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
                    self.assertEqual(
                        run_command.call_args.args[0],
                        ["cloudflared", "tunnel", "--config", config_path, "ingress", "validate"],
                    )

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
            timeout=30,
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
                with patch.object(tunnel_setup, "run_command", side_effect=results) as run_command, \
                        patch.object(tunnel_setup, "_show_service_diagnostics") as diagnostics:
                    self.assertEqual(tunnel_setup._ensure_service_running(), expected)
                self.assertEqual(run_command.call_count, len(results))
                self.assertEqual(diagnostics.call_count, 0 if expected else 1)

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

    def test_restart_failure_is_not_reported_as_a_successful_refresh(self):
        with patch.object(tunnel_setup, "run_command", return_value=SimpleNamespace(
            returncode=1, stdout="", stderr="restart failed",
        )) as run_command, patch.object(tunnel_setup, "_show_service_diagnostics") as diagnostics:
            self.assertFalse(tunnel_setup._ensure_service_running(restart=True))
        run_command.assert_called_once_with(
            ["systemctl", "restart", "cloudflared"], check=False, capture_output=True,
            timeout=tunnel_setup.SERVICE_COMMAND_TIMEOUT,
        )
        diagnostics.assert_called_once()

    def test_existing_service_is_restarted_without_reinstallation(self):
        with tempfile.TemporaryDirectory() as tmp:
            drop_in = os.path.join(tmp, "cloudflared.service.d", "50-basaltwater-startup.conf")
            with patch.object(tunnel_setup, "SERVICE_STARTUP_DROP_IN", drop_in), \
                    patch.object(tunnel_setup, "run_command", return_value=SimpleNamespace(
                        returncode=0, stdout="loaded\n", stderr="",
                    )) as run_command:
                self.assertTrue(tunnel_setup.install_and_start_service(restart=True))
            with open(drop_in, encoding="utf-8") as config:
                self.assertIn("TimeoutStartSec=120s", config.read())
            self.assertEqual(stat.S_IMODE(os.stat(drop_in).st_mode), 0o644)
        self.assertEqual([item.args[0] for item in run_command.call_args_list], [
            ["systemctl", "daemon-reload"],
            ["systemctl", "show", "cloudflared", "--property=LoadState", "--value"],
            ["systemctl", "enable", "cloudflared"],
            ["systemctl", "restart", "cloudflared"],
            ["systemctl", "is-active", "cloudflared"],
        ])

    def test_new_service_keeps_initial_connection_and_has_grace_before_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            drop_in = os.path.join(tmp, "cloudflared.service.d", "50-basaltwater-startup.conf")

            def command_result(command, **_kwargs):
                with open(drop_in, encoding="utf-8") as config:
                    self.assertIn("TimeoutStartSec=120s", config.read())
                stdout = "not-found\n" if "--property=LoadState" in command else "active\n"
                return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

            with patch.object(tunnel_setup, "SERVICE_STARTUP_DROP_IN", drop_in), \
                    patch.object(tunnel_setup, "run_command", side_effect=command_result) as run_command:
                self.assertTrue(tunnel_setup.install_and_start_service(restart=True))
        commands = [item.args[0] for item in run_command.call_args_list]
        self.assertIn(
            ["cloudflared", "--config", "/etc/cloudflared/config.yml", "service", "install"],
            commands,
        )
        self.assertNotIn(["systemctl", "restart", "cloudflared"], commands)
        self.assertNotIn(["systemctl", "start", "cloudflared"], commands)

    def test_service_command_bounds_timeout_and_handles_missing_executable(self):
        command = ["systemctl", "restart", "cloudflared"]
        for error, expected in (
            (tunnel_setup.CommandTimeoutError("systemctl restart cloudflared", 240), 124),
            (FileNotFoundError("systemctl unavailable"), 127),
        ):
            with self.subTest(error=type(error).__name__), \
                    patch.object(tunnel_setup, "run_bounded_command", side_effect=error) as run_command:
                result = tunnel_setup._run_service_command(command, timeout=240)
                self.assertEqual(result.returncode, expected)
                self.assertTrue(result.stderr)
                run_command.assert_called_once_with(command, check=False, capture_output=True, timeout=240)

    def test_service_configuration_failures_do_not_start_or_reinstall(self):
        ok = SimpleNamespace(returncode=0, stdout="", stderr="")
        failure = SimpleNamespace(returncode=1, stdout="", stderr="systemd unavailable")
        loaded = SimpleNamespace(returncode=0, stdout="loaded", stderr="")
        masked = SimpleNamespace(returncode=0, stdout="masked", stderr="")
        for results in ([failure], [ok, failure], [ok, masked], [ok, loaded, failure]):
            with self.subTest(calls=len(results)), tempfile.TemporaryDirectory() as tmp:
                with patch.object(tunnel_setup, "SERVICE_STARTUP_DROP_IN", os.path.join(tmp, "startup.conf")), \
                        patch.object(tunnel_setup, "run_command", side_effect=results), \
                        patch.object(tunnel_setup, "_show_service_diagnostics") as diagnostics, \
                        patch.object(tunnel_setup, "_ensure_service_running") as ensure:
                    self.assertFalse(tunnel_setup.install_and_start_service(restart=True))
                diagnostics.assert_called_once()
                ensure.assert_not_called()

    def test_service_install_failure_reports_diagnostics_and_can_be_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            drop_in = os.path.join(tmp, "startup.conf")
            ok = SimpleNamespace(returncode=0, stdout="", stderr="")
            missing = SimpleNamespace(returncode=0, stdout="not-found\n", stderr="")
            loaded = SimpleNamespace(returncode=0, stdout="loaded\n", stderr="")
            timeout = subprocess.TimeoutExpired(["cloudflared", "service", "install"], 240)
            with patch.object(tunnel_setup, "SERVICE_STARTUP_DROP_IN", drop_in), \
                    patch.object(tunnel_setup, "run_command", side_effect=[ok, missing, timeout]) as run_command, \
                    patch.object(tunnel_setup, "_show_service_diagnostics") as diagnostics:
                self.assertFalse(tunnel_setup.install_and_start_service(restart=True))
                diagnostics.assert_called_once()
                run_command.assert_called_with(
                    ["cloudflared", "--config", "/etc/cloudflared/config.yml", "service", "install"],
                    check=False, capture_output=True, timeout=240,
                )
            # A timed-out installation may already have written the unit. A
            # retry reuses it and activates the still-pending configuration.
            with patch.object(tunnel_setup, "SERVICE_STARTUP_DROP_IN", drop_in), \
                    patch.object(tunnel_setup, "run_command", side_effect=[ok, loaded, ok, ok, ok]) as run_command:
                self.assertTrue(tunnel_setup.install_and_start_service(restart=True))
            commands = [item.args[0] for item in run_command.call_args_list]
            self.assertIn(["systemctl", "restart", "cloudflared"], commands)
            self.assertFalse(any(item[0] == "cloudflared" for item in commands))

    def test_diagnostics_show_connection_errors_and_redact_inline_secrets(self):
        journal = (
            "INF Settings: map[token:settings-secret]\n"
            'ERR token="inline-secret" TunnelSecret:json-secret\n'
            "ERR Authorization: Bearer bearer-secret\n"
            "ERR failed to dial QUIC: timeout\n"
        )
        results = [
            SimpleNamespace(returncode=0, stdout="cloudflared version 2026.5.2", stderr=""),
            SimpleNamespace(returncode=0, stdout="Result=timeout", stderr=""),
            SimpleNamespace(returncode=0, stdout=journal, stderr=""),
        ]
        output = io.StringIO()
        with patch.object(tunnel_setup, "run_command", side_effect=results) as run_command, redirect_stdout(output):
            tunnel_setup._show_service_diagnostics()
        self.assertIn("Result=timeout", output.getvalue())
        self.assertIn("failed to dial QUIC: timeout", output.getvalue())
        self.assertIn("7844", output.getvalue())
        for secret in ("settings-secret", "inline-secret", "json-secret", "bearer-secret"):
            self.assertNotIn(secret, output.getvalue())
        self.assertTrue(all(item.kwargs["timeout"] == 10 for item in run_command.call_args_list))

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
            tunnel_setup, "install_and_start_service", return_value=True
        ):
            self.assertTrue(tunnel_setup.main(interactive=False))

        write_config.assert_not_called()
        save_state.assert_not_called()

    def test_non_interactive_update_converts_setup_errors_to_false(self):
        with patch.object(tunnel_setup, "main", side_effect=RuntimeError("broken setup")):
            self.assertFalse(tunnel_setup.run_non_interactive_update())

    def test_failed_refresh_retains_applied_routes_and_retries_restart(self):
        state = {
            "tunnel": {"name": "basaltwater", "id": "tunnel-id", "credentials_file": "/managed/tunnel.json"},
            "sites": [{"hostname": "old.example.com", "service": "http://localhost:80"}],
        }
        sites = [{"hostname": "new.example.com", "service": "http://localhost:80"}]
        with patch.object(tunnel_setup, "check_root"), \
                patch.object(tunnel_setup, "load_state", return_value=state), \
                patch.object(tunnel_setup, "install_cloudflared"), \
                patch.object(tunnel_setup, "discover_nginx_sites", return_value=sites), \
                patch.object(tunnel_setup.os.path, "isfile", return_value=True), \
                patch.object(tunnel_setup, "_write_config_file"), \
                patch.object(tunnel_setup, "install_and_start_service", side_effect=[False, True]) as ensure_service, \
                patch.object(tunnel_setup, "save_state") as save_state:
            self.assertFalse(tunnel_setup.main(interactive=False))
            save_state.assert_called_once_with(state["tunnel"], state["sites"], pending_activation=True)
            self.assertTrue(tunnel_setup.main(interactive=False))
            self.assertEqual(ensure_service.call_args_list, [call(restart=True), call(restart=True)])
            self.assertEqual(save_state.call_args_list[-1], call(state["tunnel"], sites))

    def test_failed_config_repair_retries_activation_even_with_unchanged_routes(self):
        with tempfile.TemporaryDirectory() as tmp:
            credentials = os.path.join(tmp, "tunnel.json")
            with open(credentials, "w", encoding="utf-8") as handle:
                handle.write("{}")
            state_path = os.path.join(tmp, "tunnel-state.json")
            tunnel = {"name": "basaltwater", "id": "tunnel-id", "credentials_file": credentials}
            sites = [{"hostname": "example.com", "service": "http://localhost:80"}]
            with patch.object(tunnel_setup, "STATE_FILE", state_path), \
                    patch.object(tunnel_setup, "CONFIG_DIR", tmp), \
                    patch.object(tunnel_setup, "check_root"), \
                    patch.object(tunnel_setup, "install_cloudflared"), \
                    patch.object(tunnel_setup, "discover_nginx_sites", return_value=sites), \
                    patch.object(tunnel_setup, "_config_is_valid", side_effect=[False, True]), \
                    patch.object(tunnel_setup, "_write_config_file"), \
                    patch.object(tunnel_setup, "install_and_start_service", side_effect=[False, True]) as ensure_service:
                tunnel_setup.save_state(tunnel, sites)
                self.assertFalse(tunnel_setup.main(interactive=False))
                self.assertTrue(tunnel_setup.load_state()["pending_activation"])
                self.assertTrue(tunnel_setup.main(interactive=False))
                self.assertNotIn("pending_activation", tunnel_setup.load_state())
                self.assertEqual(ensure_service.call_args_list, [call(restart=True), call(restart=True)])

    def test_missing_credentials_fail_before_config_or_service_mutation(self):
        state = {
            "tunnel": {"credentials_file": "/missing/tunnel.json"},
            "sites": [],
        }
        with patch.object(tunnel_setup, "check_root"), \
                patch.object(tunnel_setup, "load_state", return_value=state), \
                patch.object(tunnel_setup, "install_cloudflared"), \
                patch.object(tunnel_setup, "discover_nginx_sites", return_value=[{"hostname": "example.com", "service": "http://localhost:80"}]), \
                patch.object(tunnel_setup.os.path, "isfile", return_value=False), \
                patch.object(tunnel_setup, "_write_config_file") as write_config, \
                patch.object(tunnel_setup, "install_and_start_service") as ensure_service:
            with self.assertRaisesRegex(ValueError, "credentials are missing"):
                tunnel_setup.main(interactive=False)
        write_config.assert_not_called()
        ensure_service.assert_not_called()

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
                SimpleNamespace(returncode=0, stdout="not-found", stderr=""),
                SimpleNamespace(returncode=0, stdout="", stderr=""),
                SimpleNamespace(returncode=0, stdout="", stderr=""),
                SimpleNamespace(returncode=0, stdout="active", stderr=""),
            ]
            with patch.object(tunnel_setup, "STATE_FILE", state_path), \
                    patch.object(tunnel_setup, "CONFIG_DIR", config_dir), \
                    patch.object(tunnel_setup, "SERVICE_STARTUP_DROP_IN", os.path.join(tmp, "startup.conf")), \
                    patch.object(tunnel_setup, "NGINX_SITES_DIR", nginx_dir), \
                    patch.object(tunnel_setup, "check_root"), \
                    patch.object(tunnel_setup, "install_cloudflared"), \
                    patch.object(tunnel_setup, "run_command", side_effect=command_results) as run_command:
                self.assertTrue(tunnel_setup.main(interactive=False))

            commands = [item.args[0] for item in run_command.call_args_list]
            self.assertIn(["cloudflared", "--config", os.path.join(config_dir, "config.yml"), "service", "install"], commands)
            self.assertNotIn(["systemctl", "restart", "cloudflared"], commands)

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
