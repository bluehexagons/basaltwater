"""Tests for routing managed service cleanup through a unit transaction."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from lib.systemd_service import cleanup_service, cleanup_systemd_unit


class TestCleanupFunctions(unittest.TestCase):
    @patch("lib.systemd_service.remove_units")
    def test_service_and_activators_share_one_removal(self, remove):
        cleanup_service("demo")
        remove.assert_called_once_with(
            ("demo.timer", "demo.path", "demo.service"), unit_dir="/etc/systemd/system",
        )

    @patch("lib.systemd_service.remove_units")
    def test_single_mount_removal(self, remove):
        cleanup_systemd_unit("mnt-share", "mount")
        remove.assert_called_once_with(("mnt-share.mount",), unit_dir="/etc/systemd/system")

    @patch("lib.systemd_service.remove_units")
    def test_invalid_names_and_types_do_not_start_removal(self, remove):
        for name in ("../demo", "demo\nother", "demo;other"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                cleanup_service(name)
        with self.assertRaises(ValueError):
            cleanup_systemd_unit("demo", "socket")
        remove.assert_not_called()


if __name__ == '__main__':
    unittest.main()
