"""Tests for web/cloudflare_steps.py."""

from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from unittest.mock import call, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib.config import SetupConfig
from web.cloudflare_steps import (
    configure_cloudflare_firewall,
    configure_nginx_for_cloudflare,
    run_cloudflare_tunnel_setup,
)


def _make_config(antistatic_server: str | None = None) -> SetupConfig:
    return SetupConfig(
        host="example-host",
        username="alice",
        system_type="server_web",
        enable_cloudflare=True,
        antistatic_server=antistatic_server,
    )


class TestConfigureCloudflareFirewall(unittest.TestCase):
    @patch("web.cloudflare_steps.run")
    def test_domain_antistatic_keeps_stun_direct_access(self, mock_run):
        mock_run.return_value = SimpleNamespace(returncode=0)

        configure_cloudflare_firewall(_make_config("lobby.example.com"))

        mock_run.assert_has_calls(
            [
                call(
                    "systemctl is-active --quiet cloudflared",
                    check=False,
                    capture_output=True,
                ),
                call("ufw status 2>/dev/null | grep -q 'Status: active'", check=False),
                call("ufw default deny incoming"),
                call("ufw default allow outgoing"),
                call("ufw delete allow ssh", check=False),
                call("ufw delete allow 22/tcp", check=False),
                call("ufw limit ssh"),
                call("ufw delete allow 80/tcp", check=False),
                call("ufw delete allow 443/tcp", check=False),
                call("ufw delete allow 80", check=False),
                call("ufw delete allow 443", check=False),
                call("ufw allow 3478/udp comment 'antistatic STUN'"),
                call("ufw --force enable"),
            ]
        )
        commands = [call_args.args[0] for call_args in mock_run.call_args_list]
        self.assertNotIn("ufw allow 8080/tcp comment 'antistatic direct port'", commands)

    @patch("web.cloudflare_steps.run")
    def test_hostless_antistatic_keeps_direct_tcp_and_stun_access(self, mock_run):
        mock_run.return_value = SimpleNamespace(returncode=0)

        configure_cloudflare_firewall(_make_config(":8080"))

        mock_run.assert_has_calls(
            [
                call(
                    "systemctl is-active --quiet cloudflared",
                    check=False,
                    capture_output=True,
                ),
                call("ufw status 2>/dev/null | grep -q 'Status: active'", check=False),
                call("ufw default deny incoming"),
                call("ufw default allow outgoing"),
                call("ufw delete allow ssh", check=False),
                call("ufw delete allow 22/tcp", check=False),
                call("ufw limit ssh"),
                call("ufw delete allow 80/tcp", check=False),
                call("ufw delete allow 443/tcp", check=False),
                call("ufw delete allow 80", check=False),
                call("ufw delete allow 443", check=False),
                call("ufw allow 8080/tcp comment 'antistatic direct port'"),
                call("ufw allow 3478/udp comment 'antistatic STUN'"),
                call("ufw --force enable"),
            ]
        )

    @patch("web.cloudflare_steps.run")
    def test_inactive_tunnel_refuses_firewall_changes(self, mock_run):
        mock_run.return_value = SimpleNamespace(returncode=3)

        with self.assertRaisesRegex(RuntimeError, "before cloudflared is active"):
            configure_cloudflare_firewall(_make_config())

        mock_run.assert_called_once_with(
            "systemctl is-active --quiet cloudflared",
            check=False,
            capture_output=True,
        )


class TestConfigureNginxForCloudflare(unittest.TestCase):
    @patch("web.cloudflare_steps.run")
    def test_validates_and_reloads_generated_config(self, mock_run):
        mock_run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
        with TemporaryDirectory() as tmp:
            conf_path = os.path.join(tmp, "cloudflare.conf")
            with patch("web.cloudflare_steps.NGINX_CLOUDFLARE_CONF", conf_path), \
                    patch("web.cloudflare_steps.NGINX_CLOUDFLARE_CONF_DIR", tmp):
                configure_nginx_for_cloudflare(_make_config())

            with open(conf_path, encoding="utf-8") as generated:
                self.assertIn("set_real_ip_from", generated.read())
        mock_run.assert_has_calls([
            call("nginx -t", check=False, capture_output=True),
            call("systemctl reload nginx", check=False, capture_output=True),
        ])

    @patch("web.cloudflare_steps.run")
    def test_restores_previous_config_when_validation_fails(self, mock_run):
        mock_run.return_value = SimpleNamespace(
            returncode=1, stdout="", stderr="invalid config"
        )
        with TemporaryDirectory() as tmp:
            conf_path = os.path.join(tmp, "cloudflare.conf")
            with open(conf_path, "w", encoding="utf-8") as existing:
                existing.write("old config\n")
            with patch("web.cloudflare_steps.NGINX_CLOUDFLARE_CONF", conf_path), \
                    patch("web.cloudflare_steps.NGINX_CLOUDFLARE_CONF_DIR", tmp):
                with self.assertRaisesRegex(RuntimeError, "rejected"):
                    configure_nginx_for_cloudflare(_make_config())
            with open(conf_path, encoding="utf-8") as restored:
                self.assertEqual(restored.read(), "old config\n")


class TestRunCloudflareTunnelSetup(unittest.TestCase):
    @patch("web.cloudflare_steps.os.path.exists")
    @patch("web.cloudflare_steps.run")
    def test_missing_tunnel_state_retains_direct_ports(self, mock_run, mock_exists):
        mock_exists.side_effect = lambda path: not path.endswith("tunnel-state.json")

        self.assertFalse(run_cloudflare_tunnel_setup(_make_config()))

        mock_run.assert_not_called()

    @patch("web.cloudflare_steps.os.path.exists", return_value=True)
    @patch("web.cloudflare_steps.run")
    def test_failed_refresh_is_fatal(self, mock_run, _mock_exists):
        mock_run.return_value = SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="service inactive",
        )

        with self.assertRaisesRegex(RuntimeError, "service inactive"):
            run_cloudflare_tunnel_setup(_make_config())


if __name__ == "__main__":
    unittest.main()
