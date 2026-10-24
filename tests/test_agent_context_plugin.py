"""Exercise the generated native provider hook without network or model calls."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import agent_context_steps as context
from lib.config import SetupConfig
from lib.opencode_context import plugin_content


class OpenCodeContextVersionTests(unittest.TestCase):
    def test_only_qualified_versions_are_accepted_without_starting_a_model(self):
        config = SetupConfig(host="testhost", username="agent", system_type="agent_vm")
        account = SimpleNamespace(pw_dir="/home/agent", pw_uid=os.geteuid())
        for version, accepted in (("1.19.0", True), ("1.20.1", True), ("1.18.9", False),
                                  ("2.0.0", False), ("unrecognized", False)):
            with self.subTest(version=version), patch.object(context.shutil, "which", return_value="/usr/bin/opencode"), \
                    patch.object(context, "run", return_value=SimpleNamespace(stdout=version, returncode=0)) as run:
                if accepted:
                    context._check_opencode_context_support(config, account)
                else:
                    with self.assertRaisesRegex(RuntimeError, "qualified OpenCode"):
                        context._check_opencode_context_support(config, account)
                command = run.call_args.args[0]
                self.assertEqual(command[:2], ["env", "-i"])
                self.assertEqual(command[-2:], ["/usr/bin/opencode", "--version"])

    def test_probe_demotes_to_provider_owner(self):
        config = SetupConfig(host="testhost", username="agent", system_type="agent_vm")
        account = SimpleNamespace(pw_dir="/home/agent", pw_uid=1234)
        with patch.object(context.os, "geteuid", return_value=0), \
                patch("common.agent_steps._tool_path", return_value="/home/agent/.opencode/bin/opencode"), \
                patch.object(context, "run", return_value=SimpleNamespace(stdout="1.19.0", returncode=0)) as run:
            context._check_opencode_context_support(config, account)
        self.assertEqual(run.call_args.args[0][:6], ["runuser", "-u", "agent", "--", "env", "-i"])


@unittest.skipUnless(shutil.which("node"), "Node.js is required for generated OpenCode hook tests")
class OpenCodeContextHookTests(unittest.TestCase):
    def model(self, **changes):
        value = {"id": "gpt-6.1-sol", "limit": {"context": 1_050_000, "output": 128_000},
                 "cost": {"input": 2, "output": 10, "cache": {"read": 0.2, "write": 0}},
                 "capabilities": {"toolcall": True, "output": {"text": True}}}
        value.update(changes)
        return value

    def evaluate(self, models, *, config=None, provider="openai", now="2026-10-15"):
        with tempfile.TemporaryDirectory() as temporary:
            plugin = Path(temporary) / "context.mjs"
            plugin.write_text(plugin_content("2026-10-15", context._REVIEWED_OPENAI))
            script = r'''
import fs from "node:fs";
import { pathToFileURL } from "node:url";
const data = JSON.parse(fs.readFileSync(0, "utf8"));
Date.now = () => Date.parse(data.now + "T12:00:00Z");
const module = await import(pathToFileURL(process.argv[1]));
const freeze = (value) => {
  if (value && typeof value === "object") {
    Object.values(value).forEach(freeze);
    Object.freeze(value);
  }
  return value;
};
const factory = data.provider === "openai" ? module.BasaltwaterOpenAIContext : module.BasaltwaterAnthropicContext;
const hook = await factory();
await hook.config(data.config);
const result = await hook.provider.models({models: freeze(data.models)});
process.stdout.write(JSON.stringify({models:result, config:data.config, exports:Object.keys(module)}));
'''
            result = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script, str(plugin)],
                                    input=json.dumps({"models": models, "config": config or {}, "provider": provider, "now": now}),
                                    text=True, capture_output=True, timeout=15, check=False)
            if result.returncode:
                raise RuntimeError(result.stderr)
            return json.loads(result.stdout)

    def test_native_availability_capabilities_and_capacities_are_preserved(self):
        models = {"gpt-6.1-sol": self.model(), "future-model": self.model(cost={"input": 1, "output": 4}),
                  "small-model": self.model(limit={"context": 32000, "input": 24000, "output": 4000})}
        original = copy.deepcopy(models)
        result = self.evaluate(models)
        self.assertTrue(result["config"]["compaction"]["auto"])
        self.assertEqual(set(result["exports"]), {"BasaltwaterOpenAIContext", "BasaltwaterAnthropicContext"})
        updated = result["models"]
        self.assertEqual(set(updated), set(models))  # An auth-filtered model must never reappear.
        self.assertEqual(updated["gpt-6.1-sol"]["limit"], {"context": 1_050_000, "input": 240_000, "output": 128_000})
        self.assertEqual(updated["future-model"]["limit"]["input"], 80_000)
        self.assertEqual(updated["small-model"]["limit"], models["small-model"]["limit"])
        self.assertEqual(updated["gpt-6.1-sol"]["capabilities"], models["gpt-6.1-sol"]["capabilities"])
        self.assertEqual(models, original)

    def test_non_text_models_are_left_to_native_provider(self):
        models = {"audio-only": self.model(limit={"context": 0, "output": 0},
                                          capabilities={"output": {"text": False, "audio": True}})}
        self.assertEqual(self.evaluate(models)["models"], models)

    def test_unknown_model_ids_cannot_inherit_reviewed_prices_or_change_result_prototype(self):
        models = {key: self.model(cost={}) for key in ("constructor", "__proto__", "toString")}
        result = self.evaluate(models)["models"]
        self.assertEqual(set(result), set(models))
        self.assertTrue(all(model["limit"]["input"] == 80_000 for model in result.values()))

    def test_review_age_and_clock_skew_never_reduce_reviewed_budgets(self):
        for now in ("2026-10-14", "2026-10-15", "2026-10-22", "2026-10-23", "2030-10-24"):
            with self.subTest(now=now):
                result = self.evaluate({"gpt-6.1-sol": self.model()}, now=now)
                self.assertEqual(result["models"]["gpt-6.1-sol"]["limit"]["input"], 240_000)

    def test_changed_prices_still_use_fallback(self):
        result = self.evaluate({"gpt-6.1-sol": self.model(cost={"input": 4, "output": 20})})
        self.assertEqual(result["models"]["gpt-6.1-sol"]["limit"]["input"], 80_000)

    def test_metadata_lowers_budget_for_input_output_cache_and_legacy_price_tiers(self):
        for increase in ({"input": 4}, {"output": 15}, {"cache": {"read": 1}}):
            with self.subTest(increase=increase):
                cost = self.model()["cost"]
                cost["tiers"] = [{"tier": {"type": "context", "size": 50_000}, **increase}]
                result = self.evaluate({"gpt-6.1-sol": self.model(cost=cost)})
                self.assertEqual(result["models"]["gpt-6.1-sol"]["limit"]["input"], 40_000)
        cost = self.model()["cost"]
        cost["experimentalOver200K"] = {"input": 4, "output": 15}
        result = self.evaluate({"gpt-6.1-sol": self.model(cost=cost)})
        self.assertEqual(result["models"]["gpt-6.1-sol"]["limit"]["input"], 160_000)

    def test_unknown_anthropic_models_use_conservative_budget(self):
        result = self.evaluate({"claude-future": self.model()}, provider="anthropic")
        self.assertEqual(result["models"]["claude-future"]["limit"]["input"], 80_000)

    def test_explicit_limits_and_disabled_compaction_allow_user_overrides(self):
        models = {"gpt-6.1-sol": self.model()}
        for config in ({"compaction": {"auto": False}},
                       {"provider": {"openai": {"models": {"my-small-model": {"id": "gpt-6.1-sol", "limit": {"context": 32000}}}}}},
                       {"provider": {"openai": {"models": {"gpt-6.1-sol": {"limit": {"input": 800_000}}}}}}):
            with self.subTest(config=config):
                result = self.evaluate(models, config=config)
                self.assertEqual(result["models"], models)
                if "compaction" in config:
                    self.assertFalse(result["config"]["compaction"]["auto"])

    def test_unrecognized_native_capacity_or_pricing_schema_fails_explicitly(self):
        for model in (self.model(limit={"context": True}), self.model(limit={"context": 1000, "input": -1}),
                      self.model(cost={"tiers": {}}), self.model(cost={"tiers": [None]}),
                      self.model(cost={"tiers": [{"tier": {"type": "future", "size": 1000}}]})):
            with self.subTest(model=model):
                with self.assertRaisesRegex(RuntimeError, "Basaltwater"):
                    self.evaluate({"gpt-6.1-sol": model})


if __name__ == "__main__":
    unittest.main()
