"""Tests for setup summary security-policy output."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout

from lib.config import SetupConfig
from lib.display import (
    print_rdp_info,
    print_service_access_summary,
    print_setup_summary,
)


class TestRdpDisplay(unittest.TestCase):
    def test_setup_summary_redacts_notification_credentials(self) -> None:
        token = "a" * 43
        config = SetupConfig(
            host="agent-vm",
            username="agent",
            system_type="server_dev",
            notify_specs=[
                [
                    "webhook",
                    "https://panel.example/private/path?secret=yes#" + token,
                ],
                ["mailbox", "operator@example.com"],
            ],
        )

        output = io.StringIO()
        with redirect_stdout(output):
            print_setup_summary(config)

        rendered = output.getvalue()
        self.assertIn("webhook: https://panel.example", rendered)
        self.assertIn("mailbox: *@example.com", rendered)
        self.assertNotIn("private/path", rendered)
        self.assertNotIn("secret=yes", rendered)
        self.assertNotIn(token, rendered)

    def test_setup_summary_counts_duplicate_notification_target_once(self) -> None:
        config = SetupConfig(
            host="agent-vm",
            username="agent",
            system_type="server_dev",
            notify_specs=[
                ["webhook", "https://hooks.example.com/event"],
                ["webhook", " https://hooks.example.com/event "],
            ],
        )

        output = io.StringIO()
        with redirect_stdout(output):
            print_setup_summary(config)

        self.assertIn("Notifications: 1 target(s)", output.getvalue())

    def test_setup_summary_shows_strict_notification_tls_policy(self) -> None:
        config = SetupConfig(
            host="agent-vm",
            username="agent",
            system_type="server_dev",
            notify_specs=[["webhook", "https://hooks.example.com/event"]],
            notification_strict_https=True,
        )

        output = io.StringIO()
        with redirect_stdout(output):
            print_setup_summary(config)

        self.assertIn("strict HTTPS", output.getvalue())

    def test_setup_summary_makes_global_exposure_visible(self) -> None:
        config = SetupConfig(
            host="agent-vm",
            username="agent",
            system_type="workstation_dev",
            enable_rdp=True,
        )

        output = io.StringIO()
        with redirect_stdout(output):
            print_setup_summary(config)

        rendered = output.getvalue()
        self.assertIn("RDP bind address: 0.0.0.0", rendered)
        self.assertIn("RDP allowed sources: global (rate-limited", rendered)
        self.assertIn("RDP enabled channels: dynamic-resize, clipboard", rendered)
        self.assertIn("RDP maximum sessions: 1", rendered)
        self.assertIn("RDP disconnected session retention: unlimited", rendered)
        self.assertIn("RDP idle disconnect: disabled", rendered)

    def test_setup_summary_shows_restricted_sources_and_opt_in_channels(self) -> None:
        config = SetupConfig(
            host="agent-vm",
            username="agent",
            system_type="workstation_dev",
            enable_rdp=True,
            rdp_bind_address="10.0.0.25",
            rdp_allowed_sources=["10.0.0.0/24", "100.64.0.0/10"],
            rdp_clipboard=False,
            rdp_drive_redirection=True,
            rdp_audio=True,
            rdp_idle_timeout=14400,
        )

        output = io.StringIO()
        with redirect_stdout(output):
            print_setup_summary(config)

        rendered = output.getvalue()
        self.assertIn("RDP bind address: 10.0.0.25", rendered)
        self.assertIn("RDP allowed sources: 10.0.0.0/24, 100.64.0.0/10", rendered)
        self.assertIn("RDP enabled channels: dynamic-resize, drive/device, audio", rendered)
        self.assertIn("RDP maximum sessions: 1", rendered)
        self.assertIn("RDP disconnected session retention: unlimited", rendered)
        self.assertIn("RDP idle disconnect: 14400 seconds", rendered)

    def test_connection_info_repeats_the_effective_boundary(self) -> None:
        config = SetupConfig(
            host="agent-vm",
            username="agent",
            system_type="workstation_dev",
            enable_rdp=True,
            rdp_allowed_sources=["10.0.0.0/24"],
        )

        output = io.StringIO()
        with redirect_stdout(output):
            print_rdp_info(config)

        self.assertIn("Allowed sources: 10.0.0.0/24", output.getvalue())

    def test_setup_summary_shows_t3_sources_and_backup_jobs(self) -> None:
        config = SetupConfig(
            host="agent-vm",
            username="agent",
            system_type="server_dev",
            agent_tools=["codex"],
            web_interfaces=["t3code"],
            web_interface_sources=["192.168.0.0/24"],
            backup_specs=[["/srv/workspace", "/srv/backups/workspace", "daily"]],
        )

        output = io.StringIO()
        with redirect_stdout(output):
            print_setup_summary(config)

        rendered = output.getvalue()
        self.assertIn("Web interface sources: 192.168.0.0/24", rendered)
        self.assertIn("Backup Jobs: 1 job(s)", rendered)
        self.assertIn("/srv/workspace → /srv/backups/workspace (daily)", rendered)

    def test_access_summary_lists_web_interfaces_and_services(self) -> None:
        config = SetupConfig(
            host="192.168.0.41",
            username="agent",
            system_type="server_web",
            agent_tools=["codex"],
            web_interfaces=["t3code"],
            web_interface_sources=["192.168.0.0/24"],
            device_pairing_providers=["t3code"],
            gogs=["git.example.com:3000", "/srv/gogs"],
            antistatic_server="lobby.example.com",
            antistatic_db=":8081",
            enable_rdp=True,
            enable_samba=True,
        )

        output = io.StringIO()
        with redirect_stdout(output):
            print_service_access_summary(
                config,
                remote_access_details={
                    "t3code": "https://192.168.0.41:8444/",
                    "t3code-pairing": "https://192.168.0.41:8445/",
                },
            )

        rendered = output.getvalue()
        self.assertIn(
            "T3 Code: https://192.168.0.41:8444/ — coding workspace",
            rendered,
        )
        self.assertIn(
            "T3 Code device pairing: https://192.168.0.41:8445/ — protected device enrollment",
            rendered,
        )
        self.assertIn("SSH: ssh agent@192.168.0.41 — shell access", rendered)
        self.assertNotIn("http://192.168.0.41:3773/", rendered)
        self.assertNotIn("http://192.168.0.41:3774/", rendered)
        self.assertNotIn("compatibility", rendered)
        self.assertNotIn("0.0.0.0", rendered)
        self.assertIn("Gogs web: http://git.example.com:3000/", rendered)
        self.assertIn("Gogs Git over SSH: git@git.example.com", rendered)
        self.assertIn("Git access", rendered)
        self.assertIn("Antistatic lobby: http://lobby.example.com/", rendered)
        self.assertIn("Antistatic DB: http://192.168.0.41:8081/", rendered)
        self.assertIn("RDP: 192.168.0.41:3389", rendered)
        self.assertIn("Samba/SMB: //192.168.0.41", rendered)

    def test_access_summary_uses_gogs_tls_port(self) -> None:
        config = SetupConfig(
            host="192.168.0.51",
            username="gitadmin",
            system_type="server_web",
            gogs=[":3000", "/srv/gogs"],
            gogs_sources=["192.168.0.0/24"],
            enable_ssl=True,
        )

        output = io.StringIO()
        with redirect_stdout(output):
            print_service_access_summary(config)

        rendered = output.getvalue()
        self.assertIn("Web server: http://192.168.0.51/", rendered)
        self.assertIn("Gogs web: https://192.168.0.51:3000/", rendered)
        self.assertNotIn("Gogs web: https://192.168.0.51/", rendered)

    def test_access_summary_omits_cloudflare_backend_port(self) -> None:
        config = SetupConfig(
            host="192.168.0.51",
            username="gitadmin",
            system_type="server_web",
            gogs=["git.example.test:3000", "/srv/gogs"],
            enable_cloudflare=True,
        )

        output = io.StringIO()
        with redirect_stdout(output):
            print_service_access_summary(config)

        rendered = output.getvalue()
        self.assertIn("Gogs web: https://git.example.test/", rendered)
        self.assertNotIn("git.example.test:3000", rendered)

    def test_ipv6_only_generic_source_does_not_publish_ipv4_only_gogs(self) -> None:
        config = SetupConfig(
            host="192.168.0.51",
            username="gitadmin",
            system_type="server_web",
            gogs=[":3000", "/srv/gogs"],
            access_sources=["fc00::/7"],
        )

        output = io.StringIO()
        with redirect_stdout(output):
            print_service_access_summary(config)

        self.assertIn("Gogs web: http://127.0.0.1:3000/", output.getvalue())

    def test_hostless_antistatic_remains_http_when_ssl_is_for_gogs(self) -> None:
        config = SetupConfig(
            host="192.168.0.51",
            username="gitadmin",
            system_type="server_web",
            gogs=[":3000", "/srv/gogs"],
            gogs_sources=["192.168.0.0/24"],
            antistatic_server=":8080",
            antistatic_db=":8081",
            enable_ssl=True,
        )

        output = io.StringIO()
        with redirect_stdout(output):
            print_service_access_summary(config)

        rendered = output.getvalue()
        self.assertIn("Antistatic lobby: http://192.168.0.51:8080/", rendered)
        self.assertIn("Antistatic DB: http://192.168.0.51:8081/", rendered)

    def test_access_summary_marks_loopback_web_interfaces(self) -> None:
        config = SetupConfig(
            host="agent-vm",
            username="agent",
            system_type="server_dev",
            agent_tools=["codex"],
            web_interfaces=["t3code"],
        )

        output = io.StringIO()
        with redirect_stdout(output):
            print_service_access_summary(
                config,
                remote_access_details={"t3code": "https://agent-vm:8444/"},
            )

        rendered = output.getvalue()
        self.assertIn("T3 Code: https://agent-vm:8444/ — coding workspace", rendered)
        self.assertNotIn("http://127.0.0.1:3773/", rendered)
        self.assertNotIn("compatibility", rendered)
        self.assertNotIn("0.0.0.0", rendered)
        self.assertIn(
            "T3 Code pairing: infra-tools agent web pair agent-vm agent",
            rendered,
        )

    def test_access_summary_lists_managed_godot_https_origin(self) -> None:
        config = SetupConfig(
            host="192.168.0.42",
            username="agent",
            system_type="agent_vm",
            godot_bundles=["web"],
        )

        output = io.StringIO()
        with redirect_stdout(output):
            print_service_access_summary(config)

        rendered = output.getvalue()
        self.assertIn(
            "Godot web exports: https://192.168.0.42:8443/games/agent/",
            rendered,
        )
        self.assertIn("godot-web-publish GAME_NAME", rendered)

    def test_access_summary_lists_syncthing_admin_and_device_id(self) -> None:
        device_id = (
            "BJ5ID3D-3BL2IM7-KPTHNB3-LI3SO5N-"
            "KDCFYJN-Z4HKBUQ-AIANLCB-LJOJXAT"
        )
        config = SetupConfig(
            host="fileserver",
            username="admin",
            system_type="server_lite",
            enable_syncthing=True,
        )

        output = io.StringIO()
        with redirect_stdout(output):
            print_service_access_summary(
                config,
                remote_access_details={
                    "syncthing-admin": "https://fileserver:8446/",
                    "syncthing-device-id": device_id,
                },
            )

        rendered = output.getvalue()
        self.assertIn(
            "Syncthing admin: https://fileserver:8446/ "
            "— authenticated administration",
            rendered,
        )
        self.assertIn(
            f"Syncthing device ID: {device_id} — share with trusted peers",
            rendered,
        )

    def test_access_summary_does_not_leak_stale_syncthing_details(self) -> None:
        config = SetupConfig(
            host="fileserver",
            username="admin",
            system_type="server_lite",
            enable_syncthing=False,
        )

        output = io.StringIO()
        with redirect_stdout(output):
            print_service_access_summary(
                config,
                remote_access_details={
                    "syncthing-admin": "https://fileserver:8446/",
                    "syncthing-device-id": (
                        "BJ5ID3D-3BL2IM7-KPTHNB3-LI3SO5N-"
                        "KDCFYJN-Z4HKBUQ-AIANLCB-LJOJXAT"
                    ),
                },
            )

        self.assertNotIn("Syncthing", output.getvalue())


if __name__ == "__main__":
    unittest.main()
