"""Batch galleries bind inspection coverage to validated images and observations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from lib import agent_cli
from lib.agent_visual_batch import review_batch
from tests.test_agent_visuals import png_bytes


class BatchReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.root / "a.png").write_bytes(png_bytes(4, 4))
        (self.root / "b.png").write_bytes(png_bytes(4, 4, (255, 0, 0, 255)))
        self.path = self.root / "batch.json"
        self.manifest = {"version": 1, "compare_keys": ["camera", "quality"], "pairs": [{
            "id": "rear-high", "category": "silhouette",
            "before": {"image": "a.png", "settings": {"camera": "rear", "quality": 1}},
            "after": {"image": "b.png", "settings": {"camera": "rear", "quality": 1}},
            "crops": [{"name": "attachment", "rect": [1, 1, 2, 2]}],
        }]}
        parser = argparse.ArgumentParser()
        agent_cli.add_agent_subparser(parser.add_subparsers())
        self.args = parser.parse_args(["agent", "visuals", "batch", str(self.path), "--output", str(self.root / "review")])

    def review(self):
        self.path.write_text(json.dumps(self.manifest))
        return review_batch(self.args)

    def test_gallery_records_hashes_observations_crops_and_missing_coverage(self) -> None:
        result = self.review()
        self.assertEqual(result["missing_categories"], ["detail", "junction", "underside"])
        pair = result["pairs"][0]
        self.assertEqual(pair["crops"], self.manifest["pairs"][0]["crops"])
        self.assertEqual(pair["before"]["camera"], "rear")
        self.assertNotEqual(pair["before"]["sha256"], pair["after"]["sha256"])
        html = Path(result["viewer"]).read_text()
        self.assertIn("data:image/png;base64,", html)
        self.assertIn(result["batch_id"], html)
        self.assertEqual(Path(result["viewer"]).stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads((self.root / "review/batch.json").read_text()), result)

    def test_incompatible_observations_require_explicit_override(self) -> None:
        after = self.manifest["pairs"][0]["after"]["settings"]
        for value in (True, 1.0, None, "low"):
            with self.subTest(value=value):
                after["quality"] = value
                with self.assertRaisesRegex(ValueError, "observed setting: quality"):
                    self.review()
                self.assertFalse((self.root / "review").exists())
        del after["quality"]
        self.args.allow_incompatible = True
        result = self.review()
        self.assertEqual(result["pairs"][0]["issues"], ["Missing observed setting: quality"])
        self.assertEqual(result["visual_correctness"], "unverified")

    def test_invalid_manifest_and_crops_do_not_create_output(self) -> None:
        original = json.dumps(self.manifest)
        variants = [
            lambda m: m.update(version=True),
            lambda m: m.update(compare_keys=[]),
            lambda m: m.update(compare_keys=["camera", "camera"]),
            lambda m: m["pairs"].append(m["pairs"][0]),
            lambda m: m["pairs"][0].update(crops=[{"name": "bad", "rect": [0, 0, 5, 1]}]),
            lambda m: m["pairs"][0].update(crops=[{"name": "bad", "rect": [True, 0, 1, 1]}]),
            lambda m: m["pairs"][0]["before"].update(settings=None),
            lambda m: m["pairs"][0]["before"].update(image="bad\npath"),
        ]
        for mutate in variants:
            self.manifest = json.loads(original)
            mutate(self.manifest)
            with self.subTest(manifest=self.manifest), self.assertRaises(ValueError):
                self.review()
            self.assertFalse((self.root / "review").exists())

    def test_batch_identity_changes_with_capture_metadata_and_crop(self) -> None:
        original = self.review()["batch_id"]
        self.args.output = str(self.root / "changed")
        self.manifest["pairs"][0]["crops"][0]["rect"] = [0, 0, 1, 1]
        self.assertNotEqual(self.review()["batch_id"], original)

    def test_dimension_mismatch_and_unsafe_metadata(self) -> None:
        (self.root / "b.png").write_bytes(png_bytes(5, 4))
        with self.assertRaisesRegex(ValueError, "Different image dimensions"):
            self.review()
        self.args.allow_incompatible = True
        self.manifest["title"] = '</script><img src=x onerror="alert(1)">'
        result = self.review()
        self.assertNotIn(self.manifest["title"], Path(result["viewer"]).read_text())

    @unittest.skipUnless(shutil.which("node"), "Node is required for viewer behavior checks")
    def test_ui_switches_pairs_crops_and_exports_only_manual_inspections(self) -> None:
        pair = json.loads(json.dumps(self.manifest["pairs"][0]))
        pair["id"] = "incompatible"
        pair["after"]["settings"]["quality"] = 2
        self.manifest["pairs"].append(pair)
        self.args.allow_incompatible = True
        result = self.review()
        completed = subprocess.run(
            [shutil.which("node"), str(Path(__file__).with_name("visual_batch_harness.cjs"))],
            input=Path(result["viewer"]).read_text(), capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
