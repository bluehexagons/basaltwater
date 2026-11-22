"""Regression tests for safety boundaries used during setup upgrades."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from common import common_steps
from common.common_steps import copy_ssh_keys_to_user
from lib.config import SetupConfig
from lib.setup_common import prepare_deployments, run_remote_setup


def _config(**overrides: object) -> SetupConfig:
    values: dict[str, object] = {
        "host": "example.com",
        "username": "alice",
        "system_type": "server_web",
    }
    values.update(overrides)
    return SetupConfig(**values)


class TestDeploymentPreflight(unittest.TestCase):
    @patch("lib.setup_common.clone_repository")
    def test_dry_run_validates_staged_manifest(
        self, mock_clone: MagicMock
    ) -> None:
        config = _config(
            dry_run=True,
            deploy_specs=[["example.com", "https://example.com/app.git"]],
        )
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as target_dir:
            with open(os.path.join(repo_dir, "basaltwater.json"), "w", encoding="utf-8") as handle:
                handle.write('{"version": 1, "components": []}\n')
            mock_clone.return_value = (repo_dir, "abc123")

            with self.assertRaisesRegex(ValueError, "non-empty array"):
                prepare_deployments(config, target_dir)

    @patch("lib.setup_common.clone_repository", return_value=None)
    def test_any_clone_failure_aborts_staging(self, _mock_clone: MagicMock) -> None:
        config = _config(
            deploy_specs=[["example.com", "https://example.com/app.git"]]
        )

        with tempfile.TemporaryDirectory() as target_dir:
            with self.assertRaisesRegex(RuntimeError, "no target changes were started"):
                prepare_deployments(config, target_dir)

    def test_staging_errors_return_failure_and_clean_up_before_transport(self) -> None:
        config = _config(
            host="localhost", dry_run=True,
            deploy_specs=[["example.com", "https://example.com/app.git"]],
        )
        for error in (RuntimeError("fetch failed"), ValueError("invalid manifest"), OSError("disk full")):
            with self.subTest(error=error), tempfile.TemporaryDirectory() as directory:
                build_dir = os.path.join(directory, "build")
                os.mkdir(build_dir)
                output = io.StringIO()
                with patch("lib.setup_common.copy_project_files"), \
                     patch("lib.setup_common.tempfile.mkdtemp", return_value=build_dir), \
                     patch("lib.setup_common.prepare_deployments", side_effect=error), \
                     patch("lib.setup_common.run_streamed") as transport, \
                     redirect_stdout(output):
                    self.assertEqual(run_remote_setup(config), 1)
                transport.assert_not_called()
                self.assertIn(f"Error preparing deployments: {error}", output.getvalue())
                self.assertFalse(os.path.exists(build_dir))

    @patch("lib.setup_common.clone_repository")
    def test_ruby_repository_aborts_before_remote_setup(
        self, mock_clone: MagicMock
    ) -> None:
        config = _config(
            deploy_specs=[["legacy.example.com", "https://example.com/legacy.git"]]
        )
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as target_dir:
            with open(os.path.join(repo_dir, "Gemfile"), "w", encoding="utf-8") as handle:
                handle.write("source 'https://rubygems.org'\n")
            mock_clone.return_value = (repo_dir, "abc123")

            with self.assertRaisesRegex(RuntimeError, "no longer supported"):
                prepare_deployments(config, target_dir)

            self.assertFalse(os.path.exists(os.path.join(target_dir, "legacy.commit")))


class TestSshKeyUpgrade(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        root_ssh = self.root / "root-ssh"
        root_ssh.mkdir(mode=0o700)
        root_keys = root_ssh / "authorized_keys"
        root_keys.write_text("ssh-ed25519 ROOT\nssh-ed25519 EXISTING\n")
        root_inodes = {root_ssh.stat().st_ino, root_keys.stat().st_ino}
        self.home = self.root / "home"
        self.home.mkdir(mode=0o700)
        self.ssh = self.home / ".ssh"
        self.keys = self.ssh / "authorized_keys"
        uid = os.getuid() or 1000
        gid = os.getgid() or 1000
        account = SimpleNamespace(pw_dir=str(self.home), pw_uid=uid, pw_gid=gid)
        self.enterContext(patch.object(common_steps, "_ROOT_SSH_DIRECTORY", str(root_ssh)))
        self.enterContext(patch.object(common_steps.pwd, "getpwnam", return_value=account))
        fstat = os.fstat

        def stat_as_setup(fd):
            info = fstat(fd)
            return SimpleNamespace(
                st_uid=0 if info.st_ino in root_inodes else uid,
                st_gid=0 if info.st_ino in root_inodes else gid,
                st_dev=info.st_dev, st_ino=info.st_ino, st_mode=info.st_mode,
                st_nlink=info.st_nlink,
            )

        self.enterContext(patch.object(common_steps.os, "fstat", side_effect=stat_as_setup))
        self.chown = self.enterContext(patch.object(common_steps.os, "fchown"))
        real_stat = os.stat

        def stat_keys(path, *, dir_fd=None, follow_symlinks=True):
            info = real_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)
            if dir_fd is not None and path == "authorized_keys":
                return SimpleNamespace(
                    st_uid=uid, st_gid=gid, st_dev=info.st_dev,
                    st_ino=info.st_ino, st_mode=info.st_mode, st_nlink=info.st_nlink,
                )
            return info

        self.enterContext(patch.object(common_steps.os, "stat", side_effect=stat_keys))

    def test_existing_user_keys_are_preserved_and_root_keys_deduplicated(self) -> None:
        self.ssh.mkdir(mode=0o755)
        self.keys.write_text("ssh-ed25519 EXISTING\nssh-rsa USERONLY\n")
        copy_ssh_keys_to_user(_config())
        self.assertEqual(self.keys.read_text(), "ssh-ed25519 EXISTING\nssh-rsa USERONLY\nssh-ed25519 ROOT\n")
        self.assertEqual(self.ssh.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.keys.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.chown.call_count, 2)

    def test_fresh_home_gets_private_keys_file(self) -> None:
        copy_ssh_keys_to_user(_config())
        self.assertEqual(self.keys.read_text(), "ssh-ed25519 ROOT\nssh-ed25519 EXISTING\n")
        self.assertEqual(self.keys.stat().st_mode & 0o777, 0o600)

    def test_symlinked_authorized_keys_is_refused(self) -> None:
        self.ssh.mkdir(mode=0o700)
        other = self.root / "other"
        other.write_text("unrelated\n")
        self.keys.symlink_to(other)
        with self.assertRaisesRegex(RuntimeError, "regular authorized_keys"):
            copy_ssh_keys_to_user(_config())
        self.assertEqual(other.read_text(), "unrelated\n")
        self.chown.assert_not_called()


if __name__ == "__main__":
    unittest.main()
