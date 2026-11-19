# Pricing and limits

[Documentation index](agent-context.md)

## Verified pricing boundaries

Documentation checked **2026-11-17**. These are direct-provider Standard API
prices in USD per million uncached input/output tokens, not subscription or
gateway quotes.

| Model | Native context | Short-context input / output | Higher-price trigger and rates |
| --- | --- | --- | --- |
| GPT-6.2 Sol | 1,050,000 | $2 / $10 | More than 272,000 input tokens: $4 / $15 |
| GPT-6.2 Astra | 1,050,000 | $8 / $40 | More than 272,000 input tokens: $16 / $60 |
| GPT-6.1 Sol | 1,050,000 | $2 / $10 | More than 272,000 input tokens: $4 / $15 |
| GPT-6.1 Terra | 1,050,000 | $1 / $5 | More than 272,000 input tokens: $2 / $7.50 |
| GPT-6 Astra | 1,050,000 | $10 / $50 | More than 272,000 input tokens: $20 / $75 |
| GPT-6 Luna | 1,050,000 | $0.10 / $0.50 | More than 272,000 input tokens: $0.20 / $0.75 |
| Claude Haiku 5.5 | 1,000,000 | $0.10 / $0.50 | More than 100,000 input tokens: $0.50 / $2.50 |
| Claude Sonnet 5.5 / Opus 5.5 | 1,000,000 | $2 / $10; $4 / $20 respectively | Full context has the same per-token rates |
| Claude Fable 5.2 | 1,000,000 | $8 / $40 | Full context has the same per-token rates |

