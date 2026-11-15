"""Native network admission and profile ordering contracts."""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from lib.config import SetupConfig
from lib.headscale_config import SUPPORTED_PROFILES
from lib.system_types import get_steps_for_system_type
from common import headscale_preflight as admission


def configuration(**values):
    return SetupConfig(host="192.0.2.10", username="operator", machine_type="vm",
                       system_type=values.pop("system_type", "server_lite"), **values)


class CompositionTests(unittest.TestCase):
    def test_omitted_services_are_inert(self):
        names = [name for name, _ in get_steps_for_system_type(configuration())]
        self.assertFalse(any("native network" in name or "Headscale" in name for name in names))

    def test_client_on_all_supported_profiles_precedes_panel(self):
        from common.tailscale_steps import configure_tailscale
        from common.web_panel_steps import configure_web_panel
        from security.steps import configure_firewall, configure_firewall_web

        for profile in SUPPORTED_PROFILES:
            with self.subTest(profile=profile):
                runner = {"ci_repository": "example/project", "ci_allowed_users": ["operator"]} if profile == "server_github_runner" else {}
                config = configuration(system_type=profile, tailscale=True,
                                       tailscale_login_server="https://vpn.example.com", web_panel_port=8123, **runner)
                functions = [fn for _, fn in get_steps_for_system_type(config)]
                self.assertIs(functions[0], admission.preflight_network_services)
                self.assertEqual(functions.count(configure_tailscale), 1)
                firewall = next(i for i, fn in enumerate(functions) if fn in {configure_firewall, configure_firewall_web})
                self.assertLess(firewall, functions.index(configure_tailscale))
                self.assertLess(functions.index(configure_tailscale), functions.index(configure_web_panel))

    def test_host_prerequisites_and_independent_selection(self):
        from common.headscale_admin_steps import configure_headscale_administration
        from web.web_steps import install_nginx
        from web.ssl_steps import install_certbot
        from web.headscale_steps import configure_headscale
        from common.tailscale_steps import configure_tailscale

        config = configuration(headscale=True, headscale_domain="vpn.example.com",
                               headscale_base_domain="nodes.example.net", headscale_public_ipv4="192.0.2.10")
        functions = [fn for _, fn in get_steps_for_system_type(config)]
        self.assertLess(functions.index(install_nginx), functions.index(configure_headscale))
        self.assertLess(functions.index(install_certbot), functions.index(configure_headscale))
        self.assertLess(functions.index(configure_headscale), functions.index(configure_headscale_administration))
        self.assertNotIn(configure_tailscale, functions)

    def test_enable_client_before_resolver_disable_resolver_before_client(self):
        from common.tailscale_steps import configure_tailscale
        from common.unbound_steps import configure_unbound

        for enabled in (False, True):
            config = configuration(tailscale=enabled, unbound=enabled,
                                   tailscale_login_server="https://vpn.example.com")
            functions = [fn for _, fn in get_steps_for_system_type(config)]
            self.assertEqual(functions.index(configure_tailscale) < functions.index(configure_unbound), enabled)

    def test_conflicting_shared_ingress_rejected(self):
        config = configuration(headscale=True, headscale_domain="vpn.example.com",
                               headscale_base_domain="nodes.example.net", headscale_public_ipv4="192.0.2.10",
                               enable_cloudflare=True)
        with self.assertRaisesRegex(ValueError, "direct HTTPS"):
            get_steps_for_system_type(config)
        config.cloudflare_allow_direct = True
        get_steps_for_system_type(config)


class AdmissionTests(unittest.TestCase):
    def test_admin_dry_run_avoids_packages_and_native_observation(self):
        from common import headscale_admin_steps as admin_steps
        with patch.object(admin_steps, "run") as run, patch.object(admin_steps, "missing_packages") as packages:
            admin_steps.configure_headscale_administration(configuration(headscale=True, dry_run=True))
        run.assert_not_called()
        packages.assert_not_called()

    def test_dry_run_performs_no_system_reads_or_mutations(self):
        with patch.object(admission, "run") as run, patch.object(admission, "get_os_id") as os_id:
            admission.preflight_network_services(configuration(dry_run=True, tailscale=False))
        run.assert_not_called()
        os_id.assert_not_called()

    def test_actual_capability_failure_precedes_tun_and_setup(self):
        config = configuration(tailscale=True, tailscale_login_server="https://vpn.example.com")
        with patch.object(admission, "get_os_id", return_value="debian"), \
                patch.object(admission, "get_debian_codename", return_value="trixie"), \
                patch.object(admission.os, "geteuid", return_value=0), \
                patch.object(admission.shutil, "which", return_value="/mock/apt-get"), \
                patch.object(admission.os.path, "isdir", return_value=True), \
                patch.object(admission, "run", return_value=Mock(stdout="amd64\n")), \
                patch.object(admission.Path, "read_text", return_value="CapEff:\t0\n"), \
                patch.object(admission.os, "open") as opened:
            with self.assertRaisesRegex(ValueError, "CAP_NET_ADMIN"):
                admission.preflight_network_services(config)
        opened.assert_not_called()
