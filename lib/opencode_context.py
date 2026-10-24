"""Generate a dependency-free OpenCode provider hook using its native models."""

from __future__ import annotations

import json


def plugin_content(verified_on: str, reviewed: dict[str, tuple[float, float]]) -> str:
    policy = {"verifiedOn": verified_on, "reviewed": reviewed}
    return "// Managed by Basaltwater context setup.\nconst policy = " + json.dumps(policy) + ";\n" + _SOURCE


_SOURCE = r'''
const positive = (value) => Number.isSafeInteger(value) && value > 0;
const increased = (next, base) =>
  ["input", "output"].some((key) => Number.isFinite(next?.[key]) && Number.isFinite(base?.[key]) && next[key] > base[key]) ||
  ["read", "write"].some((key) => Number.isFinite(next?.cache?.[key]) && Number.isFinite(base?.cache?.[key]) && next.cache[key] > base.cache[key]);

function budgetFor(providerID, id, model) {
  const context = model.limit?.context;
  const input = model.limit?.input;
  if (!positive(context) || (input !== undefined && input !== 0 && !positive(input))) {
    throw new Error("Basaltwater cannot verify native context capacity for " + providerID + "/" + id);
  }
  let budget = 80000;
  const cost = model.cost;
  const reviewed = providerID === "openai" && Object.hasOwn(policy.reviewed, id) && policy.reviewed[id];
  if (reviewed && cost?.input === reviewed[0] && cost?.output === reviewed[1]) budget = 240000;
  if (cost?.tiers !== undefined && !Array.isArray(cost.tiers)) {
    throw new Error("Basaltwater does not recognize OpenCode context pricing metadata");
  }
  for (const tier of cost?.tiers ?? []) {
    if (tier?.tier?.type !== "context" || !positive(tier.tier.size)) {
      throw new Error("Basaltwater does not recognize OpenCode context pricing tier");
    }
    if (increased(tier, cost)) budget = Math.min(budget, Math.max(1, Math.floor(tier.tier.size * 0.8)));
  }
  if (cost?.experimentalOver200K && increased(cost.experimentalOver200K, cost)) {
    budget = Math.min(budget, 160000);
  }
  return Math.min(budget, context, positive(input) ? input : context);
}

function hooks(providerID) {
  let config = {};
  return {
    config: async (value) => {
      value.compaction ??= {};
      value.compaction.auto ??= true;
      config = value;
    },
    provider: {
      id: providerID,
      models: async (provider) => {
        const result = Object.create(null);
        // Earlier native/auth hooks decide availability. Never create model IDs,
        // copy a public catalog into config, or mutate the source model objects.
        for (const [id, model] of Object.entries(provider.models)) {
          const explicit = Object.entries(config.provider?.[providerID]?.models ?? {}).some(
            ([key, configured]) => (key === id || configured.id === id) && configured.limit !== undefined,
          );
          if (config.compaction?.auto === false || explicit || model.capabilities?.output?.text === false) {
            result[id] = model;
            continue;
          }
          result[id] = { ...model, limit: { ...model.limit, input: budgetFor(providerID, id, model) } };
        }
        return result;
      },
    },
  };
}

export const BasaltwaterOpenAIContext = async () => hooks("openai");
export const BasaltwaterAnthropicContext = async () => hooks("anthropic");
'''
