"""Tests for lib/setup_common.py: setup_main timing/status persistence."""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.config import SetupConfig
from lib.proxmox_hosts import ProxmoxHost, ProxmoxHostFacts, add_proxmox_host


def _make_config(**kwargs) -> SetupConfig:
    defaults = dict(host='testhost', username='testuser', system_type='server_lite')
    defaults.update(kwargs)
    return SetupConfig(**defaults)


class TestSetupMainTimingPersistence(unittest.TestCase):
    """Verify setup_main always saves last_start_time/end_time/success."""

    def test_remote_setup_records_generated_access_details(self):
        from lib import setup_common

        device_id = (
            "BJ5ID3D-3BL2IM7-KPTHNB3-LI3SO5N-"
            "KDCFYJN-Z4HKBUQ-AIANLCB-LJOJXAT"
        )
        setup_common._LAST_REMOTE_ACCESS_DETAILS.clear()
        setup_common._record_remote_access_output(
            "  T3 Code HTTPS endpoint: https://192.0.2.10:8444/\n"
        )
        setup_common._record_remote_access_output(
            "  T3 Code pairing HTTPS endpoint: https://192.0.2.10:8445/\n"
        )
        setup_common._record_remote_access_output(
            f"  ✓ Syncthing device ID: {device_id}\n"
        )
        setup_common._record_remote_access_output(
            "  ✓ Syncthing HTTPS admin: https://192.0.2.10:8446/\n"
        )

        self.assertEqual(
            setup_common.get_last_remote_access_details(),
            {
                "t3code": "https://192.0.2.10:8444/",
                "t3code-pairing": "https://192.0.2.10:8445/",
                "syncthing-admin": "https://192.0.2.10:8446/",
                "syncthing-device-id": device_id,
            },
        )

    def test_remote_access_capture_rejects_untrusted_values(self):
        from lib import setup_common

        setup_common._LAST_REMOTE_ACCESS_DETAILS.clear()
        setup_common._record_remote_access_output(
            "  ✓ Syncthing HTTPS admin: http://192.0.2.10:8384/\n"
        )
        setup_common._record_remote_access_output(
            "  ✓ Syncthing device ID: not-a-device-id\n"
        )

        self.assertEqual(setup_common.get_last_remote_access_details(), {})

    def test_success_saves_timing_and_success_true(self):
        with tempfile.TemporaryDirectory():
            from lib import setup_common
            config = _make_config()
            saved_calls = []

            def fake_save(cfg, start_time=None, end_time=None, success=None, **kwargs):
                saved_calls.append(
                    {
                        'start_time': start_time,
                        'end_time': end_time,
                        'success': success,
                        'operation': kwargs.get('operation'),
                    }
                )

            with patch.object(setup_common, 'run_remote_setup', return_value=0), \
                 patch.object(setup_common, 'validate_host', return_value=True), \
                 patch.object(setup_common, 'validate_username', return_value=True), \
                 patch.object(setup_common, 'validate_samba_share_credentials'), \
                 patch.object(setup_common, 'print_setup_summary'), \
                 patch.object(setup_common, 'store_cli_credentials'), \
                 patch.object(setup_common, 'save_setup_command', side_effect=fake_save):

                parser = MagicMock()
                args = MagicMock()
                args.host = 'testhost'
                args.username = 'testuser'
                args.dry_run = False
                parser.parse_args.return_value = args

                with patch.object(setup_common, 'create_argument_parser', return_value=parser), \
                     patch('lib.config.SetupConfig.from_args', return_value=config):
                    setup_common.setup_main('server_lite', 'Test', lambda c: None)

            # Two saves expected: first is the pre-run config-only save (no timing),
            # second is the post-run save with start_time/end_time/success.
            self.assertEqual(len(saved_calls), 2)
            self.assertEqual(saved_calls[0]['operation'], 'setup')
            post_run = saved_calls[1]
            self.assertIsNotNone(post_run['start_time'])
            self.assertIsNotNone(post_run['end_time'])
            self.assertIs(post_run['success'], True)
            self.assertEqual(post_run['operation'], 'setup')

    def test_failure_saves_timing_and_success_false(self):
        with tempfile.TemporaryDirectory():
            from lib import setup_common
            config = _make_config()
            saved_calls = []

            def fake_save(cfg, start_time=None, end_time=None, success=None, **kwargs):
                saved_calls.append(
                    {
                        'start_time': start_time,
                        'end_time': end_time,
                        'success': success,
                        'operation': kwargs.get('operation'),
                    }
                )

            with patch.object(setup_common, 'run_remote_setup', return_value=1), \
                 patch.object(setup_common, 'validate_host', return_value=True), \
                 patch.object(setup_common, 'validate_username', return_value=True), \
                 patch.object(setup_common, 'validate_samba_share_credentials'), \
                 patch.object(setup_common, 'print_setup_summary'), \
                 patch.object(setup_common, 'store_cli_credentials'), \
                 patch.object(setup_common, 'save_setup_command', side_effect=fake_save):

                parser = MagicMock()
                args = MagicMock()
                args.host = 'testhost'
                args.username = 'testuser'
                args.dry_run = False
                parser.parse_args.return_value = args

                with patch.object(setup_common, 'create_argument_parser', return_value=parser), \
                     patch('lib.config.SetupConfig.from_args', return_value=config):
                    result = setup_common.setup_main('server_lite', 'Test', lambda c: None)

            self.assertEqual(result, 1)
            post_run = saved_calls[1]
            self.assertIsNotNone(post_run['start_time'])
            self.assertIsNotNone(post_run['end_time'])
            self.assertIs(post_run['success'], False)
            self.assertEqual(post_run['operation'], 'setup')

    def test_exception_saves_timing_and_success_false(self):
        """Verifies that even if run_remote_setup raises, success=False is saved."""
        with tempfile.TemporaryDirectory():
            from lib import setup_common
            config = _make_config()
            saved_calls = []

            def fake_save(cfg, start_time=None, end_time=None, success=None, **kwargs):
                saved_calls.append(
                    {
                        'start_time': start_time,
                        'end_time': end_time,
                        'success': success,
                        'operation': kwargs.get('operation'),
                    }
                )

            with patch.object(setup_common, 'run_remote_setup', side_effect=RuntimeError('boom')), \
                 patch.object(setup_common, 'validate_host', return_value=True), \
                 patch.object(setup_common, 'validate_username', return_value=True), \
                 patch.object(setup_common, 'validate_samba_share_credentials'), \
                 patch.object(setup_common, 'print_setup_summary'), \
                 patch.object(setup_common, 'store_cli_credentials'), \
                 patch.object(setup_common, 'save_setup_command', side_effect=fake_save):

                parser = MagicMock()
                args = MagicMock()
                args.host = 'testhost'
                args.username = 'testuser'
                args.dry_run = False
                parser.parse_args.return_value = args

                with patch.object(setup_common, 'create_argument_parser', return_value=parser), \
                     patch('lib.config.SetupConfig.from_args', return_value=config):
                    with self.assertRaises(RuntimeError):
                        setup_common.setup_main('server_lite', 'Test', lambda c: None)

            # Post-run save must happen even after exception
            self.assertEqual(len(saved_calls), 2)
            post_run = saved_calls[1]
            self.assertIsNotNone(post_run['start_time'])
            self.assertIsNotNone(post_run['end_time'])
            self.assertIs(post_run['success'], False)
            self.assertEqual(post_run['operation'], 'setup')

    def test_dry_run_skips_post_run_save(self):
        """In dry-run mode, save_setup_command should never be called."""
        with tempfile.TemporaryDirectory():
            from lib import setup_common
            config = _make_config(dry_run=True)
            saved_calls = []

            def fake_save(cfg, start_time=None, end_time=None, success=None, **kwargs):
                saved_calls.append(
                    {
                        'start_time': start_time,
                        'end_time': end_time,
                        'success': success,
                        'operation': kwargs.get('operation'),
                    }
                )

            with patch.object(setup_common, 'run_remote_setup', return_value=0), \
                 patch.object(setup_common, 'validate_host', return_value=True), \
                 patch.object(setup_common, 'validate_username', return_value=True), \
                 patch.object(setup_common, 'validate_samba_share_credentials'), \
                 patch.object(setup_common, 'print_setup_summary'), \
                 patch.object(setup_common, 'save_setup_command', side_effect=fake_save):

                parser = MagicMock()
                args = MagicMock()
                args.host = 'testhost'
                args.username = 'testuser'
                args.dry_run = True
                parser.parse_args.return_value = args

                with patch.object(setup_common, 'create_argument_parser', return_value=parser), \
                     patch('lib.config.SetupConfig.from_args', return_value=config):
                    setup_common.setup_main('server_lite', 'Test', lambda c: None)

            self.assertEqual(saved_calls, [])


