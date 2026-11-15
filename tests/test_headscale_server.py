"""Offline native hosting contract checks; never touch host services."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from lib.atomic_io import write_json_atomic
from lib.config import SetupConfig
from lib import headscale_server as server
from web import headscale_steps as steps


def configuration(**values):
    return SetupConfig(host="vpn.example.com", username="operator", system_type="server_web", headscale=True,
                       headscale_domain="vpn.example.com", headscale_base_domain="mesh.example.net",
                       headscale_public_ipv4="8.8.4.4", **values)


def fixture_paths(root):
    root = Path(root)
    return server.ServerPaths(root / "etc", root / "data", root / "private", root / "nginx/site.conf", root / "socket")


class HeadscaleServerTests(unittest.TestCase):
    @patch.object(steps.os, "fchown")
    @patch.object(steps, "require_capabilities")
    @patch.object(steps, "health")
    @patch.object(steps, "configtest")
    @patch.object(steps, "install_deb")
    @patch.object(steps, "preflight")
    @patch.object(steps, "run")
    def test_rerun_preserves_existing_policy_config_and_keys(self, run, preflight, install,
            configtest, health, capabilities, chown):
        with tempfile.TemporaryDirectory() as root:
            paths = fixture_paths(root)
            paths.etc.mkdir()
            paths.data.mkdir()
            paths.nginx.parent.mkdir()
            retained = [paths.config, paths.etc / "policy.hujson", paths.data / "noise_private.key"]
            for path in retained:
                path.write_text("retained-private-fixture")
            preflight.return_value = server.manifest_for(configuration())
            run.return_value = subprocess.CompletedProcess([], 0, "Status: active")
            with patch.object(steps, "ServerPaths", return_value=paths):
                steps.configure_headscale(configuration(headscale_resolvers=["100.64.0.7"]))
            install.assert_not_called()
            for path in retained:
                self.assertEqual(path.read_text(), "retained-private-fixture")

    @patch.object(steps.os, "chown")
    @patch.object(server.os, "fchown")
    @patch.object(steps.pwd, "getpwnam")
    @patch.object(steps, "setup_certificate_renewal")
    @patch.object(steps, "obtain_letsencrypt_certificate", return_value=True)
    @patch.object(steps, "install_deb")
    @patch.object(steps, "require_capabilities")
    @patch.object(steps, "preflight", return_value=None)
    @patch.object(steps, "configtest")
    @patch.object(server, "configtest")
    @patch.object(server, "health")
    @patch.object(steps, "run")
    @patch.object(server, "run")
    def test_fresh_setup_publishes_private_manifest_after_health(self, native_run, run, health,
            native_test, configtest, preflight, capabilities, install, certificate, renewal,
            account, fchown, chown):
        from types import SimpleNamespace
        account.return_value = SimpleNamespace(pw_uid=-1, pw_gid=-1)
        run.side_effect = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0,
            "Status: active" if cmd == ["ufw", "status"] else "amd64" if cmd[0] == "dpkg" else "")
        with tempfile.TemporaryDirectory() as root:
            paths = fixture_paths(root)
            paths.nginx.parent.mkdir()
            with patch.object(steps, "ServerPaths", return_value=paths):
                steps.configure_headscale(configuration())
            self.assertEqual(server.load_manifest(paths), server.manifest_for(configuration()))
            self.assertEqual(paths.manifest.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads((paths.etc / "policy.hujson").read_text())["acls"][0]["dst"], ["*:*"])
            self.assertIn("proxy_set_header Upgrade", paths.nginx.read_text())
            health.assert_called_once_with(paths)
            self.assertFalse((paths.private / "operation.json").exists())

    def test_native_owned_only_render_and_candidate_resolvers(self):
        config = configuration(headscale_resolvers=["100.64.0.9"])
        rendered = json.loads(server.render_config(config))
        self.assertTrue(rendered["derp"]["server"]["verify_clients"])
        self.assertEqual(rendered["derp"]["urls"], [])
        self.assertEqual(rendered["derp"]["paths"], [])
        self.assertFalse(rendered["derp"]["auto_update_enabled"])
        self.assertEqual(rendered["dns"]["nameservers"]["global"], [])
        self.assertFalse(rendered["dns"]["override_local_dns"])
        self.assertNotIn("100.64.0.9", json.dumps(rendered))
        self.assertEqual(rendered["unix_socket_permission"], "0600")
        for key in ("listen_addr", "grpc_listen_addr", "metrics_listen_addr"):
            self.assertTrue(rendered[key].startswith("127.0.0.1:"))

    def test_private_relay_and_same_network_rejected(self):
        config = configuration()
        config.headscale_public_ipv4 = "192.168.1.8"
        with self.assertRaises(ValueError):
            server.render_config(config)
        with self.assertRaises(ValueError):
            configuration(tailscale=True, tailscale_login_server="https://vpn.example.com")

    def test_nginx_control_upgrade_and_no_auth_id_logs(self):
        result = server.render_nginx("vpn.example.com")
        for content in ("proxy_http_version 1.1", "proxy_set_header Upgrade $http_upgrade",
                        "default upgrade", "proxy_set_header X-Forwarded-For $remote_addr",
                        "access_log off", "error_log /dev/null"):
            self.assertIn(content, result)
        self.assertNotIn("$proxy_add_x_forwarded_for", result)
        with self.assertRaises(ValueError):
            server.render_nginx("bad; inject")

    @patch.object(steps, "run")
    @patch.object(steps, "require_capabilities")
    def test_none_and_dry_run_are_inert(self, capabilities, run):
        config = configuration()
        config.headscale = None
        steps.configure_headscale(config)
        config.headscale = True
        config.dry_run = True
        steps.configure_headscale(config)
        capabilities.assert_not_called()
        run.assert_not_called()

    @patch.object(steps, "runtime_operation")
    @patch.object(steps, "load_manifest")
    @patch.object(steps, "require_capabilities")
    @patch.object(steps, "stopped")
    @patch.object(steps, "run")
    def test_disable_preserves_all_files(self, run, stopped, capabilities, manifest, operation):
        config = configuration()
        config.headscale = False
        steps.configure_headscale(config)
        self.assertEqual([c.args[0] for c in run.call_args_list],
                         [["systemctl", "stop", "headscale"], ["systemctl", "disable", "headscale"]])

    def test_unsafe_manifest_and_unknown_schema_fail_closed(self):
        with tempfile.TemporaryDirectory() as root:
            paths = fixture_paths(root)
            paths.private.mkdir()
            write_json_atomic(str(paths.manifest), {"schema": 999})
            with self.assertRaises(ValueError):
                server.load_manifest(paths)
            paths.manifest.unlink()
            paths.manifest.symlink_to(Path(root) / "absent")
            with self.assertRaises(OSError):
                server.load_manifest(paths)

    @patch.object(server, "run")
    @patch.object(server.shutil, "which", return_value=None)
    def test_unmanaged_state_and_listener_conflicts_precede_mutation(self, which, run):
        with tempfile.TemporaryDirectory() as root:
            paths = fixture_paths(root)
            paths.etc.mkdir()
            with self.assertRaisesRegex(ValueError, "Unmanaged"):
                server.preflight(configuration(), paths)
            run.assert_not_called()
            paths.etc.rmdir()
            run.side_effect = [subprocess.CompletedProcess([], 0, 'not-found'),
                               subprocess.CompletedProcess([], 0, 'tcp LISTEN 0 0 127.0.0.1:8080 *:* users:(("other",pid=1))')]
            with self.assertRaisesRegex(ValueError, "listener"):
                server.preflight(configuration(), paths)

    @patch.object(server.os, "fchown")
    @patch.object(server, "run")
    @patch.object(server, "configtest")
    @patch.object(server, "health", side_effect=[RuntimeError("unhealthy"), None])
    @patch.object(server, "stopped")
    def test_failed_activation_restores_config(self, stopped, health, configtest, run, chown):
        with tempfile.TemporaryDirectory() as root:
            paths = fixture_paths(root)
            paths.etc.mkdir()
            paths.config.write_text("previous")
            paths.config.chmod(0o600)
            with self.assertRaisesRegex(RuntimeError, "unhealthy"):
                server.publish_config("candidate", paths, gid=-1)
            self.assertEqual(paths.config.read_text(), "previous")
            self.assertEqual(health.call_count, 2)

    @patch.object(server, "run")
    @patch.object(server, "verify_package")
    @patch.object(server, "stopped")
    def test_package_mask_precedes_install_and_survives_error(self, stopped, verify, run):
        def execute(command, **kwargs):
            if command[0] == "apt-get":
                raise RuntimeError("install failure")
        run.side_effect = execute
        with self.assertRaises(RuntimeError):
            server.install_deb(Path("/tmp/fixture.deb"), server.Release(server.VERSION, "amd64", server.SHA256["amd64"]))
        self.assertEqual(run.call_args_list[0].args[0], ["systemctl", "mask", "headscale.service"])
        self.assertFalse(any("unmask" in call.args[0] for call in run.call_args_list))

    @patch.object(server.os, "fchown")
    def test_interruption_blocks_retry(self, chown):
        with tempfile.TemporaryDirectory() as root:
            paths = fixture_paths(root)
            with self.assertRaises(RuntimeError):
                with server.runtime_operation(paths, "headscale-test"):
                    raise RuntimeError("interrupted")
            with self.assertRaises(ValueError):
                with server.runtime_operation(paths, "headscale-test"):
                    self.fail("must not bypass unfinished operation")
