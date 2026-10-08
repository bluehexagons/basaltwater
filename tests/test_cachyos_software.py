"""Issue 106 software: mocked package operations, no live installs or launches."""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
import shlex
from subprocess import CompletedProcess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import basaltwater
import remote_setup
from common import cachyos_aur as aur
from common import cachyos_software as software
from common import cachyos_steps as steps
from lib.cachyos import cachyos_config_from_args
from lib.config import SetupConfig
from lib.system_types import get_steps_for_system_type


FLAGS = ("--material-maker", "--etcher", "--butler", "--steamcmd")


class SoftwareTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.home = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(software, "_home", return_value=self.home))
        self.stack.enter_context(patch.object(software.os, "geteuid", return_value=1000))
        self.stack.enter_context(patch.object(software, "is_dry_run", return_value=False))
        self.stack.enter_context(patch.dict(software.os.environ, {"XDG_CACHE_HOME": ""}))
        self.which = self.stack.enter_context(patch.object(software.shutil, "which", side_effect=
            lambda name, **kw: "/usr/bin/" + name if name == "shelly" or name in self.installed
            or (name == "material-maker" and "material-maker-bin" in self.installed) else None))
        self.installed = {}
        self.events = []
        self.run = self.stack.enter_context(patch.object(aur, "run", side_effect=self.command))
        self.stack.enter_context(patch.object(software, "run", self.run))

    def config(self, *options):
        parser, _, _ = basaltwater.create_basaltwater_parser()
        return cachyos_config_from_args(parser.parse_args([
            "setup", "agent_cachyos", "localhost", "human", *options,
        ]))

    def command(self, argv, **kwargs):
        self.events.append((argv, kwargs))
        if argv[:2] == ["pacman", "-Q"]:
            package = argv[-1]
            return CompletedProcess(argv, 0 if package in self.installed else 1,
                                    f"{package} {self.installed[package]}\n" if package in self.installed else "")
        if argv[:2] == ["pacman", "-Si"]:
            return CompletedProcess(argv, 0, "")
        if argv[:2] == ["pacman", "-Qqo"]:
            name = Path(argv[-1]).name
            package = "material-maker-bin" if name == "material-maker" and "material-maker-bin" in self.installed else name
            return CompletedProcess(argv, 0, package + "\n")
        if argv[0] == "/usr/bin/shelly":
            self.assertTrue(kwargs["interactive"])
            self.assertTrue((self.home / ".cache/Shelly").is_dir())
            self.installed[argv[-1]] = "1.2-1"
            return CompletedProcess(argv, 0, "")
        self.fail(f"Unexpected host operation: {argv}")

    def test_flags_roundtrip_and_other_profiles_reject_them(self):
        config = self.config(*FLAGS)
        parser, _, _ = basaltwater.create_basaltwater_parser()
        reconstructed = cachyos_config_from_args(parser.parse_args(shlex.split(" ".join(config.to_setup_command()))[1:]))
        restored = SetupConfig.from_dict("localhost", "agent_cachyos", config.to_dict())
        for flag in FLAGS:
            field = "install_" + flag[2:].replace("-", "_")
            self.assertTrue(getattr(reconstructed, field))
            self.assertTrue(getattr(restored, field))
            self.assertIn(flag, config.to_remote_args())
            with self.assertRaisesRegex(ValueError, "agent_cachyos"):
                SetupConfig(host="localhost", username="human", system_type="server_lite", **{field: True})
            self.assertFalse(getattr(self.config(flag, "--no-" + flag[2:]), field))

    def test_repository_package_mapping_and_existing_graphics_dependencies(self):
        config = self.config(*FLAGS, "--godot", "--blender", "--gimp", "--inkscape", "--krita",
                             "--remmina", "--sunshine", "--moonlight")
        with patch.object(steps, "_home", return_value=self.home):
            packages = steps.cachyos_packages(config)
        for package in ("etcher-bin", "godot", "blender", "libdecor", "gimp", "inkscape", "krita",
                        "remmina", "freerdp", "libvncserver", "spice-gtk", "gtk-vnc", "libsecret",
                        "sunshine", "moonlight-qt", "lib32-glibc", "lib32-gcc-libs"):
            self.assertIn(package, packages)
        for package in ("material-maker-bin", "butler", "steamcmd"):
            self.assertNotIn(package, packages)
        self.assertFalse(any("nvidia" in package or "cuda" in package for package in packages))

    def test_optional_step_comes_after_dependencies_before_readiness_and_save(self):
        self.assertNotIn(software.install_software, [step for _, step in get_steps_for_system_type(self.config())])
        functions = [step for _, step in get_steps_for_system_type(self.config(*FLAGS))]
        self.assertLess(functions.index(steps.install_cachyos_packages), functions.index(software.install_software))
        self.assertLess(functions.index(software.install_software), functions.index(steps.report_cachyos_readiness))
        self.assertIs(functions[-1], steps.save_cachyos_setup)

    def test_preflight_only_queries_and_does_not_create_cache(self):
        software.preflight_software(self.config(*FLAGS))
        self.assertTrue(all(command[0] == "pacman" for command, _ in self.events))
        self.assertEqual(list(self.home.iterdir()), [])

    def test_install_keeps_review_prompts_and_rerun_does_not_upgrade(self):
        config = self.config(*FLAGS)
        software.install_software(config)
        calls = [command for command, _ in self.events if command[0] == "/usr/bin/shelly"]
        self.assertEqual(calls, [["/usr/bin/shelly", "install", "aur", package]
                                 for package in ("material-maker-bin", "butler", "steamcmd")])
        self.events.clear()
        software.install_software(config)
        self.assertTrue(all(command[0] == "pacman" for command, _ in self.events))

    def test_installed_source_material_maker_retained_without_helper(self):
        self.installed["material-maker"] = "1.6-1"
        self.which.side_effect = lambda name, **kw: "/usr/bin/material-maker" if name == "material-maker" else None
        software.install_software(self.config("--material-maker"))
        self.assertNotIn("material-maker-bin", self.installed)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_external_tool_is_not_replaced(self):
        self.which.side_effect = lambda name, **kw: "/personal/bin/butler" if name == "butler" else None
        with self.assertRaisesRegex(ValueError, "original manager"):
            software.install_software(self.config("--butler"))
        self.assertTrue(all(command[0] == "pacman" for command, _ in self.events))

    def test_missing_helper_and_missing_runtime_dependency_fail_before_mutation(self):
        self.which.side_effect = None
        self.which.return_value = None
        with self.assertRaisesRegex(RuntimeError, "requires Shelly"):
            software.preflight_software(self.config("--butler"))
        self.installed["steamcmd"] = "latest-7"
        self.which.side_effect = lambda name, **kw: "/usr/bin/steamcmd" if name == "steamcmd" else None
        self.run.side_effect = lambda argv, **kw: (CompletedProcess(argv, 1, "")
            if argv[:2] == ["pacman", "-Si"] else self.command(argv, **kw))
        with self.assertRaisesRegex(RuntimeError, "SteamCMD requires lib32-glibc"):
            software.preflight_software(self.config("--steamcmd"))
        self.assertEqual(list(self.home.iterdir()), [])

    def test_unowned_aur_helper_stops_preflight_before_install(self):
        self.which.side_effect = lambda name, **kw: "/personal/bin/shelly" if name == "shelly" else None
        self.run.side_effect = lambda argv, **kw: (CompletedProcess(argv, 1, "")
            if argv[:2] == ["pacman", "-Qqo"] else self.command(argv, **kw))
        with self.assertRaisesRegex(RuntimeError, "not owned"):
            software.preflight_software(self.config("--butler"))
        self.assertEqual(list(self.home.iterdir()), [])
        self.assertFalse(any(command[0] == "/personal/bin/shelly" for command, _ in self.events))

    def test_installed_package_missing_or_shadowed_command_fails_preflight(self):
        self.installed["butler"] = "15.31.0-1"
        self.which.side_effect = None
        self.which.return_value = None
        with self.assertRaisesRegex(RuntimeError, "executable missing"):
            software.preflight_software(self.config("--butler"))
        self.which.return_value = "/personal/bin/butler"
        self.run.side_effect = lambda argv, **kw: (CompletedProcess(argv, 1, "")
            if argv[:2] == ["pacman", "-Qqo"] else self.command(argv, **kw))
        with self.assertRaisesRegex(RuntimeError, "not owned"):
            software.preflight_software(self.config("--butler"))
        self.assertEqual(list(self.home.iterdir()), [])

    def test_cancelled_install_is_not_success(self):
        self.run.side_effect = lambda argv, **kw: (CompletedProcess(argv, 0, "")
            if argv[0] == "/usr/bin/shelly" else self.command(argv, **kw))
        with self.assertRaisesRegex(RuntimeError, "cancellation is not success"):
            software.install_software(self.config("--butler"))

    def test_failed_helper_does_not_try_another_source(self):
        self.run.side_effect = lambda argv, **kw: (_ for _ in ()).throw(
            aur.CommandExecutionError("shelly", 1, "fixture")) if argv[0] == "/usr/bin/shelly" else self.command(argv, **kw)
        with self.assertRaisesRegex(RuntimeError, "AUR installation failed"):
            software.install_software(self.config("--butler"))
        self.assertEqual(len([call for call in self.run.call_args_list if call.args[0][0] != "pacman"]), 1)

    def test_readiness_does_not_launch_self_updaters_or_gui(self):
        self.installed.update(butler="15.31.0-1", steamcmd="latest-7")
        self.which.side_effect = lambda name, **kw: "/usr/bin/" + name
        software.report_software_readiness(self.config("--butler", "--steamcmd"))
        self.assertTrue(all(command[:2] in (["pacman", "-Q"], ["pacman", "-Qqo"]) for command, _ in self.events))
        self.run.return_value = CompletedProcess([], 0, "wrong-owner\n")
        self.run.side_effect = None
        with patch.object(software, "installed_package", return_value="butler"), \
                self.assertRaisesRegex(RuntimeError, "not owned"):
            software.report_software_readiness(self.config("--butler"))

    def test_preview_never_probes_or_installs(self):
        config = self.config(*FLAGS, "--dry-run")
        software.preflight_software(config)
        software.install_software(config)
        self.assertEqual(remote_setup.run_cachyos_setup(config), 0)
        self.run.assert_not_called()
        self.assertEqual(list(self.home.iterdir()), [])

    def test_invalid_package_input_rejected_before_host_commands(self):
        with self.assertRaises(ValueError):
            aur.install_command(self.home, "butler; false")
        with self.assertRaises(ValueError):
            aur.package_version("../../bad")
        self.run.assert_not_called()

    def test_doctor_reports_source_variant_and_publishing_without_launching_them(self):
        from lib import cachyos_doctor as doctor

        config = self.config(*FLAGS, "--godot")
        versions = {"material-maker": "1.6-1", "butler": "15.31.0-1",
                    "steamcmd": "latest-7", "etcher-bin": "2.1.7-1"}
        def probe(argv, uid):
            if argv[:2] == ["/usr/bin/pacman", "-Q"]:
                package = argv[-1]
                return ("ok", f"{package} {versions[package]}\n") if package in versions else ("error", "")
            return "ok", "4.7.2\n"
        with ExitStack() as stack:
            stack.enter_context(patch.object(doctor, "is_cachyos", return_value=True))
            stack.enter_context(patch.object(doctor.platform, "machine", return_value="x86_64"))
            stack.enter_context(patch.object(doctor.os, "getuid", return_value=1000))
            stack.enter_context(patch.object(doctor.pwd, "getpwuid", return_value=SimpleNamespace(pw_name="human", pw_dir=str(self.home))))
            stack.enter_context(patch.object(doctor, "_owned_socket", return_value=False))
            stack.enter_context(patch.object(doctor.shutil, "which", side_effect=lambda name, **kw: "/usr/bin/" + name))
            probes = stack.enter_context(patch.object(doctor, "_probe", side_effect=probe))
            stack.enter_context(patch("lib.cachyos_health.collect_host_health", return_value=[]))
            stack.enter_context(patch("lib.cachyos_health.collect_network_health", return_value=[]))
            records = {item["name"]: item for item in doctor.collect_cachyos_doctor(config=config)["capabilities"]}
        for name in ("software.material-maker", "software.butler", "software.steamcmd", "package.etcher-bin", "tool.godot"):
            self.assertEqual(records[name]["state"], "available")
            self.assertTrue(records[name]["selected"])
        self.assertEqual(records["software.material-maker"]["version"], "1.6-1")
        self.assertFalse(any(Path(call.args[0][0]).name in {"butler", "steamcmd", "material-maker", "etcher"}
                             for call in probes.call_args_list))


if __name__ == "__main__":
    unittest.main()
