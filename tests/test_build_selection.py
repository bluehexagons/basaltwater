"""Controller-only saved selection using disposable keys and workspace state."""

from __future__ import annotations

import base64
from contextlib import ExitStack, contextmanager
from dataclasses import FrozenInstanceError
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lib import build_selection as selection
from lib.build_client import BuildHostClient
from lib.build_manifest import DEBIAN_PLATFORM
from lib.build_protocol import DEFAULT_LIMITS, MAX_ARTIFACT_BYTES
from lib.build_target_client import BuildTargetClient
from lib.config import SetupConfig
from lib.state_read import StateReadError


def public_key(seed: int = 1) -> str:
    wire = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + bytes([seed]) * 32
    return "ssh-ed25519 " + base64.b64encode(wire).decode("ascii")


def enrollment(*, target: bool = False) -> dict:
    if target:
        policy = {"schema": 1, "applications": {"site": {
            "project": "site", "principals": ["alice", "bob"],
            "deployment_root": "/srv/apps/site", "platform": dict(DEBIAN_PLATFORM),
            "limits": {"artifact_bytes": MAX_ARTIFACT_BYTES, "artifact_entries": 100_000},
            "components": {"web": {"type": "static", "domain": "site.example", "path": "/"}},
        }}}
    else:
        grant = {"platform": dict(DEBIAN_PLATFORM), "runtimes": ["python"],
                 "limits": dict(DEFAULT_LIMITS)}
        policy = {"schema": 1, "principals": {
            principal: {"projects": {"site": grant}} for principal in ("alice", "bob")
        }}
    return {"schema": 1, "policy": policy, "keys": {"alice": public_key(), "bob": public_key(2)},
            "max_records": 128, "max_bytes": 2 * 1024**3}


class BuildSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.base.chmod(0o700)
        self.workspace = self.base / "workspace"
        self.workspace.mkdir(mode=0o700)
        self.known_hosts = self.workspace / "known_hosts"
        self.known_hosts.write_text("# fixture; SSH checks host membership later\n", encoding="utf-8")
        self.known_hosts.chmod(0o644)
        self.key = self.base / "controller-key"
        # Deliberately not a real private key. Resolution must never read it.
        self.key.write_text("opaque controller identity fixture", encoding="utf-8")
        self.key.chmod(0o600)
        self.companion = Path(str(self.key) + ".pub")
        self.companion.write_text(public_key() + " controller@fixture\n", encoding="utf-8")
        self.companion.chmod(0o644)
        stack = ExitStack()
        self.addCleanup(stack.close)
        fixture_uid = self.key.stat().st_uid
        stack.enter_context(patch("os.geteuid", return_value=fixture_uid))
        stack.enter_context(patch.dict(os.environ, {
            "HOME": str(self.base), "BASALTWATER_WORKSPACE": str(self.workspace),
        }))
        for name in ("pwd.getpwnam", "pwd.getpwuid", "pwd.getpwall", "grp.getgrnam", "grp.getgrall",
                     "subprocess.Popen", "subprocess.run", "socket.socket", "socket.getaddrinfo"):
            stack.enter_context(patch(name, side_effect=AssertionError("Unexpected host action: " + name)))

    def config(self, *, target: bool = False, **overrides) -> SetupConfig:
        values = {"host": "builder.example", "username": "setup-user", "system_type": "server_web",
                  "machine_type": "vm", "ssh_key": str(self.key), "friendly_name": "Build Box",
                  "tags": ["builds", "shared"]}
        if target:
            values.update(host="target.example", build_target_enrollment=enrollment(target=True))
        else:
            values.update(is_build_server=True, build_host_enrollment=enrollment())
        values.update(overrides)
        return SetupConfig(**values)

    def resolve(self, config: SetupConfig | None = None, *, target: bool = False):
        config = config if config is not None else self.config(target=target)
        resolver = selection.resolve_target_endpoint if target else selection.resolve_builder_endpoint
        return resolver(config, workspace=str(self.workspace))

    def test_pure_exact_case_insensitive_selection_and_repeated_labels(self) -> None:
        config = self.config(tags=["BUILD BOX", "builder.example", "shared"])
        for reference in ("BUILDER.EXAMPLE", "build box", "SHARED"):
            with self.subTest(reference=reference):
                self.assertIs(selection.select_saved_builder(reference, [config]), config)
        for reference in ("builder", "build", "*.example", "absent"):
            with self.subTest(reference=reference), self.assertRaisesRegex(ValueError, "No saved setup"):
                selection.select_saved_builder(reference, [config])

    def test_invalid_reference_rejected_before_cache_or_credentials(self) -> None:
        for reference in (None, 1, "", " build", "build ", "x" * 129, "build\n", "b\x00x", "b\x7fx"):
            with self.subTest(reference=reference), \
                 patch.object(selection, "load_all_setup_commands") as load, \
                 self.assertRaises(ValueError):
                selection.resolve_saved_builder(reference, workspace=str(self.workspace))
            load.assert_not_called()

    def test_ambiguity_includes_host_name_tag_collisions_and_ineligible_records(self) -> None:
        first = self.config()
        second = self.config(host="second.example", friendly_name="Other")
        second.is_build_server = False
        for reference in ("shared", "builder.example", "Build Box"):
            second.tags = [reference]
            with self.subTest(reference=reference), \
                 patch.object(selection, "load_all_setup_commands", return_value=[first, second]), \
                 patch.object(selection, "_check_private_identity") as identity, \
                 self.assertRaisesRegex(ValueError, "Ambiguous"):
                selection.resolve_saved_builder(reference, workspace=str(self.workspace))
            identity.assert_not_called()

    def test_full_inventory_workspace_and_cache_errors_propagate(self) -> None:
        config = self.config()
        with patch.object(selection, "load_all_setup_commands", return_value=[config]) as load:
            result = selection.resolve_saved_builder("builds", workspace=str(self.workspace))
        load.assert_called_once_with(str(self.workspace))
        self.assertIs(result.config, config)
        self.assertEqual(result.endpoint.principal, "alice")
        error = StateReadError(str(self.workspace / "setups/broken.json"), "fixture corruption")
        with patch.object(selection, "load_all_setup_commands", side_effect=error), \
             self.assertRaises(StateReadError) as raised:
            selection.resolve_saved_builder("builds", workspace=str(self.workspace))
        self.assertIs(raised.exception, error)
        with patch.object(selection, "load_all_setup_commands", return_value=[]), \
             self.assertRaisesRegex(ValueError, "No saved setup"):
            selection.resolve_saved_builder("builds", workspace=str(self.workspace))

    def test_real_temporary_cache_inventory_reads_without_writes(self) -> None:
        setups = self.workspace / "setups"
        setups.mkdir(mode=0o700)
        config = self.config()
        record = {"host": config.host, "system_type": config.system_type, "args": config.to_dict()}
        cache = setups / "fixture.json"
        cache.write_text(json.dumps(record), encoding="utf-8")
        cache.chmod(0o600)
        before = cache.read_bytes()
        selected = selection.resolve_saved_builder("BUILD BOX", workspace=str(self.workspace))
        self.assertEqual(selected.config.host, config.host)
        self.assertEqual(selected.endpoint.identity_file, str(self.key))
        self.assertEqual(cache.read_bytes(), before)

    def test_builder_and_target_connection_contracts_and_principal_accounts(self) -> None:
        for target, prefix, client in ((False, "bw-build-", BuildHostClient),
                                       (True, "bw-target-", BuildTargetClient)):
            with self.subTest(target=target):
                config = self.config(target=target)
                before = config.to_dict()
                endpoint = self.resolve(config, target=target)
                self.assertEqual(endpoint.host, config.host)
                self.assertEqual(endpoint.principal, "alice")
                self.assertEqual(endpoint.username, prefix + "2bd806c97f0e")
                self.assertEqual(endpoint.port, 22)
                kwargs = endpoint.connection_kwargs()
                self.assertEqual(kwargs, {"host": config.host, "username": endpoint.username,
                    "port": 22, "identity_file": str(self.key), "known_hosts_file": str(self.known_hosts)})
                # Construction pins fixtures only; it must not start transport.
                with client(**kwargs, timeout=30):
                    pass
                self.assertEqual(config.to_dict(), before)
                with self.assertRaises(FrozenInstanceError):
                    endpoint.port = 23

    def test_binding_chooses_matching_principal_instead_of_first_policy_entry(self) -> None:
        self.companion.write_text(public_key(2) + " ordinary comment\n", encoding="utf-8")
        for target, prefix in ((False, "bw-build-"), (True, "bw-target-")):
            with self.subTest(target=target):
                endpoint = self.resolve(target=target)
                self.assertEqual(endpoint.principal, "bob")
                self.assertEqual(endpoint.username, prefix + "81b637d8fcd2")

    def test_disabled_legacy_and_unsupported_machine_fail_before_local_key_access(self) -> None:
        for target in (False, True):
            variants = []
            for machine in ("auto", "unprivileged", "privileged", "oci", "wsl"):
                variants.append(("machine_type", machine))
            variants.extend((("disable_build_target" if target else "disable_build_host", True),
                             ("build_target_enrollment" if target else "build_host_enrollment", None)))
            if not target:
                variants.append(("is_build_server", False))
            for field, value in variants:
                config = self.config(target=target)
                setattr(config, field, value)
                with self.subTest(target=target, field=field, value=value), \
                     patch.object(selection, "_check_private_identity") as identity, \
                     self.assertRaises(ValueError):
                    self.resolve(config, target=target)
                identity.assert_not_called()
            self.assertEqual(self.resolve(self.config(target=target, machine_type="hardware"),
                                          target=target).principal, "alice")
        with self.assertRaisesRegex(ValueError, "Legacy bare build-server"):
            self.resolve(self.config(build_host_enrollment=None))

    def test_enrollment_is_revalidated_and_duplicate_public_bindings_are_rejected(self) -> None:
        for target in (False, True):
            for invalid in ("duplicate", "missing", "bad-platform", "comment"):
                config = self.config(target=target)
                policy = config.build_target_enrollment if target else config.build_host_enrollment
                if invalid == "duplicate":
                    policy["keys"]["bob"] = public_key()
                elif invalid == "missing":
                    policy["keys"].pop("alice")
                elif invalid == "comment":
                    policy["keys"]["alice"] += " forbidden enrollment comment"
                else:
                    grant = (policy["policy"]["applications"]["site"] if target else
                             policy["policy"]["principals"]["alice"]["projects"]["site"])
                    grant["platform"]["baseline"] = "debian-12"
                with self.subTest(target=target, invalid=invalid), \
                     patch.object(selection, "_check_private_identity") as identity, \
                     self.assertRaises(ValueError):
                    self.resolve(config, target=target)
                identity.assert_not_called()

    def test_unmatched_saved_identity_fails_with_controller_guidance_without_fallback(self) -> None:
        self.companion.write_text(public_key(3) + "\n", encoding="utf-8")
        default = self.base / ".ssh/id_ed25519"
        default.parent.mkdir(mode=0o700)
        default.write_bytes(b"other identity")
        default.chmod(0o600)
        Path(str(default) + ".pub").write_text(public_key(), encoding="utf-8")
        for target in (False, True):
            with self.subTest(target=target), self.assertRaisesRegex(
                    ValueError, "controller SSH identity is not enrolled.*saved ssh_key"):
                self.resolve(target=target)

    def test_only_absent_selector_uses_established_default_and_tilde_expands(self) -> None:
        home_ssh = self.base / ".ssh"
        home_ssh.mkdir(mode=0o700)
        default = home_ssh / "id_ed25519"
        self.key.rename(default)
        self.companion.rename(str(default) + ".pub")
        for target in (False, True):
            for saved in (None, "~/.ssh/id_ed25519"):
                with self.subTest(target=target, saved=saved):
                    self.assertEqual(self.resolve(self.config(target=target, ssh_key=saved),
                        target=target).identity_file, str(default))
            for saved in ("", "relative", str(self.base / ".." / "controller-key"), "~/missing", "/tmp/key\n"):
                with self.subTest(target=target, saved=saved), self.assertRaises((ValueError, OSError)):
                    self.resolve(self.config(target=target, ssh_key=saved), target=target)

    def test_public_companion_allows_only_one_canonical_key_with_optional_comment(self) -> None:
        good = public_key()
        for text in (good, good + "\n", good + " controller comment with spaces\n"):
            self.companion.write_text(text, encoding="utf-8")
            self.assertEqual(self.resolve().principal, "alice")
        bad = ("", "ssh-ed25519", "ssh-ed25519 !!!", "ssh-ed25519 " + base64.b64encode(b"bad").decode(),
               "ssh-rsa " + good.split()[1], "restrict " + good, 'command="false" ' + good,
               " " + good, good + "\n" + good, good + "\n\n", good + "\r\n", good + "\x00comment",
               good.replace(" ", "  ", 1), good.replace(" ", "\t", 1), good + "=", good[:-2])
        for text in bad:
            self.companion.write_text(text, encoding="utf-8")
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.resolve()
        for raw in (b"\xff", b"x" * (selection.MAX_PUBLIC_KEY_BYTES + 1)):
            self.companion.write_bytes(raw)
            with self.subTest(size=len(raw)), self.assertRaises(ValueError):
                self.resolve()

    def test_missing_files_and_trust_workspace_are_not_created(self) -> None:
        for path in (self.key, self.companion, self.known_hosts):
            moved = path.with_name(path.name + ".moved")
            path.rename(moved)
            try:
                with self.subTest(path=path), self.assertRaises(OSError):
                    self.resolve()
                self.assertFalse(path.exists())
            finally:
                moved.rename(path)
        absent = self.base / "missing-workspace"
        with self.assertRaises(OSError):
            selection.resolve_builder_endpoint(self.config(), workspace=str(absent))
        self.assertFalse(absent.exists())

    def test_public_companion_size_boundary_and_changes_during_read(self) -> None:
        line = public_key() + " "
        at_limit = line + "x" * (selection.MAX_PUBLIC_KEY_BYTES - len(line))
        self.companion.write_text(at_limit, encoding="utf-8")
        self.assertEqual(self.resolve().principal, "alice")
        self.companion.write_text(at_limit + "x", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "bounded key-file limit"):
            self.resolve()

        real_fdopen = os.fdopen
        for replace in (False, True):
            self.companion.write_text(public_key() + "\n", encoding="utf-8")

            @contextmanager
            def changed_source(fd, *args, **kwargs):
                with real_fdopen(fd, *args, **kwargs) as source:
                    class Reader:
                        def read(reader, maximum):
                            raw = source.read(maximum)
                            if replace:
                                replacement = self.base / "replacement.pub"
                                replacement.write_text(public_key(2) + "\n", encoding="utf-8")
                                replacement.replace(self.companion)
                            else:
                                self.companion.write_text(public_key(2) + " changed\n", encoding="utf-8")
                            return raw

                    yield Reader()

            with self.subTest(replace=replace), patch("os.fdopen", side_effect=changed_source), \
                 self.assertRaisesRegex(ValueError, "changed during selection"):
                self.resolve()

    def test_invalid_host_and_nonboolean_role_flags_fail_closed(self) -> None:
        for target in (False, True):
            for field, value in (("host", "-oProxyCommand=bad"),
                                 ("disable_build_target" if target else "disable_build_host", 0)):
                config = self.config(target=target)
                setattr(config, field, value)
                with self.subTest(target=target, field=field), self.assertRaises(ValueError):
                    self.resolve(config, target=target)
        config = self.config()
        config.is_build_server = 1
        with self.assertRaises(ValueError):
            self.resolve(config)

    def test_each_local_file_rejects_symlinks_hardlinks_special_files_and_unsafe_permissions(self) -> None:
        for path in (self.key, self.companion, self.known_hosts):
            original = path.with_name(path.name + ".original")
            path.rename(original)
            try:
                for kind in ("symlink", "hardlink", "fifo", "directory", "unsafe-mode"):
                    with self.subTest(path=path, kind=kind):
                        if kind == "symlink":
                            path.symlink_to(original)
                        elif kind == "hardlink":
                            os.link(original, path)
                        elif kind == "fifo":
                            os.mkfifo(path, 0o600)
                        elif kind == "directory":
                            path.mkdir(mode=0o700)
                        else:
                            path.write_bytes(original.read_bytes())
                            path.chmod(0o640 if path == self.key else 0o666)
                        try:
                            with self.assertRaises((ValueError, OSError)):
                                self.resolve()
                        finally:
                            path.rmdir() if kind == "directory" else path.unlink()
            finally:
                original.rename(path)

    def test_foreign_file_owners_rejected_and_private_key_never_opened(self) -> None:
        real_stat, real_fstat = os.stat, os.fstat
        foreign_uid = self.key.stat().st_uid + 10000
        for path in (self.key, self.companion, self.known_hosts):
            def foreign(info):
                fields = list(info)
                fields[4] = foreign_uid
                return os.stat_result(fields)

            def stat_file(value, *args, **kwargs):
                info = real_stat(value, *args, **kwargs)
                return foreign(info) if str(value) == str(path) else info

            def fstat_file(fd):
                info = real_fstat(fd)
                return foreign(info) if info.st_ino == real_stat(path).st_ino else info

            with self.subTest(path=path), patch("os.stat", side_effect=stat_file), \
                 patch("os.fstat", side_effect=fstat_file), self.assertRaises(ValueError):
                self.resolve()
        real_open = os.open

        def public_only(path, *args, **kwargs):
            self.assertNotEqual(str(path), str(self.key), "Private-key bytes must not be opened")
            return real_open(path, *args, **kwargs)

        with patch("os.open", side_effect=public_only):
            self.assertEqual(self.resolve().principal, "alice")


if __name__ == "__main__":
    unittest.main()
