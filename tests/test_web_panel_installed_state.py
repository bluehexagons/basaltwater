"""Panel management uses the installer's durable state and convenience alias."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lib import machine_state
from lib.privilege_policy import protected_path


class InstalledStateTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.state = root / "persistent"
        self.state.mkdir(mode=0o700)
        self.alias = root / "runtime-state"
        self.alias.symlink_to(self.state, target_is_directory=True)
        self.setup = self.state / "setup.json"
        self.saved = {"username": "operator", "system_type": "server_lite",
                      "enable_samba": True, "samba_shares": None}
        self.setup.write_text(json.dumps(self.saved))
        self.setup.chmod(0o600)
        for name, value in (
            ("STATE_DIR", str(self.alias)), ("SETUP_CONFIG_FILE", str(self.alias / "setup.json")),
            ("PERSISTENT_STATE_DIR", str(self.state)), ("PERSISTENT_SETUP_CONFIG_FILE", str(self.setup)),
        ):
            self.enterContext(patch.object(machine_state, name, value))
        # Model root-owned protected ancestors without changing host ownership.
        original = Path.lstat
        def metadata(path, *args, **kwargs):
            values = list(original(path, *args, **kwargs))
            values[4] = 0
            if stat.S_ISDIR(values[0]):
                values[0] &= ~0o022
            return os.stat_result(values)
        self.enterContext(patch.object(Path, "lstat", metadata))

    def module(self, name):
        source = Path(__file__).resolve().parents[1] / (name.replace(".", "/") + ".py")
        spec = importlib.util.spec_from_file_location("installed_test_" + name.replace(".", "_"), source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_share_and_routine_exports_use_durable_state_and_share_setup_guard(self):
        with self.assertRaises(ValueError):
            protected_path(str(self.alias), directory=True)
        share = self.module("smb.share_management")
        routine = self.module("sync.routine_management")
        for module in (share, routine):
            self.enterContext(patch.object(module, "load_setup_config", return_value=self.saved))
        with patch.object(share, "_revision", return_value="a" * 64):
            self.assertTrue(share.collect_share_snapshot(self.saved)["available"])
        with patch.object(routine, "CONTROLS_FILE", str(self.state / "absent-controls")), \
             patch.object(routine, "_history", return_value={"available": True, "routines": {}}):
            self.assertTrue(routine.collect_routine_snapshot(self.saved)["available"])
            from lib.operation_state import OperationStateStore
            with OperationStateStore(str(self.state / "setup-operation.json")).ownership() as owner:
                owner.begin("target_setup", "server_lite", "applying")
            self.assertFalse(routine.collect_routine_snapshot(self.saved)["available"])
            self.assertFalse(share.collect_share_snapshot(self.saved)["available"])

    def test_account_execution_and_lifecycle_revision_accept_installed_layout(self):
        account = self.module("smb.account_management")
        row = {"username": "alice", "manageable": True, "uid": 1001,
               "revision": "a" * 64, "disabled": False}
        with patch.object(account.os, "geteuid", return_value=0), \
             patch.object(account, "load_setup_config", return_value=self.saved), \
             patch.object(account, "_accounts", return_value=[row]), \
             patch.object(account, "_reset_password") as reset:
            account.change_account({"action": "reset-password", "username": "alice",
                                    "revision": "a" * 64, "job_id": "b" * 32})
            reset.assert_called_once()
        lifecycle = self.module("smb.account_lifecycle")
        with patch.object(lifecycle, "password_binding", return_value="a" * 64):
            first = lifecycle._creation_revision()
            self.setup.write_text(json.dumps({**self.saved, "friendly_name": "changed"}))
            self.assertNotEqual(first, lifecycle._creation_revision())

    def test_job_status_and_refresh_use_durable_state_with_alias_present(self):
        share = self.module("smb.share_job")
        with patch.object(share, "_record", return_value={}), \
             patch.object(share.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="inactive\n")), \
             patch.object(share, "can_manage_system_services", return_value=True):
            self.assertEqual(share.job_status(), {"available": True, "unit_state": "inactive",
                                                 "latest": {}, "blocked": False})
        refresh = self.module("lib.refresh")
        first = refresh.saved_setup_revision()
        self.setup.write_text(json.dumps({**self.saved, "friendly_name": "changed"}))
        self.assertNotEqual(first, refresh.saved_setup_revision())
