"""SSH option and replay contracts, without workstation setup or system probes."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
import io
import json
from pathlib import Path
import shlex
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import basaltwater
from lib import cachyos_refresh as refresh
from lib.cachyos import cachyos_config_from_args, validate_cachyos_config
from lib.config import SetupConfig


class CachyOSSSHOptionsTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.home = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        account = SimpleNamespace(pw_name="human", pw_dir=str(self.home))
        stack.enter_context(patch.object(refresh, "_account", return_value=account))
        stack.enter_context(patch.object(refresh, "is_cachyos", return_value=True))
        stack.enter_context(patch("lib.cachyos.pwd.getpwuid", return_value=account))
        stack.enter_context(patch("lib.system_utils.get_local_timezone", return_value="UTC"))
        stack.enter_context(patch.object(refresh, "is_dry_run", return_value=False))
        stack.enter_context(patch("lib.cachyos_doctor.collect_cachyos_doctor", return_value={"capabilities": []}))
        stack.enter_context(patch("lib.cachyos_health.source_metadata", return_value={"commit": "a" * 40}))
        stack.enter_context(patch("lib.config._resolved_lan_networks", return_value=("10.0.0.0/24",)))
        self.preflight = stack.enter_context(patch.object(refresh, "preflight_cachyos"))
        stack.enter_context(patch.object(refresh, "managed_repository_path", return_value=str(self.home)))
        self.upgrade = stack.enter_context(patch.object(refresh, "upgrade_channel", return_value={
            "commit": "a" * 40, "channel": "dev",
        }))
        self.execute = stack.enter_context(patch.object(refresh.os, "execv"))
        stack.enter_context(patch("sys.stdout", new_callable=io.StringIO))
        self.parser, _, _ = basaltwater.create_basaltwater_parser()
        self.record = self.home / ".local/state/basaltwater/cachyos/last-setup.json"

    def config(self, *options):
        return cachyos_config_from_args(self.parser.parse_args([
            "setup", "agent_cachyos", "localhost", "human", *options,
        ]))

    def merge(self, config, *options):
        overrides = self.parser.parse_args(["refresh", *options]).setup_overrides
        return refresh._merge_overrides(refresh._replay_arguments(config), overrides)

    def test_defaults_and_boolean_opt_outs(self):
        config = self.config()
        self.assertIsNone(config.install_ssh)
        self.assertFalse(config.ssh_password_auth)
        self.assertIsNone(config.ssh_password_window)
        self.assertNotIn("--ssh", refresh._replay_arguments(config))
        self.assertTrue(self.config("--ssh").install_ssh)
        self.assertFalse(self.config("--no-ssh").install_ssh)
        self.assertFalse(self.config("--ssh", "--no-ssh-password-auth").ssh_password_auth)

    def test_window_bounds_and_cli_integer_validation(self):
        for minutes in (1, 60):
            self.assertEqual(self.config("--ssh", "--lan-access", "--ssh-password-window",
                                         str(minutes)).ssh_password_window, minutes)
        for value in ("0", "61", "-1", "1.5", "true"):
            with self.subTest(value=value), patch("sys.stderr", new_callable=io.StringIO):
                with self.assertRaises(SystemExit):
                    self.config("--ssh", "--lan-access", "--ssh-password-window", value)

    def test_config_types_are_strict_for_direct_and_shared_parse(self):
        config = self.config()
        for field, values in (("install_ssh", (0, 1, "yes")),
                              ("ssh_password_auth", (None, 0, 1, "yes")),
                              ("ssh_password_window", (True, False, 0, 61, 1.5, "15"))):
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    replace(config, **{field: value})
                args = self.parser.parse_args(["setup", "agent_cachyos", "localhost", "human"])
                setattr(args, field, value)
                with self.subTest(field=field, value=value, path="cachyos"), self.assertRaises(ValueError):
                    cachyos_config_from_args(args)
        args = self.parser.parse_args(["setup", "agent_cachyos", "localhost", "human"])
        args.ssh_password_window = True
        with self.assertRaisesRegex(ValueError, "integer"):
            SetupConfig.from_args(args, "agent_cachyos")

    def test_other_profiles_reject_non_default_ssh_options(self):
        for options in (("--ssh",), ("--no-ssh",), ("--ssh-password-auth",),
                        ("--ssh-password-window", "15")):
            args = self.parser.parse_args(["setup", "server_lite", "localhost", "human", *options])
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, "agent_cachyos"):
                SetupConfig.from_args(args, "server_lite")

    def test_password_modes_require_ssh_private_selection_and_exclusivity(self):
        for mode in (("--ssh-password-auth",), ("--ssh-password-window", "15")):
            for prefix in ((), ("--no-ssh",), ("--ssh",)):
                with self.subTest(mode=mode, prefix=prefix), self.assertRaises(ValueError):
                    self.config(*prefix, *mode)
            for source in (("--lan-access",), ("--access-source", "192.168.1.2")):
                self.assertTrue(self.config("--ssh", *mode, *source).install_ssh)
            with self.assertRaisesRegex(ValueError, "private"):
                self.config("--ssh", *mode, "--access-source", "8.8.8.8")
        with self.assertRaisesRegex(ValueError, "mutually exclusive"):
            self.config("--ssh", "--lan-access", "--ssh-password-auth", "--ssh-password-window", "15")
        with self.assertRaisesRegex(ValueError, "private"):
            self.config("--ssh", "--ssh-password-auth", "--no-access-source")

    def test_t3_ports_are_reserved_only_for_selected_ssh_management(self):
        for selection in (("--ssh",), ("--no-ssh",)):
            with self.assertRaisesRegex(ValueError, "SSH ports"):
                self.config(*selection, "--web-interface", "t3code", "--web-interface-port", "22")
        with self.assertRaisesRegex(ValueError, "SSH ports"):
            self.config("--ssh", "--lan-access", "--ssh-password-window", "15",
                        "--web-interface", "t3code", "--web-interface-port", "2222")
        self.assertEqual(self.config("--ssh", "--web-interface", "t3code",
                                     "--web-interface-port", "2222").web_interface_port, 2222)
        config = self.config("--ssh")
        config.ssh_password_window = True
        with self.assertRaisesRegex(ValueError, "integer"):
            validate_cachyos_config(config)

    def test_permanent_auth_roundtrips_dictionary_command_and_saved_record(self):
        original = self.config("--ssh", "--ssh-password-auth", "--access-source", "10.0.0.2")
        restored = SetupConfig.from_dict(original.host, original.system_type, original.to_dict())
        self.assertTrue(restored.install_ssh)
        self.assertTrue(restored.ssh_password_auth)
        refresh.save_successful_setup(original)
        arguments, restored = refresh.load_saved_setup()
        self.assertIn("--ssh-password-auth", arguments)
        self.assertTrue(restored.ssh_password_auth)
        self.assertIn("--ssh-password-auth", shlex.split(" ".join(original.to_remote_args())))
        self.assertEqual(self.record.stat().st_mode & 0o777, 0o600)

    def test_refresh_disable_and_password_opt_out_clear_permanent_mode(self):
        config = self.config("--ssh", "--ssh-password-auth", "--lan-access", "--python")
        for options, enabled in ((("--no-ssh",), False), (("--no-ssh-password-auth",), True)):
            arguments, merged = self.merge(config, *options)
            self.assertIs(merged.install_ssh, enabled)
            self.assertFalse(merged.ssh_password_auth)
            self.assertIsNone(merged.ssh_password_window)
            self.assertTrue(merged.install_python)
            self.assertNotIn("--ssh-password-auth", arguments)
        with self.assertRaisesRegex(ValueError, "require --ssh"):
            self.merge(config, "--no-ssh", "--ssh-password-window", "15")

    def test_refresh_one_shot_executes_then_saves_key_only_without_losing_selections(self):
        original = self.config("--ssh", "--ssh-password-auth", "--access-source", "10.0.0.2",
                               "--python", "--agent-workspace", str(self.home / "projects"))
        refresh.save_successful_setup(original)
        before = self.record.read_bytes()
        args = self.parser.parse_args(["refresh", "--ssh", "--ssh-password-window", "15"])
        self.assertEqual(refresh.run_refresh_command(args), 0)
        arguments = self.execute.call_args.args[1][2:]
        self.assertIn("--ssh-password-window", arguments)
        self.assertEqual(arguments[arguments.index("--ssh-password-window") + 1], "15")
        merged = self.preflight.call_args.args[0]
        self.assertEqual(merged.ssh_password_window, 15)
        self.assertFalse(merged.ssh_password_auth)
        self.assertTrue(merged.install_python)
        self.assertEqual(merged.agent_workspace, original.agent_workspace)
        self.assertEqual(merged.access_sources, original.access_sources)
        self.assertEqual(self.record.read_bytes(), before)
        self.assertIn("--ssh-password-window", shlex.split(" ".join(merged.to_setup_command())))
        self.assertNotIn("--ssh-password-window", refresh._replay_arguments(merged))
        refresh.save_successful_setup(merged)
        saved, restored = refresh.load_saved_setup()
        self.assertNotIn("--ssh-password-window", saved)
        self.assertNotIn("--ssh-password-window", self.record.with_name("last-report.json").read_text())
        self.assertIsNone(restored.ssh_password_window)
        self.assertTrue(restored.install_ssh)
        self.assertFalse(restored.ssh_password_auth)

    def test_refresh_adds_one_shot_ssh_to_previously_unmanaged_selection(self):
        config = self.config("--python", "--lan-access", "--no-agent-tool", "gh")
        arguments, merged = self.merge(config, "--ssh", "--ssh-password-window", "15")
        self.assertTrue(merged.install_ssh)
        self.assertEqual(merged.ssh_password_window, 15)
        self.assertTrue(merged.install_python)
        self.assertTrue(merged.lan_access)
        self.assertEqual(merged.selected_agent_tools(), ["codex"])
        self.assertIn("--ssh-password-window", arguments)

    def test_saved_window_is_rejected_before_upgrade_or_override(self):
        config = self.config("--ssh", "--ssh-password-window", "15", "--lan-access")
        refresh.save_successful_setup(config)
        record = json.loads(self.record.read_text())
        record["arguments"].append("--ssh-password-window=15")
        self.record.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, "must not contain"):
            refresh.load_saved_setup()
        with self.assertRaisesRegex(ValueError, "must not contain"):
            refresh._merge_overrides(record["arguments"], {"install_ssh": ["--no-ssh"]})
        self.assertEqual(refresh.run_refresh_command(self.parser.parse_args(["refresh"])), 1)
        self.upgrade.assert_not_called()
        self.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
