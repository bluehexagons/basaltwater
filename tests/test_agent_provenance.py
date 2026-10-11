"""Recorded setup and observed checkout provenance use separate evidence."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lib.agent_probe import ProbeResult
from lib.installation_info import inspect_installation_provenance
from lib import agent_environment, agent_support


class ProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.root / "pyproject.toml").write_text('[project]\nversion = "2.0.0rc1"\n')

    def receipt(self) -> None:
        (self.root / ".basaltwater-installation.json").write_text(json.dumps({
            "schema_version": 1, "installation_type": "setup-snapshot", "version": "2.0.0",
            "commit": "a" * 40, "dirty": False, "branch": "private-branch",
        }))

    def test_receipt_only_is_not_observed_git(self) -> None:
        self.receipt()
        with patch("lib.installation_info.run_probe") as probe:
            result = inspect_installation_provenance(str(self.root))
        self.assertEqual(result["evidence_source"], "setup-receipt")
        self.assertIsNone(result["running_source"])
        self.assertEqual(result["setup_snapshot"]["commit"], "a" * 40)
        self.assertNotIn("private-branch", json.dumps(result))
        probe.assert_not_called()

    def test_clean_dirty_and_unavailable_status_checkouts(self) -> None:
        (self.root / ".git").write_text("gitdir: private-worktree-path")
        for status, dirty in ((ProbeResult(""), False), (ProbeResult(" M private-file"), True),
                              (ProbeResult(issue="probe_timeout"), None)):
            with self.subTest(dirty=dirty), patch("lib.installation_info.run_probe", side_effect=[
                ProbeResult(str(self.root)), ProbeResult("b" * 40), status,
            ]) as probe:
                result = inspect_installation_provenance(str(self.root))
            self.assertEqual(result["installation_type"], "source-checkout")
            self.assertEqual(result["evidence_source"], "running-git")
            self.assertEqual(result["dirty"], dirty)
            self.assertIsNone(result["setup_snapshot"])
            self.assertNotIn("private-file", json.dumps(result))
            self.assertEqual(probe.call_args.kwargs["env"]["GIT_OPTIONAL_LOCKS"], "0")

    def test_observed_checkout_does_not_overwrite_recorded_snapshot(self) -> None:
        self.receipt()
        (self.root / ".git").mkdir()
        with patch("lib.installation_info.run_probe", side_effect=[
            ProbeResult(str(self.root)), ProbeResult("b" * 40), ProbeResult(" M changed"),
        ]):
            result = inspect_installation_provenance(str(self.root))
        self.assertEqual(result["commit"], "b" * 40)
        self.assertEqual(result["setup_snapshot"]["commit"], "a" * 40)
        self.assertEqual(result["running_source"]["version"], "2.0.0rc1")

    def test_non_git_or_unavailable_git_is_unrecorded(self) -> None:
        with patch("lib.installation_info.run_probe") as probe:
            result = inspect_installation_provenance(str(self.root))
        self.assertEqual(result["installation_type"], "unrecorded")
        self.assertIsNone(result["dirty"])
        probe.assert_not_called()
        (self.root / ".git").mkdir()
        for response in (ProbeResult(issue="probe_unavailable"), ProbeResult("/different/git/root")):
            with patch("lib.installation_info.run_probe", return_value=response):
                result = inspect_installation_provenance(str(self.root))
            self.assertEqual(result["evidence_source"], "unrecorded")

    def test_unsafe_oversized_receipts_are_not_read(self) -> None:
        receipt = self.root / ".basaltwater-installation.json"
        for contents in (" " * 16385, '{"schema_version":true}', '{"schema_version":1,"schema_version":1}'):
            receipt.write_text(contents)
            self.assertEqual(inspect_installation_provenance(str(self.root))["installation_type"], "unrecorded")

    def test_manifest_and_support_agree_for_running_checkout(self) -> None:
        (self.root / ".git").mkdir()
        source = [ProbeResult(str(self.root)), ProbeResult("b" * 40), ProbeResult(" M private-file")]
        host = {"healthy": True, "status": "ok", "memory": {}, "disk": {},
                "agent_storage": {"size_bytes": {}, "codex_release_count": 0},
                "t3_service": {}, "maintenance": {}, "maintenance_hold": {},
                "reboot_pending": False, "warnings": [], "errors": []}
        with (
            patch("lib.installation_info.run_probe", side_effect=source * 2),
            patch.object(agent_environment, "__file__", str(self.root / "lib/agent_environment.py")),
            patch.object(agent_support, "_INSTALLATION_ROOT", str(self.root)),
            patch("lib.agent_workspace._repository_root", return_value=str(self.root)),
            patch("lib.agent_workspace._effective_home", return_value=str(self.root)),
            patch("lib.agent_workspace._worktree_record", return_value={"branch": "private-branch", "head": "a" * 40, "dirty": False}),
            patch.object(agent_environment, "is_cachyos", return_value=False),
            patch.object(agent_environment.shutil, "which", return_value=None),
            patch("lib.agent_cli.inspect_agent_tools", return_value=[]),
            patch("lib.agent_cli.inspect_host_readiness", return_value=host),
            patch("lib.agent_cli.inspect_t3code", return_value={"healthy": True, "checks": {}, "version": None}),
            patch("lib.agent_cli.inspect_browser_automation", return_value={"installed": False, "registrations": {}, "configured": False, "smoke_test": None, "healthy": None}),
            patch("lib.agent_cli.inspect_development_readiness", return_value={}),
            patch("lib.agent_graphics.run_probe", side_effect=AssertionError("Graphics launched")),
            patch("lib.agent_containers.run_probe", side_effect=AssertionError("Containers probed")),
        ):
            manifest = agent_environment.inspect_environment(str(self.root))
            support = agent_support.build_agent_support_bundle(str(self.root))
        self.assertEqual(manifest["basaltwater"], support["basaltwater"])
        self.assertEqual(support["basaltwater"]["evidence_source"], "running-git")
        self.assertIsNone(support["basaltwater"]["setup_snapshot"])
        self.assertNotIn("private-file", json.dumps(support))
        self.assertNotIn("private-branch", json.dumps(support))
