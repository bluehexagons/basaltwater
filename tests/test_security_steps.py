"""Tests for security.security_steps auto-update configuration."""

from __future__ import annotations

import os
from contextlib import nullcontext
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import mock_open, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.config import SetupConfig
from lib.maintenance_defaults import JOURNAL_MAX_USE
from lib.remote_utils import CommandExecutionError
from lib.system_types import get_steps_for_system_type
from security.security_steps import (
    _ensure_browser_automation_userns_profile,
    configure_auto_restart,
    configure_auto_updates,
    configure_apparmor,
    configure_auditd,
    configure_cleanup_maintenance,
    configure_fail2ban,
    configure_firewall,
    configure_firewall_ssh_only,
    configure_firewall_web,
    configure_pam_lockout,
    configure_security_monitor,
    harden_kernel,
    harden_ssh,
)


class TestAppArmorProfiles(unittest.TestCase):
    @patch("security.security_steps.run")
    @patch("security.security_steps.os.path.isfile", return_value=True)
    @patch(
        "security.security_steps._apparmor_userns_restriction_enabled",
        return_value=True,
    )
    def test_loads_browser_sandbox_profile_when_userns_is_restricted(
        self, _restricted, _isfile, mock_run
    ):
        mock_run.return_value = SimpleNamespace(returncode=0)

        self.assertTrue(_ensure_browser_automation_userns_profile())
        mock_run.assert_called_once_with(
            "apparmor_parser -r -W /etc/apparmor.d/unprivileged_userns",
            check=False,
        )

    @patch("security.security_steps.run")
    @patch(
        "security.security_steps._apparmor_userns_restriction_enabled",
        return_value=False,
    )
    def test_does_not_require_userns_profile_when_kernel_does_not_restrict_it(
        self, _restricted, mock_run
    ):
        self.assertTrue(_ensure_browser_automation_userns_profile())
        mock_run.assert_not_called()

    @patch("security.security_steps._ensure_browser_automation_userns_profile")
    @patch("security.security_steps.is_hardware", return_value=False)
    @patch("security.security_steps.is_vm", return_value=True)
    @patch("security.security_steps.run")
    def test_configure_uses_supported_reload_and_preserves_declared_modes(
        self, mock_run, _vm, _hardware, mock_userns
    ):
        mock_userns.return_value = True

        def run_side_effect(command, **_kwargs):
            return SimpleNamespace(returncode=0)

        mock_run.side_effect = run_side_effect

        configure_apparmor(
            SetupConfig(username="u", host="h", system_type="workstation_dev")
        )

        commands = [call.args[0] for call in mock_run.call_args_list]
        mock_run.assert_any_call(
            "apt-get -o DPkg::Lock::Timeout=300 install -y -qq apparmor apparmor-utils"
        )
        self.assertIn("systemctl reload apparmor", commands)
        self.assertNotIn("systemctl restart apparmor", commands)
        self.assertFalse(any(command.startswith("aa-enforce ") for command in commands))
        mock_userns.assert_called_once_with()

    @patch("security.security_steps._ensure_browser_automation_userns_profile")
    @patch("security.security_steps.is_hardware", return_value=False)
    @patch("security.security_steps.is_vm", return_value=True)
    @patch("security.security_steps.run")
    def test_configure_starts_service_when_policy_is_not_active(
        self, mock_run, _vm, _hardware, mock_userns
    ):
        mock_userns.return_value = True
        aa_enabled_results = iter((1, 0))

        def run_side_effect(command, **_kwargs):
            if command == "aa-enabled -q":
                return SimpleNamespace(returncode=next(aa_enabled_results))
            return SimpleNamespace(returncode=0)

        mock_run.side_effect = run_side_effect

        configure_apparmor(
            SetupConfig(username="u", host="h", system_type="workstation_dev")
        )

        commands = [call.args[0] for call in mock_run.call_args_list]
        self.assertIn("systemctl start apparmor", commands)
        self.assertIn("systemctl reload apparmor", commands)

    @patch(
        "security.security_steps._ensure_browser_automation_userns_profile",
        return_value=False,
    )
    @patch("security.security_steps.is_hardware", return_value=False)
    @patch("security.security_steps.is_vm", return_value=True)
    @patch("security.security_steps.run")
    def test_configure_fails_when_required_browser_sandbox_policy_cannot_load(
        self, mock_run, _vm, _hardware, _mock_userns
    ):
        mock_run.return_value = SimpleNamespace(returncode=0)

        with self.assertRaisesRegex(
            RuntimeError, "browser-sandbox compatibility profile failed"
        ):
            configure_apparmor(
                SetupConfig(
                    username="u", host="h", system_type="workstation_dev"
                )
            )


