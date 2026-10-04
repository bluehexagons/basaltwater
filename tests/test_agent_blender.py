"""Blender checks use mocked processes and temporary evidence directories."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import zlib

from lib import agent_blender, agent_cli, agent_workspace


class BlenderSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.output = self.home / "artifacts with spaces"
        self.enterContext(patch.object(agent_blender, "is_dry_run", return_value=False))
        self.which = self.enterContext(patch.object(agent_blender.shutil, "which", return_value="/usr/bin/blender"))
        self.run = self.enterContext(patch.object(agent_blender, "run", side_effect=self.render))
        self.enterContext(patch.object(agent_workspace, "_effective_home", return_value=str(self.home)))

    def test_fixture_saves_uncompressed_regardless_of_blender_defaults(self) -> None:
        bpy = MagicMock()
        bpy.app.version_string = "5.2.2"
        bpy.context.scene.name = "Scene"
        bpy.context.scene.frame_current = 1
        self.output.mkdir()
        with (
            patch.dict("sys.modules", {"bpy": bpy, "mathutils": MagicMock()}),
            patch.object(agent_blender.blender_smoke_scene.sys, "argv", ["blender", "--", str(self.output)]),
        ):
            agent_blender.blender_smoke_scene.main()
        bpy.ops.wm.save_as_mainfile.assert_called_once_with(
            filepath=str(self.output / "scene.blend"), compress=False,
        )
        self.assertTrue(json.loads((self.output / "settings.json").read_text())["render_completed"])

    def render(self, command: list[str], **options) -> subprocess.CompletedProcess:
        directory = Path(command[-1])
        options["stdout"].write("Blender 4.3.2\nSaved render.png\n")
        settings = {
            "blender_version": "4.3.2", "engine": "CYCLES", "device": "CPU",
            "frame": 1, "width": 128, "height": 128, "samples": 8, "seed": 0,
            "threads": 2, "denoising": False, "render_completed": True,
        }
        (directory / "settings.json").write_text(json.dumps(settings))
        (directory / "scene.blend").write_bytes(b"BLENDER-v403fixture")

        def chunk(name: bytes, data: bytes) -> bytes:
            return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data))

        png = b"\x89PNG\r\n\x1a\n"
        png += chunk(b"IHDR", struct.pack(">IIBBBBB", 128, 128, 8, 6, 0, 0, 0))
        png += chunk(b"IDAT", zlib.compress((b"\0" + b"\x40\x80\x80\xff" * 128) * 128))
        png += chunk(b"IEND", b"")
        (directory / "render.png").write_bytes(png)
        return subprocess.CompletedProcess(command, 0)

    def test_success_retains_evidence_and_isolates_user_preferences(self) -> None:
        with patch.dict(os.environ, {
            "DISPLAY": ":human", "PYTHONPATH": "/user/modules",
            "BLENDER_USER_CONFIG": "/user/config", "BLENDER_SYSTEM_SCRIPTS": "/user/addons",
        }):
            result = agent_blender.smoke_blender(str(self.output), timeout=25)
        self.assertTrue(result["ok"])
        self.assertEqual(json.loads((self.output / "report.json").read_text()), result)
        self.assertEqual(result["gpu_readiness"], "unverified")
        self.assertEqual(result["ui_readiness"], "unverified")
        self.assertEqual(result["image"]["width"], 128)
        self.assertIn("Saved render.png", (self.output / "blender.log").read_text())
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.output / "report.json").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.output / "blender.log").stat().st_mode), 0o600)
        command = self.run.call_args.args[0]
        options = self.run.call_args.kwargs
        self.assertEqual(command[-2:], ["--", str(self.output)])
        self.assertLess(command.index("--python-exit-code"), command.index("--python"))
        self.assertIn("--factory-startup", command)
        self.assertIn("--disable-autoexec", command)
        self.assertEqual(options["timeout"], 25)
        self.assertEqual(options["cwd"], str(self.output))
        self.assertEqual(options["input_data"], "")
        self.assertEqual(options["stderr"], subprocess.STDOUT)
        environment = options["env"]
        for key in ("DISPLAY", "PYTHONPATH", "BLENDER_SYSTEM_SCRIPTS"):
            self.assertNotIn(key, environment)
        for key in ("BLENDER_USER_CONFIG", "BLENDER_USER_SCRIPTS", "BLENDER_USER_DATAFILES", "XDG_CACHE_HOME", "TMPDIR"):
            self.assertEqual(Path(environment[key]).parent, self.output)
            self.assertTrue(Path(environment[key]).is_dir())

    def test_default_runs_use_distinct_private_directories(self) -> None:
        first = agent_blender.smoke_blender()
        second = agent_blender.smoke_blender()
        self.assertTrue(first["ok"] and second["ok"])
        self.assertNotEqual(first["directory"], second["directory"])
        self.assertEqual(Path(first["directory"]).parent, self.home / ".local/state/basaltwater/blender")

    def test_nonzero_exit_fails_even_if_artifacts_exist(self) -> None:
        def fail(command, **options):
            self.render(command, **options)
            return subprocess.CompletedProcess(command, 1)
        self.run.side_effect = fail
        result = agent_blender.smoke_blender(str(self.output))
        self.assertFalse(result["ok"])
        self.assertEqual(result["returncode"], 1)
        self.assertTrue((self.output / "scene.blend").exists())
        self.assertIn("status 1", result["error"])

    def test_timeout_and_launch_errors_retain_failure_reports(self) -> None:
        for index, error in enumerate((TimeoutError(), OSError("cannot execute"), KeyboardInterrupt())):
            with self.subTest(error=error):
                self.run.side_effect = error
                directory = self.home / str(index)
                result = agent_blender.smoke_blender(str(directory), timeout=1)
                self.assertFalse(result["ok"])
                self.assertIsNone(result["returncode"])
                self.assertTrue((directory / "report.json").exists())
                self.assertTrue((directory / "blender.log").exists())
                if isinstance(error, TimeoutError):
                    self.assertIn("timed out", result["error"])

    def test_missing_corrupt_or_unexpected_artifacts_fail_successful_process(self) -> None:
        def render_and_break(command, **options):
            result = self.render(command, **options)
            directory = Path(command[-1])
            if kind == "missing":
                (directory / "render.png").unlink()
            elif kind == "corrupt":
                (directory / "render.png").write_bytes(b"not png")
            elif kind == "settings":
                (directory / "settings.json").write_text('{"engine":"GPU"}')
            elif kind == "scene":
                (directory / "scene.blend").write_bytes(b"not blend")
            else:
                (directory / "render.png").unlink()
                (directory / "render.png").symlink_to(self.home / "outside.png")
            return result
        self.run.side_effect = render_and_break
        (self.home / "outside.png").write_bytes(b"private contents")
        for kind in ("missing", "corrupt", "settings", "scene", "symlink"):
            with self.subTest(kind=kind):
                result = agent_blender.smoke_blender(str(self.home / kind))
                self.assertFalse(result["ok"])
                self.assertEqual(result["returncode"], 0)

    def test_absent_blender_and_invalid_inputs_do_not_launch(self) -> None:
        self.which.return_value = None
        with self.assertRaisesRegex(RuntimeError, "--blender"):
            agent_blender.smoke_blender(str(self.output))
        self.assertFalse(self.output.exists())
        self.which.return_value = "/usr/bin/blender"
        for timeout in (0, 601, True, 1.5):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                agent_blender.smoke_blender(str(self.output), timeout=timeout)
        with self.assertRaises(ValueError):
            agent_blender.smoke_blender(str(self.home / "bad\npath"))
        with patch.object(agent_blender, "is_dry_run", return_value=True):
            with self.assertRaisesRegex(ValueError, "dry-run"):
                agent_blender.smoke_blender(str(self.output))
        self.run.assert_not_called()

    def test_existing_destinations_are_preserved(self) -> None:
        for kind in ("file", "directory", "symlink"):
            destination = self.home / kind
            if kind == "file":
                destination.write_text("keep")
            elif kind == "directory":
                destination.mkdir()
                (destination / "keep").write_text("keep")
            else:
                destination.symlink_to(self.home / "missing")
            with self.subTest(kind=kind), self.assertRaises(FileExistsError):
                agent_blender.smoke_blender(str(destination))
        self.assertEqual((self.home / "file").read_text(), "keep")
        self.assertEqual((self.home / "directory/keep").read_text(), "keep")
        self.assertTrue((self.home / "symlink").is_symlink())
        self.run.assert_not_called()

    def test_command_dispatch_and_failure_json(self) -> None:
        parser = argparse.ArgumentParser()
        agent_cli.add_agent_subparser(parser.add_subparsers(dest="command"))
        args = parser.parse_args(["agent", "blender", "smoke", "--output", str(self.output), "--json"])
        with redirect_stdout(output := StringIO()):
            self.assertEqual(agent_cli.run_agent_command(args), 0)
        self.assertTrue(json.loads(output.getvalue())["ok"])
        args.timeout = 601
        with redirect_stdout(output := StringIO()):
            self.assertEqual(agent_cli.run_agent_command(args), 1)
        self.assertIn("600", json.loads(output.getvalue())["error"])


if __name__ == "__main__":
    unittest.main()
