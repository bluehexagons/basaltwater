"""Regression coverage for effective SSH policy and drop-in migration."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lib.config import SetupConfig
from security import security_steps as ssh


EFFECTIVE = """permitrootlogin without-password
passwordauthentication no
kbdinteractiveauthentication no
pubkeyauthentication yes
authenticationmethods publickey
allowgroups remoteusers
"""


class TestEffectiveSSHPolicy(unittest.TestCase):
    def setUp(self):
        self.config = SetupConfig(host="pve1", username="root", system_type="server_proxmox")
        self.enterContext(patch.dict(os.environ, {"SSH_CONNECTION": "198.51.100.10 12345 192.0.2.10 22"}))

    def test_conflicting_password_setting_or_missing_group_is_rejected(self):
        for output, groups, error in (
            (EFFECTIVE.replace("passwordauthentication no", "passwordauthentication yes"), "root remoteusers", "passwordauthentication"),
            (EFFECTIVE, "root", "member of remoteusers"),
            ("", "root remoteusers", "conflicts"),
            (EFFECTIVE.replace("authenticationmethods publickey", "authenticationmethods publickey,password"), "root remoteusers", "authenticationmethods"),
        ):
            with self.subTest(error=error), patch.object(ssh, "run") as run:
                run.side_effect = [SimpleNamespace(returncode=0, stdout=output), SimpleNamespace(returncode=0, stdout=groups)]
                with self.assertRaisesRegex(RuntimeError, error):
                    ssh._verify_ssh_policy("/usr/sbin/sshd", self.config)

    def test_checks_root_and_setup_identity_with_current_peer(self):
        self.config.username = "agent"
        with patch.object(ssh, "run") as run:
            run.side_effect = lambda command, **kwargs: SimpleNamespace(
                returncode=0, stdout=EFFECTIVE if " -T " in command else "remoteusers"
            )
            ssh._verify_ssh_policy("/usr/sbin/sshd", self.config)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn("/usr/sbin/sshd -T -C user=root,host=198.51.100.10,addr=198.51.100.10", commands)
        self.assertIn("/usr/sbin/sshd -T -C user=agent,host=198.51.100.10,addr=198.51.100.10", commands)

    def test_effective_conflict_rolls_back_and_never_reloads(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "00-basaltwater-hardening.conf"
            path.write_text("# previous configuration\n")
            with (
                patch.object(ssh, "_SSHD_DROPIN_DIR", root),
                patch.object(ssh, "_SSHD_DROPIN_FILE", str(path)),
                patch.object(ssh, "_LEGACY_SSHD_DROPIN_FILE", str(Path(root) / "99-legacy.conf")),
                patch.object(ssh, "shutil") as shutil,
                patch.object(ssh, "is_dry_run", return_value=False),
                patch.object(ssh, "run") as run,
            ):
                shutil.which.return_value = "/usr/sbin/sshd"
                run.side_effect = lambda command, **kwargs: SimpleNamespace(
                    returncode=0, stdout=EFFECTIVE.replace("passwordauthentication no", "passwordauthentication yes")
                )
                with self.assertRaisesRegex(RuntimeError, "passwordauthentication"):
                    ssh.harden_ssh(self.config)
            self.assertEqual(path.read_text(), "# previous configuration\n")
            self.assertFalse(any("reload" in call.args[0] for call in run.call_args_list))

    def test_managed_legacy_migration_and_rerun_verify_without_rewriting(self):
        with tempfile.TemporaryDirectory() as root:
            path, legacy = Path(root) / "00-new.conf", Path(root) / "99-old.conf"
            legacy.write_text("# Managed by basaltwater - SSH hardening drop-in.\nPasswordAuthentication no\n")
            with (
                patch.object(ssh, "_SSHD_DROPIN_DIR", root),
                patch.object(ssh, "_SSHD_DROPIN_FILE", str(path)),
                patch.object(ssh, "_LEGACY_SSHD_DROPIN_FILE", str(legacy)),
                patch.object(ssh, "shutil") as shutil,
                patch.object(ssh, "is_dry_run", return_value=False),
                patch.object(ssh, "run") as run,
            ):
                shutil.which.return_value = "/usr/sbin/sshd"
                run.side_effect = lambda command, **kwargs: SimpleNamespace(
                    returncode=0, stdout=EFFECTIVE if " -T " in command else "remoteusers"
                )
                ssh.harden_ssh(self.config)
                self.assertFalse(legacy.exists())
                self.assertTrue(any("reload" in call.args[0] for call in run.call_args_list))
                run.reset_mock()
                with patch.object(ssh, "write_text_atomic") as write:
                    ssh.harden_ssh(self.config)
                    write.assert_not_called()
                self.assertTrue(any(" -T " in call.args[0] for call in run.call_args_list))
                self.assertFalse(any("reload" in call.args[0] for call in run.call_args_list))
                legacy.write_text("# operator-owned\n")
                ssh.harden_ssh(self.config)
                self.assertEqual(legacy.read_text(), "# operator-owned\n")
