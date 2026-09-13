"""Tests for security.security_steps auto-update configuration."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import mock_open, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.config import SetupConfig
from lib.maintenance_defaults import JOURNAL_MAX_USE
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
            "/etc/ssh/sshd_config.d/99-infra-tools-hardening.conf",
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
        self.assertTrue(any(cmd.startswith("systemctl reload sshd") for cmd in run_commands))

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
        harden_ssh(SetupConfig(username="u", host="h", system_type="server_lite"))
        run_commands = [args[0] for args, _ in mock_run.call_args_list]
        self.assertIn("/usr/sbin/sshd -t", run_commands)
        self.assertFalse(any(cmd.startswith("systemctl reload sshd") for cmd in run_commands))
        mock_remove.assert_called_once_with(
            "/etc/ssh/sshd_config.d/99-infra-tools-hardening.conf"
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

        harden_ssh(SetupConfig(username="u", host="h", system_type="server_lite"))

        self.assertEqual(mock_write.call_args_list[-1].args[1], "previous\n")
        self.assertEqual(mock_write.call_args_list[-1].kwargs, {"mode": 0o600})
        run_commands = [args[0] for args, _ in mock_run.call_args_list]
        self.assertFalse(any(cmd.startswith("systemctl reload sshd") for cmd in run_commands))

    @patch("security.security_steps.shutil.which", return_value=None)
    @patch("security.security_steps.run")
    def test_skips_when_sshd_is_not_installed(self, mock_run, _which):
        harden_ssh(SetupConfig(username="u", host="h", system_type="server_lite"))
        mock_run.assert_not_called()

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
    @patch("security.security_steps.can_modify_kernel", return_value=True)
    @patch("security.security_steps.run")
    @patch("security.security_steps.open", new_callable=mock_open)
    @patch("security.security_steps.os.path.exists", return_value=False)
    def test_writes_sysctl_file_with_modern_protections(self, _exists, mock_file, mock_run, _ckm):
        mock_run.return_value = SimpleNamespace(returncode=0)
        harden_kernel(SetupConfig(username="u", host="h", system_type="server_lite"))
        written = "".join(call.args[0] for call in mock_file().write.call_args_list)
        for directive in (
            "kernel.kptr_restrict=2",
            "kernel.unprivileged_bpf_disabled=1",
            "fs.protected_symlinks=1",
            "net.ipv4.tcp_syncookies=1",
        ):
            self.assertIn(directive, written)

    @patch("security.security_steps.can_modify_kernel", return_value=True)
    @patch("security.security_steps.run")
    @patch("security.security_steps.os.path.exists", return_value=True)
    def test_skips_when_existing_content_matches(self, _exists, mock_run, _ckm):
        # Read the bytes the function would write, then mock open() to return them.
        from security.security_steps import _SYSCTL_HARDENING_FILE  # noqa: F401
        # Import inside function to avoid leaking; we re-run the function once
        # to produce expected content.
        mock_run.return_value = SimpleNamespace(returncode=0)
        captured = {}
        m = mock_open()
        # First call: prime with the expected content by running once with non-existent file
        with patch("security.security_steps.os.path.exists", return_value=False), \
             patch("security.security_steps.open", m):
            harden_kernel(SetupConfig(username="u", host="h", system_type="server_lite"))
        expected = "".join(call.args[0] for call in m().write.call_args_list)

        m2 = mock_open(read_data=expected)
        with patch("security.security_steps.open", m2):
            harden_kernel(SetupConfig(username="u", host="h", system_type="server_lite"))
        # No write should have happened because content matched.
        self.assertEqual(m2().write.call_count, 0)
        captured["ok"] = True
        self.assertTrue(captured["ok"])

    @patch("security.security_steps.can_modify_kernel", return_value=True)
    @patch("security.security_steps.run")
    @patch("security.security_steps.open", new_callable=mock_open)
    @patch("security.security_steps.os.path.exists", return_value=False)
    def test_proxmox_preserves_asymmetric_guest_networking(
        self, _exists, mock_file, mock_run, _ckm
    ):
        mock_run.return_value = SimpleNamespace(returncode=0)
        harden_kernel(SetupConfig(username="u", host="h", system_type="server_proxmox"))

        written = "".join(call.args[0] for call in mock_file().write.call_args_list)
        self.assertIn("net.ipv4.conf.default.rp_filter=0", written)
        self.assertIn("net.ipv4.conf.all.rp_filter=0", written)
        self.assertNotIn("net.ipv4.conf.all.rp_filter=1", written)


class TestConfigureFail2Ban(unittest.TestCase):
    @patch("security.security_steps.is_container", return_value=False)
    @patch("security.security_steps.run")
    @patch("security.security_steps.os.makedirs")
    @patch("security.security_steps.open", new_callable=mock_open)
    def test_writes_sshd_jail_when_rdp_disabled(self, mock_file, _md, mock_run, _ic):
        mock_run.return_value = SimpleNamespace(returncode=0)
        configure_fail2ban(SetupConfig(username="u", host="h", system_type="server_lite"))
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
                with open(rules_file, encoding="utf-8") as rules:
                    contents = rules.read()
                self.assertNotIn("-w ", contents)
                self.assertIn(
                    "-a always,exit -F path=/etc/passwd -F perm=wa -k identity",
                    contents,
                )
                self.assertIn(
                    "-a always,exit -F dir=/etc/sudoers.d/ -F perm=wa -k sudoers",
                    contents,
                )
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


class TestConfigureFirewall(unittest.TestCase):
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
            "ufw limit 3389/tcp comment 'infra_tools RDP global'",
            run_commands,
        )
        self.assertNotIn("ufw allow 3389/tcp", run_commands)

    @patch("security.security_steps.is_container", return_value=False)
    @patch("security.security_steps.run")
    def test_installs_restricted_rules_before_removing_global_access(self, mock_run, _ic):
        def run_side_effect(command, **_kwargs):
            if command.startswith("ufw status 2>"):
                return SimpleNamespace(returncode=0, stdout="")
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
            "comment 'infra_tools RDP source 10.0.0.0/24'"
        )
        delete_global_rule = commands.index("ufw delete limit 3389/tcp")
        self.assertLess(first_source_rule, delete_global_rule)
        self.assertIn(
            "ufw limit from 2001:db8::10 to any port 3389 proto tcp "
            "comment 'infra_tools RDP source 2001:db8::10'",
            commands,
        )

    @patch("security.security_steps.is_container", return_value=False)
    @patch("security.security_steps.run")
    def test_removes_only_stale_tagged_rdp_rules(self, mock_run, _ic):
        status_output = """Status: active
