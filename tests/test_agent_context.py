"""Context defaults preserve user choices and never touch live provider state."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
import io
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import tomllib
import unittest
from unittest.mock import patch

from common import agent_context_steps as context
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
        self.real_policy_fresh = context._policy_fresh
        fresh = patch.object(context, "_policy_fresh", return_value=True)
        fresh.start()
        self.addCleanup(fresh.stop)
        support = patch.object(context, "_check_opencode_context_support")
        self.support = support.start()
        self.addCleanup(support.stop)

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
        self.assertEqual(value["autoCompactWindow"], 100_000)
        self.assertNotIn("claude-haiku-5-5", value["modelSettings"])
        self.assertEqual(value["modelSettings"]["claude-sonnet-5-5"]["autoCompactWindow"], 200_000)
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

    def test_opencode_jsonc_remains_unchanged_with_native_plugin(self):
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
        previous = path.read_bytes()
        configure_agent_context_defaults(config)
        self.assertEqual(path.read_bytes(), previous)
        from common.browser_automation_steps import _load_opencode_config

        value = _load_opencode_config(str(path))
        self.assertFalse(value["compaction"]["auto"])
        model = value["provider"]["openai"]["models"]["gpt-6.1-sol"]
        self.assertEqual(model["limit"], {"context": 32000, "output": 4000})
        self.assertEqual(model["options"], {"reasoningEffort": "low"})
        self.assertEqual(value["mcp"]["personal"]["command"], ["my-server"])
        self.assertFalse(path.with_suffix(".json").exists())
        previous = path.read_bytes()
        configure_agent_context_defaults(config)
        self.assertEqual(path.read_bytes(), previous)

    def state_path(self, relative):
        path = self.home / relative
        return path.with_name(f".{path.name}.basaltwater-context.json")

    def test_opencode_plugin_refreshes_without_creating_model_definitions(self):
        config = self.config(install_opencode=True)
        configure_agent_context_defaults(config)
        path = self.home / ".config/opencode/plugins/basaltwater-context.js"
        previous = path.read_bytes()
        self.assertFalse((self.home / ".config/opencode/opencode.json").exists())
        configure_agent_context_defaults(config)
        self.assertEqual(path.read_bytes(), previous)
        with patch.object(context, "POLICY_VERIFIED_ON", date(2026, 10, 16)):
            configure_agent_context_defaults(config)
        self.assertIn('"verifiedOn": "2026-10-16"', path.read_text())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_edited_removed_or_untracked_opencode_plugin_is_preserved(self):
        for choice in ("edit", "remove", "untracked"):
            with self.subTest(choice=choice), tempfile.TemporaryDirectory() as temporary:
                account = SimpleNamespace(pw_dir=temporary, pw_uid=os.getuid(), pw_gid=os.getgid())
                path = Path(temporary) / ".config/opencode/plugins/basaltwater-context.js"
                config = self.config(install_opencode=True)
                with patch.object(context.pwd, "getpwnam", return_value=account):
                    if choice != "untracked":
                        configure_agent_context_defaults(config)
                    else:
                        path.parent.mkdir(parents=True)
                    if choice == "remove":
                        path.unlink()
                    else:
                        path.write_text("// personal plugin\n")
                    configure_agent_context_defaults(config)
                    self.assertEqual(path.read_text() if path.exists() else None,
                                     None if choice == "remove" else "// personal plugin\n")
                    state = json.loads(path.with_name(".basaltwater-context-state.json").read_text())
                    self.assertTrue(state["disabled"])

    def test_opencode_interrupted_plugin_install_recovers(self):
        for after in (False, True):
            with self.subTest(after=after), tempfile.TemporaryDirectory() as temporary:
                account = SimpleNamespace(pw_dir=temporary, pw_uid=os.getuid(), pw_gid=os.getgid())
                real_write = context.write_text_atomic
                def interrupted(*args, **kwargs):
                    if after:
                        real_write(*args, **kwargs)
                    raise OSError("interrupted plugin")
                with patch.object(context.pwd, "getpwnam", return_value=account):
                    config = self.config(install_opencode=True)
                    with patch.object(context, "write_text_atomic", side_effect=interrupted):
                        with self.assertRaisesRegex(OSError, "interrupted plugin"):
                            configure_agent_context_defaults(config)
                    configure_agent_context_defaults(config)
                    path = Path(temporary) / ".config/opencode/plugins/basaltwater-context.js"
                    self.assertIn("BasaltwaterOpenAIContext", path.read_text())
                    state = json.loads(path.with_name(".basaltwater-context-state.json").read_text())
                    self.assertFalse(state["disabled"])
                    self.assertIsNone(state["pending"])

    def test_unqualified_opencode_and_corrupt_state_preserve_plugin(self):
        config = self.config(install_opencode=True)
        configure_agent_context_defaults(config)
        path = self.home / ".config/opencode/plugins/basaltwater-context.js"
        previous = path.read_bytes()
        self.support.side_effect = RuntimeError("unqualified")
        with self.assertRaisesRegex(RuntimeError, "unqualified"):
            configure_agent_context_defaults(config)
        self.assertEqual(path.read_bytes(), previous)
        self.support.side_effect = None
        path.with_name(".basaltwater-context-state.json").write_text('{"version": true}')
        with self.assertRaisesRegex(ValueError, "ownership"):
            configure_agent_context_defaults(config)
        self.assertEqual(path.read_bytes(), previous)

    def test_managed_claude_refresh_retires_old_models_and_preserves_unrelated_edits(self):
        config = self.config(install_claude=True)
        configure_agent_context_defaults(config)
        path = self.home / ".claude/settings.json"
        value = json.loads(path.read_text())
        value["env"]["MY_SECRET_TOKEN"] = "private-value"
        value["modelSettings"]["claude-sonnet-5-5"]["effortLevel"] = "high"
        path.write_text(json.dumps(value))
        updated = {"autoCompactWindow": 100_000, "modelSettings": {"claude-sonnet-6": {"autoCompactWindow": 200_000}},
                   "env": {"CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "70"}}
        with patch.object(context, "CLAUDE_CONTEXT_DEFAULTS", updated):
            configure_agent_context_defaults(config)
        value = json.loads(path.read_text())
        self.assertEqual(value["modelSettings"]["claude-sonnet-5-5"], {"effortLevel": "high"})
        self.assertEqual(value["modelSettings"]["claude-sonnet-6"]["autoCompactWindow"], 200_000)
        self.assertEqual(value["env"]["MY_SECRET_TOKEN"], "private-value")
        self.assertEqual(value["env"]["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"], "70")
        self.assertNotIn("private-value", self.state_path(".claude/settings.json").read_text())

    def test_user_edit_or_removal_stops_managed_refresh_even_if_value_later_matches(self):
        for remove in (False, True):
            with self.subTest(remove=remove), tempfile.TemporaryDirectory() as temporary:
                account = SimpleNamespace(pw_dir=temporary, pw_uid=os.getuid(), pw_gid=os.getgid())
                path = Path(temporary) / ".claude/settings.json"
                with patch.object(context.pwd, "getpwnam", return_value=account):
                    config = self.config(install_claude=True)
                    configure_agent_context_defaults(config)
                    value = json.loads(path.read_text())
                    if remove:
                        del value["autoCompactWindow"]
                    else:
                        value["autoCompactWindow"] = 800_000
                    path.write_text(json.dumps(value))
                    previous = path.read_bytes()
                    configure_agent_context_defaults(config)
                    self.assertEqual(path.read_bytes(), previous)
                    # Restoring an old value is still a user choice after detachment.
                    value["autoCompactWindow"] = 100_000
                    path.write_text(json.dumps(value))
                    previous = path.read_bytes()
                    with patch.object(context, "CLAUDE_CONTEXT_DEFAULTS", {"autoCompactWindow": 200_000}):
                        configure_agent_context_defaults(config)
                    self.assertEqual(path.read_bytes(), previous)

    def test_local_codex_managed_block_refresh_keeps_personal_tables(self):
        path = self.write(".codex/config.toml", '# personal\n[mcp_servers.personal]\ncommand = "mine"\n')
        config = self.config(system_type="server_wsl", machine_type="wsl", install_codex=True)
        configure_agent_context_defaults(config)
        with patch.object(context, "CODEX_CONTEXT_DEFAULTS", {
            "model_auto_compact_token_limit": 200_000, "model_auto_compact_token_limit_scope": "total",
        }):
            configure_agent_context_defaults(config)
        value = tomllib.loads(path.read_text())
        self.assertEqual(value["model_auto_compact_token_limit"], 200_000)
        self.assertEqual(value["mcp_servers"]["personal"]["command"], "mine")
        self.assertIn("# personal", path.read_text())

    def test_stale_review_warns_without_changing_managed_preferences(self):
        config = self.config(system_type="agent_cachyos", install_codex=True, install_claude=True)
        configure_agent_context_defaults(config)
        paths = [self.home / ".codex/config.toml", self.home / ".claude/settings.json"]
        previous = [path.read_bytes() for path in paths]
        with patch.object(context, "_policy_fresh", return_value=False), patch("sys.stdout", new_callable=io.StringIO) as output:
            configure_agent_context_defaults(config)
            codex = tomllib.loads(context.codex_context_defaults_toml())
        self.assertEqual([path.read_bytes() for path in paths], previous)
        self.assertEqual(codex["model_auto_compact_token_limit"], 240_000)
        self.assertIn("review is stale", output.getvalue())
        self.assertIn("retaining managed context budgets", output.getvalue())

    def test_initial_defaults_are_stable_for_expired_and_future_review_dates(self):
        for verified in (date(2020, 1, 1), date(2030, 1, 1)):
            with self.subTest(verified=verified), patch.object(context, "POLICY_VERIFIED_ON", verified), \
                    patch.object(context, "_policy_fresh", return_value=False):
                self.assertEqual(context._defaults("codex")["model_auto_compact_token_limit"], 240_000)
                self.assertEqual(context._defaults("claude")["modelSettings"]["claude-sonnet-5-5"]["autoCompactWindow"], 200_000)
                for hardened in (False, True):
                    value = tomllib.loads(_codex_config_content(hardened=hardened))
                    self.assertEqual(value["model_auto_compact_token_limit"], 240_000)

    def test_refresh_restores_owned_budgets_previously_reduced_by_review_age(self):
        config = self.config(system_type="agent_cachyos", install_codex=True, install_claude=True)
        old_codex = {**context.CODEX_CONTEXT_DEFAULTS, "model_auto_compact_token_limit": 80_000}
        old_claude = {key: value for key, value in context.CLAUDE_CONTEXT_DEFAULTS.items() if key != "modelSettings"}
        with patch.object(context, "CODEX_CONTEXT_DEFAULTS", old_codex), \
                patch.object(context, "CLAUDE_CONTEXT_DEFAULTS", old_claude):
            configure_agent_context_defaults(config)
        with patch.object(context, "_policy_fresh", return_value=False):
            configure_agent_context_defaults(config)
        codex = tomllib.loads((self.home / ".codex/config.toml").read_text())
        claude = json.loads((self.home / ".claude/settings.json").read_text())
        self.assertEqual(codex["model_auto_compact_token_limit"], 240_000)
        self.assertEqual(claude["modelSettings"], context.CLAUDE_CONTEXT_DEFAULTS["modelSettings"])

    def test_untracked_small_user_budget_is_preserved_during_migration(self):
        path = self.write(".codex/config.toml", 'model_auto_compact_token_limit = 80000\n')
        previous = path.read_bytes()
        configure_agent_context_defaults(self.config(system_type="agent_cachyos", install_codex=True))
        self.assertEqual(path.read_bytes(), previous)

    def test_interrupted_writes_recover_before_and_after_configuration_replacement(self):
        for after in (False, True):
            with self.subTest(after=after), tempfile.TemporaryDirectory() as temporary:
                account = SimpleNamespace(pw_dir=temporary, pw_uid=os.getuid(), pw_gid=os.getgid())
                path = Path(temporary) / ".claude/settings.json"
                real_write = context.write_text_atomic
                def interrupted(*args, **kwargs):
                    if after:
                        real_write(*args, **kwargs)
                    raise OSError("simulated interruption")
                with patch.object(context.pwd, "getpwnam", return_value=account):
                    config = self.config(install_claude=True)
                    with patch.object(context, "write_text_atomic", side_effect=interrupted):
                        with self.assertRaisesRegex(OSError, "simulated interruption"):
                            configure_agent_context_defaults(config)
                    configure_agent_context_defaults(config)
                    self.assertEqual(json.loads(path.read_text())["autoCompactWindow"], 100_000)
                    state = json.loads(path.with_name(f".{path.name}.basaltwater-context.json").read_text())
                    self.assertFalse(state["overridden"])
                    self.assertIsNone(state["pending"])

    def test_corrupt_or_hostile_state_preserves_provider_configuration(self):
        config = self.config(install_claude=True)
        configure_agent_context_defaults(config)
        path = self.home / ".claude/settings.json"
        previous = path.read_bytes()
        state_path = self.state_path(".claude/settings.json")
        state = json.loads(state_path.read_text())
        state["active"]["paths"] = [["env", "API_KEY"]]
        for content in ("broken", json.dumps(state)):
            with self.subTest(content=content):
                state_path.write_text(content)
                with self.assertRaises(ValueError):
                    configure_agent_context_defaults(config)
                self.assertEqual(path.read_bytes(), previous)

    def test_legacy_untracked_defaults_are_not_adopted_by_value(self):
        path = self.write(".claude/settings.json", json.dumps(context.CLAUDE_CONTEXT_DEFAULTS))
        previous = path.read_bytes()
        configure_agent_context_defaults(self.config(install_claude=True))
        self.assertEqual(path.read_bytes(), previous)
        self.assertTrue(json.loads(self.state_path(".claude/settings.json").read_text())["overridden"])

    def test_review_age_includes_seven_days_and_rejects_future_dates(self):
        for verified, expected in ((date(2026, 10, 15), True), (date(2026, 10, 8), True),
                                   (date(2026, 10, 7), False), (date(2026, 10, 16), False)):
            with self.subTest(verified=verified), patch.object(context, "POLICY_VERIFIED_ON", verified), \
                    patch.object(context, "datetime") as clock:
                clock.now.return_value = datetime(2026, 10, 15, tzinfo=timezone.utc)
                self.assertEqual(self.real_policy_fresh(), expected)

    def test_simultaneous_setup_is_rejected_before_replacing_configuration(self):
        from lib.agent_context_state import context_lock

        config = self.config(install_claude=True)
        configure_agent_context_defaults(config)
        path = self.home / ".claude/settings.json"
        previous = path.read_bytes()
        with context_lock(path.with_name(f".{path.name}.basaltwater-context.lock"), os.getuid(), os.getgid()):
            with self.assertRaisesRegex(RuntimeError, "Another context setup"):
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
