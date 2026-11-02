"""Share-only target state, setup exclusion and interrupted-operation guards."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import remote_setup
from lib.config import SetupConfig
from lib.operation_state import OperationStateError, OperationStateStore
from smb import share_management as management


class SambaRefreshTests(unittest.TestCase):
    def test_saved_refresh_preflights_existing_accounts_before_source_changes(self):
        from lib import refresh

        config = SetupConfig(host="localhost", username="operator", system_type="server_lite",
                             machine_type="vm", enable_samba=True,
                             samba_shares=[["read", "docs", "/srv/docs", "alice"]])
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "setup.json"
            state.write_text("{}")
            state.chmod(0o600)
            with patch.object(refresh, "SETUP_CONFIG_FILE", str(state)), \
                    patch.object(refresh, "saved_setup_revision", return_value="a" * 64), \
                    patch.object(refresh, "_debian_setup", return_value=config), \
                    patch.object(refresh, "is_cachyos", return_value=False), \
                    patch.object(refresh, "read_os_release", return_value={"ID": "debian"}), \
                    patch.object(refresh.os, "geteuid", return_value=0), \
                    patch.object(refresh, "managed_repository_path", return_value=directory), \
                    patch.object(refresh, "get_channel_info", return_value={"channel": "dev"}), \
                    patch.object(refresh, "_apply_refresh") as apply, \
                    patch("lib.refresh_job.queue_refresh") as queue, \
                    patch("smb.samba_steps.preflight_samba_shares") as preflight, \
                    redirect_stdout(StringIO()) as output:
                self.assertEqual(refresh.run_refresh_command(argparse.Namespace(dry_run=True)), 0)
                preflight.assert_called_once()
                self.assertTrue(preflight.call_args.kwargs["preserve_accounts"])
                self.assertIsNone(preflight.call_args.args[0].share_credentials)
                self.assertIn("Configuring Samba and reconciling shares", output.getvalue())
                preflight.side_effect = ValueError("Share user has no matching Samba account")
                self.assertEqual(refresh.run_refresh_command(argparse.Namespace(dry_run=False)), 1)
                apply.assert_not_called()
                queue.assert_not_called()

    def test_fresh_runner_preserves_accounts_only_for_saved_refresh(self):
        from lib.arg_parser import create_setup_argument_parser
        from plugins.post_setup import build_post_setup_steps

        parser = create_setup_argument_parser("test", for_remote=True)
        argv = ["--system-type", "server_lite", "--username", "operator", "--machine", "vm",
                "--samba", "--share", "read", "docs", "/srv/docs", "alice"]
        with self.assertRaisesRegex(ValueError, "Missing credential"):
            remote_setup.config_from_remote_args(parser.parse_args(argv))
        with patch("smb.samba_steps.preflight_samba_shares"):
            config = remote_setup.config_from_remote_args(parser.parse_args(argv), preserve_samba_accounts=True)
        with self.assertRaisesRegex(ValueError, "without passwords"):
            remote_setup.config_from_remote_args(
                parser.parse_args([*argv[:-1], "alice:secret"]), preserve_samba_accounts=True,
            )
        with patch("smb.samba_steps.install_samba"), \
                patch("smb.samba_steps.configure_samba_global_settings"), \
                patch("smb.samba_steps.configure_samba_firewall"), \
                patch("smb.samba_steps.configure_samba_fail2ban"), \
                patch("smb.samba_steps.reconcile_samba_shares") as reconcile:
            for preserve in (False, True):
                steps = build_post_setup_steps(config, lambda *_: ("", ""),
                                               preserve_samba_accounts=preserve)
                next(function for name, function in steps if name == "Configuring Samba and reconciling shares")(config)
                reconcile.assert_called_with(config, preserve_accounts=preserve)


class ShareManagementTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(directory) / "setup.json"
        self.marker = str(Path(directory) / "setup-operation.json")
        Path(self.marker + ".lock").touch(mode=0o600)
        self.saved = {"username": "operator", "system_type": "server_lite", "machine_type": "vm",
                      "enable_samba": True, "samba_shares": [["read", "old", "/srv/old", "alice"]],
                      "samba_metadata_cache": "/srv/samba-cache", "storage_mounts": [["archive", "/srv/archive", "ext4"]],
                      "scrub_specs": [["/srv/docs", "/srv/docs/parity", "10%", "weekly"]],
                      "notify_specs": [["webhook", "https://private.example/hook"]]}
        self.path.write_text(json.dumps(self.saved))
        self.path.chmod(0o600)
        self.before = self.path.read_bytes()
        self.enterContext(patch.object(management, "STATE_DIR", directory))
        self.enterContext(patch.object(management, "SETUP_CONFIG_FILE", str(self.path)))
        self.enterContext(patch.object(management, "load_setup_config", side_effect=lambda: json.loads(self.path.read_text())))
        self.enterContext(patch.object(management, "protected_path"))
        # Model persisted ownership without changing the real runner's files.
        from lib.atomic_io import write_json_atomic
        def write(path, value, **kwargs):
            kwargs.pop("uid", None)
            kwargs.pop("gid", None)
            return write_json_atomic(path, value, **kwargs)
        self.enterContext(patch.object(management, "write_json_atomic", side_effect=write))
        self.reconcile = self.enterContext(patch.object(management, "reconcile_samba_shares"))
        self.preflight = self.enterContext(patch.object(management, "preflight_samba_shares"))

    def config(self, *, credentials=True):
        return SetupConfig(host="localhost", username="operator", system_type="custom_steps", enable_samba=True,
                           samba_shares=[["write", "docs", "/srv/docs", "alice:PRIVATE_PASSWORD" if credentials else "alice"]])

    def test_fresh_target_guards_and_unrelated_settings_survive_share_update(self):
        def reconcile(target, **kwargs):
            self.assertEqual(target.samba_metadata_cache, self.saved["samba_metadata_cache"])
            self.assertEqual(target.storage_mounts, self.saved["storage_mounts"])
            self.assertEqual(target.scrub_specs, self.saved["scrub_specs"])
            self.assertEqual(target.system_type, "server_lite")
            self.assertFalse(kwargs["preserve_accounts"])
            with self.assertRaises(OperationStateError):
                OperationStateStore(self.marker).begin("setup", "host", "applying")
            self.assertEqual(self.path.read_bytes(), self.before)
        self.reconcile.side_effect = reconcile
        management.apply_share_update(self.config())
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved, {**self.saved, "samba_shares": [["write", "docs", "/srv/docs", "alice"]]})
        self.assertFalse(Path(self.marker).exists())
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        history = Path(self.marker + ".last.json").read_text()
        self.assertNotIn("PRIVATE_PASSWORD", history + self.path.read_text())
        self.assertIn('"outcome": "succeeded"', history)

    def test_access_only_or_remove_all_shares_never_requires_stored_passwords(self):
        config = self.config(credentials=False)
        management.apply_share_update(config, preserve_accounts=True)
        self.assertTrue(self.reconcile.call_args.kwargs["preserve_accounts"])
        config.samba_shares = None
        management.apply_share_update(config, preserve_accounts=True)
        self.assertIsNone(json.loads(self.path.read_text())["samba_shares"])

    def test_conflicting_or_unfinished_setup_is_refused_before_any_effect(self):
        with OperationStateStore(self.marker).ownership() as owner:
            with self.assertRaises(OperationStateError):
                management.apply_share_update(self.config())
            owner.begin("target_setup", "server_lite", "applying")
        with self.assertRaisesRegex(ValueError, "unfinished"):
            management.apply_share_update(self.config())
        self.reconcile.assert_not_called()
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_failure_retains_nonsecret_intent_and_does_not_publish_new_saved_state(self):
        self.reconcile.side_effect = RuntimeError("PRIVATE_PASSWORD")
        with self.assertRaises(RuntimeError):
            management.apply_share_update(self.config())
        record = OperationStateStore(self.marker).load()
        self.assertEqual(record.operation_type, "samba_shares")
        self.assertEqual(record.phase, "reconciling")
        self.assertEqual(record.status, "recovery_required")
        self.assertEqual(record.context["samba_shares"], [["write", "docs", "/srv/docs", "alice"]])
        self.assertNotIn("PRIVATE_PASSWORD", Path(self.marker).read_text())
        self.assertEqual(self.path.read_bytes(), self.before)
        self.reconcile.reset_mock()
        with self.assertRaises(ValueError):
            management.apply_share_update(self.config())
        self.reconcile.assert_not_called()

    def test_state_race_does_not_clear_the_recovery_guard(self):
        def changed(*_args, **_kwargs):
            self.path.write_text(json.dumps({**self.saved, "friendly_name": "New setting"}))
        self.reconcile.side_effect = changed
        with self.assertRaisesRegex(ValueError, "changed during"):
            management.apply_share_update(self.config())
        self.assertEqual(json.loads(self.path.read_text())["friendly_name"], "New setting")
        record = OperationStateStore(self.marker).load()
        self.assertEqual(record.phase, "saving")
        self.assertEqual(record.status, "recovery_required")

    def test_save_failure_preserves_previous_setup_and_recovery_evidence(self):
        with patch.object(management, "write_json_atomic", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                management.apply_share_update(self.config())
        self.assertEqual(self.path.read_bytes(), self.before)
        self.assertEqual(OperationStateStore(self.marker).load().status, "recovery_required")
        self.reconcile.assert_called_once()

    def test_mount_or_account_preflight_failure_requires_no_recovery(self):
        self.preflight.side_effect = RuntimeError("Required mount unavailable")
        with self.assertRaises(RuntimeError):
            management.apply_share_update(self.config())
        self.reconcile.assert_not_called()
        self.assertFalse(Path(self.marker).exists())
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_all_legacy_mnt_paths_are_preflighted_before_any_share_preparation(self):
        from smb import samba_steps
        config = self.config()
        config.samba_shares.append(["read", "offline", "/mnt/offline/data", "alice:PRIVATE_PASSWORD"])
        with patch.object(samba_steps.os.path, "exists", return_value=True), \
             patch.object(samba_steps, "assert_declared_storage_mount"), \
             patch.object(samba_steps, "get_mount_ancestor", return_value=None), \
             patch.object(samba_steps, "_prepare_samba_share") as prepare:
            with self.assertRaisesRegex(RuntimeError, "No mounted filesystem"):
                samba_steps.reconcile_samba_shares(config)
        prepare.assert_not_called()

    def test_completion_failure_keeps_new_saved_state_and_guard_for_inspection(self):
        with patch.object(OperationStateStore, "complete", side_effect=OSError("storage failed")):
            with self.assertRaises(OSError):
                management.apply_share_update(self.config())
        self.assertEqual(json.loads(self.path.read_text())["samba_shares"][0][1], "docs")
        self.assertEqual(OperationStateStore(self.marker).load().status, "recovery_required")

    def test_target_cache_overlap_is_rejected_before_a_marker_or_effect(self):
        self.saved["samba_metadata_cache"] = "/srv/docs/cache"
        self.path.write_text(json.dumps(self.saved))
        with self.assertRaisesRegex(ValueError, "overlap"):
            management.apply_share_update(self.config())
        self.reconcile.assert_not_called()
        self.assertFalse(Path(self.marker).exists())

    def test_share_only_remote_entrypoint_and_dry_run_use_the_managed_boundary(self):
        config = self.config()
        config.custom_steps = "reconcile_samba_shares"
        with patch.object(remote_setup, "detect_os", return_value="Debian"), \
             patch.object(management, "apply_share_update") as update, \
             patch.object(remote_setup, "get_steps_for_system_type") as full_setup:
            self.assertEqual(remote_setup._run_configured_setup(config, SimpleNamespace(dry_run=True)), 0)
            update.assert_not_called()
            self.assertEqual(remote_setup._run_configured_setup(config, SimpleNamespace(dry_run=False)), 0)
            update.assert_called_once_with(config)
            full_setup.assert_not_called()

    def reviewed(self):
        return {"before": {"name": "old", "access": "read", "path": "/srv/old", "users": ["alice"]},
                "after": {"name": "old", "access": "write", "path": "/srv/docs", "users": ["alice"]},
                "revision": "a" * 64, "job_id": "b" * 32}

    def test_reviewed_edit_add_and_remove_use_only_fresh_target_shares(self):
        with patch.object(management, "_revision", return_value="a" * 64), \
             patch.object(management, "_accounts", return_value=[{"username": "alice", "manageable": True}]):
            value = self.reviewed()
            management.apply_reviewed_share_update(value)
            self.assertTrue(self.reconcile.call_args.kwargs["preserve_accounts"])
            self.assertIsNone(self.reconcile.call_args.args[0].share_credentials)
            other = {**value["after"], "name": "second", "path": "/srv/second"}
            management.apply_reviewed_share_update({**value, "before": None, "after": other})
            self.assertEqual(len(json.loads(self.path.read_text())["samba_shares"]), 2)
            management.apply_reviewed_share_update({**value, "before": value["after"], "after": None})
            self.assertEqual(json.loads(self.path.read_text())["samba_shares"], [["write", "second", "/srv/second", "alice"]])

    def test_stale_forged_or_privileged_share_reviews_never_reconcile(self):
        value = self.reviewed()
        with patch.object(management, "_revision", return_value="c" * 64), self.assertRaises(ValueError):
            management.apply_reviewed_share_update(value)
        with patch.object(management, "_revision", return_value="a" * 64):
            with self.assertRaises(ValueError):
                management.apply_reviewed_share_update({**value, "before": None})
            with patch.object(management, "_accounts", return_value=[{"username": "alice", "manageable": False}]), self.assertRaises(ValueError):
                management.apply_reviewed_share_update(value)
        self.reconcile.assert_not_called()
        self.assertFalse(Path(self.marker).exists())
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_case_collisions_and_incomplete_inventory_disable_share_snapshot(self):
        self.saved["samba_shares"].append(["read", "OLD", "/srv/other", "alice"])
        with self.assertRaises(ValueError):
            management._rows(self.saved)
        self.assertEqual(management.collect_share_snapshot(self.saved), {"available": False})
