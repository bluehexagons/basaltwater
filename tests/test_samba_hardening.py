"""Regression tests for Samba server hardening defaults.

These tests assert the *content* of the hardening constants so that future
edits cannot silently weaken the defaults (e.g. lowering `server min
protocol` back to SMB2, or re-enabling NetBIOS).
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.config import SetupConfig
from smb import samba_steps


def _make_config(**kwargs) -> SetupConfig:
    defaults = dict(
        host='testhost',
        username='testuser',
        system_type='server_lite',
        machine_type='vm',
    )
    defaults.update(kwargs)
    return SetupConfig(**defaults)


class TestSambaGlobalHardenedSettings(unittest.TestCase):
    def test_pins_smb3_minimum(self) -> None:
        s = samba_steps.SAMBA_GLOBAL_HARDENED_SETTINGS
        self.assertEqual(s["server min protocol"], "SMB3")
        self.assertEqual(s["client min protocol"], "SMB3")

    def test_requires_signing_and_encryption(self) -> None:
        s = samba_steps.SAMBA_GLOBAL_HARDENED_SETTINGS
        self.assertEqual(s["server signing"], "mandatory")
        self.assertEqual(s["client signing"], "mandatory")
        self.assertEqual(s["smb encrypt"], "required")

    def test_disables_netbios_and_anonymous(self) -> None:
        s = samba_steps.SAMBA_GLOBAL_HARDENED_SETTINGS
        self.assertEqual(s["disable netbios"], "yes")
        self.assertEqual(s["map to guest"], "Never")
        self.assertEqual(s["restrict anonymous"], "2")

    def test_log_template_matches_fail2ban_glob(self) -> None:
        # log.%m is what the fail2ban `log.*` glob expects to find;
        # the legacy `%m.log` template would not be picked up.
        self.assertEqual(
            samba_steps.SAMBA_GLOBAL_HARDENED_SETTINGS["log file"],
            "/var/log/samba/log.%m",
        )


class TestConfigureSambaGlobalSettings(unittest.TestCase):
    def test_only_rewrites_global_settings_and_reloads_smbd(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            smb_conf = os.path.join(tmpdir, "smb.conf")
            original = """[GLOBAL]
   server min protocol = SMB2
   null passwords = no
   workgroup = LEGACY

[archive]
   server min protocol = NT1
