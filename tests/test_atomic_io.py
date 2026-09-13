"""Tests for crash-safe text and JSON persistence helpers."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from lib.atomic_io import read_json_file, remove_file_durable, write_json_atomic, write_text_atomic


class TestAtomicIO(unittest.TestCase):
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
