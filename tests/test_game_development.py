"""Debian game prerequisites and native observations never touch the host."""

from __future__ import annotations

from contextlib import ExitStack
import io
import json
from pathlib import Path
import shlex
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import game_development_steps as steps
from lib import game_development as development
from lib.agent_readiness import build_agent_readiness_record
from lib.arg_parser import create_setup_argument_parser
from lib.config import SetupConfig
from lib.system_types import get_steps_for_system_type


class GameDevelopmentTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.receipt = self.root / "game-development.json"
        stack.enter_context(patch.object(steps, "SELECTION_PATH", self.receipt))
        stack.enter_context(patch.object(development, "SELECTION_PATH", self.receipt))
        stack.enter_context(patch.object(steps, "is_dry_run", return_value=False))
        stack.enter_context(patch("sys.stdout", new_callable=io.StringIO))
        self.config = SetupConfig(host="vm.example", username="human", system_type="agent_code_vm",
                                  include_desktop=True, install_game_dev=True)

    def test_flag_roundtrips_and_uses_the_debian_builder(self):
        config = SetupConfig.from_dict("vm.example", "agent_code_vm", self.config.to_dict())
        self.assertTrue(config.install_game_dev)
        parser = create_setup_argument_parser("remote", for_remote=True)
        restored = parser.parse_args(shlex.split(" ".join(config.to_remote_args())))
        self.assertTrue(restored.install_game_dev)
        self.assertIn("--game-dev", config.to_setup_command())
        functions = [function for _, function in get_steps_for_system_type(config)]
        self.assertIn(steps.install_game_development, functions)
        config.install_game_dev = False
        self.assertNotIn(steps.install_game_development, [function for _, function in get_steps_for_system_type(config)])
        self.assertIn(steps.record_game_development_selection, [function for _, function in get_steps_for_system_type(config)])
        for profile in ("server_wsl", "server_proxmox", "custom_steps"):
            with self.assertRaises(ValueError):
                SetupConfig(host="vm.example", username="human", system_type=profile, install_game_dev=True)
        self.assertFalse(any("sdl" in package for package in development.DEBIAN_GAME_PACKAGES))
        for package in ("libgtk-3-dev", "libnss3", "libgbm-dev", "xvfb", "xauth", "cmake", "glslang-tools"):
            self.assertIn(package, development.DEBIAN_GAME_PACKAGES)

    def test_missing_packages_install_without_downloads_or_project_execution(self):
        missing = {"libgtk-3-dev", "cmake"}
        with patch("common.common_steps.is_package_installed", side_effect=lambda name: name not in missing), \
                patch.object(steps, "run", side_effect=lambda *_args, **_kwargs: missing.clear() or subprocess.CompletedProcess([], 0)) as run:
            steps.install_game_development(self.config)
        command = run.call_args.args[0]
        self.assertIn("install", command)
        self.assertEqual(command[-2:], ["cmake", "libgtk-3-dev"])
        self.assertNotIn("update", command)
        self.assertEqual(run.call_args.kwargs["env"]["DEBIAN_FRONTEND"], "noninteractive")
        self.assertEqual(json.loads(self.receipt.read_text()), {"schema_version": 1, "selected": True})
        self.assertEqual(self.receipt.stat().st_mode & 0o777, 0o644)

    def test_existing_packages_need_no_apt_and_deselection_keeps_packages(self):
        with patch("common.common_steps.is_package_installed", return_value=True), patch.object(steps, "run") as run:
            steps.install_game_development(self.config)
            self.config.install_game_dev = False
            steps.record_game_development_selection(self.config)
        run.assert_not_called()
        self.assertFalse(json.loads(self.receipt.read_text())["selected"])

    def test_failures_and_dry_runs_do_not_claim_success(self):
        with patch("common.common_steps.is_package_installed", return_value=False), \
                patch.object(steps, "run", return_value=subprocess.CompletedProcess([], 1)), \
                self.assertRaisesRegex(RuntimeError, "failed to install"):
            steps.install_game_development(self.config)
        self.assertFalse(self.receipt.exists())
        self.config.dry_run = True
        with patch.object(steps, "run") as run:
            steps.install_game_development(self.config)
            steps.record_game_development_selection(self.config)
        run.assert_not_called()
        self.assertFalse(self.receipt.exists())

    def test_receipt_validation_is_explicit_and_no_selection_is_inferred(self):
        self.assertFalse(development.game_development_selected())
        self.receipt.write_text('{"schema_version":1,"selected":true}')
        metadata = SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_uid=0)
        with patch.object(Path, "lstat", return_value=metadata):
            self.assertTrue(development.game_development_selected())
            self.receipt.write_text('{"schema_version":2,"selected":true}')
            self.assertIsNone(development.game_development_selected())
        metadata.st_mode = stat.S_IFLNK | 0o777
        with patch.object(Path, "lstat", return_value=metadata):
            self.assertIsNone(development.game_development_selected())

    def test_native_inventory_distinguishes_project_bootstrap_from_broken_host(self):
        def probe(command, **options):
            self.assertEqual(command[:2], ["/usr/bin/pkg-config", "--modversion"])
            self.assertEqual(options["cwd"], "/")
            self.assertNotIn("HOME", options["env"])
            self.assertNotIn("NODE_OPTIONS", options["env"])
            if command[-1] in development.SOURCE_MODULES:
                return subprocess.CompletedProcess(command, 1, "", "missing")
            return subprocess.CompletedProcess(command, 0, "1.2.3\n", "")

        with patch.object(development.shutil, "which", return_value="/usr/bin/pkg-config"), \
                patch.object(development.subprocess, "run", side_effect=probe) as run:
            result = development.inspect_native_development(selected=True)
        self.assertTrue(result["healthy"])
        self.assertEqual(set(result["project_bootstrap_required"]), development.SOURCE_MODULES)
        self.assertEqual(run.call_count, len(development.NATIVE_MODULES))
        with patch.object(development.shutil, "which", return_value=None), patch.object(development.subprocess, "run") as run:
            selected = development.inspect_native_development(selected=True)
            optional = development.inspect_native_development(selected=False)
        self.assertFalse(selected["healthy"])
        self.assertIn("native_command_missing", selected["issues"])
        self.assertIn("native_module_missing", selected["issues"])
        self.assertTrue(optional["healthy"])
        self.assertFalse(optional["installed"])
        run.assert_not_called()

    def test_native_readiness_record_omits_unrecognized_names_and_paths(self):
        record = build_agent_readiness_record([], [{
            "capability": "development", "healthy": False, "installed": True,
            "issues": ["native_command_missing", "/secret"],
            "toolchains": {"native": {
                "installed": "/secret", "selected": "/secret", "healthy": "/secret",
                "commands": {"cmake": False, "cc": "/secret", "private": "/secret"},
                "modules": {"sdl3": "3.4.14", "glew": "/secret", "private": "/secret"},
                "project_bootstrap_required": ["sdl3-image", "/secret"],
            }},
        }], trigger="manual")
        native = record["capabilities"][0]["toolchains"]["native"]
        self.assertFalse(native["installed"])
        self.assertFalse(native["healthy"])
        self.assertIsNone(native["selected"])
        self.assertEqual(native["commands"], {"cmake": False})
        self.assertEqual(native["modules"], {"sdl3": "3.4.14"})
        self.assertEqual(native["project_bootstrap_required"], ["sdl3-image"])
        self.assertNotIn("/secret", json.dumps(record))


if __name__ == "__main__":
    unittest.main()
