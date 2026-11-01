"""Managed webhook setup shares the gateway and preserves build state."""

from __future__ import annotations

from contextlib import nullcontext
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from lib.build_enrollment import generate_general_builder_enrollment
from lib.config import SetupConfig
from lib.nginx_config import create_nginx_sites_for_groups
from web.ssl_steps import setup_ssl_for_deployments
from tests.test_build_policy_drafts import KEY
from tests.test_build_webhooks import SECRET, SETTINGS
from web import webhook_build_steps as setup


class WebhookSetupTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.config_path = str(self.root / "config.json")
        self.enterContext(patch.object(setup, "CONFIG", self.config_path))
        self.enterContext(patch.object(setup, "INBOX", str(self.root / "incoming")))
        self.enterContext(patch.object(setup, "is_dry_run", return_value=False))
        self.enterContext(patch.object(setup, "reject_legacy_build_services"))
        self.enterContext(patch.object(setup, "_read_file", return_value=None))
        self.enterContext(patch.object(setup, "_ensure_directory"))
        account = SimpleNamespace(pw_uid=max(1, os.getuid()), pw_gid=234,
                                  pw_dir="/nonexistent", pw_shell="/usr/sbin/nologin")
        self.enterContext(patch.object(setup.pwd, "getpwnam", return_value=account))
        self.enterContext(patch.object(setup.os, "getgrouplist", return_value=[234]))
        self.enterContext(patch.object(setup.os, "fchown"))
        self.enterContext(patch("lib.build_coordinator.ensure_coordinator"))
        self.enterContext(patch("lib.build_coordinator.maintenance", side_effect=nullcontext))
        self.write = self.enterContext(patch.object(setup, "write_json_atomic"))
        self.token = self.enterContext(patch.object(setup, "_write_file"))
        self.replace = self.enterContext(patch.object(setup, "replace_units"))
        self.remove = self.enterContext(patch.object(setup, "remove_units"))
        self.nginx = self.enterContext(patch("web.web_steps.install_nginx"))
        self.sites = self.enterContext(patch("lib.nginx_config.create_nginx_sites_for_groups"))
        self.certbot = self.enterContext(patch("web.ssl_steps.install_certbot"))
        self.ssl = self.enterContext(patch("web.ssl_steps.setup_ssl_for_deployments"))

    def config(self, **changes):
        fields = dict(host="builds.example.com", username="operator", system_type="server_lite",
                      machine_type="vm", is_build_server=True, enable_webhook_builds=True,
                      build_host_enrollment=generate_general_builder_enrollment(KEY),
                      webhook_repositories=["example/project"], webhook_domain="builds.example.com",
                      webhook_secret=SECRET, webhook_github_token="fixture-token", enable_ssl=True)
        fields.update(changes)
        return SetupConfig(**fields)

    def test_direct_https_uses_shared_nginx_and_certificate_installers(self):
        config = self.config()
        setup.configure_webhook_builds(config)
        self.nginx.assert_called_once_with(config)
        self.certbot.assert_called_once_with(config)
        self.ssl.assert_called_once()
        deployments = self.sites.call_args.args[0][config.webhook_domain]
        self.assertEqual(deployments[0]["backend_port"], setup.PORT)
        self.assertTrue(deployments[0]["preserve_path"])
        self.assertTrue(self.sites.call_args.kwargs["enable_https_redirect"])
        self.assertEqual(self.write.call_args.kwargs, dict(mode=0o640, uid=0, gid=234))
        self.assertEqual(self.token.call_args.kwargs, dict(mode=0o600))
        self.assertEqual(self.replace.call_args.kwargs["activate"], setup.UNITS)

    def test_tunnel_origin_skips_acme_and_keeps_http_origin(self):
        setup.configure_webhook_builds(self.config(enable_cloudflare=True))
        self.certbot.assert_not_called()
        self.ssl.assert_not_called()
        self.assertFalse(self.sites.call_args.kwargs["enable_https_redirect"])
        self.replace.assert_called_once()

    def test_real_sites_survive_post_setup_and_remain_discoverable(self):
        from lib import nginx_config as nginx
        from plugins.post_setup import build_post_setup_steps
        from web.service_tools import setup_cloudflare_tunnel as tunnel

        self.sites.side_effect = create_nginx_sites_for_groups
        self.ssl.side_effect = setup_ssl_for_deployments
        available = self.root / "sites-available"
        enabled = self.root / "sites-enabled"
        available.mkdir()
        enabled.mkdir()
        with patch.object(nginx, "NGINX_SITES_AVAILABLE_DIR", str(available)), \
             patch.object(nginx, "NGINX_SITES_ENABLED_DIR", str(enabled)), \
             patch.object(nginx, "_self_signed_cert_path", side_effect=lambda name: (
                 str(self.root / (name + ".crt")), str(self.root / (name + ".key")))), \
             patch.object(nginx, "generate_self_signed_cert"), \
             patch.object(nginx, "get_ssl_cert_path", return_value=("/fixture/cert", "/fixture/key")), \
             patch.object(nginx, "run", return_value=Mock(returncode=0)), \
             patch.object(tunnel, "NGINX_SITES_DIR", str(enabled)), \
             patch("web.ssl_steps.obtain_letsencrypt_certificate", return_value=True), \
             patch("web.ssl_steps.create_domain_cert_links"), \
             patch("web.ssl_steps.setup_certificate_renewal"), \
             patch("deploy.deploy_steps.deploy_repository", return_value=[dict(
                 domain="app.example.com", path="/", needs_proxy=True, backend_port=3000)]), \
             patch("lib.project_manifest.load_manifest"), \
             patch("lib.project_manifest.collect_public_udp", return_value=[]):
            for cloudflare in (False, True):
                with self.subTest(cloudflare=cloudflare):
                    config = self.config(enable_cloudflare=cloudflare,
                                         deploy_specs=[["app.example.com", "https://github.com/example/app.git"]])
                    legacy = available / "builds_example_com"
                    legacy.write_text(nginx.generate_merged_nginx_config(SETTINGS["domain"], [dict(
                        domain=SETTINGS["domain"], path="/", needs_proxy=True,
                        backend_port=setup.PORT, preserve_path=True)]))
                    (enabled / legacy.name).symlink_to(legacy)
                    setup.configure_webhook_builds(config)
                    self.assertFalse(legacy.exists())
                    self.assertFalse((enabled / legacy.name).exists())
                    for title, step in build_post_setup_steps(config, Mock(return_value=("/fixture/source", "a" * 40))):
                        if title.startswith(("Deploying uploaded", "Configuring Nginx", "Configuring certificates")):
                            step(config)
                    webhook = available / "webhook_builds_example_com"
                    self.assertTrue(webhook.is_file())
                    self.assertTrue((enabled / webhook.name).is_symlink())
                    self.assertIn("proxy_pass http://127.0.0.1:8765;", webhook.read_text())
                    self.assertEqual({site["hostname"] for site in tunnel.discover_nginx_sites()},
                                     {"app.example.com", "builds.example.com"})
                    create_nginx_sites_for_groups({}, managed_domains={SETTINGS["domain"]}, site_owner="webhook")
                    self.assertFalse(webhook.exists())
                    self.assertTrue((enabled / "app_example_com").exists())

    def test_disable_removes_intake_and_owned_domain_without_removing_state(self):
        with patch.object(setup.os.path, "lexists", side_effect=lambda path: path == self.config_path), \
             patch.object(setup, "load_settings", return_value=SETTINGS):
            setup.configure_webhook_builds(self.config(enable_webhook_builds=False))
        self.remove.assert_called_once_with(setup.UNITS)
        self.sites.assert_called_once_with({}, managed_domains={SETTINGS["domain"]}, site_owner="webhook")
        self.write.assert_not_called()
        self.token.assert_not_called()
        self.replace.assert_not_called()

    def test_units_keep_fetch_credentials_and_job_store_out_of_receiver_writes(self):
        from lib.build_coordinator import LOCK

        receiver = setup.render_unit(receive=True)
        admission = setup.render_unit(receive=False)
        self.assertIn("User=basaltwater-webhook\n", receiver)
        self.assertIn("ReadWritePaths=" + setup.INBOX + "\n", receiver)
        self.assertNotIn(setup.TOKEN, receiver)
        self.assertIn("User=root\n", admission)
        self.assertIn(LOCK, admission)
        self.assertIn(" /var/lib/basaltwater/build-host ", admission)
        self.assertIn("MemoryMax=512M", admission)


class LegacyBuildGuardTests(unittest.TestCase):
    def test_retained_legacy_grant_stops_setup_before_system_mutations(self):
        with patch.object(setup.os.path, "lexists", side_effect=lambda path: path == "/etc/sudoers.d/deploy-nginx"), \
             patch.object(setup, "run") as run, self.assertRaisesRegex(ValueError, "Retired"):
            setup.reject_legacy_build_services()
        run.assert_not_called()

    def test_loaded_legacy_units_are_refused_after_files_were_archived(self):
        for state, refused in (("LoadState=loaded\nActiveState=active\n", True),
                               ("LoadState=not-found\nActiveState=inactive\n", False)):
            with self.subTest(state=state), patch.object(setup.os.path, "lexists", return_value=False), \
                 patch.object(setup.os.path, "isdir", return_value=True), \
                 patch.object(setup, "run", return_value=Mock(stdout=state)):
                if refused:
                    with self.assertRaisesRegex(ValueError, "remain loaded"):
                        setup.reject_legacy_build_services()
                else:
                    setup.reject_legacy_build_services()


if __name__ == "__main__":
    unittest.main()
