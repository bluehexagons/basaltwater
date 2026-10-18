"""Offline SSH lifecycle contracts; no host service or package operations."""

from __future__ import annotations

from contextlib import ExitStack
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import cachyos_ssh as wrapper
from common.service_tools import cachyos_ssh as ssh


class WrapperTests(unittest.TestCase):
    def config(self, **kwargs):
        values = dict(username="alice", machine_type="hardware", dry_run=False,
                      install_ssh=True, ssh_password_auth=False, ssh_password_window=None,
                      lan_access=False, access_sources=[])
        return SimpleNamespace(**(values | kwargs))

    def test_key_default_and_password_explicit(self):
        for overrides, mode in (({}, "key"), ({"ssh_password_auth": True, "lan_access": True}, "password"),
                                ({"install_ssh": False}, "off"), ({"install_ssh": None}, "leave")):
            with self.subTest(mode=mode), patch.object(wrapper, "run") as run, \
                    patch.object(wrapper, "is_dry_run", return_value=False), \
                    patch.object(wrapper, "can_manage_system_services", return_value=True):
                wrapper.configure(self.config(**overrides))
                self.assertEqual(run.call_args_list[0].args[0], ["sudo", "-v"])
                command = run.call_args.args[0]
                self.assertEqual(command[:4], ["sudo", "-n", "/usr/bin/python3", "-I"])
                self.assertTrue(Path(command[4]).is_absolute())
                self.assertEqual(command[-3:], ["alice", mode, "0"])

    def test_dry_run_no_system_calls(self):
        with patch.object(wrapper, "run") as run, \
                patch.object(wrapper, "can_manage_system_services") as capability:
            wrapper.configure(self.config(dry_run=True, ssh_password_window=5, lan_access=True))
            run.assert_not_called()
            capability.assert_not_called()

    def test_auth_validation(self):
        for values in ({"ssh_password_auth": True}, {"ssh_password_window": 0},
                       {"ssh_password_window": True}, {"ssh_password_window": 61},
                       {"ssh_password_window": 3, "install_ssh": False},
                       {"ssh_password_window": 3, "ssh_password_auth": True, "lan_access": True},
                       {"ssh_password_auth": True, "lan_access": True, "access_sources": ["8.8.8.8"]},
                       {"username": "root"}):
            with self.subTest(values=values), patch.object(wrapper, "run") as run:
                with self.assertRaises(ValueError):
                    wrapper.configure(self.config(**values))
                run.assert_not_called()

    def test_service_capability_guard(self):
        with patch.object(wrapper, "is_dry_run", return_value=False), \
                patch.object(wrapper, "can_manage_system_services", return_value=False), \
                patch.object(wrapper, "run") as run:
            with self.assertRaises(ValueError):
                wrapper.configure(self.config())
            run.assert_not_called()


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.config_dir = Path(self.directory) / "config"
        self.unit_dir = Path(self.directory) / "units"
        self.config_dir.mkdir()
        self.unit_dir.mkdir()
        self.stack.enter_context(patch.object(ssh, "CONFIG_DIR", self.config_dir))
        self.stack.enter_context(patch.object(ssh, "UNIT_DIR", self.unit_dir))
        self.stack.enter_context(patch.object(ssh, "_unit_directories", return_value=(self.unit_dir,)))
        # Temporary fixtures belong to the test runner, never to the host's root.
        self.stack.enter_context(patch.object(ssh, "_secure"))
        self.stack.enter_context(patch("lib.atomic_io.os.fchown"))
        self.command = self.stack.enter_context(patch.object(ssh, "_command", return_value=""))
        self.show = self.stack.enter_context(patch.object(ssh, "_show", side_effect=lambda _: self.absent()))
        self.replace = self.stack.enter_context(patch.object(ssh, "replace_units"))
        self.remove = self.stack.enter_context(patch.object(ssh, "remove_units"))
        self.stack.enter_context(patch.object(ssh, "_native_binaries"))

    @staticmethod
    def absent():
        return dict(LoadState="not-found", ActiveState="inactive", UnitFileState="",
                    DropInPaths="", MainPID="0")

    def main_state(self, mode="key"):
        path = ssh._immutable_config("alice", mode)
        (self.unit_dir / ssh.SERVICE).write_text(ssh._unit(path))
        return self.absent() | dict(LoadState="loaded", Transient="no", ActiveState="active",
                                   FragmentPath=str(self.unit_dir / ssh.SERVICE), MainPID="123")

    def bootstrap_state(self, minutes=5):
        path = ssh._immutable_config("alice", "bootstrap")
        return self.absent() | dict(LoadState="loaded", ActiveState="active", Transient="yes",
            Description=ssh.BOOTSTRAP_DESCRIPTION, User="root", KillMode="control-group", Restart="no",
            RuntimeMaxUSec=f"{minutes}min", TimeoutStopUSec="5s",
            InaccessiblePaths="/run/systemd /run/dbus",
            ExecStart=f"{{ path=/usr/bin/sshd ; argv[]=/usr/bin/sshd -D -e -f {path} ; ignore_errors=no ; }}")

    def test_standalone_configs_restrict_account_and_auth(self):
        for mode in ("key", "password", "bootstrap"):
            config = ssh._config("alice", mode)
            for expected in ("AllowUsers alice\n", "PermitRootLogin no\n", "PermitEmptyPasswords no\n",
                             "KbdInteractiveAuthentication no\n", "UsePAM yes\n", "AddressFamily inet\n",
                             "PubkeyAuthentication yes\n", "Subsystem sftp internal-sftp\n"):
                self.assertIn(expected, config)
            self.assertNotIn("Include ", config)
            self.assertIn("PasswordAuthentication " + ("no" if mode == "key" else "yes") + "\n", config)
            self.assertIn("AuthenticationMethods " + ("publickey" if mode == "key" else "publickey password") + "\n", config)
            self.assertIn(f"Port {2222 if mode == 'bootstrap' else 22}\n", config)

    def test_config_immutable_modified_preserved_and_modes_distinct(self):
        old = ssh._immutable_config("alice", "key")
        password = ssh._immutable_config("alice", "password")
        self.assertNotEqual(old, password)
        self.assertEqual(old.read_text(), ssh._config("alice", "key"))
        old.write_text("user modified\n")
        with self.assertRaises(ValueError):
            ssh._immutable_config("alice", "key")
        self.assertEqual(old.read_text(), "user modified\n")

    def test_config_symlink_refused(self):
        path = ssh._config_path("alice", "key")
        path.symlink_to(self.unit_dir)
        with self.assertRaises(OSError):
            ssh._immutable_config("alice", "key")

    def test_directory_symlink_refused(self):
        link = Path(self.directory) / "linked"
        link.symlink_to(self.config_dir)
        with self.assertRaises(ValueError):
            ssh._root_directory(link)

    def test_main_uses_transaction_native_binary_and_validates_before_activation(self):
        with patch.object(ssh, "_validate_config") as validate:
            ssh._reconcile("alice", "key", 0)
            validate.assert_called_once_with(ssh._config_path("alice", "key"), "alice", "key")
        self.replace.assert_called_once()
        content = self.replace.call_args.args[0][ssh.SERVICE]
        self.assertIn("ExecStart=/usr/bin/sshd -D -e -f ", content)
        self.assertIn("WantedBy=multi-user.target", content)
        self.assertNotIn("python", content)
        self.assertIn(unittest.mock.call("/usr/bin/ssh-keygen", "-A"), self.command.call_args_list)

    def test_validation_failure_never_activates(self):
        with patch.object(ssh, "_validate_config", side_effect=RuntimeError("invalid")):
            with self.assertRaises(RuntimeError):
                ssh._reconcile("alice", "key", 0)
        self.replace.assert_not_called()

    def test_native_config_validation_effective_options(self):
        path = ssh._immutable_config("alice", "key")
        output = "\n".join(line.lower() for line in ssh._config("alice", "key").splitlines())
        self.command.side_effect = ["", output]
        ssh._validate_config(path, "alice", "key")
        self.command.side_effect = ["", output.replace("permitrootlogin no", "permitrootlogin yes")]
        with self.assertRaises(RuntimeError):
            ssh._validate_config(path, "alice", "key")

    def test_vendor_conflicts_never_stopped(self):
        for fields in ({"ActiveState": "active"}, {"UnitFileState": "enabled"},
                       {"UnitFileState": "enabled-runtime"}, {"ActiveState": "activating"}):
            self.show.side_effect = lambda unit: self.absent() | (fields if unit == "sshd.service" else {})
            with self.assertRaises(ValueError):
                ssh._reconcile("alice", "key", 0)
        self.replace.assert_not_called()
        self.assertFalse(any("stop" in call.args for call in self.command.call_args_list))

    def test_modified_main_and_dropins_refused(self):
        state = self.main_state()
        self.assertTrue(ssh._main_owned(state))
        (self.unit_dir / ssh.SERVICE).write_text("custom unit")
        with self.assertRaises(ValueError):
            ssh._main_owned(state)
        with self.assertRaises(ValueError):
            ssh._no_overrides(ssh.SERVICE, {"DropInPaths": "/custom.conf"})
        (self.unit_dir / (ssh.SERVICE + ".d")).mkdir()
        with self.assertRaises(ValueError):
            ssh._no_overrides(ssh.SERVICE, {})

    def test_ports_allow_only_owned_listener(self):
        self.command.return_value = 'LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=123,fd=3))'
        ssh._port_free(22, "123")
        for allowed in ("0", "124"):
            with self.assertRaises(ValueError):
                ssh._port_free(22, allowed)

    def test_off_removes_only_owned_main_transactionally(self):
        main = self.main_state()
        self.show.side_effect = lambda unit: main if unit == ssh.SERVICE else self.absent()
        ssh._reconcile("alice", "off", 0)
        self.remove.assert_called_once_with((ssh.SERVICE,), unit_dir=str(self.unit_dir))
        self.replace.assert_not_called()

    def test_omission_closes_window_without_touching_main(self):
        active = self.bootstrap_state()
        self.show.side_effect = [active, self.absent()]
        ssh._reconcile("alice", "leave", 0)
        self.command.assert_called_once_with("/usr/bin/systemctl", "stop", ssh.BOOTSTRAP_SERVICE)
        self.replace.assert_not_called()
        self.remove.assert_not_called()

    def test_no_window_replay(self):
        with patch.object(ssh, "_validate_config"), patch.object(ssh, "_start_bootstrap") as start:
            ssh._reconcile("alice", "key", 0)
            start.assert_not_called()
            ssh._reconcile("alice", "key", 5)
            start.assert_called_once_with("alice", 5)

    def test_bootstrap_runtime_and_deadline_properties(self):
        for minutes, formatted in ((1, "1min"), (5, "5min"), (60, "1h")):
            state = self.bootstrap_state(minutes) | {"RuntimeMaxUSec": formatted}
            self.show.side_effect = None
            self.show.return_value = state
            with patch.object(ssh, "_validate_config"):
                ssh._start_bootstrap("alice", minutes)
            command = next(call.args for call in reversed(self.command.call_args_list)
                           if call.args[0] == "/usr/bin/systemd-run")
            for prop in (f"--property=RuntimeMaxSec={minutes * 60}", "--property=TimeoutStopSec=5s",
                         "--property=KillMode=control-group", "--property=Restart=no", "--collect"):
                self.assertIn(prop, command)
            self.assertIn("--property=InaccessiblePaths=/run/systemd /run/dbus", command)
            self.assertNotIn("enable", command)

    def test_bootstrap_failed_verification_closes_owned_only(self):
        for properties in ({"RuntimeMaxUSec": "infinity"}, {"RuntimeMaxUSec": "1min"},
                           {"TimeoutStopUSec": "6s"}, {"ActiveState": "inactive"},
                           {"InaccessiblePaths": ""}):
            with self.subTest(properties=properties):
                self.command.reset_mock()
                wrong = self.bootstrap_state() | properties
                self.show.side_effect = [wrong, wrong, self.absent()]
                with patch.object(ssh, "_validate_config"), self.assertRaises((ValueError, RuntimeError)):
                    ssh._start_bootstrap("alice", 5)
                self.assertIn(unittest.mock.call("/usr/bin/systemctl", "stop", ssh.BOOTSTRAP_SERVICE), self.command.call_args_list)

    def test_bootstrap_validation_or_start_failure_closes_owned(self):
        for fail_validation in (True, False):
            self.command.reset_mock(side_effect=True)
            self.show.side_effect = [self.bootstrap_state(), self.absent()]
            if not fail_validation:
                self.command.side_effect = lambda *args: (_ for _ in ()).throw(RuntimeError("start")) if args[0] == "/usr/bin/systemd-run" else ""
            with patch.object(ssh, "_validate_config", side_effect=RuntimeError("validate") if fail_validation else None):
                with self.assertRaises(RuntimeError):
                    ssh._start_bootstrap("alice", 5)
            self.assertIn(unittest.mock.call("/usr/bin/systemctl", "stop", ssh.BOOTSTRAP_SERVICE), self.command.call_args_list)

    def test_foreign_bootstrap_never_stopped(self):
        self.show.side_effect = None
        for properties in ({"Transient": "no"}, {"Description": "foreign"},
                           {"ExecStart": "/usr/bin/custom"}, {"User": "alice"}):
            with self.subTest(properties=properties):
                self.show.return_value = self.bootstrap_state() | properties
                with self.assertRaises(ValueError):
                    ssh._close_bootstrap()
                self.assertFalse(any("stop" in call.args for call in self.command.call_args_list))


