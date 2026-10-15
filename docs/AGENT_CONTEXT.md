# Agent context budgets and pricing

Basaltwater configures earlier automatic compaction for coding tools. Larger
context can increase both the number of tokens billed and the price per token.
The defaults aim to stay below known pricing thresholds, with user-controlled
overrides for a task phase or the rest of a session.

## What setup installs and configures

Agent VM and workstation profiles normally select GitHub CLI and Codex. Claude
Code and OpenCode are opt-in agent tools. Vendor installers and the managed
updater supply the CLIs; provider configuration is seeded before a selected T3
Code interface starts. Model selection, reasoning effort, authentication, and
permissions retain their existing configuration.

| Setup | Context configuration |
| --- | --- |
| Debian agent profiles and other profiles selecting these tools | Codex defaults in `/etc/codex/config.toml`; missing Claude/OpenCode settings filled after payload seeding |
| Local `agent_cachyos` | Missing defaults filled in the desktop user's provider files, before starting T3; no system-wide policy |
| Ubuntu `server_wsl` | Missing defaults filled in the selected Linux user's provider files; Windows-native providers have separate configuration |

Existing user values take precedence and are preserved on setup reruns,
including intentionally larger budgets or disabled auto-compaction. OpenCode's
existing `limit` object is preserved as a whole, so setup cannot enlarge a
custom smaller window by filling its missing fields. Invalid or unsafe files
fail instead of being replaced. JSONC values are preserved; when adding missing
settings, setup rewrites JSONC as formatted JSON in the same `.jsonc` file.
An existing Claude global/per-model window or compaction environment setting
keeps that entire context policy: adding another setting could otherwise
override the user's intended effective window.
Rerunning setup/refresh applies these defaults; updating only a CLI does not.

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
| Claude Code | `autoCompactWindow: 200000`; `modelSettings["claude-haiku-5-5"].autoCompactWindow: 100000`; `env.CLAUDE_AUTOCOMPACT_PCT_OVERRIDE: "80"` | A conservative general budget, with earlier Haiku compaction around 80K rather than its native 1M window |
| OpenCode | `compaction.auto: true`; OpenAI model `limit.input: 240000`; Haiku 5.5 `limit.input: 80000` | Lower the input budget used for compaction while retaining the native total context/output limits |

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

OpenCode settings live in `~/.config/opencode/opencode.json` or its existing
`.jsonc` alternative. The seeded models are `openai/gpt-6.1-sol`,
`openai/gpt-6-astra`, `openai/gpt-6-luna`, and
`anthropic/claude-haiku-5-5`. Other model/provider IDs retain their upstream
limits. Extend this list only after checking the actual provider's pricing and
capacity. Current OpenCode also leaves its own compaction buffer below the
input limit, so it can compact earlier than these budgets.

Control references: [Codex configuration](https://learn.chatgpt.com/docs/config-file/config-reference),
[Claude model/context configuration](https://code.claude.com/docs/en/model-config),
[Claude percentage override](https://code.claude.com/docs/en/env-vars),
[OpenCode configuration](https://opencode.ai/docs/config/),
[OpenCode schema](https://opencode.ai/config.json), and
[OpenCode compaction implementation](https://github.com/anomalyco/opencode/blob/dev/packages/core/src/session/compaction.ts).

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
240000). Remove the context-window override to return to the model's native
default. Raising a configured window never grants access to a larger model.

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
