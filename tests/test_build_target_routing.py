"""Serving configuration stays administrator-owned across artifact deploys."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lib.build_protocol import encode_document
from lib.build_target_activation import TargetReleaseActivator
from lib.build_target_routing import TargetRouting, load_target_routing, save_target_routing
from lib.config import SetupConfig


class TargetRoutingTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(directory)
        self.root.chmod(0o700)
        self.config = SetupConfig(host="target.example", username="deploy", system_type="server_web")

    def test_default_and_administrator_configuration_round_trip(self):
        self.assertEqual(load_target_routing(str(self.root)), TargetRouting())
        self.assertEqual(list(self.root.iterdir()), [])
        self.config.enable_cloudflare = True
        self.config.enable_ssl = True
        save_target_routing(str(self.root), self.config)
        self.assertEqual(load_target_routing(str(self.root)), TargetRouting(True, True))
        self.assertTrue(all(path.stat().st_mode & 0o777 == 0o600 for path in self.root.iterdir()))

    def test_missing_initialized_policy_and_unsafe_file_refuse(self):
        save_target_routing(str(self.root), self.config)
        policy = self.root / "routing.json"
        raw = policy.read_bytes()
        policy.unlink()
        with self.assertRaisesRegex(ValueError, "missing"):
            load_target_routing(str(self.root))
        policy.write_bytes(raw)
        policy.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            load_target_routing(str(self.root))
        policy.unlink()
        policy.symlink_to(self.root / ".routing-initialized")
        with self.assertRaises(OSError):
            load_target_routing(str(self.root))

    def test_unknown_fields_invalid_types_and_invalid_email_refuse(self):
        save_target_routing(str(self.root), self.config)
        for value in (
            TargetRouting().document() | {"cloudflare": 1},
            TargetRouting().document() | {"ssl_email": "bad\nemail"},
            TargetRouting().document() | {"build_controlled": True},
        ):
            (self.root / "routing.json").write_bytes(encode_document(value))
            with self.assertRaises(ValueError):
                load_target_routing(str(self.root))

    def activator(self):
        staging = self.root / "staging"
        state = self.root / "state"
        staging.mkdir(mode=0o700)
        state.mkdir(mode=0o700)
        return TargetReleaseActivator(str(state), str(staging), open_artifact=lambda: None)

    def test_cloudflare_publication_uses_http_origin_without_acme(self):
        activator = self.activator()
        self.config.enable_cloudflare = True
        self.config.enable_ssl = True
        save_target_routing(activator.root, self.config)
        with patch("lib.build_target_activation.create_nginx_sites_for_groups") as nginx, \
             patch("deploy.network_steps.configure_public_udp") as udp, \
             patch("web.ssl_steps.install_certbot") as certbot, \
             patch("web.ssl_steps.setup_ssl_for_deployments") as ssl:
            activator._publish({"applications": {}}, {"retired.example": []})
        nginx.assert_called_once_with({}, managed_domains={"retired.example"}, enable_https_redirect=False)
        udp.assert_called_once_with([])
        certbot.assert_not_called()
        ssl.assert_not_called()
        activator.check_routing(self.config)
        self.config.enable_cloudflare = False
        with self.assertRaisesRegex(ValueError, "enroll.*controller"):
            activator.check_routing(self.config)

    def test_direct_tls_uses_shared_scoped_certificate_setup(self):
        activator = self.activator()
        self.config.enable_ssl = True
        self.config.ssl_email = "admin@example.com"
        save_target_routing(activator.root, self.config)
        with patch("lib.build_target_activation.create_nginx_sites_for_groups") as nginx, \
             patch("deploy.network_steps.configure_public_udp"), \
             patch("web.ssl_steps.install_certbot") as certbot, \
             patch("web.ssl_steps.setup_ssl_for_deployments") as ssl:
            activator._publish({"applications": {}}, {"retired.example": []})
        nginx.assert_called_once_with({}, managed_domains={"retired.example"}, enable_https_redirect=True)
        certbot.assert_called_once_with()
        ssl.assert_called_once_with([], "admin@example.com", managed_domains={"retired.example"})


if __name__ == "__main__":
    unittest.main()
