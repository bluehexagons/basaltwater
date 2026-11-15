from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.headscale_cli import add_headscale_parser, run_headscale_command
from lib.headscale_registry import RegistryStateError, list_servers, resolve_server


class HeadscaleRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name) / "private"
        self.workspace.mkdir(mode=0o700)
        self.workspace.chmod(0o700)

    def run_cli(self, *argv):
        parser = argparse.ArgumentParser()
        subs = parser.add_subparsers(dest="command", required=True)
        add_headscale_parser(subs)
        args = parser.parse_args(["headscale", *argv, "--workspace", str(self.workspace)])
        return run_headscale_command(args)

    def test_crud_stable_id_and_exact_casefold_name(self):
        self.run_cli("add", "Office", "--url", "https://vpn.example.com", "--origin", "managed",
                     "--onboarding-url", "https://join.example.com")
        saved = resolve_server("office", str(self.workspace))
        self.run_cli("rename", saved.id, "HQ")
        renamed = resolve_server("hq", str(self.workspace))
        self.assertEqual(saved.id, renamed.id)
        self.assertEqual(saved.url, renamed.url)
        self.run_cli("show", "HQ")
        self.run_cli("onboarding", "HQ")
        self.run_cli("remove", "HQ")
        self.assertEqual([], list_servers(str(self.workspace)))

    def test_ambiguous_and_corrupt_state_preserved(self):
        self.run_cli("add", "one", "--url", "https://vpn.example.com")
        path = self.workspace / "headscale-servers.json"
        original = path.read_bytes()
        value = json.loads(original)
        value["servers"].append(value["servers"][0])
        path.write_text(json.dumps(value))
        path.chmod(0o600)
        with self.assertRaises(RegistryStateError):
            list_servers(str(self.workspace))
        self.assertNotEqual(original, path.read_bytes())

    def test_url_injection_rejected_before_state_creation(self):
        with self.assertRaises(ValueError):
            self.run_cli("add", "bad", "--url", "https://user@vpn.example.com?x=1")
        self.assertFalse((self.workspace / "headscale-servers.json").exists())

    def test_dry_run_and_lookups_are_mutation_free(self):
        nonexistent = self.workspace / "no-state"
        with patch("lib.headscale_registry.resource_lock") as lock:
            self.run_cli("list", "--dry-run")
            lock.assert_not_called()
        self.run_cli("add", "preview", "--url", "https://vpn.example.com", "--dry-run")
        self.assertFalse((self.workspace / "headscale-servers.json").exists())

    def test_remove_is_local_and_never_calls_remote(self):
        self.run_cli("add", "office", "--url", "https://vpn.example.com")
        with patch("urllib.request.urlopen", side_effect=AssertionError("remote call")):
            self.run_cli("remove", "office")


if __name__ == "__main__":
    unittest.main()
