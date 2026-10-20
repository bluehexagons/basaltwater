"""SMB unit generation, migration and rollback without system mutation."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from lib.config import SetupConfig
from lib import unit_transaction
from smb import smb_mount_steps


class TestConfigureSmbMountUnit(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.unit_dir = self.root / "units"
        self.unit_dir.mkdir()
        self.creds = self.root / "creds%h"
        self.mountpoint = str(self.root / "mnt-share")
        self.escaped = "mnt-share"
        self.states: dict[str, list[str]] = {}
        self.commands: list[list[str]] = []
        self.failure: list[str] | None = None
        for target, replacement in (
            ("SMB_UNIT_DIR", str(self.unit_dir)),
            ("SMB_CREDENTIALS_DIR", str(self.creds)),
        ):
            self.enterContext(patch.object(smb_mount_steps, target, replacement))
        self.enterContext(patch.object(smb_mount_steps, "validate_smb_mount_specs"))
        self.enterContext(patch.object(smb_mount_steps.os, "fchown"))
        self.enterContext(patch.object(smb_mount_steps.pwd, "getpwnam", return_value=MagicMock(pw_uid=123)))
        self.enterContext(patch.object(smb_mount_steps.grp, "getgrnam", return_value=MagicMock(gr_gid=456)))
        self.enterContext(patch.object(smb_mount_steps, "run", side_effect=lambda *args, **kwargs: MagicMock(
            returncode=0, stdout=self.escaped + "\n",
        )))
        self.enterContext(patch.object(unit_transaction, "run", side_effect=self.run_command))
        self.enterContext(patch.object(unit_transaction, "is_dry_run", return_value=False))

    def run_command(self, command: list[str], **_kwargs: object) -> subprocess.CompletedProcess:
        self.commands.append(command)
        if command == self.failure:
            self.failure = None
            raise subprocess.CalledProcessError(1, command)
        output = ""
        if command[0] == "systemd-analyze":
            self.assertEqual(len(command[2:]), 2)
            self.assertTrue(all(Path(candidate).is_file() for candidate in command[2:]))
        elif command[0] == "systemctl" and command[1] != "daemon-reload":
            name = command[2] if command[1] == "show" else command[-1]
            path = self.unit_dir / name
            state = self.states.setdefault(name, ["inactive", ""])
            if command[1] == "show":
                enabled = state[1] if path.exists() else ""
                if path.exists() and "[Install]" not in path.read_text():
                    enabled = "static"
                output = (f"LoadState={'loaded' if path.exists() else 'not-found'}\n"
                          f"ActiveState={state[0]}\nUnitFileState={enabled}\n")
            elif command[1] == "stop":
                self.assertTrue(path.exists(), "must not stop a missing automount")
                state[0] = "inactive"
            elif command[1] == "restart":
                state[0] = "active"
            elif command[1] == "enable":
                state[1] = "enabled-runtime" if "--runtime" in command else "enabled"
            elif command[1] == "disable":
                state[1] = "disabled"
        return subprocess.CompletedProcess(command, 0, output, "")

    def configure(self, **overrides: str) -> None:
        smb_mount_steps.configure_smb_mount(
            SetupConfig(host="h", username="alice", system_type="server_lite"),
            [overrides.get("mountpoint", self.mountpoint), "192.168.1.10",
             overrides.get("credentials", "svc:hunter2"), overrides.get("share", "docs"),
             overrides.get("subdir", "/sub")],
        )

    def seed_old_units(self, *, automount: bool) -> dict[str, str]:
        original = {"mnt-share.mount": "old mount\n[Install]\nWantedBy=multi-user.target\n"}
        self.states["mnt-share.mount"] = ["active", "enabled"]
        if automount:
            original["mnt-share.automount"] = "old automount\n[Install]\nWantedBy=multi-user.target\n"
            self.states["mnt-share.automount"] = ["active", "enabled-runtime"]
        for name, content in original.items():
            path = self.unit_dir / name
            path.write_text(content)
            path.chmod(0o640)
        return original

    def test_native_pair_pins_smb3_and_enables_only_the_automount(self) -> None:
        self.configure()
        mount = (self.unit_dir / "mnt-share.mount").read_text()
        automount = (self.unit_dir / "mnt-share.automount").read_text()
        self.assertIn("vers=3.0,seal,nofail", mount)
        self.assertIn("file_mode=0644,dir_mode=0755", mount)
        self.assertNotIn("x-systemd.", mount)
        self.assertNotIn("[Install]", mount)
        self.assertIn("After=network-online.target\nWants=network-online.target\n", mount)
        self.assertIn(f"[Automount]\nWhere={self.mountpoint}\nTimeoutIdleSec=60\n", automount)
        self.assertNotIn("network-online.target", automount)
        self.assertIn("WantedBy=multi-user.target", automount)
        self.assertIn(["systemctl", "enable", "mnt-share.automount"], self.commands)
        self.assertNotIn(["systemctl", "enable", "mnt-share.mount"], self.commands)
        self.assertNotIn(["systemctl", "restart", "mnt-share.mount"], self.commands)
        self.assertEqual((self.creds / "credentials-mnt-share").read_text(), "username=svc\npassword=hunter2\n")

    def test_migration_disables_old_mount_before_enabling_automount(self) -> None:
        self.seed_old_units(automount=False)
        self.configure()
        self.assertEqual(self.states["mnt-share.mount"], ["inactive", "disabled"])
        self.assertEqual(self.states["mnt-share.automount"], ["active", "enabled"])
        self.assertLess(self.commands.index(["systemctl", "disable", "mnt-share.mount"]),
                        self.commands.index(["systemctl", "enable", "mnt-share.automount"]))

    def test_existing_automount_stops_before_mount_and_repeated_setup_succeeds(self) -> None:
        self.seed_old_units(automount=True)
        self.configure()
        self.assertLess(self.commands.index(["systemctl", "stop", "mnt-share.automount"]),
                        self.commands.index(["systemctl", "stop", "mnt-share.mount"]))
        self.configure()
        self.assertEqual(self.states["mnt-share.automount"], ["active", "enabled"])

    def test_migration_failures_restore_previous_files_modes_and_states(self) -> None:
        for automount in (False, True):
            for action in ("stop", "disable", "daemon-reload", "enable", "restart"):
                with self.subTest(automount=automount, action=action):
                    for path in self.unit_dir.glob("mnt-share.*"):
                        path.unlink()
                    self.states.clear()
                    self.commands.clear()
                    originals = self.seed_old_units(automount=automount)
                    expected_states = {name: list(state) for name, state in self.states.items()}
                    self.failure = ["systemctl", action]
                    if action in {"stop", "disable"}:
                        self.failure.append("mnt-share.mount")
                    elif action in {"enable", "restart"}:
                        self.failure.append("mnt-share.automount")
                    with self.assertRaises(subprocess.CalledProcessError):
                        self.configure()
                    for name, content in originals.items():
                        self.assertEqual((self.unit_dir / name).read_text(), content)
                        self.assertEqual((self.unit_dir / name).stat().st_mode & 0o777, 0o640)
                        self.assertEqual(self.states[name], expected_states[name])
                    if not automount:
                        self.assertFalse((self.unit_dir / "mnt-share.automount").exists())
                    self.assertFalse((self.unit_dir / ".basaltwater-unit-operation.json").exists())

    def test_validation_failure_preserves_old_units_without_stopping(self) -> None:
        originals = self.seed_old_units(automount=True)
        real_runner = self.run_command
        def fail_verify(command, **kwargs):
            if command[0] == "systemd-analyze":
                raise subprocess.CalledProcessError(1, command)
            return real_runner(command, **kwargs)
        with patch.object(unit_transaction, "run", side_effect=fail_verify), self.assertRaises(subprocess.CalledProcessError):
            self.configure()
        self.assertFalse(any(command[1] == "stop" for command in self.commands))
        for name, content in originals.items():
            self.assertEqual((self.unit_dir / name).read_text(), content)

    def test_unit_values_preserve_literal_percent_signs_and_escaped_name_backslashes(self) -> None:
        self.escaped = "mnt-share\\x25h"
        self.configure(mountpoint=self.mountpoint + "%h", share="docs%H", subdir="/reports%u")
        mount = (self.unit_dir / (self.escaped + ".mount")).read_text()
        automount = (self.unit_dir / (self.escaped + ".automount")).read_text()
        self.assertIn(f"Description=SMB mount for {self.mountpoint}%%h\n", mount)
        self.assertIn("What=//192.168.1.10/docs%%H/reports%%u\n", mount)
        for content in (mount, automount):
            self.assertIn(f"Where={self.mountpoint}%%h\n", content)
        self.assertIn("/credentials-mnt-share\\\\x25h,", mount)
        self.assertIn(str(self.creds).replace("%", "%%"), mount)
        self.assertTrue((self.creds / ("credentials-" + self.escaped)).is_file())

    def test_distinct_mountpoints_use_distinct_credential_files(self) -> None:
        self.escaped = "mnt-a_b"
        self.configure(mountpoint=str(self.root / "a_b"), credentials="first:one")
        self.escaped = "mnt-a-b"
        self.configure(mountpoint=str(self.root / "a" / "b"), credentials="second:two")
        self.assertEqual(sorted(path.name for path in self.creds.iterdir()), ["credentials-mnt-a-b", "credentials-mnt-a_b"])
        self.assertIn("username=first", (self.creds / "credentials-mnt-a_b").read_text())
        self.assertIn("username=second", (self.creds / "credentials-mnt-a-b").read_text())


class TestSmbMountPreflight(unittest.TestCase):
    def test_invalid_spec_is_rejected_before_filesystem_changes(self) -> None:
        with patch.object(smb_mount_steps, "run") as run, \
             patch.object(smb_mount_steps, "directory_fd") as directory:
            with self.assertRaisesRegex(ValueError, "control characters"):
                smb_mount_steps.configure_smb_mount(
                    SetupConfig(host="h", username="alice", system_type="server_lite"),
                    ["/mnt/share", "192.168.1.10", "svc:secret\nvalue", "docs", "/"],
                )
        run.assert_not_called()
        directory.assert_not_called()

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
