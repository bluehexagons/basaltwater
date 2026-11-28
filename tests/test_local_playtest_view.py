"""Evidence resolution is independent of host presentation."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from desktop.local_view import local_view


class LocalViewTest(unittest.TestCase):
    def test_refusal_and_repeated_views_keep_named_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            png = root / "preview.png"
            png.write_bytes(base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aO1sAAAAASUVORK5CYII="))
            digest = hashlib.sha256(png.read_bytes()).hexdigest()
            preview = {"geometry": [1, 1], "output": str(png),
                       "source_sha256": digest, "artifact_sha256": digest}
            receipt = {"ok": True, "observation_id": "a" * 32, "preview": preview, "artifact_sha256": digest}
            capture = root / "capture.json"
            capture.write_text(json.dumps(receipt))
            artifacts = [{"kind": "capture_receipt", "path": str(capture)},
                         {"kind": "screenshot_preview", "path": str(png), "sha256": digest}]
            result = root / "result.json"
            result.write_text(json.dumps({"ok": True, "observation": receipt, "artifacts": artifacts}))
            for status in ("unattempted", "seen", "refused"):
                report = local_view(str(result), status)
                self.assertEqual(report["presentation_reported"], status)
                self.assertEqual(report["artifacts"], artifacts)
                self.assertFalse(report["input_authority"])
            png.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "hash/size"):
                local_view(str(result))
