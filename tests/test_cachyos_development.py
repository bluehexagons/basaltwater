"""Native development selection and NVM preparation without host mutations."""

from __future__ import annotations

from contextlib import ExitStack
import io
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import basaltwater
import remote_setup
from common import cachyos_development as development, cachyos_steps as steps
from lib.arg_parser import create_setup_argument_parser
from lib.cachyos import cachyos_config_from_args
from lib.config import SetupConfig
from lib.system_types import get_steps_for_system_type


class CachyOSDevelopmentTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.home = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        stack.enter_context(patch.dict(os.environ, {"NVM_DIR": ""}))
        stack.enter_context(patch.object(steps, "_home", return_value=self.home))
        stack.enter_context(patch("lib.remote_utils.is_dry_run", return_value=False))
        stack.enter_context(patch("sys.stdout", new_callable=io.StringIO))
        self.parser, _, _ = basaltwater.create_basaltwater_parser()

    def config(self, *options):
        return cachyos_config_from_args(self.parser.parse_args([
            "setup", "agent_cachyos", "localhost", "human", *options,
        ]))

    def make_nvm(self):
        script = self.home / ".nvm/nvm.sh"
        script.parent.mkdir()
        script.write_text("# fixture\n")
        return script

    def test_options_roundtrip_without_selecting_a_runtime_or_release_tools(self):
        original = self.config("--game-dev", "--node-versions")
        self.assertFalse(original.install_node)
        self.assertIsNone(original.apt_packages)
        restored = SetupConfig.from_dict("localhost", "agent_cachyos", original.to_dict())
        self.assertTrue(restored.install_game_dev)
        self.assertTrue(restored.install_node_versions)
        replay = self.parser.parse_args(shlex.split(" ".join(original.to_setup_command()))[1:])
        self.assertEqual(cachyos_config_from_args(replay), original)
        parser = create_setup_argument_parser("remote", for_remote=True)
        remote = cachyos_config_from_args(parser.parse_args(shlex.split(
            " ".join(original.to_remote_args()))), for_remote=True)
        self.assertEqual(remote, original)
        for flag in ("--no-game-dev", "--no-node-versions"):
            self.assertFalse(getattr(self.config(flag), "install_" + flag[5:].replace("-", "_")))
        with self.assertRaisesRegex(ValueError, "requires the agent_cachyos"):
            SetupConfig(host="example.com", username="human", system_type="agent_code_vm", install_node_versions=True)
        with self.assertRaisesRegex(ValueError, "Debian workstation/server"):
            SetupConfig(host="example.com", username="human", system_type="server_wsl", install_game_dev=True)

    def test_game_bundle_supplies_headers_debugging_capture_and_animator_libraries(self):
        with patch.object(steps.shutil, "which", return_value="/usr/bin/available"):
            packages = steps.cachyos_packages(self.config("--game-dev"))
        for package in ("cmake", "python", "pkgconf", "sdl3", "sdl3_image", "freetype2",
                        "libvorbis", "libusb", "glew", "openal", "gdb", "ccache",
                        "xorg-server-xvfb", "xorg-xauth", "gtk3", "nss"):
            self.assertIn(package, packages)
        self.assertTrue(set(steps.CACHYOS_AUTOMATION_PACKAGES) <= set(packages))
        for package in ("electron", "podman", "steamcmd", "cachyos-gaming-meta", "clang"):
            self.assertNotIn(package, packages)
        with patch.object(steps.shutil, "which", return_value="/usr/bin/available"):
            default = steps.cachyos_packages(self.config())
        self.assertNotIn("sdl3", default)
        self.assertFalse(steps.desktop_automation_requested(self.config("--node-versions")))

    def test_node_preparation_follows_packages_and_dry_run_never_executes(self):
        functions = [function for _, function in get_steps_for_system_type(self.config("--node-versions"))]
        self.assertLess(functions.index(steps.install_cachyos_packages),
                        functions.index(development.install_cachyos_node_versions))
        with patch.object(steps, "run") as run, patch.object(steps, "install_vendor_tool") as install:
            remote_setup.run_cachyos_setup(self.config("--game-dev", "--node-versions", "--dry-run"))
            development.install_cachyos_node_versions(self.config("--node-versions", "--dry-run"))
        run.assert_not_called()
        install.assert_not_called()
        self.assertFalse((self.home / ".nvm").exists())

    def test_first_nvm_install_never_changes_profiles_or_installs_an_implicit_node(self):
        def install(tool, **kwargs):
            self.assertEqual(tool, "nvm")
            self.assertTrue(kwargs["accept_vendor_channel"])
            self.assertTrue(kwargs["non_interactive"])
            environment = kwargs["environment"]
            self.assertEqual(environment["PROFILE"], "/dev/null")
            self.assertEqual(environment["NVM_DIR"], str(self.home / ".nvm"))
            self.assertEqual(environment["HOME"], str(self.home))
            for name in ("NODE_VERSION", "NVM_SOURCE", "NVM_INSTALL_GITHUB_REPO", "NVM_INSTALL_VERSION"):
                self.assertNotIn(name, environment)
            (self.home / ".nvm/nvm.sh").write_text("# fixture\n")
            return 0

        with patch.dict(os.environ, {"PROFILE": "personal-profile", "NODE_VERSION": "node",
                                    "NVM_SOURCE": "untrusted", "NVM_INSTALL_GITHUB_REPO": "other/repo"}), \
                patch.object(steps, "install_vendor_tool", side_effect=install), \
                patch.object(steps, "_user_run", return_value=subprocess.CompletedProcess([], 0, "0.40.6\n")) as run:
            development.install_cachyos_node_versions(self.config("--node-versions"))
        self.assertEqual(run.call_count, 1)
        command = run.call_args.args[0]
        self.assertIn('. "$1" --no-use && nvm --version', command)
        self.assertEqual(run.call_args.kwargs["cwd"], "/")

    def test_existing_nvm_is_verified_without_update_or_default_changes(self):
        script = self.make_nvm()
        with patch.object(steps, "install_vendor_tool") as install, \
                patch.object(steps, "_user_run", return_value=subprocess.CompletedProcess([], 0)):
            development.install_cachyos_node_versions(self.config("--node-versions"))
        install.assert_not_called()
        self.assertEqual(script.read_text(), "# fixture\n")

    def test_project_preparation_uses_the_existing_installer_without_replaying_setup(self):
        account = SimpleNamespace(pw_name='human', pw_dir=str(self.home))

        def install(tool, **kwargs):
            (self.home / '.nvm/nvm.sh').write_text('# fixture\n')
            self.assertEqual(tool, 'nvm')
            self.assertEqual(kwargs['environment']['PROFILE'], '/dev/null')
            return 0

        with patch.object(development.pwd, 'getpwuid', return_value=account), \
                patch('lib.cachyos.preflight_cachyos') as preflight, \
                patch.object(steps, 'install_vendor_tool', side_effect=install), \
                patch.object(steps, '_user_run', return_value=subprocess.CompletedProcess([], 0)), \
                patch('remote_setup.run_cachyos_setup') as full_setup, \
                patch('lib.cachyos_refresh.save_successful_setup') as save:
            self.assertEqual(development.prepare_project_node_versions(), self.home / '.nvm/nvm.sh')
        config = preflight.call_args.args[0]
        self.assertEqual(config.username, 'human')
        self.assertTrue(config.install_node_versions)
        self.assertFalse(config.selected_agent_tools())
        full_setup.assert_not_called()
        save.assert_not_called()

    def test_project_preparation_enforces_preflight_and_custom_nvm_boundaries(self):
        account = SimpleNamespace(pw_name='human', pw_dir=str(self.home))
        with patch.object(development.pwd, 'getpwuid', return_value=account), \
                patch('lib.cachyos.preflight_cachyos', side_effect=ValueError('Run locally')) as preflight, \
                patch.object(steps, 'install_vendor_tool') as install:
            with self.assertRaisesRegex(ValueError, 'Run locally'):
                development.prepare_project_node_versions()
            install.assert_not_called()
            preflight.side_effect = None
            with patch.dict(os.environ, {'NVM_DIR': str(self.home / 'custom')}), \
                    self.assertRaisesRegex(ValueError, 'custom NVM_DIR'):
                development.prepare_project_node_versions()
            install.assert_not_called()

    def test_failed_install_and_unloadable_nvm_stop_setup(self):
        with patch.object(steps, "install_vendor_tool", return_value=1), \
                self.assertRaisesRegex(RuntimeError, "installation failed"):
            development.install_cachyos_node_versions(self.config("--node-versions"))
        script = self.home / ".nvm/nvm.sh"
        script.write_text("# fixture\n")
        with patch.object(steps, "_user_run", return_value=subprocess.CompletedProcess([], 1)), \
                self.assertRaisesRegex(RuntimeError, "could not be loaded"):
            development.verify_node_versions(self.home)

    def test_custom_symlinked_or_writable_nvm_is_rejected_without_installer(self):
        script = self.make_nvm()
        with patch.dict(os.environ, {"NVM_DIR": str(self.home / "other")}), \
                self.assertRaisesRegex(ValueError, "custom NVM_DIR"):
            development.nvm_script(self.home)
        script.chmod(0o666)
        with self.assertRaisesRegex(ValueError, "regular nvm.sh"):
            development.nvm_script(self.home)
        script.unlink()
        personal = self.home / "personal.sh"
        personal.write_text("keep\n")
        script.symlink_to(personal)
        with patch.object(steps, "install_vendor_tool") as install, \
                self.assertRaisesRegex(ValueError, "regular nvm.sh"):
            development.install_cachyos_node_versions(self.config("--node-versions"))
        install.assert_not_called()
        self.assertEqual(personal.read_text(), "keep\n")

    def test_incomplete_nvm_is_not_overwritten(self):
        root = self.home / ".nvm"
        root.mkdir()
        (root / "personal").write_text("keep\n")
        with patch.object(steps, "install_vendor_tool") as install, \
                self.assertRaisesRegex(ValueError, "no nvm.sh"):
            development.install_cachyos_node_versions(self.config("--node-versions"))
        install.assert_not_called()
        self.assertEqual((root / "personal").read_text(), "keep\n")

    def test_game_readiness_checks_modules_without_launching_gui_or_repository_code(self):
        with patch.object(development.shutil, "which", return_value="/usr/bin/available"), \
                patch.object(steps, "_user_run", return_value=subprocess.CompletedProcess([], 0)) as run:
            development.report_game_dev_readiness(self.config("--game-dev"))
        self.assertEqual([call.args[0] for call in run.call_args_list], [
            ["pkg-config", "--modversion", module] for module in development.GAME_DEV_MODULES
        ])
        self.assertTrue(all(call.kwargs["cwd"] == "/" for call in run.call_args_list))
        with patch.object(development.shutil, "which", return_value=None), \
                self.assertRaisesRegex(RuntimeError, "command missing"):
            development.report_game_dev_readiness(self.config("--game-dev"))
        with patch.object(development.shutil, "which", return_value="/usr/bin/available"), \
                patch.object(steps, "_user_run", return_value=subprocess.CompletedProcess([], 1)), \
                self.assertRaisesRegex(RuntimeError, "module missing: sdl3"):
            development.report_game_dev_readiness(self.config("--game-dev"))

    def test_host_version_checks_disable_corepack_downloads_and_project_policy(self):
        with patch.object(steps, "run") as run:
            steps._user_run(["pnpm", "--version"], self.home, cwd="/")
        command = run.call_args.args[0]
        self.assertIn("COREPACK_ENABLE_NETWORK=0", command)
        self.assertIn("COREPACK_ENABLE_PROJECT_SPEC=0", command)
        self.assertEqual(run.call_args.kwargs["cwd"], "/")


if __name__ == "__main__":
    unittest.main()
