"""Tests for common.service_tools.user_cache_maintenance."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from common.service_tools import user_cache_maintenance
from lib.agent_storage import CodexCleanupResult


class TestUserCacheHelpers(unittest.TestCase):
    def setUp(self):
        patcher = patch("common.service_tools.user_cache_maintenance.process_uses_path", return_value=False)
        self.path_patcher = patcher
        patcher.start()
        self.addCleanup(patcher.stop)

    def _context(self, home: str) -> user_cache_maintenance.UserContext:
        return user_cache_maintenance.UserContext("agent", home, os.getuid())

    def test_cache_usage_counts_files_without_following_links(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as outside:
            cache_dir = os.path.join(home, "cache")
            os.mkdir(cache_dir)
            with open(os.path.join(cache_dir, "data"), "w", encoding="utf-8") as handle:
                handle.write("data")
            with open(os.path.join(outside, "large"), "w", encoding="utf-8") as handle:
                handle.write("not part of the cache")
            os.symlink(outside, os.path.join(cache_dir, "outside"))

            usage = user_cache_maintenance.cache_usage(cache_dir)

            self.assertEqual(usage.size_bytes, 4)
            self.assertIsNotNone(usage.newest_mtime)

    def test_cache_usage_handles_deep_directory_trees(self):
        with tempfile.TemporaryDirectory() as home:
            current_dir = home
            for _ in range(150):
                current_dir = os.path.join(current_dir, "d")
                os.mkdir(current_dir)
            with open(os.path.join(current_dir, "data"), "w", encoding="utf-8") as handle:
                handle.write("data")

            original_recursion_limit = sys.getrecursionlimit()
            try:
                sys.setrecursionlimit(100)
                usage = user_cache_maintenance.cache_usage(home)
            finally:
                sys.setrecursionlimit(original_recursion_limit)

            self.assertEqual(usage.size_bytes, 4)
            self.assertIsNotNone(usage.newest_mtime)

    def test_managed_path_must_remain_inside_home(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as outside:
            context = self._context(home)

            self.assertTrue(
                user_cache_maintenance.is_safe_managed_path(
                    context,
                    os.path.join(home, ".cache", "tool"),
                    "tool",
                )
            )
            self.assertFalse(
                user_cache_maintenance.is_safe_managed_path(
                    context,
                    outside,
                    "tool",
                )
            )
            self.assertFalse(
                user_cache_maintenance.is_safe_managed_path(context, home, "tool")
            )

    def test_managed_path_rejects_an_intermediate_symlink(self):
        with tempfile.TemporaryDirectory() as home:
            context = self._context(home)
            real_cache = os.path.join(home, "real-cache")
            linked_cache = os.path.join(home, ".cache")
            os.mkdir(real_cache)
            os.symlink(real_cache, linked_cache)

            self.assertFalse(
                user_cache_maintenance.is_safe_managed_path(
                    context,
                    os.path.join(linked_cache, "tool"),
                    "tool",
                )
            )

    @patch("common.service_tools.user_cache_maintenance.run_captured")
    def test_tool_command_loads_home_scoped_nvm(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess([], 0, "", "")
        context = self._context("/home/agent")

        with patch.dict(os.environ, {"PATH": "/home/loren/.local/bin:/usr/bin"}, clear=False):
            user_cache_maintenance.run_tool_command(
                context,
                ["npm", "cache", "verify"],
                load_nvm=True,
            )

        command = mock_run.call_args.args[0]
        self.assertEqual(command[:2], ["/bin/bash", "-c"])
        self.assertIn("export NVM_DIR=/home/agent/.nvm", command[2])
        self.assertIn('"$NVM_DIR/nvm.sh"', command[2])
        self.assertIn("exec npm cache verify", command[2])
        self.assertEqual(mock_run.call_args.kwargs["cwd"], "/home/agent")
        environment = mock_run.call_args.kwargs["env"]
        self.assertEqual(environment["HOME"], "/home/agent")
        self.assertEqual(environment["PWD"], "/home/agent")
        self.assertNotIn("/home/loren", environment["PATH"])
        self.assertEqual(environment["CODEX_HOME"], "/home/agent/.codex")

    @patch("common.service_tools.user_cache_maintenance.run_tool_command")
    def test_query_cache_path_uses_first_available_tool(self, mock_run):
        mock_run.side_effect = [
            subprocess.CompletedProcess([], 77, "", ""),
            subprocess.CompletedProcess([], 0, "/home/agent/.cache/pip\n", ""),
        ]

        path, executable, failure = user_cache_maintenance.query_cache_path(
            self._context("/home/agent"),
            (["pip3", "cache", "dir"], ["pip", "cache", "dir"]),
            "pip cache path query",
        )

        self.assertEqual(path, "/home/agent/.cache/pip")
        self.assertEqual(executable, "pip")
        self.assertIsNone(failure)

    def test_active_tool_detection_reads_process_name_only(self):
        with tempfile.TemporaryDirectory() as proc_root:
            process_dir = os.path.join(proc_root, "123")
            os.mkdir(process_dir)
            with open(os.path.join(process_dir, "comm"), "w", encoding="utf-8") as handle:
                handle.write("codex-x86_64\n")

            self.assertTrue(
                user_cache_maintenance.tool_is_active(("codex",), proc_root=proc_root)
            )
            self.assertFalse(
                user_cache_maintenance.tool_is_active(("opencode",), proc_root=proc_root)
            )

            with open(os.path.join(process_dir, "comm"), "w", encoding="utf-8") as handle:
                handle.write("npm exec t3\n")
            self.assertTrue(
                user_cache_maintenance.tool_is_active(("npm",), proc_root=proc_root)
            )

    def test_process_path_detection_checks_cwd_and_individual_arguments(self):
        # This test exercises the real scanner against a synthetic proc tree.
        self.path_patcher.stop()
        with tempfile.TemporaryDirectory() as proc_root, tempfile.TemporaryDirectory() as home:
            npx_path = os.path.join(home, ".npm", "_npx")
            process_dir = os.path.join(proc_root, "123")
            os.mkdir(process_dir)
            os.symlink(npx_path, os.path.join(process_dir, "cwd"))
            with open(os.path.join(process_dir, "cmdline"), "wb") as handle:
                handle.write(b"node\0")

            self.assertTrue(
                user_cache_maintenance.process_uses_path(npx_path, proc_root=proc_root)
            )

            os.unlink(os.path.join(process_dir, "cwd"))
            os.symlink(home, os.path.join(process_dir, "cwd"))
            with open(os.path.join(process_dir, "cmdline"), "wb") as handle:
                handle.write(os.fsencode(f"{npx_path}/package/bin.js") + b"\0--serve\0")
            self.assertTrue(
                user_cache_maintenance.process_uses_path(npx_path, proc_root=proc_root)
            )

            with patch.object(user_cache_maintenance.os, "readlink", side_effect=PermissionError):
                # A protected cwd does not conceal a readable path argument.
                self.assertTrue(
                    user_cache_maintenance.process_uses_path(npx_path, proc_root=proc_root)
                )
                with open(os.path.join(process_dir, "cmdline"), "wb") as handle:
                    handle.write(b"/usr/bin/ssh-agent\0")
                self.assertFalse(
                    user_cache_maintenance.process_uses_path(npx_path, proc_root=proc_root)
                )

    @patch("common.service_tools.user_cache_maintenance.tool_is_active", return_value=False)
    def test_managed_cache_removes_only_allowlisted_directory(self, _active):
        with tempfile.TemporaryDirectory() as home:
            context = self._context(home)
            cache_dir = os.path.join(home, ".cache", "opencode")
            state_dir = os.path.join(home, ".local", "share", "opencode")
            os.makedirs(cache_dir)
            os.makedirs(state_dir)
            with open(os.path.join(cache_dir, "cache"), "w", encoding="utf-8") as handle:
                handle.write("cache")
            with open(os.path.join(state_dir, "auth.json"), "w", encoding="utf-8") as handle:
                handle.write("preserve")

            failures = user_cache_maintenance.cleanup_managed_directory(
                context,
                label="OpenCode",
                path=cache_dir,
                max_bytes=1,
                max_age_days=90,
                process_names=("opencode",),
                dry_run=False,
            )

            self.assertEqual(failures, [])
            self.assertFalse(os.path.exists(cache_dir))
            self.assertTrue(os.path.exists(os.path.join(state_dir, "auth.json")))

    @patch("common.service_tools.user_cache_maintenance.tool_is_active", return_value=True)
    def test_managed_cache_defers_while_tool_is_active(self, _active):
        with tempfile.TemporaryDirectory() as home:
            context = self._context(home)
            cache_dir = os.path.join(home, ".codex", "cache")
            os.makedirs(cache_dir)
            with open(os.path.join(cache_dir, "entry"), "w", encoding="utf-8") as handle:
                handle.write("cache")

            failures = user_cache_maintenance.cleanup_managed_directory(
                context,
                label="Codex",
                path=cache_dir,
                max_bytes=1,
                max_age_days=90,
                process_names=("codex",),
                dry_run=False,
            )

            self.assertEqual(failures, [])
            self.assertTrue(os.path.exists(cache_dir))

    @patch("common.service_tools.user_cache_maintenance.tool_is_active", return_value=False)
    def test_managed_cache_dry_run_preserves_directory(self, _active):
        with tempfile.TemporaryDirectory() as home:
            context = self._context(home)
            cache_dir = os.path.join(home, ".cache", "opencode")
            os.makedirs(cache_dir)
            with open(os.path.join(cache_dir, "entry"), "w", encoding="utf-8") as handle:
                handle.write("cache")

            user_cache_maintenance.cleanup_managed_directory(
                context,
                label="OpenCode",
                path=cache_dir,
                max_bytes=1,
                max_age_days=90,
                process_names=("opencode",),
                dry_run=True,
            )

            self.assertTrue(os.path.exists(cache_dir))

    @patch("common.service_tools.user_cache_maintenance.tool_is_active", return_value=False)
    def test_stale_temp_cleanup_preserves_fresh_entries_and_links(self, _active):
        with tempfile.TemporaryDirectory() as home:
            context = self._context(home)
            tmp_dir = os.path.join(home, ".codex", "tmp")
            os.makedirs(tmp_dir)
            old_file = os.path.join(tmp_dir, "old")
            fresh_file = os.path.join(tmp_dir, "fresh")
            linked_file = os.path.join(tmp_dir, "linked")
            for path in (old_file, fresh_file):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("x")
            os.symlink(old_file, linked_file)
            old_time = time.time() - (8 * 24 * 60 * 60)
            os.utime(old_file, (old_time, old_time))

            failures = user_cache_maintenance.cleanup_stale_children(
                context,
                label="Codex temporary files",
                path=tmp_dir,
                max_age_days=7,
                process_names=("codex",),
                dry_run=False,
            )

            self.assertEqual(failures, [])
            self.assertFalse(os.path.exists(old_file))
            self.assertTrue(os.path.exists(fresh_file))
            self.assertTrue(os.path.islink(linked_file))

    def test_t3_log_cleanup_prunes_only_numbered_rotations(self):
        with tempfile.TemporaryDirectory() as home:
            context = self._context(home)
            log_dir = os.path.join(home, ".t3", "userdata", "logs", "provider")
            os.makedirs(log_dir)
            current = os.path.join(log_dir, "events.session.log")
            old_rotation = os.path.join(log_dir, "events.session.log.1")
            newer_rotation = os.path.join(log_dir, "events.session.log.2")
            unrelated = os.path.join(log_dir, "notes.txt.1")
            for path in (current, old_rotation, newer_rotation, unrelated):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("12345")
            old_time = time.time() - (20 * 24 * 60 * 60)
            os.utime(old_rotation, (old_time, old_time))

            failures = user_cache_maintenance.cleanup_t3_rotated_logs(
                context,
                dry_run=False,
                max_bytes=5,
                max_age_days=14,
            )

            self.assertEqual(failures, [])
            self.assertTrue(os.path.exists(current))
            self.assertFalse(os.path.exists(old_rotation))
            self.assertTrue(os.path.exists(newer_rotation))
            self.assertTrue(os.path.exists(unrelated))

    def test_t3_log_cleanup_preserves_links_and_honors_dry_run(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as outside:
            context = self._context(home)
            log_root = os.path.join(home, ".t3", "userdata", "logs")
            os.makedirs(log_root)
            rotation = os.path.join(log_root, "server.trace.ndjson.1")
            with open(rotation, "w", encoding="utf-8") as handle:
                handle.write("12345")
            linked_rotation = os.path.join(log_root, "linked.log.1")
            os.symlink(os.path.join(outside, "target"), linked_rotation)

            failures = user_cache_maintenance.cleanup_t3_rotated_logs(
                context,
                dry_run=True,
                max_bytes=0,
                max_age_days=14,
            )

            self.assertEqual(failures, [])
            self.assertTrue(os.path.exists(rotation))
            self.assertTrue(os.path.islink(linked_rotation))

    def test_backup_policy_reports_dry_run_and_propagates_failures(self):
        context = self._context("/fixture-home")
        for result, expected_event in (
            (CodexCleanupResult(found=("active", "snapshot"), retained=("active",),
                                active=("active",), selected=("snapshot",)),
             "Would prune Codex update backups"),
            (CodexCleanupResult(errors=("unsafe lock",)), "Failed to reconcile Codex update backups"),
        ):
            with self.subTest(event=expected_event), patch.object(
                user_cache_maintenance, "reconcile_codex_update_backups", return_value=result,
            ) as reconcile, patch.object(user_cache_maintenance, "log_event") as log:
                failures = user_cache_maintenance.cleanup_codex_update_backups(context, dry_run=True)
            self.assertEqual(failures, list(result.errors))
            reconcile.assert_called_once_with(context.home, context.uid, dry_run=True)
            self.assertIn(expected_event, [call.args[1] for call in log.call_args_list])
            inventory = next(call for call in log.call_args_list if call.args[1] == "Codex update backup inventory")
            self.assertEqual(inventory.kwargs["known_active_count"], len(result.active))
            self.assertFalse(inventory.kwargs["cleanup_deferred"])
            self.assertNotIn("active_count", inventory.kwargs)

    def test_codex_policies_log_safety_deferrals_without_failing_maintenance(self):
        context = self._context("/fixture-home")
        result = CodexCleanupResult(
            found=("snapshot",), retained=("snapshot",),
            errors=("cannot inventory user process executable (pid 123: PermissionError)",),
            deferred=True,
        )
        for policy, reconcile in (
            (user_cache_maintenance.cleanup_codex_update_backups, "reconcile_codex_update_backups"),
            (user_cache_maintenance.cleanup_codex_standalone_releases, "reconcile_codex_standalone_releases"),
        ):
            for dry_run in (True, False):
                with self.subTest(policy=policy.__name__, dry_run=dry_run), patch.object(
                    user_cache_maintenance, reconcile, return_value=result,
                ), patch.object(user_cache_maintenance, "log_event") as log:
                    self.assertEqual(policy(context, dry_run=dry_run), [])
                event = next(call for call in log.call_args_list if "retained for retry" in call.args[1])
                self.assertEqual(event.kwargs["level"], user_cache_maintenance.INFO)
                self.assertIn("pid 123", event.kwargs["error"])
                inventory = next(call for call in log.call_args_list if "known_active_count" in call.kwargs)
                self.assertEqual(inventory.kwargs["known_active_count"], 0)
                self.assertTrue(inventory.kwargs["cleanup_deferred"])
                self.assertNotIn("active_count", inventory.kwargs)


class TestUserCachePolicies(unittest.TestCase):
    def setUp(self):
        self.context = user_cache_maintenance.UserContext("agent", "/home/agent", 1000)
        for name in ("storage_pressure", "tool_is_active", "process_uses_path"):
            patcher = patch(f"common.service_tools.user_cache_maintenance.{name}", return_value=False)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_podman_policy_maps_safe_statuses_and_logs_only_fixed_fields(self):
        helper = types.ModuleType("lib.podman_cleanup")
        helper.clean_podman_images = unittest.mock.Mock(return_value={
            "status": "preview", "candidate_count": 3, "retention_days": 7,
            "builder_candidate_count": 2, "builders_removed": 1,
            "free_bytes_before": 1234, "free_bytes_after": 5678,
            "invalid_builder_count": "4", "bool_builder_count": True,
            "image_metadata": "must not be logged", "output": "private output",
        })
        with patch.dict(sys.modules, {"lib.podman_cleanup": helper}), patch.object(
            user_cache_maintenance, "log_event",
        ) as log:
            self.assertEqual(
                user_cache_maintenance.cleanup_podman_images(self.context, dry_run=True),
                [],
            )
        helper.clean_podman_images.assert_called_once_with(apply=False)
        self.assertEqual(log.call_args.kwargs, {
            "status": "preview", "candidate_count": 3, "retention_days": 7,
            "builder_candidate_count": 2, "builders_removed": 1,
            "free_bytes_before": 1234, "free_bytes_after": 5678,
        })

        with patch.dict(sys.modules, {"lib.podman_cleanup": helper}):
            for status in ("cleaned", "absent", "deferred_active"):
                with self.subTest(status=status):
                    helper.clean_podman_images.return_value = {"status": status}
                    with patch.object(user_cache_maintenance, "log_event"):
                        self.assertEqual(
                            user_cache_maintenance.cleanup_podman_images(self.context, dry_run=False),
                            [],
                        )
                    helper.clean_podman_images.assert_called_with(apply=True)

    def test_podman_unknown_and_failed_statuses_surface_as_failures(self):
        helper = types.ModuleType("lib.podman_cleanup")
        for status, expected in (
            ("deferred_unknown", "Podman image cleanup: deferred_unknown"),
            ("failed", "Podman image cleanup: failed"),
            (
                "uncertain",
                "Podman image cleanup: outcome uncertain; inspect Podman state before retrying",
            ),
            ("future_status", "Podman image cleanup: unrecognized result status"),
        ):
            with self.subTest(status=status):
                helper.clean_podman_images = unittest.mock.Mock(return_value={"status": status})
                with patch.dict(sys.modules, {"lib.podman_cleanup": helper}), patch.object(
                    user_cache_maintenance, "log_event",
                ):
                    self.assertEqual(
                        user_cache_maintenance.cleanup_podman_images(self.context, dry_run=False),
                        [expected],
                    )

    def test_full_maintenance_aggregates_podman_and_other_policy_failures(self):
        with patch.object(user_cache_maintenance, "cleanup_agent_storage", return_value=[]), patch.object(
            user_cache_maintenance, "cleanup_podman_images", return_value=["Podman failed"],
        ) as podman, patch.object(
            user_cache_maintenance, "cleanup_electron_downloads", return_value=[],
        ), patch.object(user_cache_maintenance, "cleanup_uv_cache", return_value=[]), patch.object(
            user_cache_maintenance, "cleanup_npm_cache", return_value=["npm failed"],
        ), patch.object(user_cache_maintenance, "cleanup_pip_cache", return_value=[]), patch.object(
            user_cache_maintenance, "cleanup_go_cache", return_value=[],
        ), patch.object(user_cache_maintenance, "cleanup_agent_caches", return_value=[]):
            self.assertEqual(
                user_cache_maintenance.run_user_cache_maintenance(self.context, dry_run=True),
                ["Podman failed", "npm failed"],
            )
        podman.assert_called_once_with(self.context, dry_run=True)

    @patch("common.service_tools.user_cache_maintenance.run_cleanup_command", return_value=None)
    @patch("common.service_tools.user_cache_maintenance.process_uses_path", return_value=False)
    @patch("common.service_tools.user_cache_maintenance.cleanup_managed_directory", return_value=[])
    @patch("common.service_tools.user_cache_maintenance.inventory_cache")
    @patch("common.service_tools.user_cache_maintenance.query_cache_path")
    def test_npm_verifies_and_cleans_only_when_oversized(
        self,
        mock_query,
        mock_inventory,
        mock_directory_cleanup,
        _path_active,
        mock_cleanup,
    ):
        mock_query.return_value = ("/home/agent/.npm", "npm", None)
        oversized = user_cache_maintenance.CacheUsage(
            user_cache_maintenance.NPM_CACHE_MAX_BYTES + 1,
            time.time(),
        )
        mock_inventory.side_effect = [(oversized, None), (oversized, None)]

        failures = user_cache_maintenance.cleanup_npm_cache(self.context, dry_run=False)

        self.assertEqual(failures, [])
        mock_directory_cleanup.assert_called_once_with(
            self.context,
            label="npm npx workspaces",
            path="/home/agent/.npm/_npx",
            max_bytes=user_cache_maintenance.NPM_NPX_CACHE_MAX_BYTES,
            max_age_days=user_cache_maintenance.STALE_NPX_CACHE_MAX_AGE_DAYS,
            process_names=("npm", "npx"),
            dry_run=False,
        )
        self.assertEqual(mock_cleanup.call_count, 2)
        self.assertEqual(mock_cleanup.call_args_list[0].args[1], ["npm", "cache", "verify"])
        self.assertEqual(
            mock_cleanup.call_args_list[1].args[1],
            ["npm", "cache", "clean", "--force"],
        )

    @patch("common.service_tools.user_cache_maintenance.run_cleanup_command", return_value=None)
    @patch("common.service_tools.user_cache_maintenance.process_uses_path", return_value=False)
    @patch("common.service_tools.user_cache_maintenance.cleanup_managed_directory", return_value=[])
    @patch("common.service_tools.user_cache_maintenance.inventory_cache")
    @patch("common.service_tools.user_cache_maintenance.query_cache_path")
    def test_npm_remeasures_after_verify_before_forced_cleanup(
        self,
        mock_query,
        mock_inventory,
        _directory_cleanup,
        _path_active,
        mock_cleanup,
    ):
        mock_query.return_value = ("/home/agent/.npm", "npm", None)
        mock_inventory.side_effect = [
            (
                user_cache_maintenance.CacheUsage(
                    user_cache_maintenance.NPM_CACHE_MAX_BYTES + 1,
                    time.time(),
                ),
                None,
            ),
            (user_cache_maintenance.CacheUsage(10, time.time()), None),
        ]

        failures = user_cache_maintenance.cleanup_npm_cache(
            self.context,
            dry_run=False,
        )

        self.assertEqual(failures, [])
        mock_cleanup.assert_called_once_with(
            self.context,
            ["npm", "cache", "verify"],
            "npm cache verification and garbage collection",
            dry_run=False,
            load_nvm=True,
        )

    @patch("common.service_tools.user_cache_maintenance.run_cleanup_command")
    @patch("common.service_tools.user_cache_maintenance.inventory_cache")
    @patch("common.service_tools.user_cache_maintenance.query_cache_path")
    def test_pip_below_limit_is_inventory_only(self, mock_query, mock_inventory, mock_cleanup):
        mock_query.return_value = ("/home/agent/.cache/pip", "pip3", None)
        mock_inventory.return_value = (
            user_cache_maintenance.CacheUsage(10, time.time()),
            None,
        )

        failures = user_cache_maintenance.cleanup_pip_cache(self.context, dry_run=False)

        self.assertEqual(failures, [])
        mock_cleanup.assert_not_called()

    @patch("common.service_tools.user_cache_maintenance.run_cleanup_command", return_value=None)
    @patch("common.service_tools.user_cache_maintenance.inventory_cache")
    @patch("common.service_tools.user_cache_maintenance.query_cache_path")
    def test_uv_uses_supported_prune_command(self, mock_query, mock_inventory, mock_cleanup):
        mock_query.return_value = ("/home/agent/.cache/uv", "uv", None)
        mock_inventory.return_value = (
            user_cache_maintenance.CacheUsage(10, time.time()),
            None,
        )

        failures = user_cache_maintenance.cleanup_uv_cache(self.context, dry_run=False)

        self.assertEqual(failures, [])
        mock_cleanup.assert_called_once_with(
            self.context,
            ["uv", "cache", "prune"],
            "uv cache prune",
            dry_run=False,
        )

    @patch("common.service_tools.user_cache_maintenance.run_cleanup_command")
    @patch("common.service_tools.user_cache_maintenance.inventory_cache")
    @patch("common.service_tools.user_cache_maintenance.query_cache_path")
    def test_tool_cleanup_stops_when_reported_path_is_unsafe(
        self,
        mock_query,
        mock_inventory,
        mock_cleanup,
    ):
        mock_query.return_value = ("/var/cache/uv", "uv", None)
        mock_inventory.return_value = (None, None)

        failures = user_cache_maintenance.cleanup_uv_cache(
            self.context,
            dry_run=False,
        )

        self.assertEqual(failures, [])
        mock_cleanup.assert_not_called()

    @patch("common.service_tools.user_cache_maintenance.clean_go_build_cache")
    def test_go_build_uses_shared_guard_and_existing_pressure_policy(self, mock_cleanup):
        mock_cleanup.return_value = {"status": "preview", "path": "/fixture/cache",
                                     "activity": None, "before": None, "after": None, "errors": []}
        failures = user_cache_maintenance.cleanup_go_cache(
            self.context,
            cache_name="Go build cache",
            go_env_name="GOCACHE",
            max_bytes=100,
            clean_args=["-cache", "-testcache", "-fuzzcache"],
            dry_run=False,
        )
        self.assertEqual(failures, [])
        self.assertTrue(mock_cleanup.call_args.kwargs["apply"])
        self.assertEqual(mock_cleanup.call_args.kwargs["expected_home"], self.context.home)
        self.assertIn("/usr/local/go/bin", mock_cleanup.call_args.kwargs["search_path"].split(os.pathsep))
        decision = mock_cleanup.call_args.kwargs["needs_cleanup"]
        self.assertTrue(decision("/fixture/cache", 101))
        self.assertFalse(decision("/fixture/cache", 99))
        size = user_cache_maintenance.USER_CACHE_PRESSURE_MIN_BYTES
        user_cache_maintenance.cleanup_go_cache(
            self.context, cache_name="Go build", go_env_name="GOCACHE", max_bytes=size * 2,
            clean_args=["-cache"], dry_run=True,
        )
        decision = mock_cleanup.call_args.kwargs["needs_cleanup"]
        self.assertFalse(decision("/fixture/cache", size))
        with patch.object(user_cache_maintenance, "storage_pressure", return_value=True):
            self.assertTrue(decision("/fixture/cache", size))
            self.assertFalse(decision("/fixture/cache", size - 1))

    @patch("common.service_tools.user_cache_maintenance.defer_active_cache", return_value=True)
    @patch("common.service_tools.user_cache_maintenance.query_cache_path")
    @patch("common.service_tools.user_cache_maintenance.clean_go_build_cache")
    def test_go_module_cache_keeps_the_conservative_guard(self, shared, query, guard):
        query.return_value = ("/fixture/go/pkg/mod", "go", None)
        self.assertEqual(user_cache_maintenance.cleanup_go_cache(
            self.context, cache_name="Go modules", go_env_name="GOMODCACHE", max_bytes=1,
            clean_args=["-modcache"], dry_run=False,
        ), [])
        guard.assert_called_once_with("/fixture/go/pkg/mod", ("go", "compile", "link", "gopls"))
        shared.assert_not_called()

    @patch("common.service_tools.user_cache_maintenance.clean_go_build_cache")
    def test_go_build_preview_and_real_errors_are_preserved(self, cleanup):
        cleanup.return_value = {"status": "failed", "path": None, "activity": None,
                                "before": None, "after": None, "errors": ["cache validation failed"]}
        self.assertEqual(user_cache_maintenance.cleanup_go_cache(
            self.context, cache_name="Go build", go_env_name="GOCACHE", max_bytes=1,
            clean_args=["-cache"], dry_run=True,
        ), ["cache validation failed"])
        self.assertFalse(cleanup.call_args.kwargs["apply"])

    @patch(
        "common.service_tools.user_cache_maintenance.cleanup_codex_standalone_releases",
        return_value=[],
    )
    @patch("common.service_tools.user_cache_maintenance.cleanup_stale_children", return_value=[])
    @patch("common.service_tools.user_cache_maintenance.cleanup_managed_directory", return_value=[])
    def test_agent_cache_policy_never_targets_unmanaged_persistent_state(
        self,
        mock_directory_cleanup,
        mock_stale_cleanup,
        mock_release_cleanup,
    ):
        with patch.dict(
            os.environ,
            {
                "XDG_CACHE_HOME": "/home/loren/.cache",
                "CODEX_HOME": "/home/loren/.codex",
            },
            clear=False,
        ):
            user_cache_maintenance.cleanup_agent_caches(self.context, dry_run=False)

        managed_paths = [call.kwargs["path"] for call in mock_directory_cleanup.call_args_list]
        self.assertEqual(
            managed_paths,
            ["/home/agent/.cache/opencode", "/home/agent/.codex/cache"],
        )
        self.assertEqual(
            mock_stale_cleanup.call_args.kwargs["path"],
            "/home/agent/.codex/tmp",
        )
        mock_release_cleanup.assert_not_called()
        self.assertFalse(any(".local/share/opencode" in path for path in managed_paths))


class TestUserCacheMain(unittest.TestCase):
    def test_safety_deferral_does_not_fail_setup_or_daily_job_but_other_failures_do(self):
        context = user_cache_maintenance.UserContext("agent", "/fixture-home", 1000)
        result = CodexCleanupResult(errors=("process inventory unavailable",), deferred=True)
        for args in ([], ["--agent-storage-only"], ["--agent-storage-only", "--dry-run"]):
            for other_errors in ([], ["T3 log removal failed"]):
                with self.subTest(args=args, errors=other_errors), patch.object(
                    user_cache_maintenance, "resolve_user_context", return_value=context,
                ), patch.object(
                    user_cache_maintenance, "run_user_cache_maintenance",
                    side_effect=user_cache_maintenance.cleanup_agent_storage,
                ), patch.object(
                    user_cache_maintenance, "reconcile_codex_standalone_releases",
                    return_value=CodexCleanupResult(),
                ), patch.object(
                    user_cache_maintenance, "reconcile_codex_update_backups", return_value=result,
                ), patch.object(
                    user_cache_maintenance, "cleanup_t3_rotated_logs", return_value=other_errors,
                ) as logs, patch.object(
                    user_cache_maintenance, "cleanup_t3_runtimes", return_value=[],
                ) as runtimes:
                    self.assertEqual(user_cache_maintenance.main(args), int(bool(other_errors)))
                logs.assert_called_once_with(context, dry_run="--dry-run" in args)
                runtimes.assert_called_once_with(context, dry_run="--dry-run" in args)

    @patch("common.service_tools.user_cache_maintenance.run_user_cache_maintenance")
    @patch("common.service_tools.user_cache_maintenance.resolve_user_context")
    def test_dry_run_is_forwarded_to_cache_policies(self, mock_context, mock_run):
        context = user_cache_maintenance.UserContext("agent", "/home/agent", 1000)
        mock_context.return_value = context
        mock_run.return_value = []

        result = user_cache_maintenance.main(["--dry-run"])

        self.assertEqual(result, 0)
        mock_run.assert_called_once_with(context, dry_run=True)

    @patch("common.service_tools.user_cache_maintenance.run_user_cache_maintenance")
    @patch("common.service_tools.user_cache_maintenance.resolve_user_context")
    def test_root_account_is_skipped(self, mock_context, mock_run):
        mock_context.return_value = user_cache_maintenance.UserContext("root", "/root", 0)

        result = user_cache_maintenance.main([])

        self.assertEqual(result, 0)
        mock_run.assert_not_called()

    @patch("common.service_tools.user_cache_maintenance.run_user_cache_maintenance")
    @patch("common.service_tools.user_cache_maintenance.resolve_user_context")
    def test_failures_return_nonzero(self, mock_context, mock_run):
        mock_context.return_value = user_cache_maintenance.UserContext(
            "agent",
            "/home/agent",
            1000,
        )
        mock_run.return_value = ["npm cache: failed"]

        result = user_cache_maintenance.main([])

        self.assertEqual(result, 1)


if __name__ == "__main__":
    unittest.main()
