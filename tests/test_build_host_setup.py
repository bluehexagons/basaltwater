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
from lib.maintenance_defaults import APT_LOCK_OPTIONS
from lib.build_protocol import DEFAULT_LIMITS, encode_document
from tests.test_build_target_maintenance import root_view


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
        self.assertEqual(setup.SSH_ROOT, "/var/lib/basaltwater-build-host-ssh")
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

    def test_authorized_key_file_reader_requires_exact_public_mode(self) -> None:
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
                os.chmod(path, 0o644)
                self.assertEqual(setup._read_file(
                    str(path), mode=setup.AUTHORIZED_KEYS_MODE, maximum=64 * 1024), content)
                for mode in (0o400, 0o600, 0o640, 0o660, 0o664, 0o666):
                    with self.subTest(mode=oct(mode)):
                        os.chmod(path, mode)
                        with self.assertRaisesRegex(ValueError, "file is unsafe"):
                            setup._read_file(str(path), mode=setup.AUTHORIZED_KEYS_MODE,
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

    def test_marker_namespace_requires_exact_public_lookup_directories(self) -> None:
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

        validate_layout(setup.SSH_ROOT_MODE, setup.SSH_DIRECTORY_MODE)
        for root_mode in (0o700, 0o751):
            with self.subTest(root_mode=root_mode), self.assertRaisesRegex(ValueError, "unsupported mode or owner"):
                validate_layout(root_mode, setup.SSH_DIRECTORY_MODE)
        for ssh_mode in (0o700, 0o775):
            with self.subTest(ssh_mode=ssh_mode), self.assertRaisesRegex(ValueError, ".ssh directory is unsafe"):
                validate_layout(setup.SSH_ROOT_MODE, ssh_mode)

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
                installed.update(package for package in expected if package in args)
                return SimpleNamespace(returncode=0)

            with self.subTest(install_node=install_node), \
                 patch.dict(os.environ, {"DEBIAN_FRONTEND": "existing-value"}), \
                 patch.object(setup, "is_package_installed", side_effect=is_installed), \
                 patch.object(setup, "run", side_effect=install) as run:
                setup._install_packages(setup_config(install_node=install_node))
                self.assertEqual(installed, expected)
                self.assertEqual(os.environ["DEBIAN_FRONTEND"], "existing-value")
                run.assert_called_once()
                argv = run.call_args.args[0]
                self.assertEqual(argv[4:4 + len(APT_LOCK_OPTIONS)], APT_LOCK_OPTIONS)
                self.assertEqual(set(argv[4 + len(APT_LOCK_OPTIONS):]), expected)
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

    def test_current_public_key_is_not_rewritten(self) -> None:
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
        publish.assert_not_called()

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
                                  pw_dir=setup.SSH_ROOT + "/"
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
            _LEGACY_SSH_ROOT=str(tmp_path / "legacy-ssh"),
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
                                DATA_ROOT=str(Path(temp) / "data"), SSH_ROOT=str(Path(temp) / "ssh"),
                                _LEGACY_SSH_ROOT=str(Path(temp) / "legacy-ssh")), \
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
                                _LEGACY_SSH_ROOT=str(root / "legacy-ssh"),
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
                 patch.object(setup, "_LEGACY_SSH_ROOT", str(root / "legacy-ssh")), \
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
                stack.enter_context(patch.object(setup, "_LEGACY_SSH_ROOT", str(root / "legacy-ssh")))
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


