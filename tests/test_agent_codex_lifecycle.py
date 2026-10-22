"""Codex workstation lifecycle regressions; commands and downloads are mocked."""

from __future__ import annotations

from contextlib import ExitStack
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from common import cachyos_steps
from lib import agent_cli
from lib.agent_storage import (
    _active_codex_releases as scan_codex_processes,
    cleanup_codex_update_backups,
    codex_package_for_executable,
    codex_update_supported,
    prune_codex_update_backups,
)
from lib.config import SetupConfig
from lib.cleanup_paths import remove_checked_path


class CodexLifecycleTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.home = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.launcher = self.home / ".local/bin/codex"
        self.launcher.parent.mkdir(parents=True)
        self.root = self.home / ".codex/packages/standalone"
        self.package = self.release("1.0.0")
        (self.root / "current").symlink_to(self.package)
        self.launcher.symlink_to(self.root / "current/bin/codex")
        self.config = self.home / ".codex/config.toml"
        self.auth = self.home / ".codex/auth.json"
        self.config.write_text('model = "personal-choice"\n')
        self.auth.write_text('{"private": "fixture"}\n')
        stack.enter_context(patch.object(agent_cli, "_tool_version", side_effect=self.version))
        stack.enter_context(patch.object(agent_cli, "_tool_smoke_test", side_effect=lambda path: self.version("codex", path) is not None))
        stack.enter_context(patch.object(agent_cli, "_tool_path", side_effect=lambda tool, home: str(self.launcher)))
        stack.enter_context(patch("lib.agent_storage._active_codex_releases", return_value=set()))

    def release(self, version):
        package = self.root / "releases" / (version + "-x86_64-linux-musl")
        (package / "bin").mkdir(parents=True)
        (package / "bin/codex").write_text(version)
        (package / "bin/codex").chmod(0o755)
        (package / "codex-resources").mkdir()
        (package / "codex-resources/bwrap").write_text("sandbox fixture")
        (package / "codex-package.json").write_text(json.dumps({
            "layoutVersion": 1, "variant": "codex", "entrypoint": "bin/codex",
            "version": version, "target": "x86_64-linux-musl",
        }))
        return package

    @staticmethod
    def version(tool, path):
        try:
            value = Path(path).read_text()
            return None if value == "broken" else value
        except OSError:
            return None

    def update(self, *, broken=False, code=0):
        def invoke(tool, path, home):
            replacement = self.release("2.0.0")
            if broken:
                (replacement / "bin/codex").write_text("broken")
            (self.root / "current").unlink()
            (self.root / "current").symlink_to(replacement)
            self.launcher.unlink()
            self.launcher.symlink_to(self.root / "current/bin/codex")
            return {"returncode": code}

        with patch.object(agent_cli, "_invoke_agent_update", side_effect=invoke):
            return agent_cli.update_agent_tools(["codex"], home=str(self.home))[0]

    def test_failed_update_restores_whole_package_and_preserves_configuration(self):
        result = self.update(broken=True)
        self.assertTrue(result["rollback"])
        self.assertEqual(result["after_version"], "1.0.0")
        package = Path(codex_package_for_executable(str(self.home), str(self.launcher)))
        self.assertEqual((package / "codex-resources/bwrap").read_text(), "sandbox fixture")
        self.assertTrue(codex_update_supported(str(self.home), str(self.launcher)))
        self.assertEqual(self.config.read_text(), 'model = "personal-choice"\n')
        self.assertEqual(self.auth.read_text(), '{"private": "fixture"}\n')
        self.assertEqual(os.stat(result["backup_path"]).st_mode & 0o777, 0o755)

    def test_successful_update_retains_one_full_snapshot(self):
        first = agent_cli._backup_executable("codex", str(self.launcher), str(self.home))
        result = self.update()
        self.assertEqual(result["status"], "updated")
        self.assertTrue(Path(result["backup_path"]).is_file())
        self.assertFalse(Path(first).exists())
        self.assertEqual(self.launcher.resolve(), self.root / "releases/2.0.0-x86_64-linux-musl/bin/codex")

    def test_successful_update_still_records_process_visibility_deferral(self):
        old = Path(agent_cli._backup_executable("codex", str(self.launcher), str(self.home)))
        proc = self.home / "proc"
        (proc / "123").mkdir(parents=True)
        with patch(
            "lib.agent_storage._active_codex_releases",
            side_effect=lambda releases, uid, _root: scan_codex_processes(releases, uid, str(proc)),
        ):
            result = self.update()
        self.assertEqual(result["status"], "updated")
        self.assertTrue(result["backup_cleanup_deferred"])
        self.assertTrue(old.exists())
        self.assertTrue(Path(result["backup_path"]).exists())

    def test_backup_cleanup_retains_active_and_selected_snapshots(self):
        old = agent_cli._backup_executable("codex", str(self.launcher), str(self.home))
        current = agent_cli._backup_executable("codex", str(self.launcher), str(self.home))
        self.launcher.unlink()
        self.launcher.symlink_to(old)
        prune_codex_update_backups(str(self.home), current)
        self.assertTrue(Path(old).exists())
        self.launcher.unlink()
        self.launcher.symlink_to(self.package / "bin/codex")
        with patch("lib.agent_storage._active_codex_releases", side_effect=lambda releases, *args, **kwargs: set(releases)):
            prune_codex_update_backups(str(self.home), current)
        self.assertTrue(Path(old).exists())
        prune_codex_update_backups(str(self.home), current)
        self.assertFalse(Path(old).exists())

    def test_home_local_npm_link_is_not_adopted_by_standalone_updater(self):
        npm = self.home / ".npm-global/lib/node_modules/@openai/codex/bin/codex.js"
        npm.parent.mkdir(parents=True)
        npm.write_text("npm fixture")
        self.launcher.unlink()
        self.launcher.symlink_to(npm)
        with patch.object(agent_cli, "_invoke_agent_update") as invoke:
            result = agent_cli.update_agent_tools(["codex"], home=str(self.home))
        self.assertEqual(result[0]["failure"], "not_user_managed")
        invoke.assert_not_called()
        config = SetupConfig(host="localhost", username="human", system_type="agent_cachyos", agent_tools=["codex"])
        with patch.object(cachyos_steps, "_home", return_value=self.home), \
                patch.object(cachyos_steps.shutil, "which", return_value=str(self.launcher)), \
                patch.object(agent_cli, "update_agent_tools") as update:
            cachyos_steps.install_cachyos_agents(config)
        update.assert_not_called()
        self.assertEqual(self.launcher.resolve(), npm)

    def prepare_backup_maintenance(self):
        obsolete = Path(agent_cli._backup_executable("codex", str(self.launcher), str(self.home)))
        keep = Path(agent_cli._backup_executable("codex", str(self.launcher), str(self.home)))
        self.proc = self.home / "proc"
        self.proc.mkdir()
        self.state_path = Path(agent_cli._state_path(str(self.home)))
        self.state = {"schema_version": 1, "tools": {"codex": {
            "tool": "codex", "path": str(self.launcher), "status": "updated",
            "backup_path": str(keep),
        }}}
        with agent_cli._agent_update_lock(str(self.home)):
            agent_cli._save_update_state(str(self.state_path), self.state)
        return obsolete, keep

    def reconcile_backups(self, *, dry_run=False):
        # Override the updater fixture's scanner mock with the real scanner
        # against a disposable proc tree; never inspect this host's processes.
        with patch("lib.agent_storage._active_codex_releases", side_effect=scan_codex_processes):
            return cleanup_codex_update_backups(
                str(self.home), os.getuid(), dry_run=dry_run, proc_root=str(self.proc),
            )

    def test_periodic_backup_cleanup_retires_snapshot_after_process_exit(self):
        obsolete, keep = self.prepare_backup_maintenance()
        process = self.proc / "123"
        process.mkdir()
        (process / "exe").symlink_to(obsolete)
        result = self.reconcile_backups()
        self.assertEqual(result.active, (obsolete.parents[2].name,))
        self.assertFalse(result.removed)
        self.assertFalse(result.errors)
        self.assertTrue(obsolete.exists())
        (process / "exe").unlink()
        process.rmdir()
        result = self.reconcile_backups()
        self.assertEqual(result.removed, (obsolete.parents[2].name,))
        self.assertFalse(obsolete.exists())
        self.assertTrue(keep.exists())
        self.assertFalse(result.errors)

    def test_privileged_observer_preserves_active_backup_then_retires_after_exit(self):
        obsolete, keep = self.prepare_backup_maintenance()
        with patch("lib.agent_storage._active_codex_releases", side_effect=scan_codex_processes), \
                patch("lib.agent_storage.observed_executables", return_value=(str(obsolete),)) as observe:
            result = cleanup_codex_update_backups(str(self.home), os.getuid(), dry_run=False)
            self.assertFalse(result.errors)
            self.assertFalse(result.removed)
            self.assertTrue(obsolete.is_file())
            observe.return_value = ()
            result = cleanup_codex_update_backups(str(self.home), os.getuid(), dry_run=False)
            self.assertFalse(result.errors)
            self.assertEqual(len(result.removed), 1)
            self.assertFalse(obsolete.exists())
            self.assertTrue(keep.is_file())

    def test_privileged_backup_observer_failure_at_disposal_retains_snapshots(self):
        obsolete, keep = self.prepare_backup_maintenance()
        with patch("lib.agent_storage._active_codex_releases", side_effect=scan_codex_processes), \
                patch("lib.agent_storage.observed_executables", side_effect=[(), OSError("fixture visibility lost")]):
            result = cleanup_codex_update_backups(str(self.home), os.getuid(), dry_run=False)
        self.assertTrue(result.deferred)
        self.assertFalse(result.removed)
        self.assertTrue(obsolete.is_file())
        self.assertTrue(keep.is_file())

    def test_periodic_backup_cleanup_preserves_selected_and_unfamiliar_entries(self):
        obsolete, keep = self.prepare_backup_maintenance()
        root = keep.parents[2].parent
        unfamiliar = root / "codex-package-manual"
        unfamiliar.mkdir()
        (unfamiliar / "notes").write_text("fixture")
        linked = root / "codex-package-linked"
        linked.symlink_to(obsolete.parents[2])
        legacy = root / "codex.previous"
        legacy.write_text("distinct recovery fixture")
        for selector in (self.launcher, self.root / "current"):
            with self.subTest(selector=selector.name):
                original = os.readlink(selector)
                selector.unlink()
                selector.symlink_to(obsolete if selector == self.launcher else obsolete.parents[1])
                result = self.reconcile_backups()
                self.assertFalse(result.removed)
                self.assertFalse(result.errors)
                self.assertEqual(set(result.skipped), {unfamiliar.name, linked.name})
                self.assertTrue(obsolete.exists())
                self.assertTrue(keep.exists())
                self.assertEqual(legacy.read_text(), "distinct recovery fixture")
                selector.unlink()
                selector.symlink_to(original)

    def test_periodic_backup_dry_run_reports_without_writes(self):
        obsolete, keep = self.prepare_backup_maintenance()
        with patch("lib.agent_storage.remove_checked_path", wraps=remove_checked_path) as dispose, \
                patch("lib.cleanup_paths.os.rename") as rename:
            result = self.reconcile_backups(dry_run=True)
        self.assertEqual(result.selected, (obsolete.parents[2].name,))
        self.assertFalse(result.removed)
        self.assertTrue(obsolete.exists())
        self.assertTrue(keep.exists())
        self.assertTrue(dispose.call_args.kwargs["dry_run"])
        rename.assert_not_called()

    def test_periodic_backup_cleanup_contends_with_real_updater_lock(self):
        obsolete, keep = self.prepare_backup_maintenance()
        with agent_cli._agent_update_lock(str(self.home)):
            for dry_run in (True, False):
                result = self.reconcile_backups(dry_run=dry_run)
                self.assertIn("lock is busy", result.errors[0])
                self.assertTrue(result.deferred)
                self.assertFalse(result.removed)
                self.assertTrue(obsolete.exists())
                self.assertTrue(keep.exists())
        # Maintenance also owns the lock through the actual disposal boundary.
        def dispose(*args, **kwargs):
            with self.assertRaisesRegex(RuntimeError, "Another agent update"):
                with agent_cli._agent_update_lock(str(self.home)):
                    pass
            return remove_checked_path(*args, **kwargs)

        with patch("lib.agent_storage.remove_checked_path", side_effect=dispose):
            result = self.reconcile_backups()
        self.assertEqual(result.removed, (obsolete.parents[2].name,))

    def test_periodic_backup_cleanup_defers_on_unverifiable_update_state(self):
        obsolete, keep = self.prepare_backup_maintenance()
        invalid_records = [None, "{", "{}", '{"schema_version": 2, "tools": {}}',
                           '{"schema_version": 1, "tools": {}}']
        for mutation in (
            {"status": "in_progress"}, {"status": "planned"}, {"backup_path": None},
            {"backup_path": str(self.package / "bin/codex")},
            {"backup_path": str(keep.parent / "missing")}, {"path": None},
        ):
            state = json.loads(json.dumps(self.state))
            state["tools"]["codex"].update(mutation)
            invalid_records.append(json.dumps(state))
        for content in invalid_records:
            with self.subTest(content=content):
                if content is None:
                    self.state_path.unlink()
                else:
                    self.state_path.write_text(content)
                result = self.reconcile_backups()
                self.assertTrue(result.errors)
                self.assertFalse(result.deferred)
                self.assertFalse(result.removed)
                self.assertTrue(obsolete.exists())
                self.assertTrue(keep.exists())
                if content is not None:
                    self.assertEqual(self.state_path.read_text(), content)

    def test_periodic_backup_cleanup_defers_on_missing_or_linked_evidence(self):
        obsolete, keep = self.prepare_backup_maintenance()
        lock = self.state_path.parent / "agent-update.lock"
        lock.unlink()
        result = self.reconcile_backups(dry_run=True)
        self.assertTrue(result.errors)
        self.assertFalse(lock.exists())
        lock.symlink_to(self.state_path)
        self.assertTrue(self.reconcile_backups().errors)
        lock.unlink()
        with agent_cli._agent_update_lock(str(self.home)):
            pass
        self.state_path.unlink()
        self.state_path.symlink_to(keep)
        self.assertTrue(self.reconcile_backups().errors)
        self.assertTrue(obsolete.exists())
        self.assertTrue(keep.exists())

    def test_periodic_backup_cleanup_defers_on_process_inventory_failure(self):
        obsolete, keep = self.prepare_backup_maintenance()
        process = self.proc / "123"
        process.mkdir()  # A remaining PID with no verifiable exe is not idle.
        for dry_run in (True, False):
            result = self.reconcile_backups(dry_run=dry_run)
            self.assertTrue(result.errors)
            self.assertTrue(result.deferred)
            self.assertIn("123", result.errors[0])
            self.assertFalse(result.removed)
            self.assertEqual(result.retained, result.found)
        process.rmdir()
        self.proc.rmdir()
        self.assertTrue(self.reconcile_backups().errors)
        self.proc.mkdir()
        process.mkdir()
        with patch("lib.agent_storage.os.readlink", side_effect=PermissionError):
            self.assertTrue(self.reconcile_backups().errors)
        self.assertTrue(obsolete.exists())
        self.assertTrue(keep.exists())

    def test_periodic_backup_cleanup_defers_if_process_visibility_is_lost_before_disposal(self):
        obsolete, keep = self.prepare_backup_maintenance()
        scans = 0

        def inventory(releases, uid, proc_root):
            nonlocal scans
            scans += 1
            if scans == 2:
                (self.proc / "123").mkdir()  # Cannot verify this remaining PID.
            return scan_codex_processes(releases, uid, proc_root)

        with patch("lib.agent_storage._active_codex_releases", side_effect=inventory):
            result = cleanup_codex_update_backups(
                str(self.home), os.getuid(), dry_run=False, proc_root=str(self.proc),
            )
        self.assertTrue(result.deferred)
        self.assertTrue(result.errors)
        self.assertFalse(result.removed)
        self.assertEqual(result.retained, result.found)
        self.assertTrue(obsolete.exists())
        self.assertTrue(keep.exists())
        (self.proc / "123").rmdir()
        retried = self.reconcile_backups()
        self.assertFalse(retried.deferred)
        self.assertFalse(retried.errors)
        self.assertFalse(obsolete.exists())
        self.assertTrue(keep.exists())

    def test_periodic_backup_cleanup_needs_no_process_inventory_for_rollback_alone(self):
        obsolete, keep = self.prepare_backup_maintenance()
        self.reconcile_backups()  # Retire the obsolete fixture with full evidence.
        self.assertFalse(obsolete.exists())
        self.proc.rmdir()  # Process inventory is now unavailable.
        for dry_run in (True, False):
            with self.subTest(dry_run=dry_run):
                result = self.reconcile_backups(dry_run=dry_run)
                self.assertFalse(result.errors)
                self.assertEqual(result.found, (keep.parents[2].name,))
                self.assertEqual(result.retained, result.found)
                self.assertFalse(result.selected)
                self.assertFalse(result.removed)
                self.assertTrue(keep.exists())

    def test_periodic_backup_cleanup_rechecks_processes_before_disposal(self):
        obsolete, keep = self.prepare_backup_maintenance()
        scans = 0

        def inventory(releases, uid, proc_root, **kwargs):
            nonlocal scans
            scans += 1
            if scans == 2:
                process = self.proc / "123"
                process.mkdir()
                (process / "exe").symlink_to(obsolete)
            return scan_codex_processes(releases, uid, proc_root, **kwargs)

        with patch("lib.agent_storage._active_codex_releases", side_effect=inventory):
            result = cleanup_codex_update_backups(
                str(self.home), os.getuid(), dry_run=False, proc_root=str(self.proc),
            )
        self.assertFalse(result.removed)
        self.assertIn(obsolete.parents[2].name, result.active)
        self.assertTrue(obsolete.exists())
        self.assertTrue(keep.exists())

    def test_periodic_backup_cleanup_rechecks_snapshot_identity(self):
        obsolete, keep = self.prepare_backup_maintenance()
        snapshot = obsolete.parents[2]

        def dispose(path, metadata, **kwargs):
            snapshot.rename(snapshot.with_name("manual-recovery"))
            snapshot.mkdir()
            (snapshot / "unfamiliar").write_text("preserve")
            return remove_checked_path(path, metadata, **kwargs)

        with patch("lib.agent_storage.remove_checked_path", side_effect=dispose):
            result = self.reconcile_backups()
        self.assertFalse(result.removed)
        self.assertTrue(result.errors)
        self.assertFalse(result.deferred)
        self.assertEqual((snapshot / "unfamiliar").read_text(), "preserve")
        self.assertTrue(keep.exists())

    def test_failed_version_command_is_not_a_version(self):
        with patch.object(agent_cli.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "private error")):
            self.assertIsNone(_real_tool_version("codex", str(self.launcher)))

    def test_successful_update_after_rollback_remains_manageable(self):
        failed = self.update(broken=True)
        self.assertTrue(failed["rollback"])

        def invoke(tool, path, home):
            replacement = self.release("3.0.0")
            self.launcher.unlink()
            self.launcher.symlink_to(replacement / "bin/codex")
            return {"returncode": 0}

        with patch.object(agent_cli, "_invoke_agent_update", side_effect=invoke):
            result = agent_cli.update_agent_tools(["codex"], home=str(self.home))[0]
        self.assertEqual(result["status"], "updated")
        self.assertEqual(result["after_version"], "3.0.0")
        self.assertTrue(codex_update_supported(str(self.home), str(self.launcher)))
        self.assertFalse(Path(failed["backup_path"]).exists())
        self.assertEqual(Path(result["backup_path"]).read_text(), "1.0.0")

    def test_custom_codex_home_fails_readonly_preflight(self):
        import basaltwater
        from lib.cachyos import cachyos_config_from_args, preflight_cachyos
        parser, _, _ = basaltwater.create_basaltwater_parser()
        config = cachyos_config_from_args(parser.parse_args(["setup", "agent_cachyos", "localhost", "human"]))
        owner = type("Owner", (), {"pw_dir": str(self.home), "pw_uid": 1000})()
        with patch("lib.cachyos.is_cachyos", return_value=True), \
                patch("lib.cachyos.platform.machine", return_value="x86_64"), \
                patch("lib.cachyos.pwd.getpwnam", return_value=owner), \
                patch("lib.cachyos.os.geteuid", return_value=1000), \
                patch("lib.cachyos.os.path.expanduser", return_value=str(self.home)), \
                patch("lib.cachyos.shutil.which", return_value="/usr/bin/tool"), \
                patch("lib.machine_state.detect_machine_type", return_value="hardware"), \
                patch.dict(os.environ, {"SSH_TTY": "", "SSH_CONNECTION": "", "CODEX_HOME": str(self.home / "personal-codex")}), \
                self.assertRaisesRegex(ValueError, "custom CODEX_HOME"):
            preflight_cachyos(config)
        self.assertFalse((self.home / "personal-codex").exists())

    def test_concurrent_update_cannot_replace_another_runs_snapshot(self):
        with agent_cli._agent_update_lock(str(self.home)), \
                patch.object(agent_cli, "_invoke_agent_update") as invoke, \
                self.assertRaisesRegex(RuntimeError, "Another agent update"):
            agent_cli.update_agent_tools(["codex"], home=str(self.home))
        invoke.assert_not_called()
        self.assertEqual(self.launcher.resolve(), self.package / "bin/codex")

    def test_updater_environment_removes_vendor_redirects_and_pins(self):
        owner = type("Owner", (), {"pw_name": "human"})()
        with patch.dict(os.environ, {
            "CODEX_INSTALL_DIR": "/elsewhere", "CODEX_INSTALL_DAEMON_ONLY": "1",
            "CODEX_INSTALL_DEFER_SELECTION": "1", "CODEX_RELEASE": "old",
            "CODEX_UPDATE_FROM_RELEASE": "old", "CODEX_HOME": "/another-home",
        }):
            environment = agent_cli._agent_update_environment(str(self.home), owner)
        self.assertEqual(environment["CODEX_HOME"], str(self.home / ".codex"))
        self.assertFalse(any(key.startswith(("CODEX_INSTALL", "CODEX_UPDATE")) for key in environment))
        self.assertNotIn("CODEX_RELEASE", environment)

    def test_readiness_checks_local_login_without_echoing_credentials(self):
        config = SetupConfig(host="localhost", username="human", system_type="agent_cachyos", agent_tools=["codex"])
        for code in (0, 1):
            def command(argv, home, **kwargs):
                return subprocess.CompletedProcess(argv, code if argv[-2:] == ["login", "status"] else 0,
                                                   "private fixture", "private fixture")
            with patch.object(cachyos_steps, "_home", return_value=self.home), \
                    patch.object(cachyos_steps.shutil, "which", return_value=str(self.launcher)), \
                    patch.object(cachyos_steps, "_user_run", side_effect=command) as run, \
                    patch("sys.stdout", new_callable=io.StringIO) as output:
                cachyos_steps.report_cachyos_readiness(config)
            self.assertTrue(any(call.args[0][-2:] == ["login", "status"] for call in run.call_args_list))
            self.assertNotIn("private fixture", output.getvalue())
            self.assertIn("local login available" if code == 0 else "login/configuration check failed", output.getvalue())


_real_tool_version = agent_cli._tool_version


if __name__ == "__main__":
    unittest.main()
