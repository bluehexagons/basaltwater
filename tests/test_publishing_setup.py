"""Engine-independent publishing setup, shared ownership and busy maintenance."""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import publishing_steps as steps
from common.service_tools import auto_update_godot as updater
from lib.arg_parser import create_setup_argument_parser
from lib.config import SetupConfig
from lib.publishing_store import file_lock
from lib.system_types import get_steps_for_system_type


class PublishingSetupTests(unittest.TestCase):
    def test_independent_selection_roundtrips_without_engine(self):
        parser = create_setup_argument_parser("test")
        args = parser.parse_args(["vm.example.test", "--publishing-tool", "butler", "--publishing-tool", "steamcmd"])
        with patch("lib.system_utils.get_current_username", return_value="agent"), patch("lib.system_utils.get_local_timezone", return_value="UTC"):
            config = SetupConfig.from_args(args, "agent_vm")
        self.assertFalse(config.install_godot)
        self.assertEqual(config.publishing_tools, ["butler", "steamcmd"])
        restored = SetupConfig.from_dict(config.host, "agent_vm", config.to_dict())
        self.assertEqual(restored.publishing_tools, config.publishing_tools)
        self.assertIn("--publishing-tool butler", restored.to_remote_args())
        self.assertIn("--publishing-tool steamcmd", restored.to_setup_command())
        names = [name for name, _ in get_steps_for_system_type(config)]
        self.assertTrue(any("publishing" in name.lower() for name in names))
        self.assertNotIn("Installing Godot Engine (latest stable)", names)

    def test_invalid_or_root_selection_rejected_and_dry_run_has_no_system_calls(self):
        for values in ({"username": "root", "publishing_tools": ["butler"]},
                       {"username": "agent", "publishing_tools": ["invalid"]}):
            with self.assertRaises(ValueError):
                SetupConfig(host="vm.example.test", system_type="agent_vm", **values)
        config = SetupConfig(host="vm.example.test", username="agent", system_type="agent_vm", publishing_tools=["butler"])
        with patch.object(steps, "is_dry_run", return_value=True), patch.object(steps, "run") as run, patch.object(steps, "install_selected_tools") as install:
            steps.install_publishing_tools(config)
        run.assert_not_called()
        install.assert_not_called()

    def test_butler_setup_locks_existing_independent_and_legacy_owners(self):
        acquired = []
        @contextmanager
        def lock(user, provider):
            acquired.append((user, provider))
            yield
        with patch.object(steps, "maintenance_lock", side_effect=lock), patch.object(steps, "selections", return_value={"old": ["butler"]}), \
                patch("common.godot_steps._validated_registered_bundles", return_value=(["publishing"], ["legacy"], [])), \
                patch("common.godot_steps.install_or_update_butler_release", return_value=("1", True, "hash")) as install:
            self.assertTrue(steps.install_selected_tools({"new": ["butler"]}))
        self.assertEqual(acquired, [(user, "butler") for user in ("legacy", "new", "old")])
        install.assert_called_once()

    def test_maintenance_respects_same_provider_lease(self):
        with tempfile.TemporaryDirectory() as home:
            root = Path(home) / ".local/share/basaltwater/publishing"
            root.mkdir(parents=True, mode=0o700)
            account = SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())
            with patch.object(steps.pwd, "getpwnam", return_value=account), patch.object(steps, "get_user_home", return_value=home), \
                    patch.object(steps, "_run_as_login_user", return_value=SimpleNamespace(returncode=0)), \
                    file_lock(root / "butler.lock"):
                with self.assertRaises(BlockingIOError), steps.maintenance_lock("agent", "butler"):
                    self.fail("Maintenance acquired an active native lease")

    def test_shared_updater_runs_without_godot_and_busy_provider_is_deferred(self):
        with patch.object(updater.os.path, "exists", side_effect=lambda path: path == steps.PUBLISHING_TOOL_STATE), \
                patch.object(updater, "load_notification_configs_from_state", return_value=[]), patch.object(updater, "log_event"), \
                patch.object(updater, "send_notification_safe"), patch.object(updater, "install_or_update_godot_release") as engine, \
                patch.object(updater, "update_registered_godot_bundles") as bundles, \
                patch.object(updater, "update_registered_publishing_tools", return_value=True) as publishing:
            self.assertEqual(updater.main(), 0)
        engine.assert_not_called()
        bundles.assert_not_called()
        publishing.assert_called_once()
        with patch.object(steps, "maintenance_lock", side_effect=BlockingIOError), patch.object(steps, "selections", return_value={}), \
                patch("common.godot_steps._validated_registered_bundles", return_value=([], [], [])), \
                patch("common.godot_steps.install_or_update_butler_release") as install:
            self.assertFalse(steps.install_selected_tools({"agent": ["butler"]}, busy_skip=True))
        install.assert_not_called()
