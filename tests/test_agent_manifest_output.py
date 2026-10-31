"""Focused manifest selectors keep unrelated discovery out of agent context."""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lib.agent_environment import add_manifest_parser, run_manifest_command
from lib.agent_probe import ProbeResult
from lib.workstation_report import inspect_workstation


class ManifestOutputTests(unittest.TestCase):
    def manifest(self) -> dict:
        return {"schema_version": 1, "host_profile": "cachyos-workstation",
                "basaltwater": {"commit": "current"}, "project_runtime": None,
                "installation_context": None, "health_command": "doctor",
                "tools": {"blender": "/bin/blender", "inkscape": "/bin/inkscape", "git": "/bin/git"},
                "desktop_applications": {"blender": {"readiness": "unverified"},
                                         "inkscape": {"readiness": "unverified"}},
                "desktop_skills": ["/skills/desktop"], "skills": {"large_catalog": True},
                "development": {"readiness": "unverified"}, "required_tools": {}, "recipes": {},
                "browser": {"unrelated": True}}

    def run_cli(self, *argv):
        parser = argparse.ArgumentParser()
        add_manifest_parser(parser.add_subparsers())
        with (patch("lib.agent_environment.inspect_environment", return_value=self.manifest()),
              patch("sys.stdout", new_callable=io.StringIO) as output):
            code = run_manifest_command(parser.parse_args(["manifest", "--json", *argv]))
        self.assertEqual(code, 0)
        return json.loads(output.getvalue())

    def test_default_retains_full_inventory(self) -> None:
        self.assertEqual(self.run_cli(), self.manifest())

    def test_named_application_implies_desktop_scope_and_keeps_provenance(self) -> None:
        result = self.run_cli("--application", "blender")
        self.assertEqual(set(result["desktop_applications"]), {"blender"})
        self.assertEqual(result["tools"], {"blender": "/bin/blender"})
        self.assertEqual(result["basaltwater"], {"commit": "current"})
        self.assertEqual(result["desktop_applications"]["blender"]["readiness"], "unverified")
        self.assertNotIn("skills", result)
        self.assertNotIn("browser", result)

    def test_desktop_scope_and_repeated_applications(self) -> None:
        result = self.run_cli("--capability", "desktop")
        self.assertEqual(set(result["desktop_applications"]), {"blender", "inkscape"})
        result = self.run_cli("--application", "blender", "--application", "inkscape",
                              "--capability", "development")
        self.assertEqual(set(result["desktop_applications"]), {"blender", "inkscape"})
        self.assertIn("development", result)
        self.assertNotIn("skills", result)

    def test_uninstalled_named_application_is_empty_inventory(self) -> None:
        result = self.run_cli("--application", "krita")
        self.assertEqual(result["desktop_applications"], {})
        self.assertNotIn("tools", result)

    def test_workstation_scope_is_opt_in_and_keeps_provenance(self) -> None:
        parser = argparse.ArgumentParser()
        add_manifest_parser(parser.add_subparsers())
        manifest = {**self.manifest(), "workspace": {"repository": "/project"}}
        with (patch("lib.agent_environment.inspect_environment", return_value=manifest),
              patch("lib.agent_environment.load_project_environment", return_value={}),
              patch("lib.workstation_report.inspect_workstation", return_value={"status": "observed"}) as report,
              patch("sys.stdout", new_callable=io.StringIO) as output):
            code = run_manifest_command(parser.parse_args(["manifest", "--capability", "workstation", "--json"]))
        self.assertEqual(code, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["workstation"], {"status": "observed"})
        self.assertEqual(result["basaltwater"], {"commit": "current"})
        self.assertNotIn("desktop_applications", result)
        self.assertNotIn("skills", result)
        report.assert_called_once_with("/project", {})


class WorkstationReportTests(unittest.TestCase):
    def test_versions_are_bounded_system_probes_and_never_verify_a_project(self) -> None:
        project = {"source": None, "required_tools": [], "recipes": {
            "capture": {"description": "Project capture", "requires": ["missing"], "argv": ["do-not-run"]}}}
        def which(name):
            return {"git": "/usr/bin/git", "node": "/home/user/bin/node",
                    "blender": "/usr/bin/blender"}.get(name)
        with (patch("lib.workstation_report.shutil.which", side_effect=which),
              patch("lib.workstation_report.run_probe", return_value=ProbeResult("git version 2.50.1\n")) as probe,
              patch("lib.workstation_report._gpus", return_value={"source": "unavailable", "devices": []})):
            result = inspect_workstation("/project with spaces", project)
        probe.assert_called_once_with(["/usr/bin/git", "--version"], timeout=2, max_bytes=8192,
                                      env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
        self.assertEqual(result["tools"]["git"]["version"], "2.50.1")
        self.assertTrue(result["tools"]["blender"]["installed"])
        self.assertIsNone(result["tools"]["git"]["usable_in_session"])
        self.assertIsNone(result["tools"]["git"]["verified_by_project"])
        self.assertEqual(result["recipes"]["capture"]["missing_tools"], ["missing"])
        self.assertFalse(result["rendering_backends"]["xvfb_glx"]["tools_installed"])
        self.assertIn("/project with spaces", result["commands"]["project_check"])

    def test_local_wrappers_are_not_executed_and_probe_failure_stays_unknown(self) -> None:
        from lib.workstation_report import _tool

        with (patch("lib.workstation_report.shutil.which", return_value="/home/user/bin/git"),
              patch("lib.workstation_report.run_probe") as probe):
            self.assertEqual(_tool("git")["version_probe"], "not-run")
        probe.assert_not_called()
        with (patch("lib.workstation_report.shutil.which", return_value="/usr/bin/git"),
              patch("lib.workstation_report.run_probe", return_value=ProbeResult(issue="probe_timeout"))):
            result = _tool("git")
        self.assertTrue(result["installed"])
        self.assertEqual(result["version_probe"], "probe_timeout")
        self.assertIsNone(result["version"])

    def test_gpu_identity_is_kernel_metadata_without_a_device_probe(self) -> None:
        from lib.workstation_report import _gpus

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            device = root / "card0" / "device"
            device.mkdir(parents=True)
            (device / "vendor").write_text("0x1002\n")
            (device / "device").write_text("0x1234\n")
            (device / "driver").symlink_to("/sys/bus/pci/drivers/amdgpu")
            (root / "card0-DP-1").mkdir()
            with patch("lib.workstation_report.Path", side_effect=lambda path: root if path == "/sys/class/drm" else Path(path)):
                result = _gpus()
        self.assertEqual(result["devices"], [{"card": "card0", "pci_id": "1002:1234",
                                             "driver": "amdgpu", "usable_in_session": None}])


if __name__ == "__main__":
    unittest.main()
