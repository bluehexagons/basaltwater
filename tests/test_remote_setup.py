"""Tests for remote_setup.py argument-file handling."""

from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import MagicMock, mock_open, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import remote_setup
from lib.config import SetupConfig
from lib.machine_state import get_machine_type
from lib.operation_state import OperationStateError, OperationStateStore
from lib.remote_utils import set_dry_run


class TestRemoteSetupArgsFile(unittest.TestCase):
    def tearDown(self):
        remote_setup._active_setup_operation = None

    def test_main_prints_run_notes_after_success(self):
        def fake_run() -> int:
            print("  ⚠ optional browser check was skipped")
            return 0

        with patch.object(remote_setup, "_run_main", side_effect=fake_run), patch.object(
            remote_setup, "_remove_secret_payloads"
        ), patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(remote_setup.main(), 0)

        self.assertIn("Run notes:", output.getvalue())
        self.assertIn("optional browser check was skipped", output.getvalue())

    @patch("remote_setup.send_setup_notification", return_value=False)
    def test_setup_notification_delivery_failure_is_visible(self, _send):
        output = io.StringIO()
        with redirect_stdout(output), patch("sys.stderr", new_callable=io.StringIO) as error_output:
            delivered = remote_setup._send_setup_notification(
                notify_specs=[["webhook", "https://panel.example/hook"]],
                system_type="server_lite",
                host="agent-vm",
                success=True,
            )

        self.assertFalse(delivered)
        self.assertIn("delivery was incomplete", error_output.getvalue())

    def test_remembered_state_is_finalized_only_after_steps_succeed(self):
        args = SimpleNamespace(
            deploy_latest=False,
            dry_run=False,
            custom_steps=None,
            system_type="server_lite",
        )
        config = SetupConfig(
            host="localhost",
            username="root",
            system_type="server_lite",
            machine_type="unprivileged",
        )
        save_machine = MagicMock()
        save_config = MagicMock()

        def step(_config):
            self.assertEqual(get_machine_type(), "unprivileged")
            save_machine.assert_not_called()
            save_config.assert_not_called()

        def get_steps(_config):
            self.assertEqual(get_machine_type(), "unprivileged")
            return [("Apply", step)]

        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            remote_setup,
            "SETUP_OPERATION_FILE",
            os.path.join(tmpdir, "setup-operation.json"),
        ), patch.object(
            remote_setup, "create_setup_argument_parser"
        ) as create_parser, patch.object(
            remote_setup, "config_from_remote_args", return_value=config
        ), patch.object(
            remote_setup, "detect_os", return_value="Debian"
        ), patch.object(
            remote_setup, "print_setup_summary"
        ), patch.object(
            remote_setup, "get_steps_for_system_type", side_effect=get_steps
        ), patch.object(
            remote_setup, "save_machine_state", save_machine
        ), patch.object(
            remote_setup, "save_setup_config", save_config
        ), patch.object(
            remote_setup, "record_setup_activity"
        ), patch(
            "lib.machine_state.load_machine_state",
            return_value={
                "machine_type": "hardware",
                "system_type": "server_lite",
                "username": "root",
            },
        ):
            create_parser.return_value.parse_args.return_value = args
            self.assertEqual(remote_setup._run_main(), 0)
            self.assertIsNone(OperationStateStore(remote_setup.SETUP_OPERATION_FILE).load())
            self.assertEqual(get_machine_type(), "hardware")

        save_machine.assert_called_once()
        save_config.assert_called_once()

    def test_main_records_failed_setup_for_next_invocation(self):
        config = SetupConfig(
            host="localhost",
            username="root",
            system_type="server_lite",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            marker_path = os.path.join(tmpdir, "setup-operation.json")

            def fail_after_begin():
                remote_setup._begin_setup_operation(config)
                remote_setup._transition_setup_operation(
                    "applying",
                    {"step": "Firewall", "step_index": 2, "step_count": 4},
                )
                raise RuntimeError("failed")

            with patch.object(remote_setup, "SETUP_OPERATION_FILE", marker_path), patch.object(
                remote_setup, "record_setup_activity"
            ), patch.object(
                remote_setup, "_run_main", side_effect=fail_after_begin
            ), patch.object(remote_setup, "_remove_secret_payloads"):
                with self.assertRaisesRegex(RuntimeError, "failed"):
                    remote_setup.main()

            record = OperationStateStore(marker_path).load()
            self.assertIsNotNone(record)
            self.assertEqual(record.status, "recovery_required")
            self.assertEqual(record.context["step"], "Firewall")
            self.assertEqual(record.context["error_type"], "RuntimeError")

    def test_matching_failed_setup_is_resumed_for_idempotent_rerun(self):
        config = SetupConfig(
            host="localhost",
            username="agent",
            system_type="agent_code_vm",
            machine_type="vm",
        )
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            remote_setup,
            "SETUP_OPERATION_FILE",
            os.path.join(tmpdir, "setup-operation.json"),
        ), patch.object(
            remote_setup, "record_setup_activity"
        ):
            store = OperationStateStore(remote_setup.SETUP_OPERATION_FILE)
            started = store.begin(
                "target_setup",
                "agent_code_vm",
                "applying",
                context={
                    "machine_type": "vm",
                    "system_type": "agent_code_vm",
                    "username": "agent",
                    "step": "Installing agent browser automation",
                },
            )
            failed = store.transition(
                started.operation_id,
                "recovery",
                status="recovery_required",
                context={
                    **started.context,
                    "step": "Installing agent browser automation",
                    "error_type": "CommandExecutionError",
                },
            )

            store.close()  # The previous invocation has exited.
            remote_setup._begin_setup_operation(config)

            resumed = store.load()
            self.assertIsNotNone(resumed)
            self.assertEqual(resumed.operation_id, failed.operation_id)
            self.assertEqual(resumed.status, "in_progress")
            self.assertEqual(resumed.phase, "applying")
            self.assertEqual(resumed.context["recovery_attempt"], 1)
            self.assertEqual(
                resumed.context["recovered_from"],
                {
                    "error_type": "CommandExecutionError",
                    "phase": "recovery",
                    "step": "Installing agent browser automation",
                },
            )

    def test_failed_setup_for_different_identity_remains_blocked(self):
        config = SetupConfig(
            host="localhost",
            username="other-user",
            system_type="agent_code_vm",
            machine_type="vm",
        )
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            remote_setup,
            "SETUP_OPERATION_FILE",
            os.path.join(tmpdir, "setup-operation.json"),
        ):
            store = OperationStateStore(remote_setup.SETUP_OPERATION_FILE)
            started = store.begin(
                "target_setup",
                "agent_code_vm",
                "applying",
                context={
                    "machine_type": "vm",
                    "system_type": "agent_code_vm",
                    "username": "agent",
                },
            )
            store.transition(
                started.operation_id,
                "recovery",
                status="recovery_required",
            )

            store.close()  # The previous invocation has exited.
            with self.assertRaisesRegex(OperationStateError, started.operation_id):
                remote_setup._begin_setup_operation(config)

    def test_in_progress_setup_remains_blocked(self):
        config = SetupConfig(
            host="localhost",
            username="agent",
            system_type="agent_code_vm",
            machine_type="vm",
        )
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            remote_setup,
            "SETUP_OPERATION_FILE",
            os.path.join(tmpdir, "setup-operation.json"),
        ):
            store = OperationStateStore(remote_setup.SETUP_OPERATION_FILE)
            started = store.begin(
                "target_setup",
                "agent_code_vm",
                "applying",
                context={
                    "machine_type": "vm",
                    "system_type": "agent_code_vm",
                    "username": "agent",
                },
            )

            store.close()  # The previous invocation has exited.
            with self.assertRaisesRegex(OperationStateError, started.operation_id):
                remote_setup._begin_setup_operation(config)

    def test_dry_run_prints_plan_without_invoking_setup_steps(self):
        self.addCleanup(lambda: set_dry_run(False))
        args = SimpleNamespace(
            deploy_latest=False,
            dry_run=True,
            custom_steps=None,
            system_type="server_lite",
        )
        config = SetupConfig(
            host="localhost",
            username="root",
            system_type="server_lite",
            dry_run=True,
        )
        step = MagicMock()

        with patch.object(
            remote_setup, "create_setup_argument_parser"
        ) as create_parser, patch.object(
            remote_setup, "config_from_remote_args", return_value=config
        ), patch.object(
            remote_setup, "detect_os", return_value="Debian"
        ), patch.object(
            remote_setup, "print_setup_summary"
        ), patch.object(
            remote_setup,
            "get_steps_for_system_type",
            return_value=[("Mutating step", step)],
        ):
            create_parser.return_value.parse_args.return_value = args
            self.assertEqual(remote_setup._run_main(), 0)

        step.assert_not_called()

    def test_backup_only_setup_handles_missing_sync_specs(self):
        args = SimpleNamespace(
            deploy_latest=False,
            dry_run=False,
            custom_steps=None,
            system_type="server_lite",
        )
        config = SetupConfig(
            host="localhost",
            username="root",
            system_type="server_lite",
            backup_specs=[["/srv/projects", "/srv/backups/projects", "daily"]],
        )

        with patch.object(
            remote_setup, "create_setup_argument_parser"
        ) as create_parser, patch.object(
            remote_setup, "config_from_remote_args", return_value=config
        ), patch.object(
            remote_setup, "print_setup_summary"
        ), patch.object(
            remote_setup, "detect_os", return_value="Debian"
        ), patch.object(
            remote_setup, "save_machine_state"
        ), patch.object(
            remote_setup, "save_setup_config"
        ), patch.object(
            remote_setup, "_begin_setup_operation"
        ), patch.object(
            remote_setup, "_transition_setup_operation"
        ), patch.object(
            remote_setup, "_complete_setup_operation"
        ), patch.object(
            remote_setup, "get_steps_for_system_type", return_value=[]
        ), patch(
            "sync.sync_steps.install_rsync"
        ) as install_rsync, patch(
            "sync.storage_ops_steps.create_storage_ops_service"
        ) as create_service, patch(
            "sync.storage_ops_steps.schedule_storage_ops_update"
        ) as schedule_update:
            create_parser.return_value.parse_args.return_value = args
            self.assertEqual(remote_setup._run_main(), 0)

        install_rsync.assert_called_once_with(config)
        create_service.assert_called_once_with(config)
        schedule_update.assert_called_once_with()

    def test_resolve_cli_args_loads_and_removes_args_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            args_path = os.path.join(tmpdir, "args.json")
            with open(args_path, "w", encoding="utf-8") as file_obj:
                file_obj.write('["--system-type", "server_lite", "--credential", "mediauser", "supersecret"]\n')

            resolved = remote_setup._resolve_cli_args(["--args-file", args_path, "--dry-run"])

            self.assertEqual(
                resolved,
                ["--system-type", "server_lite", "--credential", "mediauser", "supersecret", "--dry-run"],
            )
            self.assertFalse(os.path.exists(args_path))

    def test_remote_config_resolves_auto_machine_type(self):
        args = SimpleNamespace(custom_steps=None, system_type='server_lite')
        config = SetupConfig(
            host='localhost',
            username='root',
            system_type='server_lite',
            machine_type='auto',
        )
        with patch.object(remote_setup.SetupConfig, 'from_args', return_value=config), \
             patch.object(remote_setup, 'resolve_machine_type', return_value='unprivileged') as resolve:
            resolved = remote_setup.config_from_remote_args(args)

        self.assertIs(resolved, config)
        self.assertEqual(config.machine_type, 'unprivileged')
        resolve.assert_called_once_with('auto')

    def test_remote_config_validates_samba_share_paths(self):
        args = SimpleNamespace(custom_steps=None, system_type='server_lite')
        config = SetupConfig(
            host='localhost',
            username='root',
            system_type='server_lite',
            samba_shares=[['read', 'docs', '/srv/docs,/srv/media', 'shareuser:secret']],
        )
        with patch.object(remote_setup.SetupConfig, 'from_args', return_value=config), \
             patch.object(remote_setup, 'resolve_machine_type', return_value='hardware'):
            with self.assertRaisesRegex(ValueError, 'exactly one path'):
                remote_setup.config_from_remote_args(args)

    def test_load_args_file_rejects_non_list_payload(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            args_path = os.path.join(tmpdir, "args.json")
            with open(args_path, "w", encoding="utf-8") as file_obj:
                file_obj.write('{"bad": "payload"}\n')

            with self.assertRaisesRegex(ValueError, "JSON list"):
                remote_setup._load_args_file(args_path)


class TestRepositorySourcePath(unittest.TestCase):
    def test_default_mode_uses_uploaded_repository_without_remote_clone(self):
        git_url = "git@github.com:owner/private-repo.git"
        repo_path = "/opt/infra_tools/deployments/private-repo"

        def exists(path: str) -> bool:
            return path == repo_path

        with patch.object(remote_setup.os.path, "exists", side_effect=exists):
            result = remote_setup.get_repository_source_path(git_url, "default")

        self.assertEqual(result, (repo_path, ""))

    def test_full_mode_uses_uploaded_repository_commit(self):
        git_url = "https://github.com/owner/app.git"
        repo_path = "/opt/infra_tools/deployments/app"
        commit_path = f"{repo_path}.commit"

        def exists(path: str) -> bool:
            return path in {repo_path, commit_path}

        with patch.object(remote_setup.os.path, "exists", side_effect=exists), \
             patch("builtins.open", mock_open(read_data="abc123\n")):
            result = remote_setup.get_repository_source_path(git_url, "full")

        self.assertEqual(result, (repo_path, "abc123"))

    def test_missing_uploaded_repository_aborts_without_clone(self):
        with patch.object(remote_setup.os.path, "exists", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "Uploaded repository files not found"):
                remote_setup.get_repository_source_path(
                    "https://github.com/owner/app.git",
                    "default",
                )


class TestBuildRuntimeDetection(unittest.TestCase):
    def test_finds_runtime_markers_in_monorepo_children(self):
        with tempfile.TemporaryDirectory() as repo_path:
            os.makedirs(os.path.join(repo_path, "frontend"))
            os.makedirs(os.path.join(repo_path, "services", "api"))
            os.makedirs(os.path.join(repo_path, "node_modules", "ignored"))
            for relative_path in (
                "frontend/package.json",
                "services/api/pyproject.toml",
                "node_modules/ignored/go.mod",
            ):
                path = os.path.join(repo_path, relative_path)
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("{}")

            found = remote_setup._find_project_runtime_files(repo_path)

        self.assertEqual(found["node"], [os.path.join(repo_path, "frontend", "package.json")])
        self.assertEqual(
            found["python"],
            [os.path.join(repo_path, "services", "api", "pyproject.toml")],
        )
        self.assertEqual(found["go"], [])

    def test_enables_go_for_uploaded_go_module(self):
        config = SetupConfig(
            host="localhost",
            username="root",
            system_type="server_lite",
            deploy_specs=[["click.example.com", "https://github.com/example/goclick.git"]],
        )
        with patch.dict(os.environ, {}, clear=False), \
             patch.object(remote_setup.os.path, "isfile", return_value=True), \
             patch("builtins.open", mock_open(read_data="module example.com/app\n\ngo 1.25.0\n")):
            remote_setup.enable_detected_build_runtimes(config)
            self.assertEqual(os.environ["INFRA_TOOLS_GO_VERSION"], "1.25.0")

        self.assertTrue(config.install_go)

    def test_does_not_enable_go_without_uploaded_module(self):
        config = SetupConfig(
            host="localhost",
            username="root",
            system_type="server_lite",
            deploy_specs=[["site.example.com", "https://github.com/example/site.git"]],
        )
        with patch.object(remote_setup.os.path, "isfile", return_value=False):
            remote_setup.enable_detected_build_runtimes(config)

        self.assertFalse(config.install_go)

    def test_enables_node_and_python_from_uploaded_source(self):
        config = SetupConfig(
            host="localhost",
            username="root",
            system_type="server_lite",
            deploy_specs=[["app.example.com", "https://github.com/example/polyglot.git"]],
        )

        def isfile(path: str) -> bool:
            return os.path.basename(path) in {"package.json", "pyproject.toml"}

        with patch.object(remote_setup.os.path, "isfile", side_effect=isfile):
            remote_setup.enable_detected_build_runtimes(config)

        self.assertTrue(config.install_node)
        self.assertTrue(config.install_python)


class TestAgentPayloadCleanup(unittest.TestCase):
    def test_main_removes_agent_payload_after_setup_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            payload_dir = os.path.join(directory, "agent_payload")
            os.makedirs(payload_dir)
            with patch.object(remote_setup, "REMOTE_AGENT_PAYLOAD_DIR", payload_dir), \
                 patch.object(remote_setup, "_run_main", side_effect=RuntimeError("failed")):
                with self.assertRaisesRegex(RuntimeError, "failed"):
                    remote_setup.main()

            self.assertFalse(os.path.exists(payload_dir))


if __name__ == "__main__":
    unittest.main()
