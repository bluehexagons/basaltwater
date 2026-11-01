"""Geometry contracts use synthetic meshes; Blender process calls remain mocked."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from desktop.blender_inspect_scene import bounds, topology
from lib import agent_blender_inspect, agent_cli
from tests.test_agent_visuals import png_bytes


class BlenderInspectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.source = self.root / "model.blend"
        self.source.write_bytes(b"synthetic blend")
        self.output = self.root / "inspection"
        parser = argparse.ArgumentParser()
        agent_cli.add_agent_subparser(parser.add_subparsers())
        self.args = parser.parse_args(["agent", "blender", "inspect", str(self.source), "--output", str(self.output), "--size", "64"])
        self.enterContext(patch.object(agent_blender_inspect, "is_dry_run", return_value=False))
        self.enterContext(patch.object(agent_blender_inspect.shutil, "which", return_value="/usr/bin/blender"))
        self.run = self.enterContext(patch.object(agent_blender_inspect, "run", side_effect=self.inspect))

    def inspect(self, command, **options):
        directory = Path(command[-1])
        options["stdout"].write("Synthetic inspection\n")
        (directory / "inspection.json").write_text(json.dumps({"schema_version": 1, "objects": [{"name": "Prop"}]}))
        for view in ("front", "rear", "underside"):
            for mode in ("solid", "wire", "orientation", "islands"):
                (directory / f"{view}-{mode}.png").write_bytes(png_bytes(64, 64))
        return subprocess.CompletedProcess(command, 0)

    def test_geometry_includes_isolated_vertices_edges_and_face_boundaries(self) -> None:
        vertices = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (10, 0, 0), (11, 0, 0), (20, 0, 0)]
        result, membership = topology(vertices, [(0, 1), (1, 2), (2, 0), (3, 4)], [[0, 1, 2]])
        self.assertEqual(result["component_count"], 3)
        self.assertEqual([item["vertices"] for item in result["components"]], [3, 2, 1])
        self.assertEqual(result["boundary_edges"], 3)
        self.assertEqual(result["loose_edges"], 1)
        self.assertEqual(membership, [0, 0, 0, 1, 1, 2])
        self.assertEqual(result["components"][1]["bounds"]["dimensions"], [1, 0, 0])
        self.assertIsNone(bounds([]))

    def test_nonmanifold_edges_and_component_report_limits(self) -> None:
        result, _ = topology([(0, 0, 0)] * 70, [(0, 1)], [[0, 1, 2], [1, 0, 3], [0, 1, 4]])
        self.assertEqual(result["nonmanifold_edges"], 1)
        self.assertTrue(result["components_truncated"])
        self.assertEqual(len(result["components"]), 64)

    def test_isolated_process_verifies_views_and_retains_source_hash(self) -> None:
        self.args.render_views = True
        result = agent_blender_inspect.inspect_blender(self.args)
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["images"]), 12)
        self.assertEqual(self.source.read_bytes(), b"synthetic blend")
        command = self.run.call_args.args[0]
        self.assertIn("--disable-autoexec", command)
        self.assertIn("--factory-startup", command)
        self.assertEqual(self.run.call_args.kwargs["timeout"], 300)
        self.assertEqual((self.output / "report.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads((self.output / "report.json").read_text()), result)

    def test_invalid_requests_fail_before_launch(self) -> None:
        for attr, value in (("size", 63), ("timeout", 0), ("allow_intersection", [["a", "b"]]), ("intersect", [["a", "a"]]), ("objects", ["bad\nname"])):
            with self.subTest(attr=attr):
                original = getattr(self.args, attr)
                setattr(self.args, attr, value)
                with self.assertRaises(ValueError):
                    agent_blender_inspect.inspect_blender(self.args)
                setattr(self.args, attr, original)
        self.assertFalse(self.output.exists())
        self.run.assert_not_called()

    def test_changed_source_and_missing_views_fail_with_retained_report(self) -> None:
        def changed(command, **options):
            result = self.inspect(command, **options)
            self.source.write_bytes(b"concurrent edit")
            return result
        self.run.side_effect = changed
        result = agent_blender_inspect.inspect_blender(self.args)
        self.assertFalse(result["ok"])
        self.assertIn("Source changed", result["error"])
        self.assertTrue((self.output / "inspection.json").exists())

    def test_timeout_retains_failure_report(self) -> None:
        self.run.side_effect = TimeoutError("deadline reached")
        result = agent_blender_inspect.inspect_blender(self.args)
        self.assertFalse(result["ok"])
        self.assertIsNone(result["returncode"])
        self.assertTrue((self.output / "report.json").exists())


if __name__ == "__main__":
    unittest.main()
