"""CachyOS agent boundaries and user-tool setup; all host commands are mocked."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import infra_tools
import remote_setup
from common import cachyos_steps as steps
from lib.cachyos import cachyos_config_from_args, preflight_cachyos
from lib.config import SetupConfig
from lib.system_types import get_steps_for_system_type


class CachyOSSetupTests(unittest.TestCase):
    def config(self, *options):
        parser, _, _ = infra_tools.create_infra_tools_parser()
        return cachyos_config_from_args(parser.parse_args(
            ["setup", "agent_cachyos", "localhost", "human", *options]))

    def test_default_plan_is_only_local_tooling(self):
        config = self.config()
        self.assertEqual(config.selected_agent_tools(), ["gh", "codex"])
        self.assertFalse(config.auto_restart)
        self.assertFalse(config.include_desktop)
        self.assertIsNone(config.browser_automation)
        self.assertIsNone(config.web_interfaces)
        functions = [function for _, function in get_steps_for_system_type(config)]
        self.assertTrue(all(function.__module__ == "common.cachyos_steps" for function in functions))
        self.assertNotIn(steps.install_cachyos_t3, functions)

    def test_rejects_unsupported_options_before_target_execution(self):
        cases = [
            ["--rdp"], ["--harden-agent"], ["--harden-user"], ["--nopasswd"],
            ["--browser-automation", "playwright"], ["--provision-on", "pve"],
            ["--machine", "vm"], ["--node", "--auto-restart"],
            ["--steps", "install_desktop"], ["--apt", "vim"],
            ["--agent-auth", "active"], ["--t3code-ready"],
            ["--web-interface", "t3code", "--web-interface-host", "0.0.0.0"],
        ]
        parser, _, _ = infra_tools.create_infra_tools_parser()
        for options in cases:
            with self.subTest(options=options), patch("remote_setup.run_cachyos_setup") as execute:
                args = parser.parse_args(["setup", "agent_cachyos", "localhost", "human", *options])
                self.assertEqual(infra_tools.run_setup_command(args), 1)
                execute.assert_not_called()

    def test_rejects_remote_target(self):
        parser, _, _ = infra_tools.create_infra_tools_parser()
        args = parser.parse_args(["setup", "agent_cachyos", "example.com", "human"])
        with self.assertRaisesRegex(ValueError, "local setup only"):
            cachyos_config_from_args(args)

    def test_remote_parser_has_same_defaults(self):
        from lib.arg_parser import create_setup_argument_parser
        parser = create_setup_argument_parser("test", for_remote=True, allow_steps=True)
        config = cachyos_config_from_args(parser.parse_args(
            ["--system-type", "agent_cachyos", "--username", "human"]), for_remote=True)
        self.assertEqual(config, self.config())

    def test_dry_run_never_invokes_steps_or_system_probes(self):
        from lib.remote_utils import is_dry_run, set_dry_run
        set_dry_run(False)
        with patch("lib.cachyos.is_cachyos") as detect, patch.object(steps, "run") as run:
            self.assertEqual(remote_setup.run_cachyos_setup(self.config("--dry-run")), 0)
        self.assertFalse(is_dry_run())
        detect.assert_not_called()
        run.assert_not_called()

    def test_apply_rejects_non_cachyos_before_steps(self):
        with patch("lib.cachyos.is_cachyos", return_value=False), patch.object(steps, "run") as run:
            with self.assertRaisesRegex(ValueError, "requires CachyOS"):
                remote_setup.run_cachyos_setup(self.config())
        run.assert_not_called()

    def test_preflight_rejects_different_user_and_virtual_machine(self):
        account = SimpleNamespace(pw_uid=1000, pw_dir="/home/human")
        with patch("lib.cachyos.is_cachyos", return_value=True), \
             patch("lib.cachyos.platform.machine", return_value="x86_64"), \
             patch("lib.cachyos.pwd.getpwnam", return_value=account), \
             patch("lib.cachyos.os.geteuid", return_value=0):
            with self.assertRaisesRegex(ValueError, "without sudo"):
                preflight_cachyos(self.config())
        with patch("lib.cachyos.is_cachyos", return_value=True), \
             patch("lib.cachyos.platform.machine", return_value="x86_64"), \
             patch("lib.cachyos.pwd.getpwnam", return_value=account), \
             patch("lib.cachyos.os.geteuid", return_value=1000), \
             patch.dict(os.environ, {"SSH_CONNECTION": "", "SSH_TTY": ""}), \
             patch("lib.machine_state.detect_machine_type", return_value="vm"):
            with self.assertRaisesRegex(ValueError, "bare-metal"):
                preflight_cachyos(self.config())

    def test_package_install_retains_installed_packages_and_never_syncs(self):
        def command(argv, **kwargs):
            missing = argv[:2] == ["pacman", "-Q"] and argv[-1] == "github-cli"
            return subprocess.CompletedProcess(argv, int(missing), "", "")
        with patch.object(steps, "run", side_effect=command) as run, \
             patch.object(steps.os, "geteuid", return_value=1000):
            steps.install_missing_packages(["git", "github-cli", "git"])
        self.assertEqual(run.call_args_list[-1].args[0],
                         ["sudo", "pacman", "-S", "--needed", "--noconfirm", "--", "github-cli"])
        self.assertTrue(run.call_args_list[-1].kwargs["interactive"])
        self.assertEqual(run.call_count, 3)

    def test_package_failure_stops_with_recovery_instructions(self):
        with patch.object(steps, "run", return_value=subprocess.CompletedProcess([], 1)), \
             self.assertRaisesRegex(RuntimeError, "Could not resolve host.*update CachyOS"):
            steps.install_missing_packages(["git"])

    def test_package_input_validated_before_commands(self):
        with patch.object(steps, "run") as run, self.assertRaises(ValueError):
            steps.install_missing_packages(["git;touch /tmp/unwanted"])
        run.assert_not_called()

    def test_native_graphics_flags_select_cachyos_packages(self):
        config = self.config(
            "--gaming", "--sunshine", "--moonlight", "--obs", "--blender",
            "--kdenlive", "--krita", "--inkscape", "--scribus", "--audacity",
            "--ardour", "--lmms", "--freecad", "--kicad", "--shotcut", "--gimp",
            "--remmina", "--sysadmin-tools",
        )
        with patch.object(steps, "_home", return_value=Path("/home/human")), \
             patch.object(steps.shutil, "which", return_value=None):
            packages = steps.cachyos_packages(config)
        self.assertIn("cachyos-gaming-meta", packages)
        self.assertIn("cachyos-gaming-applications", packages)
        self.assertIn("sunshine", packages)
        self.assertIn("moonlight-qt", packages)
        self.assertIn("obs-studio", packages)
        self.assertIn("blender", packages)
        self.assertIn("kdenlive", packages)
        self.assertIn("krita", packages)
        for package in (
            "inkscape", "scribus", "audacity", "ardour", "lmms", "freecad",
            "kicad", "shotcut", "gimp", "remmina", "freerdp", "libvncserver",
            "spice-gtk", "gtk-vnc", "libsecret", "nmap", "tcpdump", "bind",
            "virt-manager", "wireshark-qt",
        ):
            self.assertIn(package, packages)

    def test_native_graphics_flags_round_trip_and_reject_other_profiles(self):
        config = self.config(
            "--gaming", "--sunshine", "--moonlight", "--obs", "--blender",
            "--kdenlive", "--krita", "--inkscape", "--scribus", "--audacity",
            "--ardour", "--lmms", "--freecad", "--kicad", "--shotcut", "--gimp",
            "--remmina", "--sysadmin-tools",
        )
        self.assertIn("--gaming", config.to_remote_args())
        self.assertIn("--sunshine", config.to_setup_command())
        self.assertTrue(config.to_dict()["install_moonlight"])
        self.assertTrue(config.to_dict()["install_obs"])
        self.assertIn("--krita", config.to_remote_args())
        self.assertIn("--lmms", config.to_setup_command())
        self.assertTrue(config.to_dict()["install_sysadmin_tools"])
        with self.assertRaisesRegex(ValueError, "require the agent_cachyos profile"):
            SetupConfig(
                host="example.com",
                username="human",
                system_type="server_lite",
                install_sunshine=True,
            )
        with self.assertRaisesRegex(ValueError, "require the agent_cachyos profile"):
            SetupConfig(
                host="example.com",
                username="human",
                system_type="server_lite",
                install_lmms=True,
            )

    def test_native_streaming_readiness_checks_commands(self):
        config = self.config(
            "--sunshine", "--moonlight", "--obs", "--blender", "--kdenlive", "--krita",
            "--inkscape", "--scribus", "--audacity", "--ardour", "--lmms", "--freecad",
            "--kicad", "--shotcut", "--gimp", "--remmina", "--sysadmin-tools",
        )
        with patch.object(steps, "_home", return_value=Path("/home/human")), \
             patch.object(
                 steps.shutil,
                 "which",
                 side_effect=lambda name, **kwargs: "/usr/bin/" + name,
             ), \
             patch.object(
                 steps,
                 "run",
                 return_value=subprocess.CompletedProcess([], 0, "version\n"),
             ):
            steps.report_cachyos_readiness(config)

    def test_existing_agents_are_not_updated(self):
        with patch.object(steps, "_home", return_value=Path("/home/human")), \
             patch.object(steps.shutil, "which", return_value="/usr/bin/codex"), \
             patch.object(steps, "run") as run:
            steps.install_cachyos_agents(self.config())
        run.assert_not_called()

    def test_user_managed_agents_are_updated(self):
        with patch.object(steps, "_home", return_value=Path("/home/human")), \
             patch.object(
                 steps.shutil,
                 "which",
                 return_value="/home/human/.local/bin/codex",
             ), \
             patch(
                 "lib.agent_cli.update_agent_tools",
                 return_value=[{"status": "updated"}],
             ) as update:
            steps.install_cachyos_agents(self.config())
        update.assert_called_once_with(["codex"], home="/home/human")

    def test_new_agent_installers_are_non_interactive(self):
        with patch.object(steps, "_home", return_value=Path("/home/human")), \
             patch.object(steps.shutil, "which", side_effect=[
                 None, "/home/human/.local/bin/codex",
             ]), \
             patch.object(steps, "install_vendor_tool", return_value=0) as install:
            steps.install_cachyos_agents(self.config())
        install.assert_called_once_with(
            "codex", accept_vendor_channel=True, non_interactive=True,
        )

    def test_setup_reconciles_user_cache(self):
        config = self.config()
        with patch(
            "common.setup_maintenance.run_user_cache_maintenance"
        ) as reconcile:
            steps.reconcile_cachyos_user_cache(config)
        reconcile.assert_called_once_with(config)

    def test_go_readiness_uses_the_go_version_subcommand(self):
        with patch.object(steps, "_home", return_value=Path("/home/human")), \
             patch.object(steps.shutil, "which", side_effect=lambda name, **kwargs: "/usr/bin/" + name), \
             patch.object(steps, "run", return_value=subprocess.CompletedProcess([], 0, "version\n")) as run:
            steps.report_cachyos_readiness(self.config("--go"))
        self.assertIn(["/usr/bin/go", "version"], [call.args[0][-2:] for call in run.call_args_list])

    def test_shell_setup_is_additive_and_idempotent(self):
        for shell in ("bash", "zsh", "fish"):
            with self.subTest(shell=shell), tempfile.TemporaryDirectory() as home:
                rc = Path(home) / (".bashrc" if shell == "bash" else ".zshrc")
                rc.write_text("# personal configuration\n")
                steps.configure_cachyos_shell(home, shell)
                contents = rc.read_text()
                steps.configure_cachyos_shell(home, shell)
                self.assertEqual(rc.read_text(), contents)
                self.assertTrue(contents.startswith("# personal configuration\n"))

    def test_skill_selection_removes_managed_vm_skills_preserves_personal(self):
        with tempfile.TemporaryDirectory() as home:
            account = SimpleNamespace(pw_dir=home, pw_uid=os.getuid(), pw_gid=os.getgid())
            skill_root = Path(home) / ".agents/skills"
            for name, content in (("infra-tools-desktop", "managed-by: infra_tools\n"),
                                  ("infra-tools-vm-triage", "personal content\n")):
                (skill_root / name).mkdir(parents=True)
                (skill_root / name / "SKILL.md").write_text(content)
            with patch("common.agent_steps.pwd.getpwnam", return_value=account), \
                 patch("common.agent_steps.os.chown"):
                steps.install_cachyos_skills(self.config())
            self.assertFalse((skill_root / "infra-tools-desktop/SKILL.md").exists())
            self.assertEqual((skill_root / "infra-tools-vm-triage/SKILL.md").read_text(), "personal content\n")
            for name in steps.CACHYOS_SKILLS:
                self.assertTrue((skill_root / name / "SKILL.md").is_file())
            self.assertFalse((skill_root / steps.CACHYOS_T3_SKILL).exists())

    def test_existing_repository_is_checked_but_never_pulled(self):
        with tempfile.TemporaryDirectory() as home:
            (Path(home) / "repos/project").mkdir(parents=True)
            url = "https://github.com/example/project.git"
            results = [subprocess.CompletedProcess([], 0, str(Path(home) / "repos/project") + "\n"),
                       subprocess.CompletedProcess([], 0, url + "\n")]
            with patch.object(steps, "_home", return_value=Path(home)), \
                 patch.object(steps, "run", side_effect=results) as run:
                steps.prepare_cachyos_workspace(self.config("--repo", url))
                self.assertEqual(run.call_count, 2)
                self.assertEqual(run.call_args.args[0][-3:], ["remote", "get-url", "origin"])

    def test_existing_destination_cannot_inherit_parent_repository(self):
        with tempfile.TemporaryDirectory() as home:
            destination = Path(home) / "repos/project"
            destination.mkdir(parents=True)
            personal = destination / "personal.txt"
            personal.write_text("keep me\n")
            with patch.object(steps, "_home", return_value=Path(home)), \
                 patch.object(steps, "run", return_value=subprocess.CompletedProcess(
                     [], 0, str(destination.parent) + "\n")) as run:
                with self.assertRaisesRegex(ValueError, "not a repository root"):
                    steps.prepare_cachyos_workspace(self.config(
                        "--repo", "https://github.com/example/project.git"))
            self.assertEqual(run.call_count, 1)
            self.assertEqual(personal.read_text(), "keep me\n")

    def test_t3_refreshes_local_runtime_on_rerun(self):
        with tempfile.TemporaryDirectory() as home:
            root = Path(home)
            binary = root / ".local/share/infra-tools/cachyos-t3/bin/t3"
            def command(argv, **kwargs):
                if "npm" in argv and "install" in argv:
                    binary.parent.mkdir(parents=True, exist_ok=True)
                    binary.write_text("#!/bin/sh\n")
                version = "v24.10.0\n" if "node" in argv else "0.0.40\n"
                return subprocess.CompletedProcess(argv, 0, version, "")
            response = unittest.mock.MagicMock()
            response.__enter__.return_value.status = 200
            response.__enter__.return_value.geturl.return_value = "http://127.0.0.1:3773/"
            with patch.object(steps, "_home", return_value=root), \
                 patch.object(steps, "run", side_effect=command) as run, \
                 patch.object(steps.urllib.request, "build_opener") as opener, \
                 patch.object(steps.time, "sleep"):
                opener.return_value.open.return_value = response
                config = self.config("--web-interface", "t3code")
                steps.install_cachyos_t3(config)
                unit = root / ".config/systemd/user" / steps.T3_SERVICE
                content = unit.read_text()
                self.assertIn("serve --host 127.0.0.1 --port 3773", content)
                self.assertIn(str(binary), content)
                self.assertIn("WorkingDirectory=" + str(root / "repos"), content)
                self.assertNotIn('WorkingDirectory="', content)
                self.assertNotIn("User=", content)
                self.assertTrue(any("npm" in call.args[0] for call in run.call_args_list))
                run.reset_mock()
                steps.install_cachyos_t3(config)
                self.assertEqual(unit.read_text(), content)
                self.assertTrue(any("npm" in call.args[0] for call in run.call_args_list))
                self.assertTrue(any("start" in call.args[0] for call in run.call_args_list))
                self.assertFalse(any("restart" in call.args[0] for call in run.call_args_list))
                self.assertFalse(any("sudo" in call.args[0] for call in run.call_args_list))

    def test_t3_readiness_requires_consecutive_direct_local_successes(self):
        with tempfile.TemporaryDirectory() as home:
            # Neither a non-200 response nor a redirect counts toward readiness.
            responses = []
            for status, url in [(200, "http://127.0.0.1:3773/"),
                                (202, "http://127.0.0.1:3773/"),
                                (200, "http://example.com/"),
                                *[(200, "http://127.0.0.1:3773/")] * 3]:
                item = unittest.mock.MagicMock()
                item.__enter__.return_value.status = status
                item.__enter__.return_value.geturl.return_value = url
                responses.append(item)
            with patch.object(steps, "_home", return_value=Path(home)), \
                 patch.object(steps, "run", return_value=subprocess.CompletedProcess([], 0, "v24.10.0\n")), \
                 patch.object(steps.urllib.request, "build_opener") as opener, \
                 patch.object(steps.time, "sleep"):
                opener.return_value.open.side_effect = responses
                steps.install_cachyos_t3(self.config("--web-interface", "t3code"))
            self.assertEqual(opener.call_args.args[0].proxies, {})
            self.assertEqual(opener.return_value.open.call_count, 6)

    def test_t3_preserves_an_existing_unmanaged_unit(self):
        with tempfile.TemporaryDirectory() as home:
            unit = Path(home) / ".config/systemd/user" / steps.T3_SERVICE
            unit.parent.mkdir(parents=True)
            unit.write_text("# personal service\n")
            with patch.object(steps, "_home", return_value=Path(home)), \
                 patch.object(steps, "run", return_value=subprocess.CompletedProcess([], 0, "v24.10.0\n")) as run:
                with self.assertRaisesRegex(ValueError, "unmanaged"):
                    steps.install_cachyos_t3(self.config("--web-interface", "t3code"))
            self.assertEqual(unit.read_text(), "# personal service\n")
            self.assertEqual(run.call_count, 1)

    def test_t3_allows_npm_managed_binary_link_inside_prefix(self):
        with tempfile.TemporaryDirectory() as home:
            root = Path(home)
            prefix = root / ".local/share/infra-tools/cachyos-t3"
            binary = prefix / "bin/t3"
            target = prefix / "lib/node_modules/t3/dist/bin.mjs"
            target.parent.mkdir(parents=True)
            target.write_text("#!/bin/sh\n")
            binary.parent.mkdir(parents=True)
            binary.symlink_to("../lib/node_modules/t3/dist/bin.mjs")
            response = unittest.mock.MagicMock()
            response.__enter__.return_value.status = 200
            response.__enter__.return_value.geturl.return_value = "http://127.0.0.1:3773/"
            with patch.object(steps, "_home", return_value=root), \
                 patch.object(steps, "run", return_value=subprocess.CompletedProcess([], 0, "v24.10.0\n")), \
                 patch.object(steps.urllib.request, "build_opener") as opener, \
                 patch.object(steps.time, "sleep"):
                opener.return_value.open.return_value = response
                steps.install_cachyos_t3(self.config("--web-interface", "t3code"))

    def test_t3_rejects_binary_link_outside_prefix(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as outside:
            root = Path(home)
            prefix = root / ".local/share/infra-tools/cachyos-t3"
            binary = prefix / "bin/t3"
            binary.parent.mkdir(parents=True)
            binary.symlink_to(Path(outside) / "t3")
            with patch.object(steps, "_home", return_value=root), \
                 patch.object(steps, "run", return_value=subprocess.CompletedProcess([], 0, "v24.10.0\n")):
                with self.assertRaisesRegex(ValueError, "unsafe T3 runtime"):
                    steps.install_cachyos_t3(self.config("--web-interface", "t3code"))


if __name__ == "__main__":
    unittest.main()
