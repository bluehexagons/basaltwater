"""Minimal target enrollment reuses the shared SSH firewall policy."""

from __future__ import annotations

import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lib.config import SetupConfig
from security.security_steps import configure_firewall_ssh_only


class BuildSshFirewallTests(unittest.TestCase):
    @patch.dict(os.environ, {"SSH_CONNECTION": "192.0.2.5 12345 192.0.2.10 22"})
    @patch("security.security_steps.run")
    def test_active_only_preserves_inactive_firewall_and_public_web(self, run) -> None:
        config = SetupConfig(host="target.test", username="root", system_type="custom_steps",
                             machine_type="vm", ssh_access_sources=["192.0.2.5"])
        run.return_value = SimpleNamespace(returncode=1, stdout="")
        configure_firewall_ssh_only(config, active_only=True)
        self.assertEqual(run.call_count, 1)
        self.assertTrue(run.call_args.args[0].startswith("ufw status"))
        run.reset_mock()

        def command(value: str, **_kwargs: object) -> SimpleNamespace:
            output = "Status: active\n"
            if value == "ufw status verbose":
                output += ("Default: deny (incoming)\n80/tcp ALLOW IN Anywhere\n"
                           "443/tcp ALLOW IN Anywhere\n")
            return SimpleNamespace(returncode=0, stdout=output)

        run.side_effect = command
        configure_firewall_ssh_only(config, active_only=True)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn("ufw delete limit ssh", commands)
        self.assertTrue(any(value.startswith("ufw allow from 192.0.2.5 ") for value in commands))
        self.assertFalse(any("apt-get" in value or "ufw default" in value or "ufw --force enable" in value
                             or "80/tcp" in value or "443/tcp" in value for value in commands))


if __name__ == "__main__":
    unittest.main()
