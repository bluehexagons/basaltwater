"""Opt-in Firefox MCP registration and truthful configuration discovery."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from lib import agent_browser, agent_cli, browser_registration


class BrowserRegistrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.dict(os.environ, {"HOME": str(self.home)}))

    def test_opencode_register_keeps_chromium_and_unrelated_jsonc_settings(self) -> None:
        directory = self.home / ".config/opencode"
        directory.mkdir(parents=True)
        path = directory / "opencode.jsonc"
        path.write_text('{// user config\n"theme":"system","mcp":{"other":{"enabled":true}}}')
        browser_registration.register_browser_agent("chromium", "opencode")
        browser_registration.register_browser_agent("firefox", "opencode")
        value = json.loads(path.read_text())
        self.assertEqual(value["theme"], "system")
        self.assertTrue(value["mcp"]["other"]["enabled"])
        self.assertEqual(value["mcp"]["basaltwater-playwright"]["command"], [browser_registration.PLAYWRIGHT_MCP_WRAPPER])
        self.assertEqual(value["mcp"]["basaltwater-playwright-firefox"]["command"],
                         [browser_registration.PLAYWRIGHT_MCP_WRAPPER, "--engine", "firefox"])
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertTrue(browser_registration.browser_registrations("firefox")["opencode"])

    def test_codex_registration_requires_correct_engine_and_enabled_entry(self) -> None:
        directory = self.home / ".codex"
        directory.mkdir()
        path = directory / "config.toml"
        for args, enabled, expected in (([], True, False), (["--engine", "firefox"], False, False),
                                       (["--engine", "firefox"], True, True)):
            path.write_text('[mcp_servers.basaltwater-playwright-firefox]\n'
                            f'command = {json.dumps(browser_registration.PLAYWRIGHT_MCP_WRAPPER)}\n'
                            f'args = {json.dumps(args)}\nenabled = {str(enabled).lower()}\n')
            self.assertEqual(browser_registration.browser_registrations("firefox")["codex"], expected)
            self.assertFalse(browser_registration.browser_registrations("chromium")["codex"])

    def test_codex_add_uses_distinct_name_and_explicit_command_without_shell(self) -> None:
        with patch.object(browser_registration.shutil, "which", return_value="/tools/codex"), patch.object(browser_registration, "run") as run:
            browser_registration.register_browser_agent("firefox", "codex")
        self.assertEqual(run.call_args.args[0], ["/tools/codex", "mcp", "add", "basaltwater-playwright-firefox", "--",
                                               browser_registration.PLAYWRIGHT_MCP_WRAPPER, "--engine", "firefox"])
        self.assertEqual(run.call_args.kwargs["env"]["CODEX_HOME"], str(self.home / ".codex"))

    def test_chromium_doctors_agree_on_disabled_wrong_engine_and_malformed_configs(self) -> None:
        directory = self.home / ".codex"
        directory.mkdir()
        path = directory / "config.toml"
        for content, expected in (
            ('enabled = false', False),
            ('args = ["--engine", "firefox"]', False),
            ('args = []\nenabled = true', True),
            ('args = [', False),
        ):
            with self.subTest(content=content):
                path.write_text('[mcp_servers."basaltwater-playwright"]\n'
                                f'command = {json.dumps(browser_registration.PLAYWRIGHT_MCP_WRAPPER)}\n{content}\n')
                self.assertEqual(browser_registration.browser_registrations("chromium")["codex"], expected)
                self.assertEqual(agent_cli._codex_browser_registration(str(self.home)), expected)

    def test_registration_status_retains_unknown_unless_a_client_is_registered(self) -> None:
        for values, expected in (({"codex": None, "opencode": False}, None),
                                 ({"codex": None, "opencode": True}, True),
                                 ({"codex": False, "opencode": False}, False)):
            self.assertIs(browser_registration.registration_status(values), expected)

    def test_missing_toml_parser_is_unknown_in_both_doctors(self) -> None:
        with patch.dict(sys.modules, {"tomllib": None}), \
                patch.object(agent_cli, "_tool_path", side_effect=lambda tool, _: "/tools/codex" if tool == "codex" else None), \
                patch.object(agent_cli, "_browser_running_processes", return_value={"total": 0, "stale": 0}), \
                patch.object(agent_cli, "_browser_launchers_secure", return_value=True), \
                patch.object(agent_cli, "_browser_launcher_features", return_value={"browser_selection": True}), \
                patch.object(agent_cli, "_browser_workflow_skills", return_value=("basaltwater-playwright-testing",)), \
                patch.object(agent_cli.os.path, "isfile", return_value=True), patch.object(agent_cli.os, "access", return_value=True):
            self.assertIsNone(browser_registration.browser_registrations("chromium")["codex"])
            result = agent_cli.inspect_browser_automation(str(self.home), run_smoke=False)
        self.assertIsNone(result["registrations"]["codex"])
        self.assertIsNone(result["mcp_registered"])
        self.assertFalse(result["configured"])
        self.assertEqual(result["issues"], ["registration_unverified"])
        self.assertEqual(result["remediation"], "use_python_with_toml_support")

    def test_registration_failure_preserves_successful_runtime_diagnostics(self) -> None:
        args = argparse.Namespace(engine="firefox", context="managed", project=None,
                                  browser_command="register", agent_tool="codex", json=True)
        checked = {"ok": True, "healthy": True, "engine": "firefox", "context": "managed-mcp",
                   "rendering_verified": True, "browser_version": "153.0", "mcp_registered": False}
        for secure in (False, None):
            with self.subTest(secure=secure), patch.object(agent_browser, "check_browser", return_value=checked) as check, \
                    patch.object(agent_cli, "_browser_launchers_secure", return_value=secure), \
                    patch.object(agent_browser, "register_browser_agent") as register, redirect_stdout(output := StringIO()):
                self.assertEqual(agent_browser.run_browser_command(args), 1)
            result = json.loads(output.getvalue())
            self.assertEqual(result["issue"], "browser_registration_failed")
            self.assertEqual(result["engine"], "firefox")
            self.assertEqual(result["context"], "managed-mcp")
            self.assertTrue(result["healthy"])
            self.assertTrue(result["rendering_verified"])
            self.assertFalse(result["mcp_registered"])
            if secure is None:
                self.assertIn("security is unverified", result["error"])
                self.assertNotIn("rerun", result["error"])
            check.assert_called_once_with("firefox", "managed-mcp", None, install=False)
            register.assert_not_called()

    def test_malformed_and_linked_opencode_configs_are_preserved(self) -> None:
        directory = self.home / ".config/opencode"
        directory.mkdir(parents=True)
        path = directory / "opencode.json"
        path.write_text('{"mcp":42}')
        with self.assertRaises(ValueError):
            browser_registration.register_browser_agent("firefox", "opencode")
        self.assertEqual(path.read_text(), '{"mcp":42}')
        path.unlink()
        private = self.home / "private.json"
        private.write_text('{}')
        path.symlink_to(private)
        with self.assertRaises(OSError):
            browser_registration.register_browser_agent("firefox", "opencode")
        self.assertEqual(private.read_text(), '{}')

    def test_cli_never_registers_failed_or_project_runtime(self) -> None:
        args = argparse.Namespace(engine="firefox", context="managed", project=None,
                                  browser_command="register", agent_tool="codex", json=True)
        with patch.object(agent_browser, "register_browser_agent") as register, redirect_stdout(StringIO()):
            with patch.object(agent_browser, "check_browser", return_value={"ok": False}):
                self.assertEqual(agent_browser.run_browser_command(args), 1)
            args.context = "project"
            self.assertEqual(agent_browser.run_browser_command(args), 1)
        register.assert_not_called()


if __name__ == "__main__":
    unittest.main()
