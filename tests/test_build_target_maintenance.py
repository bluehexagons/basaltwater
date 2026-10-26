"""Protected target coordination, using real flocks only in temporary trees."""

from __future__ import annotations

from contextlib import contextmanager, ExitStack
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lib import build_target_maintenance as maintenance


@contextmanager
def root_view():
    """Model root ownership without changing a developer machine's ownership."""
    def owned(function):
        def observe(*args, **kwargs):
            info = function(*args, **kwargs)
            values = {name: getattr(info, name) for name in dir(info) if name.startswith("st_")}
            values.update(st_uid=0, st_gid=0)
            return SimpleNamespace(**values)
        return observe

    real_mkdir, real_fstat = os.mkdir, os.fstat

    def root_mkdir(path, mode=0o777, *, dir_fd=None):
        # Root can populate its read-only transport home. Model that single
        # privilege in the fixture while keeping the asserted modes real.
        parent_mode = real_fstat(dir_fd).st_mode & 0o7777 if dir_fd is not None else None
        if parent_mode is not None and not parent_mode & 0o200:
            os.fchmod(dir_fd, parent_mode | 0o200)
        try:
            return real_mkdir(path, mode, dir_fd=dir_fd)
        finally:
            if parent_mode is not None and not parent_mode & 0o200:
                os.fchmod(dir_fd, parent_mode)

    with ExitStack() as stack:
        for name in ("stat", "lstat", "fstat"):
            stack.enter_context(patch.object(os, name, side_effect=owned(getattr(os, name))))
        stack.enter_context(patch.object(os, "fchown"))
        stack.enter_context(patch.object(os, "mkdir", side_effect=root_mkdir))
        stack.enter_context(patch.object(os, "geteuid", return_value=0))
        yield


class BuildTargetMaintenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.base.chmod(0o700)
        self.path = self.base / ".maintenance.lock"
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(root_view())
        self.stack.enter_context(patch.object(maintenance, "LOCK_PATH", str(self.path)))

    def test_nonblocking_shared_requests_exclude_installer_and_keep_inode(self) -> None:
        with maintenance.target_lease(installer=True, create=True) as descriptor:
            maintenance.set_serving(descriptor, True)
        inode = self.path.stat().st_ino
        with maintenance.target_lease():
            with maintenance.target_lease():
                pass
            before = self.path.read_bytes()
            with self.assertRaisesRegex(RuntimeError, "active"):
                with maintenance.target_lease(installer=True):
                    self.fail("exclusive lease overlapped a request")
            self.assertEqual(self.path.read_bytes(), before)
        with maintenance.target_lease(installer=True):
            with self.assertRaisesRegex(RuntimeError, "active"):
                with maintenance.target_lease():
                    self.fail("request waited for enrollment")
        self.assertEqual(self.path.stat().st_ino, inode)

    def test_durable_hold_survives_exception_and_only_explicit_success_reopens(self) -> None:
        with self.assertRaises(KeyboardInterrupt):
            with maintenance.target_lease(installer=True, create=True):
                raise KeyboardInterrupt
        with self.assertRaisesRegex(RuntimeError, "reconciliation"):
            with maintenance.target_lease():
                self.fail("failed setup reopened authority")
        with maintenance.target_lease(installer=True) as descriptor:
            maintenance.set_serving(descriptor, True)
        with maintenance.target_lease():
            pass
        with maintenance.target_lease(installer=True) as descriptor:
            maintenance.set_serving(descriptor, False)
        with self.assertRaises(RuntimeError):
            with maintenance.target_lease():
                pass

    def test_local_maintenance_is_exclusive_ready_only_and_preserves_gate(self) -> None:
        with self.assertRaises(FileNotFoundError):
            with maintenance.target_maintenance():
                pass
        with maintenance.target_lease(installer=True, create=True) as descriptor:
            maintenance.set_serving(descriptor, True)
        inode, content = self.path.stat().st_ino, self.path.read_bytes()
        with maintenance.target_lease(), self.assertRaisesRegex(RuntimeError, "active"):
            with maintenance.target_maintenance():
                self.fail("local maintenance overlapped a typed request")
        with self.assertRaises(KeyboardInterrupt):
            with maintenance.target_maintenance():
                for context in (maintenance.target_lease(), maintenance.target_lease(installer=True),
                                maintenance.target_maintenance()):
                    with self.assertRaisesRegex(RuntimeError, "active"), context:
                        self.fail("operation overlapped local maintenance")
                raise KeyboardInterrupt
        self.assertEqual((self.path.stat().st_ino, self.path.read_bytes()), (inode, content))
        with maintenance.target_lease(installer=True) as descriptor:
            maintenance.set_serving(descriptor, False)
        with self.assertRaisesRegex(RuntimeError, "disabled"):
            with maintenance.target_maintenance():
                self.fail("maintenance reopened disabled enrollment")

    def test_lock_is_never_recreated_adopted_or_followed(self) -> None:
        with self.assertRaises(FileNotFoundError):
            with maintenance.target_lease(installer=True):
                pass
        with maintenance.target_lease(installer=True, create=True):
            pass
        with self.assertRaises(FileExistsError):
            with maintenance.target_lease(installer=True, create=True):
                pass
        for mode in (0o644, 0o660):
            self.path.chmod(mode)
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                with maintenance.target_lease(installer=True):
                    pass
        self.path.chmod(0o600)
        alias = self.base / "alias"
        os.link(self.path, alias)
        with self.assertRaises(ValueError):
            with maintenance.target_lease(installer=True):
                pass
        alias.unlink()
        self.path.write_bytes(b"invalid\n")
        with self.assertRaises(ValueError):
            with maintenance.target_lease(installer=True):
                pass
        self.path.unlink()
        self.path.symlink_to(alias)
        with self.assertRaises((OSError, ValueError)):
            with maintenance.target_lease(installer=True):
                pass

    def test_unsafe_ancestor_and_nonroot_file_are_rejected(self) -> None:
        with maintenance.target_lease(installer=True, create=True):
            pass
        self.base.chmod(0o777)
        with self.assertRaises(ValueError):
            with maintenance.target_lease(installer=True):
                pass

        self.base.chmod(0o700)
        real_fstat = maintenance.os.fstat

        def nonroot(descriptor):
            info = real_fstat(descriptor)
            return SimpleNamespace(**{**vars(info), "st_uid": 1000})

        with patch.object(maintenance.os, "fstat", side_effect=nonroot), self.assertRaises(ValueError):
            with maintenance.target_lease(installer=True):
                pass

    def test_protected_reads_are_bounded_regular_single_link_and_nofollow(self) -> None:
        path = self.base / "config"
        self.assertIsNone(maintenance.read_protected(str(path), mode=0o600, maximum=8))
        path.write_bytes(b"private")
        path.chmod(0o600)
        self.assertEqual(maintenance.read_protected(str(path), mode=0o600, maximum=8), b"private")
        with self.assertRaises(ValueError):
            maintenance.read_protected(str(path), mode=0o600, maximum=1)
        alias = self.base / "alias"
        os.link(path, alias)
        with self.assertRaises(ValueError):
            maintenance.read_protected(str(path), mode=0o600, maximum=8)
        alias.unlink()
        alias.symlink_to(path)
        with self.assertRaises((OSError, ValueError)):
            maintenance.read_protected(str(alias), mode=0o600, maximum=8)
        path.unlink()
        os.mkfifo(path, mode=0o600)
        with self.assertRaises(ValueError):
            maintenance.read_protected(str(path), mode=0o600, maximum=8)


if __name__ == "__main__":
    unittest.main()
