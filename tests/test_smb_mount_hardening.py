"""Regression tests for SMB client mount hardening defaults."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.config import SetupConfig
from smb import smb_mount_steps


class TestConfigureSmbMountUnit(unittest.TestCase):
    def test_unit_pins_smb3_and_uses_safe_file_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            etc_systemd = os.path.join(tmp, 'etc-systemd')
            creds_dir = os.path.join(tmp, 'creds')
            mountpoint = os.path.join(tmp, 'mnt-share')
            os.makedirs(etc_systemd)
            os.makedirs(creds_dir)

            real_open = open

            def fake_open(path, mode='r', *args, **kwargs):
                if path.startswith('/etc/systemd/system/'):
                    target = os.path.join(etc_systemd, os.path.basename(path))
                    return real_open(target, mode, *args, **kwargs)
                if path.startswith('/root/.smb/'):
                    target = os.path.join(creds_dir, os.path.basename(path))
                    return real_open(target, mode, *args, **kwargs)
                return real_open(path, mode, *args, **kwargs)

            def fake_run(cmd, **kwargs):
                res = MagicMock()
                res.returncode = 0
                if 'systemd-escape' in cmd:
                    escaped = mountpoint.lstrip('/').replace('/', '-')
                    res.stdout = escaped + '\n'
                else:
                    res.stdout = ''
                return res

            config = SetupConfig(host='h', username='alice', system_type='server_lite')

            with patch.object(smb_mount_steps, 'run', side_effect=fake_run), \
                 patch.object(smb_mount_steps, 'open', side_effect=fake_open, create=True), \
                 patch.object(smb_mount_steps, 'cleanup_systemd_unit'), \
                 patch.object(smb_mount_steps, 'validate_smb_mount_specs'), \
                 patch.object(smb_mount_steps, 'SMB_CREDENTIALS_DIR', creds_dir), \
                 patch.object(smb_mount_steps.pwd, 'getpwnam', return_value=MagicMock(pw_uid=123)), \
                 patch.object(smb_mount_steps.grp, 'getgrnam', return_value=MagicMock(gr_gid=456)), \
                 patch.object(smb_mount_steps.os, 'fchown'):
                smb_mount_steps.configure_smb_mount(
                    config,
                    mount_spec=[mountpoint, '192.168.1.10', 'svc:hunter2', 'docs', '/sub'],
                )

            unit_files = os.listdir(etc_systemd)
            self.assertEqual(len(unit_files), 1, f"unexpected units: {unit_files}")
            with real_open(os.path.join(etc_systemd, unit_files[0])) as f:
                unit_body = f.read()

            cred_files = os.listdir(creds_dir)
            self.assertEqual(len(cred_files), 1, f"unexpected creds: {cred_files}")
            with real_open(os.path.join(creds_dir, cred_files[0])) as f:
                cred_body = f.read()

        # Pinned to SMB3+ minimum and request encryption-on-the-wire.
        self.assertIn("vers=3.0", unit_body)
        self.assertIn("seal", unit_body)
        # Files should not be world-executable by default.
        self.assertIn("file_mode=0644", unit_body)
        self.assertIn("dir_mode=0755", unit_body)
        # Defensive defaults for boot-time behaviour are preserved.
        self.assertIn("nofail", unit_body)
        self.assertIn("x-systemd.automount", unit_body)
        # Credentials still contain the username/password lines.
        self.assertIn("username=svc", cred_body)
        self.assertIn("password=hunter2", cred_body)

    def test_distinct_mountpoints_use_distinct_credential_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            etc_systemd = os.path.join(tmp, "etc-systemd")
            creds_dir = os.path.join(tmp, "creds")
            first_mountpoint = os.path.join(tmp, "a_b")
            second_mountpoint = os.path.join(tmp, "a", "b")
            os.makedirs(etc_systemd)
            os.makedirs(creds_dir)

            real_open = open
            escaped_names = iter(("tmp-a_5fb", "tmp-a-b"))

            def fake_open(path, mode="r", *args, **kwargs):
                if path.startswith("/etc/systemd/system/"):
                    return real_open(os.path.join(etc_systemd, os.path.basename(path)), mode, *args, **kwargs)
                if path.startswith("/root/.smb/"):
                    return real_open(os.path.join(creds_dir, os.path.basename(path)), mode, *args, **kwargs)
                return real_open(path, mode, *args, **kwargs)

            def fake_run(command, **_kwargs):
                result = MagicMock(returncode=0, stdout="")
                if "systemd-escape" in command:
                    result.stdout = next(escaped_names) + "\n"
                return result

            config = SetupConfig(host="h", username="alice", system_type="server_lite")
            with patch.object(smb_mount_steps, "run", side_effect=fake_run), \
                 patch.object(smb_mount_steps, "open", side_effect=fake_open, create=True), \
                 patch.object(smb_mount_steps, "cleanup_systemd_unit"), \
                 patch.object(smb_mount_steps, "validate_smb_mount_specs"), \
                 patch.object(smb_mount_steps, 'SMB_CREDENTIALS_DIR', creds_dir), \
                 patch.object(smb_mount_steps.pwd, 'getpwnam', return_value=MagicMock(pw_uid=123)), \
                 patch.object(smb_mount_steps.grp, 'getgrnam', return_value=MagicMock(gr_gid=456)), \
                 patch.object(smb_mount_steps.os, 'fchown'):
                smb_mount_steps.configure_smb_mount(
                    config,
                    mount_spec=[first_mountpoint, "192.168.1.10", "first:one", "docs", "/"],
                )
                smb_mount_steps.configure_smb_mount(
                    config,
                    mount_spec=[second_mountpoint, "192.168.1.11", "second:two", "docs", "/"],
                )

            credential_files = sorted(os.listdir(creds_dir))
            self.assertEqual(
                credential_files,
                ["credentials-tmp-a-b", "credentials-tmp-a_5fb"],
            )

            with real_open(os.path.join(creds_dir, "credentials-tmp-a_5fb")) as file_obj:
                self.assertIn("username=first", file_obj.read())
            with real_open(os.path.join(creds_dir, "credentials-tmp-a-b")) as file_obj:
                self.assertIn("username=second", file_obj.read())

    def test_invalid_spec_is_rejected_before_filesystem_changes(self) -> None:
        config = SetupConfig(host="h", username="alice", system_type="server_lite")

        with patch.object(smb_mount_steps.os, "makedirs") as mock_makedirs, \
             patch.object(smb_mount_steps, "run") as mock_run:
            with self.assertRaisesRegex(ValueError, "control characters"):
                smb_mount_steps.configure_smb_mount(
                    config,
                    mount_spec=["/mnt/share", "192.168.1.10", "svc:secret\nvalue", "docs", "/"],
                )

        mock_makedirs.assert_not_called()
        mock_run.assert_not_called()

    def test_symlink_mount_or_credentials_components_fail_before_mutation(self):
        for credentials in (False, True):
            for leaf in (False, True):
                with self.subTest(credentials=credentials, leaf=leaf), tempfile.TemporaryDirectory() as root:
                    outside = os.path.join(root, 'outside')
                    os.mkdir(outside)
                    linked = os.path.join(root, 'linked')
                    os.symlink(outside, linked)
                    unsafe = linked if leaf else os.path.join(linked, 'new')
                    mount = os.path.join(root, 'mount') if credentials else unsafe
                    creds = unsafe if credentials else os.path.join(root, 'creds')
                    with patch.object(smb_mount_steps, 'SMB_CREDENTIALS_DIR', creds), \
                         patch.object(smb_mount_steps, 'validate_smb_mount_specs'), \
                         patch.object(smb_mount_steps, 'run') as run, \
                         patch.object(smb_mount_steps.os, 'fchown') as chown:
                        with self.assertRaisesRegex(ValueError, 'symlink'):
                            smb_mount_steps.configure_smb_mount(
                                SetupConfig(host='h', username='alice', system_type='server_lite'),
                                [mount, '192.168.1.10', 'svc:secret', 'docs', '/'],
                            )
                    run.assert_not_called()
                    chown.assert_not_called()
                    self.assertEqual(os.listdir(outside), [])
                    if credentials:
                        self.assertFalse(os.path.exists(mount))

    def test_credential_replacement_does_not_follow_link_or_modify_hardlink(self):
        for symlink in (False, True):
            with self.subTest(symlink=symlink), tempfile.TemporaryDirectory() as root:
                directory = os.path.join(root, 'creds')
                os.mkdir(directory)
                outside = os.path.join(root, 'outside')
                with open(outside, 'w') as stream:
                    stream.write('keep')
                target = os.path.join(directory, 'credentials-share')
                if symlink:
                    os.symlink(outside, target)
                else:
                    os.link(outside, target)
                with patch.object(smb_mount_steps.os, 'fchown'):
                    smb_mount_steps._write_credentials(directory, 'credentials-share', 'secret')
                with open(outside) as stream:
                    self.assertEqual(stream.read(), 'keep')
                with open(target) as stream:
                    self.assertEqual(stream.read(), 'secret')
                self.assertFalse(os.path.islink(target))
                self.assertEqual(os.stat(target).st_mode & 0o777, 0o600)
                self.assertEqual(os.stat(directory).st_mode & 0o777, 0o700)


if __name__ == '__main__':
    unittest.main()
