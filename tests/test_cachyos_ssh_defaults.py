"""Target-side SSH default policy; no host commands or setup mutations."""

from __future__ import annotations

import io
import os
import subprocess
import unittest
from unittest.mock import patch

import basaltwater
from lib.arg_parser import create_setup_argument_parser
from lib.cachyos import cachyos_config_from_args, preflight_cachyos, validate_cachyos_config
from lib import cachyos_ssh_policy as policy
from lib.config import SetupConfig


def unit_state(load="loaded", active="inactive", enabled="disabled", code=0):
    return subprocess.CompletedProcess([], code,
        f"LoadState={load}\nActiveState={active}\nUnitFileState={enabled}\n", "private stderr")


class CachyOSSSHDefaultsTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"SSH_CONNECTION": "", "SSH_TTY": ""})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.capability = patch.object(policy, "can_manage_system_services", return_value=True)
        self.capability.start()
        self.addCleanup(self.capability.stop)

    def config(self, *options, remote=False):
        if remote:
            parser = create_setup_argument_parser("test", for_remote=True, allow_steps=True)
            args = parser.parse_args(["--system-type", "agent_cachyos", "--username", "human", *options])
        else:
            parser, _, _ = basaltwater.create_basaltwater_parser()
            args = parser.parse_args(["setup", "agent_cachyos", "localhost", "human", *options])
        return cachyos_config_from_args(args, for_remote=remote)

    def test_active_or_enabled_native_socket_and_managed_service(self):
        for index in range(3):
            for active, enabled in (("active", "disabled"), ("inactive", "enabled"),
                                    ("inactive", "enabled-runtime")):
                with self.subTest(index=index, active=active, enabled=enabled):
                    results = [unit_state() for _ in range(3)]
                    results[index] = unit_state(active=active, enabled=enabled)
                    config = self.config("--dry-run")
                    with patch.object(policy.subprocess, "run", side_effect=results) as run, \
                            patch("lib.cachyos.is_cachyos", return_value=True):
                        preflight_cachyos(config)
                    self.assertIs(config.install_ssh, True)
                    self.assertTrue(config.lan_access)
                    self.assertEqual(run.call_args.args[0][2], policy._UNITS[index])
                    for call in run.call_args_list:
                        self.assertEqual(call.args[0][:2], ["systemctl", "show"])
                        self.assertTrue(call.kwargs["capture_output"])
                        self.assertEqual(call.kwargs["timeout"], 10)

    def test_installed_inactive_and_missing_units_are_off(self):
        for state in (unit_state(), unit_state(enabled="static"),
                      unit_state("not-found", "inactive", "", 4),
                      unit_state("masked", "inactive", "masked")):
            with self.subTest(state=state.stdout), \
                    patch.object(policy.subprocess, "run", return_value=state):
                config = self.config()
                policy.resolve_ssh_policy(config, target_is_cachyos=True)
                self.assertIs(config.install_ssh, False)
                self.assertFalse(config.lan_access)
                self.assertIn("--no-ssh", config.to_remote_args())
                self.assertIs(config.to_dict()["install_ssh"], False)

    def test_unknown_stops_apply_without_private_output(self):
        unknowns = [unit_state(code=1), unit_state(active="mystery"),
                    unit_state(enabled="mystery"), subprocess.CompletedProcess([], 0, "", "private"),
                    OSError("private command error"), subprocess.TimeoutExpired("private command", 10)]
        for unknown in unknowns:
            with self.subTest(unknown=unknown):
                config = self.config()
                kwargs = {"side_effect": unknown} if isinstance(unknown, Exception) else {"return_value": unknown}
                with patch.object(policy.subprocess, "run", **kwargs), \
                        patch("sys.stdout", new_callable=io.StringIO) as output, \
                        self.assertRaisesRegex(ValueError, "--ssh or --no-ssh") as error:
                    policy.resolve_ssh_policy(config, target_is_cachyos=True)
                self.assertNotIn("private", str(error.exception) + output.getvalue())
                self.assertIsNone(config.install_ssh)
                self.assertFalse(config.lan_access)

    def test_known_enabled_unit_wins_over_unrelated_probe_error(self):
        with patch.object(policy.subprocess, "run", side_effect=[OSError(), unit_state(active="active")]):
            self.assertIs(policy.detect_ssh_enabled(self.config()), True)

    def test_duplicate_or_extra_state_fields_cannot_enable_or_disable_ssh(self):
        for output in (unit_state().stdout + "ActiveState=active\n",
                       unit_state().stdout + "private=secret\n"):
            with patch.object(policy.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output)):
                self.assertIsNone(policy.detect_ssh_enabled(self.config()))

    def test_current_ssh_client_must_survive_private_firewall_handover(self):
        config = self.config("--ssh", "--access-source", "192.168.1.0/24")
        with patch.dict(os.environ, {"SSH_CONNECTION": "192.168.1.2 1234 192.168.1.3 22"}):
            policy.check_ssh_source(config)
        for client in ("192.168.2.2", "8.8.8.8", "2001:db8::1"):
            with patch.dict(os.environ, {"SSH_CONNECTION": client + " 1234 192.168.1.3 22"}), \
                    self.assertRaisesRegex(ValueError, "outside the selected private"):
                policy.check_ssh_source(config)
        with patch.dict(os.environ, {"SSH_CONNECTION": "8.8.8.8 1234 192.168.1.3 22"}):
            config.install_ssh = False
            policy.check_ssh_source(config)

    def test_active_masked_service_is_still_on(self):
        with patch.object(policy.subprocess, "run", return_value=unit_state("masked", "active", "masked")):
            self.assertIs(policy.detect_ssh_enabled(self.config()), True)

    def test_supported_session_markers_and_explicit_negative(self):
        for marker in ({"SSH_CONNECTION": "192.168.1.2 1234 192.168.1.3 22"},
                       {"SSH_CONNECTION": "2001:db8::1 1234 2001:db8::2 22"},
                       {"SSH_TTY": "/dev/pts/2"}):
            with self.subTest(marker=marker), patch.dict(os.environ, marker), \
                    patch.object(policy.subprocess, "run") as run:
                config = self.config()
                policy.resolve_ssh_policy(config, target_is_cachyos=True)
                self.assertIs(config.install_ssh, True)
                config = self.config("--no-ssh")
                policy.resolve_ssh_policy(config, target_is_cachyos=True)
                self.assertIs(config.install_ssh, False)
                self.assertFalse(config.lan_access)
                run.assert_not_called()

    def test_malformed_markers_do_not_imply_ssh(self):
        for connection, tty in (("private", "private"), ("1.2.3.4 0 1.2.3.5 22", ""),
                                ("1.2.3.4 1234 1.2.3.5 99999", "/dev/pts/1\n")):
            with self.subTest(connection=connection), \
                    patch.dict(os.environ, {"SSH_CONNECTION": connection, "SSH_TTY": tty}), \
                    patch.object(policy.subprocess, "run", return_value=unit_state()) as run:
                self.assertIs(policy.detect_ssh_enabled(self.config()), False)
                self.assertEqual(run.call_count, 3)

    def test_explicit_and_replayed_booleans_never_probe(self):
        for enabled in (True, False):
            config = self.config("--ssh" if enabled else "--no-ssh")
            replay = SetupConfig.from_dict("localhost", "agent_cachyos", config.to_dict())
            with patch.object(policy.subprocess, "run") as run:
                policy.resolve_ssh_policy(replay, target_is_cachyos=True)
                self.assertIs(replay.install_ssh, enabled)
                run.assert_not_called()

    def test_remote_defaults_on_only_at_target_preflight(self):
        config = self.config("--dry-run", remote=True)
        self.assertIsNone(config.install_ssh)
        with patch.object(policy.subprocess, "run") as run, \
                patch("lib.cachyos.is_cachyos", return_value=False):
            preflight_cachyos(config)
        self.assertIs(config.install_ssh, True)
        self.assertTrue(config.lan_access)
        run.assert_not_called()
        config = self.config("--no-ssh", remote=True)
        policy.resolve_ssh_policy(config, target_is_cachyos=True)
        self.assertIs(config.install_ssh, False)

    def test_explicit_network_policy_prevents_automatic_lan(self):
        for options in (("--no-lan-access",), ("--no-access-source",),
                        ("--access-source", "192.168.1.2")):
            with self.subTest(options=options):
                config = self.config("--ssh", *options)
                policy.resolve_ssh_policy(config, target_is_cachyos=True)
                self.assertFalse(config.lan_access)
        with self.assertRaisesRegex(ValueError, "private"):
            self.config("--ssh", "--access-source", "8.8.8.8")
        with self.assertRaisesRegex(ValueError, "require --lan-access"):
            self.config("--ssh", "--ssh-password-auth")

    def test_unsupported_preview_defers_and_never_probes(self):
        config = self.config("--dry-run")
        with patch("lib.cachyos.is_cachyos", return_value=False), \
                patch.object(policy.subprocess, "run") as run, \
                patch("sys.stdout", new_callable=io.StringIO) as output:
            preflight_cachyos(config)
        run.assert_not_called()
        self.assertIsNone(config.install_ssh)
        self.assertIn("SSH default deferred", output.getvalue())

    def test_unknown_preview_defers_and_service_capability_blocks_probes(self):
        config = self.config("--dry-run")
        with patch.object(policy, "can_manage_system_services", return_value=False), \
                patch.object(policy.subprocess, "run") as run, \
                patch("sys.stdout", new_callable=io.StringIO) as output:
            policy.resolve_ssh_policy(config, target_is_cachyos=True)
        run.assert_not_called()
        self.assertIsNone(config.install_ssh)
        self.assertIn("deferred", output.getvalue())

    def test_parser_serialization_and_validation_leave_none_unresolved(self):
        with patch.object(policy.subprocess, "run") as run:
            config = self.config()
            validate_cachyos_config(config)
            replay = SetupConfig.from_dict("localhost", "agent_cachyos", config.to_dict())
            self.assertIsNone(replay.install_ssh)
            self.assertNotIn("--ssh", replay.to_remote_args())
            run.assert_not_called()

    def test_invalid_config_rejected_before_observation(self):
        config = self.config("--dry-run")
        config.host = "example.com"
        with patch.object(policy.subprocess, "run") as run, \
                self.assertRaisesRegex(ValueError, "local setup only"):
            preflight_cachyos(config)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
