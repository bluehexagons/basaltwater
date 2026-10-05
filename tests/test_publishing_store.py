"""Database failures preserve recovery state and expose no raw error text."""

from __future__ import annotations

import sqlite3
import unittest
from unittest.mock import patch

from tests import test_publishing as fixtures


class PublishingStoreTests(unittest.TestCase):
    complete = fixtures.PublishingTests.complete

    def setUp(self):
        fixtures.PublishingTests.setUp(self)

    def test_connect_errors_use_sanitized_recovery_surface(self):
        with patch("lib.publishing_store.sqlite3.connect", side_effect=sqlite3.OperationalError("SECRET driver error")):
            with self.assertRaises(RuntimeError) as error:
                self.publisher.status()
        self.assertNotIn("SECRET", str(error.exception))
        self.assertIn("database is unavailable", str(error.exception))
        self.assertEqual(self.publisher.status()["projects"][0]["id"], "game")

    def test_write_failure_rolls_back_before_safe_error(self):
        with self.assertRaises(RuntimeError) as error:
            with self.publisher.store.transaction() as db:
                self.publisher.store.put(db, "projects", {**self.project, "id": "not-committed"})
                raise sqlite3.DatabaseError("SECRET database value")
        self.assertNotIn("SECRET", str(error.exception))
        self.assertEqual([row["id"] for row in self.publisher.status()["projects"]], ["game"])

    def test_corrupt_database_is_preserved_instead_of_reinitialized(self):
        path = self.publisher.store.root / "state.sqlite3"
        original = b"SECRET corrupted publishing state"
        path.write_bytes(original)
        with self.assertRaises(RuntimeError) as error:
            self.publisher.status()
        self.assertNotIn("SECRET", str(error.exception))
        self.assertEqual(path.read_bytes(), original)
