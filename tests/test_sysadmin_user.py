"""Controller-side tests for the remote target-user rename workflow."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from lib import sysadmin_user


class TestSysadminUser(unittest.TestCase):
    def test_poll_retains_authenticated_identity_without_reprobing_retired_login(self):
        progress = {"status": "in_progress", "phase": "configuration-starting"}
        complete = {"status": "success", "phase": "complete"}
        with (
            patch.object(sysadmin_user, "_run_ssh", side_effect=[
                subprocess.CompletedProcess([], 1, "This account is currently not available.\n", ""),
                subprocess.CompletedProcess([], 255, "", "Permission denied"),
                subprocess.CompletedProcess([], 0, json.dumps(progress), ""),
                subprocess.CompletedProcess([], 0, json.dumps(complete), ""),
            ]) as ssh,
            patch.object(sysadmin_user.time, "sleep"),
        ):
            self.assertEqual(sysadmin_user._wait_for_completion(
                "host.example", "olduser", "newuser", None, "0123456789abcdef",
            ), complete)
        self.assertEqual([call.args[1] for call in ssh.call_args_list],
                         ["olduser", "olduser", "newuser", "newuser"])

    def test_admin_status_poll_never_opens_new_login_for_progress_or_stale_attempt(self):
        for initial in (
            {"status": "in_progress", "phase": "quiescing", "attempt_id": "new"},
            {"status": "failed", "phase": "identity-renamed", "attempt_id": "old"},
        ):
            with self.subTest(initial=initial):
                terminal = {"status": "success", "phase": "complete", "attempt_id": "new"}
                with (
                    patch.object(sysadmin_user, "_run_ssh", side_effect=[
                        subprocess.CompletedProcess([], 0, json.dumps(status), "") for status in (initial, terminal)
                    ]) as ssh,
                    patch.object(sysadmin_user.time, "sleep") as sleep,
                ):
                    self.assertEqual(sysadmin_user._wait_for_completion(
                        "host.example", "root", "newuser", None, "0123456789abcdef", attempt_id="new",
                    ), terminal)
                self.assertEqual([call.args[1] for call in ssh.call_args_list], ["root", "root"])
                sleep.assert_called_once()

    def test_poll_falls_back_to_new_login_when_admin_ssh_is_unreachable(self):
        terminal = {"status": "success", "phase": "complete"}
        with patch.object(sysadmin_user, "_run_ssh", side_effect=[
            subprocess.CompletedProcess([], 255, "", "unreachable"),
            subprocess.CompletedProcess([], 0, json.dumps(terminal), ""),
        ]) as ssh:
            self.assertEqual(sysadmin_user._wait_for_completion(
                "host.example", "olduser", "newuser", None, "0123456789abcdef",
            ), terminal)
        self.assertEqual([call.args[1] for call in ssh.call_args_list], ["olduser", "newuser"])

    def test_malformed_status_retains_authenticated_identity_until_valid_result(self):
        terminal = {"status": "success", "phase": "complete", "attempt_id": "new"}
        for username in ("olduser", "newuser"):
            for output in ('banner\n{"status":"success"}', '{"status":"success"', '["success"]', 'null'):
                with self.subTest(username=username, output=output):
                    prefix = [] if username == "olduser" else [subprocess.CompletedProcess([], 255, "", "unreachable")]
                    with (
                        patch.object(sysadmin_user, "_run_ssh", side_effect=[
                            *prefix,
                            subprocess.CompletedProcess([], 0, output, ""),
                            subprocess.CompletedProcess([], 0, output, ""),
                            subprocess.CompletedProcess([], 0, json.dumps(terminal), ""),
                        ]) as ssh,
                        patch.object(sysadmin_user.time, "sleep") as sleep,
                    ):
                        self.assertEqual(sysadmin_user._wait_for_completion(
                            "host.example", "olduser", "newuser", None, "0123456789abcdef", attempt_id="new",
                        ), terminal)
                    self.assertEqual([call.args[1] for call in ssh.call_args_list],
                                     (["olduser"] if prefix else []) + [username] * 3)
                    self.assertEqual(sleep.call_count, 2)

    def test_malformed_status_times_out_without_claiming_completion(self):
        for username in ("olduser", "newuser"):
            with self.subTest(username=username):
                prefix = [] if username == "olduser" else [subprocess.CompletedProcess([], 255, "", "unreachable")]
                with (
                    patch.object(sysadmin_user, "_run_ssh", side_effect=[
                        *prefix, *[subprocess.CompletedProcess([], 0, 'banner\n{"status":"success"}', "")] * 2,
                    ]) as ssh,
                    patch.object(sysadmin_user.time, "monotonic", side_effect=[0, 0, 1, 301]),
                    patch.object(sysadmin_user.time, "sleep"),
                ):
                    self.assertIsNone(sysadmin_user._wait_for_completion(
                        "host.example", "olduser", "newuser", None, "0123456789abcdef",
                    ))
                self.assertEqual([call.args[1] for call in ssh.call_args_list],
                                 (["olduser"] if prefix else []) + [username] * 2)

    def test_resume_retains_authenticated_identity_with_malformed_status(self):
        manifest = {"old_username": "olduser", "new_username": "newuser"}
        for username in ("olduser", "newuser"):
            with self.subTest(username=username):
                prefix = [] if username == "olduser" else [subprocess.CompletedProcess([], 255, "", "unreachable")]
                with (
                    patch.object(sysadmin_user, "_run_ssh", side_effect=[
                        *prefix, subprocess.CompletedProcess([], 0, 'banner\n{"status":"success"}', ""),
                    ]) as ssh,
                    patch.object(sysadmin_user, "_read_manifest", side_effect=lambda _h, user, *_a: manifest if user == username else None),
                ):
                    self.assertEqual(sysadmin_user._read_resume_operation(
                        "host.example", "olduser", "newuser", None, "0123456789abcdef",
                    ), (username, {"status": "unavailable"}, manifest))
                self.assertEqual([call.args[1] for call in ssh.call_args_list],
                                 (["olduser"] if prefix else []) + [username])

    def test_unit_start_is_detached_from_the_renamed_users_session(self):
        completed = subprocess.CompletedProcess([], 0, "", "")
        staged = {}

        def scp_command(_host, _username, local_path, *_args, **_kwargs):
            staged["unit"] = Path(local_path).read_text()
            return ["scp"]

        with (
            patch.object(sysadmin_user, "build_scp_command", side_effect=scp_command),
            patch.object(sysadmin_user.subprocess, "run", return_value=completed),
            patch.object(sysadmin_user, "_run_ssh", return_value=completed) as run_ssh,
        ):
            sysadmin_user._stage_unit(
                "host.example",
                "olduser",
                None,
                "0123456789abcdef",
            )

        remote_command = run_ssh.call_args.args[3]
        self.assertIn("systemctl enable --now --no-block", remote_command)
        self.assertIn("ExecStart=/usr/bin/python3 -I " + sysadmin_user.TARGET_HELPER, staged["unit"])

    def test_preflight_uses_isolated_python_to_avoid_sibling_types_module(self):
        completed = subprocess.CompletedProcess([], 0, json.dumps({"ok": True, "manifest": {}}), "")
        with patch.object(sysadmin_user, "_run_ssh", return_value=completed) as run_ssh:
            sysadmin_user._preflight("host.example", "root", None, "0123456789abcdef")
        self.assertIn("/usr/bin/python3 -I " + sysadmin_user.TARGET_HELPER, run_ssh.call_args.args[3])

    def test_resume_unit_start_is_non_blocking(self):
        completed = subprocess.CompletedProcess([], 0, "", "")
        with patch.object(
            sysadmin_user,
            "_run_ssh",
            return_value=completed,
        ) as run_ssh, patch.object(sysadmin_user, "_operation_id", return_value="fedcba9876543210"):
            attempt_id = sysadmin_user._start_resume_unit(
                "host.example",
                "newuser",
                None,
                "0123456789abcdef",
            )

        remote_command = run_ssh.call_args.args[3]
        self.assertIn("systemctl enable --now --no-block", remote_command)
        self.assertEqual(attempt_id, "fedcba9876543210")
        self.assertIn("--prepare-resume --attempt-id fedcba9876543210", remote_command)
        self.assertIn("/usr/bin/python3 -I " + sysadmin_user.TARGET_HELPER, remote_command)
        self.assertLess(remote_command.index("--prepare-resume"), remote_command.index("systemctl enable"))

    def test_resume_poll_waits_for_matching_attempt_even_if_new_worker_finishes_immediately(self):
        for outcome in ("success", "failed"):
            with self.subTest(outcome=outcome):
                stale = {"status": "failed", "phase": "identity-renamed", "attempt_id": "old-attempt"}
                terminal = {"status": outcome, "phase": "complete", "attempt_id": "new-attempt"}
                with (
                    patch.object(sysadmin_user, "_read_status", side_effect=[stale, terminal]) as read,
                    patch.object(sysadmin_user.time, "sleep") as sleep,
                ):
                    self.assertEqual(sysadmin_user._wait_for_completion(
                        "host.example", "newuser", "newuser", None, "0123456789abcdef",
                        attempt_id="new-attempt",
                    ), terminal)
                self.assertEqual(read.call_count, 2)
                sleep.assert_called_once()

    def test_resume_poll_does_not_treat_unacknowledged_attempt_as_completed(self):
        with (
            patch.object(sysadmin_user, "_read_status", return_value={"status": "failed"}),
            patch.object(sysadmin_user.time, "monotonic", side_effect=[0, 0, 301]),
            patch.object(sysadmin_user.time, "sleep"),
        ):
            self.assertIsNone(sysadmin_user._wait_for_completion(
                "host.example", "newuser", "newuser", None, "0123456789abcdef", attempt_id="new-attempt",
            ))

    def test_ssh_resume_transport_keeps_host_key_verification(self):
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch("lib.ssh_utils.get_workspace_known_hosts_path", return_value=f"{tmpdir}/known_hosts"),
            patch.object(sysadmin_user.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run,
        ):
            sysadmin_user._run_ssh("host.example", "newuser", None, "id -u")
        command = run.call_args.args[0]
        self.assertIn("StrictHostKeyChecking=yes", command)
        self.assertIn(f"UserKnownHostsFile={tmpdir}/known_hosts", command)

    def test_dry_run_never_starts_worker_or_updates_cache(self):
        config = SimpleNamespace(username="olduser", ssh_key=None)
        with (
            patch.object(sysadmin_user, "_resolve_credentials", return_value=("root", None, config)),
            patch.object(sysadmin_user, "_acquire_host_lock", return_value=object()),
            patch.object(sysadmin_user, "_release_host_lock"),
            patch.object(sysadmin_user, "ensure_remote_sudo", return_value=True),
            patch.object(sysadmin_user, "_stage_manifest"),
            patch.object(sysadmin_user, "_preflight", return_value={"old_home": "/home/olduser", "new_home": "/home/newuser"}),
            patch.object(sysadmin_user, "_discard_remote_operation") as discard,
            patch.object(sysadmin_user, "_stage_unit") as start,
            patch.object(sysadmin_user, "_start_resume_unit") as resume,
            patch.object(sysadmin_user, "rename_setup_command") as cache,
        ):
            self.assertEqual(sysadmin_user.run_user_rename("host.example", "newuser", dry_run=True), 0)
        discard.assert_called_once()
        start.assert_not_called()
        resume.assert_not_called()
        cache.assert_not_called()

    def test_host_lock_serializes_operations(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = f"{tmpdir}/host.json"
            with patch.object(sysadmin_user, "get_cache_path_for_host", return_value=cache_path):
                first = sysadmin_user._acquire_host_lock("host.example")
                try:
                    with self.assertRaisesRegex(RuntimeError, "already running"):
                        sysadmin_user._acquire_host_lock("host.example")
                finally:
                    sysadmin_user._release_host_lock(first)

    def test_invalid_destination_does_not_contact_target(self):
        with patch.object(sysadmin_user, "_resolve_credentials") as resolve:
            result = sysadmin_user.run_user_rename("host.example", "bad name")

        self.assertEqual(result, 1)
        resolve.assert_not_called()

    def test_success_updates_controller_cache_only_after_ssh_verification(self):
        config = SimpleNamespace(username="olduser", ssh_key=None)
        status = {
            "status": "success",
            "phase": "complete",
            "details": {
                "old_home": "/home/olduser",
                "old_username": "olduser",
                "username": "newuser",
                "home": "/home/newuser",
            },
        }
        with (
            patch.object(sysadmin_user, "_resolve_credentials", return_value=("root", None, config)),
            patch.object(sysadmin_user, "_acquire_host_lock", return_value=object()),
            patch.object(sysadmin_user, "_release_host_lock"),
            patch.object(sysadmin_user, "ensure_remote_sudo", return_value=True),
            patch.object(sysadmin_user, "_stage_manifest"),
            patch.object(sysadmin_user, "_preflight", return_value={
                "old_home": "/home/olduser",
                "new_home": "/home/newuser",
            }),
            patch.object(sysadmin_user, "_stage_unit"),
            patch.object(sysadmin_user, "_wait_for_completion", return_value=status),
            patch.object(sysadmin_user, "_verify_new_login", return_value=True),
            patch.object(sysadmin_user, "_read_status", return_value=status),
            patch.object(sysadmin_user, "rename_setup_command") as rename_cache,
        ):
            result = sysadmin_user.run_user_rename(
                "host.example",
                "newuser",
                assume_yes=True,
            )

        self.assertEqual(result, 0)
        rename_cache.assert_called_once_with(
            "host.example",
            old_username="olduser",
            new_username="newuser",
            old_home="/home/olduser",
            new_home="/home/newuser",
        )

    def test_preflight_failure_discards_staged_manifest(self):
        config = SimpleNamespace(username="olduser", ssh_key=None)
        with (
            patch.object(sysadmin_user, "_resolve_credentials", return_value=("root", None, config)),
            patch.object(sysadmin_user, "_acquire_host_lock", return_value=object()),
            patch.object(sysadmin_user, "_release_host_lock"),
            patch.object(sysadmin_user, "ensure_remote_sudo", return_value=True),
            patch.object(sysadmin_user, "_stage_manifest"),
            patch.object(sysadmin_user, "_preflight", side_effect=RuntimeError("blocked")),
            patch.object(sysadmin_user, "_discard_remote_operation") as discard,
        ):
            result = sysadmin_user.run_user_rename(
                "host.example",
                "newuser",
                assume_yes=True,
            )

        self.assertEqual(result, 1)
        discard.assert_called_once()

    def test_resume_uses_new_login_after_identity_cutover(self):
        operation_id = "0123456789abcdef"
        config = SimpleNamespace(username="olduser", ssh_key=None)
        failed_status = {"status": "failed", "phase": "identity-renamed"}
        success_status = {
            "status": "success",
            "attempt_id": "fedcba9876543210",
            "phase": "complete",
            "details": {
                "old_home": "/home/olduser",
                "old_username": "olduser",
                "username": "newuser",
                "home": "/home/newuser",
            },
        }
        manifest = {
            "old_username": "olduser",
            "new_username": "newuser",
        }

        statuses = iter([failed_status, failed_status, success_status])

        def read_status(_host, username, _key, _operation_id):
            return next(statuses) if username == "newuser" else None

        def read_manifest(_host, username, _key, _operation_id):
            return manifest if username == "newuser" else None

        with (
            patch.object(
                sysadmin_user,
                "_resolve_credentials",
                return_value=("olduser", None, config),
            ),
            patch.object(sysadmin_user, "_acquire_host_lock", return_value=object()),
            patch.object(sysadmin_user, "_release_host_lock"),
            patch.object(sysadmin_user, "ensure_remote_sudo") as ensure_sudo,
            patch.object(sysadmin_user, "_read_status", side_effect=read_status),
            patch.object(sysadmin_user, "_read_manifest", side_effect=read_manifest),
            patch.object(sysadmin_user, "_start_resume_unit", return_value="fedcba9876543210") as start_resume,
            patch.object(sysadmin_user.time, "sleep"),
            patch.object(sysadmin_user, "_verify_new_login", return_value=True),
            patch.object(sysadmin_user, "rename_setup_command") as rename_cache,
        ):
            result = sysadmin_user.run_user_rename(
                "host.example",
                "newuser",
                resume=operation_id,
            )

        self.assertEqual(result, 0)
        ensure_sudo.assert_not_called()
        start_resume.assert_called_once_with(
            "host.example",
            "newuser",
            None,
            operation_id,
        )
        rename_cache.assert_called_once()

    def test_success_without_completion_paths_does_not_update_cache(self):
        config = SimpleNamespace(username="olduser", ssh_key=None)
        status = {
            "status": "success",
            "phase": "complete",
            "details": {
                "old_username": "olduser",
                "username": "newuser",
            },
        }
        with (
            patch.object(
                sysadmin_user,
                "_resolve_credentials",
                return_value=("root", None, config),
            ),
            patch.object(sysadmin_user, "_acquire_host_lock", return_value=object()),
            patch.object(sysadmin_user, "_release_host_lock"),
            patch.object(sysadmin_user, "ensure_remote_sudo", return_value=True),
            patch.object(sysadmin_user, "_stage_manifest"),
            patch.object(
                sysadmin_user,
                "_preflight",
                return_value={
                    "old_home": "/home/olduser",
                    "new_home": "/home/newuser",
                },
            ),
            patch.object(sysadmin_user, "_stage_unit"),
            patch.object(sysadmin_user, "_wait_for_completion", return_value=status),
            patch.object(sysadmin_user, "_verify_new_login", return_value=True),
            patch.object(sysadmin_user, "rename_setup_command") as rename_cache,
        ):
            result = sysadmin_user.run_user_rename(
                "host.example",
                "newuser",
                assume_yes=True,
            )

        self.assertEqual(result, 1)
        rename_cache.assert_not_called()


if __name__ == "__main__":
    unittest.main()
