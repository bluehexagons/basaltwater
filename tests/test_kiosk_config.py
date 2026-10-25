"""Contracts for optional Proxmox kiosk CLI, saved state, and preflight."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import shlex
import tempfile
import unittest
from unittest.mock import patch

import basaltwater
import remote_setup
from lib import setup_common
from lib.arg_parser import create_setup_argument_parser
from lib.cache import load_setup_command, merge_setup_configs, save_setup_command
from lib.config import SetupConfig
from lib.display import print_setup_summary
from lib.kiosk_config import (
    parse_kiosk_destination,
    validate_kiosk_destinations,
    validate_kiosk_settings,
    validate_kiosk_url,
)


def kiosk_config(**overrides) -> SetupConfig:
    values = dict(host="pve.example", username="root", system_type="server_proxmox")
    values.update(overrides)
    return SetupConfig(**values)


class TestKioskURL(unittest.TestCase):
    def test_valid_urls_are_preserved(self):
        for value in (
            "http://localhost", "https://pve.example:8006/", "http://192.0.2.1/status",
            "https://[2001:db8::1]:8006/#v1:0:=node/pve", "http://[::1]/",
            "HTTPS://PVE.Example./path?q=a%20b&next=/other#fragment",
            "https://pve.example/a'b;$HOME?x=$(id)&y=`id`#section",
        ):
            with self.subTest(value=value):
                self.assertEqual(validate_kiosk_url(value), value)

    def test_malformed_or_unsafe_urls_are_rejected(self):
        for value in (
            None, 42, "", "/relative", "pve.example", "//pve.example/", "ftp://pve.example/",
            "file:///etc/passwd", "javascript:alert(1)", "http:///path", "http://",
            "http://user@pve.example/", "https://user:password@pve.example/",
            "http://@pve.example/", "http://pve.example\\@other.example/",
            "http://pve.example/a\\b", " http://pve.example/", "http://pve.example/a b",
            "http://pve.example/\n", "http://pve.\texample/", "http://pve.example/\x00",
            "http://pve.example/\x7f", "http://pve.example/\x85", "http://pve.example/\u200b",
            "http://bad_host/", "http://-bad.example/", "http://pve.example../",
            "http://pve.example:/", "http://pve.example:0/", "http://pve.example:65536/",
            "http://pve.example:-1/", "http://pve.example:abc/", "http://pve.example:８０/",
            "http://pve.example:80:90/", "http://[::1", "http://::1/", "http://[]/",
            "http://[::1]junk/", "http://[::1]:/", "http://[::1]:80:90/",
            "http://[127.0.0.1]/", "http://[fe80::1%25eth0]/", "http://[v1.example]/",
            "http://999.0.0.1/", "http://127.1/", "http://2130706433/", "http://0177.0.0.1/",
            "http://%65xample.com/", "http://pve.example/%", "http://pve.example/%xx",
            "http://pve.example/" + "a" * 8192,
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_kiosk_url(value)


class TestKioskSettings(unittest.TestCase):
    def test_modes_and_root_account(self):
        for mode in (None, "web", "cli", "off"):
            with self.subTest(mode=mode):
                config = kiosk_config(kiosk_mode=mode)
                validate_kiosk_settings(config)
                self.assertEqual(config.username, "root")
                self.assertFalse(config.include_desktop)
                self.assertFalse(config.enable_rdp)

    def test_invalid_mode_and_url_types(self):
        for mode in (True, "", "on", "WEB", [], 1):
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, "kiosk_mode"):
                kiosk_config(kiosk_mode=mode)
        with self.assertRaises(ValueError):
            kiosk_config(kiosk_mode="web", kiosk_url=False)

    def test_profile_restrictions_include_opt_out_and_partial_url(self):
        for system_type in ("server_lite", "server_web", "agent_vm", "agent_cachyos", "server_wsl", "custom_steps"):
            for settings in ({"kiosk_mode": "web"}, {"kiosk_mode": "cli"}, {"kiosk_mode": "off"},
                             {"kiosk_url": "http://localhost/"}, {"kiosk_destinations": []},
                             {"kiosk_destinations": [{"name": "Local", "url": "http://localhost/"}]}):
                with self.subTest(system_type=system_type, settings=settings), self.assertRaisesRegex(ValueError, "server_proxmox"):
                    kiosk_config(system_type=system_type, username="operator", **settings)

    def test_shared_desktop_is_incompatible(self):
        for setting in ("include_desktop", "enable_rdp", "guacamole"):
            with self.subTest(setting=setting), self.assertRaisesRegex(ValueError, "shared desktop"):
                kiosk_config(kiosk_mode="web", **{setting: True})

    def test_dedicated_kiosk_does_not_reject_hardened_setup_user(self):
        validate_kiosk_settings(kiosk_config(kiosk_mode="web", username="operator", harden_user=True))

    def test_persistent_service_capability_checked_only_for_enabled_complete_plan(self):
        with patch("lib.kiosk_config.can_manage_system_services", return_value=False) as capability:
            config = kiosk_config(kiosk_mode="web", machine_type="oci")
            with self.assertRaisesRegex(ValueError, "persistent system services"):
                validate_kiosk_settings(config)
            capability.assert_called_once_with("oci")
            validate_kiosk_settings(kiosk_config(kiosk_mode="off", machine_type="oci"))
            validate_kiosk_settings(kiosk_config(kiosk_mode="web", machine_type="auto"))

    def test_url_only_is_partial_until_inheritance(self):
        config = kiosk_config(kiosk_url="http://localhost/")
        validate_kiosk_settings(config, complete=False)
        with self.assertRaisesRegex(ValueError, "enabled kiosk"):
            validate_kiosk_settings(config)
        with self.assertRaisesRegex(ValueError, "enabled kiosk"):
            config.to_remote_args()
        with self.assertRaisesRegex(ValueError, "enabled kiosk"):
            kiosk_config(kiosk_mode="off", kiosk_url="http://localhost/")


class TestKioskDestinations(unittest.TestCase):
    def test_valid_schema_and_boundaries_are_preserved(self):
        destinations = [
            {"name": "🖥" * 80, "url": "http://localhost/"},
            *[{"name": f"Node {index}", "url": "https://pve.example:8006/"} for index in range(15)],
        ]
        self.assertIs(validate_kiosk_destinations(destinations), destinations)
        empty = []
        self.assertIs(validate_kiosk_destinations(empty), empty)
        self.assertIsNone(validate_kiosk_destinations(None))

    def test_invalid_schema_names_urls_and_duplicates_are_rejected(self):
        def item(name="Node", url="https://pve.example/"):
            return {"name": name, "url": url}

        for value in (
            False, "Node=https://pve.example/", {}, (), [None], [[]],
            [{"name": "Node"}], [{"url": "http://localhost/"}],
            [{**item(), "extra": "value"}],
            *[[item(name)] for name in (None, 1, "", " ", " Node", "Node ", "x" * 81,
                                        "Node\n", "No\tde", "No\x00de", "No\x7fde",
                                        "No\x85de", "No\u200bde", "No\u202ede")],
            *[[item(url=url)] for url in (None, False, "", "file:///etc/passwd",
                                          "https://user:secret@pve.example/", "https://pve.example/a b")],
            [item("Node"), item("NODE")], [item("Straße"), item("STRASSE")],
            [item(f"Node {index}") for index in range(17)],
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_kiosk_destinations(value)

    def test_parser_trims_name_and_splits_only_first_equals(self):
        self.assertEqual(
            parse_kiosk_destination("  Main console  =https://pve.example/?a=b=c#home"),
            {"name": "Main console", "url": "https://pve.example/?a=b=c#home"},
        )
        for value in ("No separator", "=http://localhost/", "   =http://localhost/", "Name=",
                      "Name=file:///etc/passwd", "Name\n=http://localhost/", "\x85Name=http://localhost/"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_kiosk_destination(value)

    def test_partial_nonempty_list_requires_enabled_mode_when_complete(self):
        destinations = [{"name": "Node", "url": "http://localhost/"}]
        partial = kiosk_config(kiosk_destinations=destinations)
        validate_kiosk_settings(partial, complete=False)
        for complete in (validate_kiosk_settings, SetupConfig.to_remote_args, SetupConfig.to_setup_command):
            with self.subTest(complete=complete.__name__), self.assertRaisesRegex(ValueError, "enabled kiosk"):
                complete(partial)
        with self.assertRaisesRegex(ValueError, "enabled kiosk"):
            kiosk_config(kiosk_mode="off", kiosk_destinations=destinations)
        for mode in (None, "off", "web", "cli"):
            with self.subTest(mode=mode):
                validate_kiosk_settings(kiosk_config(kiosk_mode=mode, kiosk_destinations=[]))


class TestKioskCLI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parser, _, _ = basaltwater.create_basaltwater_parser()
        cls.remote_parser = create_setup_argument_parser("test", for_remote=True)

    def parse_config(self, *flags: str) -> SetupConfig:
        args = self.parser.parse_args(["setup", "server_proxmox", "pve.example", "root", *flags])
        return SetupConfig.from_args(args, args.system_type)

    def test_setup_patch_remote_defaults_are_omitted(self):
        self.assertIsNone(self.parse_config().kiosk_mode)
        self.assertIsNone(self.parse_config().kiosk_destinations)
        for args in (self.parser.parse_args(["patch", "pve.example"]), self.remote_parser.parse_args([])):
            self.assertIsNone(args.kiosk_mode)
            self.assertIsNone(args.kiosk_url)
            self.assertIsNone(args.kiosk_destinations)
        self.assertFalse(any("kiosk" in part for part in self.parse_config().to_remote_args()))

    def test_mutually_exclusive_mode_flags(self):
        for prefix in (["setup", "server_proxmox", "pve.example"], ["patch", "pve.example"]):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.parser.parse_args([*prefix, "--kiosk", "web", "--no-kiosk"])
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.parser.parse_args(["patch", "pve.example", "--kiosk", "off"])

    def test_explicit_off_and_url_and_desktop_flags_rejected(self):
        for flags in (("--no-kiosk", "--kiosk-url", "http://localhost/"),
                      ("--kiosk", "web", "--desktop", "xfce"),
                      ("--kiosk", "cli", "--rdp"), ("--kiosk", "web", "--guacamole")):
            with self.subTest(flags=flags), self.assertRaises(ValueError):
                self.parse_config(*flags)

    def test_remote_and_reconstructed_command_roundtrip_with_shell_metacharacters(self):
        url = "https://[::1]:8006/a'b?x=$(id)&y=`id`;v=$HOME#fragment"
        for mode in ("web", "cli", "off"):
            with self.subTest(mode=mode):
                flags = ["--no-kiosk"] if mode == "off" else ["--kiosk", mode, "--kiosk-url", url]
                config = self.parse_config(*flags)
                argv = shlex.split(" ".join(config.to_remote_args()))
                with patch("remote_setup.resolve_machine_type", return_value="hardware"):
                    remote = remote_setup.config_from_remote_args(self.remote_parser.parse_args(argv))
                reconstructed_args = self.parser.parse_args(shlex.split(" ".join(config.to_setup_command()))[1:])
                reconstructed = SetupConfig.from_args(reconstructed_args, reconstructed_args.system_type)
                for result in (remote, reconstructed):
                    self.assertEqual(result.kiosk_mode, mode)
                    self.assertEqual(result.kiosk_url, None if mode == "off" else url)

    def test_patch_inheritance_and_explicit_off_clear_saved_url(self):
        saved = self.parse_config("--kiosk", "web", "--kiosk-url", "http://localhost/old")
        for flags, expected_mode, expected_url in (
            ([], "web", "http://localhost/old"),
            (["--kiosk-url", "https://pve.example/new#home"], "web", "https://pve.example/new#home"),
            (["--kiosk", "cli"], "cli", "http://localhost/old"),
            (["--no-kiosk"], "off", None),
        ):
            with self.subTest(flags=flags):
                args = self.parser.parse_args(["patch", "pve.example", "root", *flags])
                new = SetupConfig.from_args(args, "server_proxmox")
                merged = merge_setup_configs(saved, new, preserve_keys=basaltwater._patch_preserve_keys(args))
                validate_kiosk_settings(merged)
                self.assertEqual((merged.kiosk_mode, merged.kiosk_url), (expected_mode, expected_url))
        self.assertEqual(saved.kiosk_url, "http://localhost/old")

    def test_url_only_patch_requires_inherited_enabled_mode(self):
        args = self.parser.parse_args(["patch", "pve.example", "root", "--kiosk-url", "http://localhost/"])
        new = SetupConfig.from_args(args, "server_proxmox")
        for mode in (None, "off"):
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, "enabled kiosk"):
                merged = merge_setup_configs(kiosk_config(kiosk_mode=mode), new)
                validate_kiosk_settings(merged)

    def test_enabling_kiosk_patch_rejects_saved_shared_desktop_before_execute(self):
        saved = kiosk_config(include_desktop=True)
        args = self.parser.parse_args(["patch", "pve.example", "root", "--kiosk", "web"])
        with patch("basaltwater.load_setup_command", return_value=saved), \
             patch("basaltwater._execute_patch_config") as execute, redirect_stdout(io.StringIO()) as output:
            self.assertEqual(basaltwater.run_patch_command(args), 1)
        execute.assert_not_called()
        self.assertIn("shared desktop", output.getvalue())

    def test_saved_config_roundtrips_modes_and_old_cache(self):
        for mode in (None, "web", "cli", "off"):
            config = kiosk_config(kiosk_mode=mode, kiosk_url="http://localhost/" if mode in {"web", "cli"} else None)
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as workspace, \
                 patch("lib.cache.get_setup_cache_dir", return_value=workspace):
                save_setup_command(config)
                restored = load_setup_command(config.host)
                self.assertEqual((restored.kiosk_mode, restored.kiosk_url), (mode, config.kiosk_url))
        old = SetupConfig.from_dict("pve.example", "server_proxmox", {"username": "root"})
        self.assertIsNone(old.kiosk_mode)
        self.assertIsNone(old.kiosk_url)
        self.assertIsNone(old.kiosk_destinations)

    def test_destination_flags_parse_repeats_and_clear_for_setup_patch_and_remote(self):
        for parser, prefix in (
            (self.parser, ["setup", "server_proxmox", "pve.example", "root"]),
            (self.parser, ["patch", "pve.example", "root"]),
            (self.remote_parser, []),
        ):
            with self.subTest(prefix=prefix):
                args = parser.parse_args([*prefix, "--kiosk-destination", " First =https://pve.example/?a=b", "--kiosk-destination", "Second=http://localhost/"])
                self.assertEqual(args.kiosk_destinations, [
                    {"name": "First", "url": "https://pve.example/?a=b"},
                    {"name": "Second", "url": "http://localhost/"},
                ])
                self.assertEqual(parser.parse_args([*prefix, "--clear-kiosk-destinations"]).kiosk_destinations, [])
                for flags in (
                    ["--kiosk-destination", "missing separator"],
                    ["--kiosk-destination", "Name=file:///etc/passwd"],
                    ["--kiosk-destination", "Name=http://localhost/", "--clear-kiosk-destinations"],
                    ["--clear-kiosk-destinations", "--kiosk-destination", "Name=http://localhost/"],
                ):
                    with self.subTest(flags=flags), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                        parser.parse_args([*prefix, *flags])

    def test_destination_roundtrip_preserves_omitted_empty_and_populated(self):
        destinations = [
            {"name": "-Operator's $HOME; `id`", "url": "https://[::1]:8006/?a=b&c=$(id)#home"},
            {"name": 'Other "console"', "url": "https://pve.example/a'b?v=$HOME"},
        ]
        for mode, value in ((None, None), (None, []), ("off", []), ("web", destinations), ("cli", destinations)):
            config = kiosk_config(kiosk_mode=mode, kiosk_destinations=value)
            with self.subTest(mode=mode, destinations=value):
                remote_args = self.remote_parser.parse_args(shlex.split(" ".join(config.to_remote_args())))
                with patch("remote_setup.resolve_machine_type", return_value="hardware"):
                    remote = remote_setup.config_from_remote_args(remote_args)
                setup_args = self.parser.parse_args(shlex.split(" ".join(config.to_setup_command()))[1:])
                reconstructed = SetupConfig.from_args(setup_args, setup_args.system_type)
                for result in (remote, reconstructed):
                    self.assertEqual(result.kiosk_mode, mode)
                    self.assertEqual(result.kiosk_destinations, value)

    def test_destination_cache_roundtrip_and_invalid_saved_schema(self):
        for value in (None, [], [{"name": "Node", "url": "https://pve.example:8006/"}]):
            config = kiosk_config(kiosk_mode="web", kiosk_destinations=value)
            with self.subTest(value=value), tempfile.TemporaryDirectory() as workspace, \
                 patch("lib.cache.get_setup_cache_dir", return_value=workspace):
                save_setup_command(config)
                restored = load_setup_command(config.host)
                self.assertEqual(restored.kiosk_destinations, value)
        for invalid in ({}, [{"name": "Node", "url": "file:///etc/passwd"}], [{"name": "Node"}]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                SetupConfig.from_dict("pve.example", "server_proxmox", {
                    "username": "root", "kiosk_mode": "web", "kiosk_destinations": invalid,
                })

    def test_destination_patch_inheritance_replacement_clear_and_disable(self):
        original = [{"name": "Saved", "url": "http://localhost/old"}]
        saved = kiosk_config(kiosk_mode="web", kiosk_url="http://localhost/home", kiosk_destinations=original)
        for flags, expected in (
            ([], original),
            (["--kiosk", "cli"], original),
            (["--kiosk-destination", "New=https://pve.example/new?a=b"], [{"name": "New", "url": "https://pve.example/new?a=b"}]),
            (["--clear-kiosk-destinations"], []),
            (["--no-kiosk"], []),
        ):
            args = self.parser.parse_args(["patch", "pve.example", "root", *flags])
            new = SetupConfig.from_args(args, "server_proxmox")
            merged = merge_setup_configs(saved, new, preserve_keys=basaltwater._patch_preserve_keys(args))
            with self.subTest(flags=flags):
                validate_kiosk_settings(merged)
                self.assertEqual(merged.kiosk_destinations, expected)
                self.assertEqual(merged.kiosk_url, None if "--no-kiosk" in flags else saved.kiosk_url)
        self.assertEqual(saved.kiosk_destinations, original)

    def test_destinations_only_patch_requires_inherited_enabled_mode(self):
        args = self.parser.parse_args(["patch", "pve.example", "root", "--kiosk-destination", "Node=http://localhost/"])
        new = SetupConfig.from_args(args, "server_proxmox")
        for mode in (None, "off"):
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, "enabled kiosk"):
                merged = merge_setup_configs(kiosk_config(kiosk_mode=mode), new)
                validate_kiosk_settings(merged)
        with self.assertRaisesRegex(ValueError, "enabled kiosk"):
            self.parse_config("--no-kiosk", "--kiosk-destination", "Node=http://localhost/")

    def test_repeated_destination_flags_validate_the_complete_list(self):
        for specs in (
            ["Node=http://localhost/", "NODE=https://pve.example/"],
            [f"Node {index}=http://localhost/" for index in range(17)],
        ):
            flags = [part for spec in specs for part in ("--kiosk-destination", spec)]
            with self.subTest(specs=specs), self.assertRaises(ValueError):
                self.parse_config("--kiosk", "web", *flags)

    def test_patch_summary_reports_destination_count(self):
        config = kiosk_config(kiosk_mode="web", kiosk_destinations=[{"name": "Node", "url": "http://localhost/"}])
        with patch("basaltwater.os.path.exists", return_value=True), \
             patch("basaltwater._prepare_runtime_config_for_cli", side_effect=ValueError("stop before execution")), \
             redirect_stdout(io.StringIO()) as output:
            self.assertEqual(basaltwater._execute_patch_config(config), 1)
        self.assertIn("Kiosk destinations: 1", output.getvalue())

    def test_invalid_destinations_rejected_before_staging_or_credentials(self):
        config = kiosk_config(kiosk_destinations=[{"name": "Node", "url": "http://localhost/"}])
        with patch("lib.setup_common.prepare_runtime_config") as credentials, \
             patch("lib.setup_common.copy_project_files") as stage, \
             patch("lib.setup_common.ensure_remote_sudo") as ssh, \
             self.assertRaisesRegex(ValueError, "enabled kiosk"):
            setup_common.run_remote_setup(config)
        credentials.assert_not_called()
        stage.assert_not_called()
        ssh.assert_not_called()
        config.kiosk_mode = "web"
        config.kiosk_destinations[0]["url"] = "file:///etc/passwd"
        for prepare in (SetupConfig.to_remote_args, SetupConfig.to_setup_command,
                        basaltwater._prepare_runtime_config_for_cli):
            with self.subTest(prepare=prepare.__name__), \
                 patch("basaltwater.prepare_runtime_config") as credentials, self.assertRaises(ValueError):
                prepare(config)
            credentials.assert_not_called()

    def test_invalid_completed_config_rejected_before_staging_or_credentials(self):
        config = kiosk_config(kiosk_url="http://localhost/")
        for prepare in (setup_common.prepare_validated_runtime_config, basaltwater._prepare_runtime_config_for_cli):
            with self.subTest(prepare=prepare.__name__), \
                 patch("lib.setup_common.prepare_runtime_config") as common_credentials, \
                 patch("basaltwater.prepare_runtime_config") as cli_credentials, \
                 self.assertRaisesRegex(ValueError, "enabled kiosk"):
                if prepare is setup_common.prepare_validated_runtime_config:
                    prepare(config, None)
                else:
                    prepare(config)
            common_credentials.assert_not_called()
            cli_credentials.assert_not_called()
        with patch("lib.setup_common.copy_project_files") as stage, \
             patch("lib.setup_common.ensure_remote_sudo") as ssh, \
             self.assertRaisesRegex(ValueError, "enabled kiosk"):
            setup_common.run_remote_setup(config)
        stage.assert_not_called()
        ssh.assert_not_called()

    def test_remote_rejects_url_only_or_resolved_unsupported_machine_before_steps(self):
        for flags in (["--kiosk-url", "http://localhost/"], ["--kiosk", "web"]):
            with self.subTest(flags=flags), patch("remote_setup.resolve_machine_type", return_value="oci"), \
                 patch("remote_setup.get_steps_for_system_type") as steps, \
                 patch("remote_setup._begin_setup_operation") as begin, \
                 patch("remote_setup.detect_os") as detect, redirect_stdout(io.StringIO()), \
                 patch("lib.cachyos.is_cachyos", return_value=False), \
                 patch("sys.argv", ["remote_setup.py", "--system-type", "server_proxmox", "--username", "root", *flags]):
                self.assertEqual(remote_setup._run_main(), 1)
            steps.assert_not_called()
            begin.assert_not_called()
            detect.assert_not_called()

    def test_summary_reports_boot_mode_and_managed_removal(self):
        for mode, expected in (("web", "browser at boot"), ("cli", "console at boot"), ("off", "remove managed configuration")):
            with self.subTest(mode=mode), redirect_stdout(io.StringIO()) as output:
                print_setup_summary(kiosk_config(kiosk_mode=mode))
            self.assertIn(expected, output.getvalue())