class TestHardenSSH(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("security.security_steps.managed_transport_groups", return_value=[]))
        self.enterContext(patch("security.security_steps.ssh_admission_lock", side_effect=lambda _: nullcontext()))
        self.enterContext(patch("security.security_steps._verify_ssh_policy"))
        self.enterContext(patch("security.security_steps.open", mock_open()))
        self.enterContext(patch("security.security_steps.can_manage_system_services", return_value=True))
        self.activate = self.enterContext(patch("security.security_steps._activate_ssh_service"))

    @patch("security.security_steps.shutil.which", return_value="/usr/sbin/sshd")
    @patch("security.security_steps.run")
    @patch("security.security_steps.os.makedirs")
    @patch("security.security_steps.write_text_atomic")
    @patch("security.security_steps.os.path.exists", return_value=False)
    def test_writes_dropin_with_hardening_directives(
        self, _exists, mock_write, _md, mock_run, _which
    ):
        mock_run.return_value = SimpleNamespace(returncode=0)
        harden_ssh(SetupConfig(username="u", host="h", system_type="server_lite"))

        mock_write.assert_called_once()
        self.assertEqual(
            mock_write.call_args.args[0],
            "/etc/ssh/sshd_config.d/00-basaltwater-hardening.conf",
        )
        self.assertEqual(mock_write.call_args.kwargs, {"mode": 0o600})
        written = mock_write.call_args.args[1]
        for directive in (
            "PermitRootLogin prohibit-password",
            "PasswordAuthentication no",
            "MaxAuthTries 3",
            "AllowGroups remoteusers",
            "ClientAliveInterval 300",
        ):
            self.assertIn(directive, written)
        self.assertNotIn("Match User", written)

    @patch("security.security_steps.shutil.which", return_value="/usr/sbin/sshd")
    @patch("security.security_steps.run")
    @patch("security.security_steps.os.makedirs")
    @patch("security.security_steps.write_text_atomic")
    @patch("security.security_steps.os.path.exists", return_value=False)
    def test_hardened_user_disables_ssh_forwarding_and_user_rc(
        self, _exists, mock_write, _md, mock_run, _which
    ):
        mock_run.return_value = SimpleNamespace(returncode=0)

        harden_ssh(
            SetupConfig(
                username="agent",
                host="h",
                system_type="server_lite",
                harden_user=True,
            )
        )

        written = mock_write.call_args.args[1]
        self.assertIn("Match User agent", written)
        self.assertIn("    DisableForwarding yes", written)
        self.assertIn("    PermitUserRC no", written)
        self.assertTrue(written.endswith("Match all\n"))

    def test_hardened_user_rejects_invalid_username(self):
        config = SetupConfig(
            username="agent bad",
            host="h",
            system_type="server_lite",
            harden_user=True,
        )

        with self.assertRaisesRegex(ValueError, "invalid username"):
            harden_ssh(config)

    @patch("security.security_steps.shutil.which", return_value="/usr/sbin/sshd")
    @patch("security.security_steps.run")
    @patch("security.security_steps.os.makedirs")
    @patch("security.security_steps.write_text_atomic")
    @patch("security.security_steps.os.path.exists", return_value=False)
    def test_validates_sshd_before_reload(
        self, _exists, _write, _md, mock_run, _which
    ):
        mock_run.return_value = SimpleNamespace(returncode=0)
        harden_ssh(SetupConfig(username="u", host="h", system_type="server_lite"))
        run_commands = [args[0] for args, _ in mock_run.call_args_list]
        self.assertIn("/usr/sbin/sshd -t", run_commands)
        self.activate.assert_called_once_with(changed=True)

    @patch("security.security_steps.shutil.which", return_value="/usr/sbin/sshd")
    @patch("security.security_steps.run")
    @patch("security.security_steps.os.makedirs")
    @patch("security.security_steps.write_text_atomic")
    @patch("security.security_steps.os.path.exists", return_value=False)
    @patch("security.security_steps.os.remove")
    def test_removes_new_dropin_when_validation_fails(
        self, mock_remove, _exists, _write, _md, mock_run, _which
    ):
        mock_run.return_value = SimpleNamespace(returncode=1)
        with self.assertRaisesRegex(RuntimeError, "sshd -t failed"):
            harden_ssh(SetupConfig(username="u", host="h", system_type="server_lite"))
        run_commands = [args[0] for args, _ in mock_run.call_args_list]
        self.assertIn("/usr/sbin/sshd -t", run_commands)
        self.activate.assert_not_called()
        mock_remove.assert_called_once_with(
            "/etc/ssh/sshd_config.d/00-basaltwater-hardening.conf"
        )

    @patch("security.security_steps.shutil.which", return_value="/usr/sbin/sshd")
    @patch("security.security_steps.run")
    @patch("security.security_steps.os.makedirs")
    @patch("security.security_steps.open", new_callable=mock_open, read_data="previous\n")
    @patch("security.security_steps.write_text_atomic")
    @patch("security.security_steps.os.path.exists", return_value=True)
    def test_restores_existing_dropin_when_validation_fails(
        self, _exists, mock_write, _file, _md, mock_run, _which
    ):
        mock_run.return_value = SimpleNamespace(returncode=1)

        with self.assertRaisesRegex(RuntimeError, "sshd -t failed"):
            harden_ssh(SetupConfig(username="u", host="h", system_type="server_lite"))

        self.assertEqual(mock_write.call_args_list[-1].args[1], "previous\n")
        self.assertEqual(mock_write.call_args_list[-1].kwargs, {"mode": 0o600})
        run_commands = [args[0] for args, _ in mock_run.call_args_list]
        self.activate.assert_not_called()

    @patch("security.security_steps.shutil.which", return_value=None)
    @patch("security.security_steps.os.path.isfile", return_value=False)
    @patch("security.security_steps.os.makedirs")
    @patch("security.security_steps.write_text_atomic")
    @patch("security.security_steps.run")
    def test_skips_when_sshd_is_not_installed(self, mock_run, write, makedirs, _isfile, _which):
        harden_ssh(SetupConfig(username="u", host="h", system_type="server_lite"))
        mock_run.assert_not_called()
        makedirs.assert_not_called()
        write.assert_not_called()

    @patch("security.security_steps.shutil.which", return_value="/usr/sbin/sshd")
    @patch("security.security_steps.run")
    @patch(
        "security.security_steps.os.makedirs",
        side_effect=PermissionError(13, "Permission denied"),
    )
    def test_skips_when_dropin_directory_is_not_writable(self, _md, mock_run, _which):
        harden_ssh(SetupConfig(username="u", host="h", system_type="server_lite"))
        mock_run.assert_not_called()

    @patch("security.security_steps.shutil.which", return_value="/usr/sbin/sshd")
    @patch("security.security_steps.run")
    @patch("security.security_steps.os.makedirs")
    @patch(
        "security.security_steps.write_text_atomic",
        side_effect=PermissionError(13, "Permission denied"),
    )
    @patch("security.security_steps.os.path.exists", return_value=False)
    def test_skips_when_dropin_file_is_not_writable(
        self, _exists, _write, _md, mock_run, _which
    ):
        harden_ssh(SetupConfig(username="u", host="h", system_type="server_lite"))
        mock_run.assert_not_called()


