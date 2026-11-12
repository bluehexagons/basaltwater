"""Review regressions for owner-native cache maintenance and legacy npm."""

from __future__ import annotations

from contextlib import nullcontext
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lib import managed_storage as storage
from web.service_tools import auto_update_node as updater
from lib.update_policy import ECOSYSTEM_AUTO_UPGRADE_ENV


class StorageReviewTests(unittest.TestCase):
    def test_unrelated_test_fixtures_leave_real_guard_enabled(self):
        with patch.object(storage, "storage_lock", return_value=nullcontext()), patch.object(
                storage, "configured_preflight", side_effect=OSError("preflight refused")):
            with self.assertRaisesRegex(OSError, "preflight refused"):
                with storage.operation_guard("setup", ["/tmp"]):
                    self.fail("the real safety guard was bypassed")

    def test_system_npm_does_not_require_nvm(self):
        with patch.object(storage.os.path, "lexists", return_value=False), patch(
                "lib.node_runtime_ownership.runtime_lock") as lock, patch.object(storage.subprocess, "run") as run:
            storage._run_native("npm-content", {"HOME": "/home/fixture", "NVM_DIR": "/home/fixture/.nvm"})
        lock.assert_not_called()
        self.assertEqual(run.call_args.args[0], ["npm", "--cache", "/home/fixture/.npm", "cache", "clean", "--force"])

    def test_worker_rechecks_home_and_cache_identity_before_native_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".cache/go-build"
            path.mkdir(parents=True)
            path.parent.chmod(0o700)
            path.chmod(0o700)
            info = path.stat()
            with patch.object(storage.pwd, "getpwuid", return_value=SimpleNamespace(pw_dir=directory)), \
                    patch.object(storage, "_run_native") as native:
                with self.assertRaisesRegex(ValueError, "home changed"):
                    storage._native_cleanup("go-build", "/wrong/home", info.st_dev, info.st_ino)
                with self.assertRaisesRegex(ValueError, "changed since inventory"):
                    storage._native_cleanup("go-build", directory, info.st_dev, info.st_ino + 1)
                native.assert_not_called()
                storage._native_cleanup("go-build", directory, info.st_dev, info.st_ino)
                self.assertEqual(native.call_count, 1)
                env = native.call_args.args[1]
                self.assertEqual(env["GOCACHE"], str(path))
                self.assertEqual(env["GOCACHEPROG"], "")
                self.assertNotIn("NPM_CONFIG_USERCONFIG", env)

    def test_post_cleanup_inventory_failure_is_not_success(self):
        with tempfile.TemporaryDirectory() as directory:
            for relative, _, _ in storage.CACHES:
                (Path(directory) / relative).mkdir(parents=True)
            account = SimpleNamespace(pw_name="fixture", pw_uid=os.geteuid(), pw_dir=directory)
            inspect = storage.inspect_storage
            def inventory(**kwargs):
                result = inspect(**kwargs)
                if len(kwargs["paths"]) == 1:
                    result["candidates"][0].update(status="partial", issues=["scan_limit_reached"])
                return result
            with patch.object(storage, "accounts", return_value=[account]), patch.object(
                    storage, "activity", return_value=None), patch.object(storage, "safe_cache_path", return_value=True), \
                    patch.object(storage, "run_streamed", return_value=0) as run, patch.object(
                    storage, "inspect_storage", side_effect=inventory), patch.object(storage, "_lock_fd") as fd:
                fd.get.return_value = 123
                result = storage.maintain(names=[], apply=True, budget=0, target=0, force=True)
                self.assertEqual(result["accounts"][0]["candidates"][0]["status"], "failed_verification")
                self.assertIn("Post-cleanup", result["accounts"][0]["error"])
                self.assertEqual(run.call_args.kwargs["pass_fds"], (123,))

    def test_legacy_default_lts_receives_only_npm_update(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory) / "versions/node/v24.22.1"
            (runtime / "bin").mkdir(parents=True)
            (runtime / "bin/node").write_text("fixture")
            for owner, default, globals_enabled, expected in ((None, "v24.22.1", "0", 1),
                    (None, "v24.22.1", "1", 1), (None, "v26.12.1", "0", 0), ("project", "v24.22.1", "0", 0)):
                with self.subTest(owner=owner, default=default, globals_enabled=globals_enabled), patch.dict(os.environ, {
                        ECOSYSTEM_AUTO_UPGRADE_ENV: globals_enabled, "BASALTWATER_NPM_AUTO_UPDATE": "1"}), patch.object(
                        updater, "get_nvm_dir", return_value=directory), patch.object(
                        updater, "runtime_lock", return_value=nullcontext()), patch.object(
                        updater, "runtime_owner", return_value=owner), patch.object(
                        updater, "get_current_version", return_value=default), patch.object(
                        updater, "run_nvm_command", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
                    self.assertEqual(updater.update_global_packages("v24.22.1"), (True, None))
                    self.assertEqual(run.call_count, expected)
                    if expected:
                        self.assertIn("npm@latest", run.call_args.args[0])
                        self.assertIn("--engine-strict", run.call_args.args[0])
