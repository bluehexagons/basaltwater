"""Notification identity and retry behavior at the web-panel ingest boundary."""

from __future__ import annotations

import copy
import http.client
import json
import os
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from common.service_tools.web_panel_service import (
    WebPanelHandler,
    WebPanelState,
    _render_notification_section,
    _ThreadingTCPHTTPServer,
    render_page,
)
from common.web_panel_events import (
    WEB_PANEL_NOTIFICATION_ENDPOINT,
    append_notification_event,
    load_notification_events,
    validate_notification_payload,
    unresolved_notification_alerts,
)


def _notification(event_id: str = "a" * 32) -> dict[str, object]:
    return {
        "schema_version": 2,
        "event": {
            "id": event_id,
            "occurred_at": "2026-09-03T10:00:00+00:00",
            "type": "backup",
            "state": "firing",
            "status": "warning",
            "deduplication_key": "backup:agent-2",
        },
        "operator": {
            "subject": "Backup needs attention",
            "job": "backup",
            "system": "agent-2",
            "what_happened": "The latest backup did not complete.",
            "suggested_actions": ["Check the backup service"],
            "details": "Exit status 1",
        },
        "data": {"attempt": 3},
    }


def _manifest() -> dict[str, object]:
    return {
        "version": 1,
        "title": "Notification receiver",
        "host": "panel.example",
        "system_type": "server_dev",
        "username": "agent",
        "services": [],
        "access": [],
        "features": {"t3_update": False, "notification_ingest": True},
    }


