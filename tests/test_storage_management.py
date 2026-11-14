"""Host storage policy, exclusion, native command and preservation contracts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from lib import managed_storage as storage


class StorageManagementTests(unittest.TestCase):
    def test_cli_role_dispatch_releases_cache_lock_and_reports_shortfall(self):
        from contextlib import contextmanager
        import io
        import sys
        held = False

        @contextmanager
        def lock(**_):
            nonlocal held
            held = True
            try:
                yield
            finally:
                held = False

        def roles(**options):
            self.assertFalse(held)
            self.assertFalse(options["release_bundles"])
            self.assertEqual(options["min_age"], 168 * 3600)
            return [dict(role="target", remaining_bytes=1)]

        with patch.object(sys, "argv", ["maintenance", "--payloads", "--apply"]), \
             patch.object(storage.os, "geteuid", return_value=0), \
             patch.object(storage, "storage_lock", side_effect=lock), \
             patch.object(storage, "maintain", return_value=dict(accounts=[], shortfalls=[])), \
             patch("lib.build_storage_maintenance.maintain_roles", side_effect=roles), \
             patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(storage.main(), 1)

    def test_setup_and_deployment_hold_guard_through_operation(self):
        from contextlib import contextmanager
        from tests import real_storage_operation_guard
        held = []
        @contextmanager
        def lock():
            held.append(True)
            try:
                yield
            finally:
                held.pop()
        @storage.guarded_setup
        def setup(config, args):
            self.assertTrue(held)
        class Deployment:
            base_dir = "/srv/releases"
            @storage.guarded_deployment
            def deploy(inner, *, staging_root):
                self.assertTrue(held)
        with patch.object(storage, "operation_guard", real_storage_operation_guard), patch.object(
                storage, "storage_lock", side_effect=lock), patch.object(storage, "configured_preflight") as preflight, \
                patch.object(storage.pwd, "getpwnam", return_value=SimpleNamespace(pw_dir="/home/fixture")):
            setup(SimpleNamespace(username="fixture", deploy_specs=["fixture"]), SimpleNamespace(dry_run=False))
            self.assertIn("/home/fixture", preflight.call_args.args[1])
            Deployment().deploy(staging_root="/srv/staging")
            self.assertIn("/srv/staging", preflight.call_args.args[1])
            self.assertFalse(held)

    def test_space_exhaustion_after_preflight_keeps_phase_and_observations(self):
        from contextlib import nullcontext
        from tests import real_storage_operation_guard
        with patch.object(storage, "storage_lock", return_value=nullcontext()), patch.object(
                storage, "configured_preflight"), patch.object(storage, "filesystem", return_value={
                    "path": "/tmp", "mount": "/tmp", "free_bytes": 0, "free_inodes": 10}), patch.object(
                storage, "inspect_storage", return_value={"candidates": []}):
            with self.assertRaises(OSError) as failure:
                with real_storage_operation_guard("deployment", ["/tmp"]):
                    raise OSError(28, "No space left on device")
        self.assertIn('"phase": "deployment"', str(failure.exception))
        self.assertIn('"mount": "/tmp"', str(failure.exception))

    def test_operation_preflight_failure_prevents_mutation(self):
        from contextlib import nullcontext
        from tests import real_storage_operation_guard
        with patch.object(storage, "storage_lock", return_value=nullcontext()), patch.object(
                storage, "configured_preflight", side_effect=OSError("insufficient inodes")) as preflight:
            with self.assertRaisesRegex(OSError, "insufficient inodes"):
                with real_storage_operation_guard("deployment", ["/srv/releases"]):
                    self.fail("operation began despite failed preflight")
        preflight.assert_called_once_with("deployment", ["/srv/releases"])

    def test_preflight_reports_small_separate_tmpfs_and_inode_shortfall(self):
        def capacity(path):
            return {"path": path, "mount": path, "free_bytes": 233 * storage.MIB if path == "/tmp" else 4 * 1024**3,
                    "free_inodes": 1 if path == "/data" else 20000, "inodes_supported": True}
        with patch.object(storage, "filesystem", side_effect=capacity), patch.object(
            storage, "inspect_storage", return_value={"candidates": [{"allocated_bytes": 100}]}
        ):
            with self.assertRaises(OSError) as failure:
                storage.preflight("build staging", ["/", "/tmp", "/data"])
        message = str(failure.exception)
        for expected in ("build staging", '"mount": "/tmp"', '"mount": "/data"',
                         '"free_inodes": 1', '"allocated_bytes": 100', '"required_bytes": 1073741824'):
            self.assertIn(expected, message)

    def test_lock_excludes_cleanup_and_releases_after_exception(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(storage, "LOCK", directory + "/lock"):
            real_fstat = os.fstat
            def root_info(fd):
                info = real_fstat(fd)
                return SimpleNamespace(st_mode=info.st_mode, st_uid=0, st_nlink=info.st_nlink)
            with patch.object(storage.os, "fstat", side_effect=root_info):
                with storage.storage_lock():
                    def attempt():
                        with storage.storage_lock(cleanup=True):
                            self.fail("cleanup entered a held shared lock")
                    with ThreadPoolExecutor(max_workers=1) as executor:
                        with self.assertRaises(BlockingIOError):
                            executor.submit(attempt).result()
                with self.assertRaisesRegex(RuntimeError, "fixture"):
                    with storage.storage_lock(cleanup=True):
                        raise RuntimeError("fixture")
                with storage.storage_lock(cleanup=True):
                    pass

    def test_preview_active_failure_and_apply_preserve_persistent_files(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            for relative, _, _ in storage.CACHES:
                cache = home / relative
                cache.mkdir(parents=True)
                (cache / "disposable").write_text("cache")
            protected = [".npm/_npx/tool", ".npmrc", ".nvm/versions/node/v24/bin/node",
                         "go/pkg/mod/toolchain/bin/go", "site/index.html", "data/database"]
            for relative in protected:
                path = home / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("preserve")
            for path in home.rglob("*"):
                if path.is_dir():
                    path.chmod(0o755)
            account = SimpleNamespace(pw_name="fixture", pw_uid=os.geteuid(), pw_dir=directory)
            with patch.object(storage, "accounts", return_value=[account]), patch.object(
                storage, "activity", return_value=None
            ) as active, patch.object(storage, "run_streamed", return_value=0) as run:
                def maintain(apply):
                    return storage.maintain(names=[], apply=apply, budget=0, target=0, force=True)
                result = maintain(False)
                self.assertEqual([r["status"] for r in result["accounts"][0]["candidates"]], ["preview"] * 3)
                self.assertEqual([(r["path"], r["category"]) for r in result["accounts"][0]["candidates"]],
                                 [(str(home / relative), category) for relative, category, _ in storage.CACHES])
                run.assert_not_called()
                for reason in ("active build", "unknown activity"):
                    active.return_value = reason
                    result = maintain(True)
                    self.assertEqual([r["status"] for r in result["accounts"][0]["candidates"]], ["deferred"] * 3)
                run.assert_not_called()
                active.return_value = None
                run.side_effect = OSError("native tool unavailable")
                result = maintain(True)
                self.assertTrue(all(r["status"] == "failed_native_cleanup" for r in result["accounts"][0]["candidates"]))
                run.side_effect = None
                result = maintain(True)
                self.assertEqual([r["status"] for r in result["accounts"][0]["candidates"]], ["cleaned"] * 3)
                self.assertTrue(all("after" in r for r in result["accounts"][0]["candidates"]))
            for relative in protected:
                self.assertEqual((home / relative).read_text(), "preserve")

    def test_symlink_cache_is_skipped_and_shortfall_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / ".npm").mkdir()
            (home / ".npm/_cacache").symlink_to(home, target_is_directory=True)
            account = SimpleNamespace(pw_name="fixture", pw_uid=os.geteuid(), pw_dir=directory)
            with patch.object(storage, "accounts", return_value=[account]), patch.object(storage, "run_streamed") as run:
                result = storage.maintain(names=[], apply=True, budget=0, target=10**18)
            run.assert_not_called()
            self.assertEqual(result["accounts"][0]["candidates"][0]["status"], "skipped_unsafe_or_incomplete")
            self.assertGreater(result["shortfalls"][0]["remaining_bytes"], 0)

    def test_go_native_command_disables_hooks_and_preserves_modules(self):
        env = {"HOME": "/home/fixture", "GOCACHE": "/home/fixture/.cache/go-build",
               "GOTOOLCHAIN": "local", "GOENV": "off"}
        with patch.object(storage.subprocess, "run") as run:
            storage._run_native("go-build", env)
        self.assertEqual(run.call_args.args[0], ["go", "clean", "-cache"])
        env = run.call_args.kwargs["env"]
        self.assertEqual(env["GOCACHE"], "/home/fixture/.cache/go-build")
        self.assertEqual(env["GOTOOLCHAIN"], "local")
        self.assertEqual(env["GOENV"], "off")

    def test_native_npm_and_nvm_commands_hold_runtime_lock(self):
        from contextlib import nullcontext
        for category, expected in (("npm-content", 'npm --cache "$HOME/.npm" cache clean --force'),
                                   ("nvm-downloads", "nvm cache clear")):
            with self.subTest(category=category), patch.object(storage.os.path, "lexists", return_value=True), patch(
                    "lib.node_runtime_ownership.runtime_lock", return_value=nullcontext()) as lock, patch.object(
                    storage.subprocess, "run") as run:
                storage._run_native(category, {"HOME": "/home/fixture", "NVM_DIR": "/home/fixture/.nvm"})
                lock.assert_called_once_with("/home/fixture/.nvm")
                self.assertIn(expected, run.call_args.args[0][-1])
                self.assertIn("--noprofile", run.call_args.args[0])
