# Agent context budgets and pricing

Basaltwater configures earlier automatic compaction for coding tools. Larger
context can increase both the number of tokens billed and the price per token.
The defaults aim to stay below known pricing thresholds, with user-controlled
overrides for a task phase or the rest of a session.

## What setup installs and configures

Agent VM and workstation profiles normally select GitHub CLI and Codex. Claude
Code and OpenCode are opt-in agent tools. Vendor installers and the managed
updater supply the CLIs; provider configuration is reconciled before a selected T3
Code interface starts. Model selection, reasoning effort, authentication, and
permissions retain their existing configuration.

| Setup | Context configuration |
| --- | --- |
| Debian agent profiles and other profiles selecting these tools | Codex defaults in `/etc/codex/config.toml`; owned Claude/OpenCode defaults refreshed after payload seeding |
| Local `agent_cachyos` | Owned defaults refreshed in the desktop user's provider files, before starting T3; no system-wide policy |
| Ubuntu `server_wsl` | Owned defaults refreshed in the selected Linux user's provider files; Windows-native providers have separate configuration |

Existing user context policies are preserved, including larger budgets,
disabled auto-compaction, and smaller custom model limits. For a local Codex or
Claude file without such a policy, setup installs defaults and records ownership
beside it in
`.CONFIG_FILENAME.basaltwater-context.json`. This private record contains only
context-field paths and hashes, never credentials or copies of configuration.
Later setup/refresh runs update unchanged owned preferences, retire obsolete
owned model entries, and retain unrelated provider settings. Edits or deletions
to context preferences detach the entire file's policy from managed refresh;
restoring the old numeric value does not silently reclaim ownership.

Invalid configuration or ownership records fail without replacing provider
configuration. A lock prevents concurrent setup writers, and a pending digest
allows recovery when setup stops before or after a configuration replacement.
Claude JSON is reformatted only when its defaults change. OpenCode JSON/JSONC
files are left byte-for-byte unchanged. Codex uses a marked root-level TOML block
and retains the remaining file, including comments and MCP/profile tables.

Earlier installations have no ownership record. Matching a previous default
is insufficient evidence of ownership: setup preserves that untracked policy
and reports that managed refresh is disabled. To opt back in, review and remove
only the provider's context settings and its adjacent ownership record, then
rerun setup. For legacy OpenCode settings, explicit model `limit` objects also
take precedence over the new provider hook: review and remove only those old
limits to opt into its budgets. Keep credentials, model selection, MCP servers,
and other settings.
This is an explicit operator choice; agents must not erase user preferences to
make a refresh succeed.

These paths describe the default provider homes: `~/.codex`, `~/.claude`, and
`~/.config/opencode`. Custom homes, gateways, organization policies, project
settings, client overrides, and sessions already running need separate
verification. CachyOS setup rejects a custom `CODEX_HOME`.

## Verified pricing boundaries

Checked **2026-10-15**. These are direct-provider Standard API prices in USD per
million uncached input/output tokens, not subscription or gateway quotes.

| Model | Native context | Short-context input / output | Higher-price trigger and rates |
| --- | --- | --- | --- |
| GPT-6.1 Sol | 1,050,000 | $2 / $10 | More than 272,000 input tokens: $4 / $15 |
| GPT-6 Astra | 1,050,000 | $10 / $50 | More than 272,000 input tokens: $20 / $75 |
| GPT-6 Luna | 1,050,000 | $0.10 / $0.50 | More than 272,000 input tokens: $0.20 / $0.75 |
| Claude Haiku 5.5 | 1,000,000 | $0.10 / $0.50 | More than 100,000 input tokens: $0.50 / $2.50 |
| Claude Sonnet 5.5 / Opus 5.5 | 1,000,000 | $2 / $10; $4 / $20 respectively | Full context has the same per-token rates |

