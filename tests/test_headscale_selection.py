"""Client bindings survive reference renames without silently moving networks."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lib.headscale_registry import HeadscaleServer, mutate_servers
from lib.headscale_selection import resolve_client_server


class HeadscaleSelectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = temporary.name
        Path(self.workspace).chmod(0o700)
        self.server = HeadscaleServer(
            "e23c52b1-1e80-4f91-9a68-f15c530c1d21", "Office",
            "https://vpn.example.com", "external", "https://join.example.com",
        )
        mutate_servers(self.workspace, lambda records: ([self.server], None))

    def test_new_selection_is_exact_case_insensitive_and_read_only(self):
        before = (Path(self.workspace) / "headscale-servers.json").read_bytes()
        with patch("lib.headscale_registry.resource_lock", side_effect=AssertionError("mutation")):
            selected = resolve_client_server("office", workspace=self.workspace)
        self.assertEqual(self.server, selected)
        self.assertEqual(before, (Path(self.workspace) / "headscale-servers.json").read_bytes())
        with self.assertRaises(ValueError):
            resolve_client_server("off", workspace=self.workspace)

    def test_saved_identity_follows_a_rename(self):
        renamed = replace(self.server, name="Home")
        mutate_servers(self.workspace, lambda records: ([renamed], None))
        selected = resolve_client_server(
            "Office", server_id=self.server.id, endpoint=self.server.url,
            workspace=self.workspace,
        )
        self.assertEqual(renamed, selected)

    def test_changed_endpoint_and_explicit_other_selection_are_refused(self):
        other = replace(self.server, id="092f1108-5af3-49f1-9c38-434b3d3b355c",
                        name="Other", url="https://other.example.com")
        mutate_servers(self.workspace, lambda records: ([self.server, other], None))
        with self.assertRaisesRegex(ValueError, "network change"):
            resolve_client_server(
                "Other", server_id=self.server.id, endpoint=self.server.url,
                reference_is_explicit=True, workspace=self.workspace,
            )
        changed = replace(self.server, url="https://replacement.example.com")
        mutate_servers(self.workspace, lambda records: ([changed, other], None))
        with self.assertRaisesRegex(ValueError, "endpoint changed"):
            resolve_client_server(
                "Office", server_id=self.server.id, endpoint=self.server.url,
                workspace=self.workspace,
            )

    def test_incomplete_binding_or_removed_reference_never_falls_back(self):
        with self.assertRaisesRegex(ValueError, "missing its endpoint"):
            resolve_client_server("Office", server_id=self.server.id, workspace=self.workspace)
        mutate_servers(self.workspace, lambda records: ([], None))
        with self.assertRaises(ValueError):
            resolve_client_server(
                "Office", server_id=self.server.id, endpoint=self.server.url,
                workspace=self.workspace,
            )
