"""Optional Proxmox panel setup, isolation, and certificate enrollment contracts."""

from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import DEFAULT, patch

from common import godot_web_steps as tls
from common import proxmox_web_panel_steps as isolation
from common import web_panel_steps as steps
from common.service_tools import web_panel_service as panel
from lib.arg_parser import create_setup_argument_parser
from lib.config import SetupConfig
from plugins.proxmox import build_server_proxmox_steps


def config(**overrides: object) -> SetupConfig:
    return SetupConfig(**{"host": "pve.example.test", "username": "root",
                          "system_type": "server_proxmox", **overrides})


class ProxmoxPanelSetupTest(unittest.TestCase):
    def test_cli_opt_in_and_removal_plans(self) -> None:
        parser = create_setup_argument_parser("test")
        for flags, expected in (([], None), (["--web-panel"], 80),
                                (["--web-panel", "--ssl"], 443),
                                (["--web-panel", "9443", "--ssl"], 9443),
                                (["--no-web-panel"], None)):
            with self.subTest(flags=flags):
                parsed = SetupConfig.from_args(parser.parse_args(["pve.example.test", "root", *flags]),
                                               "server_proxmox")
                self.assertEqual(parsed.web_panel_port, expected)
                plan = build_server_proxmox_steps(parsed)
                self.assertEqual(sum(step is steps.configure_web_panel for _, step in plan), bool(flags))
                self.assertFalse(any("ufw" in step.__name__ for _, step in plan))

    def test_native_ui_port_is_reserved_before_mutations(self) -> None:
        with self.assertRaisesRegex(ValueError, "8006.*reserved"):
            config(web_panel_port=8006)

    def test_manifest_links_to_native_ipv6_ui(self) -> None:
        manifest = steps.build_web_panel_manifest(config(host="2001:db8::5", web_panel_port=9443),
                                                  ["2001:db8::5"])
        self.assertEqual(manifest["services"], [{"label": "Proxmox VE",
                         "url": "https://[2001:db8::5]:8006/",
                         "description": "Native Proxmox host and guest management"}])

    def test_ssl_setup_uses_certificate_only_path_and_native_firewall(self) -> None:
        selected = config(web_panel_port=9443, enable_ssl=True)
        account = SimpleNamespace(pw_name="basaltwater-web-panel", pw_gid=991, pw_dir="/nonexistent")
        with ExitStack() as stack:
            mocks = stack.enter_context(patch.multiple(
                steps, _ensure_nginx=DEFAULT, _install_auth_file=DEFAULT,
                _ensure_web_panel_service_account=DEFAULT, _ensure_event_storage=DEFAULT,
                _validate_ingest_token_path=DEFAULT, _install_ingest_token=DEFAULT,
                write_json_atomic=DEFAULT, _configure_audit_exporter=DEFAULT,
                _configure_service=DEFAULT, _write_nginx_site=DEFAULT,
                configure_nginx_auth_failure_ban=DEFAULT,
            ))
            mocks["_install_auth_file"].return_value = (False, None)
            mocks["_ensure_web_panel_service_account"].return_value = account
            stack.enter_context(patch.object(steps.os, "makedirs"))
            stack.enter_context(patch.object(steps, "is_dry_run", return_value=False))
            stack.enter_context(patch("desktop.guacamole_steps.preflight_units"))
            stack.enter_context(patch("desktop.guacamole_steps.preflight_configuration"))
            stack.enter_context(patch("desktop.guacamole_steps.remove_guacamole"))
            stack.enter_context(patch("web.github_runner_registration_steps.remove_registration_service"))
            stack.enter_context(patch.object(isolation, "preflight_proxmox_panel_nginx"))
            isolate = stack.enter_context(patch.object(isolation, "configure_proxmox_panel_nginx"))
            firewall = stack.enter_context(patch(
                "security.proxmox_web_panel_firewall.configure_proxmox_web_panel_firewall"))
            stack.enter_context(patch.object(tls, "discover_local_web_identities", return_value=[]))
            full_host = stack.enter_context(patch.object(tls, "configure_internal_web_host"))
            certificate = stack.enter_context(patch.object(tls, "configure_internal_web_tls",
                return_value=("/etc/basaltwater/internal-web/server.crt", "/etc/basaltwater/internal-web/server.key", True, True)))
            steps.configure_web_panel(selected)

        certificate.assert_called_once()
        full_host.assert_not_called()
        isolate.assert_called_once()
        firewall.assert_called_once_with(selected)
        mocks["configure_nginx_auth_failure_ban"].assert_called_once_with(
            "web-panel", steps.WEB_PANEL_AUTH_FAILURE_LOG, port=9443)
        manifest = mocks["write_json_atomic"].call_args.args[1]
        self.assertEqual(manifest["certificate_trust"], {
            "certificate": "/etc/basaltwater/internal-web/server.crt", "local_ca": True})
        self.assertEqual(manifest["panel_url"], "https://pve.example.test:9443/")

    def test_removal_cleans_owned_proxmox_resources(self) -> None:
        with (
            patch.object(steps, "is_dry_run", return_value=False),
            patch.object(isolation, "preflight_proxmox_panel_nginx"),
            patch.object(isolation, "remove_proxmox_panel_nginx") as dropin,
            patch.object(steps, "remove_web_panel") as remove,
            patch("security.proxmox_web_panel_firewall.remove_proxmox_web_panel_firewall") as firewall,
        ):
            steps.configure_web_panel(config(disable_web_panel=True))
        remove.assert_called_once()
        firewall.assert_called_once()
        dropin.assert_called_once()

    def test_default_and_dry_run_do_not_install_anything(self) -> None:
        with (
            patch.object(steps, "is_dry_run", return_value=False) as dry,
            patch.object(steps, "_ensure_nginx") as install,
            patch.object(steps, "remove_web_panel") as remove,
            patch.object(isolation, "preflight_proxmox_panel_nginx") as preflight,
        ):
            steps.configure_web_panel(config())
            dry.return_value = True
            steps.configure_web_panel(config(web_panel_port=9443, enable_ssl=True))
            steps.configure_web_panel(config(disable_web_panel=True))
        install.assert_not_called()
        remove.assert_not_called()
        preflight.assert_not_called()

    def test_certificate_setup_does_not_install_shared_publishing_or_trust(self) -> None:
        with patch.multiple(tls, _ensure_nginx=DEFAULT, _ensure_managed_directory=DEFAULT,
                            _certificate_for_identities=DEFAULT, _configure_ca_download=DEFAULT,
                            _configure_web_policy=DEFAULT, _configure_web_control_service=DEFAULT,
                            _configure_nginx_site=DEFAULT, _install_chromium_ca_trust=DEFAULT) as mocks:
            mocks["_certificate_for_identities"].return_value = ("/tmp/cert", "/tmp/key", True, False)
            mocks["_configure_ca_download"].return_value = True
            self.assertEqual(tls.configure_internal_web_tls(["pve.example.test"]),
                             ("/tmp/cert", "/tmp/key", True, True))
        for name in ("_configure_web_policy", "_configure_web_control_service",
                     "_configure_nginx_site", "_install_chromium_ca_trust"):
            mocks[name].assert_not_called()

    def test_root_service_is_unprivileged_and_hides_proxmox_secrets(self) -> None:
        account = SimpleNamespace(pw_gid=991, pw_uid=991)
        with tempfile.TemporaryDirectory() as temporary:
            unit = os.path.join(temporary, "panel.service")
            sock = os.path.join(temporary, "http.sock")
            with open(sock, "w", encoding="utf-8"):
                pass
            with (
                patch.object(steps, "WEB_PANEL_SERVICE_FILE", unit),
                patch.object(steps, "WEB_PANEL_SOCKET", sock),
                patch.object(steps.pwd, "getpwnam", return_value=account),
                patch.object(steps, "run"),
                patch.object(steps, "is_service_active", return_value=True),
            ):
                steps._configure_service(config(web_panel_port=9443), "/root")
            with open(unit, encoding="utf-8") as stream:
                content = stream.read()
        self.assertIn("User=basaltwater-web-panel\n", content)
        self.assertIn("HOME=/nonexistent", content)
        self.assertIn(isolation.PROXMOX_PANEL_INACCESSIBLE_PATHS, content)


