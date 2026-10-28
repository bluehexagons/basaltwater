"""Mocked Git contracts for pinned source snapshots and safe unpacking."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.build_protocol import DEFAULT_LIMITS, parse_build_request
from lib.build_source import (
    MAGIC,
    MAX_PROJECT_NPM_CONFIG_BYTES,
    SourceLimits,
    create_source_snapshot,
    unpack_source_snapshot,
)


UNSAFE_NPM_CONFIGS = (
    b"_authToken=secret-canary\n", b"//registry.npmjs.org/:_authToken=secret-canary\n",
    b"_auth=secret-canary\n", b"username=secret-canary\n", b"_password=secret-canary\n",
    b"email=secret-canary\n", b"always-auth=true\n", b"registry=https://example.test\n",
    b"@scope:registry=https://example.test\n", b"userconfig=/tmp/config\n",
    b"globalconfig=/tmp/config\n", b"prefix=/tmp/install\n", b"cache=/tmp/cache\n",
    b"cafile=/tmp/ca\n", b"script-shell=/bin/sh\n", b"strict-ssl=false\n",
    b"ignore-scripts=${POLICY}\n", b"ignore-scripts=$POLICY\n",
    b"ignore-scripts=true\\\nengine-strict=true\n", b"[section]\nignore-scripts=true\n",
    b"ignore-scripts[]=true\n", b"Ignore-Scripts=true\n", b"ignore-scripts\n",
    b"ignore-scripts=1\n", b'ignore-scripts="true"\n', b"ignore-scripts=true # comment\n",
    b"ignore-scripts=true\nignore-scripts=false\n", b"engine-strict=True\n",
    b"min-release-age=-1\n", b"min-release-age=1.5\n", b"min-release-age=525601\n",
    b"min-release-age=01\n", b"min-release-age=" + b"9" * 500 + b"\n",
    b"ignore-scripts=true\x00\n", b"ignore-scripts=true\rengine-strict=true\n",
    b"\xef\xbb\xbfignore-scripts=true\n", b"# non-ascii \xff\n",
    b"#" * 513, b"\n" * 128, b"#" * (MAX_PROJECT_NPM_CONFIG_BYTES + 1),
)


def git_blob_oid(content: bytes) -> str:
    header = b"blob " + str(len(content)).encode("ascii") + b"\0"
    return hashlib.sha1(header + content).hexdigest()


class FakeGit:
    def __init__(self, repository: Path, *, entries=None, untracked: bytes = b"",
                 ignored_untracked: bytes = b"", shallow: bool = False,
                 tags: bytes = b"cccccccccccccccccccccccccccccccccccccccc refs/tags/v1.2.3\n",
                 move_tag_during_bundle: bool = False, move_head_during_bundle: bool = False,
                 bundle_head_override: str | None = None,
                 filter_value: bytes = b"unspecified", staged: bytes = b""):
        self.repository = repository
        self.revision = "a" * 40
        self.head_revision = self.revision
        self.untracked = untracked
        self.ignored_untracked = ignored_untracked
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
        self.text_paths: set[str] = set()
        self.ident_paths: set[str] = set()
        self.encoding_paths: set[str] = set()
        self.normalization_paths: list[str] = []
        self.conversion_config: dict[str, str] = {}
        self.filter_driver_config: dict[str, str] = {}
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
                               "bundle", "check-attr", "ls-files", "init", "update-ref",
                               "config", "hash-object")
             if item in command),
            None,
        )
        if subcommand == "rev-parse":
            if "--show-toplevel" in command:
                result = os.fsencode(self.repository) + b"\n"
            elif "--is-shallow-repository" in command:
                result = b"true\n" if self.shallow else b"false\n"
            elif "--git-path" in command:
                if "objects" in command:
                    git_path = "objects"
                elif "attributes" in command:
                    git_path = "info/attributes"
                else:
                    git_path = "info/grafts"
                result = os.fsencode(self.repository / ".git" / git_path) + b"\n"
            else:
                if any(item.startswith("--git-dir=") for item in command):
                    result = self.history_refs["HEAD"].encode("ascii") + b"\n"
                else:
                    result = self.head_revision.encode("ascii") + b"\n"
        elif subcommand == "status":
            raise AssertionError("Snapshot preparation must not invoke Git status")
        elif subcommand == "ls-files":
            if "--others" in command:
                result = self.untracked
                if "--exclude-standard" not in command:
                    result += self.ignored_untracked
            elif "--stage" in command:
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
        elif subcommand == "config":
            name = command[-1]
            default = command[command.index("--default") + 1]
            value = self.filter_driver_config.get(
                name, self.conversion_config.get(name, default),
            )
            result = (value + "\n").encode("ascii")
        elif subcommand == "hash-object":
            if env.get("GIT_ATTR_SOURCE") != self.revision:
                raise AssertionError("Git normalization must use the pinned attribute source")
            path = next(item.split("=", 1)[1] for item in command if item.startswith("--path="))
            self.normalization_paths.append(path)
            content = input_data or b""
            if path in self.text_paths:
                content = content.replace(b"\r\n", b"\n")
            if path in self.ident_paths:
                content = re.sub(rb"\$Id: [0-9a-f]{40,64} \$", b"$Id$", content)
            if path in self.encoding_paths:
                content = content.decode("utf-16le").encode("utf-8")
            result = git_blob_oid(content).encode("ascii") + b"\n"
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

    def prepare_crlf_checkout(self):
        attributes = b"*.txt text eol=crlf\n"
        canonical = b"line one\nline two\n"
        self.git = FakeGit(self.repository, entries=[
            (".gitattributes", "100644", "blob", attributes),
            ("basaltwater.json", "100644", "blob", b"{}\n"),
            ("src/settings.txt", "100644", "blob", canonical),
        ])
        self.git.text_paths.add("src/settings.txt")
        (self.repository / ".gitattributes").write_bytes(attributes)
        (self.repository / "basaltwater.json").write_bytes(b"{}\n")
        (self.repository / "src/settings.txt").write_bytes(canonical.replace(b"\n", b"\r\n"))
        return canonical

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
        untracked_command = next(
            call for call in self.git.calls if "--others" in call[0]
        )
        self.assertIn("--exclude-standard", untracked_command[0])
        self.assertIn("-z", untracked_command[0])
        self.assertFalse(any("status" in command for command, _ in self.git.calls))
        env = untracked_command[1]["env"]
        self.assertEqual(env["GIT_CONFIG_NOSYSTEM"], "1")
        self.assertEqual(env["GIT_CONFIG_GLOBAL"], os.devnull)
        self.assertEqual(env["GIT_NO_LAZY_FETCH"], "1")
        self.assertNotIn("GIT_CONFIG_PARAMETERS", env)

    def prepare_npm_config(self, content, path=".npmrc"):
        self.git = FakeGit(self.repository, entries=[
            *FakeGit(self.repository).entries, (path, "100644", "blob", content),
        ])
        target = self.repository / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    def test_literal_project_npm_policy_is_preserved_with_protocol_and_history(self):
        content = b"# Project policy\r\nignore-scripts=true\r\n"
        nested = b"; policy\nengine-strict = false\nmin-release-age=525600\n"
        self.prepare_npm_config(content)
        self.git = FakeGit(self.repository, entries=[
            *self.git.entries, ("src/.npmrc", "100644", "blob", nested),
        ])
        (self.repository / "src/.npmrc").write_bytes(nested)
        self.source = self.create()
        checkout, history = self.unpack()
        self.assertEqual((Path(checkout) / ".npmrc").read_bytes(), content)
        self.assertEqual((Path(checkout) / "src/.npmrc").read_bytes(), nested)
        self.assertEqual(Path(history).read_bytes(), self.git.bundle)
        self.assertEqual(self.source["manifest_sha256"], hashlib.sha256(
            (self.repository / "basaltwater.json").read_bytes()).hexdigest())
        request = {
            "schema": 1, "request_id": "c" * 32, "project": "snapshot-test",
            "source": self.source,
            "platform": {"os": "linux", "architecture": "x86_64", "baseline": "debian-13"},
            "components": [{"name": "site", "type": "static", "artifacts": ["src/main.py"],
                            "commands": [], "env": {}}], "limits": dict(DEFAULT_LIMITS),
        }
        self.assertEqual(parse_build_request(request)["source"], self.source)
        bundle = next(command for command, _ in self.git.calls if "bundle" in command)
        self.assertIn("--filter=blob:none", bundle)

    def test_unsafe_npm_configuration_is_refused_without_input_in_errors(self):
        for index, content in enumerate(UNSAFE_NPM_CONFIGS):
            with self.subTest(case=index):
                self.prepare_npm_config(content)
                with self.assertRaisesRegex(ValueError, r"\.npmrc|byte limit") as caught:
                    self.create()
                self.assertNotIn("secret-canary", str(caught.exception))
                self.assertFalse(self.snapshot.exists())

    def test_npm_literal_values_and_exact_bounds_roundtrip(self):
        accepted = (
            b"", b"\n" * 127, b"#" * 512, (b"#" + b"a" * 510 + b"\n") * 16,
            b"ignore-scripts=false\nengine-strict=true\nmin-release-age=0",
        )
        for index, content in enumerate(accepted):
            with self.subTest(case=index):
                self.prepare_npm_config(content)
                self.snapshot = self.root / f"policy-{index}.bws"
                self.output = self.root / f"policy-{index}"
                self.source = self.create()
                checkout, _history = self.unpack()
                self.assertEqual((Path(checkout) / ".npmrc").read_bytes(), content)

    def test_growing_worktree_npm_read_stops_at_config_bound(self):
        self.prepare_npm_config(b"ignore-scripts=true\n")
        original_read = os.read
        npm_reads = []

        def growing_read(descriptor, size):
            if size == MAX_PROJECT_NPM_CONFIG_BYTES + 1:
                npm_reads.append(size)
                return b"#" * size
            return original_read(descriptor, size)

        with patch("lib.build_source.os.read", side_effect=growing_read):
            with self.assertRaisesRegex(ValueError, "byte limit"):
                self.create()
        self.assertEqual(npm_reads, [MAX_PROJECT_NPM_CONFIG_BYTES + 1])
        self.assertFalse(self.snapshot.exists())

    def test_committed_npm_bytes_are_validated_independently_of_worktree_checks(self):
        self.prepare_npm_config(b"_authToken=secret-canary\n")
        with patch("lib.build_source._check_clean_repository"), self.assertRaisesRegex(
            ValueError, "unsafe project configuration",
        ):
            self.create()
        self.assertFalse(self.snapshot.exists())

    def test_unpack_rejects_unsafe_npm_bytes_even_with_valid_checksums(self):
        safe = b"ignore-scripts=true\n"
        self.prepare_npm_config(safe)
        self.source = self.create()
        original = self.snapshot.read_bytes()
        offset = original.index(safe)
        for index, content in enumerate(UNSAFE_NPM_CONFIGS):
            with self.subTest(case=index):
                forged = (original[:offset - 8] + struct.pack("!Q", len(content))
                          + content + hashlib.sha256(content).digest()
                          + original[offset + len(safe) + 32:])
                self.snapshot.chmod(0o600)
                self.snapshot.write_bytes(forged)
                expected = {**self.source, "snapshot_sha256": hashlib.sha256(forged).hexdigest()}
                with self.assertRaises(ValueError) as caught:
                    self.unpack(expected)
                self.assertNotIn("secret-canary", str(caught.exception))
                self.assertFalse(self.output.exists())

    def test_npm_config_aliases_directories_and_other_credentials_remain_forbidden(self):
        for path in (".NPMRC", "src/.Npmrc", ".npmrc/child", ".netrc", "src/.pypirc"):
            with self.subTest(path=path):
                self.git = FakeGit(self.repository, entries=[
                    *FakeGit(self.repository).entries, (path, "100644", "blob", b""),
                ])
                with self.assertRaises(ValueError):
                    self.create()
                self.assertFalse(self.snapshot.exists())

    def test_npm_config_symlink_and_fifo_are_refused_without_reading(self):
        self.prepare_npm_config(b"ignore-scripts=true\n")
        target = self.repository / ".npmrc"
        for kind in ("symlink", "fifo"):
            with self.subTest(kind=kind):
                target.unlink()
                if kind == "symlink":
                    target.symlink_to(self.root / "outside")
                else:
                    os.mkfifo(target)
                with self.assertRaisesRegex(ValueError, "symlink or special"):
                    self.create()
                self.assertFalse(self.snapshot.exists())

    def test_unpack_refuses_npm_directory_and_case_alias(self):
        self.source = self.create()
        original = self.snapshot.read_bytes()
        # The first directory frame names src. Reject .npmrc before children.
        directory = b"D\x00" + struct.pack("!I", 3) + b"src"
        for name in (b".npmrc", b".NPMRC"):
            with self.subTest(name=name):
                replacement = b"D\x00" + struct.pack("!I", len(name)) + name
                forged = original.replace(directory, replacement, 1)
                self.assertNotEqual(forged, original)
                self.snapshot.chmod(0o600)
                self.snapshot.write_bytes(forged)
                expected = {**self.source, "snapshot_sha256": hashlib.sha256(forged).hexdigest()}
                with self.assertRaisesRegex(ValueError, r"npm|regular file"):
                    self.unpack(expected)
                self.assertFalse(self.output.exists())

    def test_clean_crlf_text_checkout_matches_its_normalized_blob(self):
        canonical = self.prepare_crlf_checkout()
        self.source = self.create()
        checkout, _history = self.unpack()
        self.assertEqual((Path(checkout) / "src/settings.txt").read_bytes(), canonical)
        self.assertIn("src/settings.txt", self.git.normalization_paths)
        self.assertFalse(any("status" in command for command, _ in self.git.calls))

    def test_same_size_crlf_text_edit_is_rejected_after_normalization(self):
        self.prepare_crlf_checkout()
        tracked = self.repository / "src/settings.txt"
        original = tracked.stat()
        replacement = b"line uno\r\nline two\r\n"
        self.assertEqual(len(replacement), original.st_size)
        tracked.write_bytes(replacement)
        os.utime(tracked, ns=(original.st_atime_ns, original.st_mtime_ns))
        self.snapshot = self.root / "changed-source.bws"

        with self.assertRaisesRegex(ValueError, "dirty"):
            self.create()
        self.assertIn("src/settings.txt", self.git.normalization_paths)
        self.assertFalse(any("status" in command for command, _ in self.git.calls))
        self.assertFalse(any("--others" in command for command, _ in self.git.calls))
        self.assertFalse(self.snapshot.exists())

    def test_ident_and_working_tree_encoding_checkouts_are_normalized(self):
        attributes = b"*.id ident\n*.utf16 working-tree-encoding=UTF-16LE\n"
        canonical_ident = b"$Id$\n"
        canonical_encoding = b"encoded text\n"
        expanded_ident = b"$Id: " + git_blob_oid(canonical_ident).encode("ascii") + b" $\n"
        encoded_worktree = canonical_encoding.decode("utf-8").encode("utf-16le")
        self.git = FakeGit(self.repository, entries=[
            (".gitattributes", "100644", "blob", attributes),
            ("basaltwater.json", "100644", "blob", b"{}\n"),
            ("src/value.id", "100644", "blob", canonical_ident),
            ("src/value.utf16", "100644", "blob", canonical_encoding),
        ])
        self.git.ident_paths.add("src/value.id")
        self.git.encoding_paths.add("src/value.utf16")
        (self.repository / ".gitattributes").write_bytes(attributes)
        (self.repository / "basaltwater.json").write_bytes(b"{}\n")
        (self.repository / "src/value.id").write_bytes(expanded_ident)
        (self.repository / "src/value.utf16").write_bytes(encoded_worktree)

        self.source = self.create()
        checkout, _history = self.unpack()
        checkout_path = Path(checkout)
        self.assertEqual((checkout_path / "src/value.id").read_bytes(), canonical_ident)
        self.assertEqual((checkout_path / "src/value.utf16").read_bytes(), canonical_encoding)
        self.assertIn("src/value.id", self.git.normalization_paths)
        self.assertIn("src/value.utf16", self.git.normalization_paths)

    def test_visible_untracked_file_is_rejected_before_source_or_history_copy(self):
        self.git.untracked = b"src/new.txt\0"
        (self.repository / "src/new.txt").write_text("visible untracked\n")
        with self.assertRaisesRegex(ValueError, "dirty"):
            self.create()
        self.assertFalse(self.snapshot.exists())
        self.assertFalse(any("cat-file" in command for command, _ in self.git.calls))
        untracked_command = next(
            call[0] for call in self.git.calls if "--others" in call[0]
        )
        self.assertIn("--exclude-standard", untracked_command)
        self.assertFalse(any("status" in command for command, _ in self.git.calls))

    def test_gitignored_untracked_files_do_not_affect_snapshot(self):
        self.git.ignored_untracked = b"ignored.tmp\0"
        (self.repository / "ignored.tmp").write_text("ignored\n")

        source = self.create()

        self.assertEqual(source["revision"], self.git.revision)
        untracked_command = next(
            call[0] for call in self.git.calls if "--others" in call[0]
        )
        self.assertIn("--exclude-standard", untracked_command)
        self.assertFalse(any("status" in command for command, _ in self.git.calls))

    def test_staged_and_filter_configured_repositories_are_rejected_safely(self):
        self.git.staged = b"staged"
        with self.assertRaisesRegex(ValueError, "dirty"):
            self.create()
        self.assertFalse(any("--others" in command for command, _ in self.git.calls))

        self.git = FakeGit(self.repository, filter_value=b"custom-filter")
        with self.assertRaisesRegex(ValueError, "filters are unsupported"):
            self.create()
        self.assertFalse(any("--others" in command for command, _ in self.git.calls))
        self.assertFalse(any("hash-object" in command for command, _ in self.git.calls))
        self.assertFalse(any("status" in command for command, _ in self.git.calls))

    def test_sentinel_filter_values_without_named_drivers_remain_allowed(self):
        for sentinel in (b"unspecified", b"unset"):
            with self.subTest(sentinel=sentinel):
                self.git = FakeGit(self.repository, filter_value=sentinel)
                self.snapshot = self.root / f"{sentinel.decode('ascii')}.bws"
                source = self.create()
                self.assertEqual(source["revision"], self.git.revision)

    def test_configured_sentinel_filter_drivers_are_rejected_before_untracked_enumeration(self):
        cases = ((b"unspecified", "clean"), (b"unset", "process"))
        for sentinel, action in cases:
            with self.subTest(sentinel=sentinel, action=action):
                self.git = FakeGit(self.repository, filter_value=sentinel)
                driver_key = f"filter.{sentinel.decode('ascii')}.{action}"
                self.git.filter_driver_config[driver_key] = "marker-helper"
                self.snapshot = self.root / f"blocked-{sentinel.decode('ascii')}.bws"

                with self.assertRaisesRegex(ValueError, "Ambiguous Git filter driver"):
                    self.create()

                commands = [command for command, _ in self.git.calls]
                self.assertFalse(any("status" in command for command in commands))
                self.assertFalse(any("--others" in command for command in commands))
                self.assertFalse(any("hash-object" in command for command in commands))
                self.assertFalse(any("bundle" in command for command in commands))
                self.assertFalse(self.snapshot.exists())

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
        self.assertFalse(any("--others" in command for command, _ in self.git.calls))
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
