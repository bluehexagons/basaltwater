"""Tests for remote deployment command construction and failure handling."""

from __future__ import annotations

import os
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from lib import remote_deploy



TARGET = {
    "host": "app.example",
    "user": "deploy",
    "ssh_key": "/tmp/deploy-key",
    "ssh_port": 2222,
    "base_dir": "/var/www",
}
SITE = {'domain': 'example.com', 'path': '/', 'serve_path': '/var/www/example_com', 'project_type': 'static'}


def completed(returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["ssh"], returncode, "", stderr)


class TestRemoteDeployTargetLoading(unittest.TestCase):
    def test_missing_targets_are_distinct_from_invalid_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "targets.json")
            with patch.object(remote_deploy, "DEPLOY_TARGETS_FILE", path):
                self.assertEqual(remote_deploy.load_deploy_targets(), {})
                for content in ("not json", "[]", '{"app": []}'):
                    with self.subTest(content=content):
                        with open(path, "w") as file:
                            file.write(content)
                        with self.assertRaises(remote_deploy.StateReadError):
                            remote_deploy.load_deploy_targets()


class TestPushArtifact(unittest.TestCase):
    def test_unknown_target_is_rejected_without_rsync(self) -> None:
        with patch.object(remote_deploy, "get_deploy_target", return_value=None), patch.object(remote_deploy.subprocess, "run") as run:
            self.assertFalse(remote_deploy.push_artifact("/tmp/build", "missing", "/srv/app"))
        run.assert_not_called()

    def test_push_builds_rsync_command_with_excludes_and_trailing_source_slash(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.object(remote_deploy, "get_deploy_target", return_value=TARGET), patch.object(remote_deploy, "build_rsync_ssh_transport", return_value="ssh -i /tmp/deploy-key"), patch.object(remote_deploy, "ssh_batch_mode", return_value=True), patch.object(remote_deploy.subprocess, "run", return_value=completed()) as run:
            result = remote_deploy.push_artifact(os.path.join(directory, "build"), "app", "/var/www/app", [".git", "*.tmp"])

        self.assertTrue(result)
        command = run.call_args.args[0]
        self.assertEqual(command[:5], ["rsync", "-avz", "--delete", "-e", "ssh -i /tmp/deploy-key"])
        self.assertEqual(command[5:9], ["--exclude", ".git", "--exclude", "*.tmp"])
        self.assertTrue(command[-2].endswith("/build/"))
        self.assertEqual(command[-1], "deploy@app.example:/var/www/app")

    def test_push_artifact_handles_rsync_failure_and_timeout(self) -> None:
        with patch.object(remote_deploy, "get_deploy_target", return_value=TARGET), patch.object(remote_deploy, "build_rsync_ssh_transport", return_value="ssh"), patch.object(remote_deploy.subprocess, "run", return_value=completed(1, "permission denied")):
            self.assertFalse(remote_deploy.push_artifact("/tmp/build", "app", "/var/www/app"))
        with patch.object(remote_deploy, "get_deploy_target", return_value=TARGET), patch.object(remote_deploy, "build_rsync_ssh_transport", return_value="ssh"), patch.object(remote_deploy.subprocess, "run", side_effect=subprocess.TimeoutExpired(["rsync"], 300)):
            self.assertFalse(remote_deploy.push_artifact("/tmp/build", "app", "/var/www/app"))

    def test_push_rejects_base_aliases_and_escapes_before_running_commands(self) -> None:
        for path in ("/var/www", "/var/www/.", "/var/www/app/..", "/var/www/..", "/srv/app", "/var/www/a\n"):
            with self.subTest(path=path), patch.object(remote_deploy, "get_deploy_target", return_value=TARGET), patch.object(remote_deploy.subprocess, "run") as run:
                self.assertFalse(remote_deploy.push_artifact("/tmp/build", "app", path))
                run.assert_not_called()


class TestPushNginxConfig(unittest.TestCase):
    def test_push_nginx_config_uploads_temp_file_then_invokes_helper(self) -> None:
        captured_source: list[str] = []

        def build_scp(*args, **kwargs):
            del kwargs
            captured_source.append(args[2])
            with open(args[2], encoding='utf-8') as source:
                self.assertEqual(json.load(source), SITE)
            return ["scp", args[2], args[3]]

        def build_ssh(target, remote):
            del target
            return ["ssh", remote]

        with patch.object(remote_deploy, "get_deploy_target", return_value=TARGET), patch.object(remote_deploy, "build_scp_command", side_effect=build_scp), patch.object(remote_deploy, "_build_ssh_cmd", side_effect=build_ssh), patch.object(remote_deploy.subprocess, "run", side_effect=[completed(), completed()]) as run:
            result = remote_deploy.push_nginx_config(SITE, "app", "example.com")

        self.assertTrue(result)
        self.assertEqual(len(captured_source), 1)
        self.assertFalse(os.path.exists(captured_source[0]))
        self.assertEqual(run.call_count, 2)
        self.assertIn("example_com.json", run.call_args_list[0].args[0][2])
        self.assertIn("install-site example_com", run.call_args_list[1].args[0][-1])

    def test_push_nginx_config_rejects_invalid_domain_and_upload_failure(self) -> None:
        with patch.object(remote_deploy, "get_deploy_target", return_value=TARGET), patch.object(remote_deploy.subprocess, "run") as run:
            self.assertFalse(remote_deploy.push_nginx_config(SITE, "app", "../etc"))
        run.assert_not_called()

        with patch.object(remote_deploy, "get_deploy_target", return_value=TARGET), patch.object(remote_deploy, "build_scp_command", return_value=["scp"]), patch.object(remote_deploy.subprocess, "run", return_value=completed(1, "upload failed")) as run:
            self.assertFalse(remote_deploy.push_nginx_config(SITE, "app", "example.com"))
        run.assert_called_once()


class TestRemoteDeploymentOperations(unittest.TestCase):
    def test_reload_and_restart_call_expected_remote_helpers(self) -> None:
        def build_ssh(target, remote):
            del target
            return ["ssh", remote]

        with patch.object(remote_deploy, "get_deploy_target", return_value=TARGET), patch.object(remote_deploy, "_build_ssh_cmd", side_effect=build_ssh), patch.object(remote_deploy.subprocess, "run", return_value=completed()) as run:
            self.assertTrue(remote_deploy.reload_nginx("app"))
            self.assertTrue(remote_deploy.restart_service("app", "node-api"))

        self.assertIn("reload-nginx", run.call_args_list[0].args[0][-1])
        self.assertIn("restart-service node-api", run.call_args_list[1].args[0][-1])

    def test_reload_and_restart_handle_invalid_target_service_and_timeout(self) -> None:
        with patch.object(remote_deploy, "get_deploy_target", return_value=None):
            self.assertFalse(remote_deploy.reload_nginx("missing"))
            self.assertFalse(remote_deploy.restart_service("missing", "node-api"))

        with patch.object(remote_deploy, "get_deploy_target", return_value=TARGET), patch.object(remote_deploy, "_build_ssh_cmd", return_value=["ssh"]), patch.object(remote_deploy.subprocess, "run", side_effect=subprocess.TimeoutExpired(["ssh"], 60)):
            self.assertFalse(remote_deploy.reload_nginx("app"))
            self.assertFalse(remote_deploy.restart_service("app", "node-api"))

        with patch.object(remote_deploy, "get_deploy_target", return_value=TARGET), patch.object(remote_deploy, "_build_ssh_cmd", side_effect=lambda target, remote: ["ssh", remote]), patch.object(remote_deploy.subprocess, "run", return_value=completed()) as run:
            self.assertTrue(remote_deploy.restart_service("app", "nginx"))
        self.assertIn("restart-service nginx", run.call_args.args[0][-1])

    def test_remove_deployment_validates_path_and_chains_nginx_cleanup(self) -> None:
        with patch.object(remote_deploy, "get_deploy_target", return_value=TARGET), patch.object(remote_deploy, "_build_ssh_cmd", side_effect=lambda target, remote: ["ssh", remote]), patch.object(remote_deploy.subprocess, "run", return_value=completed()) as run:
            self.assertTrue(remote_deploy.remove_deployment("app", "/var/www/shop", "shop.example.com"))

        command = run.call_args.args[0][-1]
        self.assertIn("rm -rf -- /var/www/shop", command)
        self.assertIn("remove-nginx shop_example_com", command)
        self.assertIn("reload-nginx", command)

        with patch.object(remote_deploy, "get_deploy_target", return_value=TARGET), patch.object(remote_deploy.subprocess, "run") as run:
            self.assertFalse(remote_deploy.remove_deployment("app", "/etc/passwd", "shop.example.com"))
        run.assert_not_called()

    def test_remove_deployment_rejects_invalid_domain_and_timeout(self) -> None:
        with patch.object(remote_deploy, "get_deploy_target", return_value=TARGET), patch.object(remote_deploy.subprocess, "run") as run:
            self.assertFalse(remote_deploy.remove_deployment("app", "/var/www/shop", "../etc"))
        run.assert_not_called()

        with patch.object(remote_deploy, "get_deploy_target", return_value=TARGET), patch.object(remote_deploy, "_build_ssh_cmd", return_value=["ssh"]), patch.object(remote_deploy.subprocess, "run", side_effect=subprocess.TimeoutExpired(["ssh"], 60)):
            self.assertFalse(remote_deploy.remove_deployment("app", "/var/www/shop"))

    def test_connection_reports_success_failure_and_timeout(self) -> None:
        with patch.object(remote_deploy, "get_deploy_target", return_value=TARGET), patch.object(remote_deploy, "_build_ssh_cmd", side_effect=lambda target, remote: ["ssh", remote]), patch.object(remote_deploy.subprocess, "run", return_value=completed()) as run:
            self.assertTrue(remote_deploy.test_deploy_connection("app"))
        self.assertIn("echo 'connection ok'", run.call_args.args[0][-1])

        with patch.object(remote_deploy, "get_deploy_target", return_value=TARGET), patch.object(remote_deploy, "_build_ssh_cmd", return_value=["ssh"]), patch.object(remote_deploy.subprocess, "run", side_effect=[completed(1, "denied"), subprocess.TimeoutExpired(["ssh"], 10)]):
            self.assertFalse(remote_deploy.test_deploy_connection("app"))
            self.assertFalse(remote_deploy.test_deploy_connection("app"))

        with patch.object(remote_deploy, "get_deploy_target", return_value=None):
            self.assertFalse(remote_deploy.test_deploy_connection("missing"))


if __name__ == "__main__":
    unittest.main()
