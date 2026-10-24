"""Focused manifest selectors keep unrelated discovery out of agent context."""

from __future__ import annotations

import argparse
import io
import json
import unittest
from unittest.mock import patch

from lib.agent_environment import add_manifest_parser, run_manifest_command


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


if __name__ == "__main__":
    unittest.main()
