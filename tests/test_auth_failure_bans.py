"""Tests for managed Nginx authentication failure bans."""

from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import call, patch

from lib.auth_failure_bans import (
    configure_nginx_auth_failure_ban,
    remove_nginx_auth_failure_ban,
)


class AuthFailureBanTest(unittest.TestCase):
    def test_writes_bounded_jail_and_restarts_fail2ban(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            filter_path = os.path.join(temporary, "filter.conf")
            jail_dir = os.path.join(temporary, "jails")
            completed = SimpleNamespace(returncode=0, stdout="", stderr="")
            with (
                patch(
                    "lib.auth_failure_bans.FAIL2BAN_FILTER_PATH",
                    filter_path,
                ),
                patch("lib.auth_failure_bans.FAIL2BAN_JAIL_DIR", jail_dir),
                patch("lib.auth_failure_bans.install_package", return_value=True),
                patch(
                    "lib.auth_failure_bans.run",
                    return_value=completed,
                ) as runner,
            ):
                configure_nginx_auth_failure_ban(
                    "device-pairing",
                    "/var/log/nginx/pairing-auth-failures.log",
                )

            with open(filter_path, encoding="utf-8") as file_obj:
                filter_content = file_obj.read()
            with open(
                os.path.join(jail_dir, "basaltwater-device-pairing.local"),
                encoding="utf-8",
            ) as file_obj:
                jail_content = file_obj.read()
            self.assertIn("<HOST>", filter_content)
            self.assertIn("datepattern = {NONE}", filter_content)
            self.assertIn("maxretry = 5", jail_content)
            self.assertIn("findtime = 10m", jail_content)
            self.assertIn("bantime = 1h", jail_content)
            self.assertIn(
                "logpath = /var/log/nginx/pairing-auth-failures.log",
                jail_content,
            )
            self.assertEqual(
                [call.args[0] for call in runner.call_args_list],
                ["systemctl enable fail2ban", "systemctl restart fail2ban"],
            )

    def test_rejects_unmanaged_log_directory(self) -> None:
        with self.assertRaisesRegex(ValueError, "/var/log/nginx"):
            configure_nginx_auth_failure_ban("gogs", "/tmp/gogs.log")

    def test_removes_managed_jail_and_reloads_fail2ban(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            jail_path = os.path.join(temporary, "basaltwater-gogs.local")
            with open(jail_path, "w", encoding="utf-8") as file_obj:
                file_obj.write("stale")
            completed = SimpleNamespace(returncode=0, stdout="", stderr="")
            with (
                patch("lib.auth_failure_bans.FAIL2BAN_JAIL_DIR", temporary),
                patch(
                    "lib.auth_failure_bans.run",
                    return_value=completed,
                ) as runner,
            ):
                remove_nginx_auth_failure_ban("gogs")

            self.assertFalse(os.path.exists(jail_path))
            runner.assert_called_once_with(
                "fail2ban-client reload",
                check=False,
                capture_output=True,
            )


if __name__ == "__main__":
    unittest.main()
