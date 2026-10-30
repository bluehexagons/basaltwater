"""Private snapshot and transfer verification using disposable local files."""

from __future__ import annotations

import copy
import hashlib
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib import build_artifacts
from lib.build_artifacts import MAGIC, create_artifact, unpack_artifact
from lib.build_protocol import encode_document
from tests.test_build_protocol import build_request


class BuildArtifactsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.workspace = self.root / "workspace"
        (self.workspace / "bin").mkdir(parents=True)
        (self.workspace / "bin/api").write_bytes(b"binary-content")
        (self.workspace / "bin/api").chmod(0o6751)
        (self.workspace / "private.txt").write_text("not-exported")
        self.request = build_request()
        self.bundle = self.root / "release.bwa"
        self.destination = self.root / "release"

    def package(self):
        return create_artifact(str(self.workspace), self.request, str(self.bundle),
                               toolchains={"go": "go1.26.2"})

    def unpack(self, digest):
        return unpack_artifact(str(self.bundle), self.request, str(self.destination),
                               expected_sha256=digest)

    def test_round_trip_selects_outputs_preserves_layout_and_normalizes_modes(self):
        info = self.package()
        self.assertEqual(info["sha256"], hashlib.sha256(self.bundle.read_bytes()).hexdigest())
        self.assertEqual(info["size"], self.bundle.stat().st_size)
        self.assertEqual(self.unpack(info["sha256"]), info["descriptor"])
        self.assertEqual((self.destination / "bin/api").read_bytes(), b"binary-content")
        self.assertFalse((self.destination / "private.txt").exists())
        self.assertEqual(stat.S_IMODE((self.destination / "bin/api").stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE(self.destination.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.bundle.stat().st_mode), 0o600)

    def test_multiple_components_deduplicate_overlapping_exports(self):
        (self.workspace / "site/assets").mkdir(parents=True)
        (self.workspace / "site/index.html").write_text("<p>test</p>")
        (self.workspace / "site/assets/style.css").write_text("body{}")
        self.request["components"].append({
            "name": "site", "type": "static", "artifacts": ["site", "site/assets"],
            "commands": [], "env": {},
        })
        info = self.package()
        self.unpack(info["sha256"])
        paths = [entry["path"] for entry in info["descriptor"]["entries"]]
        self.assertEqual(len(paths), len(set(paths)))
        self.assertEqual((self.destination / "site/assets/style.css").read_text(), "body{}")

    def test_root_static_export_omits_git_and_control_metadata(self):
        self.request["components"] = [{"name": "site", "type": "static",
                                       "artifacts": ["."], "commands": [], "env": {}}]
        (self.workspace / ".git").mkdir()
        (self.workspace / ".git/config").write_text("credential-canary")
        (self.workspace / ".gitmodules").write_text("submodule-url-canary")
        (self.workspace / ".gitattributes").write_text("* filter=lfs")
        (self.workspace / ".gitignore").write_text("ignored-private-file")
        (self.workspace / ".git-credentials").symlink_to("/missing")
        (self.workspace / "bin/.GIT").mkdir()
        (self.workspace / "empty").mkdir()
        info = self.package()
        self.unpack(info["sha256"])
        self.assertTrue((self.destination / "empty").is_dir())
        self.assertFalse((self.destination / ".git").exists())
        self.assertNotIn(b"credential-canary", self.bundle.read_bytes())
        self.assertNotIn(b"submodule-url-canary", self.bundle.read_bytes())
        self.assertFalse((self.destination / ".gitattributes").exists())
        self.assertFalse((self.destination / ".gitignore").exists())

    def test_inventory_comes_from_private_snapshot_after_workspace_changes(self):
        original = build_artifacts._snapshot
        def change_workspace(*args):
            entries = original(*args)
            (self.workspace / "bin/api").write_bytes(b"later-data")
            return entries
        with patch.object(build_artifacts, "_snapshot", side_effect=change_workspace):
            info = self.package()
        self.unpack(info["sha256"])
        self.assertEqual((self.destination / "bin/api").read_bytes(), b"binary-content")

    def test_empty_and_multichunk_files_have_exact_inventory_and_payloads(self):
        empty = self.workspace / "bin/empty"
        empty.write_bytes(b"")
        payload = b"\x00\xff\n" * 19
        (self.workspace / "bin/api").write_bytes(payload)
        self.request["components"][0]["artifacts"] = ["bin"]
        with patch.object(build_artifacts, "_CHUNK", 7):
            info = self.package()
            self.unpack(info["sha256"])
        entries = {entry["path"]: entry for entry in info["descriptor"]["entries"]}
        for name, content in (("bin/empty", b""), ("bin/api", payload)):
            with self.subTest(path=name):
                self.assertEqual(entries[name]["size"], len(content))
                self.assertEqual(entries[name]["sha256"], hashlib.sha256(content).hexdigest())
                self.assertEqual((self.destination / name).read_bytes(), content)

    def test_symlinks_at_export_leaf_parent_and_source_root_are_rejected(self):
        for kind in ("leaf", "parent", "root"):
            with self.subTest(kind=kind):
                source = self.root / ("source-" + kind)
                source.mkdir()
                if kind == "leaf":
                    (source / "bin").mkdir()
                    (source / "bin/api").symlink_to(self.workspace / "bin/api")
                elif kind == "parent":
                    (source / "bin").symlink_to(self.workspace / "bin", target_is_directory=True)
                else:
                    source.rmdir()
                    source.symlink_to(self.workspace, target_is_directory=True)
                with self.assertRaises((ValueError, OSError)):
                    create_artifact(str(source), self.request, str(self.bundle))
                self.assertFalse(self.bundle.exists())
        self.assertEqual(list(self.root.glob(".artifact-*")), [])

    def test_hardlinks_and_special_files_cannot_enter_bundle(self):
        binary = self.workspace / "bin/api"
        linked = self.root / "hardlink"
        os.link(binary, linked)
        with self.assertRaisesRegex(ValueError, "hard links"):
            self.package()
        linked.unlink()
        binary.unlink()
        os.mkfifo(binary)
        with self.assertRaisesRegex(ValueError, "special file"):
            self.package()

    def test_snapshot_enforces_byte_and_entry_limits_and_cleans_up(self):
        for key, limit in (("artifact_bytes", 2), ("artifact_entries", 1)):
            with self.subTest(key=key):
                self.request = build_request()
                self.request["limits"][key] = limit
                with self.assertRaisesRegex(ValueError, "limit"):
                    self.package()
                self.assertFalse(self.bundle.exists())
                self.assertEqual(list(self.root.glob(".artifact-*")), [])

    def test_changed_file_during_snapshot_is_not_published(self):
        original = build_artifacts.os.fstat
        calls = 0
        def mutate(descriptor):
            nonlocal calls
            info = original(descriptor)
            if stat.S_ISREG(info.st_mode):
                calls += 1
                if calls == 2:
                    (self.workspace / "bin/api").write_bytes(b"mutated")
                    info = original(descriptor)
            return info
        with patch.object(build_artifacts.os, "fstat", side_effect=mutate):
            with self.assertRaisesRegex(ValueError, "changed"):
                self.package()
        self.assertFalse(self.bundle.exists())

    def test_existing_bundle_or_destination_is_never_overwritten(self):
        info = self.package()
        before = self.bundle.read_bytes()
        with self.assertRaises(FileExistsError):
            self.package()
        self.assertEqual(self.bundle.read_bytes(), before)
        self.destination.mkdir()
        (self.destination / "sentinel").write_text("keep")
        with self.assertRaises(FileExistsError):
            self.unpack(info["sha256"])
        self.assertEqual((self.destination / "sentinel").read_text(), "keep")

    def test_private_umask_does_not_restrict_nested_release_directories(self):
        previous = os.umask(0o077)
        try:
            info = self.package()
            self.unpack(info["sha256"])
        finally:
            os.umask(previous)
        self.assertEqual(stat.S_IMODE((self.destination / "bin").stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE(self.destination.stat().st_mode), 0o700)

    def test_racing_destination_directory_is_not_replaced(self):
        info = self.package()
        original = build_artifacts._publish_tree
        created_inode = None
        def create_destination(parent, source, destination):
            nonlocal created_inode
            os.mkdir(destination, mode=0o700, dir_fd=parent)
            created_inode = os.stat(destination, dir_fd=parent).st_ino
            original(parent, source, destination)
        with patch.object(build_artifacts, "_publish_tree", side_effect=create_destination):
            with self.assertRaises(FileExistsError):
                self.unpack(info["sha256"])
        self.assertEqual(self.destination.stat().st_ino, created_inode)
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_public_parent_and_symlink_parent_cannot_receive_artifacts(self):
        public = self.root / "public"
        public.mkdir(mode=0o755)
        with self.assertRaisesRegex(ValueError, "private"):
            create_artifact(str(self.workspace), self.request, str(public / "bundle"))
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            create_artifact(str(self.workspace), self.request, str(alias / "bundle"))

    def test_bundle_requires_admitted_inputs_and_expected_digest(self):
        info = self.package()
        for changed in ("source", "command", "digest"):
            with self.subTest(changed=changed):
                request = copy.deepcopy(self.request)
                if changed == "source":
                    request["source"]["revision"] = "e" * 40
                elif changed == "command":
                    request["components"][0]["commands"] = ["false"]
                digest = "0" * 64 if changed == "digest" else info["sha256"]
                with self.assertRaises(ValueError):
                    unpack_artifact(str(self.bundle), request, str(self.destination),
                                    expected_sha256=digest)
                self.assertFalse(self.destination.exists())

    def test_corruption_truncation_and_trailing_data_leave_no_release(self):
        info = self.package()
        original = self.bundle.read_bytes()
        for content in (original[:-1], original[:-1] + bytes([original[-1] ^ 1]),
                        original + b"extra", b"invalid", MAGIC + (2**63).to_bytes(8, "big")):
            with self.subTest(length=len(content)):
                self.bundle.write_bytes(content)
                with self.assertRaises(ValueError):
                    self.unpack(info["sha256"])
                self.assertFalse(self.destination.exists())
                self.assertEqual(list(self.root.glob(".artifact-*")), [])

    def test_untrusted_descriptor_paths_and_symlink_bundle_are_rejected(self):
        info = self.package()
        descriptor = info["descriptor"]
        descriptor["entries"][-1]["path"] = "../escaped"
        encoded = encode_document(descriptor)
        self.bundle.write_bytes(MAGIC + len(encoded).to_bytes(8, "big") + encoded + b"binary-content")
        with self.assertRaises(ValueError):
            self.unpack(hashlib.sha256(self.bundle.read_bytes()).hexdigest())
        self.assertFalse((self.root.parent / "escaped").exists())
        self.bundle.unlink()
        self.bundle.symlink_to(self.workspace / "bin/api")
        with self.assertRaises(OSError):
            self.unpack(info["sha256"])


if __name__ == "__main__":
    unittest.main()
