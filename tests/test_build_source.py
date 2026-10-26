"""Mocked Git contracts for pinned source snapshots and safe unpacking."""

from __future__ import annotations

import hashlib
import os
import stat
import struct
import tempfile
import unittest
from pathlib import Path

from lib.build_protocol import DEFAULT_LIMITS, parse_build_request
from lib.build_source import (
    MAGIC,
    SourceLimits,
    create_source_snapshot,
    unpack_source_snapshot,
)


class FakeGit:
    def __init__(self, repository: Path, *, entries=None, dirty: bytes = b"", shallow: bool = False,
                 tags: bytes = b"cccccccccccccccccccccccccccccccccccccccc refs/tags/v1.2.3\n",
                 move_tag_during_bundle: bool = False, filter_value: bytes = b"unspecified",
                 staged: bytes = b""):
        self.repository = repository
        self.revision = "a" * 40
        self.dirty = dirty
        self.shallow = shallow
        self.tags = tags
        self.move_tag_during_bundle = move_tag_during_bundle
        self.filter_value = filter_value
        self.staged = staged
        self.bundle = b"private-filtered-history-bundle"
        self.calls: list[tuple[list[str], dict]] = []
        self.entries = entries or [
            (".gitattributes", "100644", "blob", b"*.txt text\n"),
            (".gitignore", "100644", "blob", b"ignored.tmp\n"),
            (".gitmodules", "100644", "blob", b"[submodule \"docs\"]\npath = docs\n"),
            ("basaltwater.json", "100644", "blob", b'{ "version": 1 }\n'),
            ("src/main.py", "100755", "blob", b"committed-source\n"),
        ]
        self.objects = {}
        self.object_by_path = {}
        for path, mode, kind, content in self.entries:
            if kind == "blob":
                object_id = hashlib.sha1(content).hexdigest()
                self.objects[object_id] = content
            else:
                object_id = "b" * 40
            self.object_by_path[path] = (mode, kind, object_id)

    def __call__(self, command, *, cwd, env, input_data, timeout,
                 max_output_bytes, file_size_limit=None):
        command = list(command)
        self.calls.append((command, {
            "cwd": cwd, "env": dict(env), "input_data": input_data,
            "timeout": timeout, "max_output_bytes": max_output_bytes,
            "file_size_limit": file_size_limit,
        }))
        subcommand = next(
            (item for item in ("rev-parse", "status", "ls-tree", "cat-file", "for-each-ref",
                               "bundle", "check-attr", "ls-files")
             if item in command),
            None,
        )
        if subcommand == "rev-parse":
            if "--show-toplevel" in command:
                result = os.fsencode(self.repository) + b"\n"
            elif "--is-shallow-repository" in command:
                result = b"true\n" if self.shallow else b"false\n"
            elif "--git-path" in command:
                result = os.fsencode(self.repository / ".git/info/grafts") + b"\n"
            else:
                result = self.revision.encode("ascii") + b"\n"
        elif subcommand == "status":
            result = self.dirty
        elif subcommand == "ls-files":
            if "--stage" in command:
                records = []
                for path, (mode, _kind, object_id) in sorted(self.object_by_path.items()):
                    if self.staged and path == "src/main.py":
                        object_id = "e" * 40
                    records.append(
                        f"{mode} {object_id} 0\t{path}".encode("utf-8") + b"\0"
                    )
                result = b"".join(records)
            else:
                result = b"".join(
                    b"H " + path.encode("utf-8") + b"\0"
                    for path in sorted(self.object_by_path)
                )
        elif subcommand == "check-attr":
            paths = (input_data or b"").split(b"\0")[:-1]
            result = b"".join(path + b"\0filter\0" + self.filter_value + b"\0" for path in paths)
        elif subcommand == "ls-tree":
            self.assert_revision_in_tree_command(command)
            records = []
            for path, (mode, kind, object_id) in sorted(self.object_by_path.items()):
                records.append(
                    mode.encode("ascii") + b" " + kind.encode("ascii") + b" "
                    + object_id.encode("ascii") + b"\t" + path.encode("utf-8") + b"\0"
                )
            result = b"".join(records)
        elif subcommand == "cat-file" and "--batch-check" in command:
            object_ids = (input_data or b"").splitlines()
            result_lines = []
            for raw_object_id in object_ids:
                object_id = raw_object_id.decode("ascii")
                if object_id not in self.objects:
                    raise AssertionError("Fake Git requested an unknown blob")
                result_lines.append(
                    f"{object_id} blob {len(self.objects[object_id])}\n".encode("ascii")
                )
            result = b"".join(result_lines)
        elif subcommand == "cat-file" and "--batch" in command:
            object_ids = (input_data or b"").splitlines()
            result = b"".join(
                object_id + b" blob " + str(len(self.objects[object_id.decode("ascii")])).encode("ascii")
                + b"\n" + self.objects[object_id.decode("ascii")] + b"\n"
                for object_id in object_ids
            )
        elif subcommand == "for-each-ref":
            result = self.tags
        elif subcommand == "bundle":
            bundle_index = command.index("--version=3") + 1
            if command[bundle_index].startswith("--"):
                raise AssertionError("Git bundle revision options must follow its destination")
            bundle_path = Path(command[bundle_index])
            bundle_path.write_bytes(self.bundle)
            if self.move_tag_during_bundle:
                self.tags = b"dddddddddddddddddddddddddddddddddddddddd refs/tags/v1.2.3\n"
            result = b""
        else:
            raise AssertionError(f"Unexpected Git command: {command}")
        if len(result) > max_output_bytes:
            raise AssertionError("Fake Git output exceeds the caller's requested bound")
        return result

    def assert_revision_in_tree_command(self, command):
        index = command.index("ls-tree")
        self.last_tree_revision = command[index + 3]


class BuildSourceTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.repository = self.root / "repo"
        (self.repository / ".git").mkdir(parents=True)
        (self.repository / ".git/config").write_text("credential.helper=private-canary\n")
        hooks = self.repository / ".git/hooks"
        hooks.mkdir()
        (hooks / "pre-commit").write_text("hook-canary\n")
        remote_ref = self.repository / ".git/refs/remotes/origin"
        remote_ref.mkdir(parents=True)
        (remote_ref / "main").write_text("remote-canary\n")
        (self.repository / ".gitattributes").write_bytes(b"*.txt text\n")
        (self.repository / ".gitignore").write_bytes(b"ignored.tmp\n")
        (self.repository / ".gitmodules").write_bytes(b"[submodule \"docs\"]\npath = docs\n")
        (self.repository / "basaltwater.json").write_bytes(b'{ "version": 1 }\n')
        (self.repository / "src").mkdir()
        # A live file is deliberately different from the committed object FakeGit returns.
        (self.repository / "src/main.py").write_text("uncommitted-worktree-bytes\n")
        (self.repository / "src/main.py").chmod(0o755)
        self.snapshot = self.root / "source.bws"
        self.output = self.root / "unpacked"
        self.git = FakeGit(self.repository)

    def create(self, **kwargs):
        return create_source_snapshot(
            str(self.repository), str(self.snapshot), runner=self.git, **kwargs,
        )

    def unpack(self, source=None, **kwargs):
        return unpack_source_snapshot(
            str(self.snapshot), str(self.output),
            expected_source=source or self.source, **kwargs,
        )

    def test_clean_head_is_frozen_from_objects_and_returns_protocol_source_identity(self):
        source = self.create()
        self.source = source
        self.assertEqual(set(source), {"revision", "snapshot_sha256", "manifest_sha256"})
        self.assertEqual(source["revision"], self.git.revision)
        self.assertEqual(source["manifest_sha256"], hashlib.sha256(b'{ "version": 1 }\n').hexdigest())
        self.assertEqual(source["snapshot_sha256"], hashlib.sha256(self.snapshot.read_bytes()).hexdigest())
        request = {
            "schema": 1, "request_id": "c" * 32, "project": "snapshot-test", "source": source,
            "platform": {"os": "linux", "architecture": "x86_64", "baseline": "debian-13"},
            "components": [{"name": "site", "type": "static", "artifacts": ["src/main.py"],
                            "commands": [], "env": {}}],
            "limits": dict(DEFAULT_LIMITS),
        }
        self.assertEqual(parse_build_request(request)["source"], source)

        tree_commands = [command for command, _ in self.git.calls if "ls-tree" in command]
        self.assertEqual(len(tree_commands), 1)
        self.assertEqual(self.git.last_tree_revision, self.git.revision)
        self.assertFalse(any("archive" in command for command, _ in self.git.calls))
        self.assertEqual(stat.S_IMODE(self.snapshot.stat().st_mode), 0o400)

    def test_checked_out_source_and_filtered_history_are_separate_payloads(self):
        source = self.create()
        self.source = source
        checkout, history = self.unpack()
        checkout_path, history_path = Path(checkout), Path(history)
        self.assertEqual((checkout_path / "src/main.py").read_bytes(), b"committed-source\n")
        self.assertEqual((checkout_path / ".gitignore").read_bytes(), b"ignored.tmp\n")
        self.assertEqual((checkout_path / ".gitattributes").read_bytes(), b"*.txt text\n")
        self.assertTrue((checkout_path / ".gitmodules").is_file())
        self.assertFalse((checkout_path / ".git").exists())
        self.assertEqual(history_path.read_bytes(), self.git.bundle)
        self.assertEqual(stat.S_IMODE(history_path.stat().st_mode), 0o400)
        self.assertNotIn(b"private-canary", self.snapshot.read_bytes())
        self.assertNotIn(b"hook-canary", self.snapshot.read_bytes())
        self.assertNotIn(b"remote-canary", self.snapshot.read_bytes())
        self.assertNotIn(b"private-canary", history_path.read_bytes())

        bundle_command = next(command for command, _ in self.git.calls if "bundle" in command)
        self.assertIn("--version=3", bundle_command)
        self.assertIn("--filter=blob:none", bundle_command)
        self.assertLess(bundle_command.index("--version=3") + 1,
                        bundle_command.index("--filter=blob:none"))
        self.assertIn("HEAD", bundle_command)
        self.assertIn("refs/tags/v1.2.3", bundle_command)
        self.assertNotIn("--all", bundle_command)
        env = next(call[1]["env"] for call in self.git.calls if "status" in call[0])
        self.assertEqual(env["GIT_CONFIG_NOSYSTEM"], "1")
        self.assertEqual(env["GIT_CONFIG_GLOBAL"], os.devnull)
        self.assertEqual(env["GIT_NO_LAZY_FETCH"], "1")
        self.assertNotIn("GIT_CONFIG_PARAMETERS", env)

    def test_dirty_repository_is_rejected_before_source_or_history_copy(self):
        self.git.dirty = b" M src/main.py\0"
        with self.assertRaisesRegex(ValueError, "dirty"):
            self.create()
        self.assertFalse(self.snapshot.exists())
        self.assertFalse(any("cat-file" in command for command, _ in self.git.calls))

    def test_staged_and_filter_configured_repositories_are_rejected_safely(self):
        self.git.staged = b"staged"
        with self.assertRaisesRegex(ValueError, "dirty"):
            self.create()
        self.assertFalse(any("status" in command for command, _ in self.git.calls))

        self.git = FakeGit(self.repository, filter_value=b"custom-filter")
        with self.assertRaisesRegex(ValueError, "filters are unsupported"):
            self.create()
        self.assertFalse(any("status" in command for command, _ in self.git.calls))

    def test_symlink_and_special_worktree_files_are_not_followed(self):
        tracked = self.repository / "src/main.py"
        for kind in ("symlink", "fifo"):
            with self.subTest(kind=kind):
                tracked.unlink()
                if kind == "symlink":
                    tracked.symlink_to(self.root / "outside")
                else:
                    os.mkfifo(tracked)
                with self.assertRaisesRegex(ValueError, "symlink or special"):
                    self.create()
                self.assertFalse(self.snapshot.exists())
        tracked.unlink()
        tracked.write_text("uncommitted-worktree-bytes\n")
        tracked.chmod(0o755)

    def test_worktree_executable_mode_must_match_the_pinned_tree(self):
        tracked = self.repository / "src/main.py"
        tracked.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "dirty"):
            self.create()
        self.assertFalse(self.snapshot.exists())

    def test_shallow_repository_is_rejected(self):
        self.git.shallow = True
        with self.assertRaisesRegex(ValueError, "Shallow"):
            self.create()
        self.assertFalse(self.snapshot.exists())

    def test_local_graft_metadata_is_rejected(self):
        grafts = self.repository / ".git/info/grafts"
        grafts.parent.mkdir()
        grafts.write_text("local-parent-rewrite\n")
        with self.assertRaisesRegex(ValueError, "graft metadata"):
            self.create()
        self.assertFalse(self.snapshot.exists())

    def test_command_deadline_is_passed_to_every_git_call(self):
        self.create(limits=SourceLimits(command_timeout_seconds=7))
        self.assertTrue(self.git.calls)
        self.assertTrue(all(call[1]["timeout"] <= 7 for call in self.git.calls))

    def test_changed_tag_reference_invalidates_snapshot_publication(self):
        self.git = FakeGit(self.repository, move_tag_during_bundle=True)
        with self.assertRaisesRegex(ValueError, "tag references changed"):
            self.create()
        self.assertFalse(self.snapshot.exists())

    def test_symlinks_gitlinks_special_files_and_forbidden_metadata_are_rejected(self):
        invalid_entries = (
            [("basaltwater.json", "100644", "blob", b"{}\n"),
             ("src/link", "120000", "blob", b"../outside")],
            [("basaltwater.json", "100644", "blob", b"{}\n"),
             ("vendor/lib", "160000", "commit", None)],
            [("basaltwater.json", "100644", "blob", b"{}\n"),
             ("src/socket", "100664", "blob", b"special")],
            [("basaltwater.json", "100644", "blob", b"{}\n"),
             (".git-credentials", "100600", "blob", b"token-canary")],
            [("basaltwater.json", "100644", "blob", b"{}\n"),
             (".git/config", "100644", "blob", b"credential-canary")],
        )
        for entries in invalid_entries:
            with self.subTest(entries=entries):
                self.git = FakeGit(self.repository, entries=entries)
                with self.assertRaises(ValueError):
                    self.create()
                self.assertFalse(self.snapshot.exists())

    def test_lfs_pointer_is_rejected_even_when_it_is_a_regular_blob(self):
        pointer = (
            b"version https://git-lfs.github.com/spec/v1\n"
            + b"oid sha256:" + b"d" * 64 + b"\nsize 4096\n"
        )
        self.git = FakeGit(self.repository, entries=[
            ("basaltwater.json", "100644", "blob", b"{}\n"),
            ("src/asset.bin", "100644", "blob", pointer),
        ])
        (self.repository / "basaltwater.json").write_bytes(b"{}\n")
        (self.repository / "src/asset.bin").write_bytes(pointer)
        with self.assertRaisesRegex(ValueError, "Git LFS"):
            self.create()
        self.assertFalse(self.snapshot.exists())

    def test_case_colliding_source_paths_are_rejected(self):
        self.git = FakeGit(self.repository, entries=[
            ("basaltwater.json", "100644", "blob", b"{}\n"),
            ("Assets/logo.svg", "100644", "blob", b"upper"),
            ("assets/logo.svg", "100644", "blob", b"lower"),
        ])
        with self.assertRaisesRegex(ValueError, "case-colliding"):
            self.create()
        self.assertFalse(self.snapshot.exists())

    def test_missing_manifest_and_size_or_count_limits_fail_before_publication(self):
        self.git = FakeGit(self.repository, entries=[
            ("README.md", "100644", "blob", b"readme"),
        ])
        with self.assertRaisesRegex(ValueError, "basaltwater.json"):
            self.create()
        self.assertFalse(self.snapshot.exists())

        self.git = FakeGit(self.repository)
        with self.assertRaisesRegex(ValueError, "entry count"):
            self.create(limits=SourceLimits(max_entries=2))
        self.assertFalse(self.snapshot.exists())
        with self.assertRaisesRegex(ValueError, "total byte limit"):
            self.create(limits=SourceLimits(max_source_bytes=1))
        blob_reads = [command for command, _ in self.git.calls
                      if "cat-file" in command and "--batch" in command]
        self.assertEqual(blob_reads, [])
        self.assertFalse(self.snapshot.exists())

    def test_snapshot_and_manifest_checksums_are_verified_during_unpack(self):
        self.source = self.create()
        original = self.snapshot.read_bytes()
        damaged = original.replace(b"committed-source", b"corrupted-source")
        self.assertNotEqual(damaged, original)
        self.snapshot.chmod(0o600)
        self.snapshot.write_bytes(damaged)
        changed_identity = dict(self.source)
        changed_identity["snapshot_sha256"] = hashlib.sha256(damaged).hexdigest()
        with self.assertRaisesRegex(ValueError, "file checksum"):
            self.unpack(changed_identity)
        self.assertFalse(self.output.exists())

        self.snapshot.write_bytes(original)
        wrong_manifest = dict(self.source)
        wrong_manifest["manifest_sha256"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "manifest checksum"):
            self.unpack(wrong_manifest)
        self.assertFalse(self.output.exists())

        self.snapshot.write_bytes(original)
        changed_history = original.replace(
            b"private-filtered-history-bundle", b"private-filtered-history-bundlf",
        )
        self.snapshot.write_bytes(changed_history)
        changed_identity = dict(self.source)
        changed_identity["snapshot_sha256"] = hashlib.sha256(changed_history).hexdigest()
        with self.assertRaisesRegex(ValueError, "history checksum"):
            self.unpack(changed_identity)
        self.assertFalse(self.output.exists())

    def test_traversal_duplicate_and_control_paths_are_rejected_before_publication(self):
        self.source = self.create()
        original = self.snapshot.read_bytes()
        path_offset = original.index(b"src/main.py")
        length_offset = path_offset - 4
        self.assertEqual(struct.unpack("!I", original[length_offset:path_offset])[0], len(b"src/main.py"))

        attacks = (
            (b"../x/main.py", "traversal"),
            (b".gitmodules", "duplicate"),
            (b".gitconfig", "forbidden"),
        )
        for replacement, _label in attacks:
            with self.subTest(path=replacement):
                malformed = bytearray(original)
                malformed[length_offset:path_offset] = struct.pack("!I", len(replacement))
                malformed[path_offset:path_offset + len(b"src/main.py")] = replacement
                self.snapshot.chmod(0o600)
                self.snapshot.write_bytes(malformed)
                expected = dict(self.source)
                expected["snapshot_sha256"] = hashlib.sha256(malformed).hexdigest()
                with self.assertRaises(ValueError):
                    self.unpack(expected)
                self.assertFalse(self.output.exists())
                self.snapshot.write_bytes(original)

    def test_truncated_trailing_corrupt_and_oversized_snapshots_do_not_publish(self):
        self.source = self.create()
        original = self.snapshot.read_bytes()
        invalid_snapshots = (original[:-1], original + b"trailing", b"unsupported")
        for invalid in invalid_snapshots:
            with self.subTest(length=len(invalid)):
                self.snapshot.chmod(0o600)
                self.snapshot.write_bytes(invalid)
                expected = dict(self.source)
                expected["snapshot_sha256"] = hashlib.sha256(invalid).hexdigest()
                with self.assertRaises(ValueError):
                    self.unpack(expected)
                self.assertFalse(self.output.exists())

        self.snapshot.write_bytes(original)
        with self.assertRaisesRegex(ValueError, "snapshot checksum"):
            self.unpack({**self.source, "snapshot_sha256": "0" * 64})
        with self.assertRaisesRegex(ValueError, "byte limit"):
            self.unpack(limits=SourceLimits(max_snapshot_bytes=1))
        for limits in (SourceLimits(max_entries=2), SourceLimits(max_source_bytes=1),
                       SourceLimits(max_history_bytes=1), SourceLimits(max_metadata_bytes=1)):
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                self.unpack(limits=limits)
            self.assertFalse(self.output.exists())
        self.assertFalse(self.output.exists())

    def test_existing_publications_are_never_overwritten(self):
        self.source = self.create()
        original = self.snapshot.read_bytes()
        with self.assertRaises(FileExistsError):
            self.create()
        self.assertEqual(self.snapshot.read_bytes(), original)

        self.output.mkdir(mode=0o700)
        sentinel = self.output / "keep"
        sentinel.write_text("preserve")
        with self.assertRaises(FileExistsError):
            self.unpack()
        self.assertEqual(sentinel.read_text(), "preserve")

    def test_public_destination_parent_is_rejected(self):
        public = self.root / "public"
        public.mkdir(mode=0o755)
        destination = public / "snapshot"
        with self.assertRaisesRegex(ValueError, "private"):
            create_source_snapshot(str(self.repository), str(destination), runner=self.git)
        self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