class ProxmoxPanelTrustTest(unittest.TestCase):
    def test_trust_discovery_verifies_panel_without_installed_utility(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ca = os.path.join(temporary, "ca.crt")
            with open(ca, "wb") as stream:
                stream.write(b"public CA fixture")
            manifest = steps.build_web_panel_manifest(config(web_panel_port=9443, enable_ssl=True),
                                                     ["pve.example.test"])
            manifest["certificate_trust"] = {"certificate": "/tmp/server.crt", "local_ca": True}
            with (
                patch.object(tls, "GODOT_WEB_CA_DOWNLOAD", ca),
                patch("common.service_tools.basaltwater_web._verify_live_tls") as verify,
                patch.object(panel.shutil, "which", return_value=None),
                patch.object(panel, "_run_json") as subprocess_json,
            ):
                trust = panel.discover_certificate_trust(manifest)
            verify.assert_called_once_with("pve.example.test", 9443, ca)
            subprocess_json.assert_not_called()
            self.assertEqual(trust["sha256"], hashlib.sha256(b"public CA fixture").hexdigest())
            self.assertEqual(trust["url"], "https://pve.example.test:9443/basaltwater-ca.crt")
            content = panel._render_certificate_trust(trust)
            self.assertIn("sha256sum " + ca, content)
            self.assertNotIn("basaltwater-web ca", content)

    def test_monitoring_profile_does_not_start_installed_tool_workers(self) -> None:
        manifest = steps.build_web_panel_manifest(config(web_panel_port=80), ["pve.example.test"])
        with tempfile.TemporaryDirectory() as temporary:
            state = panel.WebPanelState(manifest, agent_home=temporary)
            with (
                patch.object(panel, "discover_features") as discover,
                patch.object(state.agent_tasks, "start") as start,
            ):
                state.start_background_workers()
                self.assertEqual(state.features().tools, frozenset())
            discover.assert_not_called()
            start.assert_not_called()

    def test_ca_routes_are_certificate_only_on_http_and_https(self) -> None:
        nginx = steps.render_web_panel_nginx(["pve.example.test"], 9443,
                                            cert_path="/tmp/server.crt", key_path="/tmp/server.key",
                                            ca_https_download=True)
        self.assertIn("listen 8442;", nginx)
        self.assertIn("listen 9443 ssl;", nginx)
        self.assertNotIn("listen 8443", nginx)
        self.assertEqual(nginx.count("location = /basaltwater-ca.crt"), 2)
        self.assertEqual(nginx.count("limit_except GET { deny all; }"), 2)
        self.assertNotIn("/etc/pve", nginx)


class ProxmoxNginxIsolationTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.path = os.path.join(directory, "nginx.service.d", "panel.conf")
        self.enterContext(patch.object(isolation, "PROXMOX_PANEL_NGINX_DROPIN", self.path))
        self.runner = self.enterContext(patch.object(isolation, "run"))
        self.active = self.enterContext(patch.object(isolation, "is_service_active", return_value=True))

    def test_install_rerun_and_owned_removal(self) -> None:
        isolation.configure_proxmox_panel_nginx()
        with open(self.path, encoding="utf-8") as stream:
            self.assertIn(isolation.PROXMOX_PANEL_INACCESSIBLE_PATHS, stream.read())
        isolation.configure_proxmox_panel_nginx()
        self.assertEqual(self.runner.call_count, 2)
        isolation.remove_proxmox_panel_nginx()
        self.assertFalse(os.path.exists(self.path))
        isolation.remove_proxmox_panel_nginx()
        self.assertEqual(self.runner.call_count, 4)

    def test_failed_restart_restores_previous_configuration_and_mode(self) -> None:
        os.makedirs(os.path.dirname(self.path))
        original = isolation._MARKER + "\n[Service]\n# old isolation\n"
        with open(self.path, "w", encoding="utf-8") as stream:
            stream.write(original)
        os.chmod(self.path, 0o640)
        self.active.side_effect = [False, True]
        with self.assertRaisesRegex(RuntimeError, "Nginx did not start"):
            isolation.configure_proxmox_panel_nginx()
        with open(self.path, encoding="utf-8") as stream:
            self.assertEqual(stream.read(), original)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o640)
        self.assertEqual(self.runner.call_count, 4)

    def test_failed_first_install_removes_dropin_and_restarts_old_nginx(self) -> None:
        self.active.side_effect = [False, True]
        with self.assertRaises(RuntimeError):
            isolation.configure_proxmox_panel_nginx()
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(self.runner.call_count, 4)

    def test_operator_files_and_links_are_refused_before_restart(self) -> None:
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w", encoding="utf-8") as stream:
            stream.write("[Service]\n# Operator configuration\n")
        for action in (isolation.configure_proxmox_panel_nginx, isolation.remove_proxmox_panel_nginx):
            with self.assertRaisesRegex(RuntimeError, "unmanaged"):
                action()
        os.rename(self.path, self.path + ".original")
        os.symlink(self.path + ".original", self.path)
        with self.assertRaises(OSError):
            isolation.configure_proxmox_panel_nginx()
        self.runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
