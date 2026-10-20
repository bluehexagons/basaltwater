"""Project cleanup inspection describes bounded evidence without running project code."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from copy import deepcopy
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lib import agent_project_cleanup as cleanup
from lib import agent_storage_inventory as inventory


class ProjectCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.scan = self.enterContext(patch.object(inventory, "inspect_storage", return_value={"issues": [], "candidates": []}))
        self.mounts = self.enterContext(patch.object(inventory, "_mounts", return_value={"/": "ext4"}))
        self.process = self.enterContext(patch("subprocess.run", side_effect=AssertionError("Inspection must not execute project commands")))

    def write_policy(self, value: object, path: str = cleanup.POLICY_PATH) -> Path:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value))
        return target

    def policy(self) -> dict:
        return {"version": 1, "instructions": "Use the native project layout.", "preserve": ["evidence/keep"],
                "rules": [
                    {"name": "build", "paths": ["out"], "strategy": "script", "instructions": "Rebuildable outputs only.",
                     "preview": ["python3", "tools/clean.py", "--dry-run"], "apply": ["python3", "tools/clean.py", "--apply"]},
                    {"name": "captures", "paths": ["evidence"], "strategy": "agent", "instructions": "Curate milestones.",
                     "archive": "evidence/history", "min_age_days": 30}]}

    def test_custom_layout_policy_describes_commands_and_preserved_subtrees(self) -> None:
        expected = self.policy()
        self.write_policy(expected, "tools/retention.json")
        result = cleanup.inspect_project_cleanup(str(self.root), policy_path="tools/retention.json", max_entries=19, timeout=2)
        self.assertEqual(result["policy"], expected)
        self.assertIn("tools/retention.json", result["protected_paths"])
        self.assertEqual(result["status"], "review_required")
        self.assertIsNone(result["suggested_policy"])
        self.assertEqual(result["policy_path"], str(self.root / "tools/retention.json"))
        self.scan.assert_called_once_with(paths=[str(self.root / "out"), str(self.root / "evidence")], max_entries=19, timeout=2)
        self.process.assert_not_called()
        self.assertFalse((self.root / "out").exists())

    def test_absence_suggests_valid_fallback_without_scanning_or_creating_it(self) -> None:
        result = cleanup.inspect_project_cleanup(str(self.root))
        self.assertEqual(result["status"], "absent")
        self.assertEqual(cleanup.validate_cleanup_policy(result["suggested_policy"]), cleanup.default_cleanup_policy())
        self.assertTrue(all(rule["strategy"] == "agent" for rule in result["suggested_policy"]["rules"]))
        result["suggested_policy"]["preserve"].clear()
        self.assertEqual(cleanup.default_cleanup_policy()["preserve"], ["artifacts/archive"])
        self.assertIsNone(result["evidence"])
        self.scan.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_sensitive_custom_policy_paths_are_refused_before_loading(self) -> None:
        for path in (".git/policy.json", "cache/.git", ".ssh/policy.json", ".credentials/policy.json",
                     "credentials/policy.json", "secrets/policy.json", ".env", ".env.production",
                     "config/.aws/policy.json", ".azure/policy.json", ".kube/policy.json",
                     ".gnupg/policy.json", ".agents/policy.json", ".codex/policy.json",
                     ".t3/policy.json", ".basaltwater/private/.credentials/policy.json"):
            self.write_policy(self.policy(), path)
            with self.subTest(path=path), patch.object(cleanup, "_load_policy", wraps=cleanup._load_policy) as load:
                with self.assertRaisesRegex(ValueError, "policy cannot use"):
                    cleanup.inspect_project_cleanup(str(self.root), policy_path=path)
                load.assert_not_called()
        self.scan.assert_not_called()

    def test_basaltwater_default_and_custom_policy_locations_remain_readable(self) -> None:
        expected = self.policy()
        for path in (cleanup.POLICY_PATH, ".basaltwater/policies/retention.json", "tools/retention.json"):
            self.write_policy(expected, path)
            with self.subTest(path=path):
                self.assertEqual(cleanup.read_cleanup_policy(str(self.root), policy_path=path), expected)
        self.process.assert_not_called()

    def test_generated_rules_offer_native_commands_without_running_them(self) -> None:
        value = self.policy()
        value["rules"][0] = {"name": "build", "paths": ["build/linux", "build/web"],
                              "strategy": "generated", "instructions": "Keep the parent .gdignore."}
        self.write_policy(value, "tools/cleanup.json")
        result = cleanup.inspect_project_cleanup(str(self.root), policy_path="tools/cleanup.json")
        command = result["generated_cleanup_commands"][0]
        self.assertEqual(command["rule"], "build")
        self.assertEqual(command["preview"], ["basaltw", "agent", "storage", "clean-generated", str(self.root),
                                              "--policy", "tools/cleanup.json", "--rule", "build"])
        self.assertEqual(command["apply"], [*command["preview"], "--apply"])
        self.process.assert_not_called()
        for changes in ({"paths": ["build", "build/web"]}, {"paths": ["evidence"]},
                        {"paths": ["tools"]}, {"min_age_days": 30}, {"preview": ["anything"]}):
            invalid = deepcopy(value)
            invalid["rules"][0].update(changes)
            self.write_policy(invalid, "tools/cleanup.json")
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                cleanup.read_cleanup_policy(str(self.root), policy_path="tools/cleanup.json")

    def test_generated_cli_defaults_to_preview_and_requires_explicit_apply(self) -> None:
        parser = argparse.ArgumentParser()
        inventory.add_storage_parser(parser.add_subparsers())
        command = ["storage", "clean-generated", str(self.root), "--rule", "build"]
        for flag, apply in (([], False), (["--dry-run"], False), (["--apply"], True)):
            with self.subTest(flag=flag), patch("lib.agent_generated_cleanup.clean_generated", return_value=0) as run:
                self.assertEqual(inventory.run_storage_command(parser.parse_args(command + flag)), 0)
                run.assert_called_once_with(str(self.root), "build", policy_path=cleanup.POLICY_PATH, apply=apply)
        with redirect_stdout(StringIO()), patch("sys.stderr", StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(command + ["--apply", "--dry-run"])

    def test_remote_or_unknown_policy_locality_stops_before_open(self) -> None:
        for mounts in ({"/": "nfs4"}, {}):
            with self.subTest(mounts=mounts), patch.object(inventory, "_open_directory") as opened:
                self.mounts.return_value = mounts
                with self.assertRaisesRegex(ValueError, "local filesystem"):
                    cleanup.inspect_project_cleanup(str(self.root))
                opened.assert_not_called()
        self.mounts.side_effect = PermissionError
        with self.assertRaisesRegex(ValueError, "locality is unavailable"):
            cleanup.inspect_project_cleanup(str(self.root))
        self.scan.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_script_targets_cannot_overlap_curated_or_protected_paths(self) -> None:
        for paths in (["evidence"], ["evidence/new"], ["evidence/history"], ["evidence/keep"], [".git"],
                      ["cache/.codex"], ["private"], [".ssh"], ["out/secrets"], [".env.local"]):
            value = self.policy()
            value["preserve"].append("private/keep")
            value["rules"][0]["paths"] = paths
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                cleanup.validate_cleanup_policy(value)
        value = self.policy()
        value["rules"][1]["paths"] = ["evidence/history/selected"]
        with self.assertRaises(ValueError):
            cleanup.validate_cleanup_policy(value)

    def test_malformed_rules_and_paths_are_rejected_before_scan(self) -> None:
        base = self.policy()
        variants = []
        for path in ("/tmp", "../out", "out/../other", "./out", "out//x", "out/*", "out\\x", "out\n"):
            value = deepcopy(base)
            value["rules"][0]["paths"] = [path]
            variants.append(value)
        for change in ({"version": True}, {"unknown": 1}, {"rules": []}, {"preserve": "out"}):
            variants.append(dict(base, **change))
        for change in ({"preview": "rm -rf out"}, {"archive": "history"}, {"min_age_days": 0}, {"extra": True},
                       {"paths": ["out"] * 2}, {"paths": [f"out{i}" for i in range(9)]}):
            value = deepcopy(base)
            value["rules"][0].update(change)
            variants.append(value)
        value = deepcopy(base)
        value["rules"][1]["name"] = "build"
        variants.append(value)
        for value in variants:
            with self.subTest(value=value):
                self.write_policy(value)
                with self.assertRaises(ValueError):
                    cleanup.inspect_project_cleanup(str(self.root))
        self.scan.assert_not_called()

    def test_policy_file_and_ancestors_must_be_regular_unlinked_bounded_json(self) -> None:
        target = self.write_policy(self.policy())
        for content in ('{"version": 1, "version": 1}', '"' + 'x' * 65536 + '"'):
            target.write_text(content)
            with self.subTest(content_size=len(content)), self.assertRaises(ValueError):
                cleanup.inspect_project_cleanup(str(self.root))
        target.unlink()
        os.mkfifo(target)
        with self.assertRaises(ValueError):
            cleanup.inspect_project_cleanup(str(self.root))
        target.unlink()
        actual = self.write_policy(self.policy(), "rules.json")
        target.symlink_to(actual)
        with self.assertRaises(OSError):
            cleanup.inspect_project_cleanup(str(self.root))
        target.unlink()
        target.parent.rmdir()
        target.parent.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            cleanup.inspect_project_cleanup(str(self.root))
        link = self.root / "project-link"
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            cleanup.inspect_project_cleanup(str(link), policy_path="rules.json")
        self.scan.assert_not_called()

    def test_cli_custom_policy_dispatch_errors_and_bounds(self) -> None:
        parser = argparse.ArgumentParser()
        inventory.add_storage_parser(parser.add_subparsers())
        self.write_policy(self.policy(), "tools/cleanup.json")
        args = parser.parse_args(["storage", "project", str(self.root), "--policy", "tools/cleanup.json", "--json"])
        with redirect_stdout(StringIO()) as output:
            self.assertEqual(inventory.run_storage_command(args), 0)
        self.assertEqual(json.loads(output.getvalue())["policy"]["rules"][0]["paths"], ["out"])
        self.scan.return_value = {"issues": ["mount_inventory_unavailable"]}
        with redirect_stdout(StringIO()):
            self.assertEqual(inventory.run_storage_command(args), 1)
        self.scan.reset_mock()
        for kwargs in ({"policy_path": "../other"}, {"max_entries": 0}, {"timeout": 61}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                cleanup.inspect_project_cleanup(str(self.root), **kwargs)
        value = self.policy()
        value["rules"][0]["paths"] = ["tools"]
        self.write_policy(value, "tools/cleanup.json")
        with self.assertRaisesRegex(ValueError, "policy file"):
            cleanup.inspect_project_cleanup(str(self.root), policy_path="tools/cleanup.json")
        args.policy = "../other"
        with redirect_stdout(StringIO()) as output:
            self.assertEqual(inventory.run_storage_command(args), 1)
        self.assertIn("error", json.loads(output.getvalue()))
        self.scan.assert_not_called()


if __name__ == "__main__":
    unittest.main()
