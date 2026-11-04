"""Corpus review semantics and offline pinned CLI acceptance."""

from __future__ import annotations

import argparse
import copy
import hashlib
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from desktop import corpus, learning_records as contract
from lib.desktop_cli import add_desktop_subparser, run_desktop_command

EXAMPLES = Path(__file__).resolve().parents[1] / "docs/examples/learning-records"


class CorpusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        self.content = (EXAMPLES / "accepted.json").read_bytes()
        self.packet = json.loads(self.content)["records"][0]
        self.manifest = {"schema_version": 1, "kind": "learning-corpus", "id": "corpus-1",
                         "cutoff_at": "2026-01-01T01:00:00Z", "sources": [{"id": "source-1",
                         "sha256": hashlib.sha256(self.content).hexdigest(), "bytes": len(self.content),
                         "availability": "available"}], "segments": [self.segment()]}
        for target in ("desktop.session_runtime.status", "desktop.session_runtime.request",
                       "desktop.native_session.status", "desktop.native_session.request", "subprocess.Popen"):
            guard = patch(target, side_effect=AssertionError("Live boundary reached"))
            guard.start()
            self.addCleanup(guard.stop)

    def segment(self, **values):
        return {"id": "segment-1", "source_id": "source-1", "packet_id": "packet-1",
                "match_id": "match-1", "run_id": "run-1", "condition_id": "condition-1",
                "conditions": copy.deepcopy(self.packet["conditions"]), "included": True,
                "reason": "Declared review window", "started_at": "2026-01-01T00:00:00Z",
                "cutoff_at": "2026-01-01T00:10:00Z", "window_outcome": "interrupted",
                "eventual_outcome": "unknown", "eventual_at": None, "setup_failure": None,
                "placement": None, "rounds": None, "displayed_timer": None,
                "wall_seconds": None, "active_seconds": None,
                "usage": {"scope": None, "tokens": None, "cost": None, "currency": None},
                "coaching_interventions": None, "evidence_availability": "partial", **values}

    def summary(self):
        return corpus.summarize(self.manifest, {"source-1": self.content})

    def test_continuations_duplicates_original_and_eventual_outcomes(self):
        first = self.manifest["segments"][0]
        terminal = self.segment(id="segment-2", cutoff_at="2026-01-01T00:30:00Z", window_outcome="win",
                                eventual_outcome="win", eventual_at="2026-01-01T00:20:00Z", placement=1)
        self.manifest["segments"] += [terminal, copy.deepcopy(first), copy.deepcopy(terminal)]
        before = copy.deepcopy(self.manifest)
        result = self.summary()
        group = result["groups"][0]
        self.assertEqual(group["starts"], 1)
        self.assertEqual(group["original_window"]["counts"]["interrupted"], 1)
        self.assertEqual(group["eventual"]["counts"]["win"], 1)
        self.assertEqual(group["eventual"]["fractions"]["win"], 1)
        self.assertEqual(result["duplicate_segments"], 2)
        self.assertEqual(len(group["segments"]), 2)
        self.assertEqual(group["usage_coverage"]["cost"], {"known_segments": 0, "unknown_segments": 2})
        self.assertEqual(self.manifest, before)
        self.manifest["segments"].reverse()
        self.assertEqual(self.summary(), result)

    def test_setup_only_zero_denominators_and_exclusion(self):
        row = self.manifest["segments"][0]
        row.update(started_at=None, setup_failure="Launch failed", window_outcome="unknown")
        result = self.summary()
        group = result["groups"][0]
        self.assertEqual(group["starts"], 0)
        self.assertEqual(len(group["setup_failures"]), 1)
        self.assertTrue(all(value is None for value in group["eventual"]["fractions"].values()))
        row.update(included=False, reason="Reviewed setup-only exclusion")
        result = self.summary()
        self.assertEqual(result["groups"], [])
        self.assertEqual(result["excluded_segments"][0]["reason"], row["reason"])

    def test_conflicting_start_condition_terminal_and_duplicate(self):
        first = self.manifest["segments"][0]
        for updates in ({"started_at": "2026-01-01T00:00:01Z"}, {"condition_id": "other"},
                        {"eventual_outcome": "loss", "eventual_at": "2026-01-01T00:20:00Z"}):
            self.manifest["segments"] = [copy.deepcopy(first), self.segment(id="segment-2", **updates)]
            if "eventual_outcome" in updates:
                self.manifest["segments"][0].update(eventual_outcome="win", eventual_at="2026-01-01T00:20:00Z")
            with self.subTest(updates=updates), self.assertRaisesRegex(ValueError, "Conflicting"):
                self.summary()
        self.manifest["segments"] = [first, {**first, "rounds": 1}]
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.summary()

    def test_mixed_conditions_missing_evidence_and_usage(self):
        self.manifest["sources"][0]["availability"] = "missing"
        second = self.segment(id="segment-2", match_id="match-2", condition_id="condition-2",
                              coaching_interventions=0, wall_seconds=12, active_seconds=4,
                              usage={"scope": "segment-2", "tokens": 0, "cost": 0, "currency": "USD"})
        second["conditions"]["build"] = "build-2"
        self.manifest["segments"].append(second)
        result = corpus.summarize(self.manifest, {})
        self.assertEqual(len(result["groups"]), 2)
        self.assertEqual(result["missing_sources"], ["source-1"])
        self.assertEqual(result["groups"][0]["segments"][0]["effective_evidence_availability"], "missing")
        self.assertEqual(result["groups"][1]["usage_coverage"]["cost"]["known_segments"], 1)
        self.assertIsNone(result["groups"][0]["conditions"]["coaching"])

    def test_changed_hash_missing_record_condition_and_bounds_fail(self):
        with self.assertRaisesRegex(ValueError, "hash/size"):
            corpus.summarize(self.manifest, {"source-1": self.content + b" "})
        with self.assertRaisesRegex(ValueError, "exactly"):
            corpus.summarize(self.manifest, {})
        row = self.manifest["segments"][0]
        for field, bad in (("packet_id", "missing"), ("match_id", "other"),
                           ("coaching_interventions", True), ("wall_seconds", float("nan")),
                           ("cutoff_at", "2027-01-01T00:00:00Z"), ("included", 1)):
            original = row[field]
            row[field] = bad
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.summary()
            row[field] = original
        row["usage"]["cost"] = 0
        with self.assertRaisesRegex(ValueError, "scope"):
            self.summary()
        row["usage"]["cost"] = None
        with patch.object(contract, "MAX_BYTES", 32), self.assertRaises(ValueError):
            self.summary()

    def test_cross_source_conflicting_records_fail_even_if_not_included(self):
        changed = json.loads(self.content)
        changed["records"][0]["phase"] = "different"
        data = json.dumps(changed).encode()
        self.manifest["sources"].append({"id": "other", "sha256": hashlib.sha256(data).hexdigest(),
                                         "bytes": len(data), "availability": "available"})
        with self.assertRaisesRegex(ValueError, "Conflicting record"):
            corpus.summarize(self.manifest, {"source-1": self.content, "other": data})

    def test_invalid_source_graph_and_unsafe_paths_and_partial_write(self):
        bad = json.loads(self.content)
        bad["schema_version"] = 99
        bad_bytes = json.dumps(bad).encode()
        declaration = self.manifest["sources"][0]
        declaration.update(sha256=hashlib.sha256(bad_bytes).hexdigest(), bytes=len(bad_bytes))
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            corpus.summarize(self.manifest, {"source-1": bad_bytes})
        declaration.update(sha256=hashlib.sha256(self.content).hexdigest(), bytes=len(self.content))
        source = self.root / "corpus.json"
        source.write_text(json.dumps(self.manifest))
        graph = self.root / "records.json"
        graph.write_bytes(self.content)
        link = self.root / "link.json"
        link.symlink_to(graph)
        with self.assertRaisesRegex(ValueError, "symlinks"):
            corpus.create(str(source), {"source-1": str(link)}, str(self.root / "linked"))
        with patch.object(corpus, "write_json_atomic", side_effect=OSError("disk full")):
            with self.assertRaises(ValueError) as error:
                corpus.create(str(source), {"source-1": str(graph)}, str(self.root / "partial"))
        self.assertTrue(error.exception.receipt["partial"])
        self.assertFalse((self.root / "partial/summary.json").exists())
        self.assertEqual(graph.read_bytes(), self.content)
        with self.assertRaises(FileExistsError):
            corpus.create(str(source), {"source-1": str(graph)}, str(self.root / "partial"))

    def test_cli_and_private_snapshot_reproducibility(self):
        source = self.root / "manifest-input.json"
        source.write_text(json.dumps(self.manifest))
        graph = self.root / "records-input.json"
        graph.write_bytes(self.content)
        parser = argparse.ArgumentParser()
        add_desktop_subparser(parser.add_subparsers(dest="command"))
        output = StringIO()
        arguments = ["desktop", "evidence", "corpus", str(source), "--source", "source-1", str(graph),
                     "--output", str(self.root / "report"), "--json"]
        with patch("sys.stdout", output):
            self.assertEqual(run_desktop_command(parser.parse_args(arguments)), 0)
        result = json.loads(output.getvalue())
        report_path = Path(result["artifact"])
        report = json.loads(report_path.read_text())
        self.assertEqual(report["corpus_sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertEqual(report_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual((report_path.parent / "source-0.json").read_bytes(), self.content)
        graph.unlink()
        replay = corpus.create(str(report_path.parent / "corpus.json"),
                               {"source-1": str(report_path.parent / "source-0.json")}, str(self.root / "replayed"))
        self.assertEqual(json.loads(Path(replay["artifact"]).read_text()), report)
        with self.assertRaises(FileExistsError):
            corpus.create(str(source), {"source-1": str(report_path.parent / "source-0.json")}, str(report_path.parent))
        self.assertFalse(result["input_authority"])


if __name__ == "__main__":
    unittest.main()
