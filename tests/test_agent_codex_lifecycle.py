"""Codex workstation lifecycle regressions; commands and downloads are mocked."""

from __future__ import annotations

from contextlib import ExitStack
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from common import cachyos_steps
from lib import agent_cli
from lib.agent_storage import codex_package_for_executable, codex_update_supported, prune_codex_update_backups
from lib.config import SetupConfig


class CodexLifecycleTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.home = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.launcher = self.home / ".local/bin/codex"
        self.launcher.parent.mkdir(parents=True)
        self.root = self.home / ".codex/packages/standalone"
        self.package = self.release("1.0.0")
        (self.root / "current").symlink_to(self.package)
        self.launcher.symlink_to(self.root / "current/bin/codex")
        self.config = self.home / ".codex/config.toml"
        self.auth = self.home / ".codex/auth.json"
        self.config.write_text('model = "personal-choice"\n')
        self.auth.write_text('{"private": "fixture"}\n')
        stack.enter_context(patch.object(agent_cli, "_tool_version", side_effect=self.version))
        stack.enter_context(patch.object(agent_cli, "_tool_smoke_test", side_effect=lambda path: self.version("codex", path) is not None))
        stack.enter_context(patch.object(agent_cli, "_tool_path", side_effect=lambda tool, home: str(self.launcher)))
        stack.enter_context(patch("lib.agent_storage._active_codex_releases", return_value=set()))

    def release(self, version):
        package = self.root / "releases" / (version + "-x86_64-linux-musl")
        (package / "bin").mkdir(parents=True)
        (package / "bin/codex").write_text(version)
        (package / "bin/codex").chmod(0o755)
        (package / "codex-resources").mkdir()
        (package / "codex-resources/bwrap").write_text("sandbox fixture")
        (package / "codex-package.json").write_text(json.dumps({
            "layoutVersion": 1, "variant": "codex", "entrypoint": "bin/codex",
            "version": version, "target": "x86_64-linux-musl",
        }))
        return package

    @staticmethod
    def version(tool, path):
        try:
            value = Path(path).read_text()
            return None if value == "broken" else value
        except OSError:
            return None

    def update(self, *, broken=False, code=0):
        def invoke(tool, path, home):
            replacement = self.release("2.0.0")
            if broken:
                (replacement / "bin/codex").write_text("broken")
            (self.root / "current").unlink()
            (self.root / "current").symlink_to(replacement)
            self.launcher.unlink()
            self.launcher.symlink_to(self.root / "current/bin/codex")
            return {"returncode": code}

        with patch.object(agent_cli, "_invoke_agent_update", side_effect=invoke):
            return agent_cli.update_agent_tools(["codex"], home=str(self.home))[0]

    def test_failed_update_restores_whole_package_and_preserves_configuration(self):
        result = self.update(broken=True)
        self.assertTrue(result["rollback"])
        self.assertEqual(result["after_version"], "1.0.0")
        package = Path(codex_package_for_executable(str(self.home), str(self.launcher)))
        self.assertEqual((package / "codex-resources/bwrap").read_text(), "sandbox fixture")
        self.assertTrue(codex_update_supported(str(self.home), str(self.launcher)))
        self.assertEqual(self.config.read_text(), 'model = "personal-choice"\n')
        self.assertEqual(self.auth.read_text(), '{"private": "fixture"}\n')
        self.assertEqual(os.stat(result["backup_path"]).st_mode & 0o777, 0o755)

    def test_successful_update_retains_one_full_snapshot(self):
        first = agent_cli._backup_executable("codex", str(self.launcher), str(self.home))
        result = self.update()
        self.assertEqual(result["status"], "updated")
        self.assertTrue(Path(result["backup_path"]).is_file())
        self.assertFalse(Path(first).exists())
        self.assertEqual(self.launcher.resolve(), self.root / "releases/2.0.0-x86_64-linux-musl/bin/codex")

    def test_backup_cleanup_retains_active_and_selected_snapshots(self):
        old = agent_cli._backup_executable("codex", str(self.launcher), str(self.home))
        current = agent_cli._backup_executable("codex", str(self.launcher), str(self.home))
        self.launcher.unlink()
        self.launcher.symlink_to(old)
        prune_codex_update_backups(str(self.home), current)
        self.assertTrue(Path(old).exists())
        self.launcher.unlink()
        self.launcher.symlink_to(self.package / "bin/codex")
        with patch("lib.agent_storage._active_codex_releases", return_value={self.package.name}):
            prune_codex_update_backups(str(self.home), current)
        self.assertTrue(Path(old).exists())
        prune_codex_update_backups(str(self.home), current)
        self.assertFalse(Path(old).exists())

    def test_home_local_npm_link_is_not_adopted_by_standalone_updater(self):
        npm = self.home / ".npm-global/lib/node_modules/@openai/codex/bin/codex.js"
        npm.parent.mkdir(parents=True)
        npm.write_text("npm fixture")
        self.launcher.unlink()
        self.launcher.symlink_to(npm)
        with patch.object(agent_cli, "_invoke_agent_update") as invoke:
            result = agent_cli.update_agent_tools(["codex"], home=str(self.home))
        self.assertEqual(result[0]["failure"], "not_user_managed")
        invoke.assert_not_called()
        config = SetupConfig(host="localhost", username="human", system_type="agent_cachyos", agent_tools=["codex"])
        with patch.object(cachyos_steps, "_home", return_value=self.home), \
                patch.object(cachyos_steps.shutil, "which", return_value=str(self.launcher)), \
                patch.object(agent_cli, "update_agent_tools") as update:
            cachyos_steps.install_cachyos_agents(config)
        update.assert_not_called()
        self.assertEqual(self.launcher.resolve(), npm)

    def test_failed_version_command_is_not_a_version(self):
        with patch.object(agent_cli.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "private error")):
            self.assertIsNone(_real_tool_version("codex", str(self.launcher)))

    def test_successful_update_after_rollback_remains_manageable(self):
        failed = self.update(broken=True)
        self.assertTrue(failed["rollback"])

        def invoke(tool, path, home):
            replacement = self.release("3.0.0")
            self.launcher.unlink()
            self.launcher.symlink_to(replacement / "bin/codex")
            return {"returncode": 0}

        with patch.object(agent_cli, "_invoke_agent_update", side_effect=invoke):
            result = agent_cli.update_agent_tools(["codex"], home=str(self.home))[0]
        self.assertEqual(result["status"], "updated")
        self.assertEqual(result["after_version"], "3.0.0")
        self.assertTrue(codex_update_supported(str(self.home), str(self.launcher)))
        self.assertFalse(Path(failed["backup_path"]).exists())
        self.assertEqual(Path(result["backup_path"]).read_text(), "1.0.0")

    def test_custom_codex_home_fails_readonly_preflight(self):
        import basaltwater
        from lib.cachyos import cachyos_config_from_args, preflight_cachyos
        parser, _, _ = basaltwater.create_basaltwater_parser()
        config = cachyos_config_from_args(parser.parse_args(["setup", "agent_cachyos", "localhost", "human"]))
        owner = type("Owner", (), {"pw_dir": str(self.home), "pw_uid": 1000})()
        with patch("lib.cachyos.is_cachyos", return_value=True), \
                patch("lib.cachyos.platform.machine", return_value="x86_64"), \
                patch("lib.cachyos.pwd.getpwnam", return_value=owner), \
                patch("lib.cachyos.os.geteuid", return_value=1000), \
                patch("lib.cachyos.os.path.expanduser", return_value=str(self.home)), \
                patch("lib.cachyos.shutil.which", return_value="/usr/bin/tool"), \
                patch("lib.machine_state.detect_machine_type", return_value="hardware"), \
                patch.dict(os.environ, {"SSH_TTY": "", "SSH_CONNECTION": "", "CODEX_HOME": str(self.home / "personal-codex")}), \
                self.assertRaisesRegex(ValueError, "custom CODEX_HOME"):
            preflight_cachyos(config)
        self.assertFalse((self.home / "personal-codex").exists())

    def test_concurrent_update_cannot_replace_another_runs_snapshot(self):
        with agent_cli._agent_update_lock(str(self.home)), \
                patch.object(agent_cli, "_invoke_agent_update") as invoke, \
                self.assertRaisesRegex(RuntimeError, "Another agent update"):
            agent_cli.update_agent_tools(["codex"], home=str(self.home))
        invoke.assert_not_called()
        self.assertEqual(self.launcher.resolve(), self.package / "bin/codex")

    def test_updater_environment_removes_vendor_redirects_and_pins(self):
        owner = type("Owner", (), {"pw_name": "human"})()
        with patch.dict(os.environ, {
            "CODEX_INSTALL_DIR": "/elsewhere", "CODEX_INSTALL_DAEMON_ONLY": "1",
            "CODEX_INSTALL_DEFER_SELECTION": "1", "CODEX_RELEASE": "old",
            "CODEX_UPDATE_FROM_RELEASE": "old", "CODEX_HOME": "/another-home",
        }):
            environment = agent_cli._agent_update_environment(str(self.home), owner)
        self.assertEqual(environment["CODEX_HOME"], str(self.home / ".codex"))
        self.assertFalse(any(key.startswith(("CODEX_INSTALL", "CODEX_UPDATE")) for key in environment))
        self.assertNotIn("CODEX_RELEASE", environment)

    def test_readiness_checks_local_login_without_echoing_credentials(self):
        config = SetupConfig(host="localhost", username="human", system_type="agent_cachyos", agent_tools=["codex"])
        for code in (0, 1):
            def command(argv, home, **kwargs):
                return subprocess.CompletedProcess(argv, code if argv[-2:] == ["login", "status"] else 0,
                                                   "private fixture", "private fixture")
            with patch.object(cachyos_steps, "_home", return_value=self.home), \
                    patch.object(cachyos_steps.shutil, "which", return_value=str(self.launcher)), \
                    patch.object(cachyos_steps, "_user_run", side_effect=command) as run, \
                    patch("sys.stdout", new_callable=io.StringIO) as output:
                cachyos_steps.report_cachyos_readiness(config)
            self.assertTrue(any(call.args[0][-2:] == ["login", "status"] for call in run.call_args_list))
            self.assertNotIn("private fixture", output.getvalue())
            self.assertIn("local login available" if code == 0 else "login/configuration check failed", output.getvalue())


_real_tool_version = agent_cli._tool_version


if __name__ == "__main__":
    unittest.main()
