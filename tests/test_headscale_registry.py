from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.headscale_cli import add_headscale_parser, run_headscale_command
from lib.headscale_registry import (
    HeadscaleServer, RegistryStateError, list_servers, mutate_servers, resolve_server,
)


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
        with redirect_stdout(io.StringIO()) as output:
            result = run_headscale_command(args)
        self.output = output.getvalue()
        return result

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

    def test_rename_second_record_reports_the_selected_record(self):
        self.run_cli("add", "first", "--url", "https://first.example.com")
        self.run_cli("add", "second", "--url", "https://second.example.com")
        self.run_cli("rename", "second", "renamed")
        self.assertEqual("Renamed second to renamed\n", self.output)
        self.assertEqual("first", list_servers(str(self.workspace))[0].name)

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
        corrupt = path.read_bytes()
        self.assertNotEqual(original, corrupt)
        with self.assertRaises(RegistryStateError):
            self.run_cli("add", "another", "--url", "https://other.example.com")
        self.assertEqual(corrupt, path.read_bytes())

    def test_invalid_schemas_records_and_unknown_versions_are_preserved(self):
        self.run_cli("add", "office", "--url", "https://vpn.example.com")
        path = self.workspace / "headscale-servers.json"
        original = json.loads(path.read_text())
        for field, value in (("id", []), ("id", 42), ("origin", []), ("name", "bad\n")):
            with self.subTest(field=field, value=value):
                candidate = json.loads(json.dumps(original))
                candidate["servers"][0][field] = value
                path.write_text(json.dumps(candidate))
                before = path.read_bytes()
                with self.assertRaises(RegistryStateError):
                    list_servers(str(self.workspace))
                self.assertEqual(before, path.read_bytes())
        original["schema_version"] = 2
        path.write_text(json.dumps(original))
        with self.assertRaises(RegistryStateError):
            list_servers(str(self.workspace))

    def test_unsafe_file_modes_links_and_workspace_are_refused(self):
        self.run_cli("add", "office", "--url", "https://vpn.example.com")
        path = self.workspace / "headscale-servers.json"
        path.chmod(0o644)
        with self.assertRaises(RegistryStateError):
            list_servers(str(self.workspace))
        path.chmod(0o600)
        linked = self.workspace / "linked.json"
        os.link(path, linked)
        with self.assertRaises(RegistryStateError):
            list_servers(str(self.workspace))
        linked.unlink()
        original = path.read_bytes()
        path.unlink()
        path.symlink_to(linked)
        with self.assertRaises(RegistryStateError):
            list_servers(str(self.workspace))
        path.unlink()
        path.write_bytes(original)
        path.chmod(0o600)
        self.workspace.chmod(0o755)
        with self.assertRaises(RegistryStateError):
            list_servers(str(self.workspace))

    def test_url_injection_rejected_before_state_creation(self):
        for url in ("http://vpn.example.com", "https://user@vpn.example.com?x=1",
                    "https://vpn.example.com/#secret", "https://vpn.example.com/$(id)",
                    "https://vpn.example.com/%2f", "https://vpn.example.com/../admin"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.run_cli("add", "bad", "--url", url)
        self.assertFalse((self.workspace / "headscale-servers.json").exists())

    def test_dry_run_and_lookups_are_mutation_free(self):
        nonexistent = self.workspace / "no-state"
        with patch("lib.headscale_registry.resource_lock") as lock:
            self.assertEqual([], list_servers(str(nonexistent)))
            self.run_cli("list", "--dry-run")
            self.run_cli("add", "preview", "--url", "https://vpn.example.com", "--dry-run")
            lock.assert_not_called()
        self.assertFalse(nonexistent.exists())
        self.assertFalse((self.workspace / "headscale-servers.json").exists())

    def test_mutation_revalidates_the_candidate_and_concurrent_selection(self):
        self.run_cli("add", "office", "--url", "https://vpn.example.com")
        path = self.workspace / "headscale-servers.json"
        original = path.read_bytes()
        selected = resolve_server("office", str(self.workspace))
        with self.assertRaises(ValueError):
            mutate_servers(str(self.workspace), lambda records: (records + records, None))
        with patch("lib.headscale_registry.MAX_REGISTRY_BYTES", len(original) + 1):
            with self.assertRaisesRegex(ValueError, "state limit"):
                self.run_cli("add", "second", "--url", "https://second.example.com")
        self.assertEqual(original, path.read_bytes())
        changed = HeadscaleServer(selected.id, "changed", selected.url, selected.origin)
        # Model a change between the initial read and lock acquisition. A stale
        # selection cannot rename or remove a record it did not inspect.
        def concurrent_mutation(workspace, operation):
            return operation([changed])
        for action in (("rename", "office", "new"), ("remove", "office")):
            with self.subTest(action=action), patch(
                "lib.headscale_cli.mutate_servers", side_effect=concurrent_mutation,
            ), self.assertRaisesRegex(ValueError, "changed"):
                self.run_cli(*action)
        self.assertEqual(original, path.read_bytes())

    def test_remove_is_local_and_never_calls_remote(self):
        self.run_cli("add", "office", "--url", "https://vpn.example.com")
        with patch("urllib.request.urlopen", side_effect=AssertionError("remote call")):
            self.run_cli("remove", "office")


if __name__ == "__main__":
    unittest.main()
