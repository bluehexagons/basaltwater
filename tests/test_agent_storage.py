"""Tests for conservative coding-agent installation retention."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from common.agent_steps import reconcile_agent_storage
from common.service_tools import user_cache_maintenance
from lib.agent_storage import (
    _CodexProcessInventoryUnavailable,
    cleanup_codex_standalone_releases,
    cleanup_t3_rotated_logs,
)
from lib.config import SetupConfig
from lib.cleanup_paths import remove_checked_path
from lib.plugin_registry import resolve_custom_step
from plugins.common import extend_agent_steps


class TestAgentStorage(unittest.TestCase):
    def setUp(self) -> None:
        self.proc = self.enterContext(tempfile.TemporaryDirectory())

    def _create_codex_release(
        self,
        home: str,
        version: str,
        *,
        mtime: float,
    ) -> str:
        target = "x86_64-unknown-linux-musl"
        release = os.path.join(
            home,
            ".codex",
            "packages",
            "standalone",
            "releases",
            f"{version}-{target}",
        )
        executable = os.path.join(release, "bin", "codex")
        os.makedirs(os.path.dirname(executable), exist_ok=True)
        with open(executable, "w", encoding="utf-8") as executable_file:
            executable_file.write("#!/bin/sh\n")
        os.chmod(executable, 0o755)
        with open(
            os.path.join(release, "codex-package.json"),
            "w",
            encoding="utf-8",
        ) as manifest_file:
            json.dump(
                {
                    "layoutVersion": 1,
                    "version": version,
                    "target": target,
                    "variant": "codex",
                    "entrypoint": "bin/codex",
                },
                manifest_file,
            )
        os.utime(release, (mtime, mtime))
        return release

    def _set_current(self, home: str, release: str) -> None:
        current = os.path.join(home, ".codex", "packages", "standalone", "current")
        os.symlink(release, current)

    def _set_launcher(self, home: str, release: str) -> str:
        launcher = os.path.join(home, ".local", "bin", "codex")
        os.makedirs(os.path.dirname(launcher), exist_ok=True)
        if os.path.lexists(launcher):
            os.unlink(launcher)
        os.symlink(os.path.join(release, "bin", "codex"), launcher)
        return launcher

    def test_launcher_selection_preserves_old_release_and_separate_rollback(self) -> None:
        for dry_run in (True, False):
            with self.subTest(dry_run=dry_run), tempfile.TemporaryDirectory() as home:
                chosen, obsolete, rollback, current = [
                    self._create_codex_release(home, f"1.{index}.0", mtime=index)
                    for index in range(4)
                ]
                self._set_current(home, current)
                launcher = self._set_launcher(home, chosen)
                result = cleanup_codex_standalone_releases(
                    home, os.getuid(), dry_run=dry_run, proc_root=self.proc,
                )
                self.assertFalse(result.errors)
                self.assertEqual(result.selected, (os.path.basename(obsolete),))
                self.assertEqual(set(result.retained), {
                    os.path.basename(path) for path in (chosen, rollback, current)
                })
                self.assertTrue(os.path.isfile(launcher))
                self.assertEqual(os.path.exists(obsolete), dry_run)

    def test_rechecks_launcher_selection_at_disposal(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            chosen, _rollback, current = [
                self._create_codex_release(home, f"1.{index}.0", mtime=index)
                for index in range(3)
            ]
            self._set_current(home, current)
            launcher = self._set_launcher(home, current)

            def dispose(*args, **kwargs):
                self._set_launcher(home, chosen)
                return remove_checked_path(*args, **kwargs)

            with patch("lib.agent_storage.remove_checked_path", side_effect=dispose):
                result = cleanup_codex_standalone_releases(
                    home, os.getuid(), dry_run=False, proc_root=self.proc,
                )
            self.assertFalse(result.errors)
            self.assertFalse(result.removed)
            self.assertIn(os.path.basename(chosen), result.retained)
            self.assertTrue(os.path.isfile(launcher))

    def test_unverifiable_launcher_preserves_all_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            releases = [
                self._create_codex_release(home, f"1.{index}.0", mtime=index)
                for index in range(3)
            ]
            self._set_current(home, releases[-1])
            self._set_launcher(home, os.path.join(home, "missing"))
            result = cleanup_codex_standalone_releases(
                home, os.getuid(), dry_run=False, proc_root=self.proc,
            )
            self.assertTrue(result.errors)
            self.assertFalse(result.deferred)
            self.assertEqual(result.retained, result.found)
            self.assertFalse(result.removed)
            self.assertTrue(all(os.path.isdir(path) for path in releases))

    def test_release_replaced_during_isolation_is_retained_for_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            oldest, _rollback, current = [
                self._create_codex_release(home, f"1.{index}.0", mtime=index)
                for index in range(3)
            ]
            self._set_current(home, current)
            original = os.path.join(home, "retained-original")
            rename = os.rename

            def substitute(source, target, **kwargs):
                if source == os.path.basename(oldest) and target == "entry":
                    rename(oldest, original)
                    os.mkdir(oldest)
                    with open(os.path.join(oldest, "unfamiliar"), "w", encoding="utf-8") as stream:
                        stream.write("preserve replacement")
                return rename(source, target, **kwargs)

            with patch("lib.cleanup_paths.os.rename", side_effect=substitute):
                result = cleanup_codex_standalone_releases(
                    home, os.getuid(), dry_run=False, proc_root=self.proc,
                )
            self.assertFalse(result.removed)
            self.assertTrue(result.errors)
            self.assertFalse(result.deferred)
            recovery = result.errors[0].split("recovery path: ", 1)[1]
            with open(os.path.join(recovery, "unfamiliar"), encoding="utf-8") as stream:
                self.assertEqual(stream.read(), "preserve replacement")
            self.assertTrue(os.path.isfile(os.path.join(original, "bin", "codex")))

    def test_recreated_original_path_survives_release_disposal(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            oldest, _rollback, current = [
                self._create_codex_release(home, f"1.{index}.0", mtime=index)
                for index in range(3)
            ]
            self._set_current(home, current)
            rename = os.rename

            def recreate(source, target, **kwargs):
                result = rename(source, target, **kwargs)
                if source == os.path.basename(oldest) and target == "entry":
                    os.mkdir(oldest)
                    with open(os.path.join(oldest, "unfamiliar"), "w", encoding="utf-8") as stream:
                        stream.write("preserve new path")
                return result

            with patch("lib.cleanup_paths.os.rename", side_effect=recreate):
                result = cleanup_codex_standalone_releases(
                    home, os.getuid(), dry_run=False, proc_root=self.proc,
                )
            self.assertFalse(result.errors)
            self.assertEqual(result.removed, (os.path.basename(oldest),))
            with open(os.path.join(oldest, "unfamiliar"), encoding="utf-8") as stream:
                self.assertEqual(stream.read(), "preserve new path")

    def test_prunes_only_oldest_validated_release(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            oldest = self._create_codex_release(home, "1.0.0", mtime=1)
            rollback = self._create_codex_release(home, "1.1.0", mtime=2)
            current = self._create_codex_release(home, "1.2.0", mtime=3)
            self._set_current(home, current)

            result = cleanup_codex_standalone_releases(
                home,
                os.getuid(),
                dry_run=False,
                proc_root=self.proc,
            )

            self.assertEqual(result.removed, (os.path.basename(oldest),))
            self.assertFalse(os.path.exists(oldest))
            self.assertTrue(os.path.isdir(rollback))
            self.assertTrue(os.path.isdir(current))
            self.assertFalse(result.errors)

    def test_dry_run_reports_without_removing(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            oldest = self._create_codex_release(home, "1.0.0", mtime=1)
            self._create_codex_release(home, "1.1.0", mtime=2)
            current = self._create_codex_release(home, "1.2.0", mtime=3)
            self._set_current(home, current)

            result = cleanup_codex_standalone_releases(
                home,
                os.getuid(),
                dry_run=True,
                proc_root=self.proc,
            )

            self.assertEqual(result.selected, (os.path.basename(oldest),))
            self.assertFalse(result.removed)
            self.assertTrue(os.path.isdir(oldest))

    def test_retains_an_active_old_release(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as proc:
            active = self._create_codex_release(home, "1.0.0", mtime=1)
            removable = self._create_codex_release(home, "1.1.0", mtime=2)
            rollback = self._create_codex_release(home, "1.2.0", mtime=3)
            current = self._create_codex_release(home, "1.3.0", mtime=4)
            self._set_current(home, current)
            process = os.path.join(proc, "123")
            os.mkdir(process)
            os.symlink(os.path.join(active, "bin", "codex"), os.path.join(process, "exe"))

            result = cleanup_codex_standalone_releases(
                home,
                os.getuid(),
                dry_run=False,
                proc_root=proc,
            )

            self.assertEqual(result.active, (os.path.basename(active),))
            self.assertEqual(result.removed, (os.path.basename(removable),))
            self.assertTrue(os.path.isdir(active))
            self.assertTrue(os.path.isdir(rollback))

    def test_invalid_current_link_prevents_removal(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            first = self._create_codex_release(home, "1.0.0", mtime=1)
            second = self._create_codex_release(home, "1.1.0", mtime=2)
            third = self._create_codex_release(home, "1.2.0", mtime=3)
            self._set_current(home, home)

            result = cleanup_codex_standalone_releases(
                home,
                os.getuid(),
                dry_run=False,
                proc_root=self.proc,
            )

            self.assertFalse(result.removed)
            self.assertTrue(result.errors)
            self.assertFalse(result.deferred)
            self.assertTrue(all(os.path.isdir(path) for path in (first, second, third)))

    def test_unfamiliar_and_symlinked_releases_are_never_removed(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            releases = os.path.join(
                home,
                ".codex",
                "packages",
                "standalone",
                "releases",
            )
            old = self._create_codex_release(home, "1.0.0", mtime=1)
            self._create_codex_release(home, "1.1.0", mtime=2)
            current = self._create_codex_release(home, "1.2.0", mtime=3)
            self._set_current(home, current)
            unfamiliar = os.path.join(releases, "manual-copy")
            os.mkdir(unfamiliar)
            symlinked = os.path.join(releases, "linked-release")
            os.symlink(old, symlinked)

            result = cleanup_codex_standalone_releases(
                home,
                os.getuid(),
                dry_run=False,
                proc_root=self.proc,
            )

            self.assertEqual(
                result.skipped,
                ("linked-release", "manual-copy"),
            )
            self.assertTrue(os.path.isdir(unfamiliar))
            self.assertTrue(os.path.islink(symlinked))

    def test_process_inventory_failure_preserves_every_release(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            releases = [
                self._create_codex_release(home, version, mtime=index)
                for index, version in enumerate(("1.0.0", "1.1.0", "1.2.0"), 1)
            ]
            self._set_current(home, releases[-1])

            result = cleanup_codex_standalone_releases(
                home,
                os.getuid(),
                dry_run=False,
                proc_root=os.path.join(home, "missing-proc"),
            )

            self.assertFalse(result.removed)
            self.assertTrue(result.errors)
            self.assertTrue(result.deferred)
            self.assertTrue(all(os.path.isdir(path) for path in releases))

    def test_unverifiable_remaining_process_preserves_every_release(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            releases = [
                self._create_codex_release(home, version, mtime=index)
                for index, version in enumerate(("1.0.0", "1.1.0", "1.2.0"), 1)
            ]
            self._set_current(home, releases[-1])
            process = os.path.join(self.proc, "123")
            os.mkdir(process)
            executable = os.path.join(process, "exe")
            os.symlink(os.path.join(releases[0], "bin", "codex"), executable)
            original_readlink = os.readlink
            for failure in (PermissionError, FileNotFoundError):
                def readlink(path, *args, **kwargs):
                    if path == executable:
                        raise failure("fixture process is unverifiable")
                    return original_readlink(path, *args, **kwargs)

                for dry_run in (True, False):
                    with self.subTest(failure=failure, dry_run=dry_run), patch(
                        "lib.agent_storage.os.readlink", side_effect=readlink,
                    ):
                        result = cleanup_codex_standalone_releases(
                            home, os.getuid(), dry_run=dry_run, proc_root=self.proc,
                        )
                    self.assertTrue(result.errors)
                    self.assertTrue(result.deferred)
                    self.assertIn("123", result.errors[0])
                    self.assertEqual(set(result.retained), set(result.found))
                    self.assertFalse(result.selected)
                    self.assertFalse(result.removed)
                    self.assertTrue(all(os.path.isdir(path) for path in releases))

    def test_process_inventory_failure_before_disposal_preserves_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            releases = [
                self._create_codex_release(home, version, mtime=index)
                for index, version in enumerate(("1.0.0", "1.1.0", "1.2.0"), 1)
            ]
            self._set_current(home, releases[-1])
            with patch("lib.agent_storage._active_codex_releases", side_effect=[
                set(), _CodexProcessInventoryUnavailable("fixture process inventory became unavailable"),
            ]):
                result = cleanup_codex_standalone_releases(
                    home, os.getuid(), dry_run=False, proc_root=self.proc,
                )
            self.assertTrue(result.errors)
            self.assertTrue(result.deferred)
            self.assertFalse(result.removed)
            self.assertEqual(set(result.retained), set(result.found))
            self.assertTrue(all(os.path.isdir(path) for path in releases))

    def test_process_enumeration_failure_is_a_safety_deferral(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            releases = [
                self._create_codex_release(home, f"1.{index}.0", mtime=index)
                for index in range(3)
            ]
            self._set_current(home, releases[-1])
            process = os.path.join(self.proc, "123")
            os.mkdir(process)
            os.symlink(os.path.join(releases[0], "bin", "codex"), os.path.join(process, "exe"))
            scandir = os.scandir

            @contextmanager
            def denied_listing():
                with scandir(self.proc) as entries:
                    def fail_after_entries():
                        yield from entries
                        raise PermissionError("fixture listing became unreadable")
                    yield fail_after_entries()

            def enumerate_path(path):
                return denied_listing() if path == self.proc else scandir(path)

            for dry_run in (True, False):
                with self.subTest(dry_run=dry_run), patch(
                    "lib.agent_storage.os.scandir", side_effect=enumerate_path,
                ):
                    result = cleanup_codex_standalone_releases(
                        home, os.getuid(), dry_run=dry_run, proc_root=self.proc,
                    )
                self.assertTrue(result.deferred)
                self.assertTrue(result.errors)
                self.assertEqual(result.retained, result.found)
                self.assertFalse(result.removed)
                self.assertTrue(all(os.path.isdir(path) for path in releases))

    def test_process_exit_during_inventory_does_not_block_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            oldest = self._create_codex_release(home, "1.0.0", mtime=1)
            self._create_codex_release(home, "1.1.0", mtime=2)
            current = self._create_codex_release(home, "1.2.0", mtime=3)
            self._set_current(home, current)
            process = os.path.join(self.proc, "123")
            os.mkdir(process)
            executable = os.path.join(process, "exe")
            original_readlink = os.readlink

            def readlink(path, *args, **kwargs):
                if path == executable:
                    os.rmdir(process)
                    raise ProcessLookupError("fixture process exited")
                return original_readlink(path, *args, **kwargs)

            with patch("lib.agent_storage.os.readlink", side_effect=readlink):
                result = cleanup_codex_standalone_releases(
                    home, os.getuid(), dry_run=False, proc_root=self.proc,
                )
            self.assertFalse(result.errors)
            self.assertEqual(result.removed, (os.path.basename(oldest),))
            self.assertFalse(os.path.exists(oldest))

    def test_no_process_inventory_needed_within_configured_retention(self) -> None:
        for count, rollback in ((1, 0), (2, 1), (3, 2)):
            for dry_run in (True, False):
                with self.subTest(count=count, rollback=rollback, dry_run=dry_run), \
                        tempfile.TemporaryDirectory() as home:
                    releases = [
                        self._create_codex_release(home, f"1.{index}.0", mtime=index)
                        for index in range(count)
                    ]
                    self._set_current(home, releases[-1])
                    result = cleanup_codex_standalone_releases(
                        home, os.getuid(), dry_run=dry_run, keep_rollback=rollback,
                        proc_root=os.path.join(home, "unavailable-proc"),
                    )
                    self.assertFalse(result.errors)
                    self.assertEqual(result.retained, result.found)
                    self.assertFalse(result.selected)
                    self.assertFalse(result.removed)

    def test_t3_cleanup_selects_only_numbered_regular_rotations(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            logs = os.path.join(home, ".t3", "userdata", "logs", "session")
            os.makedirs(logs)
            now = time.time()
            rotations = []
            for index in range(1, 4):
                path = os.path.join(logs, f"provider.log.{index}")
                with open(path, "wb") as log_file:
                    log_file.write(b"xxxx")
                os.utime(path, (now + index, now + index))
                rotations.append(path)
            current = os.path.join(logs, "provider.log")
            with open(current, "wb") as log_file:
                log_file.write(b"current")
            linked = os.path.join(logs, "provider.log.9")
            os.symlink(current, linked)

            result = cleanup_t3_rotated_logs(
                home,
                os.getuid(),
                dry_run=False,
                max_bytes=6,
                max_age_days=365,
            )

            self.assertEqual(result.found_count, 3)
            self.assertEqual(set(result.removed), set(rotations[:2]))
            self.assertTrue(os.path.isfile(rotations[2]))
            self.assertTrue(os.path.isfile(current))
            self.assertTrue(os.path.islink(linked))
            self.assertFalse(result.errors)

    def test_t3_dry_run_does_not_remove_selected_rotation(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            logs = os.path.join(home, ".t3", "userdata", "logs")
            os.makedirs(logs)
            rotation = os.path.join(logs, "trace.ndjson.1")
            with open(rotation, "wb") as log_file:
                log_file.write(b"old")
            old = time.time() - (20 * 24 * 60 * 60)
            os.utime(rotation, (old, old))

            result = cleanup_t3_rotated_logs(
                home,
                os.getuid(),
                dry_run=True,
                max_bytes=1024,
                max_age_days=14,
            )

            self.assertEqual(result.selected, (rotation,))
            self.assertTrue(os.path.isfile(rotation))

    def test_t3_rotation_replacement_is_retained_for_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            logs = os.path.join(home, ".t3", "userdata", "logs")
            os.makedirs(logs)
            rotation = os.path.join(logs, "trace.ndjson.1")
            with open(rotation, "w", encoding="utf-8") as stream:
                stream.write("inventoried rotation")
            original = os.path.join(home, "retained-original")
            rename = os.rename

            def substitute(source, target, **kwargs):
                if source == os.path.basename(rotation) and target == "entry":
                    rename(rotation, original)
                    os.mkdir(rotation)
                    with open(os.path.join(rotation, "unfamiliar.log.1"), "w", encoding="utf-8") as stream:
                        stream.write("preserve replacement")
                return rename(source, target, **kwargs)

            with patch("lib.cleanup_paths.os.rename", side_effect=substitute):
                result = cleanup_t3_rotated_logs(
                    home, os.getuid(), dry_run=False, max_bytes=0, max_age_days=14,
                )
            self.assertFalse(result.removed)
            self.assertEqual(result.removed_bytes, 0)
            self.assertTrue(result.errors)
            recovery = result.errors[0].split("recovery path: ", 1)[1]
            second = cleanup_t3_rotated_logs(
                home, os.getuid(), dry_run=False, max_bytes=0, max_age_days=14,
            )
            self.assertFalse(second.errors)
            self.assertFalse(second.found_count)
            self.assertFalse(second.removed)
            with open(os.path.join(recovery, "unfamiliar.log.1"), encoding="utf-8") as stream:
                self.assertEqual(stream.read(), "preserve replacement")
            with open(original, encoding="utf-8") as stream:
                self.assertEqual(stream.read(), "inventoried rotation")

    def test_t3_rotation_modified_after_inventory_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            logs = os.path.join(home, ".t3", "userdata", "logs")
            os.makedirs(logs)
            rotation = os.path.join(logs, "provider.log.1")
            with open(rotation, "wb") as stream:
                stream.write(b"old")

            def modify(path, *args, **kwargs):
                with open(path, "ab") as stream:
                    stream.write(b"new evidence")
                changed = os.lstat(path).st_mtime_ns + 1_000_000_000
                os.utime(path, ns=(changed, changed))
                return remove_checked_path(path, *args, **kwargs)

            with patch("lib.agent_storage.remove_checked_path", side_effect=modify):
                result = cleanup_t3_rotated_logs(
                    home, os.getuid(), dry_run=False, max_bytes=0, max_age_days=14,
                )
            self.assertFalse(result.removed)
            self.assertTrue(result.errors)
            with open(rotation, "rb") as stream:
                self.assertEqual(stream.read(), b"oldnew evidence")

    def test_agent_setups_reconcile_after_installing_t3(self) -> None:
        config = SetupConfig(
            host="target",
            username="agent",
            system_type="agent_code_vm",
            agent_tools=["opencode"],
            web_interfaces=["t3code"],
        )
        steps = []

        extend_agent_steps(config, steps)

        names = [name for name, _function in steps]
        t3_index = names.index("Installing T3 Code web interface")
        self.assertEqual(names[t3_index + 1], "Reconciling agent storage")
        self.assertIn("Updating managed agent tools", names)
        self.assertEqual(
            resolve_custom_step("reconcile_agent_storage").__name__,
            "reconcile_agent_storage",
        )

    def test_setup_reconciliation_runs_as_the_target_user(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="inventory complete\n", stderr="")
        config = SimpleNamespace(username="agent")
        with (
            patch("common.agent_steps._user_home", return_value="/home/agent"),
            patch("common.agent_steps.is_dry_run", return_value=False),
            patch(
                "common.agent_steps._run_as_login_user",
                return_value=completed,
            ) as run_as_user,
        ):
            reconcile_agent_storage(config)

        self.assertEqual(run_as_user.call_args.args[:2], ("agent", "/home/agent"))
        self.assertIn("--agent-storage-only", run_as_user.call_args.args[2])
        self.assertFalse(run_as_user.call_args.kwargs["check"])
        self.assertTrue(run_as_user.call_args.kwargs["capture_output"])

    def test_agent_storage_only_policy_combines_codex_backups_and_t3(self) -> None:
        context = user_cache_maintenance.UserContext("agent", "/home/agent", 1000)
        with (
            patch(
                "common.service_tools.user_cache_maintenance."
                "cleanup_codex_standalone_releases",
                return_value=["codex failure"],
            ) as codex_cleanup,
            patch(
                "common.service_tools.user_cache_maintenance.cleanup_codex_update_backups",
                return_value=["backup failure"],
            ) as backup_cleanup,
            patch(
                "common.service_tools.user_cache_maintenance.cleanup_t3_rotated_logs",
                return_value=["t3 failure"],
            ) as t3_cleanup,
            patch(
                "common.service_tools.user_cache_maintenance.cleanup_t3_runtimes",
                return_value=[],
            ),
        ):
            failures = user_cache_maintenance.cleanup_agent_storage(
                context,
                dry_run=True,
            )

        self.assertEqual(failures, ["codex failure", "backup failure", "t3 failure"])
        codex_cleanup.assert_called_once_with(context, dry_run=True)
        backup_cleanup.assert_called_once_with(context, dry_run=True)
        t3_cleanup.assert_called_once_with(context, dry_run=True)

    def test_agent_storage_only_cli_skips_general_cache_policies(self) -> None:
        context = user_cache_maintenance.UserContext("agent", "/home/agent", 1000)
        with (
            patch(
                "common.service_tools.user_cache_maintenance.resolve_user_context",
                return_value=context,
            ),
            patch(
                "common.service_tools.user_cache_maintenance.cleanup_agent_storage",
                return_value=[],
            ) as storage_cleanup,
            patch(
                "common.service_tools.user_cache_maintenance.run_user_cache_maintenance",
            ) as general_cleanup,
        ):
            exit_code = user_cache_maintenance.main(
                ["--agent-storage-only", "--dry-run"]
            )

        self.assertEqual(exit_code, 0)
        storage_cleanup.assert_called_once_with(context, dry_run=True)
        general_cleanup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
