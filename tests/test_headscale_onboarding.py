from __future__ import annotations

import unittest
import json
import tempfile
from pathlib import Path
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

from lib.headscale_onboarding import render_onboarding_html, resolve_control_url
from web import headscale_onboarding_steps as onboarding


class OnboardingRenderTests(unittest.TestCase):
    def test_escapes_configured_control_value_and_uses_only_page_url(self):
        html = render_onboarding_html("https://vpn.example.com", "https://join.example.com/")
        self.assertIn("https://vpn.example.com", html)
        self.assertIn("/onboarding-qr.png", html)
        self.assertNotIn("authkey", html.lower())
        self.assertNotIn("/register", html)

    def test_resolves_explicit_external_endpoint(self):
        self.assertEqual(resolve_control_url(SimpleNamespace(tailscale_login_server="https://external.example.net", headscale_domain=None)), "https://external.example.net")

    def test_rejects_untrusted_endpoint(self):
        with self.assertRaises(ValueError):
            resolve_control_url(SimpleNamespace(tailscale_login_server="http://bad.example", headscale_domain=None))

    def test_hosted_control_wins_over_another_selected_network(self):
        config = SimpleNamespace(headscale=True, headscale_domain="vpn.example.com",
                                 tailscale_login_server="https://other.example.com")
        self.assertEqual(resolve_control_url(config), "https://vpn.example.com")

    def test_rejects_noncanonical_and_shared_page_origins(self):
        for page in ("http://join.example.com/", "https://join.example.com/?token=x",
                     "https://join.example.com/path", "https://join.example.com:444/",
                     "https://vpn.example.com/"):
            with self.subTest(page=page), self.assertRaises(ValueError):
                render_onboarding_html("https://vpn.example.com", page)


class OnboardingPublicationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for name, rel in (("ROOT", "www/content"), ("SITE", "nginx/available/site"),
                          ("ENABLED", "nginx/enabled/site"), ("LOCK", "nginx/operation.json"),
                          ("RENEWAL_HOOK", "renewal/hook"),
                          ("MANIFEST", "state/owner.json"), ("MARKER", "state/snapshot.json")):
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            self.stack.enter_context(patch.object(onboarding, name, path))
        # The real path safety policy rejects /tmp; other tests exercise it.
        self.stack.enter_context(patch.object(onboarding, "_safe"))
        self.stack.enter_context(patch.object(onboarding, "preflight_onboarding"))
        self.stack.enter_context(patch.object(onboarding, "is_dry_run", return_value=False))
        self.run = self.stack.enter_context(patch.object(onboarding, "run", return_value=SimpleNamespace(returncode=0, stdout="")))
        self.qr = self.stack.enter_context(patch.object(onboarding.subprocess, "run", return_value=SimpleNamespace(stdout=b"PNG")))
        self.cert = self.stack.enter_context(patch.object(onboarding, "obtain_letsencrypt_certificate", return_value=True))
        self.renewal = self.stack.enter_context(patch.object(onboarding, "setup_certificate_renewal"))
        self.config = SimpleNamespace(headscale_onboarding_domain="JOIN.example.com.", headscale=False,
                                      tailscale_login_server="https://vpn.example.com", headscale_base_domain=None,
                                      dry_run=False, ssl_email=None, effective_access_sources=lambda: [])

    def publish(self):
        onboarding.configure_headscale_onboarding(self.config)

    def test_qr_exact_page_tls_acl_and_rerun(self):
        self.config.effective_access_sources = lambda: ["192.0.2.0/24", "2001:db8::/32"]
        self.publish()
        self.assertEqual(self.qr.call_args.kwargs["input"], b"https://join.example.com/")
        content = onboarding.SITE.read_text()
        for directive in ("ssl_certificate /etc/letsencrypt/live/join.example.com/fullchain.pem;",
                          "ssl_certificate_key /etc/letsencrypt/live/join.example.com/privkey.pem;",
                          "limit_req_zone $binary_remote_addr zone=" + onboarding.ZONE,
                          "limit_req zone=" + onboarding.ZONE, "allow 192.0.2.0/24;",
                          "allow 2001:db8::/32;", "deny all;", "error_log /dev/null;",
                          "frame-ancestors 'none'", "disable_symlinks on;"):
            self.assertIn(directive, content)
        self.assertNotIn("includeSubDomains", content)
        self.assertNotIn("proxy_pass", content)
        before = {p: p.read_bytes() for p in onboarding._files()}
        self.publish()
        self.assertEqual(before, {p: p.read_bytes() for p in onboarding._files()})
        self.assertTrue(onboarding.ENABLED.is_symlink())
        self.assertFalse(onboarding.LOCK.exists())
        self.assertFalse(onboarding.MARKER.exists())

    def test_none_and_dryrun_have_no_effects(self):
        self.config.dry_run = True
        self.publish()
        self.config.dry_run = False
        self.config.headscale_onboarding_domain = None
        self.publish()
        self.run.assert_not_called()
        self.qr.assert_not_called()
        self.assertFalse(onboarding.ROOT.exists())
        self.assertFalse(Path(str(onboarding.LOCK) + ".lock").exists())

    def test_unknown_and_modified_content_rejected_before_mutation(self):
        onboarding.ROOT.mkdir()
        with self.assertRaisesRegex(ValueError, "adoption"):
            self.publish()
        self.run.assert_not_called()
        onboarding.ROOT.rmdir()
        self.publish()
        (onboarding.ROOT / "index.html").write_text("operator edit")
        self.run.reset_mock()
        with self.assertRaisesRegex(ValueError, "Modified"):
            self.publish()
        self.run.assert_not_called()

    def test_nginx_dump_collision_including_multiline_wildcard(self):
        for name in ("join.example.com", "*.example.com", ".example.com", "~example"):
            self.run.return_value.stdout = "# configuration file /etc/nginx/conf.d/other.conf:\nserver { server_name\n" + name + "; }"
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "collides"):
                self.publish()
            self.assertFalse(onboarding.SITE.exists())
            self.qr.assert_not_called()

    def test_failure_restores_all_content_and_service_state(self):
        self.publish()
        before = {p: p.read_bytes() for p in onboarding._files()}
        self.config.tailscale_login_server = "https://other.example.com"
        self.cert.return_value = False
        with self.assertRaisesRegex(RuntimeError, "certificate"):
            self.publish()
        self.assertEqual(before, {p: p.read_bytes() for p in onboarding._files()})
        self.assertIn(unittest.mock.call(["systemctl", "reload", "nginx"]), self.run.call_args_list)
        self.assertFalse(onboarding.LOCK.exists())
        self.assertFalse(onboarding.MARKER.exists())

    def test_fresh_failure_removes_content_and_stops_previously_inactive_service(self):
        self.run.return_value.returncode = 3
        self.cert.return_value = False
        with self.assertRaisesRegex(RuntimeError, "certificate"):
            self.publish()
        self.assertFalse(onboarding.ROOT.exists())
        self.assertFalse(onboarding.SITE.exists())
        self.assertFalse(onboarding.ENABLED.is_symlink())
        self.assertIn(unittest.mock.call(["systemctl", "stop", "nginx"]), self.run.call_args_list)

    def test_uncertain_rollback_retains_private_snapshot_and_blocks_retry(self):
        self.cert.return_value = False
        def fail_stop(command, **kwargs):
            if command == ["systemctl", "reload", "nginx"]:
                raise RuntimeError("service failure")
            return SimpleNamespace(returncode=0, stdout="")
        self.run.side_effect = fail_stop
        with self.assertRaisesRegex(RuntimeError, "rollback uncertain"):
            self.publish()
        self.assertEqual(onboarding.MARKER.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(onboarding.LOCK.read_text())["status"], "recovery_required")
        with self.assertRaisesRegex(RuntimeError, "recovery"):
            self.publish()

    def test_write_failure_rolls_back_existing_files(self):
        self.publish()
        before = {p: p.read_bytes() for p in onboarding._files()}
        real_write = onboarding.write_bytes_atomic
        failed = False
        def fail_once(path, content, **kwargs):
            nonlocal failed
            if path == str(onboarding.ROOT / "style.css") and not failed:
                failed = True
                raise OSError("disk failure")
            return real_write(path, content, **kwargs)
        with patch.object(onboarding, "write_bytes_atomic", side_effect=fail_once), self.assertRaises(OSError):
            self.publish()
        self.assertEqual(before, {p: p.read_bytes() for p in onboarding._files()})

    def test_renewal_failure_restores_hook_and_timer(self):
        onboarding.RENEWAL_HOOK.write_bytes(b"previous hook")
        self.run.return_value.returncode = 3
        def fail_renewal():
            onboarding.RENEWAL_HOOK.write_bytes(b"new hook")
            raise RuntimeError("timer start failed")
        self.renewal.side_effect = fail_renewal
        with self.assertRaisesRegex(RuntimeError, "timer start failed"):
            self.publish()
        self.assertEqual(onboarding.RENEWAL_HOOK.read_bytes(), b"previous hook")
        self.assertIn(unittest.mock.call(["systemctl", "disable", "certbot.timer"]), self.run.call_args_list)
        self.assertIn(unittest.mock.call(["systemctl", "stop", "certbot.timer"]), self.run.call_args_list)

    def test_external_metadata_composes_onboarding_without_enrollment(self):
        from plugins.headscale import extend_headscale_steps
        from web.ssl_steps import install_certbot
        from web.web_steps import install_nginx
        self.config.headscale = self.config.tailscale = self.config.unbound = None
        steps = []
        extend_headscale_steps(self.config, steps)
        functions = [function for _, function in steps]
        self.assertEqual(functions, [onboarding.preflight_onboarding, install_nginx,
                                     install_certbot, onboarding.configure_headscale_onboarding])

    def test_candidate_validation_failure_restores_preexisting_site(self):
        self.publish()
        before = {p: p.read_bytes() for p in onboarding._files()}
        failed = False
        def reject_once(command, **kwargs):
            nonlocal failed
            if command == ["nginx", "-t"] and not failed:
                failed = True
                raise RuntimeError("candidate invalid")
            return SimpleNamespace(returncode=0, stdout="")
        self.run.side_effect = reject_once
        with self.assertRaisesRegex(RuntimeError, "candidate invalid"):
            self.publish()
        self.assertEqual(before, {p: p.read_bytes() for p in onboarding._files()})
        self.assertFalse(onboarding.LOCK.exists())

    def test_conflicts_and_invalid_acl_fail_before_mutations_even_in_dryrun(self):
        self.config.dry_run = True
        for suffix in ("example.com", "join.example.com", "vpn.example.com"):
            self.config.headscale_base_domain = suffix
            with self.subTest(suffix=suffix), self.assertRaisesRegex(ValueError, "MagicDNS"):
                self.publish()
        self.config.headscale_base_domain = None
        self.config.effective_access_sources = lambda: ["192.0.2.0/24; allow all"]
        with self.assertRaises(ValueError):
            self.publish()
        self.run.assert_not_called()
        self.qr.assert_not_called()

    def test_independent_zone_collision_and_unrelated_shared_operation_block(self):
        self.run.return_value.stdout = "# configuration file /etc/nginx/conf.d/other.conf:\nlimit_req_zone $remote_addr zone=" + onboarding.ZONE + ":1m rate=1r/s;"
        with self.assertRaisesRegex(ValueError, "rate zone"):
            self.publish()
        from lib.operation_state import OperationStateStore
        with OperationStateStore(str(onboarding.LOCK)).ownership() as lock:
            lock.begin("other-publication", "unrelated", "mutating")
        self.run.return_value.stdout = ""
        with self.assertRaisesRegex(RuntimeError, "Unfinished"):
            self.publish()
        self.assertFalse(onboarding.ROOT.exists())


