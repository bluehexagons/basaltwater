"""Tests for opt-in LAN hostname discovery through mDNS."""

from __future__ import annotations

import unittest
import subprocess
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
    def test_failed_disable_never_reports_success(self) -> None:
        config = _config(clear_mdns=True)
        def command(argv, **kwargs):
            if 'show' in argv:
                return subprocess.CompletedProcess(argv, 0, 'LoadState=loaded\nActiveState=active\nUnitFileState=enabled\n')
            if kwargs.get('check', True):
                raise subprocess.CalledProcessError(1, argv)
            return subprocess.CompletedProcess(argv, 1, '', 'Stop refused')
        with (
            patch.object(network_steps, 'can_manage_mdns', return_value=True),
            patch.object(network_steps, 'is_dry_run', return_value=False),
            patch.object(network_steps, 'run', side_effect=command),
            patch('builtins.print') as output,
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                network_steps.configure_mdns(config)
        self.assertFalse(any('disabled' in str(call) for call in output.call_args_list))

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

    def test_disables_socket_before_service_and_verifies_both(self) -> None:
        config = _config(clear_mdns=True)
        states = dict.fromkeys(network_steps.MDNS_UNITS, 'active')
        def command(argv, **kwargs):
            if argv[1] == 'show':
                active = states[argv[2]]
                enabled = 'enabled' if active == 'active' else 'disabled'
                return subprocess.CompletedProcess(argv, 0, f'LoadState=loaded\nActiveState={active}\nUnitFileState={enabled}\n')
            states[argv[-1]] = 'inactive'
            return subprocess.CompletedProcess(argv, 0)

        with (
            patch.object(network_steps, "can_manage_mdns", return_value=True),
            patch.object(network_steps, "is_dry_run", return_value=False),
            patch.object(network_steps, "run", side_effect=command) as run,
            patch.object(network_steps, "install_package") as install,
        ):
            network_steps.configure_mdns(config)

        self.assertEqual([call.args[0][-1] for call in run.call_args_list if call.args[0][1] == 'disable'],
                         list(network_steps.MDNS_UNITS))
        self.assertEqual([call.args[0][2] for call in run.call_args_list if call.args[0][1] == 'show'],
                         list(network_steps.MDNS_UNITS) * 2)
        self.assertTrue(all(call.kwargs.get('check', True) for call in run.call_args_list if call.args[0][1] == 'disable'))
        install.assert_not_called()

    def test_absent_or_masked_units_and_partial_installations(self) -> None:
        for load, enabled in (('not-found', ''), ('masked', 'masked'), ('loaded', 'enabled-runtime')):
            with self.subTest(load=load, enabled=enabled):
                def command(argv, **kwargs):
                    if argv[1] == 'show':
                        # Socket absent; service may be absent, masked or runtime enabled.
                        service = argv[2].endswith('.service')
                        current = enabled if service else ''
                        if len(disabled):
                            current = 'masked' if enabled == 'masked' else 'disabled'
                        return subprocess.CompletedProcess(argv, 0,
                            f'LoadState={load if service else "not-found"}\nActiveState=inactive\nUnitFileState={current if service else ""}\n')
                    disabled.append(argv)
                    return subprocess.CompletedProcess(argv, 0)
                disabled = []
                with patch.object(network_steps, 'can_manage_mdns', return_value=True), \
                        patch.object(network_steps, 'is_dry_run', return_value=False), \
                        patch.object(network_steps, 'run', side_effect=command):
                    network_steps.configure_mdns(_config(clear_mdns=True))
                self.assertEqual(len(disabled), 0 if load == 'not-found' else 1)
                if enabled == 'enabled-runtime':
                    self.assertIn('--runtime', disabled[0])

    def test_inspection_errors_or_incomplete_output_abort_before_mutation(self) -> None:
        for code, output in ((1, ''), (0, ''), (0, 'LoadState=not-found\nActiveState=inactive\n'),
                             (0, 'LoadState=error\nActiveState=inactive\nUnitFileState=\n')):
            with self.subTest(code=code, output=output), \
                    patch.object(network_steps, 'can_manage_mdns', return_value=True), \
                    patch.object(network_steps, 'is_dry_run', return_value=False), \
                    patch.object(network_steps, 'run', return_value=subprocess.CompletedProcess([], code, output)) as run:
                with self.assertRaisesRegex(RuntimeError, 'Could not inspect'):
                    network_steps.configure_mdns(_config(clear_mdns=True))
                self.assertTrue(all(call.args[0][1] == 'show' for call in run.call_args_list))

    def test_successful_command_requires_inactive_disabled_postcondition(self) -> None:
        for active, enabled in (('active', 'disabled'), ('inactive', 'enabled'), ('deactivating', 'disabled')):
            with self.subTest(active=active, enabled=enabled):
                def command(argv, **kwargs):
                    return subprocess.CompletedProcess(argv, 0,
                        f'LoadState=loaded\nActiveState={active}\nUnitFileState={enabled}\n')
                with patch.object(network_steps, 'can_manage_mdns', return_value=True), \
                        patch.object(network_steps, 'is_dry_run', return_value=False), \
                        patch.object(network_steps, 'run', side_effect=command), patch('builtins.print') as output:
                    with self.assertRaisesRegex(RuntimeError, 'still active or enabled'):
                        network_steps.configure_mdns(_config(clear_mdns=True))
                output.assert_not_called()

    def test_dry_runs_unsupported_machine_and_unselected_feature_have_no_system_calls(self) -> None:
        for config, supported, dry_run in ((_config(clear_mdns=True, dry_run=True), True, False),
                                           (_config(clear_mdns=True), True, True),
                                           (_config(clear_mdns=True), False, False),
                                           (_config(), True, False)):
            with self.subTest(supported=supported, dry_run=dry_run), \
                    patch.object(network_steps, 'can_manage_mdns', return_value=supported), \
                    patch.object(network_steps, 'is_dry_run', return_value=dry_run), \
                    patch.object(network_steps, 'run') as run, patch.object(network_steps, 'install_package') as install:
                network_steps.configure_mdns(config)
                run.assert_not_called()
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
