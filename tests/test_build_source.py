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


def git_blob_oid(content: bytes) -> str:
    header = b"blob " + str(len(content)).encode("ascii") + b"\0"
    return hashlib.sha1(header + content).hexdigest()


class FakeGit:
    def __init__(self, repository: Path, *, entries=None, dirty: bytes = b"", shallow: bool = False,
                 tags: bytes = b"cccccccccccccccccccccccccccccccccccccccc refs/tags/v1.2.3\n",
                 move_tag_during_bundle: bool = False, move_head_during_bundle: bool = False,
                 bundle_head_override: str | None = None,
                 filter_value: bytes = b"unspecified", staged: bytes = b""):
        self.repository = repository
        self.revision = "a" * 40
        self.head_revision = self.revision
        self.dirty = dirty
        self.shallow = shallow
        self.tags = tags
        self.move_tag_during_bundle = move_tag_during_bundle
        self.move_head_during_bundle = move_head_during_bundle
        self.bundle_head_override = bundle_head_override
        self.filter_value = filter_value
        self.staged = staged
        self.bundle = b"private-filtered-history-bundle"
        self.bundle_head = self.revision
        self.bundle_tags = tags
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
                object_id = git_blob_oid(content)
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
                               "bundle", "check-attr", "ls-files", "init", "update-ref")
             if item in command),
            None,
        )
        if subcommand == "rev-parse":
            if "--show-toplevel" in command:
                result = os.fsencode(self.repository) + b"\n"
            elif "--is-shallow-repository" in command:
                result = b"true\n" if self.shallow else b"false\n"
            elif "--git-path" in command:
                git_path = "objects" if "objects" in command else "info/grafts"
                result = os.fsencode(self.repository / ".git" / git_path) + b"\n"
            else:
                if any(item.startswith("--git-dir=") for item in command):
                    result = self.history_refs["HEAD"].encode("ascii") + b"\n"
                else:
                    result = self.head_revision.encode("ascii") + b"\n"
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
        elif subcommand == "init":
            self.private_git_directory = Path(command[-1])
            (self.private_git_directory / "objects/info").mkdir(parents=True)
            (self.private_git_directory / "refs/tags").mkdir(parents=True)
            (self.private_git_directory / "config").write_text(
                "[core]\n\trepositoryformatversion = 0\n\tbare = true\n",
            )
            (self.private_git_directory / "HEAD").write_text("ref: refs/heads/master\n")
            result = b""
        elif subcommand == "update-ref":
            self.history_git_directory = next(
                Path(item.split("=", 1)[1]) for item in command
                if item.startswith("--git-dir=")
            )
            self.alternates_content = (
                self.history_git_directory / "objects/info/alternates"
            ).read_bytes()
            self.history_refs = {}
            for record in (input_data or b"").decode("ascii").splitlines():
                fields = record.split()
                if len(fields) == 3 and fields[0] in {"update", "create"}:
                    self.history_refs[fields[1]] = fields[2]
            self.bundle_head = self.history_refs["HEAD"]
            self.bundle_tags = b"".join(
                f"{object_id} {ref}\n".encode("ascii")
                for ref, object_id in sorted(self.history_refs.items())
                if ref.startswith("refs/tags/")
            )
            result = b""
        elif subcommand == "bundle":
            if "create" in command:
                bundle_index = command.index("--version=3") + 1
                if command[bundle_index].startswith("--"):
                    raise AssertionError("Git bundle revision options must follow its destination")
                bundle_path = Path(command[bundle_index])
                bundle_arguments = command[bundle_index + 1:]
                if "--filter=blob:none" not in bundle_arguments:
                    raise AssertionError("Git bundle must retain its blob filter")
                if "HEAD" not in bundle_arguments:
                    raise AssertionError("Git bundle must use the private view's HEAD")
                if self.move_head_during_bundle:
                    self.head_revision = "b" * 40
                self.bundle_head = self.history_refs["HEAD"]
                self.head_revision = self.revision
                bundle_path.write_bytes(self.bundle)
                if self.move_tag_during_bundle:
                    self.tags = b"dddddddddddddddddddddddddddddddddddddddd refs/tags/v1.2.3\n"
                result = b""
            elif "list-heads" in command:
                recorded_head = self.bundle_head_override or self.bundle_head
                result = f"{recorded_head} HEAD\n".encode("ascii") + self.bundle_tags
            else:
                raise AssertionError(f"Unexpected Git bundle command: {command}")
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
        (self.repository / ".git/objects/info").mkdir(parents=True)
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
        (self.repository / "src/main.py").write_bytes(b"committed-source\n")
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
        destination_index = bundle_command.index("--version=3") + 1
        filter_index = bundle_command.index("--filter=blob:none")
        head_index = bundle_command.index("HEAD")
        self.assertGreater(filter_index, destination_index)
        self.assertGreater(head_index, filter_index)
        self.assertLess(bundle_command.index("--version=3") + 1,
                        bundle_command.index("--filter=blob:none"))
        self.assertIn("refs/tags/v1.2.3", bundle_command)
        self.assertNotIn("--all", bundle_command)
        self.assertEqual(
            sum("list-heads" in command for command, _ in self.git.calls), 1,
        )
        self.assertEqual(self.git.history_refs["HEAD"], self.git.revision)
        self.assertEqual(
            self.git.history_refs["refs/tags/v1.2.3"],
            "c" * 40,
        )
        self.assertIn(
            f"--git-dir={self.git.history_git_directory}", bundle_command,
        )
        self.assertNotEqual(
            os.path.commonpath((self.repository, self.git.history_git_directory)),
            str(self.repository),
        )
        self.assertEqual(
            self.git.alternates_content,
            os.fsencode(self.repository / ".git/objects") + b"\n",
        )
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

    def test_bundle_uses_the_resolved_revision_across_a_head_aba_race(self):
        self.git = FakeGit(self.repository, move_head_during_bundle=True)
        source = self.create()
        self.assertEqual(source["revision"], "a" * 40)
        self.assertEqual(self.git.head_revision, "a" * 40)
        self.assertEqual(self.git.bundle_head, "a" * 40)
        bundle_calls = [
            command for command, _ in self.git.calls
            if "bundle" in command and "create" in command
        ]
        self.assertEqual(len(bundle_calls), 1)
        self.assertIn("HEAD", bundle_calls[0])
        self.assertIn(
            f"--git-dir={self.git.history_git_directory}", bundle_calls[0],
        )
        self.assertEqual(self.git.history_refs["HEAD"], "a" * 40)

    def test_bundle_with_a_different_advertised_head_is_rejected(self):
        self.git = FakeGit(self.repository, bundle_head_override="b" * 40)
        with self.assertRaisesRegex(ValueError, "bundle HEAD does not match"):
            self.create()
        self.assertFalse(self.snapshot.exists())

    def test_same_size_worktree_edit_with_restored_mtime_is_rejected(self):
        tracked = self.repository / "src/main.py"
        original = tracked.stat()
        replacement = b"modified--source\n"
        self.assertEqual(len(replacement), original.st_size)
        tracked.write_bytes(replacement)
        os.utime(tracked, ns=(original.st_atime_ns, original.st_mtime_ns))
        self.assertEqual(tracked.stat().st_mtime_ns, original.st_mtime_ns)
        (self.repository / ".git/config").write_text(
            "[core]\n\ttrustctime = false\n\tcheckStat = minimal\n",
        )

        with self.assertRaisesRegex(ValueError, "dirty"):
            self.create()
        self.assertFalse(self.snapshot.exists())
        self.assertFalse(any("status" in command for command, _ in self.git.calls))
        self.assertFalse(any("bundle" in command for command, _ in self.git.calls))

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

    def test_extended_lfs_pointer_is_rejected_when_oid_and_size_follow_512_bytes(self):
        extensions = b"".join(
            f"ext-{index}-filter-{index} sha256:".encode("ascii")
            + bytes((ord("a") + index,)) * 64 + b"\n"
            for index in range(8)
        )
        pointer = (
            b"version https://git-lfs.github.com/spec/v1\n"
            + extensions
            + b"oid sha256:" + b"d" * 64 + b"\nsize 4096\n"
        )
        self.assertGreater(pointer.index(b"oid sha256:"), 512)
        self.assertLess(len(pointer), 1024)
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