class WebPanelNotificationIdempotencyTest(unittest.TestCase):
    def _record(self, *, state: str = "firing", status: str = "warning", source: str = "192.0.2.10", **event: object) -> dict[str, object]:
        notification = _notification()
        notification["event"].update(state=state, status=status, **event)
        return {"notification": notification, "source_ip": source, "received_at": "2026-10-04T12:00:00+00:00"}

    def test_repeated_alerts_coalesce_and_recovery_clears_episode(self) -> None:
        first = self._record()
        first["received_at"] = "2026-10-04T11:00:00+00:00"
        newest = self._record(status="error")
        alerts = unresolved_notification_alerts([newest, first])
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["count"], 2)
        self.assertEqual(alerts[0]["record"], newest)
        self.assertEqual(alerts[0]["first_received_at"], first["received_at"])
        for state in ("resolved", "success"):
            recovered = self._record(state=state, status="good")
            self.assertEqual(unresolved_notification_alerts([recovered, newest, first]), [])
            reopened = unresolved_notification_alerts([newest, recovered, first])
            self.assertEqual(reopened[0]["count"], 1)
            self.assertEqual(reopened[0]["first_received_at"], newest["received_at"])

    def test_recovery_cannot_clear_other_source_system_or_event_type(self) -> None:
        first = self._record()
        for kind in ("source", "system", "type", "key"):
            recovered = self._record(state="resolved", status="good")
            if kind == "source":
                recovered["source_ip"] = "192.0.2.11"
            elif kind == "system":
                recovered["notification"]["operator"]["system"] = "other-machine"
            else:
                recovered["notification"]["event"]["type" if kind == "type" else "deduplication_key"] = "other"
            with self.subTest(kind=kind):
                self.assertEqual(len(unresolved_notification_alerts([recovered, first])), 1)

    def test_unkeyed_or_unattributed_events_stay_separate(self) -> None:
        for changes in ({"deduplication_key": None}, {"source": "unknown"}):
            first = self._record(**changes)
            newest = self._record(**changes)
            recovery = self._record(state="resolved", **changes)
            alerts = unresolved_notification_alerts([recovery, newest, first])
            self.assertEqual(len(alerts), 2)

    def test_receipt_order_wins_over_sender_clock_and_inputs_are_preserved(self) -> None:
        first = self._record(occurred_at="2099-01-01T00:00:00+00:00")
        recovery = self._record(state="resolved", occurred_at="2000-01-01T00:00:00+00:00")
        records = [recovery, first]
        original = copy.deepcopy(records)
        self.assertEqual(unresolved_notification_alerts(records), [])
        self.assertEqual(records, original)

    def test_dashboard_summary_is_escaped_and_history_remains_available(self) -> None:
        record = self._record()
        record["notification"]["operator"]["subject"] = "Backup <script>"
        state = Mock()
        state.notification_events.return_value = [record, record]
        state.notification_ingest_url.return_value = None
        page = _render_notification_section(state)
        self.assertIn("1 unresolved · 2 received", page)
        self.assertIn("2 reports", page)
        self.assertIn("Backup &lt;script&gt;", page)
        self.assertNotIn("<script>", page)
        self.assertIn("Notification history (2 received)", page)

    def test_duplicate_event_id_is_retained_only_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            notification_path = os.path.join(temporary, "events.jsonl")

            first = append_notification_event(
                _notification(),
                "192.0.2.10",
                path=notification_path,
            )
            duplicate = append_notification_event(
                _notification(),
                "192.0.2.11",
                path=notification_path,
            )
            events = load_notification_events(notification_path)

        self.assertTrue(first)
        self.assertFalse(duplicate)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["source_ip"], "192.0.2.10")

    def test_event_identity_and_occurrence_time_are_strictly_validated(self) -> None:
        invalid_id = _notification("short")
        with self.assertRaisesRegex(ValueError, "event.id"):
            validate_notification_payload(invalid_id)

        invalid_time = _notification()
        invalid_time["event"]["occurred_at"] = "2026-09-03T10:00:00"
        with self.assertRaisesRegex(ValueError, "event.occurred_at"):
            validate_notification_payload(invalid_time)

    def test_ingest_reports_duplicate_retry_without_adding_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            token_path = os.path.join(temporary, "token")
            notification_path = os.path.join(temporary, "events.jsonl")
            with open(token_path, "w", encoding="utf-8") as file_obj:
                file_obj.write("t" * 43 + "\n")
            state = WebPanelState(
                _manifest(),
                audit_snapshot_path=os.path.join(temporary, "audit.json"),
                notification_log_path=notification_path,
                ingest_token_path=token_path,
            )
            WebPanelHandler.state = state
            server = _ThreadingTCPHTTPServer(("127.0.0.1", 0), WebPanelHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            responses: list[tuple[int, dict[str, object]]] = []
            try:
                for _attempt in range(2):
                    connection = http.client.HTTPConnection(
                        "127.0.0.1", server.server_address[1], timeout=5
                    )
                    connection.request(
                        "POST",
                        WEB_PANEL_NOTIFICATION_ENDPOINT,
                        body=json.dumps(_notification()),
                        headers={
                            "Authorization": "Bearer " + "t" * 43,
                            "Content-Type": "application/json",
                            "X-Forwarded-Proto": "https",
                            "X-Real-IP": "192.0.2.45",
                        },
                    )
                    response = connection.getresponse()
                    responses.append(
                        (response.status, json.loads(response.read()))
                    )
                    connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

            events = state.notification_events()

        self.assertEqual(
            responses,
            [
                (202, {"accepted": True, "duplicate": False}),
                (200, {"accepted": True, "duplicate": True}),
            ],
        )
        self.assertEqual(len(events), 1)

    def test_page_distinguishes_occurrence_time_from_receipt_time(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            token_path = os.path.join(temporary, "token")
            notification_path = os.path.join(temporary, "events.jsonl")
            with open(token_path, "w", encoding="utf-8") as file_obj:
                file_obj.write("t" * 43 + "\n")
            append_notification_event(
                _notification(),
                "192.0.2.45",
                path=notification_path,
            )
            state = WebPanelState(
                _manifest(),
                audit_snapshot_path=os.path.join(temporary, "audit.json"),
                notification_log_path=notification_path,
                ingest_token_path=token_path,
            )
            with (
                patch(
                    "common.service_tools.web_panel_service.discover_basaltwater_web_services",
                    return_value=[],
                ),
                patch(
                    "common.service_tools.web_panel_service.discover_certificate_trust",
                    return_value=None,
                ),
                patch.object(state, "system_overview", return_value=[]),
            ):
                rendered = render_page(state)

        self.assertIn(
            "reported occurrence 2026-09-03T10:00:00+00:00",
            rendered,
        )
        self.assertIn("received ", rendered)
        self.assertIn("from 192.0.2.45", rendered)


if __name__ == "__main__":
    unittest.main()
