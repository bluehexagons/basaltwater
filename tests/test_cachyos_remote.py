"""Offline contracts for controller-driven CachyOS setup."""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import basaltwater
from lib import cachyos_remote as remote


class CachyosRemoteTests(unittest.TestCase):
    def args(self, *options: str):
        parser, _, _ = basaltwater.create_basaltwater_parser()
        return parser.parse_args(["setup", "agent_cachyos", "desktop.lan", "alice", *options])

    def test_controller_defaults_and_explicit_opt_out(self):
        self.assertTrue(remote.controller_config(self.args()).install_ssh)
        self.assertFalse(remote.controller_config(self.args("--no-ssh")).install_ssh)
        self.assertEqual(remote.controller_config(self.args()).host, "localhost")

    def test_controller_rejects_invalid_contract_before_transport(self):
        for field, value in (("host", "host;touch /tmp/oops"), ("username", "root"),
                             ("username", None), ("username", "alice\n")):
            with self.subTest(field=field, value=value), patch.object(remote, "_ssh") as ssh:
                args = self.args()
                setattr(args, field, value)
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(remote.run_cachyos_remote_command(args), 1)
                ssh.assert_not_called()
        with self.assertRaisesRegex(ValueError, "does not support"):
            remote.controller_config(self.args("--password", "secret"))

    def test_password_option_uses_remote_default_ssh(self):
        config = remote.controller_config(self.args("--ssh-password-window", "5", "--access-source", "192.168.2.0/24"))
        self.assertTrue(config.install_ssh)
        self.assertEqual(config.ssh_password_window, 5)
        with self.assertRaises(ValueError):
            remote.controller_config(self.args("--no-ssh", "--ssh-password-auth", "--access-source", "192.168.2.0/24"))

    def test_dry_run_never_builds_or_connects(self):
        with patch.object(remote, "_ssh") as ssh, patch.object(remote, "_source_archive") as bundle, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(remote.run_cachyos_remote_command(self.args("--dry-run")), 0)
        ssh.assert_not_called()
        bundle.assert_not_called()
        self.assertIn("SSH enabled", output.getvalue())

    def test_public_cli_uses_new_transport_only_for_nonlocal_cachyos(self):
        with patch.object(remote, "run_cachyos_remote_command", return_value=0) as run, \
                patch("lib.cachyos.run_cachyos_command") as local, \
                patch.object(basaltwater, "run_remote_setup") as generic:
            self.assertEqual(basaltwater.run_setup_command(self.args()), 0)
        run.assert_called_once()
        local.assert_not_called()
        generic.assert_not_called()
        args = self.args()
        args.host = "localhost"
        with patch("lib.cachyos.run_cachyos_command", return_value=0) as local:
            self.assertEqual(basaltwater.run_setup_command(args), 0)
        local.assert_called_once()

    def test_archive_uses_source_bundle_and_private_args_without_identity(self):
        config = remote.controller_config(self.args("--key", "/controller/key"))
        def source(directory):
            Path(directory, "remote_setup.py").write_text("# source\n")
        with tempfile.TemporaryDirectory() as directory, \
                patch("lib.setup_common.copy_project_files", side_effect=source):
            data = remote._source_archive(config, directory)
            self.assertEqual(Path(directory, remote.ARGS_NAME).stat().st_mode & 0o777, 0o600)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            tokens = json.load(archive.extractfile("./" + remote.ARGS_NAME))
        self.assertIn("--ssh", tokens)
        self.assertNotIn("/controller/key", tokens)
        self.assertNotIn("--key", tokens)

    def test_serialization_preserves_target_lan_and_quoted_private_values(self):
        from lib.arg_parser import create_setup_argument_parser
        from lib.cachyos import cachyos_config_from_args

        receiver = "https://panel.example/api/v1/notifications#fixture_private_token_123456789012345678901234"
        config = remote.controller_config(self.args(
            "--lan-access", "--ssh-password-window", "5", "--notify", "webhook", receiver,
            "--agent-workspace", "/home/alice/projects;$(echo injected)",
        ))
        with tempfile.TemporaryDirectory() as directory, \
                patch("lib.setup_common.copy_project_files"), \
                patch("lib.config._resolved_lan_networks", side_effect=AssertionError("controller LAN probe")):
            remote._source_archive(config, directory)
            tokens = json.loads(Path(directory, remote.ARGS_NAME).read_text())
        self.assertIn("--lan-access", tokens)
        self.assertIn(receiver, tokens)
        parsed = create_setup_argument_parser("test", for_remote=True, allow_steps=True).parse_args(tokens)
        target = cachyos_config_from_args(parsed, for_remote=True)
        self.assertTrue(target.lan_access)
        self.assertEqual(target.agent_workspace, config.agent_workspace)
        self.assertEqual(target.notify_specs, config.notify_specs)

    def test_source_size_is_bounded_before_compression(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch("lib.setup_common.copy_project_files"), \
                patch.object(remote, "MAX_SOURCE_BYTES", 1), \
                self.assertRaisesRegex(ValueError, "128 MiB"):
            remote._source_archive(remote.controller_config(self.args()), directory)

    def test_target_preflight_precedes_persistent_runtime(self):
        tokens = ["--system-type", "agent_cachyos", "--username", "alice", "--ssh"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "private.json")
            path.write_text(json.dumps(tokens))
            with patch.object(remote.sys, "argv", ["cachyos_remote", "--target-args", str(path)]), \
                    patch("lib.cachyos.preflight_cachyos", side_effect=ValueError("existing user bus required")), \
                    patch.object(remote, "_target_runtime") as runtime, \
                    patch.object(remote.subprocess, "run") as run, \
                    self.assertRaisesRegex(ValueError, "user bus"):
                remote.main()
            runtime.assert_not_called()
            run.assert_not_called()
            self.assertFalse(path.exists())

    def test_success_failure_and_cleanup_contract(self):
        for upload_status, setup_status, cleanup_status, expected in (
            (0, 0, 0, 0), (0, 7, 0, 7), (6, 0, 0, 6), (0, 0, 1, 1),
        ):
            with self.subTest(upload=upload_status, setup=setup_status, cleanup=cleanup_status), \
                    patch.object(remote, "_source_archive", return_value=b"private payload"), \
                    patch.object(remote, "run_streamed", return_value=upload_status) as upload, \
                    patch.object(remote, "build_ssh_command", side_effect=lambda host, user, key, **kw: ["ssh", f"{user}@{host}", kw["remote_command"]]), \
                    patch.object(remote.sys.stdin, "isatty", return_value=True), \
                    patch.object(remote.subprocess, "run", side_effect=(
                        [subprocess.CompletedProcess([], cleanup_status)] if upload_status else
                        [subprocess.CompletedProcess([], setup_status), subprocess.CompletedProcess([], cleanup_status)]
                    )) as process, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(remote.run_cachyos_remote_command(self.args()), expected)
            upload_command = upload.call_args.args[0]
            self.assertIn("alice@desktop.lan", upload_command)
            self.assertIn("ForwardAgent=no", upload_command)
            self.assertIn("mkdir -m 700", upload_command[-1])
            self.assertNotIn("private payload", str(upload_command))
            self.assertIn("rm -rf", process.call_args.args[0][-1])
            if not upload_status:
                execution = process.call_args_list[0].args[0]
                self.assertIn("-t", execution)
                self.assertIn("lib.cachyos_remote", execution[-1])
                self.assertNotIn("sudo", execution[-1])
                self.assertNotIn("setup_upgrade", execution[-1])
                self.assertNotIn("root@", str(execution))

    def test_execution_exception_still_cleans_up(self):
        with patch.object(remote, "_source_archive", return_value=b"payload"), \
                patch.object(remote, "run_streamed", return_value=0), \
                patch.object(remote, "_ssh", return_value=["ssh"]), \
                patch.object(remote.subprocess, "run", side_effect=[
                    subprocess.TimeoutExpired("ssh", 1), subprocess.CompletedProcess([], 0),
                ]) as process, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(remote.run_cachyos_remote_command(self.args()), 1)
        self.assertEqual(process.call_count, 2)

    def test_identity_validation_and_transport_only(self):
        self.assertEqual(self.args("--ssh-key", "/controller/key").ssh_key, "/controller/key")
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory, "identity with spaces")
            key.write_text("mock private key")
            self.assertEqual(remote._identity_path(str(key)), str(key))
            with self.assertRaises(ValueError):
                remote._identity_path(directory)
            with self.assertRaises(ValueError):
                remote._identity_path(str(key) + "\n")
        with patch.object(remote, "build_ssh_command", return_value=["ssh", "alice@desktop", "true"]) as builder:
            command = remote._ssh("desktop", "alice", "/key", "true")
        self.assertEqual(builder.call_args.args, ("desktop", "alice", "/key"))
        self.assertIn("ClearAllForwardings=yes", command)

    def test_target_dispatch_normalizes_to_local_without_debian_path(self):
        import remote_setup
        with patch.object(remote_setup.sys, "argv", ["remote_setup.py", "--system-type", "agent_cachyos", "--username", "alice", "--ssh"]), \
                patch("lib.cachyos.cachyos_config_from_args") as config, \
                patch.object(remote_setup, "run_cachyos_setup", return_value=0) as run, \
                patch.object(remote_setup, "config_from_remote_args") as generic, \
                patch.object(remote_setup, "set_dry_run"):
            self.assertEqual(remote_setup._run_main(), 0)
        self.assertEqual(config.call_args.args[0].host, "localhost")
        self.assertTrue(config.call_args.kwargs["for_remote"])
        run.assert_called_once()
        generic.assert_not_called()

    def test_runtime_retains_code_but_never_private_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            home, source = Path(directory, "home"), Path(directory, "source")
            home.mkdir()
            source.mkdir()
            Path(source, "remote_setup.py").write_text("first")
            Path(source, remote.ARGS_NAME).write_text('["secret"]')
            with patch.object(remote.Path, "home", return_value=home):
                with remote._target_runtime(source) as runtime:
                    self.assertEqual((runtime / "remote_setup.py").read_text(), "first")
                    self.assertFalse((runtime / remote.ARGS_NAME).exists())
                Path(source, "remote_setup.py").write_text("second")
                with remote._target_runtime(source) as updated:
                    self.assertEqual(runtime, updated)
                    self.assertEqual((runtime / "remote_setup.py").read_text(), "second")
                self.assertFalse((runtime.parent / "previous").exists())
                self.assertEqual(sorted(p.name for p in runtime.parent.iterdir()), ["runtime", "setup.lock"])

    def test_runtime_rejects_symlink_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            home, foreign = Path(directory, "home"), Path(directory, "foreign")
            home.mkdir()
            foreign.mkdir()
            (home / ".local").symlink_to(foreign)
            with patch.object(remote.Path, "home", return_value=home), self.assertRaises(ValueError):
                with remote._target_runtime(foreign):
                    self.fail("unsafe runtime entered")


if __name__ == "__main__":
    unittest.main()
