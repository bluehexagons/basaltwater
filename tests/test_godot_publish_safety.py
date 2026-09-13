"""Regression tests for filesystem boundaries around Godot export processing."""

from __future__ import annotations

from contextlib import ExitStack
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common.service_tools import godot_web_publish as publisher


class TestGodotExportValidation(unittest.TestCase):
    def test_unsafe_lock_stops_export_without_changing_link_target(self) -> None:
        for kind in ("symlink", "hardlink", "fifo"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                project = root / "project"
                project.mkdir()
                (project / "project.godot").write_text('[application]\nconfig/name="Test"\n')
                games = root / "games"
                user_root = games / "agent"
                user_root.mkdir(parents=True)
                outside = root / "outside"
                outside.write_text("preserve")
                outside.chmod(0o644)
                lock = user_root / ".infra-tools-test.lock"
                if kind == "symlink":
                    lock.symlink_to(outside)
                elif kind == "hardlink":
                    os.link(outside, lock)
                else:
                    os.mkfifo(lock)
                with (
                    patch.object(publisher, "GAMES_ROOT", str(games)),
                    patch.object(publisher, "_current_account", return_value=SimpleNamespace(pw_name="agent", pw_uid=os.getuid())),
                    patch.object(publisher.subprocess, "run") as export,
                ):
                    args = publisher._parser().parse_args(["test", "--project", str(project)])
                    with self.assertRaises((OSError, RuntimeError)):
                        publisher._publish(args)
                    export.assert_not_called()
                self.assertEqual(outside.read_text(), "preserve")
                self.assertEqual(outside.stat().st_mode & 0o777, 0o644)
                self.assertEqual(list(user_root.iterdir()), [lock])

    def test_unreadable_subtree_stops_validation_before_permission_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = root / "unreadable"
            child.mkdir()
            real_scandir = os.scandir

            def scan(path):
                if os.fspath(path) == str(child):
                    raise PermissionError(13, "Permission denied", str(child))
                return real_scandir(path)

            with (
                patch.object(publisher.os, "scandir", side_effect=scan),
                patch.object(publisher.os, "chmod") as chmod,
            ):
                with self.assertRaisesRegex(RuntimeError, "Could not validate"):
                    publisher._make_export_readable(directory)
                chmod.assert_not_called()

    def test_unsafe_generated_files_are_rejected_before_writes(self) -> None:
        for name, kind, precompress in (
            ('game.wasm', 'symlink', True),
            ('game.wasm.gz', 'symlink', True),
            (publisher.METADATA_FILE, 'symlink', False),
            (publisher.METADATA_FILE, 'hardlink', False),
            ('game.wasm.gz', 'hardlink', True),
            ('game.pck', 'fifo', True),
        ):
            with self.subTest(name=name, kind=kind), ExitStack() as stack:
                root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
                project = root / 'project'
                project.mkdir()
                (project / 'project.godot').write_text('[application]\nconfig/name="Test"\n')
                games = root / 'games'
                user_root = games / 'agent'
                destination = user_root / 'test'
                destination.mkdir(parents=True)
                (destination / 'index.html').write_text('previous working game')
                outside = root / 'private'
                outside.write_text('do not change')
                outside.chmod(0o600)

                def export(command, **kwargs):
                    output = Path(command[-1]).parent
                    (output / 'index.html').write_text('new game')
                    (output / 'game.wasm').write_bytes(b'wasm')
                    target = output / name
                    if target.exists():
                        target.unlink()
                    if kind == 'symlink':
                        target.symlink_to(outside)
                    elif kind == 'hardlink':
                        os.link(outside, target)
                    else:
                        os.mkfifo(target)
                    return SimpleNamespace(returncode=0, stderr='')

                stack.enter_context(patch.object(publisher, 'GAMES_ROOT', str(games)))
                stack.enter_context(patch.object(publisher, '_current_account', return_value=SimpleNamespace(pw_name='agent', pw_uid=os.getuid())))
                stack.enter_context(patch.object(publisher.subprocess, 'run', side_effect=export))
                args = publisher._parser().parse_args(['test', '--project', str(project)])
                args.precompress = precompress
                with self.assertRaisesRegex(RuntimeError, 'unsafe'):
                    publisher._publish(args)
                self.assertEqual(outside.read_text(), 'do not change')
                self.assertEqual(outside.stat().st_mode & 0o777, 0o600)
                self.assertEqual((destination / 'index.html').read_text(), 'previous working game')

    def test_readability_pass_validates_every_file_before_chmod(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / 'private'
            outside.write_text('private')
            outside.chmod(0o600)
            export = root / 'export'
            export.mkdir()
            os.link(outside, export / 'linked.txt')
            with self.assertRaisesRegex(RuntimeError, 'unsafe'):
                publisher._make_export_readable(str(export))
            self.assertEqual(outside.stat().st_mode & 0o777, 0o600)
