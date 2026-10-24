"""SMB unit generation, migration and rollback without system mutation."""

from __future__ import annotations

import errno
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
        self.chown = self.enterContext(patch.object(smb_mount_steps.os, "fchown"))
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
                if name.endswith(".automount"):
                    mount = name.removesuffix(".automount") + ".mount"
                    if self.states.get(mount, ["inactive"])[0] == "active":
                        raise subprocess.CalledProcessError(1, command, stderr="Path is already a mount point")
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

    def test_native_pair_negotiates_smb3_or_newer_and_enables_only_the_automount(self) -> None:
        self.configure()
        mount = (self.unit_dir / "mnt-share.mount").read_text()
        automount = (self.unit_dir / "mnt-share.automount").read_text()
        options = next(line.removeprefix("Options=").split(",") for line in mount.splitlines()
                       if line.startswith("Options="))
        self.assertEqual([option for option in options if option.startswith("vers=")], ["vers=3"])
        self.assertIn("seal", options)
        self.assertIn("nofail", options)
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

    def test_rerun_never_probes_or_chowns_remote_root_before_group_is_stopped(self) -> None:
        for mount_state in ("active", "failed", "missing"):
            with self.subTest(mount_state=mount_state):
                originals = self.seed_old_units(automount=True)
                self.states["mnt-share.mount"][0] = mount_state
                if mount_state == "missing":
                    (self.unit_dir / "mnt-share.mount").unlink()
                    self.states["mnt-share.mount"] = ["inactive", ""]
                Path(self.mountpoint).mkdir(exist_ok=True)
                self.commands.clear()
                events = []
                real_open, real_stat = os.open, os.stat

                def quiescent():
                    return all(self.states[name][0] == "inactive"
                               for name in ("mnt-share.automount", "mnt-share.mount"))

                def open_directory(path, flags, *args, **kwargs):
                    if path in (self.mountpoint, os.path.basename(self.mountpoint)):
                        if flags & os.O_PATH:
                            self.assertTrue(flags & os.O_NOFOLLOW)
                            self.assertTrue(flags & os.O_DIRECTORY)
                            self.assertIsInstance(kwargs.get("dir_fd"), int)
                            events.append("preflight")
                        else:
                            if not quiescent():
                                raise OSError(errno.ENODEV, "remote mount unavailable")
                            events.append("local-open")
                    return real_open(path, flags, *args, **kwargs)

                def stat_path(path, *args, **kwargs):
                    if path in (self.mountpoint, os.path.basename(self.mountpoint)) and not quiescent():
                        raise OSError(errno.ENODEV, "remote metadata probe")
                    return real_stat(path, *args, **kwargs)

                def chown(descriptor, uid, gid):
                    self.assertTrue(quiescent())
                    if (uid, gid) == (123, 456):
                        self.assertEqual(os.fstat(descriptor).st_ino, real_stat(self.mountpoint).st_ino)
                        self.assertEqual((self.unit_dir / "mnt-share.automount").read_text(),
                                         originals["mnt-share.automount"])
                        events.append("local-chown")

                with patch.object(smb_mount_steps.os, "open", side_effect=open_directory), \
                     patch.object(smb_mount_steps.os, "stat", side_effect=stat_path), \
                     patch.object(smb_mount_steps.os, "fchown", side_effect=chown):
                    self.configure(credentials="changed:new-secret", share="new-server-share")
                self.assertEqual(events, ["preflight", "local-open", "local-chown"])
                self.assertEqual(self.states["mnt-share.automount"], ["active", "enabled"])
                self.assertEqual(self.states["mnt-share.mount"][0], "inactive")
                self.assertEqual((self.creds / "credentials-mnt-share").read_text(),
                                 "username=changed\npassword=new-secret\n")
                self.assertFalse(any(command[:2] == ["systemctl", "restart"] and command[-1].endswith(".mount")
                                     for command in self.commands))

    def test_preparation_failure_restores_original_unit_state(self) -> None:
        for failed_mount in (False, True):
            with self.subTest(failed_mount=failed_mount):
                originals = self.seed_old_units(automount=True)
                if failed_mount:
                    self.states["mnt-share.mount"][0] = "failed"
                expected_states = {name: list(state) for name, state in self.states.items()}
                with patch.object(smb_mount_steps, "_write_credentials", side_effect=OSError("credential write failed")):
                    with self.assertRaisesRegex(OSError, "credential write failed"):
                        self.configure()
                for name, content in originals.items():
                    self.assertEqual((self.unit_dir / name).read_text(), content)
                    self.assertEqual((self.unit_dir / name).stat().st_mode & 0o777, 0o640)
                    self.assertEqual(self.states[name][1], expected_states[name][1])
                    self.assertEqual(self.states[name][0] == "active", expected_states[name][0] == "active")
                self.assertFalse((self.unit_dir / ".basaltwater-unit-operation.json").exists())
                self.assertFalse(self.creds.exists())

    def test_symlink_swap_after_preflight_is_rejected_while_quiescent(self) -> None:
        originals = self.seed_old_units(automount=True)
        Path(self.mountpoint).mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        real_runner = self.run_command

        def swap_after_stop(command, **kwargs):
            result = real_runner(command, **kwargs)
            if command == ["systemctl", "stop", "mnt-share.mount"] and not Path(self.mountpoint).is_symlink():
                Path(self.mountpoint).rmdir()
                Path(self.mountpoint).symlink_to(outside, target_is_directory=True)
            return result

        with patch.object(unit_transaction, "run", side_effect=swap_after_stop):
            with self.assertRaisesRegex(ValueError, "symlink"):
                self.configure()
        # Rollback restores unit-file metadata, but never owns the mountpoint
        # or credentials after the swapped path fails its descriptor check.
        self.assertFalse(any(call.args[1:] == (123, 456) for call in self.chown.call_args_list))
        self.assertFalse(self.creds.exists())
        self.assertEqual(list(outside.iterdir()), [])
        for name, content in originals.items():
            self.assertEqual((self.unit_dir / name).read_text(), content)
        self.assertEqual(self.states["mnt-share.automount"], ["active", "enabled-runtime"])
        self.assertEqual(self.states["mnt-share.mount"], ["active", "enabled"])

    def test_dry_run_does_not_prepare_mountpoint_or_credentials(self) -> None:
        with patch.object(unit_transaction, "is_dry_run", return_value=True):
            self.configure()
        self.chown.assert_not_called()
        self.assertFalse(Path(self.mountpoint).exists())
        self.assertFalse(self.creds.exists())
        self.assertEqual(list(self.unit_dir.iterdir()), [])
        self.assertEqual(self.commands, [])

    def test_migration_failures_restore_previous_files_modes_and_states(self) -> None:
        for automount in (False, True):
            for action in ("stop", "disable", "daemon-reload", "enable", "restart"):
                with self.subTest(automount=automount, action=action):
                    for path in self.unit_dir.glob("mnt-share.*"):
                        path.unlink()
                    self.states.clear()
                    self.commands.clear()
                    self.chown.reset_mock()
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
                    if action in {"stop", "disable"}:
                        self.assertFalse(any(call.args[1:] == (123, 456) for call in self.chown.call_args_list))

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
        self.chown.assert_not_called()
        self.assertFalse(Path(self.mountpoint).exists())
        self.assertFalse(self.creds.exists())
        for name, content in originals.items():
            self.assertEqual((self.unit_dir / name).read_text(), content)

    def test_migration_rollback_rearms_automount_before_mount(self) -> None:
        self.seed_old_units(automount=True)
        self.failure = ["systemctl", "daemon-reload"]
        with self.assertRaises(subprocess.CalledProcessError):
            self.configure()
        self.assertEqual(self.states["mnt-share.automount"], ["active", "enabled-runtime"])
        self.assertEqual(self.states["mnt-share.mount"], ["active", "enabled"])
        self.assertFalse((self.unit_dir / ".basaltwater-unit-operation.json").exists())

    def test_unit_values_preserve_literal_percent_signs_and_escaped_name_backslashes(self) -> None:
        self.escaped = "mnt-share\\x25h"
        self.configure(mountpoint=self.mountpoint + "%h", share="docs%H", subdir="/reports%u")
        mount = (self.unit_dir / (self.escaped + ".mount")).read_text()
        automount = (self.unit_dir / (self.escaped + ".automount")).read_text()
        self.assertIn(f"Description=SMB mount for {self.mountpoint}%%h\n", mount)
        self.assertIn("What=//192.168.1.10/docs%%H/reports%%u\n", mount)
        for content in (mount, automount):
            self.assertIn(f"Where={self.mountpoint}%%h\n", content)
        self.assertIn("/credentials-mnt-share\\x25h,", mount)
        self.assertIn(str(self.creds).replace("%", "%%"), mount)
        self.assertTrue((self.creds / ("credentials-" + self.escaped)).is_file())

    def test_credentials_option_resolves_to_actual_file_for_escaped_mount_names(self) -> None:
        for mount_name, escaped in (("my-share", r"mnt-my\x2dshare"),
                                    ("space dir", r"mnt-space\x20dir"),
                                    ("percent%h", r"mnt-percent\x25h")):
            with self.subTest(mount_name=mount_name):
                self.escaped = escaped
                self.configure(mountpoint=str(self.root / mount_name))
                content = (self.unit_dir / (escaped + ".mount")).read_text()
                options = next(line.removeprefix("Options=") for line in content.splitlines()
                               if line.startswith("Options="))
                # Upstream config_parse_unit_string_printf expands %% without
                # unescaping backslashes; mount.cifs opens this value verbatim.
                credential_path = options.split(",", 1)[0].removeprefix("credentials=").replace("%%", "%")
                self.assertEqual(credential_path, str(self.creds / ("credentials-" + escaped)))
                self.assertEqual(Path(credential_path).read_text(), "username=svc\npassword=hunter2\n")

    def test_distinct_mountpoints_use_distinct_credential_files(self) -> None:
        self.escaped = "mnt-a_b"
        self.configure(mountpoint=str(self.root / "a_b"), credentials="first:one")
        self.escaped = "mnt-a-b"
        self.configure(mountpoint=str(self.root / "a" / "b"), credentials="second:two")
        self.assertEqual(sorted(path.name for path in self.creds.iterdir()), ["credentials-mnt-a-b", "credentials-mnt-a_b"])
        self.assertIn("username=first", (self.creds / "credentials-mnt-a_b").read_text())
        self.assertIn("username=second", (self.creds / "credentials-mnt-a-b").read_text())


class TestSmbMountPreflight(unittest.TestCase):
    def test_non_directory_mountpoint_is_rejected_without_opening_for_io(self) -> None:
        for kind in ("file", "fifo"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                mountpoint = os.path.join(directory, "mount")
                if kind == "fifo":
                    os.mkfifo(mountpoint)
                else:
                    Path(mountpoint).write_text("preserve")
                with self.assertRaisesRegex(ValueError, "non-directory"):
                    smb_mount_steps._check_mountpoint_path(mountpoint)

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