"""
            with open(smb_conf, "w") as file_obj:
                file_obj.write(original)

            commands: list[str] = []

            def fake_run(command: str, **_kwargs: object) -> MagicMock:
                commands.append(command)
                return MagicMock(returncode=0, stderr="")

            with patch.object(samba_steps, "SMB_CONF_PATH", smb_conf), \
                 patch.object(samba_steps, "run", side_effect=fake_run):
                samba_steps.configure_samba_global_settings(_make_config())

            with open(smb_conf) as file_obj:
                configured = file_obj.read()

        global_section, archive_section = configured.split("[archive]", 1)
        self.assertIn("server min protocol = SMB3", global_section)
        self.assertEqual(global_section.count("server min protocol"), 1)
        self.assertNotIn("null passwords", global_section)
        self.assertIn("server min protocol = NT1", archive_section)
        self.assertTrue(any(command.startswith("testparm -s ") for command in commands))
        self.assertIn("systemctl reload smbd", commands)

    def test_restores_previous_config_when_testparm_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            smb_conf = os.path.join(tmpdir, "smb.conf")
            original = "[global]\n   workgroup = LEGACY\n"
            with open(smb_conf, "w") as file_obj:
                file_obj.write(original)

            commands: list[str] = []

            def fake_run(command: str, **_kwargs: object) -> MagicMock:
                commands.append(command)
                return MagicMock(
                    returncode=1 if command.startswith("testparm -s ") else 0,
                    stderr="bad option",
                )

            with patch.object(samba_steps, "SMB_CONF_PATH", smb_conf), \
                 patch.object(samba_steps, "run", side_effect=fake_run):
                with self.assertRaisesRegex(RuntimeError, "security configuration validation failed"):
                    samba_steps.configure_samba_global_settings(_make_config())

            with open(smb_conf) as file_obj:
                self.assertEqual(file_obj.read(), original)

        self.assertNotIn("systemctl reload smbd", commands)

    def test_configures_custom_metadata_cache_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            smb_conf = os.path.join(tmpdir, "smb.conf")
            with open(smb_conf, "w", encoding="utf-8") as file_obj:
                file_obj.write("[global]\n")

            commands: list[str] = []

            def fake_run(command: str, **_kwargs: object) -> MagicMock:
                commands.append(command)
                return MagicMock(returncode=0, stderr="")

            with patch.object(samba_steps, "SMB_CONF_PATH", smb_conf), \
                 patch.object(samba_steps, "run", side_effect=fake_run):
                samba_steps.configure_samba_global_settings(
                    _make_config(
                        enable_samba=True,
                        samba_metadata_cache="/srv/ssd/samba-cache",
                    )
                )

            with open(smb_conf, encoding="utf-8") as file_obj:
                configured = file_obj.read()

        self.assertIn("cache directory = /srv/ssd/samba-cache", configured)
        self.assertIn(
            "install -d -m 0750 -o root -g root -- /srv/ssd/samba-cache",
            commands,
        )


class TestSambaFail2banFilter(unittest.TestCase):
    def test_filter_matches_modern_smbd_auth_failure(self) -> None:
        # Pull the failregex out of the multiline filter body and apply
        # fail2ban's <HOST> -> capture-group expansion to verify it
        # actually matches a current smbd "Auth:" log line.
        body = samba_steps.SAMBA_FAIL2BAN_FILTER
        match = re.search(r"failregex\s*=\s*(.+)", body)
        self.assertIsNotNone(match, "filter must declare a failregex")
        regex = match.group(1).strip()
        # fail2ban substitutes <HOST> with a host/IP capture; for testing,
        # an IPv4 capture group is sufficient.
        regex = regex.replace("<HOST>", r"(?P<host>[^\]:]+)")

        sample = (
            '[2024/01/01 12:34:56.789012,  2] '
            '../../source3/auth/auth.c:319(auth_check_ntlm_password)  '
            'Auth: [SMB2,(null)] user [WORKGROUP]\\[bob] at '
            '[Wed Jan  1 12:34:56 2024] with [NTLMv2] '
            'status [NT_STATUS_WRONG_PASSWORD] '
            'workstation [USER-PC] '
            'remote host [ipv4:192.168.1.10:54321] became [WORKGROUP]\\[]'
        )
        m = re.search(regex, sample)
        self.assertIsNotNone(m, f"filter regex did not match modern smbd line: {regex!r}")
        self.assertEqual(m.group("host"), "192.168.1.10")

    def test_filter_does_not_match_successful_auth(self) -> None:
        body = samba_steps.SAMBA_FAIL2BAN_FILTER
        match = re.search(r"failregex\s*=\s*(.+)", body)
        regex = match.group(1).strip().replace("<HOST>", r"(?P<host>[^\]:]+)")
        success = (
            '  Auth: [SMB2,(null)] user [WORKGROUP]\\[bob] at [...] '
            'with [NTLMv2] status [NT_STATUS_OK] workstation [USER-PC] '
            'remote host [ipv4:192.168.1.10:54321]'
        )
        self.assertIsNone(re.search(regex, success))


class TestSambaFail2banJail(unittest.TestCase):
    def test_jail_uses_445_only_and_renamed_filter(self) -> None:
        body = samba_steps.SAMBA_FAIL2BAN_JAIL
        self.assertIn("[samba-auth]", body)
        self.assertIn("filter = samba-auth", body)
        self.assertIn("port = 445", body)
        # Port 139 (legacy NetBIOS) must not be enabled in the jail.
        self.assertNotIn("139", body)
        # Wildcard logpath catches per-machine logs (log.%m -> log.HOSTNAME).
        self.assertIn("/var/log/samba/log.*", body)


class TestConfigureSambaFirewall(unittest.TestCase):
    def test_only_opens_445_and_removes_legacy_139(self) -> None:
        commands: list[str] = []

        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            res = MagicMock()
            res.returncode = 0
            return res

        with patch.object(samba_steps, 'run', side_effect=fake_run):
            samba_steps.configure_samba_firewall(_make_config())

        joined = "\n".join(commands)
        self.assertIn("ufw delete allow 139/tcp", joined)
        self.assertIn("ufw allow 445/tcp", joined)
        # No allow rule for 139 should be issued.
        self.assertFalse(any("ufw allow 139" in c for c in commands))
        self.assertIn("ufw reload", joined)

    def test_captures_expected_ufw_reconciliation_output(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        def fake_run(command: str, **kwargs: object) -> MagicMock:
            calls.append((command, kwargs))
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch.object(samba_steps, "run", side_effect=fake_run):
            samba_steps.configure_samba_firewall(_make_config())

        for command, kwargs in calls:
            if command.startswith("ufw "):
                self.assertTrue(kwargs.get("capture_output"), command)

    def test_container_defers_firewall_to_host(self) -> None:
        with patch.object(samba_steps, "run") as mock_run:
            samba_steps.configure_samba_firewall(
                _make_config(machine_type="unprivileged")
            )

        mock_run.assert_not_called()

    def test_required_samba_rule_failure_stops_setup(self) -> None:
        def fake_run(command: str, **_kwargs: object) -> MagicMock:
            return MagicMock(
                returncode=1 if command.startswith("ufw allow 445/tcp") else 0,
                stdout="",
                stderr="permission denied",
            )

        with patch.object(samba_steps, "run", side_effect=fake_run):
            with self.assertRaisesRegex(RuntimeError, "Samba firewall rule"):
                samba_steps.configure_samba_firewall(_make_config())


class TestConfigureSambaFail2banLegacyCleanup(unittest.TestCase):
    """The old code wrote /etc/fail2ban/filter.d/samba.conf, clobbering the
    distro-shipped conffile. The new code uses samba-auth.* and removes the
    legacy paths if present."""

    def test_container_removes_managed_jail_without_installing_fail2ban(self) -> None:
        expected_paths = {
            samba_steps.SAMBA_FAIL2BAN_JAIL_PATH,
            samba_steps.SAMBA_FAIL2BAN_FILTER_PATH,
        }
        existing_paths = set(expected_paths)
        removed: list[str] = []

        def remove(path: str) -> None:
            removed.append(path)
            existing_paths.remove(path)

        with patch.object(
            samba_steps.os.path,
            "exists",
            side_effect=existing_paths.__contains__,
        ), patch.object(
            samba_steps.os,
            "remove",
            side_effect=remove,
        ), patch.object(
            samba_steps,
            "is_package_installed",
            return_value=False,
        ), patch.object(samba_steps, "run") as mock_run:
            samba_steps.configure_samba_fail2ban(
                _make_config(machine_type="unprivileged")
            )

        self.assertCountEqual(removed, expected_paths)
        mock_run.assert_not_called()

    def test_legacy_files_are_removed_when_present(self) -> None:
        existing_paths = {
            "/etc/fail2ban/jail.d/samba.local",
            "/etc/fail2ban/filter.d/samba.conf",
            # New jail does not exist yet, so the function will (re)write it.
        }
        removed: list[str] = []
        opened: list[str] = []
        written: dict[str, str] = {}

        class _Writer:
            def __init__(self, path: str) -> None:
                self.path = path

            def __enter__(self):  # type: ignore[no-untyped-def]
                return self

            def __exit__(self, *a):  # type: ignore[no-untyped-def]
                return False

            def write(self, data: str) -> None:
                written[self.path] = written.get(self.path, "") + data

        def fake_open(path, mode='r', *a, **kw):
            opened.append(path)
            self.assertEqual(mode, 'w')
            return _Writer(path)

        def fake_remove(path):
            removed.append(path)
            existing_paths.discard(path)

        def fake_exists(path):
            return path in existing_paths

        with patch.object(samba_steps, 'run') as mock_run, \
             patch.object(samba_steps, 'is_package_installed', return_value=True), \
             patch.object(samba_steps.os.path, 'exists', side_effect=fake_exists), \
             patch.object(samba_steps.os, 'makedirs'), \
             patch.object(samba_steps.os, 'remove', side_effect=fake_remove), \
             patch.object(samba_steps, 'open', side_effect=fake_open, create=True), \
             patch('lib.remote_utils.is_service_active', return_value=False):
            mock_run.return_value = MagicMock(returncode=0)
            samba_steps.configure_samba_fail2ban(_make_config())

        self.assertIn("/etc/fail2ban/jail.d/samba.local", removed)
        self.assertIn("/etc/fail2ban/filter.d/samba.conf", removed)
        # New paths were written, not the legacy ones.
        self.assertIn("/etc/fail2ban/filter.d/samba-auth.conf", written)
        self.assertIn("/etc/fail2ban/jail.d/samba-auth.local", written)
        self.assertNotIn("/etc/fail2ban/filter.d/samba.conf", written)
        self.assertNotIn("/etc/fail2ban/jail.d/samba.local", written)

    def test_existing_jail_is_refreshed(self) -> None:
        existing_paths = {"/etc/fail2ban/jail.d/samba-auth.local"}
        written: dict[str, str] = {}

        class _Writer:
            def __init__(self, path: str) -> None:
                self.path = path

            def __enter__(self):  # type: ignore[no-untyped-def]
                return self

            def __exit__(self, *args: object) -> bool:
                return False

            def write(self, data: str) -> None:
                written[self.path] = written.get(self.path, "") + data

        def fake_open(path: str, mode: str = "r", *args: object, **kwargs: object) -> _Writer:
            self.assertEqual(mode, "w")
            return _Writer(path)

        with patch.object(samba_steps, "run", return_value=MagicMock(returncode=0)), \
             patch.object(samba_steps, "is_package_installed", return_value=True), \
             patch.object(samba_steps.os.path, "exists", side_effect=existing_paths.__contains__), \
             patch.object(samba_steps.os, "makedirs"), \
             patch.object(samba_steps, "open", side_effect=fake_open, create=True):
            samba_steps.configure_samba_fail2ban(_make_config())

        self.assertIn("/etc/fail2ban/filter.d/samba-auth.conf", written)
        self.assertIn("/etc/fail2ban/jail.d/samba-auth.local", written)


class TestVetoFilesPattern(unittest.TestCase):
    """The veto-files config must use real on-disk directory names; an
    earlier version always prepended a `.` which silently broke vetoing for
    non-dotfile directories like `subdir`."""

    def test_pattern_uses_real_dir_name(self) -> None:
        config = _make_config(scrub_specs=[
            ['/mnt/data/store/subdir', '.pardatabase', '5%', 'monthly'],
        ])
        veto_dirs = samba_steps._get_veto_dirs_for_share('/mnt/data/store', config)
        self.assertEqual(veto_dirs, ['subdir'])

        # Reproduce setup_samba_share's pattern construction so this test
        # fails if the regression returns.
        veto_pattern = "/" + "/".join(veto_dirs) + "/"
        self.assertEqual(veto_pattern, "/subdir/")
        self.assertNotEqual(veto_pattern, "/.subdir/")


class TestReconcileSambaShares(unittest.TestCase):
    def _run_reconcile(
        self,
        original: str,
        shares: list[list[str]] | None,
    ) -> tuple[str, list[tuple[str, dict[str, object]]]]:
        calls: list[tuple[str, dict[str, object]]] = []

        def fake_run(command: str, **kwargs: object) -> MagicMock:
            calls.append((command, kwargs))
            missing = command.startswith(("id ", "pdbedit -L ", "getent group "))
            return MagicMock(returncode=1 if missing else 0, stderr="")

        with tempfile.TemporaryDirectory() as tmpdir:
            smb_conf = os.path.join(tmpdir, "smb.conf")
            with open(smb_conf, "w", encoding="utf-8") as file_obj:
                file_obj.write(original)
            config = _make_config(samba_shares=shares)
            with patch.object(samba_steps, "SMB_CONF_PATH", smb_conf), \
                 patch.object(samba_steps, "run", side_effect=fake_run), \
                 patch.object(samba_steps, "assert_declared_storage_mount") as assert_mount, \
                 patch.object(samba_steps.os, "makedirs"):
                samba_steps.reconcile_samba_shares(config)
            for share in shares or []:
                assert_mount.assert_any_call(config, share[2])
            with open(smb_conf, encoding="utf-8") as file_obj:
                return file_obj.read(), calls

    def test_access_change_replaces_legacy_section_and_group(self) -> None:
        original = """[global]
   workgroup = WORKGROUP