class TestRunRemoteSetupArgumentSecurity(unittest.TestCase):
    def setUp(self):
        sudo = patch("lib.setup_common.ensure_remote_sudo", return_value=True)
        sudo.start()
        self.addCleanup(sudo.stop)

    def test_copy_project_files_includes_runtime_packages(self):
        from lib import setup_common

        with (
            patch.object(setup_common.shutil, "copytree") as copytree,
            patch.object(setup_common.shutil, "copy2"),
            patch.object(setup_common, "write_setup_snapshot_metadata"),
        ):
            tmpdir = "/payload"
            setup_common.copy_project_files(tmpdir)
        copied_names = {os.path.basename(call.args[0]) for call in copytree.call_args_list}
        self.assertTrue({"plugins", "game"}.issubset(copied_names))

    def test_write_remote_args_file_uses_secure_json_file(self):
        from lib import setup_common

        with tempfile.TemporaryDirectory() as tmpdir:
            path = setup_common._write_remote_args_file(tmpdir, ["--credential", "mediauser", "supersecret"])

            with open(path, encoding="utf-8") as file_obj:
                self.assertEqual(file_obj.read().strip(), '["--credential", "mediauser", "supersecret"]')
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_remote_ssh_command_uses_args_file_without_inline_passwords(self):
        from lib import setup_common

        config = _make_config(
            host="example.com",
            share_credentials=[["mediauser", "supersecret"]],
        )
        with patch.object(setup_common, "copy_project_files"), \
             patch.object(setup_common, "prepare_deployments"), \
             patch.object(setup_common, "build_ssh_command", return_value=["ssh"]) as mock_build_ssh, \
             patch.object(setup_common, "run_streamed", return_value=0):
            result = setup_common.run_remote_setup(config)

        self.assertEqual(result, 0)
        remote_command = mock_build_ssh.call_args.kwargs["remote_command"]
        self.assertIn("--args-file", remote_command)
        self.assertNotIn("supersecret", remote_command)
        self.assertTrue(remote_command.startswith("timeout --signal=TERM --kill-after=10s 14400 "))

    def test_remote_ssh_command_preserves_state_before_replacing_runtime(self):
        from lib import setup_common

        config = _make_config(host="example.com")
        with patch.object(setup_common, "copy_project_files"), \
             patch.object(setup_common, "prepare_deployments"), \
             patch.object(setup_common, "build_ssh_command", return_value=["ssh"]) as mock_build_ssh, \
             patch.object(setup_common, "run_streamed", return_value=0):
            result = setup_common.run_remote_setup(config)

        self.assertEqual(result, 0)
        remote_command = mock_build_ssh.call_args.kwargs["remote_command"]
        self.assertIn("install -d -m 0700 /var/lib/infra_tools", remote_command)
        self.assertIn(
            "cp -a /opt/infra_tools/state/. /var/lib/infra_tools/",
            remote_command,
        )
        self.assertIn(
            "setup-operation.pre-persistence.json",
            remote_command,
        )
        self.assertIn("rm -rf /opt/infra_tools && mkdir -p /opt/infra_tools", remote_command)
        self.assertLess(
            remote_command.index("cp -a /opt/infra_tools/state/."),
            remote_command.index("rm -rf /opt/infra_tools"),
        )
        self.assertLess(remote_command.index("rm -rf"), remote_command.index("tar xzf -"))
        self.assertIn(
            "ln -s /var/lib/infra_tools /opt/infra_tools/state",
            remote_command,
        )
        self.assertLess(
            remote_command.index("tar xzf -"),
            remote_command.index("ln -s /var/lib/infra_tools"),
        )
        self.assertIn("chmod 0755 /opt/infra_tools", remote_command)
        self.assertLess(
            remote_command.index("tar xzf -"),
            remote_command.index("chmod 0755 /opt/infra_tools"),
        )

    def test_remote_setup_finishes_verified_network_transition_after_ssh_exits(self):
        from lib import setup_common

        config = _make_config(
            host="192.168.10.20",
            static_ipv4="192.168.10.21/24",
            activate_network=True,
        )
        with patch.object(setup_common, "copy_project_files"), \
             patch.object(setup_common, "build_ssh_command", return_value=["ssh"]), \
             patch.object(setup_common, "finish_network_transition", return_value=0) as mock_finish, \
             patch.object(setup_common, "run_streamed", return_value=0):
            result = setup_common.run_remote_setup(config)

        self.assertEqual(result, 0)
        mock_finish.assert_called_once_with(config, 0)

    def test_hosted_vm_nopasswd_still_uses_retained_root_ssh(self):
        from lib import setup_common

        config = _make_config(
            host="192.168.10.20",
            username="agent",
            machine_type="vm",
            hosted_node="pve1",
            nopasswd=True,
        )
        with patch.object(setup_common, "copy_project_files"), \
             patch.object(setup_common, "build_ssh_command", return_value=["ssh"]) as mock_build, \
             patch.object(setup_common, "run_streamed", return_value=0):
            result = setup_common.run_remote_setup(config)

        self.assertEqual(result, 0)
        self.assertEqual(mock_build.call_args.args[1], "root")
        self.assertEqual(
            mock_build.call_args.kwargs["batch_mode"],
            not sys.stdin.isatty(),
        )
        remote_command = mock_build.call_args.kwargs["remote_command"]
        self.assertIn("rm -rf /opt/infra_tools", remote_command)
        self.assertIn("tar xzf - -C /opt/infra_tools", remote_command)
        self.assertIn("python3 -u /opt/infra_tools/remote_setup.py", remote_command)
        self.assertNotIn("sudo -n", remote_command)

    def test_hosted_vm_without_nopasswd_uses_retained_root_ssh(self):
        from lib import setup_common

        config = _make_config(
            host="192.168.10.20",
            username="agent",
            machine_type="vm",
            hosted_node="pve1",
        )
        with patch.object(setup_common, "copy_project_files"), \
             patch.object(setup_common, "build_ssh_command", return_value=["ssh"]) as mock_build, \
             patch.object(setup_common, "run_streamed", return_value=0):
            result = setup_common.run_remote_setup(config)

        self.assertEqual(result, 0)
        self.assertEqual(mock_build.call_args.args[1], "root")
        remote_command = mock_build.call_args.kwargs["remote_command"]
        self.assertIn("rm -rf /opt/infra_tools", remote_command)
        self.assertIn("python3 -u /opt/infra_tools/remote_setup.py", remote_command)
        self.assertNotIn("sudo -n", remote_command)

    def test_adopts_only_a_controller_verified_replacement_host(self):
        from lib import setup_common

        saved_config = _make_config(
            host="192.168.10.20",
            static_ipv4="192.168.10.21/24",
            activate_network=True,
        )
        runtime_config = _make_config(
            host="192.168.10.21",
            static_ipv4="192.168.10.21/24",
            activate_network=True,
        )

        previous = setup_common.adopt_verified_network_host(
            saved_config,
            runtime_config,
            "192.168.10.20",
        )

        self.assertEqual(previous, "192.168.10.20")
        self.assertEqual(saved_config.host, "192.168.10.21")
        self.assertFalse(saved_config.activate_network)

    def test_local_install_dir_is_traversable_after_copy(self):
        from lib import setup_common

        config = _make_config(host="localhost")
        with tempfile.TemporaryDirectory() as temp_dir:
            install_dir = os.path.join(temp_dir, "infra_tools")
            state_dir = os.path.join(temp_dir, "state")
            with patch.object(setup_common, "REMOTE_INSTALL_DIR", install_dir), \
                 patch.object(setup_common, "PERSISTENT_STATE_DIR", state_dir), \
                 patch.object(setup_common, "copy_project_files"), \
                 patch.object(setup_common.os, "geteuid", return_value=0), \
                 patch.object(setup_common, "run_streamed", return_value=0) as mock_stream:
                result = setup_common.run_remote_setup(config)

            self.assertEqual(result, 0)
            self.assertEqual(os.stat(install_dir).st_mode & 0o777, 0o755)
            self.assertEqual(mock_stream.call_args.kwargs["env"]["PYTHONUNBUFFERED"], "1")
            self.assertEqual(mock_stream.call_args.kwargs["timeout"], 14400)

    def test_local_setup_preserves_managed_git_worktree(self):
        from lib import setup_common

        with tempfile.TemporaryDirectory() as temp_dir:
            managed_dir = os.path.join(temp_dir, "infra_tools")
            state_dir = os.path.join(temp_dir, "state")
            os.makedirs(os.path.join(managed_dir, ".git"))
            with open(os.path.join(managed_dir, "keep-me"), "w", encoding="utf-8") as file_obj:
                file_obj.write("managed")

            build_dir = os.path.join(temp_dir, "build")
            os.makedirs(os.path.join(build_dir, "deployments"))
            with open(os.path.join(build_dir, "deployments", "repo"), "w", encoding="utf-8") as file_obj:
                file_obj.write("payload")
            with open(os.path.join(build_dir, setup_common.REMOTE_ARGS_FILENAME), "w", encoding="utf-8") as file_obj:
                file_obj.write("[]")

            with patch.object(setup_common, "SCRIPT_DIR", os.path.join(managed_dir, "lib")), \
                 patch.object(setup_common, "REMOTE_INSTALL_DIR", managed_dir), \
                 patch.object(setup_common, "PERSISTENT_STATE_DIR", state_dir):
                setup_common._activate_local_runtime(build_dir)

            self.assertTrue(os.path.isdir(os.path.join(managed_dir, ".git")))
            self.assertTrue(os.path.isfile(os.path.join(managed_dir, "keep-me")))
            self.assertTrue(os.path.isfile(os.path.join(managed_dir, "deployments", "repo")))
            self.assertTrue(os.path.isfile(os.path.join(managed_dir, setup_common.REMOTE_ARGS_FILENAME)))
            self.assertTrue(os.path.islink(os.path.join(managed_dir, "state")))

    def test_local_runtime_migrates_state_before_replacing_source_tree(self):
        from lib import setup_common

        with tempfile.TemporaryDirectory() as temp_dir:
            install_dir = os.path.join(temp_dir, "infra_tools")
            legacy_state_dir = os.path.join(install_dir, "state")
            persistent_state_dir = os.path.join(temp_dir, "persistent-state")
            build_dir = os.path.join(temp_dir, "build")
            os.makedirs(legacy_state_dir)
            os.makedirs(build_dir)
            with open(
                os.path.join(legacy_state_dir, "godot.json"),
                "w",
                encoding="utf-8",
            ) as file_obj:
                file_obj.write('{"tag_name":"4.7.2-stable"}')
            with open(
                os.path.join(legacy_state_dir, "setup-operation.json"),
                "w",
                encoding="utf-8",
            ) as file_obj:
                file_obj.write('{"status":"recovery_required"}')
            with open(
                os.path.join(build_dir, "remote_setup.py"),
                "w",
                encoding="utf-8",
            ) as file_obj:
                file_obj.write("# replacement runtime\n")

            with (
                patch.object(setup_common, "REMOTE_INSTALL_DIR", install_dir),
                patch.object(
                    setup_common,
                    "PERSISTENT_STATE_DIR",
                    persistent_state_dir,
                ),
            ):
                setup_common._activate_local_runtime(build_dir)

            state_link = os.path.join(install_dir, "state")
            self.assertTrue(os.path.islink(state_link))
            self.assertEqual(os.path.realpath(state_link), persistent_state_dir)
            with open(
                os.path.join(persistent_state_dir, "godot.json"),
                encoding="utf-8",
            ) as file_obj:
                self.assertEqual(file_obj.read(), '{"tag_name":"4.7.2-stable"}')
            self.assertFalse(
                os.path.exists(
                    os.path.join(persistent_state_dir, "setup-operation.json")
                )
            )
            self.assertTrue(
                os.path.isfile(
                    os.path.join(
                        persistent_state_dir,
                        setup_common.LEGACY_SETUP_OPERATION_FILENAME,
                    )
                )
            )

    def test_local_runtime_preserves_new_durable_operation_marker(self):
        from lib import setup_common

        with tempfile.TemporaryDirectory() as temp_dir:
            install_dir = os.path.join(temp_dir, "infra_tools")
            persistent_state_dir = os.path.join(temp_dir, "persistent-state")
            build_dir = os.path.join(temp_dir, "build")
            os.makedirs(install_dir)
            os.makedirs(persistent_state_dir)
            os.makedirs(build_dir)
            os.symlink(
                persistent_state_dir,
                os.path.join(install_dir, "state"),
                target_is_directory=True,
            )
            operation_marker = os.path.join(
                persistent_state_dir,
                "setup-operation.json",
            )
            with open(operation_marker, "w", encoding="utf-8") as file_obj:
                file_obj.write('{"status":"recovery_required"}')

            with (
                patch.object(setup_common, "REMOTE_INSTALL_DIR", install_dir),
                patch.object(
                    setup_common,
                    "PERSISTENT_STATE_DIR",
                    persistent_state_dir,
                ),
            ):
                setup_common._activate_local_runtime(build_dir)

            self.assertTrue(os.path.isfile(operation_marker))
            self.assertFalse(
                os.path.exists(
                    os.path.join(
                        persistent_state_dir,
                        setup_common.LEGACY_SETUP_OPERATION_FILENAME,
                    )
                )
            )


