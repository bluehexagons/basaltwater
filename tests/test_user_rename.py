"""Focused tests for the target-side user migration helper."""

from __future__ import annotations

import json
import os
import pwd
import tempfile
import unittest
from unittest.mock import patch

from lib import user_rename


class TestUserRenameHelpers(unittest.TestCase):
    def test_rewrite_setup_config_updates_username_and_home_paths(self):
        config = {
            "username": "olduser",
            "agent_workspace": "/home/olduser/repos",
            "sync_specs": [["/home/olduser/data", "/srv/backup", "daily"]],
            "share_credentials": [["olduser", "secret"]],
            "notify_specs": [["webhook", "/home/olduser/should-not-change"]],
            "gogs": ["git.example.com:3000", "/home/olduser/gogs"],
        }

        updated = user_rename._rewrite_setup_config(
            config,
            "olduser",
            "newuser",
            "/home/olduser",
            "/home/newuser",
        )

        self.assertEqual(updated["username"], "newuser")
        self.assertEqual(updated["agent_workspace"], "/home/newuser/repos")
        self.assertEqual(updated["sync_specs"][0][0], "/home/newuser/data")
        self.assertEqual(updated["share_credentials"][0][0], "olduser")
        self.assertEqual(updated["notify_specs"][0][1], "/home/olduser/should-not-change")
        self.assertEqual(updated["gogs"][1], "/home/newuser/gogs")

    def test_managed_unit_detection_rejects_unknown_references(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            managed = os.path.join(tmpdir, "auto-update-node.service")
            unmanaged = os.path.join(tmpdir, "custom.service")
            with open(managed, "w", encoding="utf-8") as file_obj:
                file_obj.write("User=olduser\n")
            with open(unmanaged, "w", encoding="utf-8") as file_obj:
                file_obj.write("User=olduser\n")
            with patch.object(user_rename, "SYSTEMD_DIR", tmpdir):
                managed_paths, unmanaged_paths = user_rename._managed_unit_files(
                    "olduser",
                    "/home/olduser",
                )

        self.assertEqual(managed_paths, [managed])
        self.assertEqual(unmanaged_paths, [unmanaged])

    def test_syncthing_service_is_a_managed_unit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            managed = os.path.join(tmpdir, "basaltwater-syncthing.service")
            with open(managed, "w", encoding="utf-8") as file_obj:
                file_obj.write("User=olduser\n")
            with patch.object(user_rename, "SYSTEMD_DIR", tmpdir):
                managed_paths, unmanaged_paths = user_rename._managed_unit_files(
                    "olduser",
                    "/home/olduser",
                )

        self.assertEqual(managed_paths, [managed])
        self.assertEqual(unmanaged_paths, [])

    def test_codex_auth_service_and_timer_are_managed_together(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = os.path.join(tmpdir, "codex-auth-maintenance.service")
            timer = os.path.join(tmpdir, "codex-auth-maintenance.timer")
            with open(service, "w", encoding="utf-8") as file_obj:
                file_obj.write("User=olduser\nEnvironment=HOME=/home/olduser\n")
            with open(timer, "w", encoding="utf-8") as file_obj:
                file_obj.write("OnCalendar=daily\n")
            with patch.object(user_rename, "SYSTEMD_DIR", tmpdir):
                managed_paths, unmanaged_paths = user_rename._managed_unit_files(
                    "olduser",
                    "/home/olduser",
                )

        self.assertEqual(managed_paths, [service, timer])
        self.assertEqual(unmanaged_paths, [])

    def test_managed_unit_rewrite_protects_old_username_in_new_home(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            unit_path = os.path.join(tmpdir, "auto-update-node.service")
            with open(unit_path, "w", encoding="utf-8") as file_obj:
                file_obj.write("WorkingDirectory=/srv/olduser-data\nUser=olduser\n")
            manifest = {
                "old_username": "olduser",
                "new_username": "newuser",
                "new_home": "/srv/olduser-data",
                "managed_units": [{"path": unit_path}],
            }

            user_rename._rewrite_managed_units(
                manifest,
                "/home/olduser",
                "/srv/olduser-data",
            )
            with open(unit_path, encoding="utf-8") as file_obj:
                content = file_obj.read()
            user_rename._verify_managed_rewrites(manifest, "/home/olduser")

        self.assertIn("WorkingDirectory=/srv/olduser-data", content)
        self.assertIn("User=newuser", content)

    def test_t3_user_service_paths_follow_a_moved_home(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old_home = "/home/olduser"
            new_home = os.path.join(tmpdir, "newuser")
            service_dir = os.path.join(new_home, ".config", "systemd", "user")
            drop_in_dir = os.path.join(service_dir, "t3code.service.d")
            os.makedirs(drop_in_dir)
            service = os.path.join(service_dir, "t3code.service")
            drop_in = os.path.join(drop_in_dir, "basaltwater.conf")
            with open(service, "w", encoding="utf-8") as file_obj:
                file_obj.write(f"ExecStart={old_home}/.t3/runtime/service-launcher.mjs\n")
            with open(drop_in, "w", encoding="utf-8") as file_obj:
                file_obj.write(f"WorkingDirectory={old_home}/repos\n")

            changed = user_rename._rewrite_managed_home_files(old_home, new_home)

            self.assertEqual(set(changed), {service, drop_in})
            for path in (service, drop_in):
                with open(path, encoding="utf-8") as file_obj:
                    content = file_obj.read()
                self.assertIn(new_home, content)
                self.assertNotIn(old_home, content)

    def test_t3_user_service_restarts_through_renamed_account(self):
        with tempfile.TemporaryDirectory() as home:
            service = os.path.join(
                home,
                ".config",
                "systemd",
                "user",
                "t3code.service",
            )
            os.makedirs(os.path.dirname(service))
            with open(service, "w", encoding="utf-8") as file_obj:
                file_obj.write("# upstream managed\n")
            completed = type("Completed", (), {"returncode": 0})()
            with patch.object(user_rename, "_run", return_value=completed) as run:
                user_rename._restore_t3_user_service("newuser", 1000, home)

            commands = [call.args[0] for call in run.call_args_list]
            self.assertEqual(commands[0], ["systemctl", "start", "user@1000.service"])
            self.assertIn("newuser", commands[1])
            self.assertEqual(
                commands[-1][-3:],
                ["is-active", "--quiet", "t3code.service"],
            )

    def test_home_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            real_home = os.path.join(tmpdir, "real-home")
            linked_home = os.path.join(tmpdir, "linked-home")
            os.mkdir(real_home)
            os.symlink(real_home, linked_home)
            account = pwd.struct_passwd(
                ("olduser", "x", os.getuid(), os.getgid(), "", linked_home, "/bin/bash")
            )

            with self.assertRaisesRegex(user_rename.RenameError, "symlink"):
                user_rename._home_for_manifest(
                    {
                        "old_username": "olduser",
                        "new_username": "newuser",
                        "keep_home": True,
                    },
                    account,
                )

    def test_managed_smb_credentials_stop_at_mount_option_comma(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            credential_dir = os.path.join(tmpdir, "credentials")
            os.mkdir(credential_dir)
            old_credential = os.path.join(credential_dir, "credentials-home-olduser")
            new_credential = os.path.join(credential_dir, "credentials-home-newuser")
            with open(old_credential, "w", encoding="utf-8") as file_obj:
                file_obj.write("username=shareuser\npassword=secret\n")
            unit_path = os.path.join(tmpdir, "home-olduser.mount")
            with open(unit_path, "w", encoding="utf-8") as file_obj:
                file_obj.write(
                    f"Options=credentials={credential_dir}/credentials-home-olduser,"
                    "uid=olduser,gid=olduser\n"
                )
            manifest = {
                "old_username": "olduser",
                "new_username": "newuser",
                "managed_units": [{"path": unit_path}],
            }

            with patch.object(user_rename, "SMB_CREDENTIAL_DIR", credential_dir):
                user_rename._rename_managed_credentials(manifest)

            self.assertFalse(os.path.exists(old_credential))
            self.assertTrue(os.path.isfile(new_credential))

    def test_cron_and_mail_rename_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cron_dir = os.path.join(tmpdir, "cron")
            mail_dir = os.path.join(tmpdir, "mail")
            os.mkdir(cron_dir)
            os.mkdir(mail_dir)
            for directory in (cron_dir, mail_dir):
                with open(os.path.join(directory, "olduser"), "w", encoding="utf-8"):
                    pass

            with (
                patch.object(user_rename, "CRON_DIR", cron_dir),
                patch.object(user_rename, "MAIL_DIRS", (mail_dir,)),
                patch.object(user_rename.os, "chown"),
            ):
                user_rename._rename_cron_and_mail("olduser", "newuser", os.getuid())
                user_rename._rename_cron_and_mail("olduser", "newuser", os.getuid())

            self.assertTrue(os.path.isfile(os.path.join(cron_dir, "newuser")))
            self.assertTrue(os.path.isfile(os.path.join(mail_dir, "newuser")))

    def test_crontab_collision_is_rejected_before_cutover(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cron_dir = os.path.join(tmpdir, "cron")
            os.mkdir(cron_dir)
            with open(os.path.join(cron_dir, "newuser"), "w", encoding="utf-8"):
                pass

            with (
                patch.object(user_rename, "CRON_DIR", cron_dir),
                patch.object(user_rename, "MAIL_DIRS", ()),
            ):
                with self.assertRaisesRegex(user_rename.RenameError, "already exists"):
                    user_rename._preflight_cron_and_mail("olduser", "newuser")

    def test_failure_restores_shell_using_new_account_name(self):
        renamed_account = pwd.struct_passwd(
            ("newuser", "x", 1000, 1000, "", "/home/newuser", "/usr/sbin/nologin")
        )

        def account(username):
            if username == "newuser":
                return renamed_account
            raise user_rename.RenameError("missing")

        with (
            patch.object(user_rename, "_account", side_effect=account),
            patch.object(user_rename, "_run") as run,
        ):
            user_rename._restore_login_shell_after_failure(
                {
                    "old_username": "olduser",
                    "new_username": "newuser",
                    "old_uid": 1000,
                    "old_shell": "/bin/bash",
                }
            )

        run.assert_called_once_with(
            ["usermod", "-s", "/bin/bash", "newuser"],
            check=False,
        )

    def test_preflight_rejects_root(self):
        account = pwd.struct_passwd(
            ("root", "x", 0, 0, "root", "/root", "/bin/bash")
        )
        with patch.object(user_rename, "_account", return_value=account):
            with self.assertRaisesRegex(user_rename.RenameError, "root"):
                user_rename._preflight(
                    {
                        "operation_id": "0123456789abcdef",
                        "old_username": "root",
                        "new_username": "newuser",
                    }
                )

    def test_status_is_atomic_and_non_secret(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(user_rename, "RENAME_ROOT", tmpdir):
                user_rename._write_status(
                    "0123456789abcdef",
                    "prepared",
                    "in_progress",
                    details={"old_home": "/home/olduser"},
                )
                status_path = os.path.join(
                    tmpdir,
                    "0123456789abcdef",
                    "status.json",
                )
                with open(status_path, encoding="utf-8") as file_obj:
                    status = json.load(file_obj)

        self.assertEqual(status["phase"], "prepared")
        self.assertNotIn("password", status)

    def test_linger_marker_moves_and_resume_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(user_rename, "LINGER_DIR", tmpdir):
                open(os.path.join(tmpdir, "olduser"), "w", encoding="utf-8").close()
                user_rename._rename_linger("olduser", "newuser", True)
                user_rename._rename_linger("olduser", "newuser", True)
                self.assertTrue(os.path.isfile(os.path.join(tmpdir, "newuser")))
                self.assertFalse(os.path.exists(os.path.join(tmpdir, "olduser")))

    def test_concurrent_operation_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            operation_id = "0123456789abcdef"
            other_id = "fedcba9876543210"
            other_dir = os.path.join(tmpdir, other_id)
            os.makedirs(other_dir)
            with open(os.path.join(other_dir, "status.json"), "w", encoding="utf-8") as file_obj:
                json.dump({"status": "in_progress"}, file_obj)
            with patch.object(user_rename, "RENAME_ROOT", tmpdir):
                with self.assertRaisesRegex(user_rename.RenameError, "unfinished"):
                    user_rename._reject_concurrent_operation(operation_id)

    def test_target_lock_serializes_jobs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(user_rename, "RENAME_ROOT", tmpdir):
                first = user_rename._acquire_target_lock()
                try:
                    with self.assertRaisesRegex(user_rename.RenameError, "already running"):
                        user_rename._acquire_target_lock()
                finally:
                    user_rename._release_target_lock(first)


if __name__ == "__main__":
    unittest.main()
