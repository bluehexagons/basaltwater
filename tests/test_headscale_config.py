"""Offline setup contracts; no registry discovery or system mutations."""

from __future__ import annotations

import contextlib
import io
import shlex
import tempfile
import unittest
from unittest.mock import patch

from lib.arg_parser import create_setup_argument_parser
from lib.cache import load_setup_command, merge_setup_configs, save_setup_command
from lib.config import SetupConfig
from lib.headscale_config import SUPPORTED_PROFILES, validate_headscale_url


class TestHeadscaleConfig(unittest.TestCase):
    def config(self, **changes):
        fields = dict(host="target.example.com", username="agent", system_type="server_lite")
        fields.update(changes)
        return SetupConfig(**fields)

    def hosting(self, **changes):
        fields = dict(headscale=True, headscale_domain="vpn.example.com",
                      headscale_base_domain="mesh.example.net",
                      headscale_onboarding_domain="join.example.com",
                      headscale_public_ipv4="198.51.100.34")
        fields.update(changes)
        return self.config(**fields)

    def parse(self, flags, *, remote=False):
        parser = create_setup_argument_parser("Headscale test", for_remote=remote)
        args = parser.parse_args(shlex.split(flags) if remote else ["target.example.com", "agent", *shlex.split(flags)])
        args.host = "target.example.com"
        args.timezone = "UTC"
        return SetupConfig.from_args(args, "server_lite")

    def test_independent_capabilities_and_help(self):
        config = self.parse("--headscale --no-unbound")
        self.assertTrue(config.headscale)
        self.assertIsNone(config.tailscale)
        self.assertFalse(config.unbound)
        config = self.parse("--tailscale Office --unbound")
        self.assertEqual(config.tailscale_server, "Office")
        self.assertTrue(config.tailscale)
        self.assertTrue(config.unbound)
        self.assertEqual("Team Space", self.parse("--tailscale 'Team Space'").tailscale_server)
        help_text = create_setup_argument_parser("help").format_help()
        for flag in ("--headscale", "--no-headscale", "--tailscale", "--tailscale-login-server", "--no-tailscale", "--unbound", "--no-unbound"):
            self.assertIn(flag, help_text)

    def test_static_external_endpoint_round_trips_without_native_intent(self):
        config = self.parse("--headscale-onboarding-domain join.example.com "
                            "--headscale-onboarding-control-url https://VPN.example.net:443/")
        self.assertEqual(config.headscale_onboarding_control_url, "https://vpn.example.net")
        with tempfile.TemporaryDirectory() as workspace, patch("lib.cache.get_setup_cache_dir", return_value=workspace):
            save_setup_command(config)
            restored = load_setup_command(config.host)
        merged = merge_setup_configs(restored, self.config())
        for current in (restored, merged,
                        self.parse(" ".join(merged.to_setup_command()[3:])),
                        self.parse(" ".join(merged.to_remote_args()), remote=True)):
            self.assertEqual(current.headscale_onboarding_control_url, "https://vpn.example.net")
            for name in ("headscale", "tailscale", "unbound"):
                self.assertIsNone(getattr(current, name))
        patched = merge_setup_configs(merged, self.config(headscale_onboarding_control_url="https://new.example.net"))
        self.assertEqual(patched.headscale_onboarding_control_url, "https://new.example.net")
        legacy = self.config().to_dict()
        legacy.pop("headscale_onboarding_control_url")
        self.assertIsNone(SetupConfig.from_dict(config.host, config.system_type, legacy).headscale_onboarding_control_url)

    def test_parsed_external_metadata_composes_only_static_steps(self):
        from plugins.headscale import extend_headscale_steps

        config = self.parse("--headscale-onboarding-domain join.example.com "
                            "--headscale-onboarding-control-url https://vpn.example.net")
        steps = []
        with patch("subprocess.run", side_effect=AssertionError("native command")):
            extend_headscale_steps(config, steps)
        self.assertEqual([function.__name__ for _, function in steps],
                         ["preflight_onboarding", "install_nginx", "install_certbot", "configure_headscale_onboarding"])

    def test_onboarding_complete_validation_and_hosted_endpoint_binding(self):
        from lib.headscale_config import validate_headscale_settings

        for changes in (
            {"headscale_onboarding_control_url": "https://vpn.example.net"},
            {"headscale_onboarding_domain": "join.example.com"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.config(**changes, dry_run=True).to_remote_args()
        self.hosting(headscale_onboarding_control_url="https://VPN.example.com:443/").to_remote_args()
        with self.assertRaisesRegex(ValueError, "own control endpoint"):
            self.hosting(headscale_onboarding_control_url="https://other.example.net")
        for profile in SUPPORTED_PROFILES:
            config = self.config(system_type=profile, headscale_onboarding_domain="join.example.com",
                                 headscale_onboarding_control_url="https://vpn.example.net",
                                 **(dict(ci_repository="example/project", ci_allowed_users=["admin"])
                                    if profile == "server_github_runner" else {}))
            validate_headscale_settings(config, complete=True)
            self.assertIsNone(config.tailscale)

    def test_external_onboarding_rejects_bad_urls_and_topology(self):
        for url in ("http://vpn.example.net", "https://user@vpn.example.net", "https://vpn.example.net/path",
                    "https://vpn.example.net?x", "https://bad..example.net", "https://vpn.example.net\n"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.config(headscale_onboarding_control_url=url)
        for changes in (
            dict(headscale_onboarding_domain="bad..example.com"),
            dict(headscale_onboarding_domain="127.0.0.1"),
            dict(headscale_onboarding_domain="vpn.example.net"),
            dict(headscale_base_domain="example.net"),
            dict(headscale_base_domain="child.vpn.example.net"),
            dict(headscale_base_domain="join.example.com"),
        ):
            fields = dict(headscale_onboarding_domain="join.example.com",
                          headscale_onboarding_control_url="https://vpn.example.net")
            fields.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.config(**fields)

    def test_parser_rejects_conflicting_client_requests(self):
        for flags in ("--tailscale office --tailscale-login-server https://vpn.example.com",
                      "--tailscale office --no-tailscale",
                      "--no-tailscale --tailscale-login-server https://vpn.example.com"):
            with self.subTest(flags=flags), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.parse(flags)

    def test_https_origin_validation(self):
        self.assertEqual(validate_headscale_url("https://[2001:db8::1]:443/"), "https://[2001:db8::1]")
        self.assertEqual(validate_headscale_url("https://VPN.Example.com:443/"), "https://vpn.example.com")
        self.assertEqual(validate_headscale_url("https://vpn.example.com:8443"), "https://vpn.example.com:8443")
        for value in ("http://vpn.example.com", "https://u:p@vpn.example.com", "https://vpn.example.com?", "https://vpn.example.com#", "https://vpn.example.com/path", "https://vpn.example.com:0", "https://vpn.example.com:65536", "https://vpn.example.com:", "https://vpn.example.com\n", "https://vpn.example.com;id", "https://vpn.example.com/$(id)", "https://vpn.example.com/%0a", "https://vpn.example.com\\evil", "tailscale up"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_headscale_url(value)

    def test_opt_out_survives_cache_merge_and_transport(self):
        cached = self.hosting(headscale_onboarding_control_url="https://vpn.example.com")
        disabled = merge_setup_configs(cached, self.parse("--no-headscale --no-tailscale --no-unbound"))
        with tempfile.TemporaryDirectory() as workspace, patch("lib.cache.get_setup_cache_dir", return_value=workspace):
            save_setup_command(disabled)
            restored = load_setup_command(disabled.host)
        merged = merge_setup_configs(restored, self.config())
        for name in ("headscale", "tailscale", "unbound"):
            self.assertIs(getattr(merged, name), False)
        remote = self.parse(" ".join(merged.to_remote_args()), remote=True)
        local = self.parse(" ".join(merged.to_setup_command()[3:]))
        for config in (remote, local):
            for name in ("headscale", "tailscale", "unbound"):
                self.assertIs(getattr(config, name), False)

    def test_resolved_name_rename_and_endpoint_guard(self):
        cached = self.config(tailscale_server="office", tailscale_server_id="stable-id",
                             tailscale_login_server="https://vpn.example.com")
        renamed = merge_setup_configs(cached, self.config(tailscale_server="renamed", tailscale_server_id="stable-id", tailscale_login_server="https://vpn.example.com/"))
        self.assertEqual(renamed.tailscale_server_id, "stable-id")
        self.assertEqual(renamed.tailscale_server, "renamed")
        self.assertIn("--tailscale renamed", renamed.to_setup_command())
        for changes in (dict(tailscale_server_id="stable-id", tailscale_login_server="https://elsewhere.example.com"),
                        dict(tailscale_server_id="new-id", tailscale_login_server="https://vpn.example.com"),
                        dict(tailscale_login_server="https://vpn.example.com"),
                        dict(tailscale_server="unresolved")):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                merge_setup_configs(cached, self.config(**changes))

    def test_disable_preserves_binding_and_does_not_authorize_migration(self):
        for server_id in (None, "stable-id"):
            with self.subTest(server_id=server_id):
                cached = self.config(tailscale_server_id=server_id, tailscale_login_server="https://vpn.example.com")
                disabled = merge_setup_configs(cached, self.parse("--no-tailscale"))
                restored = SetupConfig.from_dict(disabled.host, disabled.system_type, disabled.to_dict())
                self.assertEqual(restored.tailscale_login_server, cached.tailscale_login_server)
                self.assertIs(restored.tailscale, False)
                self.assertIn("--no-tailscale", restored.to_remote_args())
                self.assertNotIn("--tailscale-login-server", " ".join(restored.to_remote_args()))
                with self.assertRaisesRegex(ValueError, "migration"):
                    merge_setup_configs(restored, self.config(tailscale_login_server="https://other.example.com"))

    def test_transport_omits_registry_and_resolver_candidates(self):
        client = self.config(tailscale_server="office", tailscale_server_id="stable-id", tailscale_login_server="https://vpn.example.com")
        fragments = client.to_remote_args()
        self.assertIn("--tailscale-login-server https://vpn.example.com", fragments)
        self.assertNotIn("office", " ".join(fragments))
        self.assertNotIn("stable-id", " ".join(fragments))
        self.assertEqual(self.parse(" ".join(fragments), remote=True).tailscale_login_server, client.tailscale_login_server)
        host = self.hosting(headscale_resolvers=["100.64.0.10", "fd7a:115c:a1e0::10"])
        self.assertNotIn("100.64.0.10", " ".join(host.to_remote_args()))
        self.assertIn("--headscale-resolver 100.64.0.10", host.to_setup_command())

    def test_incomplete_requests_fail_before_transport(self):
        for config in (self.config(headscale=True), self.config(tailscale_server="office"), self.config(unbound=True)):
            with self.subTest(config=config), self.assertRaises(ValueError):
                config.to_remote_args()

    def test_enabled_round_trips_and_omitted_merge_preserves_values(self):
        host = self.hosting(headscale_public_ipv6="2001:db8::34")
        merged = merge_setup_configs(host, self.config())
        self.assertEqual(merged.headscale_domain, host.headscale_domain)
        self.assertIs(merged.headscale, True)
        for config in (host, self.config(unbound=True, tailscale_login_server="https://vpn.example.com")):
            restored = SetupConfig.from_dict(config.host, config.system_type, config.to_dict())
            remote = self.parse(" ".join(restored.to_remote_args()), remote=True)
            for field in ("headscale", "tailscale", "unbound", "headscale_domain", "headscale_base_domain", "headscale_public_ipv4", "headscale_public_ipv6"):
                self.assertEqual(getattr(config, field), getattr(remote, field))

    def test_enrollment_secrets_have_no_config_or_argument_surface(self):
        for field in ("tailscale_auth_key", "tailscale_auth_key_file", "headscale_api_key", "headscale_private_key"):
            with self.subTest(field=field), self.assertRaises(TypeError):
                SetupConfig.from_dict("target.example.com", "server_lite", {"username": "agent", field: "SECRET"})
        for flag in ("--tailscale-auth-key", "--tailscale-auth-key-file", "--headscale-api-key"):
            with self.subTest(flag=flag), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.parse(flag + " SECRET")

    def test_invalid_profiles_types_and_values(self):
        for changes in (dict(system_type="agent_cachyos", tailscale=False), dict(system_type="server_proxmox", headscale=True), dict(system_type="server_wsl", unbound=True), dict(system_type="custom_steps", tailscale=True), dict(container_base="ubuntu", headscale=True), dict(headscale="yes"), dict(tailscale_server="office;id"), dict(tailscale_server_id="id"), dict(headscale_domain="127.0.0.1"), dict(headscale_public_ipv4="127.0.0.1"), dict(headscale_public_ipv6="93.184.216.34"), dict(headscale_resolvers=["8.8.8.8"]), dict(headscale_resolvers="100.64.0.10")):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.config(**changes)

    def test_dns_topology_and_ingress_conflicts(self):
        for changes in (dict(tailscale_login_server="https://vpn.example.com"), dict(tailscale_login_server="https://target.example.com"), dict(unbound=True), dict(headscale_base_domain="example.com"), dict(headscale_onboarding_domain="vpn.example.com"), dict(antistatic_server="game.example.com"), dict(homebox_port=8080), dict(web_panel_port=9090), dict(enable_cloudflare=True, deploy_specs=[["site.example.com,vpn.example.com", "https://example.com/repo.git"]]), dict(deploy_specs=[["join.example.com", "https://example.com/repo.git"]])):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.hosting(**changes)
        allowed = self.hosting(enable_cloudflare=True, deploy_specs=[["site.example.com", "https://example.com/repo.git"]])
        self.assertTrue(allowed.enable_cloudflare)
        with self.assertRaises(ValueError):
            self.config(unbound=True, web_panel_port=53)

    def test_merge_revalidates_interactions_and_preserve_keys(self):
        host = self.hosting()
        with self.assertRaises(ValueError):
            merge_setup_configs(host, self.config(tailscale_login_server="https://vpn.example.com"))
        kept = merge_setup_configs(host, self.config(headscale=False), preserve_keys={"headscale"})
        self.assertTrue(kept.headscale)

    def test_omitted_is_inert_and_validation_performs_no_discovery(self):
        with patch("socket.getaddrinfo", side_effect=AssertionError("network lookup")), patch("subprocess.run", side_effect=AssertionError("system command")):
            config = self.config()
            self.assertIsNone(config.headscale)
            self.assertIsNone(config.tailscale)
            self.assertIsNone(config.unbound)
            self.hosting().to_remote_args()
            self.config(tailscale_server="office")
        for flag in ("--headscale", "--unbound", "--tailscale"):
            self.assertFalse(any(arg.startswith(flag) for arg in config.to_remote_args()))


if __name__ == "__main__":
    unittest.main()
