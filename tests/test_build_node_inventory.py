"""Managed inventory freezing preserves real installer ownership records."""

from __future__ import annotations

from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import build_host_steps as host
from lib.node_runtime_ownership import mark_runtime, runtime_lock, runtime_owner


class BuildNodeInventoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.nvm = self.home / ".nvm"
        self.runtime = self.nvm / "versions/node/v24.22.1"
        (self.runtime / "bin").mkdir(parents=True)
        (self.runtime / "bin/node").write_text("fixture")
        self.enterContext(patch.object(host, "BUILD_NODE_HOME", str(self.home)))
        self.enterContext(patch.object(host, "_ensure_directory"))
        self.enterContext(patch.object(host, "_trusted_path"))
        self.enterContext(patch("lib.node_toolchain.supported_node_baselines", return_value=()))
        self.enterContext(patch.object(host, "run", side_effect=self.command))
        self.installer = self.enterContext(patch("common.common_steps.install_node_for_user"))
        self.installer.side_effect = lambda *a, **kw: mark_runtime(self.nvm, "24.22.1", owner="project")
        self.config = SimpleNamespace(install_node=True, refresh_packages=False)

    def command(self, args, **kwargs):
        # All host operations are mocked; model chmod solely on temporary files.
        if args[:2] == ["chmod", "-R"]:
            for path in [self.home, *self.home.rglob("*")]:
                mode = stat.S_IMODE(path.stat().st_mode)
                if args[2] == "u+rwX":
                    mode |= 0o600 | (0o100 if path.is_dir() or mode & 0o111 else 0)
                elif args[2] == "go-w":
                    mode &= ~0o022
                else:
                    self.fail("Unexpected recursive inventory permission change")
                path.chmod(mode)

    def assert_private_records(self):
        for path in (self.nvm / ".basaltwater-runtime.lock", self.runtime / ".basaltwater-runtime.json"):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        with runtime_lock(self.nvm):
            self.assertEqual(runtime_owner(self.nvm, "24.22.1"), "project")

    def test_repeat_install_preserves_private_metadata(self):
        host._install_node_toolchain(self.config)
        self.assert_private_records()
        host._install_node_toolchain(self.config)
        self.assert_private_records()

    def test_old_widened_records_are_repaired_before_install(self):
        mark_runtime(self.nvm, "24.22.1", owner="project")
        (self.nvm / ".basaltwater-runtime.lock").chmod(0o644)
        (self.runtime / ".basaltwater-runtime.json").chmod(0o644)
        host._install_node_toolchain(self.config)
        self.assert_private_records()

    def test_failed_install_can_retry_existing_records(self):
        def interrupted(*args, **kwargs):
            mark_runtime(self.nvm, "24.22.1", owner="project")
            raise RuntimeError("interrupted installer")
        self.installer.side_effect = interrupted
        with self.assertRaisesRegex(RuntimeError, "interrupted installer"):
            host._install_node_toolchain(self.config)
        self.assert_private_records()
        self.installer.side_effect = lambda *a, **kw: mark_runtime(self.nvm, "24.22.1", owner="project")
        host._install_node_toolchain(self.config)
        self.assert_private_records()
