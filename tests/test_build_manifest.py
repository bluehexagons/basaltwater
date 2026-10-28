from __future__ import annotations

import copy
from pathlib import Path
import tempfile
import unittest

from lib.build_manifest import (
    DEBIAN_PLATFORM, check_tool_readiness, request_from_manifest,
    required_tools, validate_manifest_request,
)
from lib.project_manifest import parse_manifest
from tests.test_build_protocol import build_request


class BuildManifestTests(unittest.TestCase):
    def manifest(self):
        return parse_manifest({
            "version": 1, "build_runtimes": ["go"],
            "components": [{"name": "api", "type": "service", "domain": "{{domain}}",
                            "binary": "./bin/api", "port": "auto", "build": "go build -o bin/api .",
                            "env": {"CGO_ENABLED": "0"}, "runtime_env": {"TOKEN": "runtime-canary"},
                            "env_file": "/etc/private-api.env"}],
        })

    def request(self, manifest):
        return request_from_manifest(manifest, request_id="a" * 32, project="example",
                                     source=build_request()["source"], platform=DEBIAN_PLATFORM)

    def test_selection_is_exact_and_never_carries_target_or_runtime_inputs(self):
        manifest = self.manifest()
        request = self.request(manifest)
        self.assertEqual(request["components"], build_request()["components"])
        self.assertNotIn("runtime-canary", str(request))
        self.assertNotIn("private-api.env", str(request))
        self.assertNotIn("domain", str(request))
        self.assertEqual(validate_manifest_request(manifest, request), request)
        request["components"][0]["env"]["CGO_ENABLED"] = "1"
        self.assertEqual(manifest.components[0].env["CGO_ENABLED"], "0")

    def test_revalidation_rejects_changed_commands_environment_or_exports(self):
        manifest = self.manifest()
        original = self.request(manifest)
        for key, value in (("commands", ["different"]), ("env", {}), ("artifacts", ["bin/other"])):
            with self.subTest(key=key):
                changed = copy.deepcopy(original)
                changed["components"][0][key] = value
                with self.assertRaisesRegex(ValueError, "differ"):
                    validate_manifest_request(manifest, changed)

    def test_tool_selection_requires_explicit_command_dependencies(self):
        manifest = self.manifest()
        self.assertEqual(required_tools(manifest), ["go"])
        self.assertEqual(check_tool_readiness(manifest, {"go": "go1.26", "node": "v26"}), {"go": "go1.26"})
        for versions in ({}, {"go": ""}, {"go": False}):
            with self.subTest(versions=versions), self.assertRaises(ValueError):
                check_tool_readiness(manifest, versions)
        manifest.build_runtimes = None
        with self.assertRaisesRegex(ValueError, "explicit"):
            required_tools(manifest)
        manifest.components[0].build.clear()
        self.assertEqual(required_tools(manifest), [])

    def test_static_export_defaults_and_explicit_exec_outputs(self):
        manifest = parse_manifest({"version": 1, "components": [
            {"name": "site", "type": "static", "domain": "{{domain}}", "output": "./dist"},
            {"name": "api", "type": "service", "domain": "{{domain}}", "port": "auto",
             "exec": "{{release_dir}}/bin/api -port {{port}}", "artifacts": ["bin/api", "assets"]},
        ]})
        request = self.request(manifest)
        self.assertEqual([item["artifacts"] for item in request["components"]], [["dist"], ["bin/api", "assets"]])
        manifest.components[1].artifacts = None
        inferred = self.request(manifest)
        self.assertEqual(inferred["components"][1]["artifacts"], ["bin/api"])
        self.assertEqual(validate_manifest_request(manifest, inferred), inferred)

    def test_pinned_source_markers_infer_tools_without_reading_shell_commands(self):
        manifest = self.manifest()
        manifest.build_runtimes = None
        with tempfile.TemporaryDirectory() as source:
            root = Path(source)
            manifest.components[0].build = ["npm ci && npm run build"]
            self.assertEqual(required_tools(manifest, source_path=source), [])
            (root / "package.json").write_text('{}')
            self.assertEqual(required_tools(manifest, source_path=source), ["node"])
            (root / "package.json").unlink()
            (root / "go.mod").write_text('module example.test/api')
            self.assertEqual(required_tools(manifest, source_path=source), ["go"])
            with self.assertRaises(ValueError):
                check_tool_readiness(manifest, {"node": "installed-but-not-granted-go"}, source_path=source)
            self.assertEqual(check_tool_readiness(manifest, {"go": "go1.27"}, source_path=source), {"go": "go1.27"})
            (root / "frontend").mkdir()
            (root / "frontend/package.json").write_text('{}')
            self.assertEqual(required_tools(manifest, source_path=source), ["go", "node"])
            manifest.build_runtimes = []
            self.assertEqual(required_tools(manifest, source_path=source), [])
            manifest.build_runtimes = ["python"]
            self.assertEqual(required_tools(manifest, source_path=source), ["python"])

    def test_inference_ignores_dependency_trees_and_links(self):
        manifest = self.manifest()
        manifest.build_runtimes = None
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as external:
            root = Path(source)
            for name in ("node_modules", "vendor", ".git", ".venv", ".basaltwater"):
                (root / name).mkdir()
                (root / name / "package.json").write_text('{}')
            (Path(external) / "package.json").write_text('{}')
            (root / "linked").symlink_to(external, target_is_directory=True)
            self.assertEqual(required_tools(manifest, source_path=source), [])


if __name__ == "__main__":
    unittest.main()