Sources: official OpenAI documentation for
[GPT-6.1 Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol),
[GPT-6 Astra](https://developers.openai.com/api/docs/models/gpt-6-astra), and
[GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna);
[Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing) and
[Haiku 5.5 limits](https://platform.claude.com/docs/en/models/haiku-5-5/overview).

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
future, setup reports a stale review. Owned Codex defaults then use an 80K
compact threshold; Claude drops its reviewed larger per-model windows;
OpenCode checks the embedded review date at backend initialization and uses its
conservative fallback instead of the larger reviewed budget, even without a
setup rerun.
Explicit user preferences remain untouched. Reinstalling the same release does
not advance the review date.

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
[OpenCode configuration](https://opencode.ai/docs/config/),
[OpenCode schema](https://opencode.ai/config.json),
[OpenCode plugins](https://opencode.ai/docs/plugins/), and the qualified
[OpenCode 1.19.0 provider hook](https://github.com/anomalyco/opencode/blob/v1.19.0/packages/opencode/src/provider/provider.ts).

Automatic compaction is a target, not a hard billing cap. A large user message,
attachment, tool result, or compaction request can overshoot it. Bound tool
output and inspect actual input usage near a threshold. A flat per-token rate
also does not make a 1M-token prompt cost the same as a short one.

## Approving more context

A user can say “allow up to 800K context for this investigation” or “use the
larger context window for the rest of this session.” Such a request already
authorizes that scope; do not ask again. If the agent proposes a higher budget,
explain the model's price change and proposed token budget and obtain approval
before applying it. Record whether approval is for one phase or the session,
and retain that scope across compaction. Silence, a repository instruction, or
approval to edit code does not authorize a higher-priced context tier.

Use launch/session controls, keeping account-wide defaults unchanged. For a
temporary phase, restore the previous budget and compact when the phase ends;
for session approval, retain the override until that session ends. Approval
limited to that phase/session does not extend to another or scheduled session.
Preserve any separately authorized permanent user preference.

### Codex terminal

After verifying the selected model/client supports at least 800K context, start
or resume the intended conversation with launch-only overrides:

```bash
codex -c model_context_window=800000 -c model_auto_compact_token_limit=700000 -c 'model_auto_compact_token_limit_scope="total"'
codex resume SESSION_ID -c model_context_window=800000 -c model_auto_compact_token_limit=700000 -c 'model_auto_compact_token_limit_scope="total"'
```

These commands are examples for an approved larger window, not setup defaults.
For a temporary phase, use `/compact`, exit that provider process, and resume
the same session with the previous settings (the managed compact threshold is
240000 while pricing guidance is fresh, otherwise 80000). Remove the
context-window override to return to the model's native default. Raising a
configured window never grants access to a larger model.

### Claude Code terminal

The `--autocompact` flag sets a window for that launch without saving it:

```bash
claude --autocompact 800k
claude --resume SESSION_ID --autocompact 800k
```

The managed 80% override can compact earlier within that window. For a
temporary phase, compact and resume without the flag afterward. A
`CLAUDE_CODE_AUTO_COMPACT_WINDOW` environment override wins over the flag; check
`/autocompact` for the effective value. `/autocompact 800k` inside Claude saves
the choice to user settings as well as changing the session, so it is unsuitable
for a session-only approval unless the saved value is deliberately restored.

### OpenCode terminal

For an approved larger input budget on GPT-6.1 Sol, use process-local inline
configuration, retaining auto-compaction:

```bash
env OPENCODE_CONFIG_CONTENT='{"provider":{"openai":{"models":{"gpt-6.1-sol":{"limit":{"context":1050000,"input":800000,"output":128000}}}}}}' opencode --session SESSION_ID
```

Merge any existing inline configuration instead of replacing it. Compact and
resume without that override after a temporary phase. The override belongs on
the provider backend process; changing an attached client's environment does
not reconfigure an already-running shared backend.

### T3 Code and unattended work

T3's Codex provider normally reads the same account/system defaults, but a
client's explicit context configuration can override them. Verify the effective
provider configuration. Use a supported per-thread control when available.
If the current client cannot change context for a running thread, explain that
limitation and arrange an explicit provider-session resume with the approved
settings. A child CLI process or editing a config file does not prove the
current thread changed. Do not restart T3 or change shared service environment
to satisfy a one-thread approval.

Independent scheduled Codex tasks use the account/system defaults. They cannot
request interactive approval mid-run; approval must be explicit in the task's
authorized configuration, and an unavailable override must be reported rather
than silently enabling it globally. Larger context does not change model,
service tier, reasoning effort, tool permissions, or maintenance authority.