class TestAgentCredentialStaging(unittest.TestCase):
    def test_active_github_auth_reads_keyring_token_through_gh(self):
        from lib import setup_common

        config = _make_config(
            agent_tools=["gh"],
            copy_agent_keys=True,
            git_access="read",
            git_auth_source="active",
        )
        with tempfile.TemporaryDirectory() as directory:
            home = os.path.join(directory, "home")
            payload_dir = os.path.join(directory, "payload")
            hosts_path = os.path.join(home, ".config", "gh", "hosts.yml")
            os.makedirs(os.path.dirname(hosts_path))
            with open(hosts_path, "w", encoding="utf-8") as file_obj:
                file_obj.write(
                    "github.com:\n"
                    "    user: octocat\n"
                    "    oauth_token: null\n"
                    "    git_protocol: https\n"
                )
            os.chmod(hosts_path, 0o600)
            result = type(
                "Completed",
                (),
                {"returncode": 0, "stdout": "keyring-token\n", "stderr": ""},
            )()

            with (
                patch("lib.setup_common.shutil.which", return_value="/usr/bin/gh"),
                patch("lib.setup_common.subprocess.run", return_value=result) as run,
            ):
                setup_common._stage_github_auth(config, payload_dir, home)

            payload_path = os.path.join(payload_dir, "secrets", "gh", "hosts.yml")
            with open(payload_path, encoding="utf-8") as file_obj:
                payload = file_obj.read()
            self.assertIn("keyring-token", payload)
            self.assertIn("user: octocat", payload)
            self.assertEqual(payload.count("oauth_token:"), 1)
            run.assert_called_once_with(
                ["/usr/bin/gh", "auth", "token", "--hostname", "github.com"],
                check=False,
                capture_output=True,
                text=True,
                timeout=20,
            )

    def test_active_github_auth_explains_missing_controller_gh(self):
        from lib import setup_common

        config = _make_config(
            agent_tools=["gh"],
            copy_agent_keys=True,
            git_access="read",
            git_auth_source="active",
        )
        with tempfile.TemporaryDirectory() as directory:
            with patch("lib.setup_common.shutil.which", return_value=None):
                with self.assertRaisesRegex(ValueError, "gh is not installed"):
                    setup_common._stage_github_auth(
                        config,
                        os.path.join(directory, "payload"),
                        os.path.join(directory, "home"),
                    )

    def test_github_agent_auth_file_accepts_one_line_token(self):
        from lib import setup_common

        config = _make_config(
            agent_tools=["gh"],
            copy_agent_keys=True,
            git_access="read",
            agent_auth_files=[],
        )
        with tempfile.TemporaryDirectory() as directory:
            token_path = os.path.join(directory, "github-token")
            with open(token_path, "w", encoding="utf-8") as file_obj:
                file_obj.write("per-vm-token\n")
            os.chmod(token_path, 0o600)
            config.agent_auth_files = [["gh", token_path]]
            payload_dir = os.path.join(directory, "payload")

            setup_common.prepare_agent_payload(config, payload_dir)

            with open(
                os.path.join(payload_dir, "secrets", "gh", "hosts.yml"),
                encoding="utf-8",
            ) as file_obj:
                self.assertIn("per-vm-token", file_obj.read())

    def test_missing_active_codex_file_mentions_file_backend(self):
        from lib import setup_common

        config = _make_config(
            agent_tools=["codex"],
            copy_agent_keys=True,
            agent_auth_source="active",
        )
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(setup_common, "_local_user_home", return_value=directory):
                with self.assertRaisesRegex(ValueError, "cli_auth_credentials_store"):
                    setup_common.prepare_agent_payload(
                        config,
                        os.path.join(directory, "payload"),
                    )