[ 1] 22/tcp LIMIT IN Anywhere
[ 2] 3389/tcp LIMIT IN 10.0.0.0/24 # infra_tools RDP source 10.0.0.0/24
[ 3] 3389/tcp LIMIT IN 192.168.0.0/16 # operator rule
[ 4] 3389/tcp LIMIT IN 172.16.0.0/12 # infra_tools RDP source 172.16.0.0/12
"""

        def run_side_effect(command, **_kwargs):
            if command.startswith("ufw status 2>"):
                return SimpleNamespace(returncode=0, stdout="")
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
            script_path="/opt/infra_tools/common/service_tools/auto_update_apt.py",
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
        self.assertIn("/etc/apt/apt.conf.d/52infra-tools-unattended-upgrades", removed_paths)
        self.assertIn("/etc/infra_tools/unattended_upgrades_origins.list", removed_paths)

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
            script_path="/opt/infra_tools/security/service_tools/security_monitor.py",
            schedule="*:0/15",
            check_name="Security event monitor",
            randomized_delay="2min",
            timeout="10min",
            purpose="monitor",
        )

    @patch("security.security_steps.configure_maintenance_timer")
    @patch("security.security_steps.can_modify_kernel", return_value=True)
    def test_auto_restart_uses_shared_timer(self, _kernel, mock_configure):
        configure_auto_restart(SetupConfig(username="u", host="h", system_type="server_lite"))

        mock_configure.assert_called_once_with(
            service_name="auto-restart-if-needed",
            service_desc="Auto-restart system if needed",
            timer_desc="Auto-restart system if needed (daily at 2 AM)",
            script_path="/opt/infra_tools/common/service_tools/auto_restart_if_needed.py",
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
        mock_file.assert_called_once_with("/etc/systemd/journald.conf.d/infra-tools.conf", "w")
        written_text = "".join(call.args[0] for call in mock_file().write.call_args_list)
        self.assertIn(f"SystemMaxUse={JOURNAL_MAX_USE}", written_text)
        self.assertIn(f"RuntimeMaxUse={JOURNAL_MAX_USE}", written_text)
        mock_run.assert_called_once_with("systemctl restart systemd-journald", check=False)
        self.assertEqual(mock_configure.call_count, 2)
        mock_configure.assert_any_call(
            service_name="cleanup-maintenance",
            service_desc="Cleanup temporary files and package caches",
            timer_desc="Cleanup temporary files and package caches (weekly)",
            script_path="/opt/infra_tools/common/service_tools/cleanup_maintenance.py",
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
            script_path="/opt/infra_tools/common/service_tools/user_cache_maintenance.py",
            schedule="*-*-* 07:00:00",
            check_name="User cache maintenance",
            user="u",
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
