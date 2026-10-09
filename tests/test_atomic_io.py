"""Tests for crash-safe text and JSON persistence helpers."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from lib.atomic_io import fsync_tree, read_json_file, remove_file_durable, rename_path_durable, write_bytes_atomic, write_json_atomic, write_text_atomic


class TestAtomicIO(unittest.TestCase):
    def test_new_parent_entries_are_synced_before_publishing_state(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = os.path.join(directory, "state", "app")
            target = os.path.join(parent, "operation.json")
            synced = []

            def sync(path):
                if path != parent:
                    self.assertFalse(os.path.exists(target))
                synced.append(path)

            with patch("lib.atomic_io._fsync_directory", side_effect=sync):
                write_json_atomic(target, {"phase": "applying"})
            self.assertEqual(synced, [directory, os.path.dirname(parent), parent])

    def test_binary_snapshot_contents_and_permissions_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "snapshot")
            write_bytes_atomic(path, b"\xff\x00private", mode=0o640, uid=os.getuid(), gid=os.getgid())
            with open(path, "rb") as stream:
                self.assertEqual(stream.read(), b"\xff\x00private")
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o640)

    def test_invalid_json_constants_and_duplicate_keys_are_rejected_without_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "state.json")
            for content in ('{"password": "private", "password": "changed"}',
                            '{"value": NaN}', '{"value": Infinity}', '{"value": 1e10000}'):
                with self.subTest(content=content):
                    write_text_atomic(path, content)
                    with self.assertRaises(ValueError) as caught:
                        read_json_file(path)
                    self.assertNotIn("private", str(caught.exception))
                    with open(path) as stream:
                        self.assertEqual(stream.read(), content)

    def test_nonfinite_write_preserves_existing_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "state.json")
            write_json_atomic(path, {"valid": True})
            with self.assertRaises(ValueError):
                write_json_atomic(path, {"value": float("nan")})
            self.assertEqual(read_json_file(path), {"valid": True})

    def test_rename_syncs_both_parents_after_move(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "source")
            target_parent = os.path.join(directory, "nested")
            os.mkdir(source)
            os.mkdir(target_parent)
            target = os.path.join(target_parent, "destination")
            parents = []

            def sync(parent):
                self.assertFalse(os.path.exists(source))
                self.assertTrue(os.path.isdir(target))
                parents.append(parent)

            with patch("lib.atomic_io._fsync_directory", side_effect=sync):
                rename_path_durable(source, target)
            self.assertEqual(parents, [directory, target_parent])

    def test_release_flushes_files_before_directories_without_following_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = os.path.join(directory, "release")
            child = os.path.join(root, "nested")
            os.makedirs(child)
            paths = [root, child, os.path.join(root, "file"), os.path.join(child, "file"),
                     os.path.join(directory, "outside")]
            for path in paths[2:]:
                with open(path, "w") as stream:
                    stream.write("contents")
            os.symlink(paths[-1], os.path.join(root, "external-file"))
            os.symlink(directory, os.path.join(child, "external-directory"))
            names = {os.stat(path).st_ino: path for path in paths}
            flushed = []
            with patch("lib.atomic_io.os.fsync", side_effect=lambda fd: flushed.append(names[os.fstat(fd).st_ino])):
                fsync_tree(root)
            self.assertEqual(set(flushed), set(paths[:-1]))
            self.assertLess(flushed.index(paths[3]), flushed.index(child))
            self.assertLess(flushed.index(child), flushed.index(root))
            self.assertLess(flushed.index(paths[2]), flushed.index(root))

    def test_release_flush_rejects_symlink_root_and_special_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = os.path.join(directory, "release")
            os.mkdir(root)
            link = os.path.join(directory, "link")
            os.symlink(root, link)
            with self.assertRaisesRegex(ValueError, "not a link"):
                fsync_tree(link)
            os.mkfifo(os.path.join(root, "pipe"))
            script = (
                'from lib.atomic_io import fsync_tree\nimport sys\n'
                'try:\n    fsync_tree(sys.argv[1])\n'
                'except ValueError:\n    sys.exit(7)\n'
            )
            result = subprocess.run([sys.executable, '-c', script, root], capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 7, result.stderr)

    def test_json_reader_rejects_unsafe_paths_and_bounds_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'data.json')
            write_text_atomic(path, '"é"')
            self.assertEqual(read_json_file(path, max_bytes=4), 'é')
            with self.assertRaisesRegex(ValueError, 'exceeds'):
                read_json_file(path, max_bytes=3)
            link = os.path.join(directory, 'link')
            os.symlink(path, link)
            with self.assertRaises(OSError):
                read_json_file(link)
            with self.assertRaisesRegex(ValueError, 'regular file'):
                read_json_file(directory)
            fifo = os.path.join(directory, 'fifo')
            os.mkfifo(fifo)
            script = (
                'from lib.atomic_io import read_json_file\nimport sys\n'
                'try:\n    read_json_file(sys.argv[1])\n'
                'except ValueError:\n    sys.exit(7)\n'
            )
            result = subprocess.run([sys.executable, '-c', script, fifo], capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 7, result.stderr)

    def test_ownership_failure_preserves_old_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'config')
            write_text_atomic(path, 'old')
            with patch('lib.atomic_io.os.fchown', side_effect=PermissionError('ownership failed')):
                with self.assertRaises(PermissionError):
                    write_text_atomic(path, 'new', uid=0, gid=100, mode=0o640)
            with open(path) as stream:
                self.assertEqual(stream.read(), 'old')
            self.assertEqual(os.listdir(directory), ['config'])

    def test_json_write_is_complete_and_restrictive(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "state.json")
            write_json_atomic(path, {"answer": 42})

            with open(path, encoding="utf-8") as file_obj:
                self.assertEqual(json.load(file_obj), {"answer": 42})
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(
                [name for name in os.listdir(tmpdir) if name != "state.json"],
                [],
            )

    def test_replace_failure_preserves_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "state.json")
            write_text_atomic(path, "old\n")

            with patch("lib.atomic_io.os.replace", side_effect=OSError("replace failed")):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    write_text_atomic(path, "new\n")

            with open(path, encoding="utf-8") as file_obj:
                self.assertEqual(file_obj.read(), "old\n")
            self.assertEqual(os.listdir(tmpdir), ["state.json"])

    def test_parent_directory_is_created(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "nested", "state.json")
            write_text_atomic(path, "content\n", mode=0o640)

            with open(path, encoding="utf-8") as file_obj:
                self.assertEqual(file_obj.read(), "content\n")
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o640)

    def test_durable_remove_reports_presence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "state.json")
            write_text_atomic(path, "content\n")

            self.assertTrue(remove_file_durable(path))
            self.assertFalse(remove_file_durable(path))
            self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
