"""Offline SSH lifecycle contracts; no host service or package operations."""

from __future__ import annotations

from contextlib import ExitStack
import gzip
import hashlib
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
                    patch.object(ssh, "inspect_setup") as inspect, \
                    patch.object(wrapper, "is_dry_run", return_value=False), \
                    patch.object(wrapper, "can_manage_system_services", return_value=True):
                wrapper.configure(self.config(**overrides))
                self.assertEqual(run.call_args_list[0].args[0], ["sudo", "-v"])
                command = run.call_args.args[0]
                self.assertEqual(command[:4], ["sudo", "-n", "/usr/bin/python3", "-I"])
                self.assertTrue(Path(command[4]).is_absolute())
                self.assertEqual(command[-3:], ["alice", mode, "0"])
                inspect.assert_called_once_with("alice", mode, 0, metadata_only=True)

    def test_preflight_refusal_precedes_sudo(self):
        with patch.object(wrapper, "run") as run, \
                patch.object(wrapper, "is_dry_run", return_value=False), \
                patch.object(wrapper, "can_manage_system_services", return_value=True), \
                patch.object(ssh, "inspect_setup", side_effect=ValueError("enroll a public key")):
            with self.assertRaisesRegex(ValueError, "enroll a public key"):
                wrapper.configure(self.config())
            run.assert_not_called()

    def test_preflight_is_available_without_configuring(self):
        with patch.object(wrapper, "run") as run, \
                patch.object(wrapper, "is_dry_run", return_value=False), \
                patch.object(wrapper, "can_manage_system_services", return_value=True), \
                patch.object(ssh, "inspect_setup") as inspect:
            wrapper.preflight(self.config(install_ssh=False))
            inspect.assert_called_once_with("alice", "off", 0, metadata_only=True)
            run.assert_not_called()

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
        self.vendor_dir = Path(self.directory) / "vendor"
        self.vendor_dir.mkdir()
        self.stack.enter_context(patch.object(ssh, "CONFIG_DIR", self.config_dir))
        self.stack.enter_context(patch.object(ssh, "UNIT_DIR", self.unit_dir))
        self.stack.enter_context(patch.object(ssh, "VENDOR_DIR", self.vendor_dir))
        self.stack.enter_context(patch.object(ssh, "_unit_directories", return_value=(self.unit_dir, self.vendor_dir)))
        self.stack.enter_context(patch.object(ssh.pwd, "getpwnam",
            return_value=SimpleNamespace(pw_uid=1000, pw_dir=str(Path(self.directory) / "home"))))
        # Temporary fixtures belong to the test runner, never to the host's root.
        self.stack.enter_context(patch.object(ssh, "_secure"))
        self.stack.enter_context(patch("lib.atomic_io.os.fchown"))
        self.command = self.stack.enter_context(patch.object(ssh, "_command", return_value=""))
        self.show = self.stack.enter_context(patch.object(ssh, "_show", side_effect=lambda _: self.absent()))
        self.replace = self.stack.enter_context(patch.object(ssh, "replace_units"))
        self.remove = self.stack.enter_context(patch.object(ssh, "remove_units"))
        self.stack.enter_context(patch.object(ssh, "_native_binaries"))
        self.package = self.stack.enter_context(patch.object(ssh, "_packaged_file"))
        self.key = self.stack.enter_context(patch.object(ssh, "_authorized_key", return_value=True))
        self.run = self.stack.enter_context(patch.object(ssh, "run"))

    @staticmethod
    def absent():
        return dict(LoadState="not-found", ActiveState="inactive", UnitFileState="",
                    DropInPaths="", MainPID="0")

    def main_state(self, mode="key"):
        path = ssh._immutable_config("alice", mode)
        (self.unit_dir / ssh.SERVICE).write_text(ssh._unit(path))
        return self.absent() | dict(LoadState="loaded", Transient="no", ActiveState="active",
                                   UnitFileState="enabled", KillMode="process",
                                   User="root", Description=ssh.DESCRIPTION, Restart="on-failure",
                                   ExecStart=f"{{ path=/usr/bin/sshd ; argv[]=/usr/bin/sshd -D -e -f {path} ; ignore_errors=no ; }}",
                                   FragmentPath=str(self.unit_dir / ssh.SERVICE), MainPID="123")

    def native_fixture(self, **fields):
        self.states = {"sshd.service": self.absent() | dict(LoadState="loaded", Transient="no",
            ActiveState="active", UnitFileState="enabled", KillMode="process", User="",
            FragmentPath=str(self.vendor_dir / "sshd.service"), MainPID="456",
            ExecStart="{ path=/usr/bin/sshd ; argv[]=/usr/bin/sshd -D ; ignore_errors=no ; }") | fields}
        self.show.side_effect = lambda unit: self.states.get(unit, self.absent()).copy()

        def command(*args):
            if args[0] == "/usr/bin/systemctl":
                state = self.states[args[-1]]
                if args[1] in {"start", "stop"}:
                    state["ActiveState"] = "active" if args[1] == "start" else "inactive"
                elif args[1] in {"enable", "disable"}:
                    state["UnitFileState"] = ("enabled-runtime" if "--runtime" in args else "enabled") if args[1] == "enable" else "disabled"
            return ""
        self.command.side_effect = command

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

    def test_native_adoption_validates_before_stop_and_preserves_sessions(self):
        self.native_fixture()
        events = []
        command = self.command.side_effect
        self.command.side_effect = lambda *args: (events.append(args), command(*args))[1]
        with patch.object(ssh, "_validate_config", side_effect=lambda *args: events.append(("validated",))):
            ssh._reconcile("alice", "key", 0)
        self.assertLess(events.index(("validated",)), events.index(("/usr/bin/systemctl", "stop", "sshd.service")))
        self.assertEqual(self.states["sshd.service"]["ActiveState"], "inactive")
        self.assertEqual(self.states["sshd.service"]["UnitFileState"], "disabled")
        self.package.assert_called_once_with(self.vendor_dir / "sshd.service")
        self.key.assert_called_once_with("alice")
        self.assertIn("KillMode=process\n", self.replace.call_args.args[0][ssh.SERVICE])
        self.assertFalse(any("kill" in call.args for call in self.command.call_args_list))

    def test_adoption_validation_failure_never_disables_native(self):
        self.native_fixture()
        with patch.object(ssh, "_validate_config", side_effect=RuntimeError("invalid")):
            with self.assertRaisesRegex(RuntimeError, "invalid"):
                ssh._reconcile("alice", "key", 0)
        self.assertFalse(any("stop" in call.args or "disable" in call.args for call in self.command.call_args_list))
        self.replace.assert_not_called()

    def test_password_only_adoption_refused_readonly_in_both_contexts(self):
        self.native_fixture()
        self.key.return_value = False
        for metadata_only in (True, False):
            with self.subTest(metadata_only=metadata_only), self.assertRaisesRegex(ValueError, "ssh-copy-id"):
                ssh.inspect_setup("alice", "key", 0, metadata_only=metadata_only)
        self.replace.assert_not_called()
        self.remove.assert_not_called()
        self.assertFalse(any("stop" in call.args or "disable" in call.args for call in self.command.call_args_list))

    def test_explicit_password_or_window_allows_adoption_without_key(self):
        self.native_fixture()
        self.key.return_value = False
        for mode, minutes in (("password", 0), ("key", 5)):
            with self.subTest(mode=mode):
                ssh.inspect_setup("alice", mode, minutes, metadata_only=True)
        self.key.assert_not_called()

    def test_fresh_key_only_and_absent_off_are_noops_for_native(self):
        self.key.return_value = False
        ssh.inspect_setup("alice", "key", 0, metadata_only=True)
        ssh._reconcile("alice", "off", 0)
        self.replace.assert_not_called()
        self.remove.assert_not_called()
        self.assertEqual(self.command.call_args_list, [
            unittest.mock.call("/usr/bin/ss", "-H", "-ltnp", "sport", "=", ":22")
        ] * 2)

    def test_off_disables_native_active_or_enabled_including_runtime(self):
        for active, enabled in (("active", "disabled"), ("inactive", "enabled"),
                                ("active", "enabled-runtime"), ("inactive", "disabled")):
            with self.subTest(active=active, enabled=enabled):
                self.native_fixture(ActiveState=active, UnitFileState=enabled)
                self.command.reset_mock()
                ssh._reconcile("alice", "off", 0)
                calls = self.command.call_args_list
                self.assertEqual(unittest.mock.call("/usr/bin/systemctl", "stop", "sshd.service") in calls,
                                 active == "active")
                if enabled == "enabled-runtime":
                    self.assertIn(unittest.mock.call("/usr/bin/systemctl", "disable", "--runtime", "sshd.service"), calls)
                self.assertEqual(self.states["sshd.service"]["ActiveState"], "inactive")
                self.assertEqual(self.states["sshd.service"]["UnitFileState"], "disabled")
        self.key.assert_not_called()
        self.replace.assert_not_called()

    def test_foreign_or_unsafe_native_never_stopped_even_when_off(self):
        for fields in ({"FragmentPath": str(self.unit_dir / "sshd.service")},
                       {"Transient": "yes"}, {"KillMode": "control-group"},
                       {"User": "alice"}, {"ExecStart": "/usr/bin/custom"},
                       {"DropInPaths": "/usr/lib/systemd/system/service.d/custom.conf"},
                       {"ActiveState": "activating"}, {"ActiveState": "unknown"},
                       {"UnitFileState": "masked"}, {"UnitFileState": "mystery"},
                       {"UnitFileState": ""}):
            self.native_fixture(**fields)
            for mode in ("key", "off"):
                with self.subTest(fields=fields, mode=mode), self.assertRaises(ValueError):
                    ssh._reconcile("alice", mode, 0)
        self.replace.assert_not_called()
        self.remove.assert_not_called()
        self.assertFalse(any("stop" in call.args or "disable" in call.args for call in self.command.call_args_list))

    def test_foreign_package_and_generic_dropin_directory_refused(self):
        self.native_fixture()
        self.package.side_effect = ValueError("foreign package")
        with self.assertRaisesRegex(ValueError, "foreign package"):
            ssh._reconcile("alice", "off", 0)
        self.package.side_effect = None
        (self.vendor_dir / "service.d").mkdir()
        with self.assertRaisesRegex(ValueError, "drop-in"):
            ssh._reconcile("alice", "off", 0)
        self.command.assert_not_called()

    def test_shadow_unit_and_legacy_foreign_alias_refused(self):
        self.native_fixture()
        (self.unit_dir / "sshd.service").write_text("custom")
        with self.assertRaisesRegex(ValueError, "Foreign native"):
            ssh._reconcile("alice", "off", 0)
        (self.unit_dir / "sshd.service").unlink()
        self.states["ssh.service"] = self.states["sshd.service"].copy()
        with self.assertRaisesRegex(ValueError, "Foreign native"):
            ssh._reconcile("alice", "off", 0)
        self.command.assert_not_called()

    def test_failed_activation_restores_native_activity_and_enablement(self):
        for active, enabled in (("active", "enabled"), ("active", "enabled-runtime"),
                                ("active", "disabled"), ("inactive", "enabled")):
            with self.subTest(active=active, enabled=enabled):
                self.native_fixture(ActiveState=active, UnitFileState=enabled)
                self.command.reset_mock()
                self.replace.side_effect = RuntimeError("activation failed")
                with patch.object(ssh, "_validate_config"), self.assertRaisesRegex(RuntimeError, "activation failed"):
                    ssh._reconcile("alice", "key", 0)
                self.assertEqual(self.states["sshd.service"]["ActiveState"], active)
                self.assertEqual(self.states["sshd.service"]["UnitFileState"], enabled)
                if enabled == "enabled-runtime":
                    self.assertIn(unittest.mock.call("/usr/bin/systemctl", "enable", "--runtime", "sshd.service"),
                                  self.command.call_args_list)

    def test_partial_disable_failure_restores_native(self):
        self.native_fixture()
        command = self.command.side_effect
        def fail(*args):
            if "disable" in args:
                raise RuntimeError("disable failed")
            return command(*args)
        self.command.side_effect = fail
        with patch.object(ssh, "_validate_config"), self.assertRaisesRegex(RuntimeError, "disable failed"):
            ssh._reconcile("alice", "key", 0)
        self.assertEqual(self.states["sshd.service"]["ActiveState"], "active")
        self.assertEqual(self.states["sshd.service"]["UnitFileState"], "enabled")
        self.replace.assert_not_called()

    def test_restore_failure_retains_original_and_actionable_commands(self):
        self.native_fixture()
        command = self.command.side_effect
        def fail(*args):
            if "start" in args:
                raise RuntimeError("bus unavailable")
            return command(*args)
        self.command.side_effect = fail
        self.replace.side_effect = RuntimeError("managed activation failed")
        with patch.object(ssh, "_validate_config"), self.assertRaisesRegex(RuntimeError, "manual recovery") as caught:
            ssh._reconcile("alice", "key", 0)
        self.assertIn("systemctl start sshd.service: bus unavailable", str(caught.exception))
        self.assertIn("managed activation failed", str(caught.exception))
        self.assertIs(caught.exception.__cause__, self.replace.side_effect)

    def test_packaged_socket_stops_first_and_restores_after_daemon(self):
        self.native_fixture()
        self.states["sshd.socket"] = self.absent() | dict(LoadState="loaded", Transient="no",
            ActiveState="active", UnitFileState="enabled", Accept="no", Triggers="sshd.service",
            FragmentPath=str(self.vendor_dir / "sshd.socket"))
        self.replace.side_effect = RuntimeError("activation failed")
        with patch.object(ssh, "_validate_config"), self.assertRaises(RuntimeError):
            ssh._reconcile("alice", "key", 0)
        calls = self.command.call_args_list
        self.assertLess(calls.index(unittest.mock.call("/usr/bin/systemctl", "stop", "sshd.socket")),
                        calls.index(unittest.mock.call("/usr/bin/systemctl", "stop", "sshd.service")))
        self.assertLess(calls.index(unittest.mock.call("/usr/bin/systemctl", "start", "sshd.service")),
                        calls.index(unittest.mock.call("/usr/bin/systemctl", "start", "sshd.socket")))
        self.states["sshd.socket"]["Accept"] = "yes"
        with self.assertRaisesRegex(ValueError, "Accept=no"):
            ssh.inspect_setup("alice", "off", 0)

    def test_unrelated_listener_refused_before_validation_or_stop(self):
        self.native_fixture()
        self.command.side_effect = None
        self.command.return_value = 'LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("foreign",pid=999,fd=3))'
        for mode in ("key", "off"):
            with patch.object(ssh, "_validate_config") as validate, self.assertRaisesRegex(ValueError, "occupied"):
                ssh._reconcile("alice", mode, 0)
        validate.assert_not_called()
        self.assertFalse(any("stop" in call.args for call in self.command.call_args_list))

    def test_unprivileged_probe_tolerates_hidden_pids_only_with_known_daemon(self):
        self.native_fixture()
        self.command.side_effect = None
        self.command.return_value = "LISTEN 0 128 0.0.0.0:22 0.0.0.0:*"
        ssh.inspect_setup("alice", "key", 0, metadata_only=True)
        with self.assertRaisesRegex(ValueError, "occupied"):
            ssh.inspect_setup("alice", "key", 0)
        self.states.clear()
        with self.assertRaisesRegex(ValueError, "occupied"):
            ssh.inspect_setup("alice", "key", 0, metadata_only=True)

    def test_unchanged_active_main_is_never_restarted(self):
        main = self.main_state()
        self.show.side_effect = lambda unit: main.copy() if unit == ssh.SERVICE else self.absent()
        with patch.object(ssh, "_validate_config"):
            ssh._reconcile("alice", "key", 0)
        self.replace.assert_not_called()
        self.assertFalse(any("stop" in call.args or "restart" in call.args for call in self.command.call_args_list))

    def test_legacy_main_migrates_safety_without_restart_before_removal(self):
        main = self.main_state() | {"KillMode": "control-group"}
        path = ssh._config_path("alice", "key")
        (self.unit_dir / ssh.SERVICE).write_text(ssh._unit(path, legacy=True))
        self.show.side_effect = lambda unit: main.copy() if unit == ssh.SERVICE else self.absent()
        def migrate(units, *, activate, unit_dir):
            self.assertEqual(activate, ())
            (self.unit_dir / ssh.SERVICE).write_text(units[ssh.SERVICE])
            main["KillMode"] = "process"
        self.replace.side_effect = migrate
        ssh._reconcile("alice", "off", 0)
        self.replace.assert_called_once_with({ssh.SERVICE: ssh._unit(path)}, activate=(), unit_dir=str(self.unit_dir))
        self.remove.assert_called_once()
        self.command.assert_called_once_with("/usr/bin/ss", "-H", "-ltnp", "sport", "=", ":22")

    def test_legacy_unchanged_refresh_only_reloads_session_safe_definition(self):
        main = self.main_state() | {"KillMode": "control-group"}
        path = ssh._config_path("alice", "key")
        (self.unit_dir / ssh.SERVICE).write_text(ssh._unit(path, legacy=True))
        self.show.side_effect = lambda unit: main.copy() if unit == ssh.SERVICE else self.absent()
        def migrate(units, *, activate, unit_dir):
            self.assertEqual(activate, ())
            (self.unit_dir / ssh.SERVICE).write_text(units[ssh.SERVICE])
            main["KillMode"] = "process"
        self.replace.side_effect = migrate
        with patch.object(ssh, "_validate_config"):
            ssh._reconcile("alice", "key", 0)
        self.replace.assert_called_once_with({ssh.SERVICE: ssh._unit(path)}, activate=(), unit_dir=str(self.unit_dir))
        self.assertFalse(any("stop" in call.args or "restart" in call.args for call in self.command.call_args_list))
        self.remove.assert_not_called()

    def test_unverified_main_killmode_prevents_removal(self):
        main = self.main_state() | {"KillMode": "control-group"}
        self.show.side_effect = lambda unit: main.copy() if unit == ssh.SERVICE else self.absent()
        with self.assertRaisesRegex(RuntimeError, "session-preserving KillMode"):
            ssh._reconcile("alice", "off", 0)
        self.remove.assert_not_called()

    def test_window_started_before_native_stop_and_closed_on_failure(self):
        self.native_fixture()
        events = []
        command = self.command.side_effect
        self.command.side_effect = lambda *args: (events.append(args), command(*args))[1]
        self.replace.side_effect = RuntimeError("activation failed")
        with patch.object(ssh, "_validate_config"), \
                patch.object(ssh, "_start_bootstrap", side_effect=lambda *args: events.append(("window",))), \
                patch.object(ssh, "_close_bootstrap") as close, self.assertRaises(RuntimeError):
            ssh._reconcile("alice", "key", 5)
        self.assertLess(events.index(("window",)), events.index(("/usr/bin/systemctl", "stop", "sshd.service")))
        self.assertEqual(close.call_count, 2)
        self.assertEqual(self.states["sshd.service"]["ActiveState"], "active")

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
        self.show.side_effect = [active, active, self.absent()]
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
                patch.object(ssh, "_root_directory"), \
                patch.object(ssh, "_command", return_value="foreign"):
            with self.assertRaises(ValueError):
                ssh._native_binaries()
            metadata.assert_called_once_with("/usr/bin/sshd", follow_symlinks=False)

    def test_reject_identity_and_platform_before_mutations(self):
        for euid, cachyos, sudo, capable in ((1000, True, "1000", True), (0, False, "1000", True),
                                           (0, True, "1001", True), (0, True, "1000", False)):
            with patch.object(ssh.os, "geteuid", return_value=euid), \
                    patch.object(ssh, "is_cachyos", return_value=cachyos), \
                    patch.object(ssh, "can_manage_system_services", return_value=capable), \
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

    def test_authorized_key_metadata_rejects_unsafe_owners_modes_and_kinds(self):
        for directory in (True, False):
            mode = (stat.S_IFDIR if directory else stat.S_IFREG) | 0o700
            for values in (dict(st_uid=1001), dict(st_mode=mode | 0o020),
                           dict(st_mode=mode | 0o002), dict(st_mode=stat.S_IFLNK | 0o600),
                           {} if directory else dict(st_nlink=2)):
                if not values:
                    continue
                info = SimpleNamespace(**(dict(st_uid=1000, st_mode=mode, st_nlink=1) | values))
                with self.subTest(directory=directory, values=values), self.assertRaises(ValueError):
                    ssh._key_metadata(info, 1000, directory=directory)
            for uid in (0, 1000):
                ssh._key_metadata(SimpleNamespace(st_uid=uid, st_mode=mode, st_nlink=1),
                                  1000, directory=directory)


class EnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.home = Path(self.directory) / "home"
        self.ssh_dir = self.home / ".ssh"
        self.ssh_dir.mkdir(parents=True)
        self.keys = self.ssh_dir / "authorized_keys"
        self.stack.enter_context(patch.object(ssh.pwd, "getpwnam",
            return_value=SimpleNamespace(pw_uid=1000, pw_dir=str(self.home))))
        # Only these temporary paths are opened; ownership/mode decisions have
        # separate metadata tests because /tmp is intentionally world-writable.
        self.metadata = self.stack.enter_context(patch.object(ssh, "_key_metadata"))
        self.run = self.stack.enter_context(patch.object(ssh, "run",
            return_value=SimpleNamespace(returncode=0, stdout="256 SHA256:fixture comment (ED25519)\n")))

    def test_valid_public_key_is_validated_natively_without_output(self):
        self.keys.write_text('# comment\nrestrict,from="10.0.0.0/8" ssh-ed25519 AAAA fixture\n')
        self.assertTrue(ssh._authorized_key("alice"))
        self.assertEqual(self.run.call_args.args[0], ["/usr/bin/ssh-keygen", "-lf", "-"])
        self.assertEqual(self.run.call_args.kwargs["input_data"],
                         'restrict,from="10.0.0.0/8" ssh-ed25519 AAAA fixture\n')
        self.assertTrue(self.run.call_args.kwargs["capture_output"])
        self.assertFalse(self.run.call_args.kwargs["check"])

    def test_native_parser_rejects_plausible_but_invalid_wire_key(self):
        self.keys.write_text("ssh-ed25519 AAAA invalid\n")
        self.run.return_value = SimpleNamespace(returncode=255, stdout="")
        self.assertFalse(ssh._authorized_key("alice"))

    def test_known_hosts_unknown_options_and_malformed_options_do_not_count(self):
        for line in ('example.com ssh-ed25519 AAAA comment',
                     'unknown="value" ssh-ed25519 AAAA comment',
                     'from="unclosed ssh-ed25519 AAAA comment',
                     'restrict=foo ssh-ed25519 AAAA comment',
                     '# ssh-ed25519 AAAA comment', 'ssh-ed25519 AAAA ' + 'x' * 8192):
            with self.subTest(line=line[:40]):
                self.keys.write_text(line + "\n")
                self.assertFalse(ssh._authorized_key("alice"))
        self.run.assert_not_called()

    def test_options_allow_spaces_and_commas_within_quoted_commands(self):
        line = 'command="echo a,b \\"quoted\\"",restrict ssh-ed25519 AAAA comment'
        self.keys.write_text(line + "\n")
        self.assertTrue(ssh._authorized_key("alice"))
        self.assertEqual(self.run.call_args.kwargs["input_data"], line + "\n")

    def test_missing_empty_comment_only_and_nonpublic_content_not_enrolled(self):
        self.assertFalse(ssh._authorized_key("alice"))
        for content in ("", "# ssh-ed25519 AAAA comment\n", "not a public key\n",
                        "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----\n"):
            with self.subTest(content=content):
                self.keys.write_text(content)
                self.assertFalse(ssh._authorized_key("alice"))
        self.run.assert_not_called()

    def test_authorized_keys_symlink_or_symlink_parent_refused(self):
        target = self.home / "public"
        target.write_text("ssh-ed25519 AAAA fixture\n")
        self.keys.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "safely inspect"):
            ssh._authorized_key("alice")
        self.keys.unlink()
        self.ssh_dir.rmdir()
        self.ssh_dir.symlink_to(self.home)
        with self.assertRaisesRegex(ValueError, "safely inspect"):
            ssh._authorized_key("alice")
        self.run.assert_not_called()

    def test_unsafe_parent_prevents_reading_key(self):
        self.keys.write_text("ssh-ed25519 AAAA fixture\n")
        self.metadata.side_effect = ValueError("foreign writable parent")
        with self.assertRaisesRegex(ValueError, "foreign writable"):
            ssh._authorized_key("alice")
        self.run.assert_not_called()

    def test_oversized_or_nonregular_authorized_keys_refused(self):
        self.keys.write_text("x" * (1024 * 1024 + 1))
        with self.assertRaisesRegex(ValueError, "1 MiB"):
            ssh._authorized_key("alice")
        self.keys.unlink()
        self.keys.mkdir()
        with self.assertRaisesRegex(ValueError, "safely inspect"):
            ssh._authorized_key("alice")
        self.run.assert_not_called()


