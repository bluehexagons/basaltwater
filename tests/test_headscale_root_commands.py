"""Root parser and controller boundaries, using only private temporary state."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import basaltwater


class RootHeadscaleTests(unittest.TestCase):
    def main(self, *arguments):
        with patch.object(sys, "argv", ["basaltw", *arguments]), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return basaltwater.main()

    def test_root_dispatch_and_nested_workspace_override(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ):
            root = str(Path(temporary) / "root")
            local = str(Path(temporary) / "local")
            with patch.object(basaltwater, "run_headscale_management_command", return_value=7) as handler:
                self.assertEqual(self.main("--workspace", root, "headscale-manage", "show", "office"), 7)
                self.assertEqual(handler.call_args.args[0].workspace, root)
                self.assertEqual(os.environ["BASALTWATER_WORKSPACE"], root)
                self.assertEqual(self.main("--workspace", root, "headscale-manage", "show", "office", "--workspace", local), 7)
                self.assertEqual(handler.call_args.args[0].workspace, local)
            with patch.object(basaltwater, "run_headscale_dns_command", return_value=8) as handler:
                self.assertEqual(self.main("headscale-dns", "inspect", "--dry-run"), 8)
                self.assertTrue(handler.call_args.args[0].dry_run)
            self.assertFalse(Path(root).exists())
            self.assertFalse(Path(local).exists())

    def test_readonly_failure_creates_no_workspace(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ), patch("subprocess.run", side_effect=AssertionError("native call")):
            workspace = Path(temporary) / "absent"
            self.assertEqual(self.main("--workspace", str(workspace), "headscale-manage", "show", "missing"), 1)
            self.assertFalse(workspace.exists())

    def test_management_dryrun_propagates_without_native_calls(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ), patch("subprocess.run", side_effect=AssertionError("native call")), patch("lib.headscale_management.remove_management") as remove:
            workspace = Path(temporary) / "absent"
            self.assertEqual(self.main("--workspace", str(workspace), "headscale-manage", "remove", "office", "--dry-run"), 0)
            remove.assert_called_once_with("office", dry_run=True, workspace=str(workspace))
            self.assertFalse(workspace.exists())

    def test_dns_private_input_remains_handler_policy(self):
        with tempfile.TemporaryDirectory() as temporary, patch("os.geteuid", return_value=0), patch("subprocess.run", side_effect=AssertionError("native call")):
            request = Path(temporary) / "request.json"
            request.write_text("{}")
            request.chmod(0o644)
            with patch("lib.headscale_dns_publish.publish_dns") as publish:
                self.assertEqual(self.main("headscale-dns", "publish", "--input", str(request), "--dry-run"), 1)
                publish.assert_not_called()
            self.assertEqual(self.main("headscale-dns", "inspect", "--input", str(request), "--dry-run"), 1)
            self.assertEqual(self.main("headscale-dns", "inspect", "--dry-run"), 0)
            with patch("os.geteuid", return_value=1000):
                self.assertEqual(self.main("headscale-dns", "inspect", "--dry-run"), 1)

    def test_setup_rejects_complete_topology_before_runtime_preparation(self):
        for flags in (
            ["--headscale-onboarding-domain", "join.example.com"],
            ["--headscale-onboarding-control-url", "https://vpn.example.net"],
            ["--headscale-onboarding-domain", "vpn.example.net", "--headscale-onboarding-control-url", "https://vpn.example.net"],
        ):
            with self.subTest(flags=flags), patch.object(basaltwater, "load_setup_command", return_value=None), patch.object(basaltwater, "prompt_for_missing_passwords"), patch.object(basaltwater, "prepare_runtime_config") as prepare, patch.object(basaltwater, "run_remote_setup") as remote:
                self.assertEqual(self.main("setup", "server_lite", "target.example.com", "agent", "--timezone", "UTC", "--dry-run", *flags), 1)
                prepare.assert_not_called()
                remote.assert_not_called()

    def test_setup_dryrun_transports_static_metadata_and_propagates_failure(self):
        with patch.object(basaltwater, "load_setup_command", return_value=None), patch.object(basaltwater, "prompt_for_missing_passwords"), patch.object(basaltwater, "prepare_runtime_config", side_effect=lambda config: config), patch.object(basaltwater, "run_remote_setup", return_value=1) as remote, patch.object(basaltwater, "save_setup_command") as save, patch("subprocess.run", side_effect=AssertionError("native call")):
            self.assertEqual(self.main("setup", "server_lite", "target.example.com", "agent", "--timezone", "UTC", "--dry-run",
                                       "--headscale-onboarding-domain", "join.example.com",
                                       "--headscale-onboarding-control-url", "https://vpn.example.net"), 1)
            config = remote.call_args.args[0]
            self.assertTrue(config.dry_run)
            self.assertEqual(config.headscale_onboarding_control_url, "https://vpn.example.net")
            for name in ("headscale", "tailscale", "unbound"):
                self.assertIsNone(getattr(config, name))
            save.assert_not_called()

    def test_parser_modules_import_without_runtime_dependencies(self):
        code = '''
import builtins
real_import = builtins.__import__
allowed = {"lib.headscale_management_cli", "lib.headscale_dns_cli"}
def guarded(name, *args, **kwargs):
    if name.startswith(("lib.", "common.")) and name not in allowed:
        raise AssertionError(name)
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded
from lib.headscale_management_cli import add_headscale_management_parser
from lib.headscale_dns_cli import add_headscale_dns_parser
import argparse
p = argparse.ArgumentParser()
s = p.add_subparsers()
add_headscale_management_parser(s)
add_headscale_dns_parser(s)
p.parse_args(["headscale-dns", "inspect", "--dry-run"])
'''
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
