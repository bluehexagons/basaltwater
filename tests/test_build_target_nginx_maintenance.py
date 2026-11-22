"""Verify that target activation pauses and restores only the affected routes."""

from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from lib import nginx_config
from lib.operation_state import OperationStateStore


class NginxMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.available = self.directory / "sites-available"
        self.enabled = self.directory / "sites-enabled"
        self.available.mkdir()
        self.enabled.mkdir()
        self.config = self.available / "shop_example"
        self.link = self.enabled / self.config.name
        self.original = nginx_config.GENERATED_CONFIG_MARKER + "\n# previous routes\n"
        self.config.write_text(self.original)
        self.link.symlink_to(self.config)
        self.store = OperationStateStore(str(self.directory / ".basaltwater-nginx-operation.json"))
        self.groups = {"shop.example": [
            {"path": "/api", "needs_proxy": True, "backend_port": 8210, "target_maintenance": True},
            {"path": "/", "needs_proxy": False, "serve_path": "/srv/static"},
        ]}
        self.cert = str(self.directory / "cert.pem"), str(self.directory / "key.pem")
        self.enterContext(patch.multiple(nginx_config, NGINX_SITES_AVAILABLE_DIR=str(self.available),
                                         NGINX_SITES_ENABLED_DIR=str(self.enabled)))
        self.enterContext(patch.object(nginx_config, "_self_signed_cert_path", return_value=self.cert))
        self.enterContext(patch.object(nginx_config, "get_ssl_cert_path", return_value=self.cert))
        self.enterContext(patch.object(nginx_config, "generate_self_signed_cert"))
        self.run = self.enterContext(patch.object(nginx_config, "run", return_value=subprocess.CompletedProcess([], 0)))
        self.drain = self.enterContext(patch.object(nginx_config, "_reload_for_maintenance"))

    def pause(self, callback):
        nginx_config.create_nginx_sites_for_groups(
            self.groups, enable_https_redirect=False, managed_domains={"shop.example"}, while_paused=callback,
        )

    def assert_paused_routes(self):
        content = self.config.read_text()
        self.assertEqual(content.count("location = /api { return 503; }"), 2)
        self.assertEqual(content.count("location /api/ { return 503; }"), 2)
        self.assertIn("root /srv/static/;", content)
        self.assertNotIn("proxy_pass", content)
        self.assertEqual(self.store.load().phase, "routes-paused")
        self.drain.assert_called_once()

    def test_restores_neighboring_and_original_routes_after_success_or_verified_rollback(self):
        def healthy():
            self.assert_paused_routes()

        def rejected():
            self.assert_paused_routes()
            raise RuntimeError("Health check failed; prior backend restored")

        self.pause(healthy)
        self.assertEqual(self.config.read_text(), self.original)
        self.assertIsNone(self.store.load())
        self.drain.reset_mock()
        with self.assertRaisesRegex(RuntimeError, "prior backend restored"):
            self.pause(rejected)
        self.assertEqual(self.config.read_text(), self.original)
        self.assertIsNone(self.store.load())

    def test_failed_drain_refuses_to_start_the_replacement_and_restores_routes(self):
        self.drain.side_effect = RuntimeError("Nginx workers did not drain")
        with self.assertRaisesRegex(RuntimeError, "workers did not drain"):
            self.pause(lambda: self.fail("unsafe replacement started"))
        self.assertEqual(self.config.read_text(), self.original)
        self.assertIsNone(self.store.load())

    def test_uncertain_release_preserves_paused_routes_and_blocks_reconciliation(self):
        def interrupted():
            self.assert_paused_routes()
            raise nginx_config.NginxPauseRecoveryRequired("Release state is uncertain")

        with self.assertRaises(nginx_config.NginxPauseRecoveryRequired):
            self.pause(interrupted)
        self.assertIn("location /api/ { return 503; }", self.config.read_text())
        self.assertEqual(self.store.load().status, "recovery_required")
        self.assertTrue(Path(self.store.load().context["backup_dir"], "previous.json").exists())
        with self.assertRaisesRegex(ValueError, "Unfinished nginx-reconciliation"):
            nginx_config.create_nginx_sites_for_groups({"shop.example": []})
