"""Tests for managed development-toolchain readiness."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib import agent_cli


class AgentDevelopmentReadinessTests(unittest.TestCase):
    def test_node_readiness_reports_missing_pnpm(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            home = Path(temporary_directory)
            nvm_dir = home / ".nvm"
            node_bin = nvm_dir / "versions" / "node" / "v24.22.1" / "bin"
            node_bin.mkdir(parents=True)
            (nvm_dir / "nvm.sh").write_text("# nvm\n", encoding="utf-8")
            for name in ("node", "npm", "corepack"):
                (node_bin / name).write_text("", encoding="utf-8")

            def version(path: str, *_arguments: str, **_options: object) -> str | None:
                return {
                    "node": "v24.22.1",
                    "npm": "11.19.0",
                    "corepack": "0.35.0",
                }.get(os.path.basename(path))

            with (
                patch.object(agent_cli, "_nvm_node_bin", return_value=str(node_bin)),
                patch.object(agent_cli, "_command_version", side_effect=version),
            ):
                result = agent_cli._inspect_node_development(str(home))

        self.assertFalse(result["healthy"])
        self.assertEqual(result["issues"], ["node_pnpm_missing"])
        self.assertEqual(result["version"], "v24.22.1")

    def test_node_probes_ignore_project_policy_and_use_matching_interpreter(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            home = Path(temporary_directory)
            nvm_dir = home / ".nvm"
            node_bin = nvm_dir / "versions" / "node" / "v24.22.1" / "bin"
            node_bin.mkdir(parents=True)
            (nvm_dir / "nvm.sh").write_text("# nvm\n", encoding="utf-8")
            (home / "package.json").write_text(
                '{"packageManager":"npm@11.19.0"}', encoding="utf-8",
            )
            versions = {
                "node": "v24.22.1", "npm": "11.19.0",
                "pnpm": "12.6.0", "corepack": "0.35.0",
            }
            for name in versions:
                (node_bin / name).write_text("", encoding="utf-8")
            probe_directories: set[str] = set()

            def run(command: list[str], **options: object) -> subprocess.CompletedProcess[str]:
                directory = Path(str(options.get("cwd") or home))
                environment = options.get("env")
                if any((parent / "package.json").exists() for parent in (directory, *directory.parents)):
                    return subprocess.CompletedProcess(command, 1, "", "Project requires npm")
                if (
                    not isinstance(environment, dict)
                    or environment["PATH"].split(os.pathsep)[0] != str(node_bin)
                ):
                    return subprocess.CompletedProcess(command, 1, "", "Wrong Node interpreter")
                self.assertTrue(directory.is_dir())
                self.assertEqual(environment["COREPACK_ENABLE_NETWORK"], "0")
                self.assertEqual(environment["COREPACK_ENABLE_PROJECT_SPEC"], "0")
                self.assertEqual(environment["COREPACK_ENABLE_AUTO_PIN"], "0")
                self.assertEqual(environment["COREPACK_DEFAULT_TO_LATEST"], "0")
                probe_directories.add(str(directory))
                return subprocess.CompletedProcess(command, 0, versions[Path(command[0]).name] + "\n", "")

            with (
                patch.object(agent_cli, "_nvm_node_bin", return_value=str(node_bin)),
                patch.dict(os.environ, {"PATH": "/other-node/bin:/usr/bin"}),
                patch.object(agent_cli.subprocess, "run", side_effect=run) as process,
                patch.object(agent_cli.tempfile, "TemporaryDirectory", side_effect=PermissionError("read-only filesystem")),
            ):
                result = agent_cli._inspect_node_development(str(home))

            self.assertTrue(result["healthy"])
            self.assertEqual(result["pnpm"], "12.6.0")
            self.assertEqual(process.call_count, 4)
            self.assertEqual(probe_directories, {"/"})

    def test_development_readiness_ignores_absent_optional_toolchains(self) -> None:
        healthy_node = {
            "installed": True,
            "healthy": True,
            "version": "v24.22.1",
            "issues": [],
        }
        absent = {"installed": False, "healthy": True, "issues": []}
        with (
            patch.object(
                agent_cli,
                "_inspect_godot_development",
                return_value=absent,
            ),
            patch.object(agent_cli, "_inspect_go_development", return_value=absent),
            patch.object(
                agent_cli,
                "_inspect_node_development",
                return_value=healthy_node,
            ),
            patch("lib.game_development.inspect_native_development", return_value=absent),
        ):
            result = agent_cli.inspect_development_readiness("/home/agent")

        self.assertTrue(result["installed"])
        self.assertTrue(result["healthy"])
        self.assertEqual(result["issues"], [])


    def test_observed_default_policy_is_not_hidden_by_supported_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            nvm = home / '.nvm'
            for version in ('22.23.2', '24.22.1', '26.12.1', '27.0.0'):
                binary = nvm / 'versions/node' / ('v' + version) / 'bin'
                binary.mkdir(parents=True)
                for name in ('node', 'npm', 'pnpm'):
                    (binary / name).write_text('fixture')
            (nvm / 'nvm.sh').write_text('# nvm')
            (nvm / 'alias/lts').mkdir(parents=True)
            (nvm / 'alias/default').write_text('lts/old')
            (nvm / 'alias/lts/old').write_text('22')

            def version(path, **_options):
                return 'v22.23.2' if Path(path).name == 'node' else '12.1.0'

            with patch.object(agent_cli.shutil, 'which', return_value=None), \
                    patch.object(agent_cli, '_command_version', side_effect=version):
                result = agent_cli._inspect_node_development(str(home))
            self.assertFalse(result['healthy'])
            self.assertEqual(result['version'], 'v22.23.2')
            self.assertEqual(result['issues'], ['node_default_unsupported'])
            self.assertIn('^24.22.1', result['supported_range'])
            self.assertTrue((nvm / 'versions/node/v22.23.2/bin/node').is_file())
            # A broken alias is unhealthy too; inventory must not mask it.
            (nvm / 'alias/lts/old').write_text('default')
            with patch.object(agent_cli.shutil, 'which', return_value=None):
                self.assertIsNone(agent_cli._nvm_node_bin(str(home)))

    def test_browser_smoke_requires_both_launchers_and_verified_service_node(self) -> None:
        from lib.service_node import SERVICE_NODE

        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            mcp, doctor = home / 'mcp', home / 'doctor'
            mcp.write_text('unset NODE_OPTIONS NODE_PATH\n'
                           f'exec {SERVICE_NODE} {agent_cli._BROWSER_MCP_CLI}\n')
            mcp.chmod(0o755)
            for interpreter, verified in (('/usr/bin/node', True), (SERVICE_NODE, False), (SERVICE_NODE, True)):
                doctor.write_text('unset NODE_OPTIONS NODE_PATH\n'
                                  f'exec /usr/bin/timeout 180s {interpreter} {agent_cli._BROWSER_SMOKE_SCRIPT}\n')
                doctor.chmod(0o755)
                with self.subTest(interpreter=interpreter, verified=verified), \
                        patch.object(agent_cli, '_BROWSER_MCP_WRAPPER', str(mcp)), \
                        patch.object(agent_cli, '_BROWSER_DOCTOR_WRAPPER', str(doctor)), \
                        patch.object(agent_cli, '_browser_launchers_secure', return_value=True), \
                        patch.object(agent_cli, '_browser_running_processes', return_value={'inspected': True, 'stale': 0}), \
                        patch.object(agent_cli, '_browser_workflow_skills', return_value=()), \
                        patch.object(agent_cli, '_tool_path', return_value=None), \
                        patch('lib.service_node.verify_service_node', side_effect=None if verified else RuntimeError('untrusted')) as verify, \
                        patch.object(agent_cli.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'browser-ready', '')) as run:
                    result = agent_cli.inspect_browser_automation(str(home))
                if interpreter != SERVICE_NODE:
                    self.assertFalse(result['launcher_features']['service_node'])
                    self.assertIsNone(result['smoke_test'])
                    self.assertIn('managed_defaults_stale', result['issues'])
                    verify.assert_not_called()
                    run.assert_not_called()
                else:
                    verify.assert_called_once_with()
                    self.assertEqual(result['smoke_test'], verified)
                    self.assertEqual(run.call_count, int(verified))

    def test_active_nvm_runtime_is_probed_and_checked_against_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            nvm = home / '.nvm'
            binary = nvm / 'versions/node/v24.22.1/bin'
            binary.mkdir(parents=True)
            (nvm / 'nvm.sh').write_text('# nvm')
            for name in ('node', 'npm', 'pnpm'):
                (binary / name).write_text('fixture')
            with patch.object(agent_cli.shutil, 'which', return_value=str(binary / 'node')), \
                    patch.object(agent_cli, '_command_version', side_effect=lambda path, **_: (
                        'v27.0.0' if Path(path).name == 'node' else '12.1.0')):
                result = agent_cli._inspect_node_development(str(home))
            self.assertEqual(result['version'], 'v27.0.0')
            self.assertEqual(result['issues'], ['node_default_unsupported'])
            self.assertFalse(result['healthy'])


if __name__ == "__main__":
    unittest.main()
