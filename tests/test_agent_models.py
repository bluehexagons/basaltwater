"""Behavioral checks for evolving project catalogs and cost-aware recommendations."""

from __future__ import annotations

import argparse
import copy
import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from lib.agent_cli import add_agent_subparser, run_agent_command
from lib.agent_models import (
    init_model_policy,
    recommend_agent_model,
    record_model_outcome,
    report_model_outcomes,
    validate_model_policy,
)


class AgentModelsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repository = Path(self.temporary.name) / "project"
        (self.repository / ".git").mkdir(parents=True)
        primary = patch("lib.agent_models.primary_agent_repository", return_value=str(self.repository))
        primary.start()
        self.addCleanup(primary.stop)
        clock = patch("lib.agent_models.datetime")
        clock.start().now.return_value = datetime(2026, 10, 9, tzinfo=timezone.utc)
        self.addCleanup(clock.stop)
        init_model_policy(str(self.repository))
        self.policy_path = self.repository / ".basaltwater/agent-models.json"
        self.results_path = self.repository / ".basaltwater/agent-model-results.json"
        self.available_path = self.repository / "session-models.json"
        self.available = [
            {"id": "gpt-6-luna", "efforts": ["low", "medium", "high", "xhigh", "max"]},
            {"id": "gpt-6.1-sol", "efforts": ["low", "medium", "high", "xhigh", "max"]},
            {"id": "gpt-6-astra", "efforts": ["low", "medium", "high", "xhigh", "max"]},
        ]
        self._write(self.available_path, self.available)

    def _write(self, path: Path, value: object) -> None:
        path.write_text(json.dumps(value) + "\n", encoding="utf-8")

    def _policy(self) -> dict:
        return json.loads(self.policy_path.read_text(encoding="utf-8"))

    def _recommend(self, task: str = "classification", **kwargs) -> dict:
        return recommend_agent_model(str(self.repository), task, str(self.available_path), **kwargs)

    def _record(self, model: str = "gpt-6-luna", *, task: str = "classification",
                effort: str = "low", outcome: str = "accepted", cost: float | None = None) -> dict:
        return record_model_outcome(str(self.repository), {
            "task": task, "model": model, "effort": effort, "outcome": outcome,
            "cost_usd": cost, "seconds": 20, "rework_minutes": 0, "attempts": 1,
            "validation": "Parent compared all categories with the agreed rules",
        })

    def test_simple_task_uses_luna_with_runtime_effort_fallback(self) -> None:
        result = self._recommend()
        self.assertEqual(result["selection"]["model"], "gpt-6-luna")
        self.assertEqual(result["selection"]["effort"], "low")
        self.assertEqual(result["service"], "standard")
        self.assertTrue(result["provisional"])
        self.available[0]["efforts"].insert(0, "none")
        self._write(self.available_path, self.available)
        self.assertEqual(self._recommend()["selection"]["effort"], "none")
        self.assertEqual(self._recommend("coding")["selection"]["model"], "gpt-6.1-sol")

    def test_new_model_is_discovered_then_trialled_before_replacing_incumbent(self) -> None:
        for _ in range(3):
            self._record("gpt-6.1-sol", task="coding", effort="medium", cost=0.5)
        self.available.append({"id": "future-openai-model", "efforts": ["medium"]})
        self._write(self.available_path, self.available)
        result = self._recommend("coding")
        self.assertEqual(result["uncatalogued_models"], ["future-openai-model"])
        self.assertTrue(result["refresh_required"])
        policy = self._policy()
        candidate = copy.deepcopy(policy["models"][1])
        candidate.update(id="future-openai-model", input_usd_per_million=1, output_usd_per_million=5)
        policy["models"].append(candidate)
        self._write(self.policy_path, policy)
        result = self._recommend("coding")
        self.assertEqual(result["selection"]["model"], "gpt-6.1-sol")
        self.assertIn("future-openai-model", [item["model"] for item in result["trial_candidates"]])
        for _ in range(3):
            self._record("future-openai-model", task="coding", effort="medium", cost=0.1)
        result = self._recommend("coding")
        self.assertEqual(result["selection"]["model"], "future-openai-model")
        self.assertEqual(result["basis"], "reviewed_cost_per_accepted")
        self.assertFalse(result["provisional"])

    def test_failure_evidence_escalates_and_actual_costs_include_failed_attempts(self) -> None:
        for _ in range(3):
            self._record(outcome="failed", cost=1)
        self.assertEqual(self._recommend()["selection"]["model"], "gpt-6.1-sol")
        for _ in range(17):
            self._record(cost=0.1)
        for _ in range(3):
            self._record("gpt-6.1-sol", cost=0.2)
        result = self._recommend()
        self.assertEqual(result["selection"]["model"], "gpt-6.1-sol")
        self.assertEqual(result["basis"], "reviewed_cost_per_accepted")
        luna = next(row for row in report_model_outcomes(str(self.repository)) if row["model"] == "gpt-6-luna")
        self.assertEqual(luna["failed"], 3)
        self.assertAlmostEqual(luna["cost_per_accepted_usd"], 4.7 / 17)

    def test_unknown_costs_do_not_become_free_and_schemas_do_not_mix(self) -> None:
        for _ in range(3):
            self._record()
            self._record("gpt-6.1-sol", cost=0)
        result = self._recommend()
        self.assertEqual(result["basis"], "token_cost_estimate")
        self.assertEqual(result["selection"]["model"], "gpt-6-luna")
        self.assertIsNone(result["selection"]["cost_per_accepted_usd"])
        policy = self._policy()
        policy["tasks"]["classification"]["evaluation"] = "v2"
        self._write(self.policy_path, policy)
        self.assertEqual(self._recommend()["selection"]["samples"], 0)
        self._record()
        rows = report_model_outcomes(str(self.repository))
        self.assertEqual({row["evaluation"] for row in rows}, {"v1", "v2"})

    def test_proven_higher_effort_remains_eligible_after_default_fails(self) -> None:
        for _ in range(3):
            self._record("gpt-6.1-sol", task="coding", effort="medium", outcome="failed", cost=0.1)
            self._record("gpt-6.1-sol", task="coding", effort="high", cost=0.5)
        result = self._recommend("coding")
        self.assertEqual(result["selection"]["model"], "gpt-6.1-sol")
        self.assertEqual(result["selection"]["effort"], "high")
        self.assertFalse(result["provisional"])
        self.assertEqual(result["basis"], "reviewed_cost_per_accepted")
        # Losing a supported setting must also remove its otherwise proven evidence.
        self._write(self.available_path, [{"id": "gpt-6.1-sol", "efforts": ["medium"]}])
        self.assertIsNone(self._recommend("coding")["selection"])

    def test_efforts_compete_by_cost_and_sparse_higher_effort_needs_a_trial(self) -> None:
        for _ in range(3):
            self._record("gpt-6.1-sol", task="coding", effort="medium", cost=0.5)
            self._record("gpt-6.1-sol", task="coding", effort="high", cost=0.1)
        self.assertEqual(self._recommend("coding")["selection"]["effort"], "high")
        self._record("gpt-6.1-sol", task="coding", effort="medium")
        result = self._recommend("coding")
        self.assertEqual(result["basis"], "token_cost_estimate")
        self.assertEqual(result["selection"]["effort"], "medium")
        policy = self._policy()
        policy["tasks"]["coding"]["evaluation"] = "v2"
        self._write(self.policy_path, policy)
        self._record("gpt-6.1-sol", task="coding", effort="high", cost=0.1)
        result = self._recommend("coding")
        self.assertEqual(result["selection"]["effort"], "medium")
        self.assertIn("high", [item["effort"] for item in result["trial_candidates"]])

    def test_new_lower_effort_does_not_displace_proven_setting(self) -> None:
        for _ in range(3):
            self._record()
        self.available[0]["efforts"].insert(0, "none")
        self._write(self.available_path, self.available)
        result = self._recommend()
        self.assertEqual(result["selection"]["effort"], "low")
        self.assertFalse(result["provisional"])
        self.assertIn("none", [item["effort"] for item in result["trial_candidates"]])

    def test_unobserved_rework_stays_unknown_with_known_subtotal(self) -> None:
        self._record()
        record_model_outcome(str(self.repository), {
            "task": "classification", "model": "gpt-6-luna", "effort": "low",
            "outcome": "reworked", "attempts": 1, "validation": "Parent corrected an ambiguous category",
        })
        row = report_model_outcomes(str(self.repository))[0]
        self.assertEqual(row["rework_samples"], 1)
        self.assertEqual(row["known_rework_minutes"], 0)
        self.assertIsNone(row["rework_minutes"])

    def test_omitted_optional_telemetry_is_unknown_and_history_can_be_extended(self) -> None:
        for _ in range(3):
            self._record()
        history = json.loads(self.results_path.read_text(encoding="utf-8"))
        for run in history["runs"]:
            for field in ("cost_usd", "seconds", "rework_minutes"):
                del run[field]
        self._write(self.results_path, history)
        row = report_model_outcomes(str(self.repository))[0]
        self.assertEqual(row["accepted"], 3)
        self.assertEqual(row["cost_samples"], 0)
        self.assertEqual(row["rework_samples"], 0)
        self.assertIsNone(row["cost_per_accepted_usd"])
        self.assertIsNone(row["rework_minutes"])
        self.assertIsNone(row["mean_seconds"])
        self.assertFalse(self._recommend()["provisional"])
        self._record()
        saved = json.loads(self.results_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["runs"][:3], history["runs"])
        self.assertEqual(report_model_outcomes(str(self.repository))[0]["samples"], 4)

    def test_oversized_numeric_values_are_validation_errors_and_preserve_history(self) -> None:
        self._record()
        before = self.results_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "finite number"):
            self._record(cost=10 ** 400)
        self.assertEqual(self.results_path.read_bytes(), before)
        policy = self._policy()
        policy["models"][0]["input_usd_per_million"] = 10 ** 400
        self._write(self.policy_path, policy)
        parser = argparse.ArgumentParser()
        add_agent_subparser(parser.add_subparsers(dest="command"))
        args = parser.parse_args(["agent", "models", "report", str(self.repository), "--json"])
        with patch("builtins.print") as output:
            self.assertEqual(run_agent_command(args), 1)
        self.assertIn("finite number", output.call_args.args[0])

    def test_overflowing_statistics_and_estimates_fail_without_invalid_json(self) -> None:
        for _ in range(2):
            self._record(cost=1e308)
        before = self.results_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "total recorded cost"):
            report_model_outcomes(str(self.repository))
        self.assertEqual(self.results_path.read_bytes(), before)
        policy = self._policy()
        policy["tasks"]["classification"]["expected_input_tokens"] = 1e308
        policy["models"][0]["input_usd_per_million"] = 1e308
        self._write(self.policy_path, policy)
        with self.assertRaisesRegex(ValueError, "estimated token cost"):
            self._recommend()

    def test_results_schema_version_requires_an_integer(self) -> None:
        self._record()
        history = json.loads(self.results_path.read_text(encoding="utf-8"))
        for version in (True, 1.0):
            with self.subTest(version=version):
                history["schema_version"] = version
                self._write(self.results_path, history)
                with self.assertRaisesRegex(ValueError, "results schema"):
                    report_model_outcomes(str(self.repository))

    def test_old_evidence_and_stale_catalog_need_fresh_evaluation(self) -> None:
        for _ in range(3):
            self._record("gpt-6.1-sol", cost=0.2)
        results = json.loads(self.results_path.read_text(encoding="utf-8"))
        for run in results["runs"]:
            run["date"] = "2026-01-01"
        self._write(self.results_path, results)
        self.assertEqual(self._recommend()["selection"]["model"], "gpt-6-luna")
        result = self._recommend(today=date(2026, 12, 1))
        self.assertIsNone(result["selection"])
        self.assertTrue(result["refresh_required"])
        self.assertEqual(set(result["stale_models"]), {"gpt-6-luna", "gpt-6.1-sol"})

    def test_astra_needs_a_reason_and_unsafe_runtime_efforts_are_excluded(self) -> None:
        self.assertIsNone(self._recommend("specialist")["selection"])
        self.assertEqual(self._recommend("specialist", astra_reason="Independent specialized analysis")
                         ["selection"]["model"], "gpt-6-astra")
        self._write(self.available_path, [{"id": "gpt-6-luna", "efforts": ["max", "ultra"]}])
        self.assertIsNone(self._recommend()["selection"])
        policy = self._policy()
        policy["models"][0]["efforts"] = ["max"]
        with self.assertRaisesRegex(ValueError, "exclude max"):
            validate_model_policy(policy)

    def test_cache_visibility_and_exact_supported_efforts(self) -> None:
        self._write(self.available_path, {"models": [
            {"slug": "gpt-6-luna", "visibility": "hide", "supported_reasoning_levels": [{"effort": "low"}]},
            {"slug": "gpt-6.1-sol", "visibility": "list", "supported_reasoning_levels": [{"effort": "medium"}]},
        ]})
        self.assertEqual(self._recommend()["selection"]["model"], "gpt-6.1-sol")
        self._write(self.available_path, [{"id": "gpt-6-luna", "efforts": []}])
        self.assertIsNone(self._recommend()["selection"])

    def test_initialization_invalid_outcomes_and_symlinks_preserve_data(self) -> None:
        before = self.policy_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "already exists"):
            init_model_policy(str(self.repository))
        self.assertEqual(self.policy_path.read_bytes(), before)
        self._record()
        before = self.results_path.read_bytes()
        for cost in (float("nan"), float("inf"), -1):
            with self.subTest(cost=cost), self.assertRaises(ValueError):
                self._record(cost=cost)
            self.assertEqual(self.results_path.read_bytes(), before)
        self.results_path.unlink()
        outside = Path(self.temporary.name) / "outside.json"
        outside.write_bytes(before)
        self.results_path.symlink_to(outside)
        with self.assertRaises(OSError):
            self._record()
        self.assertEqual(outside.read_bytes(), before)

    def test_linked_worktree_routes_to_primary_and_shared_lock_refuses_concurrent_writer(self) -> None:
        import fcntl

        worktree = Path(self.temporary.name) / "worker"
        worktree.mkdir()
        self.assertEqual(recommend_agent_model(str(worktree), "coding", str(self.available_path))
                         ["selection"]["model"], "gpt-6.1-sol")
        with (self.repository / ".git/basaltwater-agent-models.lock").open("r+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError, "Another parent"):
                self._record()
        self._record()
        self.assertEqual(len(report_model_outcomes(str(worktree))), 1)

    def test_cli_routes_and_empty_recommendation_returns_nonzero(self) -> None:
        parser = argparse.ArgumentParser()
        add_agent_subparser(parser.add_subparsers(dest="command"))
        args = parser.parse_args(["agent", "models", "recommend", str(self.repository), "specialist",
                                  "--available", str(self.available_path), "--json"])
        with patch("builtins.print"):
            self.assertEqual(run_agent_command(args), 1)
        args = parser.parse_args(["agent", "models", "report", str(self.repository), "--json"])
        with patch("builtins.print"):
            self.assertEqual(run_agent_command(args), 0)


if __name__ == "__main__":
    unittest.main()
