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
        self.enterContext(patch.object(ssh, "can_manage_system_services", return_value=True))
        self.activate = self.enterContext(patch.object(ssh, "_activate_ssh_service"))

    def test_conflicting_password_setting_or_missing_group_is_rejected(self):
        for output, groups, error in (
            (EFFECTIVE.replace("passwordauthentication no", "passwordauthentication yes"), "root remoteusers", "passwordauthentication"),
            (EFFECTIVE, "root", "member of remoteusers"),
            ("", "root remoteusers", "conflicts"),
            (EFFECTIVE.replace("authenticationmethods publickey", "authenticationmethods publickey,password"), "root remoteusers", "authenticationmethods"),
            (EFFECTIVE.replace("authenticationmethods publickey", "authenticationmethods any") + "gssapiauthentication yes\n", "root remoteusers", "authenticationmethods"),
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
            self.activate.assert_not_called()

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
                self.activate.assert_called_once_with(changed=True)
                run.reset_mock()
                self.activate.reset_mock()
                with patch.object(ssh, "write_text_atomic") as write:
                    ssh.harden_ssh(self.config)
                    write.assert_not_called()
                self.assertTrue(any(" -T " in call.args[0] for call in run.call_args_list))
                self.activate.assert_called_once_with(changed=False)
                legacy.write_text("# operator-owned\n")
                ssh.harden_ssh(self.config)
                self.assertEqual(legacy.read_text(), "# operator-owned\n")

    def test_legacy_migration_verifies_final_policy_and_restores_files_on_conflict(self):
        with tempfile.TemporaryDirectory() as root:
            path, legacy = Path(root) / "00-new.conf", Path(root) / "99-old.conf"
            legacy_content = "# Managed by basaltwater - SSH hardening drop-in.\nPasswordAuthentication no\n"
            legacy.write_text(legacy_content)

            def verify(*_):
                self.assertFalse(legacy.exists())
                raise RuntimeError("Effective SSH policy conflicts")

            with (
                patch.object(ssh, "_SSHD_DROPIN_DIR", root),
                patch.object(ssh, "_SSHD_DROPIN_FILE", str(path)),
                patch.object(ssh, "_LEGACY_SSHD_DROPIN_FILE", str(legacy)),
                patch.object(ssh.shutil, "which", return_value="/usr/sbin/sshd"),
                patch.object(ssh, "is_dry_run", return_value=False),
                patch.object(ssh, "run", return_value=SimpleNamespace(returncode=0)),
                patch.object(ssh, "_verify_ssh_policy", side_effect=verify),
            ):
                with self.assertRaisesRegex(RuntimeError, "Effective SSH policy conflicts"):
                    ssh.harden_ssh(self.config)
            self.assertFalse(path.exists())
            self.assertEqual(legacy.read_text(), legacy_content)
            self.activate.assert_not_called()

    def test_activation_failure_restores_both_dropins_before_service_recovery(self):
        for previous in (None, "# previous configuration\n"):
            with self.subTest(previous=previous), tempfile.TemporaryDirectory() as root:
                path, legacy = Path(root) / "00-new.conf", Path(root) / "99-old.conf"
                legacy_content = "# Managed by basaltwater - SSH hardening drop-in.\nPasswordAuthentication no\n"
                legacy.write_text(legacy_content)
                if previous is not None:
                    path.write_text(previous)
                attempts = []

                def activate(**kwargs):
                    attempts.append(kwargs)
                    if len(attempts) == 1:
                        self.assertFalse(legacy.exists())
                        raise RuntimeError("SSH service activation failed")
                    self.assertEqual(path.read_text() if path.exists() else None, previous)
                    self.assertEqual(legacy.read_text(), legacy_content)

                self.activate.side_effect = activate
                with (
                    patch.object(ssh, "_SSHD_DROPIN_DIR", root),
                    patch.object(ssh, "_SSHD_DROPIN_FILE", str(path)),
                    patch.object(ssh, "_LEGACY_SSHD_DROPIN_FILE", str(legacy)),
                    patch.object(ssh.shutil, "which", return_value="/usr/sbin/sshd"),
                    patch.object(ssh, "is_dry_run", return_value=False),
                    patch.object(ssh, "run") as run,
                ):
                    run.side_effect = lambda command, **_: SimpleNamespace(
                        returncode=0, stdout=EFFECTIVE if " -T " in command else "remoteusers"
                    )
                    with self.assertRaisesRegex(RuntimeError, "SSH service activation failed"):
                        ssh.harden_ssh(self.config)
                self.assertEqual(attempts, [{"changed": True}, {"changed": True}])
                self.assertEqual(path.read_text() if path.exists() else None, previous)
                self.assertEqual(legacy.read_text(), legacy_content)

    def test_oci_validates_configuration_without_systemd_activation(self):
        with (
            tempfile.TemporaryDirectory() as root,
            patch.object(ssh, "_SSHD_DROPIN_DIR", root),
            patch.object(ssh, "_SSHD_DROPIN_FILE", str(Path(root) / "00-new.conf")),
            patch.object(ssh, "_LEGACY_SSHD_DROPIN_FILE", str(Path(root) / "99-old.conf")),
            patch.object(ssh.shutil, "which", return_value="/usr/sbin/sshd"),
            patch.object(ssh, "is_dry_run", return_value=False),
            patch.object(ssh, "can_manage_system_services", return_value=False) as capability,
            patch.object(ssh, "run") as run,
        ):
            self.config.machine_type = "oci"
            run.side_effect = lambda command, **_: SimpleNamespace(
                returncode=0, stdout=EFFECTIVE if " -T " in command else "remoteusers"
            )
            ssh.harden_ssh(self.config)
        capability.assert_called_once_with("oci")
        self.activate.assert_not_called()


def _result(stdout="", returncode=0, stderr=""):
    return SimpleNamespace(stdout=stdout, returncode=returncode, stderr=stderr)


class TestSSHServiceActivation(unittest.TestCase):
    def setUp(self):
        self.run = self.enterContext(patch.object(ssh, "run"))

    def commands(self):
        return [call.args[0] for call in self.run.call_args_list]

    def test_active_service_reloads_once_and_never_tries_its_alias(self):
        self.run.side_effect = [_result("loaded"), _result("active"), _result(), _result("active")]
        ssh._activate_ssh_service(changed=True)
        self.assertIn("systemctl reload ssh.service", self.commands())
        self.assertFalse(any("sshd.service" in command or "restart" in command for command in self.commands()))

    def test_unchanged_active_service_is_not_reloaded(self):
        self.run.side_effect = [_result("loaded"), _result("active")]
        ssh._activate_ssh_service(changed=False)
        self.assertEqual(len(self.commands()), 2)

    def test_inactive_or_failed_service_starts_even_when_config_is_unchanged(self):
        for state in ("inactive", "failed"):
            with self.subTest(state=state):
                self.run.reset_mock()
                results = [_result("loaded"), _result(state, 3)]
                if state == "inactive":
                    results.append(_result(returncode=3))
                self.run.side_effect = results + [_result(), _result("active")]
                ssh._activate_ssh_service(changed=False)
                self.assertIn("systemctl start ssh.service", self.commands())
                self.assertNotIn("systemctl reload ssh.service", self.commands())

    def test_idle_socket_activation_is_preserved(self):
        for changed in (False, True):
            with self.subTest(changed=changed):
                self.run.reset_mock()
                self.run.side_effect = [_result("loaded"), _result("inactive", 3), _result()]
                ssh._activate_ssh_service(changed=changed)
                self.assertEqual(self.commands()[-1], "systemctl is-active --quiet ssh.socket sshd.socket")
                self.assertEqual(len(self.commands()), 3)

    def test_failed_service_is_recovered_even_when_socket_activation_is_configured(self):
        self.run.side_effect = [_result("loaded"), _result("failed", 3), _result(), _result("active")]
        ssh._activate_ssh_service(changed=True)
        self.assertIn("systemctl start ssh.service", self.commands())
        self.assertFalse(any(".socket" in command for command in self.commands()))

    def test_failed_reload_that_stops_daemon_recovers_with_start(self):
        self.run.side_effect = [
            _result("loaded"), _result("active"), _result(returncode=1, stderr="reload failed"),
            _result("failed", 3), _result(), _result("active"),
        ]
        ssh._activate_ssh_service(changed=True)
        self.assertEqual([command for command in self.commands() if command.startswith("systemctl start")],
                         ["systemctl start ssh.service"])
        self.assertFalse(any("sshd.service" in command or "restart" in command for command in self.commands()))

    def test_failed_reload_of_still_running_service_is_reported_without_restart(self):
        self.run.side_effect = [
            _result("loaded"), _result("active"), _result(returncode=1, stderr="reload failed"),
            _result("active"), _result("journal details"),
        ]
        with self.assertRaisesRegex(RuntimeError, "reload failed"):
            ssh._activate_ssh_service(changed=True)
        self.assertIn("journalctl -u ssh.service -n 20 --no-pager", self.commands())
        self.assertFalse(any("start " in command for command in self.commands()))

    def test_sshd_unit_is_selected_only_when_ssh_unit_is_missing(self):
        self.run.side_effect = [_result("not-found", 1), _result("loaded"), _result("inactive", 3),
                                _result(returncode=3), _result(), _result("active")]
        ssh._activate_ssh_service(changed=True)
        self.assertIn("systemctl start sshd.service", self.commands())
        self.assertNotIn("systemctl start ssh.service", self.commands())

    def test_masked_missing_and_unreadable_units_fail_without_mutation(self):
        for results in (
            [_result("masked")], [_result("not-found", 1), _result("not-found", 1)],
            [_result("", 1, "cannot connect to systemd")], [_result("loaded"), _result("unknown", 3)],
        ):
            with self.subTest(results=results):
                self.run.reset_mock()
                self.run.side_effect = results
                with self.assertRaises(RuntimeError):
                    ssh._activate_ssh_service(changed=True)
                self.assertFalse(any("systemctl start " in command or "systemctl reload " in command
                                     or "unmask" in command for command in self.commands()))

    def test_failed_start_or_nonactive_final_state_fails_with_bounded_diagnostics(self):
        for status, result in (("failed", _result(returncode=1, stderr="port already in use")),
                               ("inactive", _result())):
            with self.subTest(status=status):
                self.run.reset_mock()
                self.run.side_effect = [_result("loaded"), _result("inactive", 3),
                                        _result(returncode=3), result, _result(status, 3), _result("journal details")]
                with self.assertRaisesRegex(RuntimeError, "SSH service activation failed"):
                    ssh._activate_ssh_service(changed=True)
                self.assertEqual(self.run.call_args.kwargs["timeout"], 15)

    def test_journal_failure_preserves_the_original_service_error(self):
        self.run.side_effect = [
            _result("loaded"), _result("inactive", 3), _result(returncode=3),
            _result(returncode=1, stderr="port already in use"), _result("failed", 3),
            TimeoutError("journal unavailable"),
        ]
        with self.assertRaisesRegex(RuntimeError, "port already in use"):
            ssh._activate_ssh_service(changed=False)


class TestSSHConfigValidation(unittest.TestCase):
    def setUp(self):
        self.run = self.enterContext(patch.object(ssh, "run"))
        self.root = self.enterContext(tempfile.TemporaryDirectory())
        self.runtime = Path(self.root) / "sshd"
        self.enterContext(patch.object(ssh, "_SSHD_RUNTIME_DIR", str(self.runtime)))
        self.enterContext(patch.object(ssh.os, "geteuid", return_value=0))

    def missing_runtime(self):
        return _result(returncode=255, stderr=f"Missing privilege separation directory: {self.runtime}\n")

    def test_missing_runtime_is_created_before_validation_is_retried(self):
        self.run.side_effect = [self.missing_runtime(), _result()]
        with patch.object(ssh.os, "lstat", side_effect=lambda _: SimpleNamespace(
            st_uid=0, st_mode=self.runtime.stat().st_mode,
        )):
            ssh._validate_ssh_config("/usr/sbin/sshd")
        self.assertTrue(self.runtime.is_dir())
        self.assertFalse(self.runtime.stat().st_mode & 0o022)
        self.assertEqual(self.run.call_count, 2)
        self.assertTrue(all(call.args[0] == "/usr/sbin/sshd -t" for call in self.run.call_args_list))

    def test_unrelated_validation_error_never_creates_runtime_directory(self):
        self.run.return_value = _result(returncode=255, stderr="Bad configuration option")
        with self.assertRaisesRegex(RuntimeError, "Bad configuration option"):
            ssh._validate_ssh_config("/usr/sbin/sshd")
        self.assertFalse(self.runtime.exists())
        self.run.assert_called_once()

    def test_retry_failure_is_reported(self):
        self.run.side_effect = [self.missing_runtime(), _result(returncode=255, stderr="no hostkeys available")]
        with patch.object(ssh.os, "lstat", return_value=SimpleNamespace(st_uid=0, st_mode=0o40755)):
            with self.assertRaisesRegex(RuntimeError, "no hostkeys available"):
                ssh._validate_ssh_config("/usr/sbin/sshd")

    def test_unsafe_runtime_paths_are_rejected_without_replacing_them(self):
        for kind in ("file", "symlink", "writable", "wrong-owner"):
            with self.subTest(kind=kind):
                if kind == "file":
                    self.runtime.write_text("keep")
                elif kind == "symlink":
                    self.runtime.symlink_to(self.root, target_is_directory=True)
                else:
                    self.runtime.mkdir(mode=0o777 if kind == "writable" else 0o755)
                    if kind == "writable":
                        self.runtime.chmod(0o777)
                mode = self.runtime.lstat().st_mode
                self.run.reset_mock()
                self.run.return_value = self.missing_runtime()
                with patch.object(ssh.os, "lstat", return_value=SimpleNamespace(
                    st_uid=1000 if kind == "wrong-owner" else 0, st_mode=mode,
                )):
                    with self.assertRaisesRegex(RuntimeError, "root-owned directory"):
                        ssh._validate_ssh_config("/usr/sbin/sshd")
                self.run.assert_called_once()
                if kind in {"file", "symlink"}:
                    self.runtime.unlink()
                else:
                    self.runtime.rmdir()

    def test_nonroot_validation_does_not_create_runtime_directory(self):
        self.run.return_value = self.missing_runtime()
        with patch.object(ssh.os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(RuntimeError, "Root privileges"):
                ssh._validate_ssh_config("/usr/sbin/sshd")
        self.assertFalse(self.runtime.exists())
