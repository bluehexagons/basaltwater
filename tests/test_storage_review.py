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
    def test_inventory_only_requires_traversal_access_to_ancestors(self):
        from lib import agent_storage_inventory as inventory
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "traverse" / "home" / "cache"
            cache.mkdir(parents=True)
            (cache / "data").write_text("cache")
            real_open = os.open
            def restricted_open(path, flags, *args, **kwargs):
                if str(path) == "traverse" and not flags & os.O_PATH:
                    raise PermissionError("ancestor is traverse-only")
                return real_open(path, flags, *args, **kwargs)
            with patch.object(inventory.os, "open", side_effect=restricted_open):
                report = inventory.inspect_storage(home=str(cache.parent), paths=[str(cache)])
            self.assertEqual(report["candidates"][0]["status"], "measured")
            self.assertEqual(report["candidates"][0]["files"], 1)

    def test_build_cache_fallback_works_without_go_and_preserves_data(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "build-fixture"
            cache = home / ".cache/go-build"
            cache.mkdir(parents=True)
            (cache / "compiled").write_text("generated")
            (home / "database").write_text("preserve")
            for path in (home, cache.parent):
                path.chmod(0o700)
            cache.chmod(0o500)
            info = cache.stat()
            with patch.object(storage, "BUILD_HOMES", directory), patch.object(
                    storage.pwd, "getpwuid", return_value=SimpleNamespace(pw_dir=str(home))), patch.object(
                    storage, "_run_native", side_effect=FileNotFoundError("go unavailable")):
                storage._native_cleanup("go-build", str(home), info.st_dev, info.st_ino)
            self.assertFalse(cache.exists())
            self.assertEqual((home / "database").read_text(), "preserve")

    def test_missing_build_home_is_absent_instead_of_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            account = SimpleNamespace(pw_name="fixture", pw_uid=os.geteuid(), pw_dir=directory + "/missing")
            with patch.object(storage, "accounts", return_value=[account]), patch.object(storage, "run_streamed") as run:
                report = storage.maintain(names=[], apply=True, budget=0, target=0)
            self.assertEqual(report["accounts"][0]["status"], "absent_home")
            self.assertNotIn("error", report["accounts"][0])
            run.assert_not_called()

    def test_pressure_prunes_only_unused_unaliased_older_build_versions(self):
        from lib import storage_pressure as pressure
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "build-fixture"
            root = home / ".nvm"
            parent = root / "versions/node"
            versions = ["v24.0.0", "v24.1.0", "v24.2.0", "v24.3.0", "v26.1.0"]
            for version in versions:
                (parent / version / "bin").mkdir(parents=True)
                (parent / version / "bin/node").write_text("fixture")
            (root / "alias").mkdir()
            (root / "alias/default").write_text("v24.0.0")
            for current, _dirs, _files in os.walk(home):
                Path(current).chmod(0o700)
            account = SimpleNamespace(pw_name="build-fixture", pw_uid=os.geteuid(), pw_gid=os.getegid(), pw_dir=str(home))
            with patch.object(storage, "BUILD_HOMES", directory), patch.object(storage, "accounts", return_value=[account]), patch.object(
                    storage, "activity", return_value=None), patch.object(storage, "filesystem", return_value={"free_bytes": 0}), patch.object(
                    pressure, "_process_uids", return_value={os.geteuid()}), patch.object(
                    pressure, "process_uses_path", side_effect=lambda path, **_: path.endswith("v24.1.0")):
                preview = pressure.clean_build_versions(target=1, apply=False)
                self.assertTrue((parent / "v24.2.0").exists())
                self.assertEqual([r["path"] for r in preview if r["status"] == "preview"], [str(parent / "v24.2.0")])
                applied = pressure.clean_build_versions(target=1, apply=True)
            self.assertEqual([r["path"] for r in applied if r["status"] == "cleaned"], [str(parent / "v24.2.0")])
            self.assertEqual(sorted(p.name for p in parent.iterdir()), sorted(set(versions) - {"v24.2.0"}))

    def test_build_npm_fallback_removes_cache_and_preserves_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "build-fixture"
            cache = home / ".npm/_cacache"
            cache.mkdir(parents=True)
            for path in (home, cache.parent, cache):
                path.chmod(0o700)
            (cache / "package").write_text("download")
            (home / ".npmrc").write_text("preserve")
            info = cache.stat()
            with patch.object(storage, "BUILD_HOMES", directory), patch.object(
                    storage.pwd, "getpwuid", return_value=SimpleNamespace(pw_dir=str(home))), patch.object(
                    storage, "_run_native", side_effect=FileNotFoundError("npm unavailable")):
                storage._native_cleanup("npm-content", str(home), info.st_dev, info.st_ino)
            self.assertFalse(cache.exists())
            self.assertEqual((home / ".npmrc").read_text(), "preserve")

    def test_extended_build_cache_unlinks_symlinks_without_following_them(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "build-fixture"
            cache = home / ".npm/_npx"
            cache.mkdir(parents=True)
            for path in (home, cache.parent, cache):
                path.chmod(0o700)
            database = home / "database"
            database.mkdir()
            (database / "records").write_text("preserve")
            (cache / "linked-data").symlink_to(database, target_is_directory=True)
            info = cache.stat()
            with patch.object(storage, "BUILD_HOMES", directory), patch.object(
                    storage.pwd, "getpwuid", return_value=SimpleNamespace(pw_dir=str(home))):
                storage._native_cleanup("npx-builds", str(home), info.st_dev, info.st_ino)
            self.assertFalse(cache.exists())
            self.assertEqual((database / "records").read_text(), "preserve")

    def test_system_pressure_preview_does_not_run_commands(self):
        from lib import storage_pressure as pressure
        with patch.object(storage, "filesystem", return_value={"free_bytes": 0}), patch.object(pressure.subprocess, "run") as run:
            report = pressure.clean_system_storage(target=1, apply=False)
            run.assert_not_called()
            self.assertEqual([r["status"] for r in report], ["preview", "preview"])
            self.assertEqual(report[1]["command"], ["journalctl", "--rotate", "--vacuum-size=64M", "--vacuum-time=7d"])

    def test_failed_build_cache_reports_worker_output_and_continues(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "build-fixture"
            npx = home / ".npm/_npx"
            modules = home / "go/pkg/mod"
            npx.mkdir(parents=True)
            modules.mkdir(parents=True)
            (home / "data").write_text("preserve")
            (npx / "link").symlink_to(home / "data")
            (modules / "module").write_text("download")
            for current, _dirs, _files in os.walk(home):
                Path(current).chmod(0o700)
            account = SimpleNamespace(pw_name="build-fixture", pw_uid=os.geteuid(), pw_dir=str(home))
            def fail_worker(_command, **kwargs):
                kwargs["on_output"]("PermissionError: fixture diagnostic\n")
                return 1
            with patch.object(storage, "BUILD_HOMES", directory), patch.object(storage, "accounts", return_value=[account]), patch.object(
                    storage, "activity", return_value=None), patch.object(storage, "filesystem", return_value={"free_bytes": 0, "mount": "/"}), patch.object(
                    storage, "run_streamed", side_effect=fail_worker):
                record = storage.maintain(names=[], apply=True, budget=0, target=1)["accounts"][0]
            failed = [row for row in record["candidates"] if row["status"] == "failed_native_cleanup"]
            self.assertEqual([row["category"] for row in failed], ["npx-builds", "go-modules"])
            self.assertTrue(all("fixture diagnostic" in row["reason"] for row in failed))
            self.assertNotIn("error", record)
            self.assertEqual((home / "data").read_text(), "preserve")

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
