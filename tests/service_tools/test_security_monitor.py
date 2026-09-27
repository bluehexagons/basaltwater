"""Tests for security.service_tools.security_monitor."""

from __future__ import annotations

import os
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from security.service_tools import security_monitor
from lib.xrdp_certificate import XrdpCertificateHealth


class TestSecurityMonitor(unittest.TestCase):
    @patch(
        "security.service_tools.security_monitor.inspect_xrdp_certificate",
        return_value=XrdpCertificateHealth("not_configured", "", ""),
    )
    @patch("security.service_tools.security_monitor._save_state")
    @patch("security.service_tools.security_monitor.send_notification_safe")
    @patch("security.service_tools.security_monitor._check_ssh_failures", return_value=(0, None))
    @patch("security.service_tools.security_monitor._check_auditd", return_value=([], False, []))
    @patch("security.service_tools.security_monitor._check_fail2ban", return_value=([], [], None))
    @patch("security.service_tools.security_monitor._load_state", return_value={})
    @patch("security.service_tools.security_monitor.load_notification_configs_from_state", return_value=[])
    def test_collects_and_advances_cursor_without_notification_targets(
        self, _configs, _state, mock_fail2ban, mock_audit, mock_ssh, mock_notify, mock_save,
        _certificate,
    ):
        self.assertEqual(security_monitor.main(), 0)

        mock_fail2ban.assert_called_once()
        mock_audit.assert_called_once()
        mock_ssh.assert_called_once()
        mock_notify.assert_not_called()
        mock_save.assert_called_once()

    @patch(
        "security.service_tools.security_monitor.inspect_xrdp_certificate",
        return_value=XrdpCertificateHealth("not_configured", "", ""),
    )
    @patch("security.service_tools.security_monitor._save_state")
    @patch("security.service_tools.security_monitor.send_notification_safe")
    @patch("security.service_tools.security_monitor._check_ssh_failures", return_value=(0, None))
    @patch("security.service_tools.security_monitor._check_auditd", return_value=([], False, []))
    @patch("security.service_tools.security_monitor._check_fail2ban")
    @patch("security.service_tools.security_monitor._load_state")
    @patch("security.service_tools.security_monitor.load_notification_configs_from_state", return_value=["cfg"])
    def test_future_cursor_is_clamped_to_recent_window(
        self, _configs, mock_state, mock_fail2ban, _audit, _ssh, mock_notify, mock_save,
        _certificate,
    ):
        mock_state.return_value = {"last_run": (datetime.now() + timedelta(days=1)).isoformat()}
        mock_fail2ban.return_value = ([], [], None)

        self.assertEqual(security_monitor.main(), 0)

        since = mock_fail2ban.call_args.args[0]
        self.assertLess(since, datetime.now())
        self.assertGreater(since, datetime.now() - timedelta(minutes=16))
        mock_notify.assert_not_called()
        mock_save.assert_called_once()

    def test_managed_setup_window_is_applied_only_to_audit_collection(self):
        window = (
            datetime.now() - timedelta(minutes=5),
            datetime.now() - timedelta(minutes=1),
        )
        prior_cursor = (datetime.now() - timedelta(minutes=15)).isoformat()
        certificate = XrdpCertificateHealth("not_configured", "", "")
        with patch.object(
            security_monitor, "load_notification_configs_from_state", return_value=[]
        ), patch.object(
            security_monitor, "_load_state", return_value={"last_run": prior_cursor}
        ), patch.object(
            security_monitor, "managed_setup_audit_window", return_value=window
        ), patch.object(
            security_monitor, "_check_fail2ban", return_value=([], [], None)
        ) as fail2ban, patch.object(
            security_monitor, "_check_auditd", return_value=([], False, [])
        ) as auditd, patch.object(
            security_monitor, "_check_ssh_failures", return_value=(0, None)
        ) as ssh, patch.object(
            security_monitor, "inspect_xrdp_certificate", return_value=certificate
        ), patch.object(security_monitor, "_save_state"):
            self.assertEqual(security_monitor.main(), 0)

        self.assertEqual(auditd.call_args.kwargs["excluded_window"], window)
        self.assertEqual(auditd.call_args.args[0], fail2ban.call_args.args[0])
        self.assertEqual(auditd.call_args.args[0], ssh.call_args.args[0])

    @patch(
        "security.service_tools.security_monitor.inspect_xrdp_certificate",
        return_value=XrdpCertificateHealth("not_configured", "", ""),
    )
    @patch("security.service_tools.security_monitor._save_state")
    @patch("security.service_tools.security_monitor.send_notification_safe")
    @patch("security.service_tools.security_monitor._check_ssh_failures", return_value=(0, "SSH journal: denied"))
    @patch("security.service_tools.security_monitor._check_auditd", return_value=([], False, []))
    @patch("security.service_tools.security_monitor._check_fail2ban", return_value=([], [], None))
    @patch("security.service_tools.security_monitor._load_state", return_value={})
    @patch("security.service_tools.security_monitor.load_notification_configs_from_state", return_value=["cfg"])
    def test_collection_failure_notifies_once_and_retains_cursor(
        self, _configs, _state, _fail2ban, _audit, _ssh, mock_notify, mock_save,
        _certificate,
    ):
        self.assertEqual(security_monitor.main(), 1)

        saved_state = mock_save.call_args.args[0]
        saved_cursor = datetime.fromisoformat(saved_state["last_run"])
        self.assertLess(saved_cursor, datetime.now())
        self.assertGreater(saved_cursor, datetime.now() - timedelta(minutes=16))
        self.assertEqual(saved_state["collection_errors"], ["SSH journal: denied"])
        self.assertEqual(mock_notify.call_args.kwargs["status"], "error")
        self.assertIn("SSH journal: denied", mock_notify.call_args.kwargs["details"])
        self.assertEqual(
            mock_notify.call_args.kwargs["data"]["events"][0]["type"],
            "source_health",
        )

    @patch(
        "security.service_tools.security_monitor.inspect_xrdp_certificate",
        return_value=XrdpCertificateHealth("not_configured", "", ""),
    )
    @patch("security.service_tools.security_monitor._save_state")
    @patch("security.service_tools.security_monitor.send_notification_safe")
    @patch("security.service_tools.security_monitor._check_ssh_failures", return_value=(0, "SSH journal: denied"))
    @patch("security.service_tools.security_monitor._check_auditd", return_value=([], False, []))
    @patch("security.service_tools.security_monitor._check_fail2ban", return_value=([], [], None))
    @patch(
        "security.service_tools.security_monitor._load_state",
        return_value={"last_run": "2026-08-22T06:00:00", "collection_errors": ["SSH journal: denied"]},
    )
    @patch("security.service_tools.security_monitor.load_notification_configs_from_state", return_value=["cfg"])
    def test_repeated_collection_failure_is_not_resent(
        self, _configs, _state, _fail2ban, _audit, _ssh, mock_notify, mock_save,
        _certificate,
    ):
        self.assertEqual(security_monitor.main(), 1)

        mock_notify.assert_not_called()
        self.assertEqual(
            mock_save.call_args.args[0]["collection_errors"],
            ["SSH journal: denied"],
        )

    @patch(
        "security.service_tools.security_monitor.inspect_xrdp_certificate",
        return_value=XrdpCertificateHealth("not_configured", "", ""),
    )
    @patch("security.service_tools.security_monitor._save_state")
    @patch("security.service_tools.security_monitor.send_notification_safe")
    @patch("security.service_tools.security_monitor._check_ssh_failures", return_value=(0, None))
    @patch("security.service_tools.security_monitor._check_auditd", return_value=([], False, []))
    @patch("security.service_tools.security_monitor._check_fail2ban", return_value=([], [], None))
    @patch(
        "security.service_tools.security_monitor._load_state",
        return_value={"last_run": "2026-08-22T06:00:00", "collection_errors": ["SSH journal: denied"]},
    )
    @patch("security.service_tools.security_monitor.load_notification_configs_from_state", return_value=["cfg"])
    def test_collection_recovery_is_reported_and_clears_error_state(
        self, _configs, _state, _fail2ban, _audit, _ssh, mock_notify, mock_save,
        _certificate,
    ):
        self.assertEqual(security_monitor.main(), 0)

        self.assertIn("monitor recovered", mock_notify.call_args.kwargs["subject"])
        self.assertEqual(
            mock_notify.call_args.kwargs["data"]["events"][0]["type"],
            "monitor_recovery",
        )
        self.assertNotIn("collection_errors", mock_save.call_args.args[0])

    @patch(
        "security.service_tools.security_monitor.inspect_xrdp_certificate",
        return_value=XrdpCertificateHealth("not_configured", "", ""),
    )
    @patch("security.service_tools.security_monitor._save_state")
    @patch("security.service_tools.security_monitor.send_notification_safe")
    @patch("security.service_tools.security_monitor._check_ssh_failures", return_value=(0, None))
    @patch("security.service_tools.security_monitor._check_auditd", return_value=(['privileged'], False, []))
    @patch("security.service_tools.security_monitor._check_fail2ban", return_value=([], [], None))
    @patch("security.service_tools.security_monitor._load_state", return_value={})
    @patch("security.service_tools.security_monitor.load_notification_configs_from_state", return_value=["cfg"])
    def test_routine_privileged_audit_event_does_not_notify(
        self, _configs, _state, _fail2ban, _audit, _ssh, mock_notify, mock_save,
        _certificate,
    ):
        self.assertEqual(security_monitor.main(), 0)

        mock_notify.assert_not_called()
        mock_save.assert_called_once()

    @patch(
        "security.service_tools.security_monitor.inspect_xrdp_certificate",
        return_value=XrdpCertificateHealth("not_configured", "", ""),
    )
    @patch("security.service_tools.security_monitor._save_state")
    @patch("security.service_tools.security_monitor.send_notification_safe")
    @patch("security.service_tools.security_monitor._check_ssh_failures", return_value=(0, None))
    @patch("security.service_tools.security_monitor._check_auditd", return_value=([], False, []))
    @patch(
        "security.service_tools.security_monitor._check_fail2ban",
        return_value=([], [{"type": "fail2ban", "action": "unban", "jail": "sshd", "source_ip": "192.0.2.4"}], None),
    )
    @patch("security.service_tools.security_monitor._load_state", return_value={})
    @patch("security.service_tools.security_monitor.load_notification_configs_from_state", return_value=["cfg"])
    def test_unban_event_is_logged_without_external_notification(
        self, _configs, _state, _fail2ban, _audit, _ssh, mock_notify, mock_save,
        _certificate,
    ):
        self.assertEqual(security_monitor.main(), 0)

        mock_notify.assert_not_called()
        mock_save.assert_called_once()

    @patch(
        "security.service_tools.security_monitor.inspect_xrdp_certificate",
        return_value=XrdpCertificateHealth(
            "error",
            "/etc/xrdp/cert.pem",
            "/etc/xrdp/key.pem",
            ("XRDP private key is unreadable",),
            "aabbcc",
        ),
    )
    @patch("security.service_tools.security_monitor._save_state")
    @patch("security.service_tools.security_monitor.send_notification_safe")
    @patch("security.service_tools.security_monitor._check_ssh_failures", return_value=(0, None))
    @patch("security.service_tools.security_monitor._check_auditd", return_value=([], False, []))
    @patch("security.service_tools.security_monitor._check_fail2ban", return_value=([], [], None))
    @patch("security.service_tools.security_monitor._load_state", return_value={})
    @patch("security.service_tools.security_monitor.load_notification_configs_from_state", return_value=["cfg"])
    def test_new_certificate_failure_is_reported_and_persisted(
        self, _configs, _state, _fail2ban, _audit, _ssh, mock_notify, mock_save,
        _certificate,
    ):
        self.assertEqual(security_monitor.main(), 1)

        self.assertEqual(mock_notify.call_args.kwargs["status"], "error")
        self.assertIn("private key is unreadable", mock_notify.call_args.kwargs["details"])
        saved_state = mock_save.call_args.args[0]
        self.assertEqual(saved_state["rdp_certificate_status"], "error")
        self.assertEqual(saved_state["rdp_certificate_fingerprint"], "aabbcc")

    @patch("security.service_tools.security_monitor.subprocess.run")
    def test_ausearch_no_matches_is_not_a_collection_error(self, mock_run):
        mock_run.return_value = SimpleNamespace(returncode=1, stdout="", stderr="")

        has_events, error = security_monitor._ausearch_has_events("identity", datetime.now())

        self.assertFalse(has_events)
        self.assertIsNone(error)

    def test_audit_events_include_evidence_summary(self):
        output = """----
type=PATH msg=audit(123): name=\"/etc/sudoers\"\ntype=SYSCALL msg=audit(123): syscall=openat auid=1000 uid=0 exe=\"/usr/bin/visudo\"
----
type=PATH msg=audit(124): name=\"/etc/sudoers.d/ops\"\ntype=SYSCALL msg=audit(124): syscall=rename auid=1000 uid=0 exe=\"/usr/bin/install\"
"""

        events = security_monitor._parse_audit_events("sudoers", output)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_count"], 2)
        self.assertEqual(events[0]["paths"], ["/etc/sudoers", "/etc/sudoers.d/ops"])
        self.assertIn("1000", events[0]["actors"])
        self.assertIn("/usr/bin/visudo", events[0]["executables"])
        self.assertEqual(events[0]["meaning"], "administrator access policy changed")

    def test_audit_events_inside_managed_setup_window_are_excluded(self):
        output = """----
type=PATH msg=audit(1766400000.100:1): name=\"/etc/passwd\"
----
type=PATH msg=audit(1766400300.100:2): name=\"/etc/passwd\"
"""
        setup_start = datetime.fromtimestamp(1766399999)
        setup_end = datetime.fromtimestamp(1766400001)

        events = security_monitor._parse_audit_events(
            "identity", output, (setup_start, setup_end)
        )

        self.assertEqual(events[0]["event_count"], 1)
        expected = datetime.fromtimestamp(1766400300.1).isoformat(timespec="seconds")
        self.assertEqual(events[0]["first_seen"], expected)

    def test_interpreted_ausearch_timestamp_is_excluded(self):
        output = """----
type=PATH msg=audit(08/22/2026 12:00:00.100:1): name=\"/etc/passwd\"
"""

        events = security_monitor._parse_audit_events(
            "identity",
            output,
            (datetime(2026, 8, 22, 11, 59), datetime(2026, 8, 22, 12, 1)),
        )

        self.assertEqual(events, [])

    def test_audit_notification_explains_meaning_and_gives_specific_action(self):
        since = datetime(2026, 8, 22, 12, 0, 0)
        details = security_monitor._format_security_details(
            since=since,
            now=since + timedelta(minutes=15),
            status="warning",
            bans=[],
            unbans=[],
            audit_keys=["sudoers"],
            ssh_failures=0,
            certificate_event=None,
            audit_events=[
                {
                    "type": "auditd",
                    "key": "sudoers",
                    "severity": "warning",
                    "event_count": 1,
                    "paths": ["/etc/sudoers.d/ops"],
                }
            ],
        )

        self.assertIn("administrator access policy changed", details)
        self.assertIn("not confirmation that the host was compromised", details)
        self.assertIn("Confirm recent sudo policy changes were approved", details)

    @patch("security.service_tools.security_monitor.shutil.which")
    def test_missing_ausearch_is_reported_when_auditd_is_installed(self, mock_which):
        mock_which.side_effect = lambda command, **_kwargs: "/usr/sbin/auditd" if command == "auditd" else None

        events, critical, errors = security_monitor._check_auditd(datetime.now())

        self.assertEqual(events, [])
        self.assertFalse(critical)
        self.assertEqual(errors, ["auditd: ausearch command unavailable"])
        self.assertEqual(
            security_monitor._collection_error_event(errors[0])["source"],
            "auditd",
        )

    @patch("security.service_tools.security_monitor.shutil.which")
    def test_audit_tools_use_system_path(self, mock_which):
        mock_which.return_value = "/usr/sbin/ausearch"

        self.assertEqual(security_monitor._audit_tool("ausearch"), "/usr/sbin/ausearch")
        mock_which.assert_called_once_with(
            "ausearch", path="/usr/sbin:/sbin:/usr/bin:/bin"
        )

    @patch("security.service_tools.security_monitor.shutil.which", return_value=None)
    def test_required_audit_coverage_does_not_report_clean_when_tools_are_missing(
        self, _which
    ):
        with patch.dict(os.environ, {"BASALTWATER_AUDIT_REQUIRED": "1"}):
            events, critical, errors = security_monitor._check_auditd(datetime.now())

        self.assertEqual(events, [])
        self.assertFalse(critical)
        self.assertEqual(errors, ["auditd: ausearch command unavailable"])

    @patch("security.service_tools.security_monitor.subprocess.run")
    def test_ssh_failures_are_aggregated_by_source_user_and_method(self, mock_run):
        mock_run.return_value = SimpleNamespace(
            returncode=0,
            stderr="",
            stdout="\n".join([
                json.dumps({
                    "MESSAGE": "Failed password for root from 192.0.2.4 port 22 ssh2",
                    "_SOURCE_REALTIME_TIMESTAMP": "1766400000000000",
                }),
                json.dumps({
                    "MESSAGE": "Failed publickey for deploy from 192.0.2.4 port 22 ssh2",
                    "_SOURCE_REALTIME_TIMESTAMP": "1766400001000000",
                }),
                json.dumps({
                    "MESSAGE": "Invalid user admin from 198.51.100.8 port 22",
                }),
            ]),
        )

        summary, error = security_monitor._check_ssh_failures(datetime.now())

        self.assertIsNone(error)
        self.assertEqual(summary["failure_count"], 3)
        self.assertEqual(summary["sources"][0]["source_ip"], "192.0.2.4")
        self.assertEqual(summary["sources"][0]["count"], 1)
        self.assertEqual(
            {source["method"] for source in summary["sources"]},
            {"password", "publickey", "unknown"},
        )

    def test_ssh_account_lockout_is_structured(self):
        event = security_monitor._parse_ssh_lockout(
            "pam_faillock(sshd:auth): user=root rhost=198.51.100.8 account temporarily locked",
            "2026-08-22T12:00:00",
        )

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event["username"], "root")
        self.assertEqual(event["source_ip"], "198.51.100.8")

    def test_fail2ban_events_are_structured_and_include_unbans(self):
        with tempfile.TemporaryDirectory() as log_dir:
            log_path = os.path.join(log_dir, "fail2ban.log")
            with open(log_path, "w", encoding="utf-8") as log_file:
                log_file.write(
                    "2026-08-22 12:01:02,123 fail2ban.actions [1]: "
                    "WARNING [nginx-http-auth] Ban 192.0.2.4\n"
                    "2026-08-22 12:02:03,456 fail2ban.actions [1]: "
                    "NOTICE [sshd] Unban 192.0.2.4\n"
                )

            with patch("security.service_tools.security_monitor._FAIL2BAN_LOG", log_path):
                bans, unbans, error = security_monitor._check_fail2ban(
                    datetime(2026, 8, 22, 12, 0, 0)
                )

        self.assertIsNone(error)
        self.assertEqual(bans[0]["jail"], "nginx-http-auth")
        self.assertEqual(bans[0]["source_ip"], "192.0.2.4")
        self.assertEqual(unbans[0]["action"], "unban")

    def test_state_roundtrip_uses_atomic_writer(self):
        with tempfile.TemporaryDirectory() as state_dir:
            state_path = os.path.join(state_dir, "security-monitor.json")
            with patch("security.service_tools.security_monitor._STATE_FILE", state_path):
                security_monitor._save_state({"last_run": "2026-08-04T00:00:00"})
                self.assertEqual(
                    security_monitor._load_state(),
                    {"last_run": "2026-08-04T00:00:00"},
                )
                self.assertEqual(os.stat(state_path).st_mode & 0o777, 0o600)

    def test_certificate_issue_notifies_only_on_change(self):
        health = XrdpCertificateHealth(
            "error",
            "/etc/xrdp/cert.pem",
            "/etc/xrdp/key.pem",
            ("XRDP certificate and private key do not match",),
            "aabbcc",
        )

        event = security_monitor._certificate_health_event({}, health)
        repeated = security_monitor._certificate_health_event(
            {"rdp_certificate_issue": health.issue or ""}, health
        )
        replaced_but_still_broken = security_monitor._certificate_health_event(
            {
                "rdp_certificate_issue": health.issue or "",
                "rdp_certificate_fingerprint": "different",
            },
            health,
        )

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event[0], "error")
        self.assertIsNone(repeated)
        self.assertIsNotNone(replaced_but_still_broken)

    def test_certificate_recovery_and_rotation_are_reported(self):
        healthy = XrdpCertificateHealth(
            "ok",
            "/etc/xrdp/cert.pem",
            "/etc/xrdp/key.pem",
            fingerprint="new",
        )

        recovered = security_monitor._certificate_health_event(
            {"rdp_certificate_issue": "expired"}, healthy
        )
        rotated = security_monitor._certificate_health_event(
            {"rdp_certificate_fingerprint": "old"}, healthy
        )

        assert recovered is not None
        assert rotated is not None
        self.assertIn("recovered", recovered[1])
        self.assertIn("changed", rotated[1])

    def test_next_state_persists_certificate_health(self):
        now = datetime(2026, 8, 9, 12, 0, 0)
        health = XrdpCertificateHealth(
            "warning",
            "/etc/xrdp/cert.pem",
            "/etc/xrdp/key.pem",
            ("expires soon",),
            "aabbcc",
        )

        state = security_monitor._next_state(now, health)

        self.assertEqual(state["rdp_certificate_status"], "warning")
        self.assertEqual(state["rdp_certificate_issue"], "expires soon")
        self.assertEqual(state["rdp_certificate_fingerprint"], "aabbcc")


if __name__ == "__main__":
    unittest.main()
