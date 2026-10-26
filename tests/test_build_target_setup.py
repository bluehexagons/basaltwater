"""Managed target enrollment without native packages, identities or artifacts."""

from __future__ import annotations

import base64
from contextlib import ExitStack
import copy
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import build_target_steps as setup
from lib import build_target_maintenance as maintenance
from tests.test_build_target_entrypoint import target_policy
from tests.test_build_target_maintenance import root_view


def public_key(seed: int = 1) -> str:
    wire = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + bytes([seed]) * 32
    return "ssh-ed25519 " + base64.b64encode(wire).decode("ascii")


def enrollment(principals: tuple[str, ...] = ("alice",)) -> dict:
    policy = target_policy(principal=principals[0])
    policy["applications"]["shop"]["principals"] = list(principals)
    return {"schema": 1, "policy": policy,
            "keys": {name: public_key(index + 1) for index, name in enumerate(principals)},
            "max_records": 128, "max_bytes": 2 * 1024**3}


def configuration(**overrides):
    values = {"build_target_enrollment": enrollment(), "disable_build_target": False,
              "dry_run": False, "machine_type": "vm"}
    values.update(overrides)
    return SimpleNamespace(**values)


class BuildTargetEnrollmentTests(unittest.TestCase):
    def test_strict_detached_schema_and_union_of_principals(self) -> None:
        original = enrollment(("zeta", "alice"))
        second = copy.deepcopy(original["policy"]["applications"]["shop"])
        second.update(deployment_root="/srv/apps/blog", project="blog-source", principals=["bob"])
        second["components"]["web"]["domain"] = "blog.example"
        original["policy"]["applications"]["blog"] = second
        original["keys"]["bob"] = public_key(3)
        parsed = setup.parse_build_target_enrollment(original)
        self.assertEqual(set(parsed), {"schema", "policy", "keys", "max_records", "max_bytes"})
        self.assertEqual(list(parsed["keys"]), ["alice", "bob", "zeta"])
        self.assertEqual(parsed, setup.parse_build_target_enrollment(json.dumps(original).encode()))
        original["keys"].clear()
        original["policy"]["applications"].clear()
        self.assertEqual(len(parsed["keys"]), 3)
        self.assertEqual(len(parsed["policy"]["applications"]), 2)

    def test_schema_extra_missing_private_duplicate_and_noncanonical_keys_rejected(self) -> None:
        cases = []
        for field, value in (("schema", True), ("schema", 2), ("extra", 1),
                             ("max_records", True), ("max_records", 0), ("max_records", 4097),
                             ("max_bytes", 1), ("max_bytes", 8 * 1024**4 + 1),
                             ("keys", {}), ("keys", {"bob": public_key()})):
            item = enrollment()
            item[field] = value
            cases.append(item)
        for key in ("-----BEGIN OPENSSH PRIVATE KEY-----", public_key() + " comment",
                    public_key() + "\n", 'restrict ' + public_key(), "ssh-ed25519 !!!",
                    "ssh-ed25519 " + base64.b64encode(b"bad").decode(),
                    "ssh-rsa " + public_key().split()[1], public_key()[:-1],
                    public_key()[:-3] + "B=="):
            item = enrollment()
            item["keys"]["alice"] = key
            cases.append(item)
        for principal in ("Alice", "alice-", "-alice", "alice@example", "x" * 33):
            cases.append(enrollment((principal,)))
        duplicate = enrollment(("alice", "bob"))
        duplicate["keys"]["bob"] = public_key()
        cases.append(duplicate)
        for item in cases:
            with self.subTest(item=item), self.assertRaises(ValueError):
                setup.parse_build_target_enrollment(item)
        with self.assertRaises(ValueError):
            setup.parse_build_target_enrollment(b'{"schema":1,"schema":1}')
        with patch.object(setup, "_username", return_value="bw-target-same"), self.assertRaises(ValueError):
            setup.parse_build_target_enrollment(enrollment(("alice", "bob")))

    def test_policy_and_protected_roots_are_validated_before_host_calls(self) -> None:
        for root in ("relative", "/opt", setup.SOURCE_ROOT, setup.DATA_ROOT, setup.SSH_ROOT,
                     setup.ETC_DIR + "/app", setup.SUDOERS_DIR):
            item = enrollment()
            item["policy"]["applications"]["shop"]["deployment_root"] = root
            with self.subTest(root=root), self.assertRaises(ValueError):
                setup.parse_build_target_enrollment(item)
        item = enrollment()
        item["policy"]["applications"]["shop"]["platform"]["baseline"] = "debian-12"
        with patch.object(setup, "run") as run, self.assertRaises(ValueError):
            setup.configure_build_target(configuration(build_target_enrollment=item))
        run.assert_not_called()


class BuildTargetSetupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.base.chmod(0o711)
        self.users = {}
        self.groups = {}
        self.commands = []
        self.installed = set()
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(root_view())
        paths = {"ETC_DIR": self.base / "etc/build-target", "DATA_ROOT": self.base / "data/build-target",
                 "SSH_ROOT": self.base / "data/build-target-ssh", "SUDOERS_DIR": self.base / "etc/sudoers.d"}
        for name, path in paths.items():
            stack.enter_context(patch.object(setup, name, str(path)))
        stack.enter_context(patch.object(setup, "MARKER_PATH", setup.ETC_DIR + "/.managed.json"))
        stack.enter_context(patch.object(setup, "CONFIG_PATH", setup.ETC_DIR + "/config.json"))
        stack.enter_context(patch.object(setup, "ROOTS", {name: setup.DATA_ROOT + "/" + name for name in setup.ROOTS}))
        stack.enter_context(patch.object(maintenance, "LOCK_PATH", setup.ETC_DIR + "/.maintenance.lock"))
        stack.enter_context(patch.object(setup, "is_dry_run", return_value=False))
        stack.enter_context(patch("lib.machine_state.can_manage_system_services", return_value=True))
        self.source = stack.enter_context(patch.object(setup, "_trusted_source_tree"))
        self.platform = stack.enter_context(patch.object(setup.entrypoint, "require_platform"))
        stack.enter_context(patch.object(setup.pwd, "getpwnam", side_effect=lambda name: self.users[name]))
        stack.enter_context(patch.object(setup.grp, "getgrnam", side_effect=lambda name: self.groups[name]))
        stack.enter_context(patch.object(setup.pwd, "getpwall", side_effect=lambda: list(self.users.values())))
        stack.enter_context(patch.object(setup.grp, "getgrall", side_effect=lambda: list(self.groups.values())))
        stack.enter_context(patch.object(setup.os, "getgrouplist", side_effect=lambda _name, gid: [gid]))
        stack.enter_context(patch.object(setup, "is_package_installed", side_effect=lambda name: name in self.installed))
        self.run = stack.enter_context(patch.object(setup, "run", side_effect=self.native))

    def native(self, args, **_kwargs):
        self.commands.append(args)
        if args[0] == "groupadd":
            name = args[-1]
            self.groups[name] = SimpleNamespace(gr_name=name, gr_gid=2000 + len(self.groups), gr_mem=[])
        elif args[0] == "useradd":
            name = args[-1]
            self.users[name] = SimpleNamespace(pw_name=name, pw_uid=3000 + len(self.users),
                                               pw_gid=self.groups[name].gr_gid,
                                               pw_dir=args[args.index("--home-dir") + 1], pw_shell="/bin/sh")
        elif args[0] == "apt-get":
            self.installed.update(args[4:])
        return SimpleNamespace(returncode=0, stdout=(args[-1] + " L today\n") if args[0] == "passwd" else "")

    def snapshot(self):
        return {str(path.relative_to(self.base)): (stat.S_IMODE(path.stat().st_mode), path.read_bytes())
                for path in self.base.rglob("*") if path.is_file()}

    def test_install_permissions_exact_grants_rotation_retirement_disable_and_reenable(self) -> None:
        setup.configure_build_target(configuration(build_target_enrollment=enrollment(("alice", "bob"))))
        lock_inode = Path(maintenance.LOCK_PATH).stat().st_ino
        for principal in ("alice", "bob"):
            sudo, auth = map(Path, setup._file_paths(principal))
            self.assertEqual(sudo.read_bytes(), setup._sudoers(principal))
            self.assertEqual(auth.read_bytes(), setup._authorized_key(principal, public_key(1 if principal == "alice" else 2)))
            for path, mode in ((Path(setup.SSH_ROOT), 0o711), (Path(setup._home(principal)), 0o555),
                               (auth.parent, 0o755), (auth, 0o644), (sudo, 0o440)):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)
            command = ("/usr/bin/python3 -I /opt/basaltwater/common/service_tools/build_target.py "
                       "--serve --principal " + principal)
            self.assertEqual(sudo.read_text(), f'Defaults:{setup._username(principal)} env_keep += "SSH_ORIGINAL_COMMAND"\n'
                             f'{setup._username(principal)} ALL=(root) NOPASSWD: {command}\n')
            self.assertEqual(auth.read_text(), f'restrict,command="/usr/bin/sudo -n {command}" '
                             + public_key(1 if principal == "alice" else 2) + "\n")
        self.assertEqual(self.installed, {"openssh-server", "sudo", "nginx"})
        self.assertTrue(any(command[0] == "visudo" for command in self.commands))
        self.assertTrue(all(command[0] != "userdel" for command in self.commands))
        for root in (setup.ETC_DIR, setup.DATA_ROOT, *setup.ROOTS.values()):
            self.assertEqual(stat.S_IMODE(Path(root).stat().st_mode), 0o700)
        for name in (setup.CONFIG_PATH, setup.MARKER_PATH, maintenance.LOCK_PATH):
            self.assertEqual(stat.S_IMODE(Path(name).stat().st_mode), 0o600)
        configured = setup.entrypoint.parse_config(json.loads(Path(setup.CONFIG_PATH).read_bytes()))
        self.assertEqual(configured["roots"], setup.ROOTS)
        self.assertNotIn("keys", configured)
        before = self.snapshot()
        setup.configure_build_target(configuration(build_target_enrollment=None))
        self.assertEqual(self.snapshot(), before)
        with maintenance.target_lease():
            pass
        setup.configure_build_target(configuration(build_target_enrollment=enrollment(("alice", "bob"))))
        self.assertEqual(self.snapshot(), before)
        rotated = enrollment()
        rotated["keys"]["alice"] = public_key(7)
        setup.configure_build_target(configuration(build_target_enrollment=rotated))
        self.assertIn(public_key(7), Path(setup._file_paths("alice")[1]).read_text())
        self.assertTrue(all(not Path(path).exists() for path in setup._file_paths("bob")))
        evidence = []
        for root in setup.ROOTS.values():
            path = Path(root) / "retained-evidence"
            path.write_bytes(b"retain")
            evidence.append(path)
        setup.configure_build_target(configuration(disable_build_target=True, build_target_enrollment="ignored"))
        self.assertTrue(all(path.read_bytes() == b"retain" for path in evidence))
        self.assertFalse(Path(setup.CONFIG_PATH).exists())
        self.assertTrue(all(not Path(path).exists() for path in setup._file_paths("alice")))
        self.assertEqual(len(self.users), 2)
        before = self.snapshot()
        setup.configure_build_target(configuration(build_target_enrollment=None))
        self.assertEqual(self.snapshot(), before)
        setup.configure_build_target(configuration(disable_build_target=True))
        self.assertEqual(self.snapshot(), before)
        with self.assertRaises(RuntimeError):
            with maintenance.target_lease():
                pass
        setup.configure_build_target(configuration(build_target_enrollment=rotated))
        with maintenance.target_lease():
            pass
        self.assertEqual(Path(maintenance.LOCK_PATH).stat().st_ino, lock_inode)

    def test_busy_request_refuses_enable_and_disable_before_any_change(self) -> None:
        setup.configure_build_target(configuration())
        before = self.snapshot()
        self.run.reset_mock()
        with maintenance.target_lease():
            for config in (configuration(), configuration(disable_build_target=True)):
                with self.assertRaisesRegex(RuntimeError, "active"):
                    setup.configure_build_target(config)
        self.run.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_inaccessible_shared_ssh_ancestor_refuses_before_bootstrap_or_changes(self) -> None:
        shared_parent = Path(setup.SSH_ROOT).parent
        shared_parent.mkdir(mode=0o700)
        for ancestor in (shared_parent, self.base):
            with self.subTest(ancestor=ancestor):
                shared_parent.chmod(0o755)
                self.base.chmod(0o711)
                ancestor.chmod(0o700)
                before = {str(path): stat.S_IMODE(path.stat().st_mode)
                          for path in (self.base, *self.base.rglob("*"))}
                with patch.object(setup, "_write") as publish, \
                     patch.object(setup.os, "mkdir") as mkdir, \
                     patch.object(setup.os, "fchmod") as chmod, \
                     self.assertRaisesRegex(ValueError, "SSH ancestor is not traversable"):
                    setup.configure_build_target(configuration())
                publish.assert_not_called()
                mkdir.assert_not_called()
                chmod.assert_not_called()
                self.run.assert_not_called()
                self.assertEqual({str(path): stat.S_IMODE(path.stat().st_mode)
                                  for path in (self.base, *self.base.rglob("*"))}, before)
                self.assertFalse(Path(setup.ETC_DIR).exists())
                self.assertEqual(self.users, {})
                self.assertEqual(self.groups, {})

    def test_new_shared_parents_are_traversable_with_restrictive_umask(self) -> None:
        previous = os.umask(0o077)
        try:
            setup.configure_build_target(configuration())
        finally:
            os.umask(previous)
        self.assertEqual(stat.S_IMODE(Path(setup.SSH_ROOT).parent.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE(Path(setup.SSH_ROOT).stat().st_mode), 0o711)
        auth = Path(setup._file_paths("alice")[1])
        self.assertEqual(stat.S_IMODE(auth.parent.parent.stat().st_mode), 0o555)
        self.assertEqual(stat.S_IMODE(auth.parent.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE(auth.stat().st_mode), 0o644)

    def test_inaccessible_shared_parent_refuses_update_without_preventing_disable(self) -> None:
        setup.configure_build_target(configuration())
        shared_parent = Path(setup.SSH_ROOT).parent
        shared_parent.chmod(0o700)
        before = self.snapshot()
        self.run.reset_mock()
        with self.assertRaisesRegex(ValueError, "SSH ancestor is not traversable"):
            setup.configure_build_target(configuration())
        self.run.assert_not_called()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(stat.S_IMODE(shared_parent.stat().st_mode), 0o700)
        setup.configure_build_target(configuration(disable_build_target=True))
        self.assertFalse(Path(setup.CONFIG_PATH).exists())
        self.assertTrue(all(not Path(path).exists() for path in setup._file_paths("alice")))
        self.assertEqual(stat.S_IMODE(shared_parent.stat().st_mode), 0o700)

    def test_omission_dry_run_unsupported_and_source_trust_failure_do_not_mutate(self) -> None:
        setup.configure_build_target(configuration(build_target_enrollment=None))
        setup.configure_build_target(configuration(dry_run=True))
        self.source.assert_not_called()
        self.platform.assert_not_called()
        for machine_type in ("privileged", "unprivileged", "oci", "wsl"):
            with self.subTest(machine_type=machine_type), self.assertRaises(RuntimeError):
                setup.configure_build_target(configuration(machine_type=machine_type))
        with patch.object(setup, "_trusted_source_tree", side_effect=ValueError("untrusted")), self.assertRaises(ValueError):
            setup.configure_build_target(configuration())
        self.run.assert_not_called()
        self.assertEqual(list(self.base.iterdir()), [])
        for value in (1, "true", None):
            with self.assertRaises(ValueError):
                setup.configure_build_target(configuration(disable_build_target=value))

    def test_failure_leaves_durable_hold_evidence_and_refuses_automatic_retry(self) -> None:
        setup.configure_build_target(configuration())
        with patch.object(setup, "_validate_sudoers", side_effect=RuntimeError("visudo failure")), \
             self.assertRaises(RuntimeError):
            setup.configure_build_target(configuration())
        marker = json.loads(Path(setup.MARKER_PATH).read_text())
        self.assertEqual(marker["state"], "updating")
        self.assertIsNotNone(marker["pending"])
        self.assertTrue(Path(setup.CONFIG_PATH).exists())
        before = self.snapshot()
        with self.assertRaisesRegex(RuntimeError, "reconciliation"):
            with maintenance.target_lease():
                pass
        for config in (configuration(), configuration(disable_build_target=True)):
            with self.assertRaisesRegex(RuntimeError, "reconciliation"):
                setup.configure_build_target(config)
        self.assertEqual(self.snapshot(), before)

    def test_failed_first_install_never_reopens_serving(self) -> None:
        with patch.object(setup, "_install_packages", side_effect=RuntimeError("package failure")), \
             self.assertRaises(RuntimeError):
            setup.configure_build_target(configuration())
        self.assertEqual(self.commands, [])
        self.assertTrue(Path(setup.MARKER_PATH).exists())
        with self.assertRaisesRegex(RuntimeError, "reconciliation"):
            with maintenance.target_lease():
                pass
        with self.assertRaisesRegex(RuntimeError, "reconciliation"):
            setup.configure_build_target(configuration())

    def test_disable_failure_retains_data_and_holds_even_remaining_keys(self) -> None:
        setup.configure_build_target(configuration())
        evidence = Path(setup.ROOTS["admission"]) / "evidence"
        evidence.write_bytes(b"retained")
        real_remove = setup.remove_file_durable

        def fail_keys(path):
            if path.endswith("authorized_keys"):
                raise OSError("cannot remove key")
            return real_remove(path)

        with patch.object(setup, "remove_file_durable", side_effect=fail_keys), self.assertRaises(OSError):
            setup.configure_build_target(configuration(disable_build_target=True))
        self.assertEqual(evidence.read_bytes(), b"retained")
        self.assertTrue(Path(setup._file_paths("alice")[1]).exists())
        self.assertFalse(Path(setup._file_paths("alice")[0]).exists())
        with self.assertRaises(RuntimeError):
            with maintenance.target_lease():
                pass

    def test_visudo_failure_prevents_publication_and_retains_private_candidate(self) -> None:
        setup.configure_build_target(configuration())
        before = {path: Path(path).read_bytes() for path in (setup.CONFIG_PATH, *setup._file_paths("alice"))}
        rotated = enrollment()
        rotated["keys"]["alice"] = public_key(7)

        def validate(args, **kwargs):
            if args[0] == "visudo":
                raise RuntimeError("visudo rejected candidate")
            return self.native(args, **kwargs)

        with patch.object(setup, "run", side_effect=validate), self.assertRaises(RuntimeError):
            setup.configure_build_target(configuration(build_target_enrollment=rotated))
        self.assertEqual({path: Path(path).read_bytes() for path in before}, before)
        self.assertEqual(Path(setup.ETC_DIR + "/.sudoers-candidate").read_bytes(), setup._sudoers("alice"))
        with self.assertRaises(RuntimeError):
            with maintenance.target_lease():
                pass

    def test_unsupported_platform_and_unverified_packages_fail_before_grants(self) -> None:
        self.platform.side_effect = ValueError("unsupported platform")
        with self.assertRaises(ValueError):
            setup.configure_build_target(configuration())
        self.run.assert_not_called()
        self.assertEqual(list(self.base.iterdir()), [])
        self.platform.side_effect = None
        with patch.object(setup, "is_package_installed", return_value=False), self.assertRaises(RuntimeError):
            setup.configure_build_target(configuration())
        self.assertEqual(self.users, {})
        self.assertTrue(all(command[0] == "apt-get" for command in self.commands))

    def test_unmarked_identity_and_namespace_files_are_never_adopted(self) -> None:
        name = setup._username("alice")
        self.users[name] = SimpleNamespace(pw_name=name)
        with self.assertRaisesRegex(ValueError, "unmarked"):
            setup.configure_build_target(configuration())
        self.users.clear()
        setup.configure_build_target(configuration())
        extra = Path(setup._home("alice")) / ".ssh/unmarked"
        extra.write_bytes(b"foreign")
        with self.assertRaisesRegex(ValueError, "unmarked"):
            setup.configure_build_target(configuration())
        extra.unlink()
        extra = Path(setup.SUDOERS_DIR) / "bw-target-unmarked"
        extra.write_bytes(b"foreign")
        with self.assertRaisesRegex(ValueError, "unmarked"):
            setup.configure_build_target(configuration(disable_build_target=True))
        extra.unlink()
        self.groups["bw-target-unmarked"] = SimpleNamespace(gr_name="bw-target-unmarked")
        with self.assertRaisesRegex(ValueError, "unmarked"):
            setup.configure_build_target(configuration())

    def test_missing_marker_or_lock_never_recreates_ownership(self) -> None:
        setup.configure_build_target(configuration())
        marker = Path(setup.MARKER_PATH)
        saved = marker.read_bytes()
        marker.unlink()
        with self.assertRaisesRegex(ValueError, "ownership marker"):
            setup.configure_build_target(configuration())
        marker.write_bytes(saved)
        marker.chmod(0o600)
        Path(maintenance.LOCK_PATH).unlink()
        before = self.snapshot()
        with self.assertRaises(FileNotFoundError):
            setup.configure_build_target(configuration())
        self.assertEqual(self.snapshot(), before)

    def test_unmarked_modified_and_unsafe_resources_fail_closed(self) -> None:
        for resource in (setup.DATA_ROOT, setup.SSH_ROOT, setup.ETC_DIR):
            path = Path(resource)
            path.mkdir(parents=True, mode=0o700)
            path.parent.chmod(0o755)
            with self.subTest(resource=resource), self.assertRaises((ValueError, FileNotFoundError)):
                setup.configure_build_target(configuration())
            path.rmdir()
        setup.configure_build_target(configuration())
        for path in (setup.CONFIG_PATH, *setup._file_paths("alice")):
            original = Path(path).read_bytes()
            mode = stat.S_IMODE(Path(path).stat().st_mode)
            Path(path).chmod(mode | 0o200)
            Path(path).write_bytes(original + b"modified\n")
            Path(path).chmod(mode)
            for config in (configuration(), configuration(disable_build_target=True)):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    setup.configure_build_target(config)
            Path(path).chmod(mode | 0o200)
            Path(path).write_bytes(original)
            Path(path).chmod(mode)
        auth = Path(setup._file_paths("alice")[1])
        original = auth.read_bytes()
        auth.unlink()
        auth.symlink_to(self.base / "elsewhere")
        with self.assertRaises((OSError, ValueError)):
            setup.configure_build_target(configuration())
        auth.unlink()
        auth.write_bytes(original)
        auth.chmod(0o644)
        Path(setup.SSH_ROOT).chmod(0o777)
        with self.assertRaises(ValueError):
            setup.configure_build_target(configuration())

    def test_transport_uid_gid_membership_and_password_must_remain_exclusive(self) -> None:
        setup.configure_build_target(configuration())
        alice = self.users[setup._username("alice")]
        uid = alice.pw_uid
        alice.pw_uid += 1
        with self.assertRaisesRegex(ValueError, "identity changed"):
            setup.configure_build_target(configuration())
        alice.pw_uid = uid
        alice.pw_shell = "/bin/bash"
        with self.assertRaisesRegex(ValueError, "Invalid managed"):
            setup.configure_build_target(configuration())
        alice.pw_shell = "/bin/sh"
        for changes in ({"pw_uid": alice.pw_uid}, {"pw_gid": alice.pw_gid}):
            self.users["other"] = SimpleNamespace(pw_name="other", pw_uid=9876, pw_gid=9876)
            vars(self.users["other"]).update(changes)
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "shared"):
                setup.configure_build_target(configuration())
            del self.users["other"]
        self.groups["alias"] = SimpleNamespace(gr_name="alias", gr_gid=alice.pw_gid, gr_mem=[])
        with self.assertRaisesRegex(ValueError, "alias"):
            setup.configure_build_target(configuration())
        del self.groups["alias"]
        with patch.object(setup.os, "getgrouplist", return_value=[alice.pw_gid, 27]), self.assertRaises(ValueError):
            setup.configure_build_target(configuration())
        with patch.object(setup, "run", return_value=SimpleNamespace(stdout=alice.pw_name + " P today")), \
             self.assertRaisesRegex(ValueError, "locked"):
            setup.configure_build_target(configuration())


if __name__ == "__main__":
    unittest.main()