[docs_read]
   path = /srv/docs
   valid users = @smb_docs_read
   force group = smb_docs_read

[manual]
   path = /srv/manual
"""
        configured, calls = self._run_reconcile(
            original,
            [["write", "docs", "/srv/docs", "alice:secret"]],
        )

        self.assertNotIn("[docs_read]", configured)
        self.assertIn("[docs_write]", configured)
        self.assertIn("[manual]", configured)
        self.assertIn(samba_steps.MANAGED_SHARES_BEGIN, configured)
        commands = [command for command, _kwargs in calls]
        self.assertIn("groupdel smb_docs_read", commands)
        self.assertEqual(commands.count("systemctl reload smbd"), 1)

    def test_membership_is_replaced_not_only_appended(self) -> None:
        original = """[global]

[docs_write]
   path = /srv/docs
   valid users = @smb_docs_write
   force group = smb_docs_write
"""
        _configured, calls = self._run_reconcile(
            original,
            [["write", "docs", "/srv/docs", "bob:secret"]],
        )

        commands = [command for command, _kwargs in calls]
        self.assertIn("gpasswd -M bob smb_docs_write", commands)

    def test_empty_desired_state_removes_only_managed_shares(self) -> None:
        original = """[global]

[docs_write]
   path = /srv/docs
   valid users = @smb_docs_write
   force group = smb_docs_write

