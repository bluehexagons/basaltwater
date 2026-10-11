"""Context creation, software rendering, and physical display tests differ."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from io import StringIO
import json
import unittest
from unittest.mock import patch

from lib import agent_graphics as graphics
from lib.agent_cli import add_agent_subparser, run_agent_command
from lib.agent_probe import ProbeResult


class GraphicsTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(graphics.shutil, "which", side_effect=lambda name, **_: "/usr/bin/" + name))

    def test_software_context_is_healthy_without_hardware_or_frame_claims(self):
        with patch.object(graphics, "run_probe", return_value=ProbeResult(
            "Accelerated: no\nVideo memory: 123456789 MB\nOpenGL renderer string: llvmpipe (LLVM 19.1.7, 256 bits)\nOpenGL core profile version string: 4.5 Mesa 25.0.7\n"
        )) as probe:
            result = graphics.inspect_graphics_context("xvfb")
        self.assertTrue(result["healthy"])
        self.assertTrue(result["context_created"])
        self.assertFalse(result["accelerated"])
        for field in ("rendering_verified", "hardware_profiling_qualified", "physical_display_qualified", "hdr_qualified", "input_latency_qualified"):
            self.assertFalse(result[field])
        self.assertNotIn("123456789", json.dumps(result))
        self.assertEqual(probe.call_args.kwargs["timeout"], 15)
        self.assertIn("--server-args=-screen 0 640x480x24 -nolisten tcp", probe.call_args.args[0])

    def test_display_hardware_context_does_not_qualify_hdr_or_timing(self):
        with patch.dict(graphics.os.environ, {"DISPLAY": ":9"}), patch.object(graphics, "run_probe", return_value=ProbeResult(
            "Accelerated: yes\nOpenGL renderer string: AMD Radeon Graphics\nOpenGL version string: 4.6 Mesa\n"
        )) as probe:
            result = graphics.inspect_graphics_context("display")
        self.assertTrue(result["healthy"])
        self.assertTrue(result["accelerated"])
        self.assertFalse(result["physical_display_qualified"])
        self.assertEqual(probe.call_args.args[0], ["/usr/bin/glxinfo", "-B"])

    def test_missing_display_and_tools_do_not_launch(self):
        with patch.dict(graphics.os.environ, {}, clear=True), patch.object(graphics, "run_probe") as probe:
            self.assertEqual(graphics.inspect_graphics_context("display")["issues"], ["display_missing"])
        probe.assert_not_called()
        with patch.object(graphics.shutil, "which", return_value=None), patch.object(graphics, "run_probe") as probe:
            self.assertEqual(graphics.inspect_graphics_context("xvfb")["issues"], ["glxinfo_missing"])
        probe.assert_not_called()
        with patch.object(graphics.shutil, "which", side_effect=lambda name, **_: "/usr/bin/glxinfo" if name == "glxinfo" else None):
            self.assertEqual(graphics.inspect_graphics_context("xvfb")["issues"], ["xvfb_tools_missing"])

    def test_process_failure_and_success_without_context_are_distinct(self):
        for response, exit_success, issue in ((ProbeResult(issue="probe_failed"), False, "probe_failed"),
                                             (ProbeResult(issue="probe_timeout"), None, "probe_timeout"),
                                             (ProbeResult("nothing useful"), True, "context_creation_unverified")):
            with patch.object(graphics, "run_probe", return_value=response):
                result = graphics.inspect_graphics_context("xvfb")
            self.assertFalse(result["healthy"])
            self.assertEqual(result["process_exit_success"], exit_success)
            self.assertEqual(result["issues"], [issue])

    def test_temporary_directory_failure_does_not_launch_or_expose_paths(self):
        with patch.object(graphics.tempfile, "TemporaryDirectory", side_effect=OSError("/private/temp is full")), \
                patch.object(graphics, "run_probe") as probe:
            result = graphics.inspect_graphics_context("xvfb")
        self.assertEqual(result["issues"], ["probe_unavailable"])
        self.assertNotIn("private", json.dumps(result))
        probe.assert_not_called()

    def test_parser_requires_context_and_dispatches_doctor(self):
        parser = argparse.ArgumentParser()
        add_agent_subparser(parser.add_subparsers(dest="command"))
        args = parser.parse_args(["agent", "graphics", "doctor", "--context", "xvfb", "--json"])
        with patch.object(graphics, "inspect_graphics_context", return_value={"healthy": False}), redirect_stdout(output := StringIO()):
            self.assertEqual(run_agent_command(args), 1)
        self.assertEqual(json.loads(output.getvalue()), {"healthy": False})