class TestCloneRepository(unittest.TestCase):
    def test_dry_run_clones_into_disposable_staging_for_preflight(self):
        from lib import setup_common

        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = os.path.join(tmpdir, "work")
            os.makedirs(work_dir)
            clone_path = os.path.join(work_dir, "repo")

            def clone_result(command, **_kwargs):
                os.makedirs(clone_path)
                return MagicMock(returncode=0, stdout="", stderr="")

            with patch("subprocess.run", side_effect=clone_result) as mock_run, \
                 patch("lib.deploy_utils.get_git_commit_hash", return_value="abc123"):
                result = setup_common.clone_repository(
                    "https://git.example.com/repo.git",
                    work_dir,
                    cache_dir=os.path.join(tmpdir, "cache"),
                    dry_run=True,
                )

        self.assertEqual(result, (clone_path, "abc123"))
        self.assertEqual(
            mock_run.call_args.args[0],
            [
                "git",
                "clone",
                "--depth",
                "1",
                "https://git.example.com/repo.git",
                clone_path,
            ],
        )

    def test_repository_name_cannot_escape_work_directory(self):
        from lib import setup_common

        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = os.path.join(tmpdir, "work")
            os.makedirs(work_dir)
            sentinel = os.path.join(tmpdir, "sentinel")
            with open(sentinel, "w", encoding="utf-8") as file_obj:
                file_obj.write("keep")

            for git_url in (
                "https://git.example.com/.",
                "https://git.example.com/..",
            ):
                with self.subTest(git_url=git_url):
                    self.assertIsNone(setup_common.clone_repository(git_url, work_dir))

            with open(sentinel, encoding="utf-8") as file_obj:
                self.assertEqual(file_obj.read(), "keep")

    def test_existing_cache_is_cleaned_after_reset(self):
        from lib import setup_common

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = os.path.join(tmpdir, "cache")
            work_dir = os.path.join(tmpdir, "work")
            git_url = "https://git.example.com/repo.git"
            cache_digest = hashlib.sha256(git_url.encode("utf-8")).hexdigest()[:16]
            cache_repo = os.path.join(cache_dir, f"repo-{cache_digest}")
            os.makedirs(cache_repo)
            os.makedirs(work_dir)
            with open(os.path.join(cache_repo, "index.html"), "w", encoding="utf-8") as file_obj:
                file_obj.write("ok")

            run_results = [
                MagicMock(returncode=0),
                MagicMock(returncode=0, stdout="origin/main\n"),
                MagicMock(returncode=0),
                MagicMock(returncode=0),
            ]

            with patch("subprocess.run", side_effect=run_results) as mock_run, \
                 patch("lib.deploy_utils.get_git_commit_hash", return_value="abc123"):
                result = setup_common.clone_repository(
                    git_url,
                    work_dir,
                    cache_dir=cache_dir,
                )

        self.assertEqual(result, (os.path.join(work_dir, "repo"), "abc123"))
        commands = [call.args[0] for call in mock_run.call_args_list]
        self.assertIn(["git", "-C", cache_repo, "reset", "--hard", "origin/main"], commands)
        self.assertIn(["git", "-C", cache_repo, "clean", "-fdx"], commands)
        self.assertLess(
            commands.index(["git", "-C", cache_repo, "reset", "--hard", "origin/main"]),
            commands.index(["git", "-C", cache_repo, "clean", "-fdx"]),
        )

    def test_cache_path_distinguishes_same_named_repositories(self):
        from lib.setup_common import _repository_cache_path

        first = _repository_cache_path(
            "/cache",
            "https://github.com/one/repo.git",
            "repo",
        )
        second = _repository_cache_path(
            "/cache",
            "https://github.com/two/repo.git",
            "repo",
        )

        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("/cache/repo-"))
        self.assertTrue(second.startswith("/cache/repo-"))