[manual]
   path = /srv/manual
"""
        configured, calls = self._run_reconcile(original, None)

        self.assertNotIn("[docs_write]", configured)
        self.assertIn("[manual]", configured)
        self.assertIn("groupdel smb_docs_write", [command for command, _ in calls])

    def test_password_is_passed_over_stdin_not_in_command(self) -> None:
        _configured, calls = self._run_reconcile(
            "[global]\n",
            [["read", "docs", "/srv/docs", "alice:very-secret"]],
        )

        self.assertFalse(any("very-secret" in command for command, _ in calls))
        password_calls = [
            kwargs
            for command, kwargs in calls
            if command.startswith("smbpasswd -a -s ")
        ]
        self.assertEqual(password_calls[0]["input_data"], "very-secret\nvery-secret\n")

    def test_recursive_permission_changes_preserve_executable_bits(self) -> None:
        _configured, calls = self._run_reconcile(
            "[global]\n",
            [["write", "docs", "/srv/docs", "alice:very-secret"]],
        )

        commands = [command for command, _ in calls]
        self.assertIn("chgrp -R --preserve-root smb_docs_write /srv/docs", commands)
        self.assertTrue(any('RequiresMountsFor="/srv/docs"' in command for command in commands))
        self.assertIn("systemctl daemon-reload", commands)
        self.assertIn("chmod -R --preserve-root g+rwX /srv/docs", commands)
        self.assertIn(
            "find /srv/docs -xdev -type d -exec chmod g+s -- {} +",
            commands,
        )
        self.assertNotIn("chmod -R --preserve-root 2775 /srv/docs", commands)

    def test_read_share_removes_group_write_without_making_files_executable(self) -> None:
        _configured, calls = self._run_reconcile(
            "[global]\n",
            [["read", "docs", "/srv/docs", "alice:very-secret"]],
        )

        commands = [command for command, _ in calls]
        self.assertIn("chmod -R --preserve-root g+rX,g-w /srv/docs", commands)
        self.assertNotIn("chmod -R --preserve-root 2755 /srv/docs", commands)


if __name__ == '__main__':
    unittest.main()
