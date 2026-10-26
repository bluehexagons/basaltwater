"""Mocked lifecycle tests for the root-worker ext4 scratch adapter."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lib import build_scratch as scratch


REQUEST_ID = "a" * 32
SIZE = 256 * scratch.MIB
CAPACITY = SIZE - 4096


def _completed(command: list[str], returncode: int = 0, stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(command, returncode, stdout, "")


class MockNative:
    def __init__(self, root: Path):
        self.root = root
        self.calls: list[list[str]] = []
        self.mounted = False
        self.mount_record: dict[str, str | int] | None = None
        self.target: str | None = None
        self.image_path: str | None = None
        self.fail_mkfs = False
        self.fail_mount = False
        self.fail_umount = False
        self.keep_mounted_after_umount = False
        self.fail_findmnt_call: int | None = None
        self.findmnt_calls = 0
        self.mount_before_marker = False
        self.replace_mountpoint = False

    def __call__(self, command: list[str], **kwargs) -> subprocess.CompletedProcess:
        self.calls.append(command)
        executable = command[0]
        if executable == "/usr/bin/findmnt":
            self.findmnt_calls += 1
            if self.fail_findmnt_call == self.findmnt_calls:
                return _completed(command, 2, "")
            records = []
            if self.mounted:
                records = [self.mount_record or {
                    "id": 42,
                    "source": "/dev/loop7",
                    "fstype": "ext4",
                    "target": self.target,
                    "options": "rw,relatime,nodev,nosuid",
                    "maj:min": "7:7",
                }]
            return _completed(command, 0 if records else 1,
                              json.dumps({"filesystems": records}))
        if executable == "/usr/sbin/mkfs.ext4":
            marker = Path(command[-1]).parent / "identity.json"
            self.mount_before_marker = not marker.is_file()
            if self.replace_mountpoint:
                mountpoint = marker.parent / "fs"
                mountpoint.rmdir()
                mountpoint.symlink_to(self.root.parent, target_is_directory=True)
            return _completed(command, 1 if self.fail_mkfs else 0)
        if executable == "/usr/bin/mount":
            self.image_path = command[command.index("--source") + 1]
            self.target = command[command.index("--target") + 1]
            marker = Path(self.image_path).parent / "identity.json"
            self.mount_before_marker = self.mount_before_marker or not marker.is_file()
            if self.fail_mount:
                self.mounted = True  # Model an ambiguous command result.
                return _completed(command, 1)
            self.mounted = True
            return _completed(command)
        if executable == "/usr/bin/umount":
            if self.fail_umount:
                return _completed(command, 1)
            if not self.keep_mounted_after_umount:
                self.mounted = False
            return _completed(command)
        raise AssertionError(f"Unexpected native command: {command}")


class BuildScratchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "scratch-root"
        self.root.mkdir(mode=0o700)
        self.root.chmod(0o700)
        self.native = MockNative(self.root)
        self.require_root = scratch._require_root
        self._patches = [
            patch.object(scratch, "_require_root"),
            patch.object(scratch.subprocess, "run", side_effect=self.native),
            patch.object(scratch, "_device_numbers", return_value=(7, 7)),
            patch.object(scratch, "_loop_backing_file", side_effect=lambda _major, _minor: self.native.image_path),
            patch.object(scratch.os, "statvfs", return_value=SimpleNamespace(
                f_blocks=CAPACITY // 4096, f_frsize=4096,
            )),
        ]
        for mocked in self._patches:
            mocked.start()
            self.addCleanup(mocked.stop)

    def create(self, *, request_id: str = REQUEST_ID, size_bytes: int = SIZE) -> scratch.ScratchHandle:
        return scratch.create_scratch(str(self.root), request_id, size_bytes=size_bytes)

    def _paths(self) -> tuple[Path, Path, Path]:
        request_dir = self.root / REQUEST_ID
        return request_dir, request_dir / "scratch.ext4", request_dir / "identity.json"

    def _assert_evidence(self) -> None:
        request_dir, image, marker = self._paths()
        self.assertTrue(request_dir.is_dir())
        self.assertTrue(marker.is_file())
        self.assertTrue(image.is_file())
        self.assertEqual(stat.S_IMODE(request_dir.stat().st_mode), 0o700)

    def test_create_uses_exclusive_sparse_image_fixed_commands_and_observed_identity(self) -> None:
        handle = self.create()
        request_dir, image, marker_path = self._paths()
        marker = json.loads(marker_path.read_text())
        self.assertEqual(handle.mount_path, str(request_dir / "fs"))
        self.assertEqual(handle.size_bytes, SIZE)
        self.assertEqual(handle.capacity_bytes, CAPACITY)
        self.assertEqual(image.stat().st_size, SIZE)
        self.assertLess(image.stat().st_blocks * 512, SIZE)
        self.assertEqual(stat.S_IMODE(image.stat().st_mode), 0o600)
        self.assertEqual(image.stat().st_uid, os.geteuid())
        self.assertEqual(stat.S_IMODE(marker_path.stat().st_mode), 0o600)
        self.assertEqual(marker["request_id"], REQUEST_ID)
        self.assertEqual(marker["image_path"], str(image))
        self.assertEqual(marker["mount_path"], handle.mount_path)
        self.assertEqual(marker["mountpoint_dev"], (request_dir / "fs").stat().st_dev)
        self.assertEqual(marker["mountpoint_ino"], (request_dir / "fs").stat().st_ino)
        self.assertFalse(self.native.mount_before_marker)

        mkfs = next(command for command in self.native.calls if command[0] == "/usr/sbin/mkfs.ext4")
        self.assertEqual(mkfs, [
            "/usr/sbin/mkfs.ext4", "-F", "-q", "-m", "0", "-E",
            "root_owner=0:0,root_perms=0700", str(image),
        ])
        self.assertFalse(mkfs[-1].startswith("/dev/"))
        mount = next(command for command in self.native.calls if command[0] == "/usr/bin/mount")
        self.assertEqual(mount, [
            "/usr/bin/mount", "--types", "ext4", "--options", "loop,nodev,nosuid",
            "--source", str(image), "--target", handle.mount_path,
        ])
        findmnt = next(command for command in self.native.calls if command[0] == "/usr/bin/findmnt")
        self.assertIn("--mountpoint", findmnt)
        self.assertIn("ID,SOURCE,FSTYPE,TARGET,OPTIONS,MAJ:MIN", findmnt)
        self.assertEqual(oct(stat.S_IMODE(request_dir.stat().st_mode)), "0o700")

        with self.assertRaises(FileExistsError):
            self.create()
        self.assertEqual(sum(command[0] == "/usr/sbin/mkfs.ext4" for command in self.native.calls), 1)

    def test_invalid_size_and_request_id_are_rejected_before_native_work(self) -> None:
        invalid_sizes = (True, 0, scratch.MIN_SIZE_BYTES - 1,
                         scratch.MIN_SIZE_BYTES + 1, scratch.MAX_SIZE_BYTES + 1, 1.5)
        for size in invalid_sizes:
            with self.subTest(size=size), self.assertRaises(ValueError):
                self.create(size_bytes=size)
        for request_id in ("../" + REQUEST_ID, "A" * 32, "a" * 31, None):
            with self.subTest(request_id=request_id), self.assertRaises(ValueError):
                self.create(request_id=request_id)  # type: ignore[arg-type]
        self.assertEqual(self.native.calls, [])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_root_worker_gate_rejects_non_root(self) -> None:
        with patch.object(scratch.os, "geteuid", return_value=1000):
            with self.assertRaises(PermissionError):
                self.require_root()
        self.assertEqual(self.native.calls, [])

    def test_symlink_mode_owner_and_existing_state_rejections_do_not_run_commands(self) -> None:
        alias = Path(self.temporary.name) / "root-alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises((ValueError, OSError)):
            scratch.create_scratch(str(alias), REQUEST_ID, size_bytes=SIZE)

        self.root.chmod(0o755)
        with self.assertRaises(ValueError):
            self.create()
        self.root.chmod(0o700)

        with patch.object(scratch.os, "geteuid", return_value=os.geteuid() + 1):
            with self.assertRaises(ValueError):
                self.create()

        (self.root / REQUEST_ID).mkdir(mode=0o700)
        with self.assertRaises(FileExistsError):
            self.create()
        self.assertEqual(self.native.calls, [])

    def test_format_and_ambiguous_mount_failures_leave_identity_and_image(self) -> None:
        self.native.fail_mkfs = True
        with self.assertRaisesRegex(RuntimeError, "formatting failed"):
            self.create()
        self._assert_evidence()
        self.assertFalse(any(command[0] == "/usr/bin/mount" for command in self.native.calls))

        self.root.joinpath(REQUEST_ID).rename(self.root / (REQUEST_ID + "-held"))
        self.native.calls.clear()
        self.native.findmnt_calls = 0
        self.native.fail_mkfs = False
        self.native.fail_mount = True
        with self.assertRaisesRegex(RuntimeError, "mount is unconfirmed"):
            self.create()
        self._assert_evidence()
        self.assertTrue(self.native.mounted)
        self.assertFalse(any(command[0] == "/usr/bin/umount" for command in self.native.calls))

    def test_mount_lookup_failure_and_wrong_source_preserve_evidence(self) -> None:
        self.native.fail_findmnt_call = 1
        with self.assertRaisesRegex(RuntimeError, "findmnt failed"):
            self.create()
        self._assert_evidence()
        self.assertFalse(any(command[0] == "/usr/sbin/mkfs.ext4" for command in self.native.calls))

        self.root.joinpath(REQUEST_ID).rename(self.root / (REQUEST_ID + "-held"))
        self.native.calls.clear()
        self.native.findmnt_calls = 0
        self.native.fail_findmnt_call = None
        self.native.mount_record = {
            "id": 42,
            "source": "/dev/loop8",
            "fstype": "ext4",
            "target": str(self.root / REQUEST_ID / "fs"),
            "options": "rw,nodev,nosuid",
            "maj:min": "7:8",
        }
        with self.assertRaisesRegex(RuntimeError, "identity"):
            self.create()
        self._assert_evidence()
        self.assertTrue(self.native.mounted)

    def test_capacity_over_image_bound_keeps_mounted_evidence(self) -> None:
        with patch.object(scratch.os, "statvfs", return_value=SimpleNamespace(
                f_blocks=SIZE // 4096 + 1, f_frsize=4096)):
            with self.assertRaisesRegex(RuntimeError, "exceeds the image bound"):
                self.create()
        self._assert_evidence()
        self.assertTrue(self.native.mounted)
        self.assertFalse(any(command[0] == "/usr/bin/umount" for command in self.native.calls))

    def test_replaced_mountpoint_symlink_is_rejected_before_mount(self) -> None:
        self.native.replace_mountpoint = True
        with self.assertRaises((ValueError, OSError)):
            self.create()
        self._assert_evidence()
        self.assertTrue((self.root / REQUEST_ID / "fs").is_symlink())
        self.assertFalse(any(command[0] == "/usr/bin/mount" for command in self.native.calls))

    def test_release_requires_stopped_work_and_unmounts_once_before_cleanup(self) -> None:
        handle = self.create()
        request_dir, image, marker = self._paths()
        with self.assertRaisesRegex(ValueError, "stopped work"):
            handle.release(work_stopped=False)
        self.assertTrue(image.exists())
        self.assertTrue(marker.exists())
        self.assertTrue(self.native.mounted)
        self.assertFalse(any(command[0] == "/usr/bin/umount" for command in self.native.calls))

        handle.release(work_stopped=True)
        self.assertFalse(request_dir.exists())
        umounts = [command for command in self.native.calls if command[0] == "/usr/bin/umount"]
        self.assertEqual(umounts, [["/usr/bin/umount", "--", handle.mount_path]])
        self.assertFalse(self.native.mounted)

    def test_unmount_failure_retains_mount_image_marker_and_directory(self) -> None:
        handle = self.create()
        self.native.fail_umount = True
        with self.assertRaisesRegex(RuntimeError, "unmount is busy or unconfirmed"):
            handle.release(work_stopped=True)
        self._assert_evidence()
        self.assertTrue(self.native.mounted)
        self.assertEqual(sum(command[0] == "/usr/bin/umount" for command in self.native.calls), 1)

    def test_successful_umount_command_with_mount_still_observed_retains_state(self) -> None:
        handle = self.create()
        self.native.keep_mounted_after_umount = True
        with self.assertRaisesRegex(RuntimeError, "remains after unmount"):
            handle.release(work_stopped=True)
        self._assert_evidence()
        self.assertTrue(self.native.mounted)
        self.assertEqual(sum(command[0] == "/usr/bin/umount" for command in self.native.calls), 1)

    def test_image_replacement_or_findmnt_error_blocks_release(self) -> None:
        handle = self.create()
        request_dir, image, marker = self._paths()
        with image.open("rb") as previous:
            image.unlink()
            with image.open("wb") as replacement:
                replacement.truncate(SIZE)
            image.chmod(0o600)
            self.assertNotEqual(image.stat().st_ino, os.fstat(previous.fileno()).st_ino)
        with self.assertRaisesRegex(ValueError, "image identity changed"):
            handle.release(work_stopped=True)
        self.assertTrue(request_dir.exists())
        self.assertTrue(marker.exists())
        self.assertTrue(self.native.mounted)
        self.assertFalse(any(command[0] == "/usr/bin/umount" for command in self.native.calls))

        # Restore the original inode is not possible; a lookup failure is also
        # fail-closed and independently retains a valid live image.
        self.root.joinpath(REQUEST_ID).rename(self.root / (REQUEST_ID + "-held"))
        self.native.calls.clear()
        self.native.findmnt_calls = 0
        self.native.mounted = False
        self.native.mount_record = None
        handle = self.create(request_id="b" * 32)
        self.native.fail_findmnt_call = self.native.findmnt_calls + 1
        with self.assertRaisesRegex(RuntimeError, "observation failed|findmnt failed"):
            handle.release(work_stopped=True)
        self.assertTrue(Path(handle.mount_path).parent.exists())
        self.assertTrue(self.native.mounted)
        self.assertFalse(any(command[0] == "/usr/bin/umount" for command in self.native.calls))


if __name__ == "__main__":
    unittest.main()
