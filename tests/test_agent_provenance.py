"""Recorded setup and observed checkout provenance use separate evidence."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lib.agent_probe import ProbeResult
from lib.installation_info import inspect_installation_context, inspect_installation_provenance, read_refresh_source_metadata, write_refresh_source_metadata
from lib import installation_info
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

    def test_refresh_receipt_survives_unavailable_private_git_without_claiming_observation(self) -> None:
        (self.root / ".git").mkdir()
        self.receipt()
        with patch("lib.installation_info.run_probe", side_effect=[
            ProbeResult(str(self.root)), ProbeResult("b" * 40), ProbeResult(""),
        ]):
            path = write_refresh_source_metadata(str(self.root))
        self.assertEqual(Path(path).stat().st_mode & 0o777, 0o644)
        with patch("lib.installation_info.run_probe", return_value=ProbeResult(issue="probe_unavailable")):
            result = inspect_installation_provenance(str(self.root))
        self.assertEqual(result["commit"], "b" * 40)
        self.assertEqual(result["evidence_source"], "refresh-receipt")
        self.assertEqual(result["installation_type"], "managed-checkout")
        self.assertIsNone(result["running_source"])
        self.assertEqual(result["setup_snapshot"]["commit"], "a" * 40)
        self.assertEqual(result["refresh_receipt"]["commit"], "b" * 40)

    def test_observed_git_prefers_current_revision_over_old_refresh_receipt(self) -> None:
        (self.root / ".git").mkdir()
        with patch("lib.installation_info.run_probe", side_effect=[
            ProbeResult(str(self.root)), ProbeResult("b" * 40), ProbeResult(""),
        ]):
            write_refresh_source_metadata(str(self.root))
        with patch("lib.installation_info.run_probe", side_effect=[
            ProbeResult(str(self.root)), ProbeResult("c" * 40), ProbeResult(" M private-name"),
        ]):
            result = inspect_installation_provenance(str(self.root))
        self.assertEqual(result["commit"], "c" * 40)
        self.assertEqual(result["refresh_receipt"]["commit"], "b" * 40)
        self.assertTrue(result["dirty"])
        self.assertNotIn("private-name", json.dumps(result))

    def test_refresh_receipt_cannot_be_created_without_observed_git(self) -> None:
        with patch("lib.installation_info.run_probe") as probe, self.assertRaises(ValueError):
            write_refresh_source_metadata(str(self.root))
        probe.assert_not_called()
        self.assertFalse((self.root / ".basaltwater-refresh.json").exists())

    def test_unsafe_or_invalid_refresh_receipts_are_rejected(self) -> None:
        path = self.root / ".basaltwater-refresh.json"
        valid = {"schema_version": 1, "source": {"version": "2.0.0", "commit": "b" * 40, "dirty": False}}
        invalid = [" " * 16385, "{", json.dumps({**valid, "schema_version": True}),
                   json.dumps({**valid, "source": {**valid["source"], "dirty": 1}}),
                   json.dumps({**valid, "source": {**valid["source"], "commit": None}}),
                   json.dumps({**valid, "source": {**valid["source"], "credential": "must-not-leak"}})]
        for contents in invalid:
            path.write_text(contents)
            self.assertIsNone(read_refresh_source_metadata(str(self.root)))
        path.unlink()
        path.symlink_to(self.root / "pyproject.toml")
        self.assertIsNone(read_refresh_source_metadata(str(self.root)))

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

    def test_context_distinguishes_developer_managed_activation_and_setup_revisions(self) -> None:
        managed_root = self.root / "managed"
        managed_root.mkdir()
        entry = managed_root / "basaltwater.py"
        entry.write_text("preserve this source")
        invoked = {"commit": "a" * 40, "dirty": True, "evidence_source": "running-git"}
        managed = {"commit": "b" * 40, "dirty": False, "evidence_source": "running-git",
                   "refresh_receipt": {"commit": "c" * 40}, "setup_snapshot": {"commit": "d" * 40}}
        with patch.object(installation_info, "inspect_installation_provenance", side_effect=[invoked, managed]), \
                patch("lib.cachyos.is_cachyos", return_value=True), \
                patch("lib.cachyos_refresh.inspect_successful_setup_source", return_value={"commit": "e" * 40}), \
                patch.object(installation_info, "_cachyos_service_context", return_value={"state": "active", "source": None}):
            result = inspect_installation_context(str(self.root), managed_root=str(managed_root))
        self.assertEqual(result["invoked"], invoked)
        self.assertEqual(result["managed"], managed)
        self.assertEqual(result["managed_source_selection"], "explicit")
        self.assertEqual(result["same_revision"], {"invoked_managed": False, "managed_activation": False,
            "managed_snapshot": False, "managed_last_setup": False, "managed_service": None})
        self.assertEqual(entry.read_text(), "preserve this source")
        self.assertNotIn(str(self.root), json.dumps(result))

    def test_context_keeps_old_reader_missing_revisions_unknown_and_does_not_probe_other_hosts(self) -> None:
        (self.root / "basaltwater.py").write_text("never executed")
        for managed in ({"commit": None}, {"commit": "short"}, {"commit": "a" * 40, "dirty": None}):
            with self.subTest(managed=managed), \
                    patch.object(installation_info, "inspect_installation_provenance", side_effect=[{"commit": "a" * 40}, managed]), \
                    patch("lib.cachyos.is_cachyos", return_value=False), \
                    patch.object(installation_info, "_cachyos_service_context") as service, \
                    patch("lib.cachyos_refresh.inspect_successful_setup_source") as receipt:
                result = inspect_installation_context(str(self.root / "developer"), managed_root=str(self.root))
            self.assertEqual(result["same_revision"]["invoked_managed"], True if len(managed["commit"] or "") == 40 else None)
            self.assertIsNone(result["same_revision"]["managed_activation"])
            self.assertIsNone(result["service_context"])
            self.assertIsNone(result["last_successful_setup"])
            service.assert_not_called()
            receipt.assert_not_called()

    def test_same_source_reuses_observation_and_missing_managed_source_is_not_a_match(self) -> None:
        (self.root / "basaltwater.py").write_text("never executed")
        with patch.object(installation_info, "inspect_installation_provenance", return_value={"commit": "a" * 40}) as inspect, \
                patch("lib.cachyos.is_cachyos", return_value=False):
            result = inspect_installation_context(str(self.root), managed_root=str(self.root))
            self.assertTrue(result["same_revision"]["invoked_managed"])
            inspect.assert_called_once_with(str(self.root))
            (self.root / "basaltwater.py").unlink()
            result = inspect_installation_context(str(self.root), managed_root=str(self.root))
        self.assertIsNone(result["managed"])
        self.assertIsNone(result["same_revision"]["invoked_managed"])

    def test_successful_native_setup_receipt_omits_arguments_and_rejects_unsafe_or_old_metadata(self) -> None:
        from lib.cachyos_refresh import inspect_successful_setup_source

        path = self.root / "last-report.json"
        valid = {"schema_version": 1, "source": {"commit": "a" * 40, "channel": "dev"},
                 "completed_at": "2026-10-12T12:00:00+00:00", "arguments": ["private-credential"]}
        with patch("lib.cachyos_refresh._record_path", return_value=self.root / "last-setup.json"):
            self.assertIsNone(inspect_successful_setup_source())
            for value in (valid, {**valid, "source": {"commit": None}}):
                path.write_text(json.dumps(value))
                path.chmod(0o600)
                result = inspect_successful_setup_source()
                self.assertEqual(result, {"commit": value["source"]["commit"], "completed_at": valid["completed_at"]})
                self.assertNotIn("private-credential", json.dumps(result))
            for value in ({**valid, "schema_version": 2}, {**valid, "schema_version": True},
                          {**valid, "source": {"commit": "bad"}}, {**valid, "completed_at": "not-a-date"},
                          {**valid, "completed_at": "2026-10-12T12:00:00"}):
                path.write_text(json.dumps(value))
                self.assertIsNone(inspect_successful_setup_source())
            path.write_text(" " * (256 * 1024 + 1))
            self.assertIsNone(inspect_successful_setup_source())
            path.write_text(json.dumps(valid))
            path.chmod(0o644)
            self.assertIsNone(inspect_successful_setup_source())
            path.unlink()
            path.symlink_to(self.root / "pyproject.toml")
            self.assertIsNone(inspect_successful_setup_source())

    def test_service_identity_never_infers_loaded_revision_or_activates_a_missing_bus(self) -> None:
        for loaded, active, invocation, expected in (
            ("loaded", "active", "b" * 32, "active"), ("loaded", "inactive", "", "inactive"),
            ("not-found", "inactive", "", "absent"), ("loaded", "private-secret", "", "unknown"),
        ):
            with self.subTest(loaded=loaded, active=active), \
                    patch("lib.cachyos_doctor._owned_socket", return_value=True), \
                    patch.object(installation_info.os, "getuid", return_value=1000), \
                    patch.object(installation_info.os, "geteuid", return_value=1000), \
                    patch("lib.cachyos_doctor._probe", return_value=("ok",
                          f"LoadState={loaded}\nActiveState={active}\nInvocationID={invocation}")) as probe:
                result = installation_info._cachyos_service_context()
            self.assertEqual(result["state"], expected)
            self.assertIsNone(result["source"])
            self.assertNotIn("private-secret", json.dumps(result))
            self.assertEqual(probe.call_args.args[0][:4],
                             ["/usr/bin/systemctl", "--user", "show", "basaltwater-cachyos-t3.service"])
        with patch("lib.cachyos_doctor._owned_socket", return_value=False), \
                patch("lib.cachyos_doctor._probe") as probe:
            self.assertEqual(installation_info._cachyos_service_context()["state"], "unknown")
        probe.assert_not_called()

    def test_version_metadata_reader_is_bounded_regular_and_nonblocking(self) -> None:
        metadata = self.root / "pyproject.toml"
        for kind in ("oversized", "symlink", "fifo"):
            with self.subTest(kind=kind):
                metadata.unlink()
                if kind == "oversized":
                    metadata.write_bytes(b"x" * 65537)
                elif kind == "symlink":
                    metadata.symlink_to(self.root / "missing")
                else:
                    os.mkfifo(metadata)
                with patch.object(installation_info.importlib_metadata, "version", return_value="1.0.0"):
                    self.assertEqual(installation_info._project_version(str(self.root)), "1.0.0")
