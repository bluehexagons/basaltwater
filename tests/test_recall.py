"""Tests for remote configuration recall and reconstruction."""

from __future__ import annotations

import io
import json
import base64
import os
import subprocess
import tempfile
import tarfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from lib import recall
from lib.config import SetupConfig
from lib.remote_utils import CommandTimeoutError


def completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["ssh"], returncode, stdout, stderr)


class TestRetrieveStoredConfig(unittest.TestCase):
    def test_retrieves_and_decodes_stored_config(self) -> None:
        stored = {"username": "remote", "friendly_name": "production", "tags": "web,prod"}
        with patch.object(recall, "build_ssh_command", return_value=["ssh"]), patch.object(recall, "run", return_value=completed(stdout=json.dumps(stored))) as run:
            config = recall.retrieve_stored_config("server", "remote", "/tmp/key")

        self.assertIsNotNone(config)
        self.assertEqual(config.host, "server")
        self.assertEqual(config.username, "remote")
        self.assertEqual(config.tags, ["web", "prod"])
        self.assertEqual(run.call_args.kwargs["timeout"], 30)
        self.assertIn("cat /opt/infra_tools/state/setup.json", run.call_args.args[0][-1])

    def test_retrieve_returns_none_for_empty_or_invalid_remote_data(self) -> None:
        for result in (completed(stdout=""), completed(stdout="not json"), completed(stdout='[]')):
            with self.subTest(stdout=result.stdout), patch.object(recall, "build_ssh_command", return_value=["ssh"]), patch.object(recall, "run", return_value=result):
                self.assertIsNone(recall.retrieve_stored_config("server", "remote"))

    def test_retrieve_handles_timeout_and_missing_ssh(self) -> None:
        stderr = io.StringIO()
        with patch.object(recall, "build_ssh_command", return_value=["ssh"]), patch.object(recall, "run", side_effect=CommandTimeoutError('ssh', 30)):
            with redirect_stderr(stderr):
                self.assertIsNone(recall.retrieve_stored_config("server", "remote"))
        self.assertIn("Timeout retrieving stored config", stderr.getvalue())

        stderr = io.StringIO()
        with patch.object(recall, "build_ssh_command", return_value=["ssh"]), patch.object(recall, "run", side_effect=FileNotFoundError):
            with redirect_stderr(stderr):
                self.assertIsNone(recall.retrieve_stored_config("server", "remote"))
        self.assertIn("SSH command not available", stderr.getvalue())


class TestReconstructRemoteConfig(unittest.TestCase):
    def test_reconstructs_existing_remote_install_and_preserves_extras(self) -> None:
        reconstructed = {
            "install_go": True,
            "install_node": False,
            "deploy": [["example.com", "https://example.com/repo.git"]],
            "samba_shares": ["public"],
        }
        with patch.object(recall, "build_ssh_command", return_value=["ssh"]), patch.object(recall, "run", side_effect=[completed(), completed(stdout=json.dumps(reconstructed))]) as run:
            result = recall.reconstruct_remote_config("server", "remote", "/tmp/key")

        self.assertIsNotNone(result)
        config, extras = result
        self.assertEqual(config.system_type, "server_web")
        self.assertTrue(config.install_go)
        self.assertEqual(extras, {"samba_shares": ["public"], "deploy": [["example.com", "https://example.com/repo.git"]]})
        self.assertEqual(run.call_count, 2)
        self.assertIn("test -f /opt/infra_tools/infra_tools.py", run.call_args_list[0].args[0][-1])
        self.assertIn("reconstruct --compact", run.call_args_list[1].args[0][-1])

    def test_reconstruct_uses_temporary_remote_source_when_tool_is_missing(self) -> None:
        reconstructed = {"install_python": True}
        with tempfile.TemporaryDirectory() as temp_root, patch.object(recall.tempfile, "mkdtemp", return_value=os.path.join(temp_root, "build")), patch.object(recall, "copy_project_files") as copy_files, patch.object(recall, "create_tar_from_dir", return_value=b"tar data") as create_tar, patch.object(recall, "build_ssh_command", return_value=["ssh"]), patch.object(recall, "run", side_effect=[completed(1), completed(stdout=json.dumps(reconstructed))]) as run:
            os.mkdir(os.path.join(temp_root, "build"))
            result = recall.reconstruct_remote_config("server", "remote")

        self.assertIsNotNone(result)
        self.assertTrue(result[0].install_python)
        copy_files.assert_called_once_with(os.path.join(temp_root, "build"))
        create_tar.assert_called_once_with(os.path.join(temp_root, "build"))
        self.assertEqual(run.call_count, 2)
        invocation = run.call_args
        self.assertEqual(invocation.kwargs['input_data'], base64.b64encode(b'tar data').decode())
        self.assertEqual(invocation.kwargs['timeout'], 120)
        self.assertIn('timeout --kill-after=5s 60s', invocation.args[0][-1])
        self.assertNotIn('/opt/infra_tools', invocation.args[0][-1])
        self.assertFalse(os.path.exists(os.path.join(temp_root, "build")))

    def test_reconstruct_cleans_local_source_after_remote_failure_or_timeout(self) -> None:
        for result in (completed(1), CommandTimeoutError('ssh', 120)):
            with self.subTest(result=result), tempfile.TemporaryDirectory() as temp_root, patch.object(recall.tempfile, "mkdtemp", return_value=os.path.join(temp_root, "build")), patch.object(recall, "copy_project_files"), patch.object(recall, "create_tar_from_dir", return_value=b"tar data"), patch.object(recall, "build_ssh_command", return_value=["ssh"]), patch.object(recall, "run", side_effect=[completed(1), result]):
                os.mkdir(os.path.join(temp_root, "build"))
                self.assertIsNone(recall.reconstruct_remote_config("server", "remote"))
                self.assertFalse(os.path.exists(os.path.join(temp_root, 'build')))

    def test_failed_remote_probe_never_uploads_source(self):
        with patch.object(recall, 'build_ssh_command', return_value=['ssh']), patch.object(recall, 'run', return_value=completed(255)) as run, patch.object(recall, 'copy_project_files') as copy:
            self.assertIsNone(recall.reconstruct_remote_config('server', 'remote'))
            copy.assert_not_called()
            run.assert_called_once()

    def test_remote_staging_is_cleaned_after_success_bad_archive_and_timeout(self):
        for content in (b'print("{}")', None, b'import time; time.sleep(60)'):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                archive = io.BytesIO()
                if content is not None:
                    with tarfile.open(fileobj=archive, mode='w:gz') as tar:
                        entry = tarfile.TarInfo('infra_tools.py')
                        entry.size = len(content)
                        tar.addfile(entry, io.BytesIO(content))
                script = ('mktemp() { command mktemp -d "$RECALL_TEST_ROOT/stage.XXXXXXXX"; }\n'
                          + recall.REMOTE_RECONSTRUCT_SCRIPT)
                result = subprocess.run(
                    ['timeout', '--kill-after=1s', '1s', 'sh', '-c', script],
                    input=base64.b64encode(archive.getvalue()).decode(), capture_output=True,
                    text=True, timeout=5, env={**os.environ, 'RECALL_TEST_ROOT': directory},
                )
                self.assertEqual(result.returncode == 0, content == b'print("{}")', result.stderr)
                self.assertEqual(os.listdir(directory), [])