class PackageFileTests(unittest.TestCase):
    def test_native_unit_file_package_and_integrity_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sshd.service"
            path.write_text("[Service]\nExecStart=/usr/bin/sshd -D\nKillMode=process\n")
            metadata = Path(directory) / "openssh-10.0p1-1"
            metadata.mkdir()
            checksum = hashlib.sha256(path.read_bytes()).hexdigest()
            manifest = f"./usr/lib/systemd/system/sshd.service type=file sha256digest={checksum}\n"
            with gzip.open(metadata / "mtree", "wb") as stream:
                stream.write(manifest.encode())
            with patch.object(ssh, "_root_directory"), patch.object(ssh, "_secure"), \
                    patch.object(ssh, "PACKAGE_DB", Path(directory)), \
                    patch.object(ssh, "_command", side_effect=lambda *args:
                                 "openssh\n" if "-Qqo" in args else "openssh 10.0p1-1\n") as command, \
                    patch.object(ssh, "run") as run:
                ssh._packaged_file(path)
                self.assertEqual(command.call_args_list, [
                    unittest.mock.call("/usr/bin/pacman", "-Qqo", "--", str(path)),
                    unittest.mock.call("/usr/bin/pacman", "-Q", "--", "openssh")])
                path.write_text(path.read_text().replace("-D", "-e"))  # Same size is still refused.
                with self.assertRaisesRegex(ValueError, "Modified stock"):
                    ssh._packaged_file(path)
                (metadata / "mtree").write_bytes(b"corrupt manifest")
                with self.assertRaisesRegex(ValueError, "Cannot verify"):
                    ssh._packaged_file(path)
                command.side_effect = None
                command.return_value = "foreign-package\n"
                with self.assertRaisesRegex(ValueError, "Foreign SSH package"):
                    ssh._packaged_file(path)
                run.assert_not_called()

    def test_native_unit_symlink_is_never_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sshd.service"
            target = Path(directory) / "custom"
            target.write_text("custom")
            path.symlink_to(target)
            with patch.object(ssh, "_root_directory"), patch.object(ssh, "run") as run:
                with self.assertRaises(OSError):
                    ssh._packaged_file(path)
                run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
