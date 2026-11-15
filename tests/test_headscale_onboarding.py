from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from lib.headscale_onboarding import render_onboarding_html, resolve_control_url


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


if __name__ == "__main__":
    unittest.main()
