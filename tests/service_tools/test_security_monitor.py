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
from security.service_tools import security_retention
from lib.xrdp_certificate import XrdpCertificateHealth


def _stream_output(output: str, returncode: int = 0):
    def run(_command, *, on_output, **_kwargs):
        for line in output.splitlines(keepends=True):
            on_output(line)
        return returncode

    return run


class TestSecurityMonitor(unittest.TestCase):
    def setUp(self):
        self.retention = self.enterContext(patch.object(
            security_monitor, '_check_retention', return_value=({}, []),
        ))

    @patch(
        "security.service_tools.security_monitor.inspect_xrdp_certificate",
        return_value=XrdpCertificateHealth("not_configured", "", ""),
    )
    @patch("security.service_tools.security_monitor._save_state")
    @patch("security.service_tools.security_monitor.send_notification_safe")
    @patch("security.service_tools.security_monitor._check_ssh_failures", return_value=(0, None))
    @patch("security.service_tools.security_monitor._check_auditd", return_value=([], False, []))
    @patch("security.service_tools.security_monitor._check_fail2ban", return_value=security_monitor.Fail2banScan())
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

    def test_catchup_advances_one_complete_window_per_run(self):
        since = (datetime.now() - timedelta(minutes=45)).replace(microsecond=0)
        state = {"last_run": since.isoformat()}
        observed_windows = []
        certificate = XrdpCertificateHealth("not_configured", "", "")

        def check_fail2ban(start, end):
            observed_windows.append((start, end))
            return security_monitor.Fail2banScan()

        def save(next_state):
            state.clear()
            state.update(next_state)

        with patch.object(
            security_monitor, "load_notification_configs_from_state", return_value=[]
        ), patch.object(
            security_monitor, "_load_state", side_effect=lambda: dict(state)
        ), patch.object(
            security_monitor, "_check_fail2ban", side_effect=check_fail2ban
        ), patch.object(
            security_monitor, "_check_auditd", return_value=([], False, [])
        ), patch.object(
            security_monitor, "_check_ssh_failures", return_value=(0, None)
        ), patch.object(
            security_monitor, "inspect_xrdp_certificate", return_value=certificate
        ), patch.object(security_monitor, "_save_state", side_effect=save):
            self.assertEqual(security_monitor.main(), 0)
            first_cursor = datetime.fromisoformat(state["last_run"])
            self.assertEqual(security_monitor.main(), 0)

        self.assertEqual(observed_windows[0], (since, since + timedelta(minutes=15, seconds=-1)))
        self.assertEqual(first_cursor, since + timedelta(minutes=15))
        self.assertEqual(observed_windows[1][0], first_cursor)
        self.assertEqual(
            observed_windows[1][1], first_cursor + timedelta(minutes=15, seconds=-1)
        )

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
        mock_fail2ban.return_value = security_monitor.Fail2banScan()

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
            security_monitor, "managed_setup_audit_windows", return_value=[window]
        ), patch.object(
            security_monitor, "_check_fail2ban", return_value=security_monitor.Fail2banScan()
        ) as fail2ban, patch.object(
            security_monitor, "_check_auditd", return_value=([], False, [])
        ) as auditd, patch.object(
            security_monitor, "_check_ssh_failures", return_value=(0, None)
        ) as ssh, patch.object(
            security_monitor, "inspect_xrdp_certificate", return_value=certificate
        ), patch.object(security_monitor, "_save_state"):
            self.assertEqual(security_monitor.main(), 0)

        self.assertEqual(auditd.call_args.kwargs["excluded_windows"], [window])
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
    @patch("security.service_tools.security_monitor._check_fail2ban", return_value=security_monitor.Fail2banScan())
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
    @patch("security.service_tools.security_monitor._check_fail2ban", return_value=security_monitor.Fail2banScan())
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
    @patch("security.service_tools.security_monitor._check_fail2ban", return_value=security_monitor.Fail2banScan())
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
    @patch("security.service_tools.security_monitor._check_fail2ban", return_value=security_monitor.Fail2banScan())
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
        return_value=security_monitor.Fail2banScan(
            unbans=[{"type": "fail2ban", "action": "unban", "jail": "sshd", "source_ip": "192.0.2.4"}],
            unban_count=1,
        ),
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
    @patch("security.service_tools.security_monitor._check_fail2ban", return_value=security_monitor.Fail2banScan())
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

    @patch("security.service_tools.security_monitor._audit_tool", return_value="/usr/sbin/ausearch")
    @patch("security.service_tools.security_monitor.run_streamed")
    def test_ausearch_no_matches_is_not_a_collection_error(self, mock_run, mock_tool):
        def query(_command, *, on_error, **_kwargs):
            on_error('<no matches>\n')
            return 1
        mock_run.side_effect = query
        since = datetime(2026, 8, 22, 12)
        events, error = security_monitor._ausearch_events(
            "identity", since, since + timedelta(minutes=15)
        )

        self.assertEqual(events, [])
        self.assertIsNone(error)
        mock_tool.assert_called_once_with("ausearch")
        self.assertEqual(mock_run.call_args.args[0][0], "/usr/sbin/ausearch")
        self.assertIn('--input-logs', mock_run.call_args.args[0])
        self.assertIn('08/22/26', mock_run.call_args.args[0])
        self.assertEqual(mock_run.call_args.kwargs['env']['LC_ALL'], 'C')
        self.assertIn("--end", mock_run.call_args.args[0])
        self.assertEqual(mock_run.call_args.kwargs["timeout"], 60)

    @patch("security.service_tools.security_monitor._audit_tool", return_value="/usr/sbin/ausearch")
    @patch("security.service_tools.security_monitor.run_streamed")
    def test_ausearch_oversized_record_is_a_collection_error(self, mock_run, _tool):
        def stream(_command, *, on_output, **_kwargs):
            on_output("x" * (security_monitor._MAX_SOURCE_LINE_BYTES + 1))
            return 0

        mock_run.side_effect = stream
        since = datetime(2026, 8, 22, 12)
        events, error = security_monitor._ausearch_events(
            "identity", since, since + timedelta(minutes=15)
        )

        self.assertEqual(events, [])
        self.assertIn("oversized record", error)

    @patch('security.service_tools.security_monitor._audit_tool', return_value='/usr/sbin/ausearch')
    @patch('security.service_tools.security_monitor.run_streamed')
    def test_audit_exit_one_distinguishes_no_matches_from_read_errors(self, mock_run, _tool):
        since = datetime(2026, 8, 22, 12)
        for message, failed in (('<no matches>\n', False), ('Error opening audit.log\n', True), ('', True)):
            def stream(_command, *, on_error, **_kwargs):
                on_error(message)
                return 1
            mock_run.side_effect = stream
            with self.subTest(message=message):
                events, error = security_monitor._ausearch_events('identity', since, since)
                self.assertEqual(events, [])
                self.assertEqual(error is not None, failed)

    @patch("security.service_tools.security_monitor._audit_tool", return_value="/usr/sbin/ausearch")
    @patch("security.service_tools.security_monitor.run_streamed")
    def test_ausearch_streams_more_than_two_megabytes(self, mock_run, _tool):
        def stream(_command, *, on_output, **_kwargs):
            for _ in range(3000):
                on_output("----\n")
                on_output(
                    'type=PATH msg=audit(1766400000.100:1): '
                    'name="/etc/passwd" extra=' + 'x' * 700 + '\n'
                )
            return 0

        mock_run.side_effect = stream
        since = datetime(2026, 8, 22, 12)
        events, error = security_monitor._ausearch_events(
            "identity", since, since + timedelta(minutes=15)
        )

        self.assertIsNone(error)
        self.assertEqual(events[0]["event_count"], 3000)
        self.assertEqual(events[0]["paths"], ["/etc/passwd"])

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
            "identity", output, [(setup_start, setup_end)]
        )

        self.assertEqual(events[0]["event_count"], 1)
        expected = datetime.fromtimestamp(1766400300.1).isoformat(timespec="seconds")
        self.assertEqual(events[0]["first_seen"], expected)

    def test_interpreted_ausearch_timestamp_is_excluded(self):
        for date in ('08/22/2026', '08/22/26'):
            output = f'----\ntype=PATH msg=audit({date} 12:00:00.100:1): name="/etc/passwd"\n'
            with self.subTest(date=date):
                events = security_monitor._parse_audit_events(
                    'identity', output,
                    [(datetime(2026, 8, 22, 11, 59), datetime(2026, 8, 22, 12, 1))],
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
            ban_count=0,
            unban_count=0,
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

        since = datetime.now()
        events, critical, errors = security_monitor._check_auditd(
            since, since + timedelta(minutes=15)
        )

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
            since = datetime.now()
            events, critical, errors = security_monitor._check_auditd(
                since, since + timedelta(minutes=15)
            )

        self.assertEqual(events, [])
        self.assertFalse(critical)
        self.assertEqual(errors, ["auditd: ausearch command unavailable"])

    @patch("security.service_tools.security_monitor._ausearch_events")
    @patch("security.service_tools.security_monitor._audit_health")
    @patch("security.service_tools.security_monitor._audit_tool", return_value="/usr/sbin/ausearch")
    def test_required_audit_coverage_checks_daemon_and_rules_before_events(
        self, _tool, mock_health, mock_events
    ):
        mock_health.return_value = ("degraded", ["Expected audit rules are not loaded: sudoers."])
        with patch.dict(os.environ, {"BASALTWATER_AUDIT_REQUIRED": "1"}):
            since = datetime.now()
            events, critical, errors = security_monitor._check_auditd(
                since, since + timedelta(minutes=15)
            )

        self.assertEqual(events, [])
        self.assertFalse(critical)
        self.assertEqual(errors, ["auditd: Expected audit rules are not loaded: sudoers."])
        mock_events.assert_not_called()

    @patch("security.service_tools.security_monitor.run_streamed")
    def test_ssh_failures_are_aggregated_by_source_user_and_method(self, mock_run):
        mock_run.side_effect = _stream_output(
            "\n".join([
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

        since = datetime(2026, 8, 22, 12)
        summary, error = security_monitor._check_ssh_failures(
            since, since + timedelta(minutes=15)
        )

        self.assertIsNone(error)
        self.assertEqual(summary["failure_count"], 3)
        self.assertEqual(summary["sources"][0]["source_ip"], "192.0.2.4")
        self.assertEqual(summary["sources"][0]["count"], 1)
        self.assertEqual(
            {source["method"] for source in summary["sources"]},
            {"password", "publickey", "unknown"},
        )
        self.assertEqual(mock_run.call_args.kwargs["timeout"], 60)
        self.assertIn("--until", mock_run.call_args.args[0])

    @patch("security.service_tools.security_monitor.run_streamed", side_effect=TimeoutError)
    def test_ssh_journal_timeout_is_a_collection_error(self, _run):
        since = datetime(2026, 8, 22, 12)
        summary, error = security_monitor._check_ssh_failures(
            since, since + timedelta(minutes=15)
        )

        self.assertEqual(summary["failure_count"], 0)
        self.assertIn("timed out", error)

    @patch("security.service_tools.security_monitor.run_streamed")
    def test_ssh_journal_invalid_timestamp_keeps_failure(self, mock_run):
        mock_run.side_effect = _stream_output(
            json.dumps({
                "MESSAGE": "Failed password for root from 192.0.2.4 port 22 ssh2",
                "_SOURCE_REALTIME_TIMESTAMP": "9" * 100,
            }),
        )

        since = datetime(2026, 8, 22, 12)
        summary, error = security_monitor._check_ssh_failures(
            since, since + timedelta(minutes=15)
        )

        self.assertIsNone(error)
        self.assertEqual(summary["failure_count"], 1)

    @patch("security.service_tools.security_monitor.run_streamed")
    def test_ssh_journal_streams_large_output_with_bounded_breakdown(self, mock_run):
        def stream(_command, *, on_output, **_kwargs):
            for index in range(3000):
                on_output(json.dumps({
                    "MESSAGE": f"Failed password for root from 192.0.2.{index % 256} port 22 ssh2",
                    "PADDING": "x" * 700,
                }) + "\n")
            return 0

        mock_run.side_effect = stream
        since = datetime(2026, 8, 22, 12)
        summary, error = security_monitor._check_ssh_failures(
            since, since + timedelta(minutes=15)
        )

        self.assertIsNone(error)
        self.assertEqual(summary["failure_count"], 3000)
        self.assertEqual(len(summary["sources"]), 10)

    @patch("security.service_tools.security_monitor.run_streamed")
    def test_ssh_journal_discards_partial_scan_after_failure(self, mock_run):
        def stream(_command, *, on_output, **_kwargs):
            on_output(json.dumps({
                "MESSAGE": "Failed password for root from 192.0.2.4 port 22 ssh2",
            }) + "\n")
            raise TimeoutError("journal stalled")

        mock_run.side_effect = stream
        since = datetime(2026, 8, 22, 12)
        summary, error = security_monitor._check_ssh_failures(
            since, since + timedelta(minutes=15)
        )

        self.assertEqual(summary["failure_count"], 0)
        self.assertIn("timed out", error)

    @patch("security.service_tools.security_monitor.run_streamed")
    def test_ssh_source_cardinality_does_not_expand_breakdown_without_limit(self, mock_run):
        def stream(_command, *, on_output, **_kwargs):
            for index in range(1200):
                on_output(json.dumps({
                    "MESSAGE": (
                        f"Failed password for user{index} from "
                        "192.0.2.4 port 22 ssh2"
                    ),
                }) + "\n")
            return 0

        mock_run.side_effect = stream
        since = datetime(2026, 8, 22, 12)
        summary, error = security_monitor._check_ssh_failures(
            since, since + timedelta(minutes=15)
        )

        self.assertIsNone(error)
        self.assertEqual(summary["failure_count"], 1200)
        self.assertEqual(summary["unattributed_failures"], 200)
        self.assertEqual(len(summary["sources"]), 10)

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
                scan = security_monitor._check_fail2ban(
                    datetime(2026, 8, 22, 12, 0, 0),
                    datetime(2026, 8, 22, 12, 15, 0),
                )
                first_scan = security_monitor._check_fail2ban(
                    datetime(2026, 8, 22, 12, 0, 0),
                    datetime(2026, 8, 22, 12, 1, 59),
                )

        self.assertIsNone(scan.error)
        self.assertEqual(scan.ban_count, 1)
        self.assertEqual(scan.unban_count, 1)
        self.assertEqual(scan.bans[0]["jail"], "nginx-http-auth")
        self.assertEqual(scan.bans[0]["source_ip"], "192.0.2.4")
        self.assertEqual(scan.unbans[0]["action"], "unban")
        self.assertIsNone(first_scan.error)
        self.assertEqual(first_scan.ban_count, 1)
        self.assertEqual(first_scan.unbans, [])

    def test_fail2ban_volume_keeps_exact_counts_and_bounded_examples(self):
        with tempfile.TemporaryDirectory() as log_dir:
            log_path = os.path.join(log_dir, "fail2ban.log")
            with open(log_path, "w", encoding="utf-8") as log_file:
                for index in range(1200):
                    action = "Ban" if index < 1000 else "Unban"
                    log_file.write(
                        f"2026-08-22 12:01:02,123 fail2ban.actions [1]: "
                        f"WARNING [sshd] {action} 192.0.2.4\n"
                    )

            with patch("security.service_tools.security_monitor._FAIL2BAN_LOG", log_path):
                scan = security_monitor._check_fail2ban(
                    datetime(2026, 8, 22, 12),
                    datetime(2026, 8, 22, 12, 14, 59),
                )

        self.assertIsNone(scan.error)
        self.assertEqual((scan.ban_count, scan.unban_count), (1000, 200))
        self.assertEqual(len(scan.bans), 50)
        self.assertEqual(len(scan.unbans), 50)
        data = security_monitor._build_security_data(
            since=datetime(2026, 8, 22, 12),
            now=datetime(2026, 8, 22, 12, 14, 59),
            status="warning",
            bans=scan.bans,
            unbans=scan.unbans,
            ban_count=scan.ban_count,
            unban_count=scan.unban_count,
            audit_keys=[],
            ssh_failures=0,
            certificate_event=None,
        )
        self.assertEqual(data["counts"]["fail2ban_bans"], 1000)
        self.assertEqual(data["counts"]["fail2ban_unbans"], 200)
        self.assertEqual(data["counts"]["fail2ban_bans_omitted"], 950)
        self.assertEqual(len(data["events"]), 100)

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
        return_value=security_monitor.Fail2banScan(
            bans=[{"type": "fail2ban", "action": "ban", "jail": "sshd", "source_ip": "192.0.2.4"}],
            ban_count=100,
        ),
    )
    @patch("security.service_tools.security_monitor._load_state", return_value={})
    @patch("security.service_tools.security_monitor.load_notification_configs_from_state", return_value=["cfg"])
    def test_fail2ban_notification_uses_exact_count_and_marks_omissions(
        self, _configs, _state, _fail2ban, _audit, _ssh, mock_notify, _save,
        _certificate,
    ):
        self.assertEqual(security_monitor.main(), 0)

        notification = mock_notify.call_args.kwargs
        self.assertIn("100 fail2ban bans", notification["subject"])
        self.assertIn("Fail2ban bans: 100", notification["details"])
        self.assertIn("99 more fail2ban event(s) omitted", notification["details"])
        self.assertEqual(notification["data"]["counts"]["fail2ban_bans"], 100)
        self.assertEqual(len(notification["data"]["events"]), 1)

    def test_fail2ban_oversized_line_is_collection_error(self):
        with tempfile.TemporaryDirectory() as log_dir:
            log_path = os.path.join(log_dir, "fail2ban.log")
            with open(log_path, "w", encoding="utf-8") as log_file:
                log_file.write("x" * (security_monitor._MAX_SOURCE_LINE_BYTES + 1))

            with patch("security.service_tools.security_monitor._FAIL2BAN_LOG", log_path):
                scan = security_monitor._check_fail2ban(
                    datetime(2026, 8, 22, 12),
                    datetime(2026, 8, 22, 12, 14, 59),
                )

        self.assertIn("size limit", scan.error)

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


class TestSecurityRetention(unittest.TestCase):
    @patch.object(security_retention, 'run_captured')
    @patch.object(security_retention, 'run_streamed')
    def test_global_oldest_records_use_receipt_time_and_unfiltered_audit_logs(self, stream, capture):
        stream.side_effect = _stream_output(json.dumps({
            '__REALTIME_TIMESTAMP': '1766400000999999',
            '_SOURCE_REALTIME_TIMESTAMP': '1',
        }))
        capture.return_value = SimpleNamespace(
            returncode=0, stderr='',
            stdout='type=DAEMON_START msg=audit(1766400000.100:1): op=start\n',
        )
        floors, errors = security_retention.inspect_retention('/usr/sbin/ausearch')
        self.assertEqual(errors, [])
        self.assertEqual(floors, dict.fromkeys(('auditd', 'ssh_journal'), datetime.fromtimestamp(1766400000)))
        journal_command = stream.call_args.args[0]
        self.assertIn('--system', journal_command)
        self.assertIn('--no-tail', journal_command)
        self.assertNotIn('-u', journal_command)
        self.assertNotIn('--since', journal_command)
        self.assertNotIn('--lines=+1', journal_command)  # Also supports Bookworm's systemd.
        self.assertEqual(capture.call_args.args[0],
                         ['/usr/sbin/ausearch', '--input-logs', '--raw', '--just-one'])
        self.assertEqual(capture.call_args.kwargs['max_bytes'], 256 * 1024)
        self.assertEqual(capture.call_args.kwargs['timeout'], 15)
        self.assertEqual(stream.call_args.kwargs['timeout'], 15)

    @patch.object(security_retention, 'run_streamed')
    def test_missing_malformed_and_failed_journal_evidence_is_unknown(self, stream):
        for output in ('', '{}', '[]', 'not json', '{"__REALTIME_TIMESTAMP":"99999999999999999999"}'):
            with self.subTest(output=output):
                stream.side_effect = _stream_output(output)
                floors, errors = security_retention.inspect_retention(None)
                self.assertEqual(floors, {})
                self.assertIn('SSH journal: retention evidence unavailable', errors[0])
        for failure in (TimeoutError(), OSError('denied')):
            stream.side_effect = failure
            self.assertEqual(security_retention.inspect_retention(None)[0], {})

    @patch.object(security_retention, 'run_streamed')
    def test_journal_warning_does_not_establish_coverage(self, stream):
        def query(_command, *, on_error, **_kwargs):
            on_error('Journal file is truncated, ignoring file.\n')
            return 0
        stream.side_effect = query
        self.assertEqual(security_retention.inspect_retention(None)[0], {})

    @patch.object(security_retention, '_oldest_journal', return_value=datetime(2026, 8, 1))
    @patch.object(security_retention, 'run_captured')
    def test_empty_failed_or_oversized_audit_metadata_is_unknown(self, capture, _journal):
        for code, output, error in ((1, '', '<no matches>'), (0, '', ''),
                                    (0, 'invalid record', ''), (0, '', 'Permission denied')):
            capture.return_value = SimpleNamespace(returncode=code, stdout=output, stderr=error)
            floors, errors = security_retention.inspect_retention('/usr/sbin/ausearch')
            self.assertEqual(set(floors), {'ssh_journal'})
            self.assertIn('auditd: retention evidence unavailable', errors[0])
        capture.side_effect = security_retention.CommandOutputLimitError('too large')
        self.assertEqual(set(security_retention.inspect_retention('/usr/sbin/ausearch')[0]), {'ssh_journal'})

    @patch.object(security_retention, 'run_captured')
    def test_audit_argument_text_cannot_forge_an_earlier_retention_floor(self, capture):
        capture.return_value = SimpleNamespace(
            returncode=0, stderr='', stdout=(
                'node=fixture type=EXECVE msg=audit(1766400000.100:1): '
                'a0="echo" a1="msg=audit(0:2):"\n'
            ),
        )
        self.assertEqual(security_retention._oldest_audit('/usr/sbin/ausearch'),
                         datetime.fromtimestamp(1766400000))

    def test_gap_boundaries_are_exclusive_and_history_is_bounded_and_retained(self):
        start = datetime(2026, 8, 22, 12)
        end = start + timedelta(minutes=15, seconds=-1)
        first = security_retention.window_gaps({'auditd': end + timedelta(hours=1)}, start, end)
        self.assertEqual(first, [{'source': 'auditd', 'since': start.isoformat(),
                                  'until': (end + timedelta(seconds=1)).isoformat()}])
        next_gap = security_retention.window_gaps({'auditd': end + timedelta(hours=1)}, end + timedelta(seconds=1), end + timedelta(minutes=15))
        history = security_retention.retained_gaps({'retention_gaps': [None, {'source': []}, *first]}, next_gap)
        self.assertEqual(history[0]['since'], start.isoformat())
        self.assertEqual(history[0]['until'], (end + timedelta(minutes=15, seconds=1)).isoformat())
        self.assertEqual(security_retention.retained_gaps({'retention_gaps': history}, []), history)
        self.assertEqual(security_retention.window_gaps({'auditd': start}, start, end), [])


class TestSecurityRetentionLifecycle(unittest.TestCase):
    def setUp(self):
        self.start = (datetime.now() - timedelta(minutes=45)).replace(microsecond=0)
        self.end = self.start + timedelta(minutes=15, seconds=-1)
        self.state = {'last_run': self.start.isoformat()}
        self.health = XrdpCertificateHealth('not_configured', '', '')
        self.enterContext(patch.object(security_monitor, 'load_notification_configs_from_state', return_value=['config']))
        self.enterContext(patch.object(security_monitor, '_load_state', side_effect=lambda: dict(self.state)))
        self.save = self.enterContext(patch.object(security_monitor, '_save_state', side_effect=self.persist))
        self.notify = self.enterContext(patch.object(security_monitor, 'send_notification_safe', return_value=False))
        self.enterContext(patch.object(security_monitor, 'inspect_xrdp_certificate', return_value=self.health))
        self.fail2ban = self.enterContext(patch.object(security_monitor, '_check_fail2ban', return_value=security_monitor.Fail2banScan()))
        self.audit = self.enterContext(patch.object(security_monitor, '_check_auditd', return_value=([], False, [])))
        self.ssh = self.enterContext(patch.object(security_monitor, '_check_ssh_failures', return_value=(0, None)))
        self.floors = {'auditd': self.start - timedelta(days=1), 'ssh_journal': self.start - timedelta(days=1)}
        self.retention = self.enterContext(patch.object(security_monitor, '_check_retention', side_effect=lambda: (dict(self.floors), [])))

    def persist(self, state):
        self.state.clear()
        self.state.update(state)

    def test_gap_reports_missing_coverage_and_preserves_healthy_source_findings(self):
        floor = self.start + timedelta(minutes=10)
        self.floors['auditd'] = floor
        self.fail2ban.return_value = security_monitor.Fail2banScan(
            bans=[{'type': 'fail2ban', 'timestamp': (self.start + timedelta(minutes=1)).isoformat(),
                   'action': 'ban', 'jail': 'sshd', 'source_ip': '192.0.2.4'}], ban_count=1)
        self.assertEqual(security_monitor.main(), 1)
        self.assertEqual(self.state['last_run'], (self.end + timedelta(seconds=1)).isoformat())
        for check in (self.fail2ban, self.audit, self.ssh):
            self.assertEqual(check.call_args.args[:2], (self.start, self.end))
        notification = self.notify.call_args.kwargs
        self.assertEqual(notification['status'], 'error')
        self.assertEqual(notification['dedup_key'], 'security_monitor:retention-gap')
        events = notification['data']['events']
        gap = next(event for event in events if event['type'] == 'source_retention_gap')
        self.assertEqual((gap['since'], gap['until']), (self.start.isoformat(), floor.isoformat()))
        self.assertEqual(notification['data']['counts']['fail2ban_bans'], 1)
        self.assertIn('not checked as clean', notification['details'])
        history = self.state['retention_gaps']
        self.notify.reset_mock()
        self.fail2ban.return_value = security_monitor.Fail2banScan()
        self.assertEqual(security_monitor.main(), 0)
        self.assertEqual(self.state['retention_gaps'], history)
        self.notify.assert_not_called()  # Historical loss is not announced as recovered.

    def test_empty_but_known_window_advances_only_one_window_and_saves_gap(self):
        self.floors['auditd'] = self.end + timedelta(days=1)
        self.assertEqual(security_monitor.main(), 1)
        self.assertEqual(self.state['last_run'], (self.end + timedelta(seconds=1)).isoformat())
        self.assertEqual(self.state['retention_gaps'][0]['until'], self.state['last_run'])
        self.assertNotIn('collection_errors', self.state)

    def test_unknown_retention_freezes_cursor_and_suppresses_identical_error(self):
        self.retention.side_effect = lambda: ({}, ['SSH journal: retention evidence unavailable; interval coverage is unknown'])
        self.assertEqual(security_monitor.main(), 1)
        self.assertEqual(self.state['last_run'], self.start.isoformat())
        self.assertIn('retention evidence unavailable', self.state['collection_errors'][0])
        self.notify.reset_mock()
        self.assertEqual(security_monitor.main(), 1)
        self.notify.assert_not_called()

    def test_postflight_rotation_reports_new_floor_and_disappearing_source_freezes(self):
        initial = dict(self.floors)
        final = {**initial, 'auditd': self.start + timedelta(minutes=5)}
        self.retention.side_effect = [(initial, []), (final, [])]
        self.assertEqual(security_monitor.main(), 1)
        self.assertEqual(self.state['retention_gaps'][0]['until'], final['auditd'].isoformat())
        retained = dict(self.state)
        self.retention.side_effect = [(initial, []), ({'ssh_journal': initial['ssh_journal']}, [])]
        self.assertEqual(security_monitor.main(), 1)
        self.assertEqual(self.state['last_run'], retained['last_run'])
        self.assertEqual(self.state['retention_gaps'], retained['retention_gaps'])
        self.assertIn('changed during collection', self.state['collection_errors'][0])

    def test_first_observation_reports_coverage_without_permanent_stall(self):
        self.state.clear()
        self.floors = dict.fromkeys(self.floors, datetime.now().replace(microsecond=0))
        self.assertEqual(security_monitor.main(), 1)
        self.assertEqual(len(self.state['retention_gaps']), 2)
        self.assertGreater(datetime.fromisoformat(self.state['last_run']), datetime.now() - timedelta(seconds=3))

    def test_gap_and_cursor_are_one_atomic_state_write_even_if_notification_fails(self):
        self.floors['auditd'] = self.start + timedelta(minutes=2)
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'state.json')
            # Use the real atomic writer while notification delivery returns False.
            from lib.atomic_io import write_json_atomic
            self.save.side_effect = lambda state: write_json_atomic(path, state, mode=0o600)
            self.assertEqual(security_monitor.main(), 1)
            with open(path) as source:
                saved = json.load(source)
            self.assertEqual(saved['last_run'], (self.end + timedelta(seconds=1)).isoformat())
            self.assertEqual(saved['retention_gaps'][0]['since'], self.start.isoformat())
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.save.side_effect = OSError('disk full')
        with self.assertRaises(OSError):
            security_monitor.main()
        self.assertEqual(self.state['last_run'], self.start.isoformat())


if __name__ == "__main__":
    unittest.main()
