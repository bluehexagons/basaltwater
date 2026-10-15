"""Opt-in Firefox MCP registration and truthful configuration discovery."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lib import agent_browser, browser_registration


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
