"""Publication and ownership failures in panel Nginx setup."""

from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from common import web_panel_steps as panel


class WebPanelNginxSetupTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.site = self.root / "site"
        self.link = self.root / "enabled"
        self.previous = panel._NGINX_MARKER + "\n# previous site\n"
        self.desired = panel._NGINX_MARKER + "\n# desired site\n"
        self.enterContext(patch.multiple(panel, WEB_PANEL_NGINX_SITE=str(self.site),
                                         WEB_PANEL_NGINX_LINK=str(self.link)))
        self.run = self.enterContext(patch.object(panel, "run", return_value=
                                                  subprocess.CompletedProcess([], 0, stdout="", stderr="")))

    def test_unmanaged_enabled_entries_are_rejected_before_site_publication(self) -> None:
        for kind in ("file", "directory", "foreign-link", "dangling-link"):
            with self.subTest(kind=kind):
                self.site.write_text(self.previous)
                self.site.chmod(0o600)
                before = self.site.stat()
                target = self.root / "other-site"
                target.write_text("operator content\n")
                if kind == "file":
                    self.link.write_text("operator content\n")
                elif kind == "directory":
                    self.link.mkdir()
                else:
                    self.link.symlink_to(target if kind == "foreign-link" else self.root / "missing")
                try:
                    with patch.object(panel, "write_text_atomic", wraps=panel.write_text_atomic) as write:
                        with self.assertRaisesRegex(RuntimeError, "unmanaged.*Nginx link"):
                            panel._write_nginx_site(self.desired)
                    write.assert_not_called()
                    self.run.assert_not_called()
                    self.assertEqual(self.site.read_text(), self.previous)
                    self.assertEqual((self.site.stat().st_ino, self.site.stat().st_mode),
                                     (before.st_ino, before.st_mode))
                    self.assertEqual(target.read_text(), "operator content\n")
                finally:
                    if kind == "directory":
                        self.link.rmdir()
                    else:
                        self.link.unlink()

    def test_link_without_a_previous_managed_site_does_not_create_one(self) -> None:
        self.link.symlink_to(self.site)
        with self.assertRaisesRegex(RuntimeError, "unmanaged.*Nginx link"):
            panel._write_nginx_site(self.desired)
        self.assertFalse(self.site.exists())
        self.assertTrue(self.link.is_symlink())
        self.run.assert_not_called()

    def test_failed_symlink_creation_restores_previous_or_absent_site(self) -> None:
        for existing in (False, True):
            with self.subTest(existing=existing):
                if existing:
                    self.site.write_text(self.previous)
                with patch.object(panel.os, "symlink", side_effect=OSError("link creation failed")):
                    with self.assertRaisesRegex(OSError, "link creation failed"):
                        panel._write_nginx_site(self.desired)
                if existing:
                    self.assertEqual(self.site.read_text(), self.previous)
                else:
                    self.assertFalse(self.site.exists())
                self.assertFalse(self.link.is_symlink())
                self.run.assert_not_called()

    def test_publication_error_after_atomic_replace_restores_old_site(self) -> None:
        self.site.write_text(self.previous)
        self.link.symlink_to(self.site)
        write = panel.write_text_atomic

        def publish(path: str, content: str, **kwargs) -> None:
            write(path, content, **kwargs)
            if content == self.desired:
                raise OSError("publication sync failed")

        with patch.object(panel, "write_text_atomic", side_effect=publish):
            with self.assertRaisesRegex(OSError, "publication sync failed"):
                panel._write_nginx_site(self.desired)
        self.assertEqual(self.site.read_text(), self.previous)
        self.assertEqual(self.link.resolve(), self.site)
        self.run.assert_not_called()

    def test_unchanged_invalid_configuration_is_not_republished_or_reloaded(self) -> None:
        self.site.write_text(self.previous)
        self.link.symlink_to(self.site)
        before = self.site.stat()
        self.run.return_value.returncode = 1
        with self.assertRaisesRegex(RuntimeError, "Nginx rejected"):
            panel._write_nginx_site(self.previous)
        self.assertEqual(self.site.stat().st_ino, before.st_ino)
        self.run.assert_called_once_with("nginx -t", check=False, capture_output=True)

    def test_first_setup_failure_removes_only_its_created_site_and_link(self) -> None:
        for failed in ("nginx -t", "systemctl reload nginx"):
            with self.subTest(failed=failed):
                def run(command, **kwargs):
                    if command == failed:
                        raise OSError("command failed")
                    return subprocess.CompletedProcess([], 0, stdout="", stderr="")

                self.run.side_effect = run
                with self.assertRaisesRegex(OSError, "command failed"):
                    panel._write_nginx_site(self.desired)
                self.assertFalse(self.site.exists())
                self.assertFalse(self.link.is_symlink())

    def test_successful_setup_validates_reloads_and_then_preserves_unchanged_files(self) -> None:
        self.assertTrue(panel._write_nginx_site(self.desired))
        self.assertEqual(self.site.read_text(), self.desired)
        self.assertEqual(self.link.resolve(), self.site)
        self.assertEqual([call.args[0] for call in self.run.call_args_list],
                         ["nginx -t", "systemctl reload nginx"])
        before = self.site.stat()
        self.run.reset_mock()
        self.assertFalse(panel._write_nginx_site(self.desired))
        self.assertEqual(self.site.stat().st_ino, before.st_ino)
        self.run.assert_called_once_with("nginx -t", check=False, capture_output=True)


if __name__ == "__main__":
    unittest.main()