class TestHardenKernel(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.sysctl_file = os.path.join(temporary, "hardening.conf")
        self.enterContext(patch("security.security_steps._SYSCTL_HARDENING_FILE", self.sysctl_file))
        self.capability = self.enterContext(patch("security.security_steps.can_modify_kernel", return_value=True))
        self.enterContext(patch("security.security_steps.is_dry_run", return_value=False))
        self.command = self.enterContext(patch("security.security_steps.run"))
        self.live = {}
        self.absent = set()
        real_exists = os.path.exists

        def exists(path):
            if path.startswith("/proc/sys/"):
                return path not in self.absent
            return real_exists(path)

        self.enterContext(patch("security.security_steps.os.path.exists", side_effect=exists))
        self.command.side_effect = self.apply_setting
        self.config = SetupConfig(username="u", host="h", system_type="server_lite")

    def apply_setting(self, command, **kwargs):
        self.assertNotEqual(kwargs.get("check"), False)
        if command.startswith("sysctl -n "):
            key = command.removeprefix("sysctl -n ")
            return SimpleNamespace(returncode=0, stdout=self.live.get(key, "0") + "\n")
        self.assertTrue(command.startswith("sysctl -w "))
        key, value = command.removeprefix("sysctl -w ").split("=", 1)
        self.live[key] = value
        return SimpleNamespace(returncode=0)

    def read_config(self):
        with open(self.sysctl_file, encoding="utf-8") as file_obj:
            return file_obj.read()

    def test_writes_sysctl_file_with_modern_protections(self):
        harden_kernel(self.config)
        written = self.read_config()
        for directive in (
            "kernel.kptr_restrict=2",
            "kernel.unprivileged_bpf_disabled=1",
            "fs.protected_symlinks=1",
            "net.ipv4.tcp_syncookies=1",
        ):
            self.assertIn(directive, written)

    def test_matching_file_repairs_live_drift_without_rewriting(self):
        harden_kernel(self.config)
        self.live["kernel.kptr_restrict"] = "0"
        self.live["net.ipv4.conf.all.rp_filter"] = "0"
        with patch("security.security_steps.open", wraps=open) as file_open:
            harden_kernel(self.config)
        self.assertTrue(all(call.args[1] == "r" for call in file_open.call_args_list))
        self.assertEqual(self.live["kernel.kptr_restrict"], "2")
        self.assertEqual(self.live["net.ipv4.conf.all.rp_filter"], "1")

    def test_matching_irreversible_setting_is_not_written_again(self):
        harden_kernel(self.config)
        self.command.reset_mock()
        harden_kernel(self.config)
        self.assertTrue(self.command.called)
        self.assertTrue(all(call.args[0].startswith("sysctl -n ") for call in self.command.call_args_list))

    def test_partial_failure_propagates_and_matching_file_retry_repairs_proxmox(self):
        self.config.system_type = "server_proxmox"
        self.live["net.ipv4.conf.default.rp_filter"] = "1"
        self.live["net.ipv4.conf.all.rp_filter"] = "1"

        def fail_partway(command, **kwargs):
            if command == "sysctl -w net.ipv4.conf.all.rp_filter=0":
                raise RuntimeError("mock sysctl permission denied")
            return self.apply_setting(command, **kwargs)

        self.command.side_effect = fail_partway
        with patch("builtins.print") as output:
            with self.assertRaisesRegex(RuntimeError, "permission denied"):
                harden_kernel(self.config)
        self.assertFalse(any("✓ Kernel" in call.args[0] for call in output.call_args_list))
        self.assertEqual(self.live["net.ipv4.conf.default.rp_filter"], "0")
        self.assertEqual(self.live["net.ipv4.conf.all.rp_filter"], "1")
        expected = self.read_config()

        self.command.side_effect = self.apply_setting
        harden_kernel(self.config)
        self.assertEqual(self.read_config(), expected)
        self.assertEqual(self.live["net.ipv4.conf.all.rp_filter"], "0")
        self.assertEqual(self.live["kernel.core_uses_pid"], "1")

    def test_absent_optional_kernel_features_are_skipped(self):
        keys = ("net.ipv6.conf.all.accept_redirects", "kernel.yama.ptrace_scope", "net.core.bpf_jit_harden")
        self.absent = {"/proc/sys/" + key.replace(".", "/") for key in keys}
        harden_kernel(self.config)
        for key in keys:
            self.assertNotIn(key, self.live)
        self.assertEqual(self.live["kernel.core_uses_pid"], "1")

    def test_capability_gate_skips_all_files_and_commands(self):
        self.capability.return_value = False
        with patch("security.security_steps.open") as file_open:
            harden_kernel(self.config)
        file_open.assert_not_called()
        self.command.assert_not_called()

    def test_dry_run_skips_all_files_and_commands(self):
        with patch("security.security_steps.is_dry_run", return_value=True), patch("security.security_steps.open") as file_open:
            harden_kernel(self.config)
        file_open.assert_not_called()
        self.command.assert_not_called()

    def test_proxmox_preserves_asymmetric_guest_networking(self):
        self.config.system_type = "server_proxmox"
        harden_kernel(self.config)
        written = self.read_config()
        self.assertIn("net.ipv4.conf.default.rp_filter=0", written)
        self.assertIn("net.ipv4.conf.all.rp_filter=0", written)
        self.assertNotIn("net.ipv4.conf.all.rp_filter=1", written)
        self.live["net.ipv4.conf.all.rp_filter"] = "1"
        self.live["net.ipv4.conf.default.rp_filter"] = "1"
        harden_kernel(self.config)
        self.assertEqual(self.live["net.ipv4.conf.all.rp_filter"], "0")
        self.assertEqual(self.live["net.ipv4.conf.default.rp_filter"], "0")


class TestConfigureFail2Ban(unittest.TestCase):
    @patch("security.security_steps.is_container", return_value=False)
    @patch("security.security_steps.run")
    @patch("security.security_steps.os.makedirs")
    @patch("security.security_steps.open", new_callable=mock_open)
    def test_writes_sshd_jail_when_rdp_disabled(self, mock_file, _md, mock_run, _ic):
        mock_run.return_value = SimpleNamespace(returncode=0)
        configure_fail2ban(SetupConfig(username="u", host="h", system_type="server_lite"))
        mock_run.assert_any_call(
            "apt-get -o DPkg::Lock::Timeout=300 install -y -qq fail2ban"
        )
        opened_paths = [args[0] for args, _ in mock_file.call_args_list]
        self.assertIn("/etc/fail2ban/jail.d/sshd.local", opened_paths)
        self.assertNotIn("/etc/fail2ban/jail.d/xrdp.local", opened_paths)

    @patch("security.security_steps.is_container", return_value=False)
    @patch("security.security_steps.run")
    @patch("security.security_steps.os.makedirs")
    @patch("security.security_steps.open", new_callable=mock_open)
    def test_writes_xrdp_jail_and_filter_when_rdp_enabled(self, mock_file, _md, mock_run, _ic):
        mock_run.return_value = SimpleNamespace(returncode=0)
        cfg = SetupConfig(username="u", host="h", system_type="workstation_dev")
        cfg.enable_rdp = True
        configure_fail2ban(cfg)
        opened_paths = [args[0] for args, _ in mock_file.call_args_list]
        self.assertIn("/etc/fail2ban/jail.d/sshd.local", opened_paths)
        self.assertIn("/etc/fail2ban/jail.d/xrdp.local", opened_paths)
        self.assertIn("/etc/fail2ban/filter.d/xrdp.conf", opened_paths)
        # The filter should target AUTHFAIL to avoid false positives on
        # informational connection lines.
        written = "".join(call.args[0] for call in mock_file().write.call_args_list)
        self.assertIn("AUTHFAIL", written)
        commands = [call.args[0] for call in mock_run.call_args_list]
        self.assertLess(commands.index("fail2ban-client -t"), commands.index("systemctl restart fail2ban"))

    @patch("security.security_steps.is_container", return_value=False)
    @patch("security.security_steps.run")
    @patch("security.security_steps.os.makedirs")
    @patch("security.security_steps.open", new_callable=mock_open)
    def test_missing_log_validation_stops_before_service_activation(self, _file, _md, command, _ic):
        failure = CommandExecutionError("fail2ban-client -t", 255, "Have not found any log file for xrdp jail")

        def run_command(value, **_kwargs):
            if value == "fail2ban-client -t":
                raise failure
            return SimpleNamespace(returncode=0)

        command.side_effect = run_command
        config = SetupConfig(username="u", host="h", system_type="server_lite", enable_rdp=True)
        with patch("builtins.print") as output, self.assertRaises(CommandExecutionError) as raised:
            configure_fail2ban(config)
        self.assertIs(raised.exception, failure)
        self.assertFalse(any(call.args[0].startswith("systemctl ") for call in command.call_args_list))
        output.assert_not_called()

    @patch("security.security_steps.is_container", return_value=True)
    def test_skipped_in_container(self, _ic):
        with patch("builtins.print") as mock_print:
            configure_fail2ban(SetupConfig(username="u", host="h", system_type="server_lite"))
        mock_print.assert_any_call("  ✓ Skipping fail2ban configuration (limited functionality in containers)")


class TestConfigureAuditd(unittest.TestCase):
    @patch("security.security_steps.is_hardware", return_value=False)
    @patch("security.security_steps.is_vm", return_value=True)
    @patch("security.security_steps.os.makedirs")
    @patch("security.security_steps.run")
    def test_required_audit_coverage_fails_setup_when_activation_fails(
        self, mock_run, _makedirs, _vm, _hardware
    ):
        config = SetupConfig(username="u", host="h", system_type="server_lite")
        with tempfile.TemporaryDirectory() as temporary:
            with patch(
                "security.security_steps._AUDIT_RULES_FILE",
                os.path.join(temporary, "audit.rules"),
            ):
                for failed_command, message in (
                    ("systemctl start auditd", "service did not start"),
                    ("augenrules --load", "rules did not load"),
                ):
                    with self.subTest(command=failed_command):
                        mock_run.side_effect = lambda command, **_kwargs: SimpleNamespace(
                            returncode=1 if command == failed_command else 0
                        )
                        with self.assertRaisesRegex(RuntimeError, message):
                            configure_auditd(config)

    @patch("security.security_steps.is_hardware", return_value=False)
    @patch("security.security_steps.is_vm", return_value=True)
    @patch("security.security_steps.os.makedirs")
    @patch("security.security_steps.run")
    def test_rules_are_loaded_without_restarting_the_debian_service(
        self, mock_run, _makedirs, _vm, _hardware
    ):
        mock_run.return_value = SimpleNamespace(returncode=0)
        config = SetupConfig(username="u", host="h", system_type="server_lite")
        with tempfile.TemporaryDirectory() as temporary:
            rules_file = os.path.join(temporary, "audit.rules")
            with patch(
                "security.security_steps._AUDIT_RULES_FILE", rules_file
            ):
                configure_auditd(config)
                mock_run.assert_any_call(
                    "apt-get -o DPkg::Lock::Timeout=300 install -y -qq auditd audispd-plugins"
                )
                with open(rules_file, encoding="utf-8") as rules:
                    contents = rules.read()
                self.assertNotIn("-w ", contents)
                self.assertIn(
                    "-a always,exit -F arch=b64 -F path=/etc/passwd -F perm=wa -k identity",
                    contents,
                )
                self.assertIn(
                    "-a always,exit -F arch=b32 -F dir=/etc/sudoers.d/ -F perm=wa -k sudoers",
                    contents,
                )
                for line in contents.splitlines():
                    if "-F perm=" in line:
                        self.assertIn("-F arch=", line)
                changed_commands = [
                    call.args[0] for call in mock_run.call_args_list
                ]
                mock_run.reset_mock()
                configure_auditd(config)

        matching_commands = [call.args[0] for call in mock_run.call_args_list]
        for commands in (changed_commands, matching_commands):
            self.assertIn("systemctl enable auditd", commands)
            self.assertIn("systemctl start auditd", commands)
            self.assertIn("augenrules --load", commands)
            self.assertNotIn("systemctl restart auditd", commands)


class TestSecurityPackageInstallFailures(unittest.TestCase):
    def test_install_failure_stops_configuration_without_replay(self):
        config = SetupConfig(username="u", host="h", system_type="server_lite")
        for configure, packages in (
            (configure_fail2ban, "fail2ban"),
            (configure_apparmor, "apparmor apparmor-utils"),
            (configure_auditd, "auditd audispd-plugins"),
        ):
            command = f"apt-get -o DPkg::Lock::Timeout=300 install -y -qq {packages}"
            for detail in ("Could not acquire dpkg frontend lock", "Package installation failed"):
                with self.subTest(configure=configure.__name__, detail=detail):
                    failure = CommandExecutionError(command, 100, detail)
                    with (
                        patch.dict(os.environ),
                        patch("security.security_steps.is_dry_run", return_value=False),
                        patch("security.security_steps.is_container", return_value=False),
                        patch("security.security_steps.is_vm", return_value=True),
                        patch("security.security_steps.is_hardware", return_value=False),
                        patch("security.security_steps.run", side_effect=failure) as run,
                        patch("security.security_steps.os.makedirs") as makedirs,
                        patch("security.security_steps.open") as file_open,
                        patch("security.security_steps.write_text_atomic") as write,
                        patch("builtins.print") as output,
                    ):
                        with self.assertRaises(CommandExecutionError) as raised:
                            configure(config)
                        self.assertIs(raised.exception, failure)
                        run.assert_called_once_with(command)
                        makedirs.assert_not_called()
                        file_open.assert_not_called()
                        write.assert_not_called()
                        output.assert_not_called()


class TestConfigurePamLockout(unittest.TestCase):
    @patch("security.security_steps.is_hardware", return_value=False)
    @patch("security.security_steps.is_vm", return_value=True)
    @patch("security.security_steps.run")
    def test_reconciles_files_and_active_pam_stack(self, mock_run, _vm, _hardware):
        config = SetupConfig(username="u", host="h", system_type="workstation_dev")
        with tempfile.TemporaryDirectory() as temporary:
            profile = os.path.join(temporary, "pam-configs", "faillock-basaltwater")
            settings = os.path.join(temporary, "faillock.conf")
            auth = os.path.join(temporary, "common-auth")
            account = os.path.join(temporary, "common-account")

            def activate(_command, **_kwargs):
                with open(auth, "w", encoding="utf-8") as file_obj:
                    file_obj.write("auth [default=die] pam_faillock.so authfail\n")
                with open(account, "w", encoding="utf-8") as file_obj:
                    file_obj.write("account required pam_faillock.so\n")
                return SimpleNamespace(returncode=0)

            mock_run.side_effect = activate
            with patch.multiple(
                "security.security_steps",
                _PAM_FAILLOCK_PROFILE=profile,
                _FAILLOCK_CONF=settings,
                _PAM_COMMON_AUTH=auth,
                _PAM_COMMON_ACCOUNT=account,
            ):
                configure_pam_lockout(config)
                with open(profile, encoding="utf-8") as file_obj:
                    self.assertIn("pam_faillock.so", file_obj.read())
                with open(settings, encoding="utf-8") as file_obj:
                    self.assertIn("deny = 5", file_obj.read())
                mock_run.reset_mock()
                configure_pam_lockout(config)
                mock_run.assert_not_called()

                with open(profile, "w", encoding="utf-8") as file_obj:
                    file_obj.write("stale profile\n")
                configure_pam_lockout(config)
                with open(profile, encoding="utf-8") as file_obj:
                    self.assertIn("pam_faillock.so", file_obj.read())
                mock_run.assert_called_once()
                mock_run.reset_mock()

                with open(auth, "w", encoding="utf-8") as file_obj:
                    file_obj.write("# auth required pam_faillock.so\n")
                configure_pam_lockout(config)
                mock_run.assert_called_once_with(
                    "pam-auth-update --enable faillock-basaltwater", check=False
                )

    @patch("security.security_steps.is_hardware", return_value=False)
    @patch("security.security_steps.is_vm", return_value=True)
    @patch("security.security_steps.run")
    def test_fails_if_pam_activation_does_not_take_effect(
        self, mock_run, _vm, _hardware
    ):
        config = SetupConfig(username="u", host="h", system_type="workstation_dev")
        with tempfile.TemporaryDirectory() as temporary:
            with patch.multiple(
                "security.security_steps",
                _PAM_FAILLOCK_PROFILE=os.path.join(temporary, "pam-configs", "faillock"),
                _FAILLOCK_CONF=os.path.join(temporary, "faillock.conf"),
                _PAM_COMMON_AUTH=os.path.join(temporary, "common-auth"),
                _PAM_COMMON_ACCOUNT=os.path.join(temporary, "common-account"),
            ):
                mock_run.return_value = SimpleNamespace(returncode=1)
                with self.assertRaisesRegex(RuntimeError, "could not be enabled"):
                    configure_pam_lockout(config)

                mock_run.return_value = SimpleNamespace(returncode=0)
                with self.assertRaisesRegex(RuntimeError, "absent from the active"):
                    configure_pam_lockout(config)


class TestSSHFirewallPeerSafety(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("lib.machine_state.detect_machine_type", return_value="vm"))
        self.enterContext(patch.dict(os.environ, {"SSH_CONNECTION": ""}))
        self.command = self.enterContext(patch("security.security_steps.run"))
        self.command.return_value = SimpleNamespace(
            returncode=0,
            stdout="Status: active\nDefault: deny (incoming), allow (outgoing), disabled (routed)\n",
            stderr="",
        )
        self.config = SetupConfig(
            username="agent", host="192.168.0.44", system_type="agent_vm",
            lan_access=True,
        )

    def test_routed_controller_is_rejected_before_any_firewall_changes(self):
        for configure in (configure_firewall, configure_firewall_web, configure_firewall_ssh_only):
            with self.subTest(configure=configure.__name__), patch.dict(
                os.environ, {"SSH_CONNECTION": "192.168.68.56 50000 192.168.0.44 22"}
            ):
                self.command.reset_mock()
                with self.assertRaisesRegex(
                    RuntimeError, r"Add --access-source 192\.168\.68\.56/32"
                ):
                    configure(self.config)
                self.command.assert_not_called()

    def test_explicit_controller_source_allows_replacement_before_global_removal(self):
        self.config.access_sources = ["192.168.68.0/22"]
        with patch.dict(
            os.environ, {"SSH_CONNECTION": "192.168.68.56 50000 192.168.0.44 22"}
        ):
            configure_firewall(self.config)
        commands = [call.args[0] for call in self.command.call_args_list]
        allowed = commands.index(
            "ufw allow from 192.168.68.0/22 to any port 22 proto tcp "
            "comment 'basaltwater SSH trusted source 192.168.68.0/22'"
        )
        self.assertLess(allowed, commands.index("ufw delete allow ssh"))
        self.assertIn(
            "ufw allow from 192.168.0.0/24 to any port 22 proto tcp "
            "comment 'basaltwater SSH trusted source 192.168.0.0/24'",
            commands,
        )

    def test_allowed_peers_support_ipv4_ipv6_hosts_and_mixed_source_families(self):
        self.config.lan_access = False
        for peer, sources in (
            ("192.168.0.5", ["192.168.0.0/24"]),
            ("192.168.68.56", ["192.168.68.56"]),
            ("192.168.68.56", ["fd00::/64", "192.168.68.56/32"]),
            ("fd00::5", ["192.168.0.0/24", "fd00::/64"]),
            ("fd00::5", ["fd00::5/128"]),
        ):
            with self.subTest(peer=peer, sources=sources), patch.dict(
                os.environ, {"SSH_CONNECTION": f"{peer} 50000 192.168.0.44 22"}
            ):
                self.command.reset_mock()
                self.config.access_sources = sources
                configure_firewall(self.config)
                self.assertTrue(self.command.called)

    def test_excluded_ipv6_peer_gets_a_host_source_suggestion(self):
        with patch.dict(
            os.environ, {"SSH_CONNECTION": "fd00::5 50000 fd00:1::4 22"}
        ):
            with self.assertRaisesRegex(RuntimeError, r"--access-source fd00::5/128"):
                configure_firewall(self.config)
        self.command.assert_not_called()

    def test_malformed_ssh_connection_stops_before_changes(self):
        for connection in (" ", "192.168.0.5 50000", "invalid 50000 192.168.0.44 22"):
            with self.subTest(connection=connection), patch.dict(
                os.environ, {"SSH_CONNECTION": connection}
            ):
                with self.assertRaisesRegex(RuntimeError, "Cannot verify the current SSH peer"):
                    configure_firewall(self.config)
                self.command.assert_not_called()

    def test_local_setup_without_ssh_connection_can_apply_sources(self):
        configure_firewall(self.config)
        self.assertTrue(self.command.called)

    def test_unrestricted_policy_does_not_exclude_routed_peer(self):
        self.config.lan_access = False
        with patch.dict(
            os.environ, {"SSH_CONNECTION": "192.168.68.56 50000 192.168.0.44 22"}
        ):
            configure_firewall(self.config)
        self.assertIn("ufw limit ssh", [call.args[0] for call in self.command.call_args_list])


class TestConfigureFirewall(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("lib.machine_state.detect_machine_type", return_value="vm"))
        self.enterContext(patch.dict(os.environ, {"SSH_CONNECTION": ""}))

    def test_all_entrypoints_install_ufw_with_bounded_lock_wait_before_rules(self):
        config = SetupConfig(username="u", host="h", system_type="server_lite")
        for configure in (configure_firewall, configure_firewall_web, configure_firewall_ssh_only):
            with self.subTest(configure=configure.__name__):
                def run_command(command, **_kwargs):
                    return SimpleNamespace(
                        returncode=1 if command.startswith("ufw status 2>") else 0,
                        stdout="", stderr="",
                    )

                with patch("security.security_steps.run", side_effect=run_command) as run:
                    configure(config)
                self.assertEqual(
                    [call.args[0] for call in run.call_args_list[:3]],
                    [
                        "ufw status 2>/dev/null | grep -q 'Status: active'",
                        "apt-get -o DPkg::Lock::Timeout=300 install -y -qq ufw",
                        "ufw default deny incoming",
                    ],
                )
                self.assertEqual(run.call_args_list[1].kwargs, {})
                self.assertEqual(
                    sum(call.args[0].startswith("apt-get ") for call in run.call_args_list), 1
                )

    def test_install_failure_stops_all_entrypoints_before_rules_without_replay(self):
        config = SetupConfig(username="u", host="h", system_type="server_lite")
        command = "apt-get -o DPkg::Lock::Timeout=300 install -y -qq ufw"
        for configure in (configure_firewall, configure_firewall_web, configure_firewall_ssh_only):
            for detail in ("Could not acquire dpkg frontend lock", "Package installation failed"):
                with self.subTest(configure=configure.__name__, detail=detail):
                    failure = CommandExecutionError(command, 100, detail)
                    with (
                        patch("security.security_steps.run", side_effect=[
                            SimpleNamespace(returncode=1), failure,
                        ]) as run,
                        patch("builtins.print") as output,
                    ):
                        with self.assertRaises(CommandExecutionError) as raised:
                            configure(config)
                        self.assertIs(raised.exception, failure)
                        self.assertEqual(run.call_count, 2)
                        run.assert_called_with(command)
                        output.assert_not_called()

    @patch("security.security_steps.is_container", return_value=False)
    @patch("security.security_steps.run")
    def test_rate_limits_ssh_and_rdp_when_rdp_enabled(self, mock_run, _ic):
        def run_side_effect(command, **_kwargs):
            if command.startswith("ufw status 2>"):
                return SimpleNamespace(returncode=1, stdout="")
            return SimpleNamespace(returncode=0, stdout="")

        mock_run.side_effect = run_side_effect
        cfg = SetupConfig(username="u", host="h", system_type="workstation_dev")
        cfg.enable_rdp = True
        configure_firewall(cfg)
        run_commands = [args[0] for args, _ in mock_run.call_args_list]
        self.assertIn("ufw limit ssh", run_commands)
        self.assertIn(
            "ufw limit 3389/tcp comment 'basaltwater RDP global'",
            run_commands,
        )
        self.assertNotIn("ufw allow 3389/tcp", run_commands)

    @patch("security.security_steps.is_container", return_value=False)
    @patch("security.security_steps.run")
    def test_installs_restricted_rules_before_removing_global_access(self, mock_run, _ic):
        def run_side_effect(command, **_kwargs):
            if command.startswith("ufw status 2>"):
                return SimpleNamespace(returncode=0, stdout="")
            if command == "ufw status verbose":
                return SimpleNamespace(returncode=0, stdout="Status: active\nDefault: deny (incoming)\n")
            if command == "ufw status numbered":
                return SimpleNamespace(returncode=0, stdout="Status: active\n")
            return SimpleNamespace(returncode=0, stdout="")

        mock_run.side_effect = run_side_effect
        config = SetupConfig(
            username="u",
            host="h",
            system_type="workstation_dev",
            enable_rdp=True,
            rdp_allowed_sources=["10.0.0.1/24", "2001:db8::10"],
        )

        configure_firewall(config)

        commands = [args[0] for args, _ in mock_run.call_args_list]
        first_source_rule = commands.index(
            "ufw limit from 10.0.0.0/24 to any port 3389 proto tcp "
            "comment 'basaltwater RDP source 10.0.0.0/24'"
        )
        delete_global_rule = commands.index("ufw delete limit 3389/tcp")
        self.assertLess(first_source_rule, delete_global_rule)
        self.assertIn(
            "ufw limit from 2001:db8::10 to any port 3389 proto tcp "
            "comment 'basaltwater RDP source 2001:db8::10'",
            commands,
        )

    @patch("security.security_steps.is_container", return_value=False)
    @patch("security.security_steps.run")
    def test_removes_only_stale_tagged_rdp_rules(self, mock_run, _ic):
        status_output = """Status: active
[ 1] 22/tcp LIMIT IN Anywhere
[ 2] 3389/tcp LIMIT IN 10.0.0.0/24 # basaltwater RDP source 10.0.0.0/24
[ 3] 3389/tcp LIMIT IN 192.168.0.0/16 # operator rule
[ 4] 3389/tcp LIMIT IN 172.16.0.0/12 # basaltwater RDP source 172.16.0.0/12
"""

        def run_side_effect(command, **_kwargs):
            if command.startswith("ufw status 2>"):
                return SimpleNamespace(returncode=0, stdout="")
            if command == "ufw status verbose":
                return SimpleNamespace(returncode=0, stdout="Status: active\nDefault: deny (incoming)\n")
            if command == "ufw status numbered":
                return SimpleNamespace(returncode=0, stdout=status_output)
            return SimpleNamespace(returncode=0, stdout="")

        mock_run.side_effect = run_side_effect
        config = SetupConfig(
            username="u",
            host="h",
            system_type="workstation_dev",
            enable_rdp=True,
            rdp_allowed_sources=["10.0.0.0/24"],
        )

        configure_firewall(config)

        commands = [args[0] for args, _ in mock_run.call_args_list]
        self.assertIn("ufw --force delete 4", commands)
        self.assertNotIn("ufw --force delete 2", commands)
        self.assertNotIn("ufw --force delete 3", commands)

    @patch("security.security_steps.is_container", return_value=False)
    @patch("security.security_steps.run")
    def test_keeps_global_access_if_restricted_rule_install_fails(self, mock_run, _ic):
        def run_side_effect(command, **_kwargs):
            if command.startswith("ufw status 2>"):
                return SimpleNamespace(returncode=0, stdout="")
            if command == "ufw status verbose":
                return SimpleNamespace(returncode=0, stdout="Status: active\nDefault: deny (incoming)\n")
            if command.startswith("ufw limit from"):
                return SimpleNamespace(returncode=1, stdout="")
            return SimpleNamespace(returncode=0, stdout="")

        mock_run.side_effect = run_side_effect
        config = SetupConfig(
            username="u",
            host="h",
            system_type="workstation_dev",
            enable_rdp=True,
            rdp_allowed_sources=["10.0.0.0/24"],
        )

        with self.assertRaisesRegex(RuntimeError, "requested RDP firewall rule"):
            configure_firewall(config)

        commands = [args[0] for args, _ in mock_run.call_args_list]
        self.assertNotIn("ufw delete allow 3389/tcp", commands)
        self.assertNotIn("ufw delete limit 3389/tcp", commands)

    @patch("security.security_steps.is_container", return_value=False)
    @patch("security.security_steps.run")
    def test_restricted_firewall_rejects_remaining_broad_rules(self, mock_run, _ic):
        config = SetupConfig(
            username="u", host="h", system_type="workstation_dev",
            access_sources=["192.168.1.0/24"], enable_rdp=True,
            rdp_allowed_sources=["192.168.1.0/24"], web_ports=[8080],
            default_web_ports=False,
        )
        for destination, service in (
            ("22/tcp", "SSH"),
            ("3389/tcp (v6)", "RDP"),
            ("8080/tcp", "web TCP 8080"),
        ):
            with self.subTest(destination=destination):
                def run_side_effect(command, **_kwargs):
                    if command.startswith("ufw status 2>"):
                        return SimpleNamespace(returncode=0, stdout="")
                    if command == "ufw status numbered":
                        return SimpleNamespace(returncode=0, stdout="Status: active\n")
                    if command == "ufw status verbose":
                        return SimpleNamespace(
                            returncode=0,
                            stdout=f"Status: active\nDefault: deny (incoming)\n{destination} ALLOW IN Anywhere\n",
                        )
                    return SimpleNamespace(returncode=0, stdout="")

                mock_run.side_effect = run_side_effect
                with self.assertRaisesRegex(RuntimeError, f"Source-restricted {service}"):
                    configure_firewall(config)

    @patch("security.security_steps.is_container", return_value=False)
    @patch("security.security_steps.run")
    def test_restricted_firewall_requires_status_verification(self, mock_run, _ic):
        def run_side_effect(command, **_kwargs):
            if command.startswith("ufw status 2>"):
                return SimpleNamespace(returncode=0, stdout="")
            if command in {"ufw status numbered", "ufw status verbose"}:
                return SimpleNamespace(returncode=1, stdout="", stderr="query failed")
            return SimpleNamespace(returncode=0, stdout="")

        mock_run.side_effect = run_side_effect
        config = SetupConfig(
            username="u", host="h", system_type="server_lite",
            access_sources=["192.168.1.0/24"],
        )
        with self.assertRaisesRegex(RuntimeError, "Could not verify source-restricted"):
            configure_firewall(config)


class TestConfigureAutoUpdates(unittest.TestCase):
    @patch("security.security_steps.configure_maintenance_timer")
    @patch("security.security_steps.run")
    @patch("security.security_steps.os.path.exists", return_value=False)
    def test_configures_shared_systemd_timer(self, _exists, mock_run, mock_configure):
        mock_run.return_value = SimpleNamespace(returncode=0)
        configure_auto_updates(SetupConfig(username="u", host="h", system_type="server_lite"))

        mock_configure.assert_called_once_with(
            service_name="auto-update-apt",
            service_desc="Auto-update APT packages",
            timer_desc="Auto-update APT packages daily",
            script_path="/opt/basaltwater/common/service_tools/auto_update_apt.py",
            schedule="*-*-* 06:00:00",
            check_name="APT packages",
            purpose="auto-update",
        )

    @patch("security.security_steps.configure_maintenance_timer")
    @patch("security.security_steps.run")
    @patch("security.security_steps.os.path.exists", return_value=True)
    def test_cleans_up_legacy_unattended_upgrades(self, _exists, mock_run, _configure):
        mock_run.return_value = SimpleNamespace(returncode=0)
        removed_paths = []
        with patch("security.security_steps.os.remove", side_effect=lambda p: removed_paths.append(p)):
            configure_auto_updates(SetupConfig(username="u", host="h", system_type="server_lite"))
        self.assertIn("/etc/apt/apt.conf.d/52basaltwater-unattended-upgrades", removed_paths)
        self.assertIn("/etc/basaltwater/unattended_upgrades_origins.list", removed_paths)

    @patch("security.security_steps.configure_maintenance_timer")
    @patch("security.security_steps.run")
    @patch("security.security_steps.os.path.exists", return_value=False)
    def test_stops_and_disables_competing_apt_timers(self, _exists, mock_run, _configure):
        mock_run.return_value = SimpleNamespace(returncode=0)
        configure_auto_updates(SetupConfig(username="u", host="h", system_type="server_lite"))

        run_commands = [args[0] for args, _ in mock_run.call_args_list]
        for unit in (
            "unattended-upgrades.service",
            "apt-daily.timer",
            "apt-daily-upgrade.timer",
        ):
            self.assertIn(f"systemctl stop {unit}", run_commands)
            self.assertIn(f"systemctl disable {unit}", run_commands)

    @patch("security.security_steps.configure_maintenance_timer", return_value=False)
    @patch("security.security_steps.run")
    @patch("security.security_steps.os.path.exists", return_value=False)
    def test_retains_distro_timers_when_replacement_is_not_verified(
        self, _exists, mock_run, _configure
    ):
        with self.assertRaisesRegex(RuntimeError, "APT update timer failed verification"):
            configure_auto_updates(SetupConfig(username="u", host="h", system_type="server_lite"))

        mock_run.assert_not_called()


class TestConfigureMaintenanceTimers(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("security.security_steps.can_restart_system", return_value=True))
        patcher = patch("security.security_steps.install_kernel_restart_hook")
        patcher.start()
        self.addCleanup(patcher.stop)

    @patch("security.security_steps.configure_maintenance_timer", return_value=False)
    @patch("security.security_steps.is_hardware", return_value=False)
    @patch("security.security_steps.is_vm", return_value=True)
    def test_security_monitor_verification_failure_stops_setup(
        self, _vm, _hardware, _configure
    ):
        with self.assertRaisesRegex(RuntimeError, "monitor timer failed verification"):
            configure_security_monitor(
                SetupConfig(username="u", host="h", system_type="server_lite")
            )

    @patch("security.security_steps.configure_maintenance_timer")
    @patch("security.security_steps.is_hardware", return_value=False)
    @patch("security.security_steps.is_vm", return_value=True)
    def test_security_monitor_uses_shared_timer(self, _vm, _hardware, mock_configure):
        configure_security_monitor(SetupConfig(username="u", host="h", system_type="server_lite"))

        mock_configure.assert_called_once_with(
            service_name="security-monitor",
            service_desc="Security event monitor",
            timer_desc="Security event monitor (every 15 minutes)",
            script_path="/opt/basaltwater/security/service_tools/security_monitor.py",
            schedule="*:0/15",
            check_name="Security event monitor",
            randomized_delay="2min",
            timeout="10min",
            purpose="monitor",
            environment={"BASALTWATER_AUDIT_REQUIRED": "1"},
        )
        mock_configure.reset_mock()
        configure_security_monitor(
            SetupConfig(username="u", host="h", system_type="server_proxmox")
        )
        self.assertEqual(
            mock_configure.call_args.kwargs["environment"],
            {"BASALTWATER_AUDIT_REQUIRED": "0"},
        )

    @patch("security.security_steps.configure_maintenance_timer")
    @patch("security.security_steps.can_modify_kernel", return_value=True)
    def test_auto_restart_uses_shared_timer(self, _kernel, mock_configure):
        configure_auto_restart(SetupConfig(username="u", host="h", system_type="server_lite"))

        mock_configure.assert_called_once_with(
            service_name="auto-restart-if-needed",
            service_desc="Auto-restart system if needed",
            timer_desc="Auto-restart system if needed (daily at 2 AM)",
            script_path="/opt/basaltwater/common/service_tools/auto_restart_if_needed.py",
            schedule="*-*-* 02:00:00",
            on_boot_sec="30min",
            check_name="Automatic restart",
            randomized_delay="10min",
            timeout="10min",
            network_online=False,
            purpose="check",
        )

    @patch("security.security_steps.configure_maintenance_timer", return_value=False)
    @patch("security.security_steps.can_modify_kernel", return_value=True)
    def test_auto_restart_verification_failure_stops_setup(self, _kernel, _configure):
        with self.assertRaisesRegex(RuntimeError, "restart timer failed verification"):
            configure_auto_restart(
                SetupConfig(username="u", host="h", system_type="server_lite")
            )

    @patch("security.security_steps.configure_maintenance_timer")
    @patch("security.security_steps.run")
    @patch("security.security_steps.open", new_callable=mock_open)
    @patch("security.security_steps.os.makedirs")
    def test_cleanup_sets_journal_limit_and_uses_shared_timer(
        self, mock_makedirs, mock_file, mock_run, mock_configure
    ):
        mock_run.return_value = SimpleNamespace(returncode=0)
        configure_cleanup_maintenance(SetupConfig(username="u", host="h", system_type="server_lite"))

        mock_makedirs.assert_called_once_with("/etc/systemd/journald.conf.d", exist_ok=True)
        mock_file.assert_called_once_with("/etc/systemd/journald.conf.d/basaltwater.conf", "w")
        written_text = "".join(call.args[0] for call in mock_file().write.call_args_list)
        self.assertIn(f"SystemMaxUse={JOURNAL_MAX_USE}", written_text)
        self.assertIn(f"RuntimeMaxUse={JOURNAL_MAX_USE}", written_text)
        mock_run.assert_called_once_with("systemctl restart systemd-journald", check=False)
        self.assertEqual(mock_configure.call_count, 2)
        mock_configure.assert_any_call(
            service_name="cleanup-maintenance",
            service_desc="Cleanup temporary files and package caches",
            timer_desc="Cleanup temporary files and package caches (weekly)",
            script_path="/opt/basaltwater/common/service_tools/cleanup_maintenance.py",
            schedule="Sun *-*-* 03:30:00",
            check_name="Cleanup maintenance",
            randomized_delay="30min",
            timeout="1h",
            network_online=False,
            purpose="job",
        )
        mock_configure.assert_any_call(
            service_name="user-cache-maintenance",
            service_desc="Prune configured user developer-tool caches",
            timer_desc="Prune configured user developer-tool caches (daily)",
            script_path="/opt/basaltwater/common/service_tools/user_cache_launcher.py",
            script_args=("--user", "u"),
            isolated_python=True,
            schedule="*-*-* 07:00:00",
            check_name="User cache maintenance",
            randomized_delay="30min",
            timeout="1h",
            network_online=False,
            purpose="job",
        )

    @patch("security.security_steps.configure_maintenance_timer", return_value=False)
    @patch("security.security_steps.run")
    @patch("security.security_steps.open", new_callable=mock_open)
    @patch("security.security_steps.os.makedirs")
    def test_cleanup_verification_failure_stops_setup(
        self, _makedirs, _file, mock_run, _configure
    ):
        mock_run.return_value = SimpleNamespace(returncode=0)
        with self.assertRaisesRegex(RuntimeError, "Cleanup maintenance timer failed verification"):
            configure_cleanup_maintenance(
                SetupConfig(username="u", host="h", system_type="server_lite")
            )

    @patch(
        "security.security_steps.configure_maintenance_timer",
        side_effect=[True, False],
    )
    @patch("security.security_steps.run")
    @patch("security.security_steps.open", new_callable=mock_open)
    @patch("security.security_steps.os.makedirs")
    def test_user_cache_verification_failure_stops_setup(
        self, _makedirs, _file, mock_run, _configure
    ):
        mock_run.return_value = SimpleNamespace(returncode=0)
        with self.assertRaisesRegex(
            RuntimeError,
            "User cache maintenance timer failed verification",
        ):
            configure_cleanup_maintenance(
                SetupConfig(username="u", host="h", system_type="server_lite")
            )

    @patch("security.security_steps.configure_maintenance_timer")
    @patch("security.security_steps.run")
    @patch("security.security_steps.open", new_callable=mock_open)
    @patch("security.security_steps.os.makedirs")
    def test_root_setup_skips_user_cache_timer(
        self, _makedirs, _file, mock_run, mock_configure
    ):
        mock_run.return_value = SimpleNamespace(returncode=0)

        configure_cleanup_maintenance(
            SetupConfig(username="root", host="h", system_type="server_proxmox")
        )

        self.assertEqual(mock_configure.call_count, 1)
        self.assertEqual(
            mock_configure.call_args.kwargs["service_name"],
            "cleanup-maintenance",
        )


class TestCleanupMaintenanceStepWiring(unittest.TestCase):
    def test_server_lite_includes_cleanup_maintenance_step(self):
        config = SetupConfig(username="u", host="h", system_type="server_lite")
        step_names = [name for name, _ in get_steps_for_system_type(config)]
        self.assertIn("Configuring cleanup maintenance service", step_names)

    def test_server_proxmox_includes_cleanup_maintenance_step(self):
        config = SetupConfig(username="u", host="h", system_type="server_proxmox")
        step_names = [name for name, _ in get_steps_for_system_type(config)]
        self.assertIn("Configuring cleanup maintenance service", step_names)

    def test_server_proxmox_includes_security_monitor_step(self):
        config = SetupConfig(username="u", host="h", system_type="server_proxmox")
        step_names = [name for name, _ in get_steps_for_system_type(config)]
        self.assertIn("Configuring security event monitor", step_names)


if __name__ == "__main__":
    unittest.main()
