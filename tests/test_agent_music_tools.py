"""Music tools use existing package selection and read-only discovery boundaries."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from common import cachyos_steps
from desktop import apps_steps
from lib import agent_environment, agent_workspace, cachyos, reconstruct
from lib.arg_parser import add_setup_arguments, create_setup_argument_parser
from lib.config import SetupConfig
from lib.cache import merge_setup_configs
from lib.display import print_setup_summary
from plugins.desktop import extend_desktop_media_steps


class MusicToolsTests(unittest.TestCase):
    def test_musescore_selection_round_trips_and_can_be_disabled(self):
        parser = create_setup_argument_parser("test")
        omitted = parser.parse_args(["vm.example", "agent"])
        self.assertIsNone(omitted.install_musescore)
        for flag, enabled in (("--musescore", True), ("--no-musescore", False)):
            args = parser.parse_args(["vm.example", "agent", flag])
            config = SetupConfig.from_args(args, "agent_code_vm")
            self.assertEqual(config.install_musescore, enabled)
            restored = SetupConfig.from_dict(config.host, config.system_type, config.to_dict())
            self.assertEqual(restored.install_musescore, enabled)
            remote = create_setup_argument_parser("remote", for_remote=True)
            remote_args = remote.parse_args(shlex.split(" ".join(config.to_remote_args())))
            self.assertEqual(remote_args.install_musescore, enabled)
            self.assertEqual("--musescore" in config.to_setup_command(), enabled)

    def test_musescore_rejects_unsupported_profiles(self):
        for profile in ("server_proxmox", "server_wsl"):
            with self.subTest(profile=profile), self.assertRaisesRegex(ValueError, "--musescore"):
                SetupConfig(host="vm.example", username="agent", system_type=profile, install_musescore=True)

    def test_patch_omission_retains_musescore_and_explicit_disable_clears_it(self):
        parser = create_setup_argument_parser("test")
        cached = SetupConfig.from_args(parser.parse_args(["vm.example", "agent", "--musescore"]), "agent_vm")
        omitted = SetupConfig.from_args(parser.parse_args(["vm.example", "agent"]), "agent_vm")
        disabled = SetupConfig.from_args(parser.parse_args(["vm.example", "agent", "--no-musescore"]), "agent_vm")
        self.assertTrue(merge_setup_configs(cached, omitted).install_musescore)
        self.assertFalse(merge_setup_configs(cached, disabled).install_musescore)
        self.assertIs(omitted.to_dict()["install_musescore"], False)

    def test_debian_installs_the_versioned_native_package_without_enabling_a_desktop(self):
        config = SetupConfig(host="vm.example", username="agent", system_type="agent_code_vm",
                             install_musescore=True, use_flatpak=True)
        steps = []
        extend_desktop_media_steps(config, steps)
        self.assertEqual(len(steps), 1)
        self.assertFalse(config.include_desktop)
        with patch.object(apps_steps, "install_package", return_value=True) as install:
            steps[0][1](config)
        install.assert_called_once_with(
            "MuseScore", "musescore3",
            ["apt-get", "install", "-y", "-qq", "--no-install-recommends", "musescore3"],
        )
        with patch.object(apps_steps, "install_package", return_value=False), self.assertRaises(RuntimeError):
            apps_steps.install_media_apps(config)

    def test_musescore_summary_identifies_debian_package_source(self):
        config = SetupConfig(host="localhost", username="agent", system_type="server_dev", install_musescore=True)
        with redirect_stdout(output := StringIO()):
            print_setup_summary(config)
        self.assertIn("MuseScore: Yes (Debian APT package)", output.getvalue())

    def test_cachyos_accepts_and_replays_musescore_with_native_command(self):
        parser = argparse.ArgumentParser()
        add_setup_arguments(parser, include_system_type=True)
        args = parser.parse_args(["agent_cachyos", "localhost", "agent", "--musescore"])
        config = cachyos.cachyos_config_from_args(args)
        self.assertTrue(config.install_musescore)
        self.assertIn("--musescore", config.to_setup_command())
        with patch.object(cachyos_steps, "_home", return_value=Path("/task/home")), patch.object(
            cachyos_steps.shutil, "which", return_value=None,
        ):
            packages = cachyos_steps.cachyos_packages(config)
        self.assertIn("musescore", packages)
        self.assertIn(("install_musescore", "mscore", "musescore"), cachyos_steps.CACHYOS_DESKTOP_PACKAGES)
        disabled = cachyos.cachyos_config_from_args(
            parser.parse_args(["agent_cachyos", "localhost", "agent", "--no-musescore"]),
        )
        self.assertFalse(disabled.install_musescore)

    def test_audio_bundle_is_optional_deduplicated_and_persisted_as_packages(self):
        parser = create_setup_argument_parser("test")
        args = parser.parse_args(["vm.example", "agent", "--apt-install", "sox",
                                  "--audio-tools", "--audio-tools"])
        self.assertEqual(args.apt_packages, ["sox", "libsox-fmt-all", "alsa-utils", "pulseaudio-utils"])
        config = SetupConfig.from_args(args, "agent_code_vm")
        saved = SetupConfig.from_dict(config.host, config.system_type, config.to_dict())
        remote = create_setup_argument_parser("remote", for_remote=True)
        self.assertEqual(remote.parse_args(shlex.split(" ".join(saved.to_remote_args()))).apt_packages,
                         args.apt_packages)
        self.assertIsNone(parser.parse_args(["vm.example", "agent"]).apt_packages)
        self.assertFalse(config.rdp_audio)
        self.assertTrue(all("--apt-install " + package in saved.to_setup_command()
                            for package in args.apt_packages))
        cachyos_parser = argparse.ArgumentParser()
        add_setup_arguments(cachyos_parser, include_system_type=True)
        with self.assertRaisesRegex(ValueError, "apt_packages"):
            cachyos.cachyos_config_from_args(cachyos_parser.parse_args(
                ["agent_cachyos", "localhost", "agent", "--audio-tools"],
            ))

    def test_discovery_resolves_each_alias_for_requirements_recipes_and_launches_without_execution(self):
        for native in (False, True):
            for alias in agent_environment.MUSESCORE_EXECUTABLES:
                with self.subTest(native=native, alias=alias), tempfile.TemporaryDirectory() as root:
                    Path(root, agent_environment.PROJECT_FILE).write_text(json.dumps({
                        "version": 1, "required_tools": ["musescore"],
                        "recipes": {"score": {"description": "Inspect a score", "argv": ["python3", "inspect.py"],
                                              "requires": ["musescore"]}},
                    }))
                    executable = "/bin/" + alias
                    with (
                        patch.object(agent_workspace, "_repository_root", return_value=root),
                        patch.object(agent_workspace, "_effective_home", return_value=root),
                        patch.object(agent_workspace, "_worktree_record", return_value={
                            "branch": "main", "head": "a" * 40, "dirty": False,
                        }),
                        patch.object(agent_environment, "is_cachyos", return_value=native),
                        patch.object(agent_environment.shutil, "which", side_effect=lambda name:
                                     executable if name == alias else
                                     ("/old/musescore" if name == "musescore" and alias.endswith(("3", "4")) else None)),
                        patch.object(agent_environment.subprocess, "run") as execute,
                    ):
                        result = agent_environment.inspect_environment(root)
                    app = result["desktop_applications"]["musescore"]
                    self.assertEqual(app["executable"], executable)
                    self.assertEqual(app["readiness"], "unverified")
                    self.assertEqual(app["launch_argv"], [executable] if native else
                                     ["basaltw", "desktop", "exec", "--", executable])
                    self.assertTrue(any(executable in instruction for instruction in app["instructions"]))
                    self.assertEqual(result["required_tools"]["musescore"]["status"], "available")
                    self.assertEqual(result["recipes"]["score"]["missing_tools"], [])
                    for tool in ("sox", "soxi", "arecord", "aplay", "amidi", "aconnect", "pactl", "parecord"):
                        self.assertIn(tool, result["tools"])
                    execute.assert_not_called()

    def test_reconstruction_identifies_installed_debian_musescore_without_launching_qt(self):
        with (
            patch.object(reconstruct, "check_package_installed", side_effect=lambda package: package == "musescore3"),
            patch.object(reconstruct, "detect_go", return_value=False),
            patch.object(reconstruct, "detect_node", return_value=False),
            patch.object(reconstruct, "detect_python", return_value=False),
            patch.object(reconstruct, "detect_samba", return_value=False),
            patch.object(reconstruct, "detect_deployments", return_value=[]),
            patch.object(reconstruct, "detect_sync_operations", return_value=[]),
            patch.object(reconstruct, "detect_scrub_operations", return_value=[]),
            patch.object(reconstruct, "detect_smb_mounts", return_value=[]),
        ):
            config, _ = reconstruct.reconstruct_configuration(username="agent")
        self.assertTrue(config.install_musescore)


if __name__ == "__main__":
    unittest.main()
