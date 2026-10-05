"""Publishing safety, durable identity and per-language human review."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lib.publishing import Publishing
from lib.publishing_artifacts import complete_record
from lib.publishing_auth import environment, login_view
from lib.publishing_store import PublishingStore
from lib.publishing_worker import receipt, upload_command, work


class PublishingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.repo = self.home / "repo"
        self.repo.mkdir()
        self.output = self.repo / "build"
        self.output.mkdir()
        (self.output / "game.bin").write_bytes(b"game")
        (self.repo / "basaltwater.json").write_text(json.dumps({"version": 1, "components": [], "publishing": {
            "languages": {"source": "en", "supported": ["en", "es", "fr"]}}}))
        self.publisher = Publishing(str(self.home))
        self.project = self.publisher.save_project("game", str(self.repo), "butler", "owner/game:linux")
        self.complete()

    def complete(self):
        return complete_record(str(self.repo), "build", "internal-1", self.project["record"])

    def approve(self, draft):
        self.publisher.review_from_panel(draft["id"], draft["hash"], "operator", approve=True)

    def test_snapshot_isolated_and_source_changes_rejected(self):
        artifact = self.publisher.prepare("game")
        (self.output / "game.bin").write_bytes(b"changed")
        self.assertEqual((Path(artifact["snapshot"]) / "game.bin").read_bytes(), b"game")
        with self.assertRaisesRegex(ValueError, "digest"):
            self.publisher.prepare("game")
        run = self.publisher.upload(artifact["id"])
        self.assertEqual(run["state"], "queued")

    def test_no_links_or_secret_artifacts(self):
        for name, create in (("link", lambda path: path.symlink_to("game.bin")),
                             ("hard", lambda path: os.link(self.output / "game.bin", path)),
                             (".env", lambda path: path.write_text("secret"))):
            path = self.output / name
            create(path)
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.complete()
            path.unlink()

    def test_completed_record_cannot_follow_ancestor_links(self):
        (self.repo / "linked").symlink_to(self.output, target_is_directory=True)
        with self.assertRaises(ValueError):
            complete_record(str(self.repo), "linked", "1", "record.json")

    def test_dispatch_deduplicates_across_preparations_and_restart(self):
        first = self.publisher.prepare("game")
        second = self.publisher.prepare("game")
        run = self.publisher.upload(first["id"])
        self.assertEqual(Publishing(str(self.home)).upload(second["id"])["id"], run["id"])
        with self.publisher.store.transaction() as db:
            run["state"] = "running"
            self.publisher.store.put(db, "runs", run)
        with patch("lib.publishing_worker.os.geteuid", return_value=1000):
            work(self.publisher)
        self.assertEqual(self.publisher.upload(first["id"])["state"], "unknown")

    def test_each_translation_needs_own_review_and_replacement_invalidates(self):
        en = self.publisher.draft("game", "en", "Update", "Hello, world!")
        es = self.publisher.draft("game", "es", "Actualización", "¡Hola, mundo!", source=en["id"])
        self.approve(en)
        with self.assertRaisesRegex(ValueError, "Human review"):
            self.publisher.export(es["id"])
        self.approve(es)
        self.assertEqual(self.publisher.export(es["id"])["body"], "¡Hola, mundo!")
        self.publisher.draft("game", "en", "Updated", "Changed", replaces=en["id"])
        for record in (en, es):
            with self.subTest(language=record["language"]), self.assertRaises(ValueError):
                self.publisher.export(record["id"])

    def test_stale_review_and_language_changes(self):
        draft = self.publisher.draft("game", "en", "News", "Some news")
        with self.assertRaises(ValueError):
            self.publisher.review_from_panel(draft["id"], "wrong", "operator", approve=True)
        self.approve(draft)
        (self.repo / "basaltwater.json").write_text('{"version":1,"components":[],"publishing":{}}')
        with self.assertRaises(ValueError):
            self.publisher.export(draft["id"])

    def test_post_handoff_and_expiry_not_automatic_success(self):
        draft = self.publisher.draft("game", "en", "News", "Hello", publish_at=100, late_minutes=1)
        self.approve(draft)
        with patch("lib.publishing.now", return_value=120):
            payload = self.publisher.export(draft["id"], dispatch=True)
        self.assertFalse(payload["published"])
        self.assertEqual(payload["delivery"], "human-editor-handoff")
        with self.assertRaises(ValueError):
            self.publisher.confirm_post_from_panel(draft["id"], "https://other.itch.io/game/devlog/1", "operator")
        result = self.publisher.confirm_post_from_panel(draft["id"], "https://owner.itch.io/game/devlog/1", "operator")
        self.assertEqual(result["state"], "operator-confirmed")
        late = self.publisher.draft("game", "en", "Late", "Late post", publish_at=100, late_minutes=1)
        self.approve(late)
        with patch("lib.publishing.now", return_value=200):
            self.publisher.tick()
        stored = next(row for row in self.publisher.status()["drafts"] if row["id"] == late["id"])
        self.assertEqual(stored["state"], "window-expired")

    def test_bundled_public_text_requires_exact_review(self):
        text = "Reviewed patch notes\n"
        (self.output / "patch-notes.txt").write_text(text)
        self.complete()
        artifact = self.publisher.prepare("game")
        with self.assertRaisesRegex(ValueError, "human review"):
            self.publisher.upload(artifact["id"])
        draft = self.publisher.draft("game", "en", "Patch notes", text)
        self.approve(draft)
        self.publisher.authorize_artifact_text(artifact["id"], "patch-notes.txt", draft["id"])
        self.assertEqual(self.publisher.upload(artifact["id"])["state"], "queued")
        self.publisher.review_from_panel(draft["id"], draft["hash"], "operator", approve=False)
        with self.assertRaises(ValueError):
            self.publisher.upload(artifact["id"])

    def test_unknown_reconciliation_controls_retry(self):
        artifact = self.publisher.prepare("game")
        run = self.publisher.upload(artifact["id"])
        with self.publisher.store.transaction() as db:
            run["state"] = "unknown"
            self.publisher.store.put(db, "runs", run)
        self.assertEqual(self.publisher.upload(artifact["id"])["id"], run["id"])
        self.publisher.reconcile_from_panel(run["id"], "not-submitted", "Checked itch dashboard", "operator")
        self.assertNotEqual(self.publisher.upload(artifact["id"])["id"], run["id"])

    def test_generated_vdf_never_contains_setlive_or_password(self):
        self.publisher.save_project("steam", str(self.repo), "steamcmd", "123", depot="124", username="publisher")
        artifact = self.publisher.prepare("steam")
        run = self.publisher.upload(artifact["id"])
        with patch("lib.publishing_worker.executable", return_value="/fake/steamcmd"):
            command = upload_command(self.publisher, run, artifact)
        vdf = Path(command[-2]).read_text()
        self.assertNotIn("SetLive", vdf)
        self.assertNotIn("password", " ".join(command))
        self.assertNotIn("+set_app_config", command)

    def test_saved_state_modes_and_no_network_get(self):
        with patch("subprocess.run", side_effect=AssertionError("network/process GET")):
            self.publisher.status()
        self.assertEqual(self.publisher.store.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.publisher.store.root / "state.sqlite3").stat().st_mode & 0o777, 0o600)

    def test_auth_views_never_show_raw_output(self):
        value = login_view("SECRET password:\n", "steamcmd")
        self.assertNotIn("SECRET", json.dumps(value))
        self.assertEqual(value["challenge"], "password")
        self.assertNotIn("BUTLER_API_KEY", environment(self.home))
        self.assertEqual(receipt('{"type":"result","value":{"buildId":42}}', "butler"), "42")
        self.assertEqual(receipt("Successfully finished AppID 123 build (BuildID 456)", "steamcmd"), "456")


if __name__ == "__main__":
    unittest.main()
