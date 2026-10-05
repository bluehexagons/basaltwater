"""Conservative Steam beta promotion and manual default release boundaries."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from lib import publishing_steam as steam
from lib.publishing_worker import work
from tests import test_publishing as fixtures


class SteamPromotionTests(unittest.TestCase):
    complete = fixtures.PublishingTests.complete
    approve = fixtures.PublishingTests.approve

    def setUp(self):
        fixtures.PublishingTests.setUp(self)
        self.publisher.save_project("steam", str(self.repo), "steamcmd", "123", depot="124", username="publisher")
        artifact = self.publisher.prepare("steam")
        self.upload = self.publisher.upload(artifact["id"])
        with self.publisher.store.transaction() as db:
            self.upload.update(state="uploaded", receipt="456")
            self.publisher.store.put(db, "runs", self.upload)
        self.live = "100"
        self.next_build = "456"
        self.calls = []
        self.native_request = steam.request
        self.request = patch.object(steam, "request", side_effect=self.api)
        self.request.start()
        self.addCleanup(self.request.stop)

    def api(self, publisher, method, fields, **kwargs):
        self.calls.append((method, fields))
        if method == "GetAppBetas":
            return {"betas": {"public": {"buildid": "10"}, "test": {"buildid": self.live}}}
        if method == "GetAppBuilds":
            return {"builds": [{"buildid": "456"}, {"buildid": self.next_build}]}
        self.assertEqual(fields, {"appid": "123", "buildid": self.next_build, "betakey": "test"})
        self.live = self.next_build
        return {"success": True}

    def get(self, kind, record):
        with self.publisher.store.transaction() as db:
            return self.publisher.store.get(db, kind, record["id"])

    def test_beta_requires_fresh_observation_and_verifies_readback(self):
        release = steam.prepare_beta(self.publisher, self.upload["id"], "test")
        run = steam.queue_beta(self.publisher, release["id"])
        with patch("lib.publishing_worker.os.geteuid", return_value=1000):
            work(self.publisher)
        self.assertEqual(self.get("runs", run)["state"], "promoted")
        self.assertEqual(self.get("releases", release)["state"], "verified")
        self.assertEqual(sum(method == "SetAppBuildLive" for method, _ in self.calls), 1)

    def test_repeated_promotion_submissions_return_the_saved_operation(self):
        release = steam.prepare_beta(self.publisher, self.upload["id"], "test")
        run = steam.queue_beta(self.publisher, release["id"])
        self.assertEqual(steam.queue_beta(self.publisher, release["id"])["id"], run["id"])
        with patch("lib.publishing_worker.os.geteuid", return_value=1000):
            work(self.publisher)
        with patch.object(steam, "now", return_value=release["observed_at"] + 301):
            self.assertEqual(steam.queue_beta(self.publisher, release["id"])["id"], run["id"])
        self.assertEqual(sum(method == "SetAppBuildLive" for method, _ in self.calls), 1)

    def test_review_revoked_during_observation_prevents_promotion(self):
        uploaded, draft = self.notes_upload()
        release = steam.prepare_beta(self.publisher, uploaded["id"], "test")
        run = steam.queue_beta(self.publisher, release["id"])

        def revoke_after_observation(publisher, method, fields, **kwargs):
            response = self.api(publisher, method, fields, **kwargs)
            if method == "GetAppBuilds":
                self.publisher.review_from_panel(draft["id"], draft["hash"], "operator", approve=False)
            return response

        with patch.object(steam, "request", side_effect=revoke_after_observation), \
                patch("lib.publishing_worker.os.geteuid", return_value=1000):
            work(self.publisher)
        self.assertEqual(self.get("runs", run)["state"], "preflight-failed")
        self.assertFalse(any(method == "SetAppBuildLive" for method, _ in self.calls))

    def test_destination_changed_during_observation_prevents_promotion(self):
        release = steam.prepare_beta(self.publisher, self.upload["id"], "test")
        run = steam.queue_beta(self.publisher, release["id"])

        def change_after_observation(publisher, method, fields, **kwargs):
            response = self.api(publisher, method, fields, **kwargs)
            if method == "GetAppBuilds":
                self.publisher.save_project("steam", str(self.repo), "steamcmd", "999", depot="998", username="publisher")
            return response

        with patch.object(steam, "request", side_effect=change_after_observation), \
                patch("lib.publishing_worker.os.geteuid", return_value=1000):
            work(self.publisher)
        self.assertEqual(self.get("runs", run)["state"], "preflight-failed")
        self.assertFalse(any(method == "SetAppBuildLive" for method, _ in self.calls))

    def test_preparation_expiring_during_observation_prevents_promotion(self):
        release = steam.prepare_beta(self.publisher, self.upload["id"], "test")
        run = steam.queue_beta(self.publisher, release["id"])
        current = [release["observed_at"]]

        def delay_observation(publisher, method, fields, **kwargs):
            response = self.api(publisher, method, fields, **kwargs)
            if method == "GetAppBuilds":
                current[0] += 301
            return response

        with patch.object(steam, "request", side_effect=delay_observation), \
                patch.object(steam, "now", side_effect=lambda: current[0]), \
                patch("lib.publishing_worker.os.geteuid", return_value=1000):
            work(self.publisher)
        self.assertEqual(self.get("runs", run)["state"], "preflight-failed")
        self.assertFalse(any(method == "SetAppBuildLive" for method, _ in self.calls))

    def test_changed_branch_never_dispatches_and_gate_stays_closed(self):
        release = steam.prepare_beta(self.publisher, self.upload["id"], "test")
        run = steam.queue_beta(self.publisher, release["id"])
        self.live = "101"
        with patch("lib.publishing_worker.os.geteuid", return_value=1000):
            work(self.publisher)
        self.assertEqual(self.get("runs", run)["state"], "preflight-failed")
        self.assertEqual(self.get("releases", release)["state"], "preflight-failed")
        self.assertFalse(any(method == "SetAppBuildLive" for method, _ in self.calls))

    def notes_upload(self, *, release=""):
        text = "Reviewed release notes\n"
        draft = self.publisher.draft("steam", "en", "Notes", text, release=release)
        self.approve(draft)
        (self.output / "patch-notes.txt").write_text(text)
        self.complete()
        uploaded = self.publisher.upload(self.publisher.prepare("steam")["id"])
        self.next_build = "789"
        with self.publisher.store.transaction() as db:
            uploaded.update(state="uploaded", receipt=self.next_build)
            self.publisher.store.put(db, "runs", uploaded)
        return uploaded, draft

    def test_private_upload_can_stage_gated_notes_but_beta_promotion_waits(self):
        gate = self.publisher.create_release(self.upload["id"])
        uploaded, _ = self.notes_upload(release=gate["id"])
        prepared = steam.prepare_beta(self.publisher, uploaded["id"], "test")
        run = steam.queue_beta(self.publisher, prepared["id"])
        with patch("lib.publishing_worker.os.geteuid", return_value=1000):
            work(self.publisher)
        self.assertEqual(self.get("runs", run)["state"], "preflight-failed")
        self.assertFalse(any(method == "SetAppBuildLive" for method, _ in self.calls))
        self.publisher.confirm_release_from_panel(gate["id"], "456", "operator")
        prepared = steam.prepare_beta(self.publisher, uploaded["id"], "test")
        run = steam.queue_beta(self.publisher, prepared["id"])
        with patch("lib.publishing_worker.os.geteuid", return_value=1000):
            work(self.publisher)
        self.assertEqual(self.get("runs", run)["state"], "promoted")

    def test_beta_promotion_rechecks_notes_review_after_private_upload(self):
        uploaded, draft = self.notes_upload()
        prepared = steam.prepare_beta(self.publisher, uploaded["id"], "test")
        run = steam.queue_beta(self.publisher, prepared["id"])
        self.publisher.review_from_panel(draft["id"], draft["hash"], "operator", approve=False)
        with patch("lib.publishing_worker.os.geteuid", return_value=1000):
            work(self.publisher)
        self.assertEqual(self.get("runs", run)["state"], "preflight-failed")
        self.assertFalse(any(method == "SetAppBuildLive" for method, _ in self.calls))

    def test_default_aliases_rejected_before_provider_requests(self):
        for branch in ("public", "default", "Public", "DEFAULT", "", " test"):
            with self.subTest(branch=branch), self.assertRaises(ValueError):
                steam.prepare_beta(self.publisher, self.upload["id"], branch)
        self.assertEqual(self.calls, [])
        for response in ({"betas": {"test": {"buildid": "100"}}},
                         {"betas": {"public": {"buildid": "10"}, "test": {"buildid": "100", "isdefault": 1}}}):
            with self.assertRaises(ValueError):
                steam.branches(response)

    def test_interruption_needs_exact_build_reconciliation(self):
        release = steam.prepare_beta(self.publisher, self.upload["id"], "test")
        run = steam.queue_beta(self.publisher, release["id"])
        with self.publisher.store.transaction() as db:
            run["state"] = "running"
            self.publisher.store.put(db, "runs", run)
        with patch("lib.publishing_worker.os.geteuid", return_value=1000):
            work(self.publisher)
        self.assertEqual(self.get("releases", release)["state"], "unknown")
        with self.assertRaises(ValueError):
            self.publisher.reconcile_from_panel(run["id"], "uploaded", "999", "operator")
        self.publisher.confirm_release_from_panel(release["id"], "456", "operator")
        self.assertEqual(self.get("runs", run)["state"], "operator-confirmed")
        self.assertFalse(any(method == "SetAppBuildLive" for method, _ in self.calls))

    def test_api_key_private_not_in_status_and_logout_removes_it(self):
        from lib.publishing_auth import PublishingAuth

        steam.set_api_key_from_panel(self.publisher, "a" * 32)
        key = self.publisher.store.root / "steam-api.key"
        self.assertEqual(key.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("a" * 32, str(self.publisher.status()))
        PublishingAuth(self.publisher).logout("steamcmd")
        self.assertFalse(key.exists())

    def test_default_release_gate_requires_manual_confirmation(self):
        release = self.publisher.create_release(self.upload["id"])
        draft = self.publisher.draft("steam", "en", "Now available", "The update is live.", release=release["id"])
        self.approve(draft)
        with self.assertRaises(ValueError):
            self.publisher.export(draft["id"], dispatch=True)
        self.publisher.confirm_release_from_panel(release["id"], "456", "operator")
        self.assertFalse(self.publisher.export(draft["id"], dispatch=True)["published"])
        self.assertEqual(self.calls, [])

    def test_destination_changes_cannot_reuse_uploads_or_release_gates(self):
        release = self.publisher.create_release(self.upload["id"])
        self.publisher.save_project("steam", str(self.repo), "steamcmd", "999", depot="998", username="publisher")
        with self.assertRaises(ValueError):
            self.publisher.create_release(self.upload["id"])
        with self.assertRaises(ValueError):
            steam.prepare_beta(self.publisher, self.upload["id"], "test")
        with self.assertRaises(ValueError):
            self.publisher.confirm_release_from_panel(release["id"], "456", "operator")
        with self.assertRaises(ValueError):
            self.publisher.draft("steam", "en", "News", "New release", release=release["id"])
        self.assertEqual(self.calls, [])

    def test_steam_account_tokens_do_not_use_unix_username_rules(self):
        from lib.validators import validate_steam_account_name

        for account in ("MixedCase_123", "123publisher", "publisher" * 5):
            self.assertTrue(validate_steam_account_name(account))
            project = self.publisher.save_project("steam", str(self.repo), "steamcmd", "123", depot="124", username=account)
            self.assertEqual(project["username"], account)
        for account in ("-option", "+quit", "user password", "name\n", "x" * 65, None):
            self.assertFalse(validate_steam_account_name(account))

    def test_native_api_rejects_default_and_request_field_injection(self):
        for fields, mutate in (({"appid": "123", "buildid": "456", "betakey": "public"}, True),
                               ({"appid": "123", "buildid": "456", "betakey": "test"}, False),
                               ({"appid": "123", "buildid": "456", "betakey": "test", "steamid": "1"}, True)):
            with self.assertRaises(ValueError), patch.object(steam.http.client, "HTTPSConnection") as connection:
                self.native_request(self.publisher, "SetAppBuildLive", fields, mutate=mutate)
            connection.assert_not_called()

    def test_native_beta_api_posts_only_validated_fields_and_bounded_response(self):
        steam.set_api_key_from_panel(self.publisher, "a" * 32)
        response = MagicMock(status=200)
        response.read.return_value = b'{"response":{"success":true}}'
        with patch.object(steam.http.client, "HTTPSConnection") as connection:
            connection.return_value.getresponse.return_value = response
            result = self.native_request(self.publisher, "SetAppBuildLive", {"appid": "123", "buildid": "456", "betakey": "test"}, mutate=True)
        self.assertEqual(result, {"success": True})
        method, endpoint = connection.return_value.request.call_args.args
        self.assertEqual((method, endpoint), ("POST", "/ISteamApps/SetAppBuildLive/v2/"))
        self.assertNotIn("steamid", connection.return_value.request.call_args.kwargs["body"])
        response.read.assert_called_once_with(steam.MAX_RESPONSE + 1)
        connection.return_value.close.assert_called_once()