See the official model documentation for
[GPT-6.2 Sol](https://developers.openai.com/api/docs/models/gpt-6.2-sol),
[GPT-6.2 Astra](https://developers.openai.com/api/docs/models/gpt-6.2-astra),
[GPT-6.1 Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol),
[GPT-6.1 Terra](https://developers.openai.com/api/docs/models/gpt-6.1-terra),
[GPT-6 Astra](https://developers.openai.com/api/docs/models/gpt-6-astra), and
[GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna);
[Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing) and
[Haiku 5.5 limits](https://platform.claude.com/docs/en/models/haiku-5-5/overview).

## Subscription and effective context limits

Keep three values separate: the model's native API capacity, the client/account's
active window, and its configured compaction target. The API models above allow
up to 128K output tokens; output and reasoning also need room in the request.
An API capacity is not a promise that a particular agent can use all of it.

Codex documents a **500K default context window for GPT-6.2 Sol** and **272K
for GPT-6.2 Astra**. Each API model allows up to 922K input and 128K output
within a 1,050,000-token window. The client's active context and Basaltwater's
compaction target can both be lower. Use the live client and account settings
to verify the usable window. See [Codex models](https://learn.chatgpt.com/docs/models).

The 272K price threshold applies to direct API billing. Check the active plan
for included usage, purchased credits, or gateway pricing. Longer prompts
consume more of the included allowance. OpenAI explicitly documents no
long-context surcharge on included GPT-6.1 Sol usage. See
[Codex subscription pricing](https://learn.chatgpt.com/docs/pricing).

Current Claude Fable 5.2, Opus 5.5, Sonnet 5.5 and Haiku 5.5 have native 1M
windows, but plan/provider access, gateway restrictions and local compaction
can reduce the effective window. Do not generalize older Opus/Sonnet 4.6
`[1m]` access rules to the current models. Fable may require separately billed
usage credits on a subscription. Check `/model`, `/context` and `/autocompact`;
see [Claude client configuration](https://code.claude.com/docs/en/model-config).

The managed targets below apply across billing routes. The OpenCode hook
recognizes the reviewed GPT-6.2 Sol and Astra prices. Terra and Fable use their
provider's managed fallback. Verify effective settings before relying on native
capacity, including for subagents. Use a fresh bounded brief when supported.
See [model choices](agent-subagents.md) for workload and subscription guidance.

Input thresholds include cached input. A cache hit does not make a large prompt
short for pricing purposes. Fast/priority service, batch/flex processing, and
regional premiums are separate pricing dimensions; compaction does not select
a cheaper service tier. Account usage tiers govern rate limits and availability,
not the prompt-length threshold. Check the active model and provider before
raising a budget. API capacity does not prove that a Codex subscription or
particular client exposes the same window or bills at API rates.

## Managed defaults

| Tool | Defaults | Purpose |
| --- | --- | --- |
| Codex | `model_auto_compact_token_limit = 240000`, `model_auto_compact_token_limit_scope = "total"` | Count the entire active context and compact with 32K headroom before the known 272K price boundary |
| Claude Code | Global `autoCompactWindow: 100000`, with `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE: "80"`; reviewed Sonnet 5.5/Opus 5.5 get 200K windows | Around 80K for unknown/renamed models and Haiku; around 160K for reviewed flat-price models |
| OpenCode | Native provider hook; input budget at most 240K for reviewed OpenAI prices, otherwise 80K | Retain native availability/capacities and lower the budget further when metadata reports an earlier price boundary |

Codex's native model capacity is left unset. A smaller model's own compaction
limit can still apply. The `total` scope matters: counting only growth after an
inherited prefix can let the actual prompt exceed the pricing boundary.
These are ordinary defaults, including in hardened mode; they add no context
constraint to Codex's requirements file.

Claude settings live in `~/.claude/settings.json`. Its percentage override
applies to the configured auto-compact window, including subagents, where
proactive compaction is supported. Per-model settings require Claude Code
v2.1.288 or later. WSL setup retains an already installed CLI; externally
managed clients also keep their original updater. Older clients, custom model
IDs, or provider-specific aliases must be checked with
`/autocompact` and `/context` before relying on the Haiku budget.

OpenCode loads `~/.config/opencode/plugins/basaltwater-context.js` through its
native local-plugin discovery. The hook targets OpenAI/Anthropic text models
already present after built-in authentication hooks. It never adds model IDs or
enables a provider, so it preserves subscription model filters. It does not copy
model definitions into configuration, fetch a second catalog, or make model/API
calls. New model IDs and changed capacities follow OpenCode's native metadata
when the backend initializes; restart/resume that backend to reload it.

Setup qualifies OpenCode 1.x >= 1.19.0 with an owner-scoped `--version` probe.
Older clients must be updated before context setup, including when the normal
setup updater would otherwise run later. Use `basaltw agent update --tool opencode`
as the provider user, or the executable's owning package manager. A new major
version requires reviewing its provider-hook API before enabling this integration.
Custom config homes and disabled plugin loading require separate verification.

Each native text model receives an input budget bounded by its reported native
input/context capacity. Total context, output and capabilities remain native.
Unknown or renamed models get an 80K fallback. A reviewed OpenAI model gets up to
240K only while its base input/output prices still match the reviewed prices.
Context-price tiers in metadata lower the input budget to at most 80% of the
earliest reported price increase. The older `context_over_200k` metadata field
is mapped by OpenCode to `experimentalOver200K` and treated conservatively,
even if newer official pricing allows more context.
OpenCode's own compaction buffer can trigger earlier still.

Explicit per-model `limit` objects and `compaction.auto: false` in any effective
configuration, including process-local approval overrides, take precedence.
The hook enables automatic compaction in memory when that preference is unset.
Setup refreshes the unchanged owned plugin using a private hash journal beside
it (`.basaltwater-context-state.json`); editing or removing the plugin opts out
of managed refresh. Invalid ownership records or unrecognized runtime metadata
fail explicitly. Other providers and custom routing/pricing need separate
verification.

## Keeping pricing guidance current

Aim to review pricing during the near-daily Basaltwater update cycle. Updating
Basaltwater or a CLI executable alone does **not** reconcile provider files:
rerun the applicable setup/refresh flow to apply reviewed defaults. A newer
binary or a freshly fetched catalog does not prove that official pricing was
reviewed.

The single `POLICY_VERIFIED_ON` date and seven-day review interval live in
`common/agent_context_steps.py`. After seven days, or if the date is in the
future, setup reports a stale review and retains the reviewed defaults. The
reminder is advisory: neither setup nor the OpenCode runtime changes budgets
because time passed. Price or model-capacity changes still require review;
OpenCode also retains its checks against native pricing and capacity metadata.
Reinstalling the same release does not advance the review date.

The next setup/refresh restores unchanged owned Codex and Claude settings that
the old age-based policy reduced. Untracked or edited user policies remain
untouched, including an explicit 80K budget. Managed T3 setup refreshes reload
provider configuration and skills through the selected provider lifecycle;
standalone provider CLIs must be exited and resumed manually. Existing saved T3
threads can continue when sent a new message. Continuation of an interrupted
active turn depends on T3's **Continue threads after server update** setting;
queued turns still require user resume, and approvals may not survive a restart.

Before advancing that date, check the official sources above for every reviewed
model and the current native configuration controls. Update prices, thresholds,
model IDs and documentation together, and run `tests.test_agent_context` and
`tests.test_agent_context_plugin` (with Node.js available) plus
the normal CI checks. Review changed native catalog schemas too: metadata is
useful for reducing budgets, but is not permission to increase them beyond the
reviewed/fallback caps. A future price boundary below the fallback, missing
pricing metadata, a custom Codex provider, or a mid-session model change can
still require a new review; no fixed default predicts every future price tier.

Control references: [Codex configuration](https://learn.chatgpt.com/docs/config-file/config-reference),
[Claude model/context configuration](https://code.claude.com/docs/en/model-config),
[Claude percentage override](https://code.claude.com/docs/en/env-vars),
[Claude auto-compaction switch](https://code.claude.com/docs/en/settings-reference#autocompactenabled),
[OpenCode configuration](https://opencode.ai/docs/config/),
[OpenCode schema](https://opencode.ai/config.json),
[OpenCode plugins](https://opencode.ai/docs/plugins/), and the qualified
[OpenCode 1.19.0 provider hook](https://github.com/anomalyco/opencode/blob/v1.19.0/packages/opencode/src/provider/provider.ts).

Automatic compaction is a target, not a hard billing cap. A large user message,
attachment, tool result, or compaction request can overshoot it. Bound tool
output and inspect actual input usage near a threshold. A flat per-token rate
also does not make a 1M-token prompt cost the same as a short one.
