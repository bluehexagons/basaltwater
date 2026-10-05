"""Webhook-only CachyOS setup and refresh; no real host or network changes."""

from __future__ import annotations

from contextlib import ExitStack
import io
import json
from pathlib import Path
import shlex
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import basaltwater
import remote_setup
from lib import cachyos_refresh as refresh
from lib import notifications
from lib import cachyos_notification_state as notification_state
from lib.arg_parser import create_setup_argument_parser
from lib.cachyos import cachyos_config_from_args, validate_cachyos_config
from lib.remote_utils import is_dry_run, set_dry_run


TOKEN = "fixture_token_012345678901234567890123456789"
TARGET = "https://panel.example/api/v1/notifications#" + TOKEN


class CachyOSNotificationTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.home = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        account = SimpleNamespace(pw_dir=str(self.home), pw_name="human")
        stack.enter_context(patch.object(refresh, "_account", return_value=account))
        stack.enter_context(patch.object(refresh, "is_cachyos", return_value=True))
        stack.enter_context(patch.object(refresh, "is_dry_run", return_value=False))
        stack.enter_context(patch.object(refresh, "preflight_cachyos"))
        stack.enter_context(patch.object(refresh, "managed_repository_path", return_value=str(self.home / "installation")))
        stack.enter_context(patch.object(refresh, "get_channel_info", return_value={"channel": "dev"}))
        self.upgrade = stack.enter_context(patch.object(refresh, "upgrade_channel", return_value={
            "commit": "abcdef0123456789", "channel": "dev",
        }))
        self.execute = stack.enter_context(patch.object(refresh.os, "execv"))
        self.preflight = stack.enter_context(patch("lib.cachyos.preflight_cachyos"))
        self.doctor = stack.enter_context(patch("lib.cachyos_doctor.collect_cachyos_doctor", return_value={"capabilities": []}))
        stack.enter_context(patch("lib.cachyos_health.source_metadata", return_value={"commit": "fixture"}))
        stack.enter_context(patch.object(remote_setup.socket, "gethostname", return_value="workstation"))
        self.step = MagicMock()
        self.plan = stack.enter_context(patch.object(remote_setup, "get_steps_for_system_type", return_value=[
            ("Installing tools", self.step), ("Saving selection", refresh.save_successful_setup),
        ]))
        response = MagicMock()
        response.__enter__.return_value.status = 204
        self.request = stack.enter_context(patch.object(notifications, "_open_webhook_request", return_value=response))
        stack.enter_context(patch.object(notifications.time, "sleep"))
        self.parser, _, _ = basaltwater.create_basaltwater_parser()
        self.record = self.home / ".local/state/basaltwater/cachyos/last-setup.json"
        previous_dry_run = is_dry_run()
        self.addCleanup(set_dry_run, previous_dry_run)
        set_dry_run(False)

    def config(self, *options):
        return cachyos_config_from_args(self.parser.parse_args([
            "setup", "agent_cachyos", "localhost", "human", *options,
        ]))

    def webhook_config(self, *options):
        return self.config("--notify", "webhook", TARGET, *options)

    def test_success_sends_shared_payload_after_private_selection_is_saved(self):
        config = self.webhook_config("--notification-strict-https", "--node")
        response = self.request.return_value

        def open_request(request, **kwargs):
            self.assertTrue(self.record.exists())
            return response

        self.request.side_effect = open_request
        self.assertEqual(remote_setup.run_cachyos_setup(config), 0)
        request = self.request.call_args.args[0]
        self.assertEqual(request.full_url, "https://panel.example/api/v1/notifications")
        self.assertEqual(request.get_header("Authorization"), "Bearer " + TOKEN)
        self.assertTrue(self.request.call_args.kwargs["strict_https"])
        payload = json.loads(request.data)
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["event"]["status"], "good")
        self.assertEqual(payload["event"]["type"], "setup")
        self.assertEqual(payload["operator"]["system"], "workstation")
        self.assertIn("workstation", payload["operator"]["subject"])
        self.assertNotIn(TOKEN, request.data.decode())
        self.assertEqual(refresh.load_saved_setup()[1].notify_specs, [["webhook", TARGET]])
        self.assertEqual(stat.S_IMODE(self.record.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.record.parent.stat().st_mode), 0o700)
        evidence = self.record.with_name("last-notification.json")
        self.assertEqual(stat.S_IMODE(evidence.stat().st_mode), 0o600)
        self.assertNotIn(TOKEN, evidence.read_text())
        self.assertNotIn("panel.example", evidence.read_text())
        self.assertEqual(notification_state.collect_notification_health(config)[0], "available")

    def test_failure_notifies_once_without_forwarding_exception_secrets_or_saving(self):
        refresh.save_successful_setup(self.config("--node"))
        before = self.record.read_bytes()
        error = RuntimeError("secret exception " + TARGET)
        self.step.side_effect = error
        with self.assertRaises(RuntimeError) as caught:
            remote_setup.run_cachyos_setup(self.webhook_config())
        self.assertIs(caught.exception, error)
        self.request.assert_called_once()
        payload = json.loads(self.request.call_args.args[0].data)
        self.assertEqual(payload["event"]["status"], "error")
        self.assertIn("Installing tools", payload["operator"]["details"])
        self.assertIn("RuntimeError", payload["operator"]["details"])
        self.assertNotIn("secret exception", json.dumps(payload))
        self.assertNotIn(TOKEN, json.dumps(payload))
        self.assertEqual(self.record.read_bytes(), before)

    def test_preflight_failure_notifies_without_executing_steps(self):
        self.preflight.side_effect = ValueError("wrong session")
        with self.assertRaisesRegex(ValueError, "wrong session"):
            remote_setup.run_cachyos_setup(self.webhook_config())
        self.step.assert_not_called()
        self.assertFalse(self.record.exists())
        self.assertIn("prerequisites", json.loads(self.request.call_args.args[0].data)["operator"]["details"])

    def test_delivery_failure_never_changes_setup_outcome_or_discloses_target(self):
        for transport in (False, RuntimeError("secret transport " + TARGET)):
            for setup_fails in (False, True):
                with self.subTest(transport=type(transport), setup_fails=setup_fails):
                    self.step.side_effect = RuntimeError("original failure") if setup_fails else None
                    with patch.object(remote_setup, "send_setup_notification", return_value=transport,
                                      side_effect=transport if isinstance(transport, Exception) else None), \
                            patch("sys.stderr", new_callable=io.StringIO) as output:
                        if setup_fails:
                            with self.assertRaisesRegex(RuntimeError, "original failure"):
                                remote_setup.run_cachyos_setup(self.webhook_config())
                        else:
                            self.assertEqual(remote_setup.run_cachyos_setup(self.webhook_config()), 0)
                    self.assertIn("delivery was incomplete", output.getvalue())
                    self.assertNotIn(TOKEN, output.getvalue())
                    self.assertNotIn("secret transport", output.getvalue())

    def test_dry_run_and_no_targets_send_nothing(self):
        self.assertEqual(remote_setup.run_cachyos_setup(self.webhook_config("--dry-run")), 0)
        self.assertFalse(self.record.exists())
        self.assertFalse(self.record.with_name("last-notification.json").exists())
        self.step.assert_not_called()
        self.assertEqual(remote_setup.run_cachyos_setup(self.config()), 0)
        self.request.assert_not_called()
        self.assertFalse(is_dry_run())

    def test_receipt_writer_failure_preserves_setup_result_and_hides_exception(self):
        for setup_fails in (False, True):
            with self.subTest(setup_fails=setup_fails):
                error = ValueError("original setup failure")
                self.step.side_effect = error if setup_fails else None
                with patch.object(notification_state, "record_notification_result",
                                  side_effect=RuntimeError("secret receipt error " + TARGET)), \
                        patch("sys.stderr", new_callable=io.StringIO) as output:
                    if setup_fails:
                        with self.assertRaises(ValueError) as caught:
                            remote_setup.run_cachyos_setup(self.webhook_config())
                        self.assertIs(caught.exception, error)
                    else:
                        self.assertEqual(remote_setup.run_cachyos_setup(self.webhook_config()), 0)
                self.assertIn("Could not record", output.getvalue())
                self.assertNotIn("secret receipt", output.getvalue())
                self.assertNotIn(TOKEN, output.getvalue())

    def test_setup_report_marks_its_notification_pending_until_delivery(self):
        self.doctor.return_value = {"capabilities": [{
            "name": "notifications.webhook", "state": "available", "reason": "Prior setup accepted.",
        }]}
        remote_setup.run_cachyos_setup(self.webhook_config())
        report = json.loads(self.record.with_name("last-report.json").read_text())
        observation = report["observations"][0]
        self.assertEqual(observation["state"], "deferred")
        self.assertIn("pending", observation["reason"])
        self.assertEqual(notification_state.collect_notification_health(self.webhook_config())[0], "available")

    def test_levels_and_identical_targets_use_shared_delivery_policy(self):
        for level, success_calls, failure_calls in (("normal", 1, 1), ("verbose", 1, 1),
                                                  ("warning", 0, 1), ("error", 0, 1), ("off", 0, 0)):
            with self.subTest(level=level):
                config = self.webhook_config("--notify", "webhook", TARGET, "--notification-level", level)
                self.step.side_effect = None
                self.request.reset_mock()
                self.assertEqual(remote_setup.run_cachyos_setup(config), 0)
                self.assertEqual(self.request.call_count, success_calls)
                evidence = json.loads(self.record.with_name("last-notification.json").read_text())
                self.assertEqual(evidence["target_count"], 1)
                self.assertEqual(evidence["delivery"], "delivered" if success_calls else "suppressed")
                self.request.reset_mock()
                self.step.side_effect = RuntimeError("fixture failure")
                with self.assertRaises(RuntimeError):
                    remote_setup.run_cachyos_setup(config)
                self.assertEqual(self.request.call_count, failure_calls)

    def test_notification_receipts_do_not_qualify_other_targets_or_later_setups(self):
        config = self.webhook_config()
        self.assertEqual(notification_state.collect_notification_health(config)[0], "deferred")
        remote_setup.run_cachyos_setup(config)
        changed = self.config("--notify", "webhook", "https://other.example/hook")
        self.assertEqual(notification_state.collect_notification_health(changed)[0], "deferred")
        refresh.save_successful_setup(config)
        state, reason = notification_state.collect_notification_health(config)
        self.assertEqual(state, "deferred")
        self.assertIn("predates", reason)
        notification_state.record_notification_result(config, success=False, delivered=False)
        self.assertEqual(notification_state.collect_notification_health(config)[0], "failed")

    def test_invalid_and_unsafe_delivery_receipts_never_claim_success(self):
        config = self.webhook_config()
        remote_setup.run_cachyos_setup(config)
        evidence = self.record.with_name("last-notification.json")
        before = json.loads(evidence.read_text())
        for field, invalid in (("schema_version", True), ("delivery", "secret invalid"),
                               ("recorded_at", "secret invalid"), ("recorded_at", "2000-01-01T00:00:00"),
                               ("target_count", True), ("configuration_digest", "secret invalid")):
            evidence.write_text(json.dumps({**before, field: invalid}))
            state, reason = notification_state.collect_notification_health(config)
            self.assertEqual(state, "failed")
            self.assertNotIn("secret", reason)
        evidence.write_text(json.dumps(before))
        evidence.chmod(0o644)
        self.assertEqual(notification_state.collect_notification_health(config)[0], "failed")
        evidence.unlink()
        outside = self.home / "outside.json"
        outside.write_text("preserve")
        evidence.symlink_to(outside)
        with patch("sys.stderr", new_callable=io.StringIO) as output:
            self.assertEqual(remote_setup.run_cachyos_setup(config), 0)
        self.assertIn("Could not record", output.getvalue())
        self.assertEqual(outside.read_text(), "preserve")

    def test_mailbox_mixed_targets_and_invalid_urls_fail_before_any_action(self):
        for options in (("--notify", "mailbox", "ops@example.com"),
                        ("--notify", "webhook", TARGET, "--notify", "mailbox", "ops@example.com"),
                        ("--notify", "webhook", "file:///tmp/secret"),
                        ("--notify", "webhook", "https://user:secret@panel.example/hook")):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.config(*options)
        config = self.config()
        config.notify_specs = [["mailbox", "ops@example.com"]]
        with self.assertRaisesRegex(ValueError, "webhook notifications only"):
            remote_setup.run_cachyos_setup(config)
        self.preflight.assert_not_called()
        self.step.assert_not_called()
        self.request.assert_not_called()

    def test_direct_config_revalidates_targets_levels_and_tls(self):
        for field, value in (("notify_specs", [["webhook"]]), ("notify_specs", [["webhook", "bad"]]),
                             ("notification_level", "bad"), ("notification_strict_https", "yes")):
            config = self.config()
            setattr(config, field, value)
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_cachyos_config(config)

    def test_remote_argument_surface_preserves_webhook_level_and_tls(self):
        config = self.webhook_config("--notification-level", "warning", "--notification-strict-https")
        parser = create_setup_argument_parser("Remote setup test", for_remote=True, allow_steps=True)
        restored = cachyos_config_from_args(parser.parse_args(shlex.split(" ".join(config.to_remote_args()))), for_remote=True)
        self.assertEqual(restored.notify_specs, config.notify_specs)
        self.assertEqual(restored.notification_level, "warning")
        self.assertTrue(restored.notification_strict_https)

    def test_refresh_merges_targets_and_keeps_secrets_only_in_record_and_execution(self):
        refresh.save_successful_setup(self.webhook_config("--node", "--notification-strict-https"))
        before = self.record.read_bytes()
        other = "https://other.example/private_path?secret=private_query"
        for dry_run in (True, False):
            args = self.parser.parse_args(["refresh", "--notify", "webhook", TARGET,
                "--notify", "webhook", other, "--notification-level", "error",
                "--no-notification-strict-https", *(["--dry-run"] if dry_run else [])])
            with patch("sys.stdout", new_callable=io.StringIO) as output:
                self.assertEqual(refresh.run_refresh_command(args), 0)
            visible = output.getvalue()
            for secret in (TOKEN, "private_path", "private_query", "/api/v1/notifications"):
                self.assertNotIn(secret, visible)
            self.assertIn("REPLACE_WITH_WEBHOOK_URL", visible)
            self.assertEqual(self.record.read_bytes(), before)
            if dry_run:
                self.execute.assert_not_called()
                self.upgrade.assert_not_called()
            else:
                merged = refresh._parse_saved_arguments(self.execute.call_args.args[1][2:])
                self.assertEqual(merged.notify_specs, [["webhook", TARGET], ["webhook", other]])
                self.assertEqual(merged.notification_level, "error")
                self.assertFalse(merged.notification_strict_https)
                self.assertTrue(merged.install_node)
                refresh.save_successful_setup(merged)
                self.assertEqual(refresh.load_saved_setup()[1].notify_specs, merged.notify_specs)
        self.request.assert_not_called()

    def test_refresh_rejects_mailbox_before_upgrade_or_replacing_selection(self):
        refresh.save_successful_setup(self.config("--node"))
        before = self.record.read_bytes()
        args = self.parser.parse_args(["refresh", "--notify", "mailbox", "ops@example.com"])
        self.assertEqual(refresh.run_refresh_command(args), 1)
        self.upgrade.assert_not_called()
        self.execute.assert_not_called()
        self.assertEqual(self.record.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
