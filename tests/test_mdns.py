"""Tests for opt-in LAN hostname discovery through mDNS."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import basaltwater
from common import network_steps
from lib.config import SetupConfig
from lib.system_types import get_steps_for_system_type
from security import security_steps


def _config(**overrides: object) -> SetupConfig:
    values: dict[str, object] = {
        "host": "192.168.1.50",
        "username": "admin",
        "system_type": "server_lite",
    }
    values.update(overrides)
    return SetupConfig(**values)  # type: ignore[arg-type]


class TestMdnsConfig(unittest.TestCase):
    def test_setup_flag_round_trips_and_reconstructs(self) -> None:
        parser, _setup_parser, _patch_parser = basaltwater.create_basaltwater_parser()
        args = parser.parse_args(
            [
                "setup",
                "server_lite",
                "192.168.1.50",
                "admin",
                "--hostname",
                "fileserver",
                "--mdns",
            ]
        )

        config = SetupConfig.from_args(args, args.system_type)

        self.assertTrue(config.enable_mdns)
        self.assertFalse(config.clear_mdns)
        self.assertIn("--mdns", config.to_remote_args())
        self.assertIn("--mdns", config.to_setup_command())
        self.assertTrue(config.to_dict()["enable_mdns"])

        reloaded = SetupConfig.from_dict(
            config.host,
            config.system_type,
            config.to_dict(),
        )
        self.assertTrue(reloaded.enable_mdns)

    def test_patch_preserves_mdns_without_an_explicit_flag(self) -> None:
        parser, _setup_parser, _patch_parser = basaltwater.create_basaltwater_parser()
        omitted = parser.parse_args(["patch", "fileserver", "admin"])
        enabled = parser.parse_args(["patch", "fileserver", "admin", "--mdns"])
        disabled = parser.parse_args(["patch", "fileserver", "admin", "--no-mdns"])

        self.assertIsNone(omitted.enable_mdns)
        self.assertIn("enable_mdns", basaltwater._patch_preserve_keys(omitted))
        self.assertNotIn("enable_mdns", basaltwater._patch_preserve_keys(enabled))
        self.assertNotIn("clear_mdns", basaltwater._patch_preserve_keys(disabled))

        disabled_config = SetupConfig.from_args(disabled, "server_lite")
        self.assertFalse(disabled_config.enable_mdns)
        self.assertTrue(disabled_config.clear_mdns)
        self.assertIn("--no-mdns", disabled_config.to_remote_args())

    def test_mdns_adds_service_and_firewall_steps(self) -> None:
        config = _config(enable_mdns=True)
        step_names = [name for name, _function in get_steps_for_system_type(config)]

        self.assertIn("Configuring mDNS hostname discovery", step_names)
        self.assertIn("Configuring firewall for requested web ports", step_names)


class TestMdnsSetup(unittest.TestCase):
    def test_enables_avahi_and_installs_resolution_support(self) -> None:
        config = _config(enable_mdns=True)

        with (
            patch.object(network_steps, "can_manage_mdns", return_value=True),
            patch.object(network_steps, "is_dry_run", return_value=False),
            patch.object(network_steps, "install_package", return_value=True) as install,
            patch.object(network_steps, "run") as run,
        ):
            network_steps.configure_mdns(config)

        self.assertEqual(
            [call.args[1] for call in install.call_args_list],
            ["avahi-daemon", "libnss-mdns"],
        )
        run.assert_called_once_with("systemctl enable --now avahi-daemon")

    def test_disables_managed_avahi_service(self) -> None:
        config = _config(clear_mdns=True)

        with (
            patch.object(network_steps, "can_manage_mdns", return_value=True),
            patch.object(network_steps, "is_dry_run", return_value=False),
            patch.object(network_steps, "run") as run,
            patch.object(network_steps, "install_package") as install,
        ):
            network_steps.configure_mdns(config)

        run.assert_called_once_with("systemctl disable --now avahi-daemon", check=False)
        install.assert_not_called()

    def test_reconciles_mdns_firewall_rule(self) -> None:
        config = _config(enable_mdns=True)

        with (
            patch.object(
                security_steps,
                "run",
                return_value=SimpleNamespace(returncode=0),
            ) as run,
            patch.object(security_steps, "can_manage_mdns", return_value=True),
            patch.object(security_steps, "_remove_stale_managed_rules") as remove,
        ):
            security_steps._configure_mdns_firewall(config)

        run.assert_called_once_with(
            "ufw allow 5353/udp comment 'basaltwater mDNS UDP'",
            check=False,
            capture_output=True,
        )
        remove.assert_called_once_with(
            "basaltwater mDNS UDP",
            {"basaltwater mDNS UDP"},
        )


if __name__ == "__main__":
    unittest.main()
