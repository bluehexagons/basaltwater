"""Synthetic PNG/recognizer and frozen held-out qualification boundaries."""

from __future__ import annotations

import argparse
import copy
import hashlib
from io import StringIO
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

from desktop import perception, perception_reports, png_regions, record_bundles
from lib.desktop_cli import add_desktop_subparser, run_desktop_command

EXAMPLES = Path(__file__).resolve().parents[1] / "docs/examples/learning-records"


class PerceptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        self.red = png_regions.encode(2, 2, b"\xff\0\0" * 4)
        self.blue = png_regions.encode(2, 2, b"\0\0\xff" * 4)
        self.packet = json.loads((EXAMPLES / "accepted.json").read_text())["records"][0]
        self.packet["conditions"].update(game="synthetic", build="build-1", profile="profile-1", recognizer="region-mae-v1")
        self.packet["frames"] = [self.frame("frame-1", self.red)]
        self.profile = {"schema_version": 1, "kind": "region-profile", "id": "synthetic-scene",
                        "version": "1", "detector": "region-mae-v1",
                        "conditions": {key: self.packet["conditions"][key] for key in ("game", "build", "profile", "recognizer")},
                        "geometry": [2, 2], "image_origin": [0, 0], "image_geometry": [2, 2],
                        "fields": [{"name": "scene", "rectangle": [0, 0, 2, 2], "max_error": .01,
                                    "min_margin": .1, "action_risk": "offline-review",
                                    "templates": [self.template("red", self.red), self.template("blue", self.blue)]}]}
        self.templates = {"red": self.red, "blue": self.blue}
        for target in ("desktop.session_runtime.status", "desktop.session_runtime.request",
                       "desktop.native_session.status", "desktop.native_session.request", "subprocess.Popen"):
            guard = patch(target, side_effect=AssertionError("Live boundary reached"))
            guard.start()
            self.addCleanup(guard.stop)

    def frame(self, identifier, data):
        return {"id": identifier, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
                "width": 2, "height": 2, "origin": [0, 0], "geometry": [2, 2]}

    def template(self, label, data):
        return {"id": label, "label": label, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}

    def recognize(self, data=None):
        data = self.red if data is None else data
        self.packet["frames"] = [self.frame("frame-1", data)]
        return perception.recognize(self.packet, "frame-1", data, self.profile, self.templates)

    def test_matches_unknown_ambiguous_and_literal_crops(self):
        for data, expected in ((self.red, "red"), (self.blue, "blue"),
                               (png_regions.encode(2, 2, b"\0\xff\0" * 4), None)):
            result, crops = self.recognize(data)
            self.assertEqual(result["fields"][0]["value"], expected)
            self.assertFalse(result["input_authority"])
            self.assertEqual(png_regions.decode(crops["scene"]), png_regions.decode(data))
        self.profile["fields"][0]["templates"][1] = self.template("duplicate", self.red)
        self.templates = {"red": self.red, "duplicate": self.red}
        result, _ = self.recognize()
        self.assertIsNone(result["fields"][0]["value"])
        self.assertEqual(result["fields"][0]["reason"], "ambiguous")

    def test_version_geometry_profile_drift_abstains(self):
        for key in ("game", "build", "profile", "recognizer"):
            previous = self.packet["conditions"][key]
            self.packet["conditions"][key] = "changed"
            result, crops = self.recognize()
            self.assertTrue(result["drift"])
            self.assertEqual(result["fields"][0]["sources"], [])
            self.assertEqual(crops, {})
            self.packet["conditions"][key] = previous
        self.profile["geometry"] = [3, 3]
        self.assertTrue(self.recognize()[0]["drift"])

    def test_field_delta_retains_unknowns_and_drift(self):
        prior = self.recognize()[0]
        current = self.recognize(self.blue)[0]
        delta = perception.field_delta(prior, current)
        self.assertEqual((delta["changes"][0]["before"], delta["changes"][0]["after"]), ("red", "blue"))
        self.assertEqual(perception.field_delta(prior, prior)["changes"], [])
        current["drift"] = True
        self.assertFalse(perception.field_delta(prior, current)["comparable"])

    def test_invalid_profiles_changed_inputs_and_bounds(self):
        for field, value in (("max_error", True), ("min_margin", 0), ("min_margin", float("nan")),
                             ("rectangle", [1, 1, 2, 2])):
            original = self.profile["fields"][0][field]
            self.profile["fields"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.recognize()
            self.profile["fields"][0][field] = original
        with self.assertRaisesRegex(ValueError, "hash"):
            perception.recognize(self.packet, "frame-1", self.blue, self.profile, self.templates)
        self.templates["red"] = self.blue
        with self.assertRaisesRegex(ValueError, "hash"):
            self.recognize()

    def bundle(self, name, images):
        packet = copy.deepcopy(self.packet)
        packet["frames"] = [self.frame(identifier, data) for identifier, data in images.items()]
        source = self.root / f"{name}-source.json"
        source.write_text(json.dumps({"schema_version": 1, "records": [packet]}))
        paths = {}
        for identifier, data in images.items():
            path = self.root / f"{name}-{identifier}.png"
            path.write_bytes(data)
            paths[identifier] = str(path)
        return record_bundles.create(str(source), paths, str(self.root / name))["artifact"]

    def assets(self):
        profile = self.root / "profile.json"
        profile.write_text(json.dumps(self.profile))
        paths = {}
        for key, content in self.templates.items():
            path = self.root / f"{key}.png"
            path.write_bytes(content)
            paths[key] = str(path)
        return str(profile), paths

    def test_heldout_metrics_false_confident_abstention_and_frozen_split(self):
        green = png_regions.encode(2, 2, b"\0\xff\0" * 4)
        artifact = self.bundle("heldout", {"red": self.red, "blue": self.blue, "unknown": green})
        profile, paths = self.assets()
        dataset = {"schema_version": 1, "kind": "region-heldout", "id": "dataset-1",
                   "profile_sha256": hashlib.sha256(Path(profile).read_bytes()).hexdigest(),
                   "calibration_runs": ["training-run"], "heldout_runs": ["run-1"],
                   "samples": [{"id": key, "run_id": "run-1", "packet_id": "packet-1", "frame_id": key,
                                "case": case, "expected": {"scene": expected}}
                               for key, case, expected in (("red", "normal", "red"), ("blue", "wrong-label", "red"),
                                                           ("unknown", "occlusion", None))]}
        source = self.root / "dataset.json"
        source.write_text(json.dumps(dataset))
        result = perception_reports.evaluate(str(source), {"run-1": artifact}, profile, paths, str(self.root / "report"))
        metric = result["metrics"]["scene"]
        self.assertEqual((metric["samples"], metric["false_confident"], metric["abstentions"]), (3, 1, 1))
        self.assertEqual(len(metric["latencies_seconds"]), 3)
        self.assertEqual(result["game_qualification"], "not-asserted")
        for key, value in (("calibration_runs", ["run-1"]), ("profile_sha256", "0" * 64),
                           ("samples", dataset["samples"] + [dataset["samples"][0]])):
            original = dataset[key]
            dataset[key] = value
            source.write_text(json.dumps(dataset))
            with self.assertRaises(ValueError):
                perception_reports.evaluate(str(source), {"run-1": artifact}, profile, paths)
            dataset[key] = original

    def test_cli_pinned_profile_regions_and_no_overwrite(self):
        artifact = self.bundle("input", {"frame-1": self.red})
        profile, paths = self.assets()
        parser = argparse.ArgumentParser()
        add_desktop_subparser(parser.add_subparsers(dest="command"))
        arguments = ["desktop", "evidence", "perception", "recognize", artifact, "packet-1", "frame-1", "--profile", profile,
                     "--output", str(self.root / "recognition"), "--json"]
        for key, path in paths.items():
            arguments += ["--template", key, path]
        for expected in (0, 1):
            output = StringIO()
            with patch("sys.stdout", output):
                self.assertEqual(run_desktop_command(parser.parse_args(arguments)), expected)
            result = json.loads(output.getvalue())
            self.assertFalse(result["input_authority"])
            if expected == 0:
                self.assertEqual(result["fields"][0]["value"], "red")
                self.assertEqual(Path(result["artifact"]).stat().st_mode & 0o777, 0o600)

    def test_partial_write_retains_inputs_and_no_completion_marker(self):
        artifact = self.bundle("input", {"frame-1": self.red})
        profile, paths = self.assets()
        before = Path(artifact).read_bytes()
        with patch.object(perception_reports, "write_json_atomic", side_effect=OSError("disk full")):
            with self.assertRaises(ValueError) as error:
                perception_reports.recognize(artifact, "packet-1", "frame-1", profile, paths, str(self.root / "partial"))
        self.assertTrue(error.exception.receipt["partial"])
        self.assertFalse((self.root / "partial/recognition.json").exists())
        self.assertEqual(Path(artifact).read_bytes(), before)


class PngTests(unittest.TestCase):
    def chunk(self, kind, content):
        return struct.pack(">I", len(content)) + kind + content + struct.pack(">I", zlib.crc32(kind + content))

    def png(self, raw, *, color=2, dimensions=(1, 2)):
        return b"\x89PNG\r\n\x1a\n" + self.chunk(b"IHDR", struct.pack(">IIBBBBB", *dimensions, 8, color, 0, 0, 0)) + self.chunk(b"IDAT", zlib.compress(raw)) + self.chunk(b"IEND", b"")

    def test_filters_and_rgba(self):
        # Two identical rows: left is zero at width one, above is previous RGB.
        for method, residual in ((0, b"\x10\x20\x30"), (1, b"\x10\x20\x30"),
                                  (2, b"\0\0\0"), (3, b"\x08\x10\x18"), (4, b"\0\0\0")):
            data = self.png(b"\0\x10\x20\x30" + bytes([method]) + residual)
            self.assertEqual(png_regions.decode(data)[2], b"\x10\x20\x30" * 2)
        image = self.png(b"\0\x10\x20\x30\xff", color=6, dimensions=(1, 1))
        self.assertEqual(png_regions.decode(image), (1, 1, b"\x10\x20\x30"))
        for method, residual in ((1, [20, 30, 40, 30, 30, 30]), (2, [10] * 6),
                                  (3, [15, 20, 25, 20, 20, 20]), (4, [10] * 6)):
            data = self.png(bytes([0, 10, 20, 30, 40, 50, 60, method, *residual]), dimensions=(2, 2))
            self.assertEqual(png_regions.decode(data)[2], bytes([10, 20, 30, 40, 50, 60, 20, 30, 40, 50, 60, 70]))

    def test_corruption_bombs_filters_and_unsupported_transparency(self):
        valid = self.png(b"\0\0\0\0", dimensions=(1, 1))
        for data in (valid[:-1], valid[:-5] + b"wrong", valid + b"tail",
                     self.png(b"\0" * 100, dimensions=(1, 1)),
                     self.png(b"\x05\0\0\0", dimensions=(1, 1)),
                     self.png(b"\0\0\0\0\0", color=6, dimensions=(1, 1)),
                     self.png(b"", dimensions=(16384, 16384))):
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                png_regions.decode(data)
        with patch.object(png_regions, "MAX_PIXELS", 1), self.assertRaises(ValueError):
            png_regions.decode(self.png(b"\0" * 8))


if __name__ == "__main__":
    unittest.main()
