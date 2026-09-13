"""Final audit review: service rollback and local diagnostic boundaries."""

from __future__ import annotations

from contextlib import ExitStack
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
import urllib.error

from game import antistatic_steps as antistatic
from lib import agent_cli


class TestAuditReviewBoundaries(unittest.TestCase):
    def test_antistatic_unit_failure_stops_before_proxy_and_firewall_changes(self):
        for database in (False, True):
            with self.subTest(database=database), ExitStack() as stack:
                for name in ("_ensure_antistatic_dependencies", "_ensure_antistatic_user",
                             "_ensure_antistatic_db_user", "_download_antistatic_binary",
                             "_download_antistatic_db_binary", "_configure_antistatic_environment",
                             "_detect_arch"):
                    stack.enter_context(patch.object(antistatic, name))
                stack.enter_context(patch("web.web_steps.install_nginx"))
                proxy = stack.enter_context(patch.object(antistatic, "_maybe_configure_nginx_proxy"))
                firewall = stack.enter_context(patch.object(antistatic, "_maybe_configure_antistatic_firewall"))
                db_firewall = stack.enter_context(patch.object(antistatic, "_maybe_configure_direct_port_firewall"))
                replace = stack.enter_context(patch.object(antistatic, "replace_units", side_effect=RuntimeError("activation failed")))
                config = SimpleNamespace(antistatic_server=":8080", antistatic_db=":8081", enable_cloudflare=False)
                setup = antistatic.setup_antistatic_db if database else antistatic.setup_antistatic_server
                with self.assertRaisesRegex(RuntimeError, "activation failed"):
                    setup(config)
                unit = "antistatic-db.service" if database else "antistatic.service"
                self.assertEqual(replace.call_args.kwargs["activate"], (unit,))
                self.assertIn("[Service]", replace.call_args.args[0][unit])
                proxy.assert_not_called()
                firewall.assert_not_called()
                db_firewall.assert_not_called()

    def test_skill_fifo_is_rejected_without_blocking(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "SKILL.md"
            os.mkfifo(path)
            self.assertFalse(agent_cli._managed_agent_skill_ready(str(path), os.getuid()))

    def test_t3_probe_uses_loopback_client_and_rejects_redirects(self):
        response = MagicMock()
        response.__enter__.return_value.status = 200
        with patch.object(agent_cli, "open_loopback", return_value=response) as probe:
            self.assertTrue(agent_cli._t3_endpoint_reachable(8080))
            probe.assert_called_once_with("http://127.0.0.1:8080/", timeout=5)
        for code, expected in ((302, False), (401, True), (503, False)):
            with patch.object(agent_cli, "open_loopback", side_effect=urllib.error.HTTPError(
                "http://127.0.0.1:8080/", code, "status", {}, None,
            )):
                self.assertEqual(agent_cli._t3_endpoint_reachable(8080), expected)