class TestSetupMainValidation(unittest.TestCase):

    @patch("builtins.print")
    def test_invalid_workspace_returns_error_before_validation(self, mock_print):
        from lib import setup_common

        parser = MagicMock()
        args = MagicMock()
        args.workspace = "/bad/workspace"
        parser.parse_args.return_value = args

        with patch.object(setup_common, "create_argument_parser", return_value=parser), \
             patch.object(setup_common, "validate_workspace_dir", side_effect=ValueError("bad workspace")), \
             patch.object(setup_common, "set_workspace_dir") as mock_set_workspace, \
             patch.object(setup_common, "validate_host") as mock_validate_host:
            result = setup_common.setup_main("server_lite", "Test", lambda c: None)

        self.assertEqual(result, 1)
        mock_set_workspace.assert_not_called()
        mock_validate_host.assert_not_called()
        mock_print.assert_called_with("Error: bad workspace")

    @patch("builtins.print")
    def test_invalid_notify_specs_return_error_before_remote_setup(self, mock_print):
        from lib import setup_common

        parser = MagicMock()
        args = MagicMock()
        args.workspace = None
        args.host = "testhost"
        args.username = "testuser"
        parser.parse_args.return_value = args
        config = _make_config(notify_specs=[["webhook", "not-a-url"]])

        with patch.object(setup_common, "create_argument_parser", return_value=parser), \
             patch.object(setup_common, "validate_host", return_value=True), \
             patch.object(setup_common, "validate_username", return_value=True), \
             patch.object(setup_common, "prepare_runtime_config", return_value=config), \
             patch.object(setup_common, "validate_hosted_flags", side_effect=ValueError("Invalid hosted node host: bad host")), \
             patch.object(setup_common, "run_remote_setup") as mock_run_remote:
            result = setup_common.setup_main("server_lite", "Test", lambda c: None)

        self.assertEqual(result, 1)
        mock_run_remote.assert_not_called()
        mock_print.assert_called_with("Error: Invalid webhook URL")

    @patch("builtins.print")
    def test_invalid_deploy_targets_return_error_before_remote_setup(self, mock_print):
        from lib import setup_common

        parser = MagicMock()
        args = MagicMock()
        args.workspace = None
        args.host = "testhost"
        args.username = "testuser"
        parser.parse_args.return_value = args
        config = _make_config(deploy_targets=["bad target"])

        with patch.object(setup_common, "create_argument_parser", return_value=parser), \
             patch.object(setup_common, "validate_host", return_value=True), \
             patch.object(setup_common, "validate_username", return_value=True), \
             patch.object(setup_common, "prepare_runtime_config", return_value=config), \
             patch.object(setup_common, "validate_hosted_flags", side_effect=ValueError("Invalid hosted node host: bad host")), \
             patch.object(setup_common, "run_remote_setup") as mock_run_remote:
            result = setup_common.setup_main("server_lite", "Test", lambda c: None)

        self.assertEqual(result, 1)
        mock_run_remote.assert_not_called()
        mock_print.assert_called_with("Error: Invalid deploy target host: bad target")

    @patch("builtins.print")
    def test_invalid_deploy_specs_return_error_before_remote_setup(self, mock_print):
        from lib import setup_common

        parser = MagicMock()
        args = MagicMock()
        args.workspace = None
        args.host = "testhost"
        args.username = "testuser"
        parser.parse_args.return_value = args
        config = _make_config(deploy_specs=[["bad domain", "https://github.com/user/repo.git"]])

        with patch.object(setup_common, "create_argument_parser", return_value=parser), \
             patch.object(setup_common, "validate_host", return_value=True), \
             patch.object(setup_common, "validate_username", return_value=True), \
             patch.object(setup_common, "prepare_runtime_config", return_value=config), \
             patch.object(setup_common, "validate_hosted_flags", side_effect=ValueError("Invalid hosted node host: bad host")), \
             patch.object(setup_common, "run_remote_setup") as mock_run_remote:
            result = setup_common.setup_main("server_lite", "Test", lambda c: None)

        self.assertEqual(result, 1)
        mock_run_remote.assert_not_called()
        mock_print.assert_called_with("Error: Invalid deploy domain: bad domain")

    @patch("builtins.print")
    def test_invalid_samba_share_specs_return_error_before_remote_setup(self, mock_print):
        from lib import setup_common

        parser = MagicMock()
        args = MagicMock()
        args.workspace = None
        args.host = "testhost"
        args.username = "testuser"
        parser.parse_args.return_value = args
        config = _make_config(samba_shares=[["read", "bad/share", "/mnt/docs", "shareuser:secret"]])

        with patch.object(setup_common, "create_argument_parser", return_value=parser), \
             patch.object(setup_common, "validate_host", return_value=True), \
             patch.object(setup_common, "validate_username", return_value=True), \
             patch.object(setup_common, "prepare_runtime_config", return_value=config), \
             patch.object(setup_common, "run_remote_setup") as mock_run_remote:
            result = setup_common.setup_main("server_lite", "Test", lambda c: None)

        self.assertEqual(result, 1)
        mock_run_remote.assert_not_called()
        mock_print.assert_called_with("Error: Invalid Samba share name (cannot contain /, \\, or spaces): bad/share")

    @patch("builtins.print")
    def test_invalid_ssl_email_returns_error_before_remote_setup(self, mock_print):
        from lib import setup_common

        parser = MagicMock()
        args = MagicMock()
        args.workspace = None
        args.host = "testhost"
        args.username = "testuser"
        parser.parse_args.return_value = args
        config = _make_config(ssl_email="bad-email")

        with patch.object(setup_common, "create_argument_parser", return_value=parser), \
             patch.object(setup_common, "validate_host", return_value=True), \
             patch.object(setup_common, "validate_username", return_value=True), \
             patch.object(setup_common, "prepare_runtime_config", return_value=config), \
             patch.object(setup_common, "run_remote_setup") as mock_run_remote:
            result = setup_common.setup_main("server_lite", "Test", lambda c: None)

        self.assertEqual(result, 1)
        mock_run_remote.assert_not_called()
        mock_print.assert_called_with("Error: Invalid SSL email address: bad-email")

    @patch("builtins.print")
    def test_invalid_apt_package_returns_error_before_remote_setup(self, mock_print):
        from lib import setup_common

        parser = MagicMock()
        args = MagicMock()
        args.workspace = None
        args.host = "testhost"
        args.username = "testuser"
        parser.parse_args.return_value = args
        config = _make_config(apt_packages=["python3; rm -rf /"])

        with patch.object(setup_common, "create_argument_parser", return_value=parser), \
             patch.object(setup_common, "validate_host", return_value=True), \
             patch.object(setup_common, "validate_username", return_value=True), \
             patch.object(setup_common, "prepare_runtime_config", return_value=config), \
             patch.object(setup_common, "run_remote_setup") as mock_run_remote:
            result = setup_common.setup_main("server_lite", "Test", lambda c: None)

        self.assertEqual(result, 1)
        mock_run_remote.assert_not_called()
        mock_print.assert_called_with("Error: Invalid --apt-install name: python3; rm -rf /")

    @patch("builtins.print")
    def test_invalid_timezone_returns_error_before_remote_setup(self, mock_print):
        from lib import setup_common

        parser = MagicMock()
        args = MagicMock()
        args.workspace = None
        args.host = "testhost"
        args.username = "testuser"
        parser.parse_args.return_value = args
        config = _make_config(timezone="Mars/Olympus")

        with patch.object(setup_common, "create_argument_parser", return_value=parser), \
             patch.object(setup_common, "validate_host", return_value=True), \
             patch.object(setup_common, "validate_username", return_value=True), \
             patch.object(setup_common, "prepare_runtime_config", return_value=config), \
             patch.object(setup_common, "run_remote_setup") as mock_run_remote:
            result = setup_common.setup_main("server_lite", "Test", lambda c: None)

        self.assertEqual(result, 1)
        mock_run_remote.assert_not_called()
        mock_print.assert_called_with("Error: Invalid timezone: Mars/Olympus")

    @patch("builtins.print")
    def test_invalid_sync_specs_return_error_before_remote_setup(self, mock_print):
        from lib import setup_common

        parser = MagicMock()
        args = MagicMock()
        args.workspace = None
        args.host = "testhost"
        args.username = "testuser"
        parser.parse_args.return_value = args
        config = _make_config(sync_specs=[["relative", "/dst", "daily"]])

        with patch.object(setup_common, "create_argument_parser", return_value=parser), \
             patch.object(setup_common, "validate_host", return_value=True), \
             patch.object(setup_common, "validate_username", return_value=True), \
             patch.object(setup_common, "prepare_runtime_config", return_value=config), \
             patch.object(setup_common, "run_remote_setup") as mock_run_remote:
            result = setup_common.setup_main("server_lite", "Test", lambda c: None)

        self.assertEqual(result, 1)
        mock_run_remote.assert_not_called()
        mock_print.assert_called_with("Error: Source path must be absolute: relative")

    @patch("builtins.print")
    def test_invalid_scrub_specs_return_error_before_remote_setup(self, mock_print):
        from lib import setup_common

        parser = MagicMock()
        args = MagicMock()
        args.workspace = None
        args.host = "testhost"
        args.username = "testuser"
        parser.parse_args.return_value = args
        config = _make_config(scrub_specs=[["/data", ".pardatabase", "0%", "weekly"]])

        with patch.object(setup_common, "create_argument_parser", return_value=parser), \
             patch.object(setup_common, "validate_host", return_value=True), \
             patch.object(setup_common, "validate_username", return_value=True), \
             patch.object(setup_common, "prepare_runtime_config", return_value=config), \
             patch.object(setup_common, "run_remote_setup") as mock_run_remote:
            result = setup_common.setup_main("server_lite", "Test", lambda c: None)

        self.assertEqual(result, 1)
        mock_run_remote.assert_not_called()
        mock_print.assert_called_with("Error: Redundancy percentage must be between 1 and 100: 0%")

    @patch("builtins.print")
    def test_invalid_smb_mount_specs_return_error_before_remote_setup(self, mock_print):
        from lib import setup_common

        parser = MagicMock()
        args = MagicMock()
        args.workspace = None
        args.host = "testhost"
        args.username = "testuser"
        parser.parse_args.return_value = args
        config = _make_config(smb_mounts=[["/mnt/share", "bad host", "user:pass", "docs", "/sub"]])

        with patch.object(setup_common, "create_argument_parser", return_value=parser), \
             patch.object(setup_common, "validate_host", return_value=True), \
             patch.object(setup_common, "validate_username", return_value=True), \
             patch.object(setup_common, "prepare_runtime_config", return_value=config), \
             patch.object(setup_common, "run_remote_setup") as mock_run_remote:
            result = setup_common.setup_main("server_lite", "Test", lambda c: None)

        self.assertEqual(result, 1)
        mock_run_remote.assert_not_called()
        mock_print.assert_called_with("Error: Invalid SMB mount host: bad host")


