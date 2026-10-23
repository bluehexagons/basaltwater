"""Tests for administrative T3 Code remote-environment pairing."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from common.service_tools import t3code_admin_pair


class T3CodeAdministrativePairingTest(unittest.TestCase):
    def test_remote_server_urls_fail_before_issuing_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            t3_binary = os.path.join(temporary, "t3")
            base_dir = os.path.join(temporary, "state")
            os.mkdir(base_dir)
            with open(t3_binary, "w", encoding="utf-8") as file_obj:
                file_obj.write("#!/bin/sh\n")
            with (
                patch.object(t3code_admin_pair, "_issue_temporary_session") as issue,
                patch.object(t3code_admin_pair, "_request_pairing_link") as request,
            ):
                for host in (
                    "attacker.example", "203.0.113.9", "192.168.1.9", "0.0.0.0",
                    "[2001:db8::9]", "[fd00::9]", "[::]",
                ):
                    with self.subTest(host=host), self.assertRaisesRegex(
                        ValueError, "Invalid local T3 server URL"
                    ):
                        t3code_admin_pair.issue_administrative_pairing(
                            t3_binary, base_dir, f"http://{host}:3773",
                            "https://agent.example", "Remote app",
                        )
                issue.assert_not_called()
                request.assert_not_called()

    def test_local_server_urls_accept_only_loopback_or_localhost(self) -> None:
        for host in ("127.0.0.1", "127.12.34.56", "[::1]", "localhost"):
            with self.subTest(host=host):
                url = f"http://{host}:3773"
                self.assertEqual(t3code_admin_pair._validated_url(
                    url, "local T3 server URL", local_only=True,
                ), url)

    def test_public_base_url_can_use_remote_ipv6(self) -> None:
        url = "https://[2001:db8::9]:3773"
        self.assertEqual(t3code_admin_pair._validated_url(url, "public T3 base URL"), url)
        self.assertEqual(t3code_admin_pair._pairing_url(url, "credential"),
                         url + "/pair#token=credential")

    def test_url_rejects_invalid_port(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid local T3 server URL"):
            t3code_admin_pair._validated_url(
                "http://127.0.0.1:65536",
                "local T3 server URL",
                local_only=True,
            )

    def test_issues_admin_link_and_revokes_temporary_session(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            t3_binary = os.path.join(temporary, "t3")
            base_dir = os.path.join(temporary, "state")
            os.mkdir(base_dir)
            with open(t3_binary, "w", encoding="utf-8") as file_obj:
                file_obj.write("#!/bin/sh\n")

            with (
                patch.object(
                    t3code_admin_pair,
                    "_issue_temporary_session",
                    return_value=(
                        "session-1", "temporary-secret", t3code_admin_pair.ADMINISTRATIVE_SCOPES
                    ),
                ),
                patch.object(
                    t3code_admin_pair,
                    "_request_pairing_link",
                    return_value={
                        "id": "pairing-1",
                        "credential": "one-time-secret",
                        "expiresAt": "2026-08-21T20:00:00Z",
                    },
                ) as request_pairing,
                patch.object(
                    t3code_admin_pair,
                    "_revoke_session",
                    return_value=True,
                ) as revoke_session,
            ):
                result = t3code_admin_pair.issue_administrative_pairing(
                    t3_binary,
                    base_dir,
                    "http://127.0.0.1:3773",
                    "https://agent.example:3773",
                    "Remote app",
                )

        self.assertEqual(
            result["pairUrl"],
            "https://agent.example:3773/pair#token=one-time-secret",
        )
        self.assertIn("access:write", result["scopes"])
        request_pairing.assert_called_once_with(
            "http://127.0.0.1:3773",
            "temporary-secret",
            "Remote app",
            t3code_admin_pair.ADMINISTRATIVE_SCOPES,
        )
        revoke_session.assert_called_once_with(t3_binary, base_dir, "session-1")

    def test_api_request_delegates_all_administrative_scopes(self) -> None:
        response = MagicMock()
        response.read.return_value = json.dumps(
            {
                "id": "pairing-1",
                "credential": "one-time-secret",
                "expiresAt": "2026-08-21T20:00:00Z",
            }
        ).encode("utf-8")
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value = response

        with patch.object(
            t3code_admin_pair.urllib.request,
            "build_opener",
            return_value=opener,
        ) as build_opener:
            result = t3code_admin_pair._request_pairing_link(
                "http://127.0.0.1:3773",
                "temporary-secret",
                "Remote app",
                t3code_admin_pair.ADMINISTRATIVE_SCOPES,
            )

        request = opener.open.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, "http://127.0.0.1:3773/api/auth/pairing-token")
        self.assertEqual(request.get_header("Authorization"), "Bearer temporary-secret")
        self.assertEqual(payload["scopes"], list(t3code_admin_pair.ADMINISTRATIVE_SCOPES))
        self.assertEqual(result["id"], "pairing-1")
        self.assertTrue(
            any(
                isinstance(handler, t3code_admin_pair._NoRedirectHandler)
                for handler in build_opener.call_args.args
            )
        )

    def test_rejects_standard_session_and_revokes_it(self) -> None:
        issued = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {
                    "sessionId": "session-1",
                    "token": "temporary-secret",
                    "scopes": ["orchestration:read", "orchestration:operate"],
                }
            ),
        )
        with (
            patch.object(t3code_admin_pair, "_run_t3", return_value=issued),
            patch.object(
                t3code_admin_pair,
                "_revoke_session",
                return_value=True,
            ) as revoke_session,
        ):
            with self.assertRaisesRegex(RuntimeError, "required administrative"):
                t3code_admin_pair._issue_temporary_session("/opt/t3", "/tmp/state")

        revoke_session.assert_called_once_with("/opt/t3", "/tmp/state", "session-1")

    def test_negotiates_current_and_legacy_administrative_session_grants(self) -> None:
        current = (
            "orchestration:read", "orchestration:operate", "settings:write",
            "providers:manage", "environment:maintain", "preview:operate",
            "diagnostics:read", "terminal:read", "terminal:operate",
            "source-control:write", "filesystem:read", "filesystem:write",
            "relay:read", "access:read", "access:write", "relay:write",
        )
        legacy = (
            "orchestration:read", "orchestration:operate", "terminal:operate",
            "review:write", "relay:read", "access:read", "access:write", "relay:write",
        )
        for scopes in (current, legacy):
            with self.subTest(scopes=scopes):
                issued = MagicMock(stdout=json.dumps({
                    "sessionId": "session-1", "token": "temporary-secret",
                    "scopes": list(scopes),
                }))
                with (
                    patch.object(t3code_admin_pair, "_run_t3", return_value=issued),
                    patch.object(t3code_admin_pair, "_revoke_session") as revoke,
                ):
                    session, token, delegated = t3code_admin_pair._issue_temporary_session(
                        "/opt/t3", "/tmp/state"
                    )
                self.assertEqual((session, token), ("session-1", "temporary-secret"))
                self.assertEqual(set(delegated), set(scopes))
                revoke.assert_not_called()

    def test_rejects_incomplete_or_malformed_granular_grants_and_revokes(self) -> None:
        for scopes in (
            [scope for scope in t3code_admin_pair.ADMINISTRATIVE_SCOPES
             if scope != "environment:maintain"],
            "orchestration:read",
            ["access:write", {"invalid": "scope"}],
        ):
            with self.subTest(scopes=scopes):
                issued = MagicMock(stdout=json.dumps({
                    "sessionId": "session-1", "token": "temporary-secret", "scopes": scopes,
                }))
                with (
                    patch.object(t3code_admin_pair, "_run_t3", return_value=issued),
                    patch.object(t3code_admin_pair, "_revoke_session", return_value=True) as revoke,
                ):
                    with self.assertRaisesRegex(RuntimeError, "required administrative"):
                        t3code_admin_pair._issue_temporary_session("/opt/t3", "/tmp/state")
                revoke.assert_called_once_with("/opt/t3", "/tmp/state", "session-1")

    def test_discards_pairing_link_when_temporary_session_cleanup_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            t3_binary = os.path.join(temporary, "t3")
            base_dir = os.path.join(temporary, "state")
            os.mkdir(base_dir)
            with open(t3_binary, "w", encoding="utf-8") as file_obj:
                file_obj.write("#!/bin/sh\n")

            with (
                patch.object(
                    t3code_admin_pair,
                    "_issue_temporary_session",
                    return_value=(
                        "session-1", "temporary-secret", t3code_admin_pair.ADMINISTRATIVE_SCOPES
                    ),
                ),
                patch.object(
                    t3code_admin_pair,
                    "_request_pairing_link",
                    return_value={
                        "id": "pairing-1",
                        "credential": "one-time-secret",
                        "expiresAt": "2026-08-21T20:00:00Z",
                    },
                ),
                patch.object(
                    t3code_admin_pair,
                    "_revoke_session",
                    return_value=False,
                ),
                patch.object(t3code_admin_pair, "_revoke_pairing_link") as revoke_pairing,
            ):
                with self.assertRaisesRegex(RuntimeError, "temporary T3 administrative"):
                    t3code_admin_pair.issue_administrative_pairing(
                        t3_binary,
                        base_dir,
                        "http://127.0.0.1:3773",
                        "http://agent.example:3773",
                        "Remote app",
                    )

        revoke_pairing.assert_called_once_with(t3_binary, base_dir, "pairing-1")

    def test_discards_incomplete_pairing_link(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            t3_binary = os.path.join(temporary, "t3")
            base_dir = os.path.join(temporary, "state")
            os.mkdir(base_dir)
            with open(t3_binary, "w", encoding="utf-8") as file_obj:
                file_obj.write("#!/bin/sh\n")

            with (
                patch.object(
                    t3code_admin_pair,
                    "_issue_temporary_session",
                    return_value=(
                        "session-1", "temporary-secret", t3code_admin_pair.ADMINISTRATIVE_SCOPES
                    ),
                ),
                patch.object(
                    t3code_admin_pair,
                    "_request_pairing_link",
                    return_value={"id": "pairing-1"},
                ),
                patch.object(t3code_admin_pair, "_revoke_session", return_value=True),
                patch.object(t3code_admin_pair, "_revoke_pairing_link") as revoke_pairing,
            ):
                with self.assertRaisesRegex(RuntimeError, "incomplete"):
                    t3code_admin_pair.issue_administrative_pairing(
                        t3_binary,
                        base_dir,
                        "http://127.0.0.1:3773",
                        "https://agent.example",
                        "Remote app",
                    )

        revoke_pairing.assert_called_once_with(t3_binary, base_dir, "pairing-1")


if __name__ == "__main__":
    unittest.main()
