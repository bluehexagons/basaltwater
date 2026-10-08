"""Fault injection for release activation, persistence and interruption."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from subprocess import CompletedProcess

from lib.deployment import DeploymentOrchestrator
from lib.unit_transaction import UnitRecoveryError
from lib.operation_state import OperationStateError, OperationStateStore
from lib.project_manifest import parse_manifest
from lib.remote_utils import CommandTimeoutError


class TestReleaseRecovery(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.base = self.root / "www"
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "index.html").write_text("new")
        self.dest = self.base / "example_com"
        self.dest.mkdir(parents=True)
        (self.dest / "index.html").write_text("old")
        self.orchestrator = DeploymentOrchestrator(base_dir=str(self.base))
        self.state = self.base / ".basaltwater_shared" / "example_com"
        self.manifest = parse_manifest({"version": 1, "components": [{
            "name": "site", "type": "static", "domain": "example.com", "output": ".",
        }]})
        self.run = patch("lib.deployment.run", side_effect=lambda command, **kwargs:
                         CompletedProcess(command, 3 if "is-active" in command else 0, "", ""))
        self.run.start()
        self.addCleanup(self.run.stop)
        self.units = patch.object(self.orchestrator, "_app_unit_snapshots", return_value={})
        self.units.start()
        self.addCleanup(self.units.stop)

    def deploy(self, *, manifest=False, keep_source=True):
        args = (str(self.source), "example.com", "/", "https://example.test/site.git", "new")
        if manifest:
            return self.orchestrator.deploy_manifest(self.manifest, *args, keep_source=keep_source)
        return self.orchestrator.deploy_from_archive(*args)

    def test_static_metadata_failure_restores_last_release(self):
        with patch("lib.deployment.save_deployment_metadata", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.deploy()
        self.assertEqual((self.dest / "index.html").read_text(), "old")
        self.assertFalse((self.state / "static-operation.json").exists())
        self.assertEqual(list(self.base.glob("*.failed-*")), [])

    def test_static_first_release_metadata_failure_removes_failed_activation(self):
        (self.dest / "index.html").unlink()
        self.dest.rmdir()
        with patch("lib.deployment.save_deployment_metadata", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.deploy()
        self.assertFalse(self.dest.exists())
        self.assertFalse((self.state / "static-operation.json").exists())

    def test_static_rename_failure_recovers_displaced_release(self):
        rename = os.rename

        def fail(source, target):
            if ".build-" in str(source):
                raise OSError("activation failed")
            return rename(source, target)

        with patch("lib.deployment.os.rename", side_effect=fail):
            with self.assertRaisesRegex(OSError, "activation failed"):
                self.deploy()
        self.assertEqual((self.dest / "index.html").read_text(), "old")
        self.assertFalse((self.state / "static-operation.json").exists())

    def test_activation_sync_failure_after_rename_restores_previous_tree(self):
        from lib.atomic_io import rename_path_durable
        for manifest in (False, True):
            with self.subTest(manifest=manifest):
                def rename(source, destination):
                    rename_path_durable(source, destination)
                    if ".build-" in source:
                        raise OSError("activation fsync failed")

                with patch("lib.deployment.rename_path_durable", side_effect=rename), patch.object(
                    self.orchestrator, "_restore_app_units",
                ):
                    with self.assertRaisesRegex(OSError, "activation fsync failed"):
                        self.deploy(manifest=manifest)
                self.assertEqual((self.dest / "index.html").read_text(), "old")
                marker = "manifest-operation.json" if manifest else "static-operation.json"
                self.assertFalse((self.state / marker).exists())

    def test_static_failed_restore_retains_both_trees_and_blocks_manifest(self):
        rename = os.rename

        def fail(source, target):
            if ".previous-" in str(source):
                raise OSError("restore failed")
            return rename(source, target)

        with patch("lib.deployment.save_deployment_metadata", side_effect=OSError("disk full")), patch(
            "lib.deployment.os.rename", side_effect=fail,
        ):
            with self.assertRaisesRegex(RuntimeError, "recovery was incomplete"):
                self.deploy()
        record = OperationStateStore(str(self.state / "static-operation.json")).load()
        self.assertEqual(record.status, "recovery_required")
        self.assertEqual((Path(record.context["backup_path"]) / "index.html").read_text(), "old")
        self.assertEqual((Path(record.context["failed_path"]) / "index.html").read_text(), "new")
        with self.assertRaisesRegex(OperationStateError, "Unfinished static_deploy"):
            self.deploy(manifest=True)

    def test_rollback_marker_write_failure_still_attempts_release_restore(self):
        transition = OperationStateStore.transition
        for manifest in (False, True):
            with self.subTest(manifest=manifest):
                def fail(store, operation_id, phase, **kwargs):
                    if phase in {"rolling-back", "recovery"}:
                        raise OSError("marker storage unavailable")
                    return transition(store, operation_id, phase, **kwargs)

                with patch.object(OperationStateStore, "transition", fail), patch(
                    "lib.deployment.save_deployment_metadata", side_effect=OSError("metadata unavailable"),
                ), patch.object(self.orchestrator, "_restore_app_units"):
                    with self.assertRaisesRegex(RuntimeError, "recovery was incomplete"):
                        self.deploy(manifest=manifest)
                self.assertEqual((self.dest / "index.html").read_text(), "old")
                filename = "manifest-operation.json" if manifest else "static-operation.json"
                self.assertTrue((self.state / filename).exists())
                store = OperationStateStore(str(self.state / filename))
                store.complete(store.load().operation_id, outcome="rolled_back")

    def test_static_interruption_retains_staging_and_blocks_both_entrypoints(self):
        with patch.object(self.orchestrator, "_copy_deployment_source", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.deploy()
        record = OperationStateStore(str(self.state / "static-operation.json")).load()
        self.assertEqual(record.phase, "staging")
        self.assertTrue(Path(record.context["staging_path"]).is_dir())
        for manifest in (False, True):
            with self.subTest(manifest=manifest), self.assertRaises(OperationStateError):
                self.deploy(manifest=manifest)
        self.assertEqual((self.dest / "index.html").read_text(), "old")

    def test_manifest_interruption_retains_staging_and_blocks_static(self):
        with patch.object(self.orchestrator, "_run_component_build", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.deploy(manifest=True)
        record = OperationStateStore(str(self.state / "manifest-operation.json")).load()
        self.assertTrue(Path(record.context["staging_path"]).is_dir())
        with self.assertRaisesRegex(OperationStateError, "Unfinished manifest_deploy"):
            self.deploy()

    def test_manifest_metadata_failure_restores_port_state(self):
        self.state.mkdir(parents=True)
        ports = self.state / "manifest-ports.json"
        for previous in (None, {"previous": 8123}):
            with self.subTest(previous=previous):
                if previous is not None:
                    ports.write_text(json.dumps(previous))
                with patch("lib.deployment.save_deployment_metadata", side_effect=OSError("disk full")), patch.object(
                    self.orchestrator, "_restore_app_units",
                ):
                    with self.assertRaisesRegex(OSError, "disk full"):
                        self.deploy(manifest=True)
                self.assertEqual(json.loads(ports.read_text()) if ports.exists() else None, previous)
                self.assertEqual((self.dest / "index.html").read_text(), "old")
                self.assertFalse((self.state / "manifest-operation.json").exists())

    def test_manifest_stop_timeout_restarts_previous_service(self):
        snapshot = {"old-api": {"state": {"ActiveState": "active"}}}
        with patch.object(self.orchestrator, "_app_unit_snapshots", return_value=snapshot), patch.object(
            self.orchestrator, "_stop_app_unit", side_effect=CommandTimeoutError("systemctl stop old-api", 1),
        ), patch.object(self.orchestrator, "_restart_app_units") as restart:
            with self.assertRaises(CommandTimeoutError):
                self.deploy(manifest=True)
        restart.assert_called_once_with(["old-api"])
        self.assertEqual((self.dest / "index.html").read_text(), "old")
        self.assertFalse((self.state / "manifest-operation.json").exists())

    def test_source_cleanup_failure_preserves_successful_manifest(self):
        from lib import deployment
        remove = deployment.shutil.rmtree

        def fail(path, *args, **kwargs):
            if path == str(self.source):
                raise OSError("source busy")
            return remove(path, *args, **kwargs)

        with patch("lib.deployment.shutil.rmtree", side_effect=fail):
            self.deploy(manifest=True, keep_source=False)
        self.assertEqual((self.dest / "index.html").read_text(), "new")
        self.assertTrue(self.source.exists())
        self.assertFalse((self.state / "manifest-operation.json").exists())

    def test_manifest_restore_sync_failure_still_restores_service_units(self):
        from lib.atomic_io import rename_path_durable

        def rename(source, destination):
            rename_path_durable(source, destination)
            if ".previous-" in source:
                raise OSError("restore sync failed")

        with (
            patch("lib.deployment.rename_path_durable", side_effect=rename),
            patch("lib.deployment.save_deployment_metadata", side_effect=OSError("metadata failed")),
            patch.object(self.orchestrator, "_restore_app_units") as restore,
        ):
            with self.assertRaisesRegex(RuntimeError, "recovery was incomplete"):
                self.deploy(manifest=True)
        self.assertEqual((self.dest / "index.html").read_text(), "old")
        restore.assert_called_once_with(str(self.dest), {})
        self.assertTrue((self.state / "manifest-operation.json").exists())

    def test_release_flush_failure_restores_tree_before_discarding_backup(self):
        for manifest in (False, True):
            with self.subTest(manifest=manifest):
                with patch("lib.deployment.fsync_tree", side_effect=OSError("release flush failed")), patch.object(
                    self.orchestrator, "_restore_app_units",
                ):
                    with self.assertRaisesRegex(OSError, "release flush failed"):
                        self.deploy(manifest=manifest)
                self.assertEqual((self.dest / "index.html").read_text(), "old")
                marker = "manifest-operation.json" if manifest else "static-operation.json"
                self.assertFalse((self.state / marker).exists())

    def test_failed_port_restore_retains_rejected_release(self):
        self.state.mkdir(parents=True)
        ports = self.state / "manifest-ports.json"
        ports.write_text('{"previous": 8123}')
        from lib.atomic_io import write_json_atomic

        def write(path, value, **kwargs):
            if path == str(ports) and value == {"previous": 8123}:
                raise OSError("port restoration failed")
            return write_json_atomic(path, value, **kwargs)

        with (
            patch("lib.deployment.write_json_atomic", side_effect=write),
            patch("lib.deployment.save_deployment_metadata", side_effect=OSError("metadata failed")),
            patch.object(self.orchestrator, "_restore_app_units"),
        ):
            with self.assertRaisesRegex(RuntimeError, "recovery was incomplete"):
                self.deploy(manifest=True)
        record = OperationStateStore(str(self.state / "manifest-operation.json")).load()
        self.assertEqual((Path(record.context["failed_path"]) / "index.html").read_text(), "new")
        self.assertEqual((self.dest / "index.html").read_text(), "old")

    def test_failed_rollback_completion_retains_rejected_release_and_marker(self):
        with patch.object(OperationStateStore, "complete", side_effect=OSError("completion failed")), patch.object(
            self.orchestrator, "_restore_app_units",
        ):
            with self.assertRaisesRegex(OSError, "completion failed"):
                self.deploy(manifest=True)
        record = OperationStateStore(str(self.state / "manifest-operation.json")).load()
        self.assertEqual((Path(record.context["failed_path"]) / "index.html").read_text(), "new")
        self.assertEqual((self.dest / "index.html").read_text(), "old")

    def test_nested_unit_recovery_keeps_manifest_marker_after_tree_restoration(self):
        self.manifest = parse_manifest({"version": 1, "components": [{
            "name": "api", "type": "service", "domain": "example.com",
            "exec": "/bin/true", "port": 8123,
        }]})
        unit_marker = self.root / "unit-operation.json"
        unit_store = OperationStateStore(str(unit_marker))
        record = unit_store.begin("unit-replacement", "units", "staging")
        unit_store.transition(record.operation_id, "rollback-failed", status="recovery_required")
        unit_store.close()
        with (
            patch.object(self.orchestrator, "_install_service_component", side_effect=UnitRecoveryError(
                str(unit_marker), str(self.root / "unit-backup"),
            )),
            patch.object(self.orchestrator, "_restore_app_units") as restore,
        ):
            with self.assertRaisesRegex(RuntimeError, "Systemd rollback needs recovery"):
                self.deploy(manifest=True)
        restore.assert_called_once_with(str(self.dest), {})
        self.assertEqual((self.dest / "index.html").read_text(), "old")
        manifest_record = OperationStateStore(str(self.state / "manifest-operation.json")).load()
        self.assertEqual(manifest_record.status, "recovery_required")
        self.assertTrue(Path(manifest_record.context["failed_path"]).is_dir())
        self.assertEqual(unit_store.load().operation_id, record.operation_id)
        with self.assertRaises(OperationStateError):
            self.deploy(manifest=True)

    def test_reporting_failure_after_commit_cannot_undo_activation(self):
        def output(message):
            if "Manifest deployed" in message or "Repository deployed" in message:
                raise BrokenPipeError("output disconnected")

        for manifest in (False, True):
            with self.subTest(manifest=manifest):
                with patch("builtins.print", side_effect=output), patch.object(
                    self.orchestrator, "_restore_app_units",
                ) as restore:
                    with self.assertRaises(BrokenPipeError):
                        self.deploy(manifest=manifest)
                self.assertEqual((self.dest / "index.html").read_text(), "new")
                restore.assert_not_called()

    def test_source_and_release_overlap_is_rejected_before_mutation(self):
        for source in (self.dest, self.base, self.dest / "nested"):
            source.mkdir(exist_ok=True)
            for manifest in (False, True):
                with self.subTest(source=source, manifest=manifest):
                    args = (str(source), "example.com", "/", "https://example.test/site.git", "new")
                    with self.assertRaisesRegex(ValueError, "overlap"):
                        if manifest:
                            self.orchestrator.deploy_manifest(self.manifest, *args)
                        else:
                            self.orchestrator.deploy_from_archive(*args)
                    self.assertEqual((self.dest / "index.html").read_text(), "old")
                    self.assertFalse(self.state.exists())

    def test_source_special_file_is_rejected_before_copying_its_contents(self):
        os.mkfifo(self.source / "pipe")
        for manifest in (False, True):
            with self.subTest(manifest=manifest):
                with self.assertRaisesRegex(ValueError, "must be regular"):
                    self.deploy(manifest=manifest)
                self.assertEqual((self.dest / "index.html").read_text(), "old")
                marker = "manifest-operation.json" if manifest else "static-operation.json"
                self.assertFalse((self.state / marker).exists())

    def test_symlink_release_is_rejected_without_changing_link_target(self):
        (self.dest / "index.html").unlink()
        self.dest.rmdir()
        self.dest.symlink_to(self.source, target_is_directory=True)
        for manifest in (False, True):
            with self.subTest(manifest=manifest), self.assertRaisesRegex(ValueError, "not a link"):
                self.deploy(manifest=manifest)
        self.assertEqual((self.source / "index.html").read_text(), "new")

    def test_one_unit_restore_failure_does_not_skip_other_services(self):
        state = {"LoadState": "loaded", "ActiveState": "active", "UnitFileState": "enabled"}
        file = {"content": "old unit", "mode": 0o644, "uid": os.getuid(), "gid": os.getgid()}
        snapshots = {name: {"file": file, "state": state} for name in ("old-api", "old-worker")}
        units = self.root / "units"
        units.mkdir()
        from lib.atomic_io import write_text_atomic

        def write(path, **kwargs):
            if path.endswith("old-api.service"):
                raise OSError("unit restoration failed")
            write_text_atomic(path, **kwargs)

        with (
            patch("lib.deployment.SYSTEMD_UNIT_DIR", str(units)),
            patch("lib.deployment.write_text_atomic", side_effect=write),
            patch("lib.deployment.inspect_unit_state", return_value=state),
            patch.object(self.orchestrator, "_app_unit_names", return_value=set(snapshots)),
            patch.object(self.orchestrator, "_restart_app_units") as restart,
        ):
            with self.assertRaisesRegex(RuntimeError, "recovery was incomplete"):
                self.orchestrator._restore_app_units(str(self.dest), snapshots)
        self.assertEqual((units / "old-worker.service").read_text(), "old unit")
        restart.assert_called_once_with(["old-worker"])

    def test_new_unit_cleanup_failure_does_not_skip_old_service_restoration(self):
        state = {"LoadState": "loaded", "ActiveState": "active", "UnitFileState": "enabled"}
        snapshots = {"old-api": {"state": state, "file": {"content": "old unit"}}}
        with (
            patch("lib.deployment.write_text_atomic") as write,
            patch("lib.deployment.cleanup_service", side_effect=OSError("cleanup failed")),
            patch("lib.deployment.inspect_unit_state", return_value=state),
            patch.object(self.orchestrator, "_app_unit_names", return_value={"new-api", "old-api"}),
            patch.object(self.orchestrator, "_restart_app_units") as restart,
        ):
            with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
                self.orchestrator._restore_app_units(str(self.dest), snapshots)
        write.assert_called_once()
        restart.assert_called_once_with(["old-api"])

    def test_service_restart_timeout_does_not_skip_remaining_units(self):
        def run(command, **kwargs):
            if command == "systemctl restart first.service":
                raise CommandTimeoutError(command, 1)
            return CompletedProcess(command, 0, "", "")

        with patch("lib.deployment.run", side_effect=run) as command:
            with self.assertRaisesRegex(RuntimeError, "first.service recovery failed"):
                self.orchestrator._restart_app_units(["first", "second"])
        self.assertIn("systemctl restart second.service", [call.args[0] for call in command.call_args_list])

    def test_failed_tree_restore_does_not_restart_old_units_against_new_release(self):
        from lib.atomic_io import rename_path_durable

        def rename(source, destination):
            if ".previous-" in source or ".failed-" in destination:
                raise OSError("restoration unavailable")
            rename_path_durable(source, destination)

        with (
            patch("lib.deployment.rename_path_durable", side_effect=rename),
            patch("lib.deployment.save_deployment_metadata", side_effect=OSError("metadata failed")),
            patch.object(self.orchestrator, "_restore_app_units") as restore,
        ):
            with self.assertRaisesRegex(RuntimeError, "recovery was incomplete"):
                self.deploy(manifest=True)
        restore.assert_not_called()
        self.assertEqual((self.dest / "index.html").read_text(), "new")
