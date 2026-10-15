"""Context defaults preserve user choices and never touch live provider state."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import tomllib
import unittest
from unittest.mock import patch

from common.agent_context_steps import configure_agent_context_defaults
from common.agent_security_steps import _codex_config_content, _codex_requirements_content
from lib.config import SetupConfig
from plugins.common import extend_agent_steps, get_custom_step_functions
from plugins.wsl import build_wsl_steps


class AgentContextDefaultsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        account = SimpleNamespace(pw_dir=str(self.home), pw_uid=os.getuid(), pw_gid=os.getgid())
        lookup = patch("common.agent_context_steps.pwd.getpwnam", return_value=account)
        lookup.start()
        self.addCleanup(lookup.stop)

    def config(self, **kwargs):
        values = dict(host="testhost", username="agent", system_type="agent_vm")
        values.update(kwargs)
        return SetupConfig(**values)

    def write(self, relative, content):
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def test_codex_system_budget_applies_in_both_security_postures(self):
        for hardened in (False, True):
            with self.subTest(hardened=hardened):
                value = tomllib.loads(_codex_config_content(hardened=hardened))
                self.assertEqual(value["model_auto_compact_token_limit"], 240_000)
                self.assertEqual(value["model_auto_compact_token_limit_scope"], "total")
                self.assertNotIn("model_context_window", value)
        requirements = tomllib.loads(_codex_requirements_content())
        self.assertNotIn("model_auto_compact_token_limit", requirements)

    def test_vm_uses_system_codex_defaults_without_creating_personal_config(self):
        configure_agent_context_defaults(self.config(install_codex=True))
        self.assertFalse((self.home / ".codex").exists())

    def test_local_codex_prepend_preserves_comments_tables_and_is_idempotent(self):
        path = self.write(".codex/config.toml", '# personal comment\nmodel = "chosen-model"\n[mcp_servers.personal]\ncommand = "my-server"\n')
        config = self.config(system_type="server_wsl", machine_type="wsl", install_codex=True)
        configure_agent_context_defaults(config)
        first = path.read_bytes()
        value = tomllib.loads(first.decode())
        self.assertEqual(value["model"], "chosen-model")
        self.assertEqual(value["model_auto_compact_token_limit"], 240_000)
        self.assertEqual(value["mcp_servers"]["personal"]["command"], "my-server")
        self.assertIn(b"# personal comment", first)
        configure_agent_context_defaults(config)
        self.assertEqual(path.read_bytes(), first)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_explicit_local_codex_budget_and_scope_are_preserved_byte_for_byte(self):
        path = self.write(".codex/config.toml", 'model_auto_compact_token_limit = 800000\nmodel_auto_compact_token_limit_scope = "body_after_prefix"\n')
        previous = path.read_bytes()
        configure_agent_context_defaults(self.config(system_type="agent_cachyos", install_codex=True))
        self.assertEqual(path.read_bytes(), previous)

    def test_claude_has_earlier_haiku_budget_and_keeps_other_configuration(self):
        path = self.write(".claude/settings.json", '{"model": "sonnet", "permissions": {"allow": ["Read"]}, "env": {"EXISTING": "yes"}}')
        config = self.config(install_claude=True)
        configure_agent_context_defaults(config)
        value = json.loads(path.read_text())
        self.assertEqual(value["autoCompactWindow"], 200_000)
        self.assertEqual(value["modelSettings"]["claude-haiku-5-5"]["autoCompactWindow"], 100_000)
        self.assertEqual(value["env"]["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"], "80")
        self.assertEqual(value["env"]["EXISTING"], "yes")
        self.assertEqual(value["model"], "sonnet")
        self.assertEqual(value["permissions"], {"allow": ["Read"]})
        previous = path.read_bytes()
        configure_agent_context_defaults(config)
        self.assertEqual(path.read_bytes(), previous)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_claude_retains_explicit_global_and_per_model_choices(self):
        value = {"autoCompactWindow": 800_000, "modelSettings": {"claude-haiku-5-5": {"autoCompactWindow": 500_000}},
                 "env": {"CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "90"}}
        path = self.write(".claude/settings.json", json.dumps(value))
        previous = path.read_bytes()
        configure_agent_context_defaults(self.config(install_claude=True))
        self.assertEqual(path.read_bytes(), previous)

    def test_existing_claude_context_policy_is_not_changed_by_adding_other_defaults(self):
        for value in (
            {"autoCompactWindow": 800_000},
            {"modelSettings": {"claude-sonnet-5-5": {"autoCompactWindow": 800_000}}},
            {"env": {"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "800000"}},
            {"env": {"CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "90"}},
            {"env": {"DISABLE_AUTO_COMPACT": "1"}},
        ):
            with self.subTest(value=value):
                path = self.write(".claude/settings.json", json.dumps(value))
                previous = path.read_bytes()
                configure_agent_context_defaults(self.config(install_claude=True))
                self.assertEqual(path.read_bytes(), previous)

    def test_opencode_input_budgets_leave_native_capacity_and_flat_price_models_alone(self):
        configure_agent_context_defaults(self.config(install_opencode=True))
        value = json.loads((self.home / ".config/opencode/opencode.json").read_text())
        self.assertTrue(value["compaction"]["auto"])
        models = value["provider"]["openai"]["models"]
        self.assertEqual(set(models), {"gpt-6.1-sol", "gpt-6-astra", "gpt-6-luna"})
        for model in models.values():
            self.assertEqual(model["limit"], {"context": 1_050_000, "input": 240_000, "output": 128_000})
        self.assertEqual(value["provider"]["anthropic"]["models"], {
            "claude-haiku-5-5": {"limit": {"context": 1_000_000, "input": 80_000, "output": 128_000}},
        })
        self.assertNotIn("model", value)

    def test_opencode_jsonc_merges_without_changing_existing_limits_or_mcp(self):
        path = self.write(".config/opencode/opencode.jsonc", '''{
          // Existing settings may contain JSONC comments and trailing commas.
          "compaction": {"auto": false},
          "provider": {"openai": {"models": {"gpt-6.1-sol": {
            "limit": {"context": 32000, "output": 4000},
            "options": {"reasoningEffort": "low"}
          }}}},
          "mcp": {"personal": {"type": "local", "command": ["my-server"]}},
        }''')
        config = self.config(install_opencode=True)
        configure_agent_context_defaults(config)
        value = json.loads(path.read_text())
        self.assertFalse(value["compaction"]["auto"])
        model = value["provider"]["openai"]["models"]["gpt-6.1-sol"]
        self.assertEqual(model["limit"], {"context": 32000, "output": 4000})
        self.assertEqual(model["options"], {"reasoningEffort": "low"})
        self.assertEqual(value["mcp"]["personal"]["command"], ["my-server"])
        self.assertFalse(path.with_suffix(".json").exists())
        previous = path.read_bytes()
        configure_agent_context_defaults(config)
        self.assertEqual(path.read_bytes(), previous)

    def test_malformed_configs_are_preserved(self):
        for content in ('', '[]', '{broken', '{"env": []}', '{"autoCompactWindow": 100000, "modelSettings": null}', '{"model": "a", "model": "b"}'):
            with self.subTest(content=content):
                path = self.write(".claude/settings.json", content)
                with self.assertRaises(ValueError):
                    configure_agent_context_defaults(self.config(install_claude=True))
                self.assertEqual(path.read_text(), content)

    def test_symlink_fifo_and_hardlink_configs_are_rejected_without_modifying_target(self):
        target = self.write("outside.json", '{"personal": true}')
        path = self.home / ".claude/settings.json"
        path.parent.mkdir()
        for kind in ("symlink", "fifo", "hardlink"):
            with self.subTest(kind=kind):
                if kind == "symlink":
                    path.symlink_to(target)
                elif kind == "fifo":
                    os.mkfifo(path)
                else:
                    os.link(target, path)
                try:
                    with self.assertRaises((OSError, ValueError)):
                        configure_agent_context_defaults(self.config(install_claude=True))
                    self.assertEqual(target.read_text(), '{"personal": true}')
                finally:
                    path.unlink()

    def test_symlinked_ancestor_is_rejected(self):
        directory = self.home / "elsewhere"
        directory.mkdir()
        (self.home / ".claude").symlink_to(directory, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Unsafe agent configuration directory"):
            configure_agent_context_defaults(self.config(install_claude=True))
        self.assertEqual(list(directory.iterdir()), [])

    def test_dry_run_and_unselected_tools_do_not_read_or_write(self):
        for config in (self.config(install_claude=True, dry_run=True), self.config()):
            with patch("common.agent_context_steps.pwd.getpwnam") as lookup:
                configure_agent_context_defaults(config)
                lookup.assert_not_called()
        self.assertEqual(list(self.home.iterdir()), [])

    def test_context_step_is_exported_and_follows_payload_before_t3(self):
        self.assertIs(get_custom_step_functions()["configure_agent_context_defaults"], configure_agent_context_defaults)
        config = self.config(install_codex=True, install_claude=True, agent_payload=True, web_interfaces=["t3code"])
        steps = []
        extend_agent_steps(config, steps)
        labels = [label for label, _ in steps]
        self.assertLess(labels.index("Copying agent tool configuration"), labels.index("Configuring agent context budgets"))
        self.assertLess(labels.index("Configuring agent context budgets"), labels.index("Installing T3 Code web interface"))

    def test_wsl_plan_configures_user_context_defaults(self):
        config = self.config(system_type="server_wsl", machine_type="wsl", install_codex=True)
        functions = [function for _, function in build_wsl_steps(config)]
        self.assertIn(configure_agent_context_defaults, functions)


if __name__ == "__main__":
    unittest.main()
