from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import basaltwater
from lib.headscale_cli import _complete_server_names, add_headscale_parser, run_headscale_command
from lib.headscale_registry import list_servers, resolve_server


class HeadscaleCliTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name) / "private"
        self.workspace.mkdir(mode=0o700)
        self.workspace.chmod(0o700)

    def parse(self, *argv):
        parser, _, _ = basaltwater.create_basaltwater_parser()
        return parser.parse_args(argv)

    def invoke(self, *argv):
        output = io.StringIO()
        with patch("sys.argv", ["basaltw", *argv]), redirect_stdout(output):
            code = basaltwater.main()
        return code, output.getvalue()

    def test_root_parser_help_and_workspace_placement(self):
        parser, _, _ = basaltwater.create_basaltwater_parser()
        self.assertIn("headscale", parser.format_help())
        for argv in (
            ("--workspace", str(self.workspace), "headscale", "list"),
            ("headscale", "list", "--workspace", str(self.workspace)),
        ):
            args = self.parse(*argv)
            self.assertEqual(str(self.workspace), args.workspace)

    def test_public_dispatch_add_list_show_rename_onboarding_remove(self):
        ws = str(self.workspace)
        code, _ = self.invoke("--workspace", ws, "headscale", "add", "Team Space",
                              "--url", "https://vpn.example.com", "--onboarding-url",
                              "https://join.example.com")
        self.assertEqual(0, code)
        saved = resolve_server("team space", ws)
        self.assertEqual("Team Space", saved.name)
        self.assertEqual(0, self.invoke("headscale", "list", "--workspace", ws)[0])
        self.assertEqual(0, self.invoke("headscale", "show", "team space", "--workspace", ws)[0])
        self.assertEqual(0, self.invoke("headscale", "onboarding", "Team Space", "--workspace", ws)[0])
        self.assertEqual(0, self.invoke("headscale", "rename", "TEAM SPACE", "HQ", "--workspace", ws)[0])
        self.assertEqual(saved.id, resolve_server("hq", ws).id)
        self.assertEqual(0, self.invoke("headscale", "remove", "HQ", "--workspace", ws)[0])
        self.assertEqual([], list_servers(ws))

    def test_failures_return_one_without_traceback(self):
        code, output = self.invoke("headscale", "show", "missing", "--workspace", str(self.workspace))
        self.assertEqual(1, code)
        self.assertIn("Error:", output)
        self.assertNotIn("Traceback", output)

    def test_completion_is_casefolded_read_only_and_preserves_spaces(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers()
        add_headscale_parser(subparsers)
        args = parser.parse_args(["headscale", "list", "--workspace", str(self.workspace)])
        args.headscale_action = "add"
        args.name = "A Space"
        args.url = "https://vpn.example.com"
        args.origin = "external"
        args.onboarding_url = None
        args.dry_run = False
        run_headscale_command(args)
        path = self.workspace / "headscale-servers.json"
        before = path.read_bytes()
        self.assertEqual(["A Space"], _complete_server_names("a s", args))
        self.assertEqual(before, path.read_bytes())
        self.assertFalse((self.workspace / "missing").exists())


if __name__ == "__main__":
    unittest.main()