class BuildHostTransportIntegrationTests(unittest.TestCase):
    """Exercise installer files/modes with private state and mocked native calls."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.base.chmod(0o711)
        self.users, self.groups = {}, {}
        self.commands = []
        self.store = FakeStore()
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(root_view())
        real_scandir = os.scandir

        class RootEntry:
            def __init__(self, entry):
                self.entry = entry

            def __getattr__(self, name):
                return getattr(self.entry, name)

            def stat(self, *, follow_symlinks=True):
                return os.stat(self.entry.path, follow_symlinks=follow_symlinks)

        @contextmanager
        def root_scandir(path):
            with real_scandir(path) as entries:
                yield iter(RootEntry(entry) for entry in entries)

        stack.enter_context(patch.object(setup.os, "scandir", side_effect=root_scandir))
        paths = {"ETC_DIR": self.base / "etc/build-host",
                 "DATA_ROOT": self.base / "var/lib/basaltwater/build-host",
                 "SSH_ROOT": self.base / "var/lib/basaltwater-build-host-ssh",
                 "_LEGACY_SSH_ROOT": self.base / "var/lib/basaltwater/build-host-ssh",
                 "SUDOERS_DIR": self.base / "etc/sudoers.d",
                 "SERVICE_UNIT": self.base / "etc/systemd" / setup.SERVICE_NAME}
        for name, path in paths.items():
            stack.enter_context(patch.object(setup, name, str(path)))
        stack.enter_context(patch.object(setup, "ROOTS", {
            name: setup.DATA_ROOT + "/" + name for name in setup.ROOTS}))
        stack.enter_context(patch.object(setup, "MARKER_PATH", setup.ETC_DIR + "/.managed.json"))
        stack.enter_context(patch.object(setup, "CONFIG_PATH", setup.ETC_DIR + "/config.json"))
        Path(setup.SERVICE_UNIT).parent.mkdir(parents=True, mode=0o755)
        Path(setup.SUDOERS_DIR).mkdir(mode=0o755)
        # The production helper refuses writable shared ancestors. Bound its
        # recursive creation to this simulated root, outside sticky /tmp.
        ensure_parents = setup._ensure_parent_chain
        stack.enter_context(patch.object(setup, "_ensure_parent_chain", side_effect=lambda path:
                            None if Path(path) == self.base else ensure_parents(path)))
        stack.enter_context(patch("lib.machine_state.can_manage_system_services", return_value=True))
        stack.enter_context(patch.object(setup, "is_dry_run", return_value=False))
        stack.enter_context(patch.object(setup, "_trusted_source_tree"))
        stack.enter_context(patch("common.service_tools.build_host.require_platform"))
        stack.enter_context(patch("common.service_tools.build_host.require_build_account"))
        stack.enter_context(patch.object(setup, "_verify_unit_source"))
        stack.enter_context(patch.object(setup.pwd, "getpwnam", side_effect=lambda name: self.users[name]))
        stack.enter_context(patch.object(setup.grp, "getgrnam", side_effect=lambda name: self.groups[name]))
        stack.enter_context(patch.object(setup.pwd, "getpwall", side_effect=lambda: list(self.users.values())))
        stack.enter_context(patch.object(setup.grp, "getgrall", side_effect=lambda: list(self.groups.values())))
        stack.enter_context(patch.object(setup.os, "getgrouplist", side_effect=lambda _name, gid: [gid]))
        stack.enter_context(patch.object(setup, "is_package_installed", return_value=True))
        self.run = stack.enter_context(patch.object(setup, "run", side_effect=self.native))
        stack.enter_context(patch("lib.build_jobs.BuildJobStore", return_value=self.store))

        def replace(units, *, activate, deactivate, prepare):
            prepare()
            setup._write_file(setup.SERVICE_UNIT, units[setup.SERVICE_NAME].encode(), mode=0o644)

        def remove(_units, *, prepare):
            prepare()
            setup._remove_file(setup.SERVICE_UNIT, mode=0o644, maximum=1024 * 1024)

        stack.enter_context(patch("lib.unit_transaction.replace_units", side_effect=replace))
        stack.enter_context(patch("lib.unit_transaction.remove_units", side_effect=remove))

    def native(self, args, **_kwargs):
        self.commands.append(args)
        if args[0] == "groupadd":
            name = args[-1]
            self.groups[name] = SimpleNamespace(gr_name=name, gr_gid=2000 + len(self.groups), gr_mem=[])
        elif args[0] == "useradd":
            name = args[-1]
            self.users[name] = SimpleNamespace(
                pw_name=name, pw_uid=3000 + len(self.users), pw_gid=self.groups[name].gr_gid,
                pw_dir=args[args.index("--home-dir") + 1], pw_shell=args[args.index("--shell") + 1])
        return SimpleNamespace(returncode=0, stdout=args[-1] + " L today\n" if args[0] == "passwd" else "")

    def snapshot(self):
        return {str(path.relative_to(self.base)): (stat.S_IMODE(path.stat().st_mode),
                                                   path.read_bytes() if path.is_file() else None)
                for path in self.base.rglob("*")}

    def test_private_state_enrollment_refresh_and_disable_preserve_data_and_identities(self) -> None:
        private = Path(setup.DATA_ROOT).parent
        private.mkdir(parents=True, mode=0o700)
        evidence = private / "private-evidence"
        evidence.write_bytes(b"retain")
        setup.configure_build_host(setup_config())
        username = setup._transport_username("alice")
        home = Path(self.users[username].pw_dir)
        auth = home / ".ssh/authorized_keys"
        sudo = Path(setup.SUDOERS_DIR) / username
        for path, mode in ((private, 0o700), (Path(setup.SSH_ROOT), 0o711), (home, 0o555),
                           (auth.parent, 0o755), (auth, 0o644), (sudo, 0o440)):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)
            self.assertEqual((path.stat().st_uid, path.stat().st_gid), (0, 0))
            self.assertFalse(path.stat().st_mode & 0o022)
        self.assertEqual(auth.read_bytes(), setup._authorized_key_line("alice", public_key()))
        before = self.snapshot()
        identities = copy.deepcopy((self.users, self.groups))
        jobs = copy.deepcopy(self.store.state["jobs"])
        # Model the ordinary setup refresh reasserting the private parent mode.
        private.chmod(0o700)
        setup.configure_build_host(setup_config())
        self.assertEqual(self.snapshot(), before)
        setup.configure_build_host(setup_config(disable_build_host=True))
        self.assertFalse(auth.exists())
        self.assertFalse(sudo.exists())
        self.assertEqual((self.users, self.groups), identities)
        self.assertEqual(self.store.state["jobs"], jobs)
        self.assertEqual(evidence.read_bytes(), b"retain")
        self.assertEqual(stat.S_IMODE(private.stat().st_mode), 0o700)
        self.assertTrue(home.is_dir())

    def test_unsafe_or_blocked_ancestors_refuse_before_any_mutation(self) -> None:
        parent = Path(setup.SSH_ROOT).parent
        parent.mkdir(parents=True, mode=0o755)
        real_lstat = Path.lstat
        for mode, uid, gid in ((stat.S_IFDIR | 0o700, 0, 0), (stat.S_IFDIR | 0o775, 0, 0),
                               (stat.S_IFDIR | 0o755, 1234, 0), (stat.S_IFDIR | 0o701, 0, 2001),
                               (stat.S_IFLNK | 0o755, 0, 0)):
            def observe(path):
                if path == parent:
                    return SimpleNamespace(st_mode=mode, st_uid=uid, st_gid=gid)
                return real_lstat(path)

            before = self.snapshot()
            with self.subTest(mode=mode, uid=uid, gid=gid), \
                 patch.object(Path, "lstat", autospec=True, side_effect=observe), \
                 patch.object(setup, "_write_file") as publish, \
                 patch.object(setup.os, "mkdir") as mkdir, \
                 patch.object(setup.os, "fchmod") as chmod, self.assertRaises(ValueError):
                setup.configure_build_host(setup_config())
            publish.assert_not_called()
            mkdir.assert_not_called()
            chmod.assert_not_called()
            self.run.assert_not_called()
            self.assertEqual(self.snapshot(), before)

    def test_new_shared_parents_are_traversable_with_restrictive_umask(self) -> None:
        previous = os.umask(0o077)
        try:
            setup.configure_build_host(setup_config())
        finally:
            os.umask(previous)
        self.assertEqual(stat.S_IMODE(Path(setup.SSH_ROOT).parent.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE(Path(setup.SSH_ROOT).stat().st_mode), 0o711)

    def test_legacy_root_or_marker_refuses_enable_and_disable_without_changes(self) -> None:
        legacy = Path(setup._LEGACY_SSH_ROOT)
        legacy.mkdir(parents=True, mode=0o711)
        (legacy / "evidence").write_bytes(b"retain")
        for disable in (False, True):
            before = self.snapshot()
            with self.assertRaisesRegex(ValueError, "operator reconciliation or a clean reinstall"):
                setup.configure_build_host(setup_config(disable_build_host=disable))
            self.assertEqual(self.snapshot(), before)
            self.run.assert_not_called()
        (legacy / "evidence").unlink()
        legacy.rmdir()
        setup.configure_build_host(setup_config())
        marker = json.loads(Path(setup.MARKER_PATH).read_bytes())
        marker["schema"] = 1
        setup._write_marker(marker)
        before = self.snapshot()
        self.run.reset_mock()
        for disable in (False, True):
            with self.assertRaisesRegex(ValueError, "operator reconciliation or a clean reinstall"):
                setup.configure_build_host(setup_config(disable_build_host=disable))
            self.assertEqual(self.snapshot(), before)
            self.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