class PrivilegeTests(unittest.TestCase):
    def test_native_binary_package_ownership_required(self):
        with patch.object(ssh.os, "stat") as metadata, patch.object(ssh, "_secure"), \
                patch.object(ssh, "_command", return_value="foreign"):
            with self.assertRaises(ValueError):
                ssh._native_binaries()
            metadata.assert_called_once_with("/usr/bin/sshd", follow_symlinks=False)

    def test_reject_identity_and_platform_before_mutations(self):
        for euid, cachyos, sudo in ((1000, True, "1000"), (0, False, "1000"), (0, True, "1001")):
            with patch.object(ssh.os, "geteuid", return_value=euid), \
                    patch.object(ssh, "is_cachyos", return_value=cachyos), \
                    patch.object(ssh.platform, "machine", return_value="x86_64"), \
                    patch.object(ssh.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=1000)), \
                    patch.dict(os.environ, {"SUDO_UID": sudo}), \
                    patch.object(ssh, "_root_directory") as directory, \
                    patch.object(ssh, "_command") as command:
                with self.assertRaises(ValueError):
                    ssh.reconcile("alice", "key", 0)
                directory.assert_not_called()
                command.assert_not_called()

    def test_secure_file_rejects_foreign_writable_and_nonregular(self):
        for values in (dict(st_uid=1000), dict(st_gid=1000), dict(st_mode=stat.S_IFREG | 0o622),
                       dict(st_mode=stat.S_IFLNK | 0o600), dict(st_nlink=2)):
            info = SimpleNamespace(**(dict(st_uid=0, st_gid=0, st_mode=stat.S_IFREG | 0o600, st_nlink=1) | values))
            with self.assertRaises(ValueError):
                ssh._secure(info)


if __name__ == "__main__":
    unittest.main()
