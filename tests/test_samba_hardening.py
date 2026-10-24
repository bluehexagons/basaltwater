"""Regression tests for Samba server hardening defaults.

These tests assert the *content* of the hardening constants so that future
edits cannot silently weaken the defaults (e.g. lowering `server min
protocol` back to SMB2, or re-enabling NetBIOS).
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.config import SetupConfig
from lib.validation import validate_samba_share_specs
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
        self.assertEqual(s["smb ports"], "445")
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
    def test_rewrites_global_settings_and_restarts_smbd_after_disabling_nmbd(self) -> None:
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
                 patch.object(samba_steps.os, "fchown"), \
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
        self.assertIn("systemctl disable --now nmbd", commands)
        self.assertIn("systemctl restart smbd", commands)
        self.assertLess(
            commands.index("systemctl disable --now nmbd"),
            commands.index("systemctl restart smbd"),
        )

    def test_unchanged_global_config_stops_and_disables_nmbd_without_restarting_smbd(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            smb_conf = os.path.join(tmpdir, "smb.conf")
            already_configured = samba_steps._render_hardened_global_settings("[global]\n")
            with open(smb_conf, "w", encoding="utf-8") as file_obj:
                file_obj.write(already_configured)
            with patch.object(samba_steps, "SMB_CONF_PATH", smb_conf), \
                 patch.object(samba_steps.os, "fchown"), \
                 patch.object(samba_steps, "run", return_value=MagicMock(returncode=0)) as run:
                samba_steps.configure_samba_global_settings(_make_config())

        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn("systemctl disable --now nmbd", commands)
        self.assertNotIn("systemctl restart smbd", commands)
        self.assertNotIn("systemctl reload smbd", commands)

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
                 patch.object(samba_steps.os, "fchown"), \
                 patch.object(samba_steps, "run", side_effect=fake_run):
                with self.assertRaisesRegex(RuntimeError, "Samba configuration validation failed"):
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
                 patch.object(samba_steps.os, "fchown"), \
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
    def setUp(self) -> None:
        self.rules: list[str] = []
        self.commands: list[str] = []
        self.verbose = "Status: active\nDefault: deny (incoming), allow (outgoing)\n"

    def run_ufw(self, command: str, **_kwargs: object) -> MagicMock:
        import shlex

        self.commands.append(command)
        stdout = ""
        if command == "ufw status numbered":
            stdout = "Status: active\n" + "\n".join(
                f"[{number:2}] {rule}" for number, rule in enumerate(self.rules, 1)
            )
        elif command == "ufw status verbose":
            stdout = self.verbose + "\n".join(self.rules)
        elif command.startswith("ufw --force delete "):
            del self.rules[int(command.rsplit(" ", 1)[1]) - 1]
        elif command.startswith("ufw allow "):
            args = shlex.split(command)
            source = args[3] if args[2] == "from" else "Anywhere"
            if ":" in source:
                self.rules.append(f"445/tcp (v6) ALLOW IN {source} # {args[-1]}")
            else:
                index = next((index for index, rule in enumerate(self.rules) if "(v6)" in rule), len(self.rules))
                self.rules.insert(index, f"445/tcp ALLOW IN {source} # {args[-1]}")
        return MagicMock(returncode=0, stdout=stdout, stderr="")

    def test_only_opens_445_and_removes_legacy_139(self) -> None:
        self.rules = ["139/tcp ALLOW IN Anywhere", "139 ALLOW IN Anywhere (v6)",
                      "139/udp ALLOW IN 10.0.0.0/8 # operator"]
        with patch.object(samba_steps, 'run', side_effect=self.run_ufw):
            samba_steps.configure_samba_firewall(_make_config())

        joined = "\n".join(self.commands)
        self.assertIn("ufw --force delete 3", joined)
        self.assertIn("ufw --force delete 1", joined)
        self.assertIn("ufw allow 445/tcp", joined)
        self.assertIn("139/udp ALLOW IN 10.0.0.0/8 # operator", self.rules)
        # No allow rule for 139 should be issued.
        self.assertFalse(any("ufw allow 139" in c for c in self.commands))
        self.assertIn("ufw reload", joined)

    def test_captures_expected_ufw_reconciliation_output(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        def fake_run(command: str, **kwargs: object) -> MagicMock:
            calls.append((command, kwargs))
            return self.run_ufw(command, **kwargs)

        with patch.object(samba_steps, "run", side_effect=fake_run):
            samba_steps.configure_samba_firewall(_make_config())

        for command, kwargs in calls:
            if command.startswith("ufw "):
                self.assertTrue(kwargs.get("capture_output"), command)

    def test_container_defers_firewall_to_host(self) -> None:
        for machine_type in ("unprivileged", "oci", "wsl"):
            with self.subTest(machine_type=machine_type), patch.object(samba_steps, "run") as mock_run:
                samba_steps.configure_samba_firewall(_make_config(machine_type=machine_type))
            mock_run.assert_not_called()

    def test_required_samba_rule_failure_stops_setup(self) -> None:
        def fake_run(command: str, **_kwargs: object) -> MagicMock:
            if command.startswith("ufw allow 445/tcp"):
                return MagicMock(returncode=1, stderr="permission denied")
            return self.run_ufw(command)

        with patch.object(samba_steps, "run", side_effect=fake_run):
            with self.assertRaisesRegex(RuntimeError, "Samba firewall rule"):
                samba_steps.configure_samba_firewall(_make_config())

    def test_revocation_removes_broad_and_stale_rules_in_descending_order(self) -> None:
        self.rules = [
            "445/tcp ALLOW IN Anywhere # Samba SMB",
            "22/tcp ALLOW IN 10.0.0.0/8 # operator",
            "445/tcp ALLOW IN 10.0.0.0/8 # basaltwater Samba 445/tcp source 10.0.0.0/8",
            "445/tcp (v6) ALLOW IN Anywhere (v6) # Samba SMB",
        ]
        with patch.object(samba_steps, "run", side_effect=self.run_ufw):
            samba_steps.configure_samba_firewall(_make_config(samba_sources=["192.168.1.0/24"]))
        deletes = [command for command in self.commands if "delete" in command]
        self.assertEqual(deletes, ["ufw --force delete 5", "ufw --force delete 3", "ufw --force delete 1"])
        self.assertEqual(len(self.rules), 2)
        self.assertIn("22/tcp ALLOW IN 10.0.0.0/8 # operator", self.rules)

    def test_clearing_sources_removes_old_managed_restrictions(self) -> None:
        self.rules = ["445/tcp ALLOW IN 10.0.0.0/8 # basaltwater Samba 445/tcp source 10.0.0.0/8"]
        with patch.object(samba_steps, "run", side_effect=self.run_ufw):
            samba_steps.configure_samba_firewall(_make_config())
        self.assertEqual(self.rules, ["445/tcp ALLOW IN Anywhere # Samba SMB"])

    def test_inspection_deletion_and_reload_failures_are_fatal(self) -> None:
        for failed_command in ("ufw status numbered", "ufw --force delete 1", "ufw reload", "ufw status verbose"):
            with self.subTest(failed_command=failed_command):
                self.rules = ["445/tcp ALLOW IN Anywhere"]
                def fail(command, **kwargs):
                    if command == failed_command:
                        return MagicMock(returncode=1, stdout="", stderr="failed")
                    return self.run_ufw(command, **kwargs)
                with patch.object(samba_steps, "run", side_effect=fail), self.assertRaises(RuntimeError):
                    samba_steps.configure_samba_firewall(_make_config(samba_sources=["192.168.1.0/24"]))

    def test_successful_but_ineffective_deletion_is_fatal(self) -> None:
        self.rules = ["445/tcp ALLOW IN Anywhere"]
        def ignore_delete(command, **kwargs):
            if command.startswith("ufw --force delete"):
                return MagicMock(returncode=0)
            return self.run_ufw(command, **kwargs)
        with patch.object(samba_steps, "run", side_effect=ignore_delete), \
             self.assertRaisesRegex(RuntimeError, "Obsolete Samba firewall rules remain"):
            samba_steps.configure_samba_firewall(_make_config(samba_sources=["192.168.1.0/24"]))

    def test_inactive_numbered_status_fails_before_rule_mutation(self) -> None:
        with patch.object(samba_steps, "run", return_value=MagicMock(
            returncode=0, stdout="Status: inactive\n",
        )) as run, self.assertRaisesRegex(RuntimeError, "requires active UFW"):
            samba_steps.configure_samba_firewall(_make_config(samba_sources=["192.168.1.0/24"]))
        self.assertEqual([call.args[0] for call in run.call_args_list], ["ufw status numbered"])

    def test_failed_final_inspection_or_missing_requested_rule_is_fatal(self) -> None:
        for fail_inspection in (False, True):
            with self.subTest(fail_inspection=fail_inspection):
                self.rules = []
                inspections = 0
                def inspect(command, **kwargs):
                    nonlocal inspections
                    if command == "ufw status numbered":
                        inspections += 1
                        if inspections == 3:
                            return MagicMock(returncode=int(fail_inspection), stdout="Status: active\n")
                    return self.run_ufw(command, **kwargs)
                with patch.object(samba_steps, "run", side_effect=inspect), self.assertRaises(RuntimeError):
                    samba_steps.configure_samba_firewall(_make_config(samba_sources=["192.168.1.0/24"]))

    def test_inactive_firewall_or_unfiltered_default_cannot_report_success(self) -> None:
        for output in ("Status: inactive\n", "Status: active\nDefault: allow (incoming)\n", "Status: active\n"):
            with self.subTest(output=output):
                self.verbose = output
                with patch.object(samba_steps, "run", side_effect=self.run_ufw), self.assertRaises(RuntimeError):
                    samba_steps.configure_samba_firewall(_make_config(samba_sources=["192.168.1.0/24"]))

    def test_unrelated_rules_that_widen_smb_access_are_reported_and_preserved(self) -> None:
        for rule in (
            "440:450/tcp ALLOW IN Anywhere # operator range",
            "137,138/udp ALLOW IN Anywhere\n139,445/tcp ALLOW IN Anywhere # expanded Samba profile",
            "445/tcp ALLOW IN 10.0.0.0/8 # operator subnet",
            "445/tcp (v6) ALLOW IN 2001:db8::/32 # operator IPv6",
        ):
            with self.subTest(rule=rule):
                self.rules = [rule]
                with patch.object(samba_steps, "run", side_effect=self.run_ufw), \
                     self.assertRaisesRegex(RuntimeError, "unrequested source"):
                    samba_steps.configure_samba_firewall(_make_config(samba_sources=["192.168.1.0/24"]))
                self.assertIn(rule, self.rules)

    def test_requested_ipv4_and_ipv6_sources_pass_verification(self) -> None:
        with patch.object(samba_steps, "run", side_effect=self.run_ufw):
            samba_steps.configure_samba_firewall(_make_config(
                samba_sources=["192.168.1.0/24", "fd00::/64"], machine_type="privileged",
            ))
        self.assertEqual(len(self.rules), 2)


class TestSambaFirewallComposition(unittest.TestCase):
    def test_capable_server_lite_samba_selects_base_firewall(self) -> None:
        from plugins.server import build_server_steps
        from security.steps import configure_firewall

        for machine in ("vm", "hardware", "privileged", "unprivileged", "oci", "wsl"):
            with self.subTest(machine=machine):
                steps = build_server_steps(_make_config(enable_samba=True, machine_type=machine))
                functions = [function for _label, function in steps]
                self.assertEqual(functions.count(configure_firewall), int(machine in {"vm", "hardware", "privileged"}))

    def test_existing_profiles_keep_single_firewall_and_proxmox_native_plan(self) -> None:
        from plugins.server import build_server_steps
        from plugins.workstation import build_workstation_steps
        from plugins.proxmox import build_server_proxmox_steps
        from security.steps import configure_firewall, configure_firewall_web

        for profile, builder in (("server_dev", build_server_steps), ("server_web", build_server_steps),
                                 ("workstation_dev", build_workstation_steps), ("server_proxmox", build_server_proxmox_steps)):
            with self.subTest(profile=profile):
                functions = [function for _label, function in builder(_make_config(
                    enable_samba=True, system_type=profile, include_web_firewall=profile == "server_web",
                ))]
                self.assertEqual(sum(function in {configure_firewall, configure_firewall_web} for function in functions),
                                 int(profile != "server_proxmox"))


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
    def test_non_normalized_root_cannot_bypass_missing_declared_storage(self) -> None:
        for path in ("/srv/other/../data/docs", "/srv/data/./docs", "//srv/data/docs", "/srv/data/docs/"):
            with self.subTest(path=path), tempfile.TemporaryDirectory() as directory:
                conf = Path(directory) / "smb.conf"
                conf.write_text("[global]\n")
                config = _make_config(
                    samba_shares=[["write", "docs", path, "alice:secret"]],
                    storage_mounts=[["data", "/srv/data", "ext4", "empty"]],
                )
                with patch.object(samba_steps, "SMB_CONF_PATH", str(conf)), \
                     patch.object(samba_steps, "run") as run, \
                     patch.object(samba_steps.os, "makedirs") as mkdir, \
                     patch.object(samba_steps, "_prepare_samba_share") as prepare, \
                     patch("common.storage_steps.read_json_file", side_effect=FileNotFoundError):
                    with self.assertRaisesRegex(ValueError, "must be normalized"):
                        samba_steps.reconcile_samba_shares(config)
                    # The canonical spelling must still fail the actual mount
                    # guard when no declared storage marker is available.
                    config.samba_shares[0][2] = "/srv/data/docs"
                    with self.assertRaisesRegex(RuntimeError, "storage marker is missing"):
                        samba_steps.reconcile_samba_shares(config)
                run.assert_not_called()
                mkdir.assert_not_called()
                prepare.assert_not_called()
                self.assertEqual(conf.read_text(), "[global]\n")

    def test_overlapping_roots_fail_before_any_account_or_filesystem_mutation(self) -> None:
        for first, second in (
            ("/srv/docs", "/srv/docs"),
            ("/srv/docs", "/srv/docs/archive"),
            ("/srv/docs/archive", "/srv/docs"),
            ("/srv/docs/", "/srv/other/../docs"),
            ("//srv/docs", "/srv/docs/archive"),
        ):
            with self.subTest(first=first, second=second):
                shares = [["read", "docs", first, "alice:secret"],
                          ["write", "archive", second, "bob:secret"]]
                with self.assertRaisesRegex(ValueError, "Overlapping Samba share roots"):
                    validate_samba_share_specs(shares)
                with patch.object(samba_steps, "run") as run, \
                     patch.object(samba_steps.os, "makedirs") as mkdir, \
                     patch.object(samba_steps, "_prepare_samba_share") as prepare, \
                     self.assertRaisesRegex(ValueError, "Overlapping Samba share roots"):
                    samba_steps.reconcile_samba_shares(_make_config(samba_shares=shares))
                run.assert_not_called()
                mkdir.assert_not_called()
                prepare.assert_not_called()

    def test_sibling_roots_with_shared_prefix_are_allowed(self) -> None:
        validate_samba_share_specs([
            ["read", "docs", "/srv/docs", "alice:secret"],
            ["write", "archive", "/srv/docs-archive", "bob:secret"],
        ])

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
                 patch.object(samba_steps.os, "fchown"), \
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


class TestSambaConfigActivation(unittest.TestCase):
    def setUp(self) -> None:
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.conf = Path(directory) / "smb.conf"
        self.original = ("[global]\n   workgroup = LEGACY\n\n[obsolete_read]\n"
                         "   path = /srv/obsolete\n   valid users = @smb_obsolete_read\n"
                         "   force group = smb_obsolete_read\n")
        self.conf.write_text(self.original)
        self.conf.chmod(0o640)
        self.commands: list[str] = []
        self.candidates: list[Path] = []
        self.failure = ""
        self.error: BaseException | None = None
        self.enterContext(patch.object(samba_steps, "SMB_CONF_PATH", str(self.conf)))
        self.enterContext(patch.object(samba_steps, "run", side_effect=self.run_command))
        self.enterContext(patch.object(samba_steps.os, "fchown"))
        self.enterContext(patch.object(samba_steps, "assert_declared_storage_mount"))
        self.enterContext(patch.object(samba_steps, "_prepare_samba_share", return_value=(
            "smb_docs_read", "[docs_read]\n   path = /srv/docs\n",
        )))

    def configure(self, operation: str) -> None:
        config = _make_config(samba_shares=[["read", "docs", "/srv/docs", "alice:secret"]])
        if operation == "global":
            samba_steps.configure_samba_global_settings(config)
        else:
            samba_steps.reconcile_samba_shares(config)

    def run_command(self, command: str, **_kwargs: object) -> MagicMock:
        self.commands.append(command)
        if command.startswith("testparm -s "):
            candidate = Path(shlex.split(command)[-1])
            self.candidates.append(candidate)
            self.assertNotEqual(candidate, self.conf)
            self.assertEqual(candidate.parent, self.conf.parent)
            self.assertEqual(candidate.stat().st_mode & 0o777, 0o600)
            self.assertNotEqual(candidate.read_text(), self.original)
            self.assertEqual(self.conf.read_text(), self.original)
        if self.failure and command.startswith(self.failure):
            if self.error:
                raise self.error
            return MagicMock(returncode=1, stderr="bad candidate")
        return MagicMock(returncode=0, stderr="")

    def assert_restored(self) -> None:
        self.assertEqual(self.conf.read_text(), self.original)
        self.assertEqual(self.conf.stat().st_mode & 0o777, 0o640)
        self.assertTrue(all(not path.exists() for path in self.candidates))
        self.assertEqual(list(self.conf.parent.iterdir()), [self.conf])

    def test_validation_rejection_exception_timeout_and_interrupt_preserve_live_file(self) -> None:
        for operation in ("global", "shares"):
            for error in (None, subprocess.CalledProcessError(1, "testparm"),
                          subprocess.TimeoutExpired("testparm", 30), KeyboardInterrupt()):
                with self.subTest(operation=operation, error=type(error).__name__):
                    self.commands.clear()
                    self.failure, self.error = "testparm -s ", error
                    with self.assertRaises(type(error) if error else RuntimeError):
                        self.configure(operation)
                    self.assert_restored()
                    self.assertFalse(any(command.startswith(("systemctl", "groupdel", "printf"))
                                         for command in self.commands))

    def test_activation_failure_restores_config_and_retry_activates(self) -> None:
        for operation, failure in (
            ("global", "systemctl restart smbd"),
            ("shares", "install -d -m 0755"),
            ("shares", "printf %s"),
            ("shares", "systemctl daemon-reload"),
            ("shares", "systemctl reload smbd"),
        ):
            with self.subTest(operation=operation, failure=failure):
                self.conf.write_text(self.original)
                self.commands.clear()
                self.failure, self.error = failure, subprocess.CalledProcessError(1, failure)
                with self.assertRaises(subprocess.CalledProcessError):
                    self.configure(operation)
                self.assert_restored()
                self.assertNotIn("groupdel smb_obsolete_read", self.commands)

                self.commands.clear()
                self.failure = ""
                self.configure(operation)
                self.assertNotEqual(self.conf.read_text(), self.original)
                self.assertEqual(self.conf.stat().st_mode & 0o777, 0o640)
                activation = "systemctl restart smbd" if operation == "global" else "systemctl reload smbd"
                self.assertEqual(self.commands.count(activation), 1)
                self.commands.clear()
                self.configure(operation)
                self.assertNotIn("systemctl restart smbd", self.commands)
                self.assertNotIn("systemctl reload smbd", self.commands)
                self.assertFalse(any(command.startswith("testparm") for command in self.commands))

    def test_activation_interrupt_restores_previous_file(self) -> None:
        self.failure, self.error = "systemctl restart smbd", KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.configure("global")
        self.assert_restored()

    def test_post_publication_sync_error_also_restores_previous_file(self) -> None:
        writer = samba_steps.write_text_atomic
        publications = 0

        def fail_after_replace(*args, **kwargs):
            nonlocal publications
            writer(*args, **kwargs)
            publications += 1
            if publications == 1:
                raise OSError("directory sync failed after replacement")

        with patch.object(samba_steps, "write_text_atomic", side_effect=fail_after_replace), \
             self.assertRaisesRegex(OSError, "directory sync failed"):
            self.configure("global")
        self.assert_restored()
        self.assertNotIn("systemctl restart smbd", self.commands)


if __name__ == '__main__':
    unittest.main()
