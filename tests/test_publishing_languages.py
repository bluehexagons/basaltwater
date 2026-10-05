"""Compatibility and English/Spanish publishing language contracts."""

from __future__ import annotations

import unittest

from lib.project_manifest import parse_manifest
from lib.publishing_languages import parse_publishing


class LanguagesTests(unittest.TestCase):
    def test_defaults_and_metadata_only(self):
        manifest = parse_manifest({"version": 1, "components": [], "publishing": {}})
        self.assertEqual(manifest.publishing, parse_publishing({}))
        self.assertEqual(manifest.publishing["languages"], {"source": "en", "supported": ["en"]})

    def test_both_translation_directions_and_other_tags(self):
        for source in ("en", "es"):
            result = parse_publishing({"languages": {"source": source, "supported": ["EN", "es", "es-mx", "zh-hant"]}})
            self.assertEqual(result["languages"]["supported"], ["en", "es", "es-MX", "zh-Hant"])

    def test_invalid_languages(self):
        for value in ({"supported": []}, {"supported": ["en", "EN"]}, {"source": "es"},
                      {"supported": ["es_MX"]}, {"supported": [True]}, {"typo": "en"}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_publishing({"languages": value})
        with self.assertRaises(ValueError):
            parse_publishing({"credentials": "no"})