class OnboardingSafetyTests(unittest.TestCase):
    def test_unsafe_ancestor_modes_owners_and_symlinks(self):
        from types import SimpleNamespace
        import os
        import stat
        path = Path("/safe/content/index.html")
        for changes in ({"st_mode": stat.S_IFDIR | 0o777},
                        {"st_mode": stat.S_IFLNK | 0o777},
                        {"st_uid": os.geteuid() + 1}):
            def observation(p):
                values = dict(st_mode=stat.S_IFDIR | 0o755, st_uid=os.geteuid())
                if str(p) == "/safe/content":
                    values.update(changes)
                return SimpleNamespace(**values)
            with self.subTest(changes=changes), patch.object(Path, "lstat", observation), self.assertRaisesRegex(ValueError, "Unsafe"):
                onboarding._safe(path)

    def test_enabled_link_must_point_exactly_to_owned_site(self):
        with tempfile.TemporaryDirectory() as temporary:
            link = Path(temporary) / "enabled"
            link.symlink_to("/unrelated/site")
            real_lstat = Path.lstat
            def observation(p):
                info = real_lstat(p)
                if p == link:
                    return info
                import os
                import stat
                return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=os.geteuid())
            with patch.object(Path, "lstat", observation), self.assertRaisesRegex(ValueError, "adoption"):
                onboarding._safe(link, link=True)


if __name__ == "__main__":
    unittest.main()
