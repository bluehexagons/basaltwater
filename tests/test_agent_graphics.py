"""Context creation, software rendering, and physical display tests differ."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from io import StringIO
import json
import unittest
from unittest.mock import MagicMock, patch

from lib import agent_graphics as graphics
from lib import agent_egl_probe as egl_probe
from lib.agent_cli import add_agent_subparser, run_agent_command
from lib.agent_probe import ProbeResult


class GraphicsTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(graphics.shutil, "which", side_effect=lambda name, **_: "/usr/bin/" + name))

    def test_software_context_is_healthy_without_hardware_or_frame_claims(self):
        with patch.object(graphics, "run_probe", return_value=ProbeResult(
            "Accelerated: no\nVideo memory: 123456789 MB\nOpenGL renderer string: llvmpipe (LLVM 19.1.7, 256 bits)\nOpenGL core profile version string: 4.5 Mesa 25.0.7\n"
        )) as probe:
            result = graphics.inspect_graphics_context("xvfb", require_software=True)
        self.assertTrue(result["healthy"])
        self.assertTrue(result["context_created"])
        self.assertFalse(result["accelerated"])
        self.assertTrue(result["software_renderer"])
        self.assertTrue(result["software_required"])
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
        self.assertFalse(result["software_renderer"])
        self.assertFalse(result["software_required"])
        self.assertFalse(result["physical_display_qualified"])
        self.assertEqual(probe.call_args.args[0], ["/usr/bin/glxinfo", "-B"])

    def test_xvfb_prefers_atomic_allocation_and_supports_older_wrappers(self):
        for help_result, flag, method in (
            (ProbeResult("Options:\n-d        --auto-display            choose display\n"), "-d", "auto-display"),
            (ProbeResult("-a --auto-servernum\n"), "-a", "auto-servernum"),
            (ProbeResult(issue="probe_timeout"), "-a", "auto-servernum"),
        ):
            with self.subTest(method=method, issue=help_result.issue), patch.object(graphics, "run_probe", side_effect=[
                help_result, ProbeResult("OpenGL renderer string: llvmpipe\nOpenGL version string: 4.6 Mesa\n"),
            ]) as probe:
                result = graphics.inspect_graphics_context("xvfb", require_software=True)
            self.assertTrue(result["healthy"])
            self.assertEqual(result["xvfb_allocation"], method)
            self.assertEqual(probe.call_args_list[0].args[0], ["/usr/bin/xvfb-run", "--help"])
            self.assertEqual(probe.call_args_list[1].args[0][:2], ["/usr/bin/xvfb-run", flag])

    def test_software_requirement_uses_observed_renderer_not_environment_or_acceleration_alone(self):
        for acceleration, software in (("yes", False), ("no", None), ("", None)):
            with self.subTest(acceleration=acceleration), \
                    patch.dict(graphics.os.environ, {"LIBGL_ALWAYS_SOFTWARE": "1", "GALLIUM_DRIVER": "llvmpipe"}), \
                    patch.object(graphics, "run_probe", return_value=ProbeResult(
                        f"Accelerated: {acceleration}\nOpenGL renderer string: Other GPU\nOpenGL version string: 4.6 Mesa\n"
                    )):
                required = graphics.inspect_graphics_context("xvfb", require_software=True)
                ordinary = graphics.inspect_graphics_context("xvfb")
            self.assertTrue(required["process_exit_success"])
            self.assertTrue(required["context_created"])
            self.assertIs(required["software_renderer"], software)
            self.assertFalse(required["healthy"])
            self.assertEqual(required["issues"], ["software_renderer_unverified"])
            self.assertTrue(ordinary["healthy"])
            self.assertEqual(ordinary["issues"], [])

    def test_software_names_need_a_complete_context_and_whole_tokens(self):
        for renderer, version, expected in (("softpipe", "3.3 Mesa", True),
                                             ("SwiftShader Device", "4.1", True),
                                             ("notllvmpipe", "4.6", None),
                                             ("llvmpipe", "", None)):
            with self.subTest(renderer=renderer, version=version), \
                    patch.object(graphics, "run_probe", return_value=ProbeResult(
                        f"OpenGL renderer string: {renderer}\nOpenGL version string: {version}\n"
                    )):
                result = graphics.inspect_graphics_context("xvfb", require_software=True)
            self.assertIs(result["software_renderer"], expected)
            self.assertEqual(result["healthy"], expected is True)

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
        args = parser.parse_args(["agent", "graphics", "doctor", "--context", "xvfb", "--require-software", "--json"])
        with patch.object(graphics, "inspect_graphics_context", return_value={"healthy": False}) as inspect, redirect_stdout(output := StringIO()):
            self.assertEqual(run_agent_command(args), 1)
        inspect.assert_called_once_with("xvfb", require_software=True, backend="glx", expect_renderer=None)
        self.assertEqual(json.loads(output.getvalue()), {"healthy": False})

    def test_egl_uses_observed_renderer_despite_software_selectors(self):
        for renderer, expected in (("NVIDIA Example GPU", False), ("llvmpipe (LLVM)", True)):
            with self.subTest(renderer=renderer), patch.dict(graphics.os.environ, {
                "LIBGL_ALWAYS_SOFTWARE": "1", "GALLIUM_DRIVER": "llvmpipe", "__GLX_VENDOR_LIBRARY_NAME": "nvidia",
                "__EGL_VENDOR_LIBRARY_FILENAMES": "/example/vendor.json", "TOKEN": "private",
            }), patch.object(graphics, "run_probe", return_value=ProbeResult(json.dumps({
                "renderer": renderer, "opengl_version": "4.6", "egl_vendor": "Example", "egl_version": "1.5", "client_api": "OpenGL",
            }))) as probe:
                result = graphics.inspect_graphics_context("offscreen", backend="egl", require_software=True, expect_renderer="LLVMPIPE")
            self.assertEqual(result["healthy"], expected)
            self.assertEqual(result["renderer_matches"], expected)
            self.assertTrue(result["context_created"])
            self.assertEqual(result["egl_vendor"], "Example")
            self.assertEqual(result["inherited_selectors"]["__GLX_VENDOR_LIBRARY_NAME"], "nvidia")
            self.assertNotIn("TOKEN", result["inherited_selectors"])
            self.assertEqual(probe.call_args.args[0][-1], "offscreen")
            self.assertIn("-I", probe.call_args.args[0])
            self.assertEqual(probe.call_args.kwargs["env"]["LIBGL_ALWAYS_SOFTWARE"], "1")
            self.assertFalse(result["rendering_verified"])
            self.assertFalse(result["hardware_profiling_qualified"])

    def test_egl_failures_and_invalid_requests_never_fall_back_to_glx(self):
        for response, issue in ((ProbeResult('{"issue":"egl_initialize_failed"}'), "egl_initialize_failed"),
                                (ProbeResult("invalid"), "egl_probe_invalid"),
                                (ProbeResult("{}"), "context_creation_unverified"),
                                (ProbeResult(issue="probe_timeout"), "probe_timeout")):
            with self.subTest(issue=issue), patch.object(graphics, "run_probe", return_value=response) as probe:
                result = graphics.inspect_graphics_context("surfaceless", backend="egl")
            self.assertFalse(result["healthy"])
            self.assertEqual(result["issues"], [issue])
            probe.assert_called_once()
        for context, backend, expectation in (("offscreen", "glx", None), ("xvfb", "egl", None), ("display", "glx", ""), ("display", "glx", "bad\n")):
            with self.subTest(context=context), patch.object(graphics, "run_probe") as probe, self.assertRaises(ValueError):
                graphics.inspect_graphics_context(context, backend=backend, expect_renderer=expectation)
            probe.assert_not_called()

    def test_selector_reporting_bounds_values_without_collecting_other_environment(self):
        self.assertEqual(graphics.graphics_environment({"DRI_PRIME": "x" * 2000, "EGL_PLATFORM": "bad\n", "SECRET": "no"}),
                         {"DRI_PRIME": "x" * 1024, "EGL_PLATFORM": "<invalid>"})


class EGLProbeTests(unittest.TestCase):
    def setUp(self):
        self.egl = MagicMock()
        self.enterContext(patch.object(egl_probe.C, "CDLL", return_value=self.egl))
        self.egl.eglGetDisplay.return_value = 11
        self.egl.eglCreatePbufferSurface.return_value = 22
        self.egl.eglCreateContext.return_value = 33
        self.egl.eglInitialize.side_effect = self.initialize
        self.egl.eglChooseConfig.side_effect = self.choose
        self.egl.eglGetProcAddress.return_value = 44
        self.egl.eglQueryString.side_effect = lambda display, key: b"EGL_MESA_platform_surfaceless EGL_EXT_platform_base" if key == 0x3055 else b"Example vendor"
        self.gl = lambda key: b"llvmpipe" if key == 0x1F01 else b"4.6 Mesa"
        self.enterContext(patch.object(egl_probe.C, "CFUNCTYPE", return_value=lambda address: self.gl))

    def initialize(self, display, major, minor):
        major._obj.value, minor._obj.value = 1, 5
        return 1

    def choose(self, display, attributes, config, limit, count):
        config._obj.value, count._obj.value = 55, 1
        self.assertEqual(list(attributes)[:4], [0x3033, 1, 0x3040, 8])
        return 1

    def test_pbuffer_queries_gl_renderer_and_releases_every_egl_object(self):
        result = egl_probe.probe_context("offscreen")
        self.assertEqual(result["renderer"], "llvmpipe")
        self.assertEqual(result["egl_version"], "1.5")
        self.egl.eglGetDisplay.assert_called_once_with(None)
        self.egl.eglBindAPI.assert_called_once_with(0x30A2)
        self.egl.eglMakeCurrent.assert_any_call(11, 22, 22, 33)
        self.egl.eglMakeCurrent.assert_called_with(11, None, None, None)
        self.egl.eglDestroyContext.assert_called_once_with(11, 33)
        self.egl.eglDestroySurface.assert_called_once_with(11, 22)
        self.egl.eglTerminate.assert_called_once_with(11)

    def test_context_failure_still_destroys_surface_and_terminates_display(self):
        self.egl.eglCreateContext.return_value = None
        with self.assertRaisesRegex(egl_probe.EGLFailure, "egl_context_creation_failed"):
            egl_probe.probe_context("offscreen")
        self.egl.eglDestroyContext.assert_not_called()
        self.egl.eglDestroySurface.assert_called_once_with(11, 22)
        self.egl.eglTerminate.assert_called_once_with(11)

    def test_explicit_surfaceless_needs_extension_and_never_uses_default_display(self):
        self.egl.eglQueryString.side_effect = None
        self.egl.eglQueryString.return_value = b"unrelated"
        with self.assertRaisesRegex(egl_probe.EGLFailure, "egl_surfaceless_unsupported"):
            egl_probe.probe_context("surfaceless")
        self.egl.eglGetDisplay.assert_not_called()
        platform = MagicMock(return_value=11)
        self.egl.eglQueryString.return_value = b"EGL_MESA_platform_surfaceless EGL_EXT_platform_base"
        with patch.object(egl_probe.C, "CFUNCTYPE", side_effect=[lambda address: platform, lambda address: self.gl]):
            result = egl_probe.probe_context("surfaceless")
        self.assertEqual(result["renderer"], "llvmpipe")
        platform.assert_called_once_with(0x31DD, None, None)
        self.egl.eglGetDisplay.assert_not_called()
