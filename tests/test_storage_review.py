"""Review regressions for owner-native cache maintenance and legacy npm."""

from __future__ import annotations

from contextlib import ExitStack, nullcontext
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
            def capacity(path):
                runtime = Path(path).is_relative_to(parent)
                return dict(mount="/runtimes" if runtime else "/", free_bytes=0 if runtime else 2)
            with patch.object(storage, "BUILD_HOMES", directory), patch.object(storage, "accounts", return_value=[account]), patch.object(
                    storage, "activity", return_value=None), patch.object(storage, "filesystem", side_effect=capacity), patch.object(
                    pressure, "_process_uids", return_value={os.geteuid()}), patch.object(
                    pressure, "process_uses_path", side_effect=lambda path, **_: path.endswith("v24.1.0")):
                with patch.object(storage, "filesystem", side_effect=lambda path: dict(
                        mount="/" if path == str(home) else "/separate-node", free_bytes=0)):
                    separate = pressure.clean_build_versions(target=1, apply=True,
                        requirements={"/": dict(required_bytes=1, required_inodes=0)})
                self.assertEqual(separate, [])
                self.assertFalse((root / ".basaltwater-runtime.lock").exists())
                with patch.object(pressure, "inspect_storage", return_value={
                        "issues": ["mount_inventory_unavailable"], "candidates": []}):
                    deferred = pressure.clean_build_versions(target=1, apply=True)
                self.assertTrue(all(row["status"] == "deferred" for row in deferred))
                self.assertEqual(sorted(p.name for p in parent.iterdir()), sorted(versions))
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

    def test_npm_allocation_failure_falls_back_only_in_managed_build_homes(self):
        for managed, allocation in ((True, "directory"), (True, "file"), (False, "directory"), (False, "file")):
            with self.subTest(managed=managed, allocation=allocation), tempfile.TemporaryDirectory() as directory:
                home = Path(directory) / ("build-fixture" if managed else "ordinary")
                cache = home / ".npm/_cacache"
                cache.mkdir(parents=True)
                (cache / "download").write_text("reproducible")
                (home / ".npmrc").write_text("preserve")
                for path in (home, cache.parent, cache):
                    path.chmod(0o700)
                info = cache.stat()
                with ExitStack() as mocks:
                    mocks.enter_context(patch.object(storage, "BUILD_HOMES", directory))
                    mocks.enter_context(patch.object(storage.pwd, "getpwuid", return_value=SimpleNamespace(pw_dir=str(home))))
                    native = mocks.enter_context(patch.object(storage, "_run_native"))
                    failure = OSError(28, "No space left on device")
                    if allocation == "directory":
                        mocks.enter_context(patch.object(storage.tempfile, "TemporaryDirectory", side_effect=failure))
                    else:
                        write = Path.write_text
                        def full_configuration(path, *args, **kwargs):
                            if path.name == "user.npmrc":
                                raise failure
                            return write(path, *args, **kwargs)
                        mocks.enter_context(patch.object(Path, "write_text", full_configuration))
                    if managed:
                        storage._native_cleanup("npm-content", str(home), info.st_dev, info.st_ino)
                    else:
                        with self.assertRaises(OSError) as error:
                            storage._native_cleanup("npm-content", str(home), info.st_dev, info.st_ino)
                        self.assertEqual(error.exception.errno, 28)
                    native.assert_not_called()
                self.assertEqual(cache.exists(), not managed)
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
        with patch.object(pressure, "clean_temporary_artifacts", return_value={
                "selected": 0, "deferred": 0, "failures": []}), patch.object(
                storage, "filesystem", return_value={"free_bytes": 0}), patch.object(
                pressure.os.path, "isdir", return_value=True), patch.object(pressure.subprocess, "run") as run:
            report = pressure.clean_system_storage(target=1, apply=False)
            run.assert_not_called()
            self.assertEqual([r["status"] for r in report], ["preview"] * 8)
            self.assertEqual(report[1]["command"], ["journalctl", "--directory=/var/log/journal",
                                                   "--rotate", "--vacuum-size=64M", "--vacuum-time=7d"])
            self.assertEqual([r["command"] for r in report[3:6]], [
                ["systemd-tmpfiles", "--clean", f"--prefix={path}"]
                for path in ("/tmp", "/var/tmp", "/var/lib/systemd/coredump")])
            self.assertTrue(all(row["before"]["free_bytes"] == 0 and "net_freed_bytes" not in row for row in report))

    def test_system_cleanup_reports_signed_capacity_changes_even_after_native_failure(self):
        from lib import storage_pressure as pressure
        for delta, exit_code, supported in ((64, 0, True), (0, 0, True), (-10, 1, True), (64, 0, False)):
            with self.subTest(delta=delta, exit_code=exit_code, supported=supported):
                before = dict(mount="/", free_bytes=100, free_inodes=10, inodes_supported=supported)
                after = dict(before, free_bytes=100 + delta, free_inodes=11)
                with patch.object(storage, "filesystem", side_effect=[before, after]), patch.object(
                        pressure.os.path, "isdir", return_value=False), patch.object(
                        pressure.shutil, "which", return_value="/usr/bin/fixture-cleaner"), patch.object(
                        pressure.subprocess, "run", return_value=subprocess.CompletedProcess([], exit_code, "fixture diagnostic", "")) as native, \
                        tempfile.TemporaryFile() as lease, patch.object(storage, "_lock_fd") as descriptor:
                    descriptor.get.return_value = lease.fileno()
                    row, = pressure.clean_system_storage(target=200, apply=True)
                    self.assertEqual(native.call_args.kwargs["pass_fds"], (lease.fileno(),))
                self.assertEqual(row["status"], "failed" if exit_code else "cleaned")
                self.assertEqual(row["net_freed_bytes"], delta)
                self.assertEqual(row.get("net_freed_inodes"), 1 if supported else None)
                if exit_code:
                    self.assertIn("fixture diagnostic", row["reason"])

    def test_system_cleanup_refuses_unknown_or_changed_final_filesystem(self):
        from lib import storage_pressure as pressure
        for outcome in (OSError("Capacity unavailable"), dict(mount="/replacement", free_bytes=200)):
            with self.subTest(outcome=outcome), patch.object(storage, "filesystem", side_effect=[dict(mount="/", free_bytes=0), outcome]), patch.object(
                    pressure.os.path, "isdir", return_value=False), patch.object(
                    pressure.shutil, "which", return_value="/usr/bin/fixture-cleaner"), patch.object(
                    pressure.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
                row, = pressure.clean_system_storage(target=1, apply=True)
            self.assertEqual(row["status"], "failed")
            self.assertIn("Final filesystem observation failed", row["reason"])
            self.assertNotIn("net_freed_bytes", row)

    def test_setup_tmpfs_refusal_does_not_clean_unrelated_root_filesystem(self):
        from lib import storage_pressure as pressure
        from tests import real_storage_operation_guard
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            cache = home / ".cache/go-build"
            cache.mkdir(parents=True)
            for path in (cache.parent, cache):
                path.chmod(0o700)
            (cache / "compiled").write_text("preserve")
            account = SimpleNamespace(pw_name="fixture", pw_uid=os.geteuid(), pw_dir=directory)
            def capacity(path):
                return dict(path=path, mount="/tmp" if path == "/tmp" else "/",
                            free_bytes=0 if path == "/tmp" else 1280 * storage.MIB,
                            free_inodes=20000, inodes_supported=True)
            inspect = storage.inspect_storage
            def inventory(**options):
                return {"candidates": []} if options["paths"] == [storage.BUILD_HOMES] else inspect(**options)
            with patch.object(storage, "storage_lock", side_effect=lambda **_: nullcontext()), patch.object(
                    storage, "filesystem", side_effect=capacity), patch.object(storage, "accounts", return_value=[account]), patch.object(
                    storage, "inspect_storage", side_effect=inventory), patch.object(storage, "run_streamed") as worker, patch.object(
                    pressure.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as native, patch.object(
                    pressure.shutil, "which", return_value="/usr/bin/systemd-tmpfiles"), patch.object(
                    pressure.os.path, "isdir", return_value=True), patch.object(
                    pressure, "clean_temporary_artifacts", return_value={
                        "selected": 0, "deferred": 0, "failures": []}) as artifacts, patch.dict(os.environ, {
                        "BASALTWATER_STORAGE_PEAK_MIB": "512", "BASALTWATER_STORAGE_RESERVE_MIB": "512",
                        "BASALTWATER_STORAGE_MIN_INODES": "10000", "BASALTWATER_STORAGE_TMP_PEAK_MIB": "128",
                        "BASALTWATER_STORAGE_TMP_RESERVE_MIB": "64", "TMPDIR": "/tmp"}):
                with self.assertRaises(storage.StoragePreflightError):
                    with real_storage_operation_guard("setup", ["/opt/basaltwater", "/tmp"], cleanup_accounts=[]):
                        self.fail("setup started with a full tmpfs")
                worker.assert_not_called()
                native.assert_called_once()
                self.assertEqual(native.call_args.args[0], ["/usr/bin/systemd-tmpfiles", "--clean", "--prefix=/tmp"])
                artifacts.assert_called_once_with("/tmp", apply=True)
            self.assertEqual((cache / "compiled").read_text(), "preserve")

    def test_inode_pressure_selects_build_caches_and_reports_inode_shortfall(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "build-fixture"
            for relative in (".cache/go-build", "go/pkg/mod"):
                cache = home / relative
                cache.mkdir(parents=True)
                (cache / "generated").write_text("reproducible")
            for current, _dirs, _files in os.walk(home):
                Path(current).chmod(0o700)
            account = SimpleNamespace(pw_name="build-fixture", pw_uid=os.geteuid(), pw_dir=str(home))
            with patch.object(storage, "BUILD_HOMES", directory), patch.object(storage, "accounts", return_value=[account]), patch.object(
                    storage, "activity", return_value=None), patch.object(storage, "filesystem", return_value=dict(
                        mount="/", free_bytes=4 * 1024**3, free_inodes=1, inodes_supported=True)), patch.object(storage, "run_streamed") as worker:
                report = storage.maintain(names=[], apply=False, budget=1024 * storage.MIB, target=1536 * storage.MIB,
                    requirements={"/": dict(required_bytes=1024 * storage.MIB, required_inodes=10000)})
            worker.assert_not_called()
            self.assertEqual([row["category"] for row in report["accounts"][0]["candidates"] if row["status"] == "preview"],
                             ["go-build", "go-modules"])
            self.assertTrue(all(row["remaining_bytes"] == 0 and row["remaining_inodes"] == 9999 for row in report["shortfalls"]))

    def test_system_pressure_preserves_journals_on_other_mounts(self):
        from lib import storage_pressure as pressure
        def capacity(path):
            return dict(mount="/" if path == "/var/cache/apt/archives" else "/separate-journal", free_bytes=0)
        with patch.object(storage, "filesystem", side_effect=capacity), patch.object(pressure.os.path, "isdir", return_value=True), patch.object(
                pressure.shutil, "which", return_value="/usr/bin/fixture-cleaner"), patch.object(
                pressure.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            report = pressure.clean_system_storage(target=1, apply=True,
                requirements={"/": dict(required_bytes=1, required_inodes=0)})
        self.assertEqual([row["category"] for row in report], ["apt-downloads"])
        run.assert_called_once()

    def test_system_pressure_keeps_cleaning_after_unavailable_filesystem(self):
        from lib import storage_pressure as pressure
        def capacity(path):
            if path == "/tmp":
                raise OSError("Temporary filesystem unavailable")
            return dict(mount="/", free_bytes=0)
        with patch.object(pressure, "clean_temporary_artifacts", return_value={
                "selected": 0, "deferred": 0, "failures": []}), patch.object(
                storage, "filesystem", side_effect=capacity), patch.object(
                pressure.os.path, "isdir", return_value=True), patch.object(
                pressure.shutil, "which", return_value="/usr/bin/fixture-cleaner"), patch.object(
                pressure.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
            report = pressure.clean_system_storage(target=1, apply=True)
        self.assertEqual(report[3]["status"], "failed")
        self.assertIn("unavailable", report[3]["reason"])
        self.assertEqual([row["status"] for row in report[4:]], ["cleaned", "cleaned", "failed", "cleaned"])
        self.assertEqual(run.call_count, 5)

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

    def test_build_home_capacity_error_does_not_abort_other_accounts(self):
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory) / "build-fixture"
            ordinary = Path(directory) / "ordinary"
            build.mkdir(mode=0o700)
            ordinary.mkdir(mode=0o700)
            accounts = [SimpleNamespace(pw_name=name, pw_uid=os.geteuid(), pw_dir=str(path))
                        for name, path in (("build-fixture", build), ("ordinary", ordinary))]
            def capacity(path):
                if path.startswith(str(build) + "/"):
                    raise OSError("Build filesystem unavailable")
                return dict(mount="/", free_bytes=4 * 1024**3)
            with patch.object(storage, "BUILD_HOMES", directory), patch.object(storage, "accounts", return_value=accounts), patch.object(
                    storage, "filesystem", side_effect=capacity), patch.object(storage, "run_streamed") as worker:
                report = storage.maintain(names=[], apply=True, budget=1024 * storage.MIB, target=1536 * storage.MIB)
            worker.assert_not_called()
            self.assertIn("Build filesystem unavailable", report["accounts"][0]["error"])
            self.assertNotIn("error", report["accounts"][1])
            self.assertEqual(len(report["accounts"][1]["candidates"]), len(storage.CACHES))

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
