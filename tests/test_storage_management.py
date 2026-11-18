"""Host storage policy, exclusion, native command and preservation contracts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import io
import json
from pathlib import Path
import os
import sys
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from lib import managed_storage as storage


class StorageManagementTests(unittest.TestCase):
    def test_setup_pressure_cleans_once_under_exclusive_ownership_before_operation(self):
        from contextlib import contextmanager
        from tests import real_storage_operation_guard
        held = []

        @contextmanager
        def lock(*, cleanup=False):
            self.assertFalse(held)
            held.append(cleanup)
            try:
                yield
            finally:
                held.pop()

        def clean(**options):
            self.assertEqual(held, [True])
            self.assertEqual(options["target"], 0)
            self.assertTrue(options["apply"])
            self.assertEqual(options["requirements"]["/"]["required_bytes"], 2048 * storage.MIB)
            self.assertEqual(options["requirements"]["/"]["required_inodes"], 10000)
            return {"accounts": []}

        pressure = storage.StoragePreflightError("low space", [dict(
            mount="/", required_bytes=2048 * storage.MIB, required_inodes=10000)])
        with patch.object(storage, "storage_lock", side_effect=lock), patch.object(
                storage, "configured_preflight", side_effect=[pressure, None]) as check, patch(
                "lib.storage_pressure.clean_system_storage", return_value=[]), patch(
                "lib.storage_pressure.clean_build_versions", return_value=[]), patch.object(
                storage, "maintain", side_effect=clean) as maintain:
            with real_storage_operation_guard("setup", ["/srv"], cleanup_accounts=["fixture"]):
                self.assertEqual(held, [False])
            maintain.assert_called_once()
            self.assertEqual(check.call_count, 2)

    def test_setup_recovery_handles_all_mounts_and_keeps_the_strictest_requirement(self):
        from tests import real_storage_operation_guard
        for separate in (True, False):
            with self.subTest(separate=separate):
                recovered = False
                temporary = "/scratch-tmp"
                def capacity(path):
                    memory = path == temporary and separate
                    return dict(path=path, mount=temporary if memory else "/healthy" if path == "/healthy" else "/",
                                filesystem_type="tmpfs" if memory else "ext4", total_bytes=512 * storage.MIB if memory else 8 * 1024**3,
                                free_bytes=(256 * storage.MIB if memory else 4 * 1024**3) if recovered or path == "/healthy" else 0,
                                free_inodes=20000, inodes_supported=True)
                def cleanup(**options):
                    nonlocal recovered
                    required = options["requirements"]
                    self.assertEqual(options["target"], 0)
                    self.assertEqual(set(required), {"/", temporary} if separate else {"/"})
                    self.assertEqual(required["/"]["required_bytes"], 3072 * storage.MIB)
                    if separate:
                        self.assertEqual(required[temporary]["required_bytes"], 192 * storage.MIB)
                    recovered = True
                    return []
                with patch.object(storage, "storage_lock", side_effect=lambda **_: nullcontext()), patch.object(
                        storage, "filesystem", side_effect=capacity), patch.object(
                        storage, "inspect_storage", return_value={"candidates": []}) as inventory, patch.object(
                        storage, "maintain", return_value={"accounts": []}) as caches, patch(
                        "lib.storage_pressure.clean_system_storage", side_effect=cleanup) as native, patch(
                        "lib.storage_pressure.clean_build_versions", return_value=[]) as versions, patch.dict(os.environ, {
                            "TMPDIR": temporary, "BASALTWATER_STORAGE_PEAK_MIB": "2560", "BASALTWATER_STORAGE_RESERVE_MIB": "512",
                            "BASALTWATER_STORAGE_TMP_PEAK_MIB": "128", "BASALTWATER_STORAGE_TMP_RESERVE_MIB": "64",
                            "BASALTWATER_STORAGE_MIN_INODES": "10000"}):
                    with real_storage_operation_guard("setup", ["/srv", temporary, "/healthy"], cleanup_accounts=[]):
                        self.assertTrue(recovered)
                    inventory.assert_called_once()
                    native.assert_called_once()
                    caches.assert_called_once()
                    versions.assert_called_once()
                    self.assertEqual(caches.call_args.kwargs["requirements"], native.call_args.kwargs["requirements"])
                    self.assertEqual(versions.call_args.kwargs["requirements"], native.call_args.kwargs["requirements"])

    def test_setup_refuses_persistent_pressure_without_retrying_setup(self):
        from contextlib import nullcontext
        import io
        from tests import real_storage_operation_guard
        from unittest.mock import Mock
        action = Mock()
        setup = storage.guarded_setup(action)
        pressure = storage.StoragePreflightError("low space", [dict(
            mount="/", required_bytes=storage.MIB, required_inodes=10000)])
        with patch.object(storage, "operation_guard", real_storage_operation_guard), patch.object(
                storage, "storage_lock", side_effect=lambda **_: nullcontext()), patch(
                "lib.storage_pressure.clean_system_storage", return_value=[]), patch(
                "lib.storage_pressure.clean_build_versions", return_value=[]), patch.object(
                storage, "configured_preflight", side_effect=pressure) as check, patch.object(
                storage, "maintain", side_effect=ValueError("busy cache")) as maintain, patch.object(
                storage.pwd, "getpwnam", side_effect=KeyError), patch("sys.stderr", new_callable=io.StringIO) as output:
            self.assertEqual(setup(SimpleNamespace(username="fixture", deploy_specs=[]), SimpleNamespace(dry_run=False)), 1)
            action.assert_not_called()
            maintain.assert_called_once()
            self.assertEqual(maintain.call_args.kwargs["names"], [])
            self.assertEqual(check.call_count, 2)
            self.assertIn("journalctl", output.getvalue())

    def test_successful_preflight_and_body_failure_do_not_trigger_cleanup(self):
        from contextlib import nullcontext
        from tests import real_storage_operation_guard
        with patch.object(storage, "storage_lock", side_effect=lambda **_: nullcontext()), patch.object(
                storage, "configured_preflight"), patch.object(storage, "maintain") as maintain:
            with self.assertRaisesRegex(RuntimeError, "setup step failed"):
                with real_storage_operation_guard("setup", ["/srv"], cleanup_accounts=[]):
                    raise RuntimeError("setup step failed")
            maintain.assert_not_called()

    def test_later_deployment_refusal_propagates_for_setup_failure_journaling(self):
        from contextlib import nullcontext
        from tests import real_storage_operation_guard
        @storage.guarded_setup
        def setup(config, args):
            raise storage.StoragePreflightError("deployment pressure", [])
        with patch.object(storage, "operation_guard", real_storage_operation_guard), patch.object(
                storage, "storage_lock", side_effect=lambda **_: nullcontext()), patch.object(
                storage, "configured_preflight"), patch.object(storage, "maintain") as maintain, patch.object(
                storage.pwd, "getpwnam", return_value=SimpleNamespace(pw_dir="/home/fixture")):
            with self.assertRaisesRegex(storage.StoragePreflightError, "deployment pressure"):
                setup(SimpleNamespace(username="fixture", deploy_specs=[]), SimpleNamespace(dry_run=False))
            maintain.assert_not_called()

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
             patch.object(storage, "accounts", return_value=[]), \
             patch.object(storage, "filesystem", return_value=dict(path="/", mount="/", free_bytes=10 * 1024**3,
                 free_inodes=100000, inodes_supported=True)), \
             patch.object(storage, "maintain", return_value=dict(accounts=[], shortfalls=[])), \
             patch("lib.build_storage_maintenance.maintain_roles", side_effect=roles), \
             patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(storage.main(), 1)

    def test_maintenance_capacity_caps_separate_memory_filesystems_and_deduplicates_mounts(self):
        def capacity(path):
            memory = path == "/tmp"
            return dict(path=path, mount="/tmp" if memory else "/", filesystem_type="tmpfs" if memory else "ext4",
                        total_bytes=512 * storage.MIB, total_inodes=10000,
                        free_bytes=100 * storage.MIB if memory else 2 * 1024**3,
                        free_inodes=1500 if memory else 20000, inodes_supported=True)
        with patch.object(storage, "filesystem", side_effect=capacity):
            result = storage._maintenance_capacity(["/", "/tmp", "/root/cache"], target=1536 * storage.MIB, min_inodes=10000)
        self.assertEqual(len(result["filesystems"]), 2)
        self.assertEqual(result["shortfalls"], [])
        self.assertEqual(result["requirements"]["/tmp"]["required_bytes"], 512 * storage.MIB // 10)
        self.assertEqual(result["requirements"]["/tmp"]["required_inodes"], 1000)
        self.assertEqual(result["requirements"]["/"]["required_bytes"], 1536 * storage.MIB)

    def test_cli_pressure_reports_system_only_byte_inode_and_observation_failures(self):
        for mode in ("bytes", "inodes", "unavailable"):
            with self.subTest(mode=mode):
                def capacity(path):
                    temporary = path == "/tmp"
                    if temporary and mode == "unavailable":
                        raise OSError("Temporary filesystem unavailable")
                    return dict(path=path, mount="/tmp" if temporary else "/", filesystem_type="ext4",
                                total_bytes=8 * 1024**3, total_inodes=100000,
                                free_bytes=0 if temporary and mode == "bytes" else 4 * 1024**3,
                                free_inodes=1 if temporary and mode == "inodes" else 20000, inodes_supported=True)
                with patch.object(sys, "argv", ["maintenance", "--pressure", "--apply"]), patch.object(
                        storage.os, "geteuid", return_value=0), patch.object(storage, "accounts", return_value=[]), patch.object(
                        storage, "storage_lock", return_value=nullcontext()), patch.object(storage, "filesystem", side_effect=capacity), patch.object(
                        storage, "maintain", return_value=dict(accounts=[], shortfalls=[])) as caches, patch(
                        "lib.storage_pressure.clean_system_storage", return_value=[]), patch(
                        "lib.storage_pressure.clean_build_versions", return_value=[]), patch("sys.stdout", new_callable=io.StringIO) as output:
                    self.assertEqual(storage.main(), 1)
                result = json.loads(output.getvalue())
                self.assertEqual(len(result["filesystems"]), 2)
                if mode == "unavailable":
                    self.assertEqual(result["filesystems"][1]["status"], "unavailable")
                else:
                    self.assertEqual(result["shortfalls"][0]["mount"], "/tmp")
                    self.assertGreater(result["shortfalls"][0][f"remaining_{mode}"], 0)
                    self.assertEqual(caches.call_args.kwargs["requirements"]["/tmp"]["required_inodes"], 10000)

    def test_payload_reclamation_rechecks_inodes_before_reporting_success(self):
        free_inodes = 1
        def capacity(path):
            return dict(path=path, mount="/", free_bytes=4 * 1024**3, free_inodes=free_inodes, inodes_supported=True)
        def roles(**_):
            nonlocal free_inodes
            free_inodes = 20000
            return []
        with patch.object(sys, "argv", ["maintenance", "--payloads", "--apply"]), patch.object(
                storage.os, "geteuid", return_value=0), patch.object(storage, "accounts", return_value=[]), patch.object(
                storage, "storage_lock", return_value=nullcontext()), patch.object(storage, "filesystem", side_effect=capacity), patch.object(
                storage, "maintain", return_value=dict(accounts=[], shortfalls=[dict(path="/", remaining_inodes=9999)])), patch(
                "lib.build_storage_maintenance.maintain_roles", side_effect=roles), patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(storage.main(), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["shortfalls"], [])
        self.assertEqual(result["filesystems"][0]["free_inodes"], 20000)

    def test_pressure_observes_separate_build_runtime_mount(self):
        account = SimpleNamespace(pw_name="build-fixture", pw_dir=f"{storage.BUILD_HOMES}/build-fixture")
        def capacity(path):
            runtime = path == f"{account.pw_dir}/.nvm/versions/node"
            return dict(path=path, mount="/runtimes" if runtime else "/", free_bytes=0 if runtime else 4 * 1024**3,
                        free_inodes=20000, inodes_supported=True)
        with patch.object(sys, "argv", ["maintenance", "--pressure"]), patch.object(
                storage.os, "geteuid", return_value=0), patch.object(storage, "accounts", return_value=[account]), patch.object(
                storage, "storage_lock", return_value=nullcontext()), patch.object(storage, "filesystem", side_effect=capacity), patch.object(
                storage, "maintain", return_value=dict(accounts=[], shortfalls=[])), patch(
                "lib.storage_pressure.clean_system_storage", return_value=[]), patch(
                "lib.storage_pressure.clean_build_versions", return_value=[]) as versions, patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(storage.main(), 1)
        self.assertEqual(set(versions.call_args.kwargs["requirements"]), {"/", "/runtimes"})
        self.assertEqual(json.loads(output.getvalue())["shortfalls"][0]["mount"], "/runtimes")

    def test_daily_job_enables_pressure_recovery_with_existing_payload_retention(self):
        from common.storage_maintenance_steps import configure_storage_maintenance
        with patch("common.storage_maintenance_steps.configure_maintenance_timer", return_value=True) as timer:
            configure_storage_maintenance(SimpleNamespace(username="fixture"))
        options = timer.call_args.kwargs
        self.assertEqual(options["schedule"], "daily")
        self.assertEqual(options["script_args"], ("--apply", "--account", "fixture", "--payloads", "--pressure"))

    def test_extended_build_caches_follow_their_own_filesystem_pressure(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "build-fixture"
            for relative in (".npm/_logs", "go/pkg/mod"):
                cache = home / relative
                cache.mkdir(parents=True)
                (cache / "generated").write_text("reproducible")
            for current, _dirs, _files in os.walk(home):
                Path(current).chmod(0o700)
            account = SimpleNamespace(pw_name="build-fixture", pw_uid=os.geteuid(), pw_dir=str(home))
            def capacity(path):
                separate = path == str(home / "go/pkg/mod")
                return dict(path=path, mount="/modules" if separate else "/", free_bytes=0 if separate else 2)
            with patch.object(storage, "BUILD_HOMES", directory), patch.object(storage, "accounts", return_value=[account]), patch.object(
                    storage, "activity", return_value=None), patch.object(storage, "filesystem", side_effect=capacity), patch.object(
                    storage, "run_streamed") as worker:
                result = storage.maintain(names=[], apply=False, budget=0, target=1)
            worker.assert_not_called()
            self.assertEqual([row["category"] for row in result["accounts"][0]["candidates"] if row["status"] == "preview"],
                             ["go-modules"])
            self.assertTrue((home / ".npm/_logs/generated").exists())

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
        self.assertEqual([row["mount"] for row in failure.exception.failures], ["/tmp", "/data"])

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
