"""Focused installer tests for strict enrollment and managed build-host setup."""

from __future__ import annotations

import base64
import copy
from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import build_host_steps as setup
from lib.build_host_policy import parse_build_host_policy
from lib.build_manifest import DEBIAN_PLATFORM
from lib.build_protocol import DEFAULT_LIMITS, encode_document


def public_key(seed: int = 1) -> str:
    key_bytes = bytes([seed]) * 32

    def ssh_string(value: bytes) -> bytes:
        return len(value).to_bytes(4, "big") + value

    blob = ssh_string(b"ssh-ed25519") + ssh_string(key_bytes)
    return "ssh-ed25519 " + base64.b64encode(blob).decode("ascii")


def grant() -> dict:
    return {
        "platform": dict(DEBIAN_PLATFORM),
        "runtimes": ["python"],
        "limits": dict(DEFAULT_LIMITS),
    }


def enrollment(principals: tuple[str, ...] = ("alice",), *, keys: dict | None = None) -> dict:
    names = tuple(principals)
    return {
        "schema": 1,
        "policy": {
            "schema": 1,
            "principals": {
                name: {"projects": {"site": grant()}}
                for name in names
            },
        },
        "keys": dict(keys or {name: public_key(index + 1) for index, name in enumerate(names)}),
        "max_records": 128,
        "max_bytes": 2 * 1024**3,
    }


