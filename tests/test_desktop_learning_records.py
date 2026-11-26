"""Offline record lifecycle, adversarial inputs and CLI authority boundaries."""

from __future__ import annotations

import argparse
import copy
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

from desktop import decision_audit, learning_records as records, record_bundles as bundles
from lib.atomic_io import parse_json
from lib.desktop_cli import add_desktop_subparser, run_desktop_command

EXAMPLES = Path(__file__).resolve().parents[1] / "docs/examples/learning-records"


class LearningRecordTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        self.document = json.loads((EXAMPLES / "accepted.json").read_text())
        for target in ("desktop.session_runtime.status", "desktop.session_runtime.request",
                       "desktop.native_session.status", "desktop.native_session.request",
                       "subprocess.Popen"):
            guard = patch(target, side_effect=AssertionError("Live host/model boundary reached"))
            guard.start()
            self.addCleanup(guard.stop)

    def create(self, name="bundle", media=None):
        source = self.root / "input.json"
        source.write_text(json.dumps(self.document))
        return bundles.create(str(source), media or {}, str(self.root / name))

    def test_private_lifecycle_freshness_and_originals(self):
        result = self.create()
        artifact = result["artifact"]
        before = {p.name: p.read_bytes() for p in Path(artifact).parent.iterdir()}
        for state, instant, expected in (("state-1", "00:00:20", "valid"),
                                         ("state-2", "00:00:20", "stale"),
                                         ("unknown", "00:00:20", "unknown-state"),
                                         ("state-1", "00:01:01", "late")):
            report = bundles.validate(artifact, current_state_revision=state, at=f"2026-01-01T{instant}+00:00")
            self.assertEqual(report["advice"][0]["validation"], expected)
            self.assertEqual(report["advice"][0]["controller_disposition"], "not-evaluated")
            self.assertFalse(report["input_authority"])
        self.assertEqual(before, {p.name: p.read_bytes() for p in Path(artifact).parent.iterdir()})
        self.assertEqual(Path(artifact).stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            self.create()
        self.document = json.loads((EXAMPLES / "rejected.json").read_text())
        self.assertEqual(bundles.validate(self.create("rejected")["artifact"])["validation"], "valid")

    def test_unknown_state_and_disposition_guards(self):
        for row in self.document["records"]:
            if "state_revision" in row:
                row["state_revision"] = None
        disposition = self.document["records"][-1]
        disposition.update(validation="unknown-state", disposition="rejected", current_state_revision=None)
        records.validate(self.document)
        for validation, choice, instant in (("valid", "accepted", "00:00:20"),
                                            ("unknown-state", "accepted", "00:00:20"),
                                            ("unknown-state", "rejected", "00:01:01")):
            with self.subTest(validation=validation, choice=choice, instant=instant):
                disposition.update(validation=validation, disposition=choice, at=f"2026-01-01T{instant}+00:00")
                with self.assertRaises(ValueError):
                    records.validate(self.document)

    def test_invalid_contract_values_and_bounds(self):
        changes = [lambda d: d.update(schema_version=True), lambda d: d.update(schema_version=2),
                   lambda d: d["records"].append(copy.deepcopy(d["records"][0])),
                   lambda d: d["records"][0].update(monotonic_ns=True),
                   lambda d: d["records"][0].update(monotonic_ns=float("inf")),
                   lambda d: d["records"][1].update(opportunity_cost=float("nan")),
                   lambda d: d["records"][1].update(state_revision="other"),
                   lambda d: d["records"][2].update(intervention_budget=True),
                   lambda d: d["records"][3].update(packet_id="missing"),
                   lambda d: d["records"][3].update(question="unexpected"),
                   lambda d: d["records"][0]["fields"][0].update(value=0),
                   lambda d: d["records"][0]["fields"][0].update(status="observed"),
                   lambda d: d["records"][0].update(fields=[{}] * 129)]
        for change in changes:
            data = copy.deepcopy(self.document)
            change(data)
            with self.subTest(change=changes.index(change)), self.assertRaises(ValueError):
                records.validate(data)
        nested = None
        for _ in range(14):
            nested = [nested]
        self.document["records"][1]["alternative"] = nested
        with self.assertRaisesRegex(ValueError, "nesting"):
            records.validate(self.document)
        with patch.object(records, "MAX_BYTES", 64), self.assertRaisesRegex(ValueError, "MiB"):
            records.validate(json.loads((EXAMPLES / "accepted.json").read_text()))
        for value in ('{"id":1,"id":2}', '{"x":NaN}', '{"x":1e999}'):
            with self.assertRaises(ValueError):
                parse_json(value)

    def frame(self):
        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        image = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
        image += chunk(b"IDAT", zlib.compress(b"\0" + b"\0" * 6 + b"\0" + b"\0" * 6)) + chunk(b"IEND", b"")
        path = self.root / "frame.png"
        path.write_bytes(image)
        declaration = {"id": "frame-1", "sha256": hashlib.sha256(image).hexdigest(), "bytes": len(image),
                       "width": 2, "height": 2, "origin": [1, 1], "geometry": [4, 4]}
        packet = self.document["records"][0]
        packet["frames"] = [declaration]
        packet["fields"] = [{"name": "scene", "status": "observed", "value": "synthetic",
                             "sources": [{"frame_id": "frame-1", "rectangle": [0, 0, 2, 2]}]}]
        return path, declaration

    def test_frame_pinning_geometry_sources_and_tampering(self):
        path, frame = self.frame()
        artifact = self.create(media={"frame-1": str(path)})["artifact"]
        bundles.validate(artifact)
        path.write_bytes(b"changed original")
        bundles.validate(artifact)  # Snapshot is independent of mutable originals.
        saved = Path(artifact).parent / "frame-0.png"
        saved.write_bytes(b"changed snapshot")
        with self.assertRaises(ValueError):
            bundles.validate(artifact)
        for key, value in (("width", True), ("width", 4), ("origin", [4, 4]), ("geometry", [1, 1])):
            original = frame[key]
            frame[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                records.validate(self.document)
            frame[key] = original
        source = self.document["records"][0]["fields"][0]["sources"][0]
        for key, value in (("frame_id", "foreign"), ("rectangle", [1, 1, 2, 2])):
            original = source[key]
            source[key] = value
            with self.assertRaises(ValueError):
                records.validate(self.document)
            source[key] = original

    def test_failed_copy_partial_and_unsafe_inputs(self):
        path, frame = self.frame()
        frame["sha256"] = "a" * 64
        with self.assertRaises(ValueError) as error:
            self.create(media={"frame-1": str(path)})
        self.assertTrue(error.exception.receipt["partial"])
        self.assertFalse((self.root / "bundle/manifest.json").exists())
        with self.assertRaises(FileExistsError):
            self.create(media={"frame-1": str(path)})
        frame["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        link = self.root / "link.png"
        link.symlink_to(path)
        with self.assertRaises(ValueError):
            self.create("linked", {"frame-1": str(link)})
        with self.assertRaises(ValueError):
            self.create("missing", {"frame-1": str(self.root / "absent")})
        (self.root / ".git").mkdir()
        with self.assertRaisesRegex(ValueError, "Git"):
            self.create("git", {"frame-1": str(path)})

    def test_changed_source_during_read(self):
        source = self.root / "input.json"
        source.write_text("{}")
        real = os.fstat
        count = 0
        def changing(fd):
            nonlocal count
            info = real(fd)
            count += 1
            if count == 2:
                from types import SimpleNamespace
                return SimpleNamespace(st_mtime_ns=info.st_mtime_ns + 1, st_ctime_ns=info.st_ctime_ns)
            return info
        with patch.object(bundles.os, "fstat", side_effect=changing), self.assertRaisesRegex(ValueError, "changed"):
            bundles._read(source, 100)

    def test_pin_paths_permissions_duplicate_frames_and_limits(self):
        path, frame = self.frame()
        self.document["records"][0]["frames"].append(copy.deepcopy(frame))
        with self.assertRaisesRegex(ValueError, "Duplicate frame"):
            records.validate(self.document)
        self.document["records"][0]["frames"].pop()
        original = frame["bytes"]
        frame["bytes"] = 16 * 1024 * 1024 + 1
        with self.assertRaises(ValueError):
            records.validate(self.document)
        frame["bytes"] = original
        frame["width"] = 3
        with self.assertRaisesRegex(ValueError, "geometry"):
            self.create("geometry", {"frame-1": str(path)})
        frame["width"] = 2
        artifact = self.create("safe", {"frame-1": str(path)})["artifact"]
        manifest = Path(artifact)
        original_manifest = manifest.read_bytes()
        value = json.loads(original_manifest)
        value["source"]["artifact"] = "../input.json"
        manifest.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "artifact name"):
            bundles.validate(artifact)
        manifest.write_bytes(original_manifest)
        source = manifest.parent / "records.json"
        original_source = source.read_bytes()
        source.write_bytes(original_source + b" ")
        with self.assertRaisesRegex(ValueError, "hash/size"):
            bundles.validate(artifact)
        source.write_bytes(original_source)
        source.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "private"):
            bundles.validate(artifact)
        source.chmod(0o600)
        source.unlink()
        source.symlink_to(path)
        with self.assertRaisesRegex(ValueError, "symlinks"):
            bundles.validate(artifact)
        with patch.object(records, "MAX_RECORDS", 2), self.assertRaises(ValueError):
            records.validate(self.document)

    def test_cli_create_validate_and_errors_without_helper(self):
        source = self.root / "input.json"
        source.write_text(json.dumps(self.document))
        parser = argparse.ArgumentParser()
        add_desktop_subparser(parser.add_subparsers(dest="command"))
        def request(arguments, expected=0):
            output = StringIO()
            with patch("sys.stdout", output):
                self.assertEqual(run_desktop_command(parser.parse_args(["desktop", *arguments])), expected)
            return json.loads(output.getvalue())
        result = request(["evidence", "records", "create", str(source), "--output", str(self.root / "cli"), "--json"])
        self.assertFalse(result["input_authority"])
        request(["evidence", "records", "validate", result["artifact"], "--json"])
        result = request(["evidence", "records", "create", str(source), "--media", "same", "a", "--media", "same", "b"], 1)
        self.assertEqual(result["validation"], "invalid")
        self.assertFalse(result["partial"])

    def test_audit_links_exact_receipts_and_unchanged_frames(self):
        path, frame = self.frame()
        before_packet, decision = self.document["records"][:2]
        decision.update(dispatch={"id": "input-1", "stage": "completed", "delivery": "sent"},
                        observed_effect={"description": "private effect"})
        after_packet = copy.deepcopy(before_packet)
        after_packet.update(id="packet-2", phase="gameplay", monotonic_ns=200)
        after_packet["frames"][0]["id"] = "frame-2"
        after_packet["fields"][0]["sources"][0]["frame_id"] = "frame-2"
        self.document["records"].append(after_packet)
        artifact = self.create(media={"frame-1": str(path), "frame-2": str(path)})["artifact"]
        input_path, before_path = self.root / "submitted.json", self.root / "before.json"
        receipt = {"input_id": "input-1", "generation": "session-1", "observation_id": "capture-1",
                   "dispatch_stage": "input-completed", "input_delivery": "completed"}
        screenshot = {"geometry": [4, 4], "image_geometry": [2, 2], "image_origin": [1, 1],
                      "artifact_sha256": frame["sha256"]}
        before_path.write_text(json.dumps({"generation": "session-1", "observation_id": "capture-1",
                                           **screenshot}))
        receipt["observation"] = {"generation": "session-1", "observation_id": "capture-2",
                                  "last_input": {"input_id": "input-1", "input_delivery": "completed"},
                                  **screenshot, "changed": False}
        parser = argparse.ArgumentParser()
        add_desktop_subparser(parser.add_subparsers(dest="command"))
        args = parser.parse_args(["desktop", "evidence", "records", "audit", artifact, "decision-1",
                                  "--input", str(input_path), "--before", str(before_path),
                                  "--after", "packet-2", str(input_path), "--json"])

        def run(expected, saved=receipt, encoding="utf-8"):
            input_path.write_text(json.dumps(saved), encoding=encoding)
            output = StringIO()
            with patch("sys.stdout", output):
                self.assertEqual(run_desktop_command(args), expected)
            self.assertNotIn("private effect", output.getvalue())
            return json.loads(output.getvalue())

        report = run(0)
        self.assertEqual(report["status"], "matched")
        self.assertEqual(report["before"]["frame_ids"], ["frame-1"])
        self.assertEqual(report["after"]["frame_ids"], ["frame-2"])
        self.assertEqual(report["dispatch"]["receipt_sha256"], hashlib.sha256(input_path.read_bytes()).hexdigest())
        self.assertEqual(report["dispatch"]["receipt_sha256"], report["after"]["receipt_sha256"])
        self.assertIs(report["after"]["changed"], False)
        self.assertTrue(report["effect_declared"])
        self.assertEqual(report["effect_assessment"], "not-evaluated")
        self.assertFalse(report["input_authority"])
        for key, value, reason in (("last_input", {"input_id": "different"}, "last-input-id-differs"),
                                   ("generation", "new-session", "generation-differs"),
                                   ("observation_id", "capture-1", "reused-input-observation"),
                                   ("artifact_sha256", "a" * 64, "frame-digest-differs"),
                                   ("image_origin", [0, 0], "frame-geometry-differs"),
                                   ("image_geometry", [2, 1], "frame-geometry-differs"),
                                   ("geometry", [5, 4], "frame-geometry-differs")):
            with self.subTest(key=key), patch.dict(receipt["observation"], {key: value}):
                report = run(1)
                self.assertEqual(report["dispatch"]["status"], "matched")
                self.assertEqual(report["after"]["reason"], reason)
                self.assertIsNone(report["after"]["changed"])
        windows_before = {"ok": True, "error": None, "generation": "session-1",
                          "result": json.loads(before_path.read_text())}
        before_path.write_text(json.dumps(windows_before), encoding="utf-16")
        windows_after = {"ok": True, "generation": "session-1", "result": receipt}
        report = run(0, windows_after, encoding="utf-16")
        self.assertEqual(report["status"], "matched")
        self.assertEqual(report["after"]["frame_ids"], ["frame-2"])
        with patch.dict(receipt["observation"], {"ok": False}):
            report = run(0)
            self.assertEqual(report["dispatch"]["status"], "matched")
            self.assertEqual(report["after"]["status"], "unverified")
            self.assertEqual(report["after"]["frame_ids"], [])
        before_packet.update(frames=[], fields=[])
        unpinned = self.create("unpinned", {"frame-2": str(path)})["artifact"]
        with patch.object(args, "artifact", unpinned):
            report = run(0)
            self.assertEqual(report["before"]["status"], "unverified")
            self.assertEqual(report["before"]["reason"], "frame-not-pinned")
        (Path(artifact).parent / "frame-1.png").write_bytes(b"tampered")
        self.assertEqual(run(1)["validation"], "invalid")

    def test_audit_preserves_dispatch_reports_and_missing_evidence(self):
        input_path = self.root / "receipt.json"
        decision = self.document["records"][1]
        decision["dispatch"]["id"] = "input-1"

        def check(artifact, saved, encoding="utf-8"):
            input_path.write_text(json.dumps(saved), encoding=encoding)
            return decision_audit.audit(artifact, "decision-1", input_path)["dispatch"]

        for delivery, stage, claim in (
                ("not-sent", "before-input", ("refused", "not-sent")),
                ("partial", "between-characters", ("attempted", "unknown")),
                ("unknown", "transport", ("attempted", "unknown")),
                ("completed", "post-input-observation", ("completed", "sent"))):
            with self.subTest(delivery=delivery):
                decision["dispatch"].update(stage=claim[0], delivery=claim[1])
                artifact = self.create(delivery)["artifact"]
                saved = {"input_id": "input-1", "input_delivery": delivery, "dispatch_stage": stage,
                         "error": "private backend failure"}
                report = check(artifact, saved)
                self.assertEqual(report["status"], "matched")
                self.assertEqual(report["input_delivery"], delivery)
                self.assertEqual(report["dispatch_stage"], stage)
                self.assertEqual(check(artifact, {**saved, "input_id": "other"})["reason"], "input-id-differs")
                self.assertEqual(check(artifact, {**saved, "dispatch_stage": None})["status"], "unverified")
        self.assertEqual(check(artifact, {**saved, "input_delivery": "unknown"})["reason"], "dispatch-claim-differs")
        legacy = {"ok": True, "generation": "session-1", "result": {"submitted": True, "observation": {},
                  "input_delivery": "completed", "dispatch_stage": "input-completed"}}
        report = check(artifact, legacy, encoding="utf-16")
        self.assertEqual(report["reason"], "input-id-unavailable")
        self.assertEqual(report["input_delivery"], "completed")
        del legacy["result"]["input_delivery"]
        self.assertIsNone(check(artifact, legacy)["input_delivery"])

    def test_audit_rejects_unrelated_or_older_followup_packets(self):
        packet = self.document["records"][0]
        input_path = self.root / "receipt.json"
        input_path.write_text("{}")
        followup = copy.deepcopy(packet)
        followup.update(id="followup", match_id="different")
        self.document["records"].append(followup)
        artifact = self.create("wrong-match")["artifact"]
        with self.assertRaisesRegex(ValueError, "share the original run"):
            decision_audit.audit(artifact, "decision-1", input_path, after=("followup", input_path))
        followup.update(match_id=packet["match_id"], monotonic_ns=packet["monotonic_ns"] - 1)
        artifact = self.create("old")["artifact"]
        with self.assertRaisesRegex(ValueError, "predates"):
            decision_audit.audit(artifact, "decision-1", input_path, after=("followup", input_path))


if __name__ == "__main__":
    unittest.main()
