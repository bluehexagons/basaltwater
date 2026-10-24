"""Private desktop observation output and CLI compatibility checks."""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from desktop.observations import save_observation
from lib.desktop_cli import add_desktop_subparser, run_desktop_command


class DesktopOutputTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name) / "observation"

        def create_directory(**kwargs):
            self.directory.mkdir(mode=0o700)
            return str(self.directory)

        self.addCleanup(patch.stopall)
        patch("desktop.observations.tempfile.mkdtemp", side_effect=create_directory).start()

    def observation(self) -> dict:
        return {"generation": "generation-1", "pid": 123, "root": "root-ref",
                "truncated": True, "elements": [
                    {"ref": f"ref-{index}", "name": "n" * 256, "role": "button",
                     "text": "private document content" * 10, "states": ["showing"]}
                    for index in range(128)]}

    def test_full_payload_is_private_and_preview_is_bounded(self) -> None:
        result = self.observation()
        summary = save_observation(result)
        artifact = Path(summary["artifact"])
        self.assertEqual(json.loads(artifact.read_text()), result)
        self.assertEqual(summary["bytes"], artifact.stat().st_size)
        self.assertEqual(artifact.stat().st_mode & 0o777, 0o600)
        self.assertEqual(artifact.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(summary["counts"], {"elements": 128})
        self.assertEqual(len(summary["preview"]["elements"]), 3)
        self.assertTrue(summary["truncated"])
        self.assertEqual(summary["root"], "root-ref")
        self.assertEqual(summary["generation"], "generation-1")
        self.assertLess(len(json.dumps(summary)), 2500)
        self.assertNotIn("private document", json.dumps(summary))
        self.assertEqual(len(result["elements"][0]["name"]), 256)

    def test_incomplete_artifact_is_removed_on_serialization_failure(self) -> None:
        with self.assertRaises(TypeError):
            save_observation({"elements": [object()]})
        self.assertFalse(self.directory.exists())

    def arguments(self, *argv):
        parser = argparse.ArgumentParser()
        add_desktop_subparser(parser.add_subparsers())
        return parser.parse_args(["desktop", *argv])

    def run_cli(self, result, *argv, mode="enabled"):
        with (patch("lib.desktop_cli.runtime.status", return_value={"state": "running", "generation": "generation-1", "control_revision": "r",
                                                                   "agent_control": mode, "paused": mode != "enabled"}),
              patch("lib.desktop_cli.runtime.request", return_value=result) as request,
              patch("sys.stdout", new_callable=io.StringIO) as output):
            code = run_desktop_command(self.arguments(*argv))
        return code, json.loads(output.getvalue()), request

    def test_default_inspection_keeps_legacy_payload(self) -> None:
        result = self.observation()
        for mode in ("enabled", "human-override", "disabled"):
            with self.subTest(mode=mode):
                code, output, request = self.run_cli(result, "inspect", "--pid", "123", mode=mode)
                self.assertEqual(code, 0)
                self.assertEqual(output, result)
                request.assert_called_once()
                self.assertEqual(request.call_args.args[0]["action"], "inspect")
                self.assertFalse(self.directory.exists())

    def test_save_output_preserves_query_and_failure_status(self) -> None:
        result = {**self.observation(), "error": "Timed out waiting for one complete match"}
        code, output, request = self.run_cli(result, "inspect", "--pid", "123", "--root", "root-ref",
                                             "--generation", "generation-1", "--save-output")
        self.assertEqual(code, 1)
        self.assertEqual(output["error"], result["error"])
        self.assertEqual(json.loads(Path(output["artifact"]).read_text()), result)
        self.assertEqual(request.call_args.args[0]["root"], "root-ref")
        self.assertEqual(request.call_args.args[0]["generation"], "generation-1")

    def test_windows_and_both_wait_commands_accept_saved_output(self) -> None:
        result = {"generation": "generation-1", "windows": [{"id": "42", "title": "App"}],
                  "coverage": "partial", "truncated": True}
        code, output, _ = self.run_cli(result, "windows", "--save-output")
        self.assertEqual(code, 0)
        self.assertEqual(output["counts"], {"windows": 1})
        self.assertEqual(output["coverage"], "partial")
        self.assertTrue(output["truncated"])
        self.assertTrue(self.arguments("wait", "--title", "App", "--save-output").save_output)
        self.assertTrue(self.arguments("wait-element", "--pid", "123", "--role", "button",
                                       "--generation", "generation-1", "--save-output").save_output)


if __name__ == "__main__":
    unittest.main()