def setup_config(**overrides):
    values = {
        "build_host_enrollment": enrollment(),
        "disable_build_host": False,
        "machine_type": "vm",
        "install_go": False,
        "install_node": False,
        "install_python": False,
        "dry_run": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class FakeStore:
    def __init__(self, *, busy: bool = False, running: bool = False) -> None:
        self.state = {
            "draining": False,
            "jobs": {"q" * 32: {"state": "running" if running else "queued"}},
        }
        self.busy = busy
        self.drain_calls: list[bool] = []

    @contextmanager
    def _locked(self):
        yield self.state, -1

    def set_draining(self, draining: bool) -> bool:
        self.drain_calls.append(draining)
        self.state["draining"] = draining
        return draining

    @contextmanager
    def worker(self):
        if self.busy:
            raise RuntimeError("Build worker slot is already leased")
        yield object()


class BuildHostEnrollmentTests(unittest.TestCase):
    def test_mapping_and_json_bytes_normalize_exact_schema(self) -> None:
        source = enrollment(("zeta", "alice"))
        parsed = setup.parse_build_host_enrollment(source)
        self.assertEqual(set(parsed), {"schema", "policy", "keys", "max_records", "max_bytes"})
        self.assertEqual(list(parsed["policy"]["principals"]), ["alice", "zeta"])
        self.assertEqual(list(parsed["keys"]), ["alice", "zeta"])
        self.assertEqual(parsed, setup.parse_build_host_enrollment(json.dumps(source).encode()))
        source["keys"].clear()
        self.assertEqual(set(parsed["keys"]), {"alice", "zeta"})

    def test_enrollment_rejects_ambiguous_fields_principals_and_limits(self) -> None:
        cases = []
        extra = enrollment()
        extra["unexpected"] = True
        cases.append(extra)
        mismatch = enrollment()
        mismatch["keys"] = {"bob": public_key()}
        cases.append(mismatch)
        for principal in ("Alice", "-alice", "alice-", "x" * 33):
            cases.append(enrollment((principal,)))
        bad_bool = enrollment()
        bad_bool["max_records"] = True
        cases.append(bad_bool)
        too_small = enrollment()
        too_small["max_bytes"] = 1
        cases.append(too_small)
        bad_schema = enrollment()
        bad_schema["schema"] = True
        cases.append(bad_schema)

        for item in cases:
            with self.subTest(item=item), self.assertRaises(ValueError):
                setup.parse_build_host_enrollment(item)

    def test_only_canonical_uncommented_ed25519_keys_are_accepted(self) -> None:
        good = public_key()
        bad_values = (
            good + " operator-comment",
            "restrict,command=\"false\" " + good,
            good + "\nssh-ed25519 AAAA",
            "ssh-rsa " + good.split(" ", 1)[1],
            "ssh-ed25519 !!!",
            "ssh-ed25519 " + base64.b64encode(b"wrong wire blob").decode("ascii"),
        )
        for bad in bad_values:
            with self.subTest(key=bad), self.assertRaises(ValueError):
                setup.parse_build_host_enrollment(enrollment(keys={"alice": bad}))

    def test_duplicate_public_key_cannot_be_assigned_to_two_principals(self) -> None:
        duplicate = public_key()
        with self.assertRaisesRegex(ValueError, "multiple principals"):
            setup.parse_build_host_enrollment(
                enrollment(("alice", "bob"), keys={"alice": duplicate, "bob": duplicate})
            )

    def test_transport_name_hash_collision_is_rejected(self) -> None:
        with patch.object(setup, "_transport_username", return_value="bw-build-0123456789ab"), \
             self.assertRaisesRegex(ValueError, "collide on a transport account name"):
            setup.parse_build_host_enrollment(enrollment(("alice", "bob")))

    def test_raw_json_rejects_duplicate_object_keys(self) -> None:
        raw = b'{"schema":1,"schema":1,"policy":{},"keys":{},"max_records":1,"max_bytes":268435456}'
        with self.assertRaises(ValueError):
            setup.parse_build_host_enrollment(raw)

    def test_policy_uses_shared_policy_validation(self) -> None:
        invalid = enrollment()
        invalid["policy"]["principals"]["alice"]["projects"]["site"]["platform"] = {
            "os": "linux", "architecture": "aarch64", "baseline": "debian-13",
        }
        with self.assertRaisesRegex(ValueError, "unsupported build platform"):
            setup.parse_build_host_enrollment(invalid)


class BuildHostSetupUnitTests(unittest.TestCase):
    def test_none_enrollment_is_a_noop_even_if_disable_flag_is_false(self) -> None:
        with patch("lib.machine_state.can_manage_system_services") as capability, \
             patch.object(setup, "run") as run:
            setup.configure_build_host(setup_config(build_host_enrollment=None))
        capability.assert_not_called()
        run.assert_not_called()

    def test_invalid_enrollment_is_rejected_before_capability_or_host_calls(self) -> None:
        invalid = enrollment()
        invalid["keys"]["alice"] += " comment"
        with patch("lib.machine_state.can_manage_system_services") as capability, \
             patch.object(setup, "run") as run, self.assertRaises(ValueError):
            setup.configure_build_host(setup_config(build_host_enrollment=invalid))
        capability.assert_not_called()
        run.assert_not_called()

    def test_dry_run_returns_before_platform_or_mutation(self) -> None:
        with patch("lib.machine_state.can_manage_system_services", return_value=True), \
             patch.object(setup, "is_dry_run", return_value=False), \
             patch("common.service_tools.build_host.require_platform") as platform_check, \
             patch.object(setup, "run") as run:
            setup.configure_build_host(setup_config(dry_run=True))
        platform_check.assert_not_called()
        run.assert_not_called()

    def test_lxc_role_is_rejected_before_source_or_system_mutation(self) -> None:
        with patch("lib.machine_state.can_manage_system_services", return_value=True), \
             patch.object(setup, "is_dry_run", return_value=False), \
             patch.object(setup, "_trusted_source_tree") as source, \
             patch.object(setup, "run") as run, \
             self.assertRaisesRegex(RuntimeError, "VM or physical"):
            setup.configure_build_host(setup_config(machine_type="privileged"))
        source.assert_not_called()
        run.assert_not_called()

    def test_trusted_path_requires_root_owned_ancestors(self) -> None:
        info = {
            "/safe": SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0, st_gid=0),
            "/safe/file": SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=0, st_gid=0),
        }
        with patch.object(setup, "validate_filesystem_path"), \
             patch("os.stat", side_effect=lambda path, **_kwargs: info[str(path)]):
            self.assertEqual(setup._trusted_path("/safe/file").st_uid, 0)
            info["/safe"].st_uid = 12345
            with self.assertRaisesRegex(ValueError, "not controlled by root"):
                setup._trusted_path("/safe/file")

    def test_transport_path_modes_allow_lookup_without_transport_write_access(self) -> None:
        modes = (
            setup.SSH_ROOT_MODE,
            setup.SSH_HOME_MODE,
            setup.SSH_DIRECTORY_MODE,
            setup.AUTHORIZED_KEYS_MODE,
        )
        required_other_access = (0o1, 0o5, 0o5, 0o4)
        self.assertEqual(modes, (0o711, 0o555, 0o755, 0o644))
        for mode, required in zip(modes, required_other_access, strict=True):
            other_bits = mode & 0o7
            self.assertEqual(other_bits & required, required)
            self.assertEqual(mode & 0o022, 0)

    def test_authorized_key_reader_accepts_only_exact_legacy_and_public_modes(self) -> None:
        allowed = setup._ALLOWED_AUTHORIZED_KEYS_MODES
        self.assertEqual(allowed, (0o600, 0o644))
        for mode in allowed:
            self.assertTrue(setup._mode_is_allowed(mode, allowed))
        for mode in (0o400, 0o640, 0o660, 0o664, 0o666):
            with self.subTest(mode=oct(mode)):
                self.assertFalse(setup._mode_is_allowed(mode, allowed))

    def test_authorized_key_file_reader_accepts_only_legacy_and_public_modes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "authorized_keys"
            content = b'restrict,command="fixed" ssh-ed25519 AAAA\n'
            path.write_bytes(content)
            real_fstat = os.fstat

            def as_root_owned(descriptor):
                actual = real_fstat(descriptor)
                return SimpleNamespace(
                    st_mode=actual.st_mode, st_uid=0, st_gid=0, st_nlink=actual.st_nlink,
                    st_size=actual.st_size, st_dev=actual.st_dev, st_ino=actual.st_ino,
                    st_mtime_ns=actual.st_mtime_ns, st_ctime_ns=actual.st_ctime_ns,
                )

            with patch.object(setup, "_trusted_path"), \
                 patch.object(setup.os, "fstat", side_effect=as_root_owned):
                for mode in setup._ALLOWED_AUTHORIZED_KEYS_MODES:
                    with self.subTest(mode=oct(mode)):
                        os.chmod(path, mode)
                        self.assertEqual(setup._read_file(
                            str(path), mode=setup._ALLOWED_AUTHORIZED_KEYS_MODES,
                            maximum=64 * 1024), content)
                os.chmod(path, 0o664)
                with self.assertRaisesRegex(ValueError, "file is unsafe"):
                    setup._read_file(str(path), mode=setup._ALLOWED_AUTHORIZED_KEYS_MODES,
                                     maximum=64 * 1024)

    def test_unmarked_transport_account_namespace_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(setup, "SUDOERS_DIR", temp), \
                 patch.object(setup, "SSH_ROOT", os.path.join(temp, "missing")), \
                 patch.object(setup.pwd, "getpwall",
                              return_value=[SimpleNamespace(pw_name="bw-build-unmarked")]), \
                 patch.object(setup.grp, "getgrall", return_value=[]), \
                 self.assertRaisesRegex(ValueError, "unmarked build-host transport"):
                setup._validate_transport_namespace({"alice"})

    def test_marker_namespace_accepts_only_legacy_or_public_lookup_directories(self) -> None:
        username = setup._transport_username("alice")
        home_name = username.removeprefix("bw-build-")
        ssh_root = "/trusted/build-host-ssh"
        ssh_path = ssh_root + "/" + home_name + "/.ssh"

        class Entry:
            def __init__(self, name: str, info):
                self.name = name
                self.info = info

            def stat(self, *, follow_symlinks: bool):
                return self.info

        class Scan:
            def __init__(self, entries):
                self.entries = entries

            def __enter__(self):
                return iter(self.entries)

            def __exit__(self, *_args):
                return False

        home_entry = Entry(home_name, SimpleNamespace(
            st_mode=stat.S_IFDIR | setup.SSH_HOME_MODE, st_uid=0, st_gid=0))
        key_entry = Entry("authorized_keys", SimpleNamespace(
            st_mode=stat.S_IFREG | setup.AUTHORIZED_KEYS_MODE, st_uid=0, st_gid=0))

        def validate_layout(root_mode: int, ssh_mode: int) -> None:
            def trusted(path: str, **_kwargs):
                mode = root_mode if path == ssh_root else ssh_mode
                return SimpleNamespace(st_mode=stat.S_IFDIR | mode, st_uid=0, st_gid=0)

            def scan(path: str):
                return Scan([home_entry] if path == ssh_root else [key_entry])

            with ExitStack() as stack:
                stack.enter_context(patch.object(setup, "SUDOERS_DIR", "/missing/sudoers.d"))
                stack.enter_context(patch.object(setup, "SSH_ROOT", ssh_root))
                stack.enter_context(patch.object(setup, "_trusted_path", side_effect=trusted))
                stack.enter_context(patch.object(Path, "lstat", autospec=True,
                                                  return_value=SimpleNamespace(
                                                      st_mode=stat.S_IFDIR | root_mode,
                                                      st_uid=0, st_gid=0)))
                stack.enter_context(patch.object(setup.os.path, "lexists",
                                                  side_effect=lambda path: path in {ssh_root, ssh_path}))
                stack.enter_context(patch.object(setup.os, "scandir", side_effect=scan))
                stack.enter_context(patch.object(setup.pwd, "getpwall",
                                                  return_value=[SimpleNamespace(pw_name=username)]))
                stack.enter_context(patch.object(setup.grp, "getgrall",
                                                  return_value=[SimpleNamespace(gr_name=username)]))
                setup._validate_transport_namespace({"alice"})

        for root_mode in setup._ALLOWED_SSH_ROOT_MODES:
            for ssh_mode in setup._ALLOWED_SSH_DIRECTORY_MODES:
                with self.subTest(root_mode=oct(root_mode), ssh_mode=oct(ssh_mode)):
                    validate_layout(root_mode, ssh_mode)
        with self.assertRaisesRegex(ValueError, "unsupported mode or owner"):
            validate_layout(0o751, setup.SSH_DIRECTORY_MODE)
        with self.assertRaisesRegex(ValueError, ".ssh directory is unsafe"):
            validate_layout(setup.SSH_ROOT_MODE, 0o775)

    def test_system_service_capability_skip_does_not_mutate(self) -> None:
        with patch("lib.machine_state.can_manage_system_services", return_value=False), \
             patch.object(setup, "run") as run:
            setup.configure_build_host(setup_config(machine_type="oci"))
        run.assert_not_called()

    def test_node_package_is_selected_only_when_requested(self) -> None:
        for install_node, expected in ((False, set(setup._FIXED_PACKAGES)),
                                       (True, set(setup._FIXED_PACKAGES) | {"nodejs"})):
            installed: set[str] = set()

            def is_installed(package: str) -> bool:
                return package in installed

            def install(args, **_kwargs):
                installed.update(args[4:])
                return SimpleNamespace(returncode=0)

            with self.subTest(install_node=install_node), \
                 patch.dict(os.environ, {"DEBIAN_FRONTEND": "existing-value"}), \
                 patch.object(setup, "is_package_installed", side_effect=is_installed), \
                 patch.object(setup, "run", side_effect=install) as run:
                setup._install_packages(setup_config(install_node=install_node))
                self.assertEqual(installed, expected)
                self.assertEqual(os.environ["DEBIAN_FRONTEND"], "existing-value")
                run.assert_called_once()
                self.assertEqual(set(run.call_args.args[0][4:]), expected)
                self.assertEqual(run.call_args.kwargs["env"]["DEBIAN_FRONTEND"], "noninteractive")

    def test_service_and_ssh_sudo_configuration_are_fixed(self) -> None:
        unit = setup._service_content()
        self.assertIn("ExecStart=/usr/bin/python3 -I /opt/basaltwater/common/service_tools/build_host.py --worker\n", unit)
        self.assertIn("User=root\nGroup=root\n", unit)
        self.assertIn("UMask=0077\nKillMode=control-group\nRestart=no\n", unit)
        self.assertNotIn("Restart=on-failure", unit)

        key = setup._authorized_key_line("alice", public_key())
        self.assertIn(b'restrict,command="/usr/bin/sudo -n /usr/bin/python3 -I ', key)
        self.assertTrue(key.endswith((public_key() + "\n").encode()))
        sudoers = setup._sudoers_content("bw-build-0123456789ab", "alice").decode()
        self.assertIn('Defaults:bw-build-0123456789ab env_keep += "SSH_ORIGINAL_COMMAND"', sudoers)
        self.assertIn("--serve --principal alice\n", sudoers)
        self.assertNotIn("*", sudoers)
        self.assertNotIn("SETENV", sudoers)

    def test_key_revocation_removes_only_the_enrolled_public_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            key = public_key()
            other = b"ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQ== operator-key\n"
            edited_managed_line = (
                b'command="/bin/false" ssh-ed25519 ' + key.split(" ", 1)[1].encode()
                + b" edited-root-comment\r\n"
            )
            record = {"username": "bw-build-0123456789ab", "key": key,
                      "auth_managed": True}
            marker = setup._empty_marker()
            ssh_root = Path(temp) / "ssh"
            ssh_parent = ssh_root / "0123456789ab" / ".ssh"
            ssh_parent.mkdir(parents=True)
            with patch.object(setup, "SSH_ROOT", str(ssh_root)), \
                 patch.object(setup, "_read_root_file", return_value=edited_managed_line + other), \
                 patch.object(setup, "_write_file") as publish, \
                 patch.object(setup, "_write_marker"), \
                 patch.object(setup, "_remove_file"):
                setup._reconcile_authorized_key("alice", record, None, marker)
        publish.assert_called_once()
        self.assertEqual(publish.call_args.args[1], other)

    def test_managed_key_mode_is_upgraded_when_content_is_already_current(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            key = public_key()
            username = "bw-build-0123456789ab"
            ssh_root = Path(temp) / "ssh"
            ssh_dir = ssh_root / "0123456789ab" / ".ssh"
            ssh_dir.mkdir(parents=True)
            expected = setup._authorized_key_line("alice", key)
            record = {"username": username, "key": key, "auth_managed": True}
            with patch.object(setup, "SSH_ROOT", str(ssh_root)), \
                 patch.object(setup, "_read_root_file", return_value=expected), \
                 patch.object(setup, "_write_file") as publish, \
                 patch.object(setup, "_write_marker"):
                setup._reconcile_authorized_key("alice", record, key, setup._empty_marker())
        publish.assert_called_once_with(
            str(ssh_dir / "authorized_keys"), expected, mode=setup.AUTHORIZED_KEYS_MODE)

    def test_sudoers_revocation_requires_and_removes_the_marker_owned_file(self) -> None:
        username, principal = "bw-build-0123456789ab", "alice"
        content = setup._sudoers_content(username, principal)
        record = {"username": username, "key": public_key(), "managed": True,
                  "auth_managed": True, "sudo_managed": True,
                  "sudo_hashes": [hashlib.sha256(content).hexdigest()]}
        marker = setup._empty_marker()
        with tempfile.TemporaryDirectory() as temp:
            Path(temp, username).write_bytes(content)
            sudo_path = os.path.join(temp, username)
            with patch.object(setup, "SUDOERS_DIR", temp), \
                 patch.object(setup, "_read_root_file", return_value=content), \
                 patch.object(setup, "_remove_file") as remove, \
                 patch.object(setup, "_write_marker"):
                setup._reconcile_sudoers(principal, record, None, marker)
        remove.assert_called_once_with(sudo_path,
                                       mode=0o440, maximum=64 * 1024)
        self.assertEqual(record["sudo_hashes"], [])

    def test_transport_account_requires_exclusive_ids_groups_and_locked_password(self) -> None:
        username = setup._transport_username("alice")
        account = SimpleNamespace(pw_name=username, pw_uid=1701, pw_gid=1702,
                                  pw_dir="/var/lib/basaltwater/build-host-ssh/"
                                         + username.removeprefix("bw-build-"),
                                  pw_shell="/bin/sh")
        group = SimpleNamespace(gr_name=username, gr_gid=1702, gr_mem=[])
        with patch.object(setup.pwd, "getpwnam", return_value=account), \
             patch.object(setup.pwd, "getpwall", return_value=[account]), \
             patch.object(setup.grp, "getgrnam", return_value=group), \
             patch.object(setup.grp, "getgrall", return_value=[group]), \
             patch.object(setup.os, "getgrouplist", return_value=[1702]), \
             patch.object(setup, "run", return_value=SimpleNamespace(stdout=f"{username} L 2026-01-01 0 99999 7 -1\n")):
            self.assertEqual(setup._require_exclusive_transport_account(
                username, account.pw_dir, 1701, 1702), (1701, 1702))

        with patch.object(setup.pwd, "getpwnam", return_value=account), \
             patch.object(setup.pwd, "getpwall", return_value=[account]), \
             patch.object(setup.grp, "getgrnam", return_value=group), \
             patch.object(setup.grp, "getgrall", return_value=[group]), \
             patch.object(setup.os, "getgrouplist", return_value=[1702, 1703]), \
             patch.object(setup, "run", return_value=SimpleNamespace(stdout=f"{username} L\n")), \
             self.assertRaisesRegex(ValueError, "supplementary groups"):
            setup._require_exclusive_transport_account(username, account.pw_dir, 1701, 1702)


class BuildHostLifecycleTests(unittest.TestCase):
    def _disable_setup(self, tmp_path: Path, *, store: FakeStore | None = None):
        etc_dir = tmp_path / "etc" / "basaltwater" / "build-host"
        etc_dir.mkdir(parents=True)
        unit_dir = tmp_path / "systemd"
        unit_dir.mkdir()
        unit_file = unit_dir / setup.SERVICE_NAME
        unit_file.write_text("managed unit fixture\n")
        state_root = tmp_path / "state"
        state_root.mkdir()
        data_root = tmp_path / "data"
        data_root.mkdir()
        ssh_root = tmp_path / "ssh"
        ssh_root.mkdir()
        sudoers_dir = tmp_path / "sudoers"
        sudoers_dir.mkdir()
        config_path = etc_dir / "config.json"
        config_bytes = b"managed runtime config\n"
        config_path.write_bytes(config_bytes)
        managed_key = public_key()
        username = setup._transport_username("alice")
        sudo_content = setup._sudoers_content(username, "alice")
        marker = setup._empty_marker()
        marker["worker"] = {"uid": 1600, "gid": 1600}
        marker["principals"]["alice"] = {
            "username": username, "uid": 1701, "gid": 1702, "key": managed_key,
            "managed": True, "auth_managed": True, "sudo_managed": True,
            "sudo_hashes": [hashlib.sha256(sudo_content).hexdigest()],
        }
        marker["files"]["config"] = [hashlib.sha256(config_bytes).hexdigest()]
        marker["files"]["unit"] = [hashlib.sha256(b"managed unit fixture\n").hexdigest()]
        raw_marker = encode_document(marker) + b"\n"
        fake_store = FakeStore() if store is None else store

        constants = patch.multiple(
            setup,
            ETC_DIR=str(etc_dir),
            MARKER_PATH=str(etc_dir / ".managed.json"),
            CONFIG_PATH=str(config_path),
            SERVICE_UNIT=str(unit_file),
            DATA_ROOT=str(data_root),
            SSH_ROOT=str(ssh_root),
            SUDOERS_DIR=str(sudoers_dir),
            ROOTS={"state": str(state_root), "inputs": str(data_root / "inputs"),
                   "scratch": str(data_root / "scratch"), "artifacts": str(data_root / "artifacts"),
                   "logs": str(data_root / "logs")},
        )
        constants.start()

        def read_file(path: str, **_kwargs):
            if path == str(etc_dir / ".managed.json"):
                return raw_marker
            if path == str(config_path):
                return config_bytes
            if path == str(unit_file):
                return b"managed unit fixture\n"
            return None

        reads = patch.object(setup, "_read_file", side_effect=read_file)
        return {
            "etc_dir": etc_dir,
            "unit_file": unit_file,
            "config_path": config_path,
            "marker": marker,
            "store": fake_store,
            "constants": constants,
            "reads": reads,
            "config_bytes": config_bytes,
            "sudo_content": sudo_content,
        }

    def test_explicit_disable_drains_removes_managed_authority_and_retains_queue(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = self._disable_setup(Path(temp))
            fake_store: FakeStore = fixture["store"]
            before = copy.deepcopy(fake_store.state["jobs"])
            marker_writes = []
            fixture["reads"].start()
            try:
                with patch("lib.machine_state.can_manage_system_services", return_value=True), \
                     patch.object(setup, "is_dry_run", return_value=False), \
                     patch.object(setup, "_preflight_disable"), \
                     patch.object(setup, "_verify_unit_source"), \
                     patch("lib.build_jobs.BuildJobStore", return_value=fake_store), \
                     patch.object(setup, "_write_marker", side_effect=lambda value: marker_writes.append(copy.deepcopy(value))), \
                     patch.object(setup, "_remove_file") as remove_file, \
                     patch.object(setup, "_reconcile_sudoers") as revoke_sudo, \
                     patch.object(setup, "_reconcile_authorized_key") as revoke_key, \
                     patch("lib.unit_transaction.remove_units", side_effect=lambda _names, prepare: prepare()) as remove_units:
                    setup.configure_build_host(setup_config(build_host_enrollment=None,
                                                            disable_build_host=True))
            finally:
                fixture["reads"].stop()
                fixture["constants"].stop()

            self.assertEqual(fake_store.drain_calls, [True, True])
            self.assertTrue(fake_store.state["draining"])
            self.assertEqual(fake_store.state["jobs"], before)
            remove_units.assert_called_once_with((setup.SERVICE_NAME,), prepare=unittest.mock.ANY)
            revoke_sudo.assert_called_once()
            revoke_key.assert_called_once()
            self.assertEqual(revoke_key.call_args.kwargs["extra_keys"], ())
            remove_file.assert_called_once_with(str(fixture["config_path"]), mode=0o600,
                                                maximum=setup.MAX_ENROLLMENT_BYTES)
            self.assertEqual(marker_writes[-1]["principals"]["alice"]["key"], None)
            self.assertEqual(marker_writes[-1]["principals"]["alice"]["uid"], 1701)
            self.assertEqual(marker_writes[-1]["files"], {"config": [], "unit": []})
            self.assertTrue(marker_writes[-1]["disabled"])

    def test_busy_disable_retains_drain_and_never_calls_unit_removal(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fake_store = FakeStore(busy=True)
            fixture = self._disable_setup(Path(temp), store=fake_store)
            fixture["reads"].start()
            try:
                with patch("lib.machine_state.can_manage_system_services", return_value=True), \
                     patch.object(setup, "is_dry_run", return_value=False), \
                     patch.object(setup, "_preflight_disable"), \
                     patch.object(setup, "_verify_unit_source"), \
                     patch("lib.build_jobs.BuildJobStore", return_value=fake_store), \
                     patch("lib.unit_transaction.remove_units") as remove_units, \
                     self.assertRaisesRegex(RuntimeError, "drain is retained"):
                    setup.configure_build_host(setup_config(build_host_enrollment=None,
                                                            disable_build_host=True))
            finally:
                fixture["reads"].stop()
                fixture["constants"].stop()
            self.assertEqual(fake_store.drain_calls, [True])
            self.assertTrue(fake_store.state["draining"])
            remove_units.assert_not_called()

    def test_retained_running_claim_refuses_disable_without_removing_unit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fake_store = FakeStore(running=True)
            fixture = self._disable_setup(Path(temp), store=fake_store)
            fixture["reads"].start()
            try:
                with patch("lib.machine_state.can_manage_system_services", return_value=True), \
                     patch.object(setup, "is_dry_run", return_value=False), \
                     patch.object(setup, "_preflight_disable"), \
                     patch.object(setup, "_verify_unit_source"), \
                     patch("lib.build_jobs.BuildJobStore", return_value=fake_store), \
                     patch("lib.unit_transaction.remove_units") as remove_units, \
                     self.assertRaisesRegex(RuntimeError, "retained running claim"):
                    setup.configure_build_host(setup_config(build_host_enrollment=None,
                                                            disable_build_host=True))
            finally:
                fixture["reads"].stop()
                fixture["constants"].stop()
            self.assertTrue(fake_store.state["draining"])
            remove_units.assert_not_called()

    def test_successful_idle_update_retains_ledger_and_managed_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = self._disable_setup(Path(temp))
            store: FakeStore = fixture["store"]
            store.state["draining"] = True
            original_jobs = copy.deepcopy(store.state["jobs"])
            marker_writes = []
            real_lstat = Path.lstat

            def fake_lstat(path):
                if path == fixture["etc_dir"]:
                    return SimpleNamespace(st_uid=0, st_gid=0,
                                           st_mode=stat.S_IFDIR | 0o700)
                return real_lstat(path)

            fixture["reads"].start()
            try:
                with patch("lib.machine_state.can_manage_system_services", return_value=True), \
                     patch.object(setup, "is_dry_run", return_value=False), \
                     patch("common.service_tools.build_host.require_platform"), \
                     patch.object(setup, "_trusted_source_tree"), \
                     patch.object(setup, "_preflight_managed_state"), \
                     patch.object(setup, "_verify_unit_source"), \
                     patch.object(setup, "_trusted_path"), \
                     patch.object(Path, "lstat", autospec=True, side_effect=fake_lstat), \
                     patch.object(setup, "_ensure_runtime_directories"), \
                     patch.object(setup, "_install_packages"), \
                     patch.object(setup, "_ensure_worker_account", return_value=fixture["marker"]["worker"]), \
                     patch.object(setup, "_ensure_transport_account") as ensure_account, \
                     patch.object(setup, "_ensure_directory"), \
                     patch.object(setup, "_reconcile_sudoers") as reconcile_sudo, \
                     patch.object(setup, "_reconcile_authorized_key") as reconcile_key, \
                     patch.object(setup, "_write_file"), \
                     patch.object(setup, "_write_marker",
                                  side_effect=lambda value: marker_writes.append(copy.deepcopy(value))), \
                     patch("lib.build_jobs.BuildJobStore", return_value=store), \
                     patch("lib.unit_transaction.replace_units",
                           side_effect=lambda _units, activate, deactivate, prepare: prepare()) as replace_units:
                    setup.configure_build_host(setup_config())
            finally:
                fixture["reads"].stop()
                fixture["constants"].stop()

            self.assertEqual(store.drain_calls, [True, True])
            self.assertTrue(store.state["draining"])
            self.assertEqual(store.state["jobs"], original_jobs)
            ensure_account.assert_called_once()
            reconcile_sudo.assert_called_once()
            reconcile_key.assert_called_once()
            self.assertEqual(reconcile_sudo.call_args.args[2], public_key())
            self.assertEqual(reconcile_key.call_args.args[2], public_key())
            replace_units.assert_called_once()
            final_marker = marker_writes[-1]
            self.assertEqual(final_marker["worker"], {"uid": 1600, "gid": 1600})
            self.assertEqual(final_marker["principals"]["alice"]["key"], public_key())
            self.assertTrue(final_marker["principals"]["alice"]["sudo_managed"])
            self.assertTrue(final_marker["principals"]["alice"]["auth_managed"])
            self.assertIsNone(final_marker["pending"])
            self.assertFalse(final_marker["disabled"])

    def test_unmanaged_disable_fixture_is_refused_and_left_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            unit = Path(temp) / setup.SERVICE_NAME
            unit.write_text("unmanaged fixture\n")
            with patch("lib.machine_state.can_manage_system_services", return_value=True), \
                 patch.object(setup, "is_dry_run", return_value=False), \
                 patch.multiple(setup, ETC_DIR=str(Path(temp) / "missing"), SERVICE_UNIT=str(unit),
                                DATA_ROOT=str(Path(temp) / "data"), SSH_ROOT=str(Path(temp) / "ssh")), \
                 patch.object(setup, "run") as run, \
                 self.assertRaisesRegex(ValueError, "without their ownership marker"):
                setup.configure_build_host(setup_config(build_host_enrollment=None,
                                                        disable_build_host=True))
            self.assertEqual(unit.read_text(), "unmanaged fixture\n")
            run.assert_not_called()

    def test_unmarked_transport_account_is_refused_during_disable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sudoers = root / "sudoers"
            sudoers.mkdir()
            with patch("lib.machine_state.can_manage_system_services", return_value=True), \
                 patch.object(setup, "is_dry_run", return_value=False), \
                 patch.multiple(setup, ETC_DIR=str(root / "missing"),
                                SERVICE_UNIT=str(root / setup.SERVICE_NAME),
                                DATA_ROOT=str(root / "data"), SSH_ROOT=str(root / "ssh"),
                                SUDOERS_DIR=str(sudoers)), \
                 patch.object(setup.pwd, "getpwnam", side_effect=KeyError), \
                 patch.object(setup.grp, "getgrnam", side_effect=KeyError), \
                 patch.object(setup.pwd, "getpwall",
                              return_value=[SimpleNamespace(pw_name="bw-build-unmarked")]), \
                 patch.object(setup.grp, "getgrall", return_value=[]), \
                 self.assertRaisesRegex(ValueError, "without their ownership marker"):
                setup.configure_build_host(setup_config(build_host_enrollment=None,
                                                        disable_build_host=True))

    def test_busy_enable_restores_drain_before_policy_or_unit_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = FakeStore(busy=True)
            config = setup_config()
            with patch("lib.machine_state.can_manage_system_services", return_value=True), \
                 patch.object(setup, "is_dry_run", return_value=False), \
                 patch.object(setup, "ETC_DIR", str(root / "etc")), \
                 patch.object(setup, "MARKER_PATH", str(root / "etc" / ".managed.json")), \
                 patch("common.service_tools.build_host.require_platform"), \
                 patch.object(setup, "_trusted_source_tree"), \
                 patch.object(setup, "_initial_install_conflicts"), \
                 patch.object(setup, "_preflight_managed_state"), \
                 patch.object(setup, "_verify_unit_source"), \
                 patch.object(setup, "_trusted_path"), \
                 patch.object(setup, "_read_file", return_value=None), \
                 patch.object(setup, "_ensure_directory"), \
                 patch.object(setup, "_write_marker"), \
                 patch.object(setup, "_ensure_runtime_directories"), \
                 patch.object(setup, "_prepare_enrollment") as prepare, \
                 patch.object(setup, "_install_packages") as install_packages, \
                 patch("lib.build_jobs.BuildJobStore", return_value=store), \
                 patch("lib.unit_transaction.replace_units") as replace_units, \
                 self.assertRaisesRegex(RuntimeError, "worker is active"):
                setup.configure_build_host(config)

            self.assertEqual(store.drain_calls, [True, False])
            self.assertFalse(store.state["draining"])
            prepare.assert_not_called()
            install_packages.assert_not_called()
            replace_units.assert_not_called()

    def test_retained_running_claim_stays_drained_without_enable_mutations(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = FakeStore(running=True)
            with ExitStack() as stack:
                stack.enter_context(patch("lib.machine_state.can_manage_system_services", return_value=True))
                stack.enter_context(patch.object(setup, "is_dry_run", return_value=False))
                stack.enter_context(patch.object(setup, "ETC_DIR", str(root / "etc")))
                stack.enter_context(patch.object(setup, "MARKER_PATH", str(root / "etc" / ".managed.json")))
                stack.enter_context(patch("common.service_tools.build_host.require_platform"))
                stack.enter_context(patch.object(setup, "_trusted_source_tree"))
                stack.enter_context(patch.object(setup, "_initial_install_conflicts"))
                stack.enter_context(patch.object(setup, "_preflight_managed_state"))
                stack.enter_context(patch.object(setup, "_verify_unit_source"))
                stack.enter_context(patch.object(setup, "_trusted_path"))
                stack.enter_context(patch.object(setup, "_read_file", return_value=None))
                stack.enter_context(patch.object(setup, "_ensure_directory"))
                stack.enter_context(patch.object(setup, "_write_marker"))
                stack.enter_context(patch.object(setup, "_ensure_runtime_directories"))
                prepare = stack.enter_context(patch.object(setup, "_prepare_enrollment"))
                install_packages = stack.enter_context(patch.object(setup, "_install_packages"))
                ensure_worker = stack.enter_context(patch.object(setup, "_ensure_worker_account"))
                ensure_transport = stack.enter_context(patch.object(setup, "_ensure_transport_account"))
                reconcile_sudo = stack.enter_context(patch.object(setup, "_reconcile_sudoers"))
                reconcile_key = stack.enter_context(patch.object(setup, "_reconcile_authorized_key"))
                write_file = stack.enter_context(patch.object(setup, "_write_file"))
                stack.enter_context(patch("lib.build_jobs.BuildJobStore", return_value=store))
                replace_units = stack.enter_context(patch("lib.unit_transaction.replace_units"))
                with self.assertRaisesRegex(RuntimeError, "retained running claim"):
                    setup.configure_build_host(setup_config())

            self.assertEqual(store.drain_calls, [True])
            self.assertTrue(store.state["draining"])
            prepare.assert_not_called()
            install_packages.assert_not_called()
            ensure_worker.assert_not_called()
            ensure_transport.assert_not_called()
            reconcile_sudo.assert_not_called()
            reconcile_key.assert_not_called()
            write_file.assert_not_called()
            replace_units.assert_not_called()


if __name__ == "__main__":
    unittest.main()