class TestExpandRemoteArgs(unittest.TestCase):
    def test_expand_remote_args_preserves_quoted_values(self):
        from lib.setup_common import _expand_remote_args

        expanded = _expand_remote_args([
            "--timezone 'America/New_York'",
            "--mount-smb /mnt/share 1.2.3.4 'user:secret phrase' docs /",
        ])

        self.assertEqual(
            expanded,
            [
                "--timezone",
                "America/New_York",
                "--mount-smb",
                "/mnt/share",
                "1.2.3.4",
                "user:secret phrase",
                "docs",
                "/",
            ],
        )


class TestHostedProvisioningDispatch(unittest.TestCase):
    def _make_args(self) -> MagicMock:
        args = MagicMock()
        args.workspace = None
        args.host = "testhost"
        args.username = "testuser"
        args.dry_run = False
        return args

    @patch("builtins.print")
    def test_hosted_vm_setup_dispatches_to_provision_vm(self, _mock_print):
        from lib import setup_common

        parser = MagicMock()
        parser.parse_args.return_value = self._make_args()
        config = _make_config(
            host="10.0.0.50",
            system_type="server_web",
            machine_type="vm",
            hosted_node="10.0.0.1",
            container_memory="2G",
            container_storage=[["root", "local-lvm", "10G"]],
            vm_image="local:import/debian-12-generic-amd64.qcow2",
        )

        with patch.object(setup_common, "create_argument_parser", return_value=parser), \
             patch.object(setup_common, "validate_host", return_value=True), \
             patch.object(setup_common, "validate_username", return_value=True), \
             patch.object(setup_common, "prepare_runtime_config", return_value=config) as mock_prepare, \
             patch.object(setup_common, "validate_hosted_flags"), \
             patch.object(setup_common, "validate_samba_share_credentials"), \
             patch.object(setup_common, "print_setup_summary"), \
             patch.object(setup_common, "store_cli_credentials"), \
             patch.object(setup_common, "save_setup_command"), \
             patch.object(setup_common, "run_remote_setup", return_value=0) as mock_run_remote, \
             patch("lib.config.SetupConfig.from_args", return_value=config), \
             patch("lib.proxmox_vm.provision_vm") as mock_provision_vm:
            result = setup_common.setup_main("server_web", "Test", lambda c: None)

        self.assertEqual(result, 0)
        mock_provision_vm.assert_called_once_with(config, image=config.vm_image)
        self.assertEqual(mock_prepare.call_count, 2)
        mock_run_remote.assert_called_once_with(config)

    @patch("builtins.print")
    def test_hosted_vm_setup_continues_when_vm_already_exists(self, _mock_print):
        from lib import setup_common
        from lib.proxmox_vm import VMAlreadyExists

        parser = MagicMock()
        parser.parse_args.return_value = self._make_args()
        config = _make_config(
            host="10.0.0.50",
            system_type="server_web",
            machine_type="vm",
            hosted_node="10.0.0.1",
            container_memory="2G",
            container_storage=[["root", "local-lvm", "10G"]],
        )

        with patch.object(setup_common, "create_argument_parser", return_value=parser), \
             patch.object(setup_common, "validate_host", return_value=True), \
             patch.object(setup_common, "validate_username", return_value=True), \
             patch.object(setup_common, "prepare_runtime_config", return_value=config), \
             patch.object(setup_common, "validate_hosted_flags"), \
             patch.object(setup_common, "validate_samba_share_credentials"), \
             patch.object(setup_common, "print_setup_summary"), \
             patch.object(setup_common, "store_cli_credentials"), \
             patch.object(setup_common, "save_setup_command"), \
             patch.object(setup_common, "run_remote_setup", return_value=0) as mock_run_remote, \
             patch("lib.config.SetupConfig.from_args", return_value=config), \
             patch("lib.proxmox_vm.provision_vm", side_effect=VMAlreadyExists()):
            result = setup_common.setup_main("server_web", "Test", lambda c: None)

        self.assertEqual(result, 0)
        mock_run_remote.assert_called_once_with(config)

    @patch("builtins.print")
    def test_hosted_vm_refuses_data_disk_adoption_when_vm_exists(self, mock_print):
        from lib import setup_common
        from lib.proxmox_vm import VMAlreadyExists

        parser = MagicMock()
        parser.parse_args.return_value = self._make_args()
        config = _make_config(
            host="10.0.0.50",
            system_type="server_web",
            machine_type="vm",
            hosted_node="10.0.0.1",
            container_memory="2G",
            container_storage=[
                ["root", "local-lvm", "10G"],
                ["git-data", "bulk-lvm", "64G"],
            ],
            storage_mounts=[["git-data", "/srv/gogs"]],
        )

        with patch.object(setup_common, "create_argument_parser", return_value=parser), \
             patch.object(setup_common, "validate_host", return_value=True), \
             patch.object(setup_common, "validate_username", return_value=True), \
             patch.object(setup_common, "prepare_runtime_config", return_value=config), \
             patch.object(setup_common, "validate_hosted_flags"), \
             patch.object(setup_common, "validate_samba_share_credentials"), \
             patch.object(setup_common, "run_remote_setup") as mock_remote, \
             patch("lib.config.SetupConfig.from_args", return_value=config), \
             patch("lib.proxmox_vm.provision_vm", side_effect=VMAlreadyExists()):
            result = setup_common.setup_main("server_web", "Test", lambda c: None)

        self.assertEqual(result, 1)
        mock_remote.assert_not_called()
        self.assertIn(
            "refusing to adopt disks",
            "\n".join(str(call.args[0]) for call in mock_print.call_args_list),
        )

    @patch("builtins.print")
    def test_hosted_lxc_setup_dispatches_to_provision_container(self, _mock_print):
        from lib import setup_common

        parser = MagicMock()
        parser.parse_args.return_value = self._make_args()
        config = _make_config(
            host="10.0.0.50",
            machine_type="unprivileged",
            hosted_node="10.0.0.1",
            container_memory="2G",
            container_storage=[["root", "local-lvm", "10G"], ["template", "local"]],
        )

        with patch.object(setup_common, "create_argument_parser", return_value=parser), \
             patch.object(setup_common, "validate_host", return_value=True), \
             patch.object(setup_common, "validate_username", return_value=True), \
             patch.object(setup_common, "prepare_runtime_config", return_value=config), \
             patch.object(setup_common, "validate_hosted_flags"), \
             patch.object(setup_common, "validate_samba_share_credentials"), \
             patch.object(setup_common, "print_setup_summary"), \
             patch.object(setup_common, "store_cli_credentials"), \
             patch.object(setup_common, "save_setup_command"), \
             patch.object(setup_common, "run_remote_setup", return_value=0) as mock_run_remote, \
             patch("lib.config.SetupConfig.from_args", return_value=config), \
             patch("lib.proxmox_node.provision_container") as mock_provision_container:
            result = setup_common.setup_main("server_lite", "Test", lambda c: None)

        self.assertEqual(result, 0)
        mock_provision_container.assert_called_once_with(config)
        mock_run_remote.assert_called_once_with(config)

    @patch("builtins.print")
    def test_hosted_vm_setup_expands_saved_host_root_storage(self, _mock_print):
        from lib import setup_common

        with tempfile.TemporaryDirectory() as workspace:
            add_proxmox_host(
                ProxmoxHost(
                    name="pve1",
                    address="10.0.0.1",
                    ssh_key="/keys/proxmox",
                    facts=ProxmoxHostFacts(
                        default_root_storage="local-lvm",
                        default_bridge="vmbr0",
                    ),
                ),
                workspace,
            )
            parser = MagicMock()
            args = self._make_args()
            args.workspace = workspace
            parser.parse_args.return_value = args
            config = _make_config(
                host="10.0.0.50",
                system_type="server_web",
                machine_type="vm",
                hosted_node="pve1",
                hosted_key=None,
                container_memory="2G",
                container_storage=[["root", "10G"]],
                vm_image="local:import/debian-12-generic-amd64.qcow2",
            )

            with patch.object(setup_common, "create_argument_parser", return_value=parser), \
                 patch.object(setup_common, "validate_host", return_value=True), \
                 patch.object(setup_common, "validate_username", return_value=True), \
                 patch.object(setup_common, "resolve_guest_ssh_key", return_value=None), \
                 patch.object(setup_common, "prepare_runtime_config", return_value=config), \
                 patch.object(setup_common, "validate_hosted_flags"), \
                 patch.object(setup_common, "validate_samba_share_credentials"), \
                 patch.object(setup_common, "print_setup_summary"), \
                 patch.object(setup_common, "store_cli_credentials"), \
                 patch.object(setup_common, "save_setup_command"), \
                 patch.object(setup_common, "run_remote_setup", return_value=0), \
                 patch("lib.config.SetupConfig.from_args", return_value=config), \
                 patch("lib.proxmox_vm.provision_vm") as mock_provision_vm:
                result = setup_common.setup_main("server_web", "Test", lambda c: None)

        self.assertEqual(result, 0)
        self.assertEqual(config.hosted_node, "10.0.0.1")
        self.assertEqual(config.hosted_key, "/keys/proxmox")
        self.assertEqual(config.ssh_key, "/keys/proxmox")
        self.assertEqual(config.hosted_bridge, "vmbr0")
        self.assertEqual(config.container_storage, [["root", "local-lvm", "10G"]])
        mock_provision_vm.assert_called_once_with(config, image=config.vm_image)

    def test_hosted_vm_setup_uses_implicit_guest_ssh_key(self):
        from lib import setup_common

        config = _make_config(
            host="10.0.0.50",
            machine_type="vm",
            hosted_node="10.0.0.1",
            hosted_key=None,
            container_memory="2G",
            container_storage=[["root", "local-lvm", "10G"]],
        )

        with patch.object(
            setup_common,
            "resolve_guest_ssh_key",
            return_value="/home/test/.ssh/id_ed25519",
        ) as mock_resolve:
            setup_common._apply_hosted_proxmox_defaults(config, None)

        self.assertEqual(config.ssh_key, "/home/test/.ssh/id_ed25519")
        self.assertIsNone(config.hosted_key)
        mock_resolve.assert_called_once_with(
            None,
            home=setup_common._local_user_home(),
        )

    def test_registered_host_key_can_be_used_for_guest(self):
        from lib import setup_common

        with tempfile.TemporaryDirectory() as workspace:
            add_proxmox_host(
                ProxmoxHost(
                    name="pve1",
                    address="10.0.0.1",
                    ssh_key="/keys/proxmox",
                ),
                workspace,
            )
            config = _make_config(
                host="10.0.0.50",
                hosted_node="pve1",
                container_storage=[["root", "10G"]],
            )

            with patch.object(
                setup_common,
                "resolve_guest_ssh_key",
                return_value=None,
            ) as mock_resolve:
                setup_common._apply_hosted_proxmox_defaults(config, workspace)

        self.assertEqual(config.ssh_key, "/keys/proxmox")
        mock_resolve.assert_called_once_with(
            "/keys/proxmox",
            home=setup_common._local_user_home(),
        )

    def test_hosted_setup_preserves_explicit_guest_ssh_key(self):
        from lib import setup_common

        with tempfile.TemporaryDirectory() as workspace:
            add_proxmox_host(
                ProxmoxHost(
                    name="pve1",
                    address="10.0.0.1",
                    ssh_key="/keys/proxmox",
                ),
                workspace,
            )
            config = _make_config(
                host="10.0.0.50",
                hosted_node="pve1",
                hosted_key=None,
                ssh_key="/keys/agent-vm",
                container_storage=[["root", "10G"]],
            )

            setup_common._apply_hosted_proxmox_defaults(config, workspace)

        self.assertEqual(config.hosted_key, "/keys/proxmox")
        self.assertEqual(config.ssh_key, "/keys/agent-vm")

    def test_unregistered_node_defaults_to_guest_ssh_key(self):
        from lib import setup_common

        config = _make_config(
            host="10.0.0.50",
            hosted_node="10.0.0.1",
            ssh_key="/keys/shared",
            container_storage=[["root", "10G"]],
        )

        setup_common._apply_hosted_proxmox_defaults(config, None)

        self.assertEqual(config.hosted_key, "/keys/shared")

    @patch("builtins.print")
    def test_hosted_lxc_setup_expands_saved_template_storage(self, _mock_print):
        from lib import setup_common

        with tempfile.TemporaryDirectory() as workspace:
            add_proxmox_host(
                ProxmoxHost(
                    name="pve1",
                    address="10.0.0.1",
                    default_storage="local-lvm",
                    facts=ProxmoxHostFacts(default_template_storage="local"),
                ),
                workspace,
            )
            parser = MagicMock()
            args = self._make_args()
            args.workspace = workspace
            parser.parse_args.return_value = args
            config = _make_config(
                host="10.0.0.50",
                machine_type="unprivileged",
                hosted_node="pve1",
                container_memory="2G",
                container_storage=[["root", "host", "10G"], ["template"]],
            )

            with patch.object(setup_common, "create_argument_parser", return_value=parser), \
                 patch.object(setup_common, "validate_host", return_value=True), \
                 patch.object(setup_common, "validate_username", return_value=True), \
                 patch.object(setup_common, "prepare_runtime_config", return_value=config), \
                 patch.object(setup_common, "validate_samba_share_credentials"), \
                 patch.object(setup_common, "print_setup_summary"), \
                 patch.object(setup_common, "store_cli_credentials"), \
                 patch.object(setup_common, "save_setup_command"), \
                 patch.object(setup_common, "run_remote_setup", return_value=0), \
                 patch("lib.config.SetupConfig.from_args", return_value=config), \
                 patch("lib.proxmox_node.provision_container") as mock_provision_container:
                result = setup_common.setup_main("server_lite", "Test", lambda c: None)

        self.assertEqual(result, 0)
        self.assertEqual(
            config.container_storage,
            [["root", "local-lvm", "10G"], ["template", "local"]],
        )
        mock_provision_container.assert_called_once_with(config)

    def test_hosted_setup_expands_unregistered_storage_shorthand_to_auto(self):
        from lib import setup_common

        with tempfile.TemporaryDirectory() as workspace:
            config = _make_config(
                host="10.0.0.50",
                hosted_node="10.0.0.1",
                container_storage=[["root", "10G"], ["template"]],
            )

            setup_common._apply_hosted_proxmox_defaults(config, workspace)

        self.assertEqual(config.hosted_node, "10.0.0.1")
        self.assertEqual(
            config.container_storage,
            [["root", "auto", "10G"], ["template", "auto"]],
        )

    def test_provisioned_bare_ipv4_defaults_to_slash_24(self):
        from lib import setup_common

        config = _make_config(
            host="10.0.0.50",
            hosted_node="10.0.0.1",
        )

        setup_common._apply_hosted_proxmox_defaults(config, None)

        self.assertEqual(config.host, "10.0.0.50")
        self.assertEqual(config.static_ipv4, "10.0.0.50/24")

    def test_provisioned_cidr_moves_prefix_into_static_network_config(self):
        from lib import setup_common

        config = _make_config(
            host="10.0.0.50/20",
            hosted_node="10.0.0.1",
        )

        setup_common._apply_hosted_proxmox_defaults(config, None)

        self.assertEqual(config.host, "10.0.0.50")
        self.assertEqual(config.static_ipv4, "10.0.0.50/20")

    def test_provisioned_hostname_without_an_address_is_rejected(self):
        from lib import setup_common

        config = _make_config(
            host="guest.example.test",
            hosted_node="10.0.0.1",
        )

        with self.assertRaisesRegex(ValueError, "requires an IPv4 target"):
            setup_common._apply_hosted_proxmox_defaults(config, None)

    def test_server_proxmox_setup_registers_host(self):
        from lib import setup_common

        with tempfile.TemporaryDirectory() as workspace:
            config = _make_config(
                system_type="server_proxmox",
                host="10.0.0.10",
                ssh_key="/keys/proxmox",
                friendly_name="pve1",
            )

            setup_common.register_proxmox_setup_host(config, workspace)

            registered = setup_common.find_proxmox_host("pve1", workspace)

        self.assertIsNotNone(registered)
        assert registered is not None
        self.assertEqual(registered.address, "10.0.0.10")
        self.assertEqual(registered.user, "root")
        self.assertEqual(registered.ssh_key, "/keys/proxmox")


if __name__ == '__main__':
    unittest.main()
