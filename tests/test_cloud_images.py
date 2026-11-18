"""Tests for lib/cloud_images.py: catalog resolution and --image parsing."""

from __future__ import annotations

import ast
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.cloud_images import (
    CLOUD_IMAGES,
    debian_image_location,
    is_local_image_ref,
    parse_image_argument,
    resolve_cloud_image,
)
from scripts import update_cloud_images


class TestResolveCloudImage(unittest.TestCase):
    def test_default_debian(self):
        entry = resolve_cloud_image("debian")
        self.assertEqual(entry["codename"], "trixie")
        self.assertEqual(entry["version"], "13")
        self.assertEqual(entry, CLOUD_IMAGES["debian-13"])
        self.assertTrue(entry["url"].startswith("https://cloud.debian.org/"))
        self.assertRegex(entry["snapshot"], r"^\d{8}-\d{3,4}$")
        self.assertRegex(entry["sha512"], r"^[0-9a-f]{128}$")

    def test_case_insensitive(self):
        self.assertEqual(
            resolve_cloud_image("Debian-12")["codename"],
            CLOUD_IMAGES["debian-12"]["codename"],
        )
        self.assertEqual(resolve_cloud_image("Debian-13"), resolve_cloud_image("debian"))

    def test_unknown_raises(self):
        with self.assertRaises(ValueError):
            resolve_cloud_image("ubuntu")

    def test_empty_falls_back_to_default(self):
        self.assertEqual(resolve_cloud_image("")["codename"], "trixie")

    def test_every_alias_is_validated_before_catalog_resolution(self):
        for alias in CLOUD_IMAGES:
            for field, value in (("sha512", ""), ("snapshot", "latest"),
                                 ("url", "https://example.test/wrong.qcow2"),
                                 ("filename", "different.qcow2")):
                with self.subTest(alias=alias, field=field):
                    with patch.dict(CLOUD_IMAGES[alias], {field: value}):
                        with self.assertRaisesRegex(ValueError, alias):
                            resolve_cloud_image("debian")


class TestCatalogRefresh(unittest.TestCase):
    snapshot = "20261005-3000"
    entries = {
        alias: dict(codename=codename, version=version, snapshot="latest", sha512="")
        for alias, codename, version in (
            ("debian", "trixie", "13"), ("debian-13", "trixie", "13"),
            ("debian-12", "bookworm", "12"),
        )
    }

    def setUp(self):
        self.path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "cloud_images.py"
        self.original = "# first\n# BEGIN CLOUD_IMAGES\nold catalog\n# END CLOUD_IMAGES\n# last\n"
        self.path.write_text(self.original)
        self.enterContext(patch.object(update_cloud_images, "CATALOG_FILE", self.path))
        self.enterContext(patch.object(update_cloud_images, "REPO_ROOT", self.path.parent))
        self.enterContext(patch.object(update_cloud_images, "CLOUD_IMAGES", self.entries))
        self.enterContext(patch.object(update_cloud_images, "scan_pinned_urls", return_value=[]))

    def response(self, url):
        if url.endswith("/"):
            return f'<a href="latest/">alias</a><a href="{self.snapshot}/">snapshot</a>'.encode()
        version = "13" if "/trixie/" in url else "12"
        codename = "trixie" if version == "13" else "bookworm"
        filename, _ = debian_image_location(codename, version, self.snapshot)
        return f"{'A' * 128} *./{filename}\n".encode()

    def test_default_refresh_pins_every_alias_and_preview_keeps_original(self):
        with patch.object(update_cloud_images, "_http_get", side_effect=self.response) as fetch:
            self.assertEqual(update_cloud_images.main(["--dry-run"]), 0)
            self.assertEqual(self.path.read_text(), self.original)
            fetch.reset_mock()
            self.assertEqual(update_cloud_images.main([]), 0)
        self.assertEqual(fetch.call_count, 4)
        catalog = self.path.read_text()
        self.assertTrue(catalog.startswith("# first\n"))
        self.assertTrue(catalog.endswith("# last\n"))
        entries = ast.literal_eval(ast.parse(catalog).body[0].value)
        self.assertEqual(entries["debian"], entries["debian-13"])
        for alias, entry in entries.items():
            self.assertEqual(entry["sha512"], "a" * 128, alias)
            self.assertEqual(entry["snapshot"], self.snapshot, alias)
            self.assertIn(f"/{self.snapshot}/", entry["url"], alias)

    def test_missing_malformed_or_duplicate_checksum_prevents_any_rewrite(self):
        for invalid in (b"", b"short  ", b"duplicate"):
            with self.subTest(invalid=invalid):
                def response(url):
                    body = self.response(url)
                    if url.endswith("/bookworm/" + self.snapshot + "/SHA512SUMS"):
                        if invalid == b"duplicate":
                            return body * 2
                        return invalid + body.split(maxsplit=1)[1] if invalid else b""
                    return body

                with patch.object(update_cloud_images, "_http_get", side_effect=response):
                    with self.assertRaisesRegex(ValueError, "valid SHA-512"):
                        update_cloud_images.update_catalog(dry_run=False)
                self.assertEqual(self.path.read_text(), self.original)

    def test_refuses_changed_checksum_for_same_snapshot(self):
        previous = {key: dict(value) for key, value in self.entries.items()}
        previous["debian"].update(snapshot=self.snapshot, sha512="b" * 128)
        with patch.object(update_cloud_images, "CLOUD_IMAGES", previous), \
             patch.object(update_cloud_images, "_http_get", side_effect=self.response):
            with self.assertRaisesRegex(ValueError, "SHA-512 changed"):
                update_cloud_images.update_catalog(dry_run=False)
        self.assertEqual(self.path.read_text(), self.original)


class TestParseImageArgument(unittest.TestCase):
    def test_url(self):
        self.assertEqual(
            parse_image_argument("https://example.com/foo.qcow2"),
            ("https://example.com/foo.qcow2", None),
        )

    def test_storage_ref(self):
        self.assertEqual(
            parse_image_argument("local:iso/foo.img"),
            (None, "local:iso/foo.img"),
        )
        self.assertEqual(
            parse_image_argument("local:import/foo.qcow2"),
            (None, "local:import/foo.qcow2"),
        )

    def test_qcow2_iso_ref_is_rejected(self):
        with self.assertRaisesRegex(ValueError, r"import.*content type"):
            parse_image_argument("local:iso/foo.qcow2")

    def test_empty(self):
        self.assertEqual(parse_image_argument(""), (None, None))
        self.assertEqual(parse_image_argument(None), (None, None))

    def test_invalid_raises(self):
        with self.assertRaises(ValueError):
            parse_image_argument("not-a-url-or-ref.qcow2")
        with self.assertRaises(ValueError):
            parse_image_argument("http://example.com/foo.qcow2")
        with self.assertRaises(ValueError):
            parse_image_argument("local:")


class TestIsLocalImageRef(unittest.TestCase):
    def test_valid(self):
        self.assertTrue(is_local_image_ref("local:iso/foo.img"))
        self.assertTrue(is_local_image_ref("nfs-store:iso/x/y.img"))
        self.assertTrue(is_local_image_ref("local:import/foo.qcow2"))

    def test_invalid(self):
        self.assertFalse(is_local_image_ref(""))
        self.assertFalse(is_local_image_ref("local:"))
        self.assertFalse(is_local_image_ref("foo.qcow2"))
        self.assertFalse(is_local_image_ref(":iso/foo.qcow2"))


if __name__ == "__main__":
    unittest.main()
