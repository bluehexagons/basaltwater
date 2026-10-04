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
            node_bin = nvm_dir / "versions" / "node" / "v24.20.0" / "bin"
            node_bin.mkdir(parents=True)
            (nvm_dir / "nvm.sh").write_text("# nvm\n", encoding="utf-8")
            for name in ("node", "npm", "corepack"):
                (node_bin / name).write_text("", encoding="utf-8")

            def version(path: str, *_arguments: str, **_options: object) -> str | None:
                return {
                    "node": "v24.20.0",
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
        self.assertEqual(result["version"], "v24.20.0")

    def test_node_probes_ignore_project_policy_and_use_matching_interpreter(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            home = Path(temporary_directory)
            nvm_dir = home / ".nvm"
            node_bin = nvm_dir / "versions" / "node" / "v24.20.0" / "bin"
            node_bin.mkdir(parents=True)
            (nvm_dir / "nvm.sh").write_text("# nvm\n", encoding="utf-8")
            (home / "package.json").write_text(
                '{"packageManager":"npm@11.19.0"}', encoding="utf-8",
            )
            versions = {
                "node": "v24.20.0", "npm": "11.19.0",
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
                probe_directories.add(str(directory))
                return subprocess.CompletedProcess(command, 0, versions[Path(command[0]).name] + "\n", "")

            with (
                patch.object(agent_cli, "_nvm_node_bin", return_value=str(node_bin)),
                patch.dict(os.environ, {"PATH": "/other-node/bin:/usr/bin"}),
                patch.object(agent_cli.subprocess, "run", side_effect=run) as process,
            ):
                result = agent_cli._inspect_node_development(str(home))

            self.assertTrue(result["healthy"])
            self.assertEqual(result["pnpm"], "12.6.0")
            self.assertEqual(process.call_count, 4)
            self.assertEqual(len(probe_directories), 1)
            self.assertTrue(all(not Path(directory).exists() for directory in probe_directories))

    def test_development_readiness_ignores_absent_optional_toolchains(self) -> None:
        healthy_node = {
            "installed": True,
            "healthy": True,
            "version": "v24.20.0",
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


if __name__ == "__main__":
    unittest.main()
