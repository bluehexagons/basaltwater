"""Notification identity and retry behavior at the web-panel ingest boundary."""

from __future__ import annotations

import http.client
import json
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

from common.service_tools.web_panel_service import (
    WebPanelHandler,
    WebPanelState,
    _ThreadingTCPHTTPServer,
    render_page,
)
from common.web_panel_events import (
    WEB_PANEL_NOTIFICATION_ENDPOINT,
    append_notification_event,
    load_notification_events,
    validate_notification_payload,
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