class TestRecallCommand(unittest.TestCase):
    def test_recall_uses_stored_configuration_without_reconstruction(self) -> None:
        config = SetupConfig(host="server", username="remote", system_type="server_dev")
        stdout = io.StringIO()
        with patch.object(recall, "retrieve_stored_config", return_value=config) as retrieve, patch.object(recall, "reconstruct_remote_config") as reconstruct:
            with redirect_stdout(stdout):
                result = recall.run_recall_command("server", "remote", None)

        self.assertEqual(result, 0)
        retrieve.assert_called_once_with("server", "remote", None)
        reconstruct.assert_not_called()
        self.assertIn("Stored configuration file", stdout.getvalue())
        self.assertIn("infra-tools setup server_dev", stdout.getvalue())
        self.assertIn("  remote", stdout.getvalue())

    def test_recall_reports_reconstruction_failure(self) -> None:
        stderr = io.StringIO()
        with patch.object(recall, "retrieve_stored_config", return_value=None), patch.object(recall, "reconstruct_remote_config", return_value=None), redirect_stderr(stderr):
            result = recall.run_recall_command("server", "remote", None)
        self.assertEqual(result, 1)
        self.assertIn("Failed to retrieve or reconstruct", stderr.getvalue())

    def test_recall_prints_reconstructed_feature_notes(self) -> None:
        config = SetupConfig(host="server", username="remote", system_type="server_web")
        extras = {
            "samba_shares": ["public"],
            "deploy": [["example.com", "repo"]],
            "sync": [["/a", "/b"]],
            "scrub": [["/data", "/db"]],
            "mount_smb": ["//nas/share /mnt/share"],
        }
        stdout = io.StringIO()
        with patch.object(recall, "retrieve_stored_config", return_value=None), patch.object(recall, "reconstruct_remote_config", return_value=(config, extras)), patch.object(recall.os, "getenv", return_value="other-user"):
            with redirect_stdout(stdout):
                result = recall.run_recall_command("server", "remote", None)

        self.assertEqual(result, 0)
        output = stdout.getvalue()
        self.assertIn("Detected 1 Samba share(s)", output)
        self.assertIn("Detected 1 deployment(s)", output)
        self.assertIn("Detected 1 sync operation(s)", output)
        self.assertIn("Detected 1 scrub operation(s)", output)
        self.assertIn("Detected 1 SMB mount(s)", output)
        self.assertIn("  remote", output)


if __name__ == "__main__":
    unittest.main()
