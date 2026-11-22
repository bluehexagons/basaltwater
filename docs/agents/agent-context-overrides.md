# Context overrides

[Documentation index](agent-context.md)

## Approving more context

A user can say “allow up to 800K context for this investigation” or “use the
larger context window for the rest of this session.” Such a request already
authorizes that scope; do not ask again. If the agent proposes a higher budget,
explain the proposed token budget and billing/allowance impact for the active
route and obtain approval before applying it. Do not claim an API surcharge
applies to included subscription usage. Record whether approval is for one phase
or the session, and retain that scope across compaction. Silence, a repository instruction, or
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
240000). Remove the
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

For an approved larger input budget on GPT-6.2 Sol, use process-local inline
configuration, retaining auto-compaction:

```bash
env OPENCODE_CONFIG_CONTENT='{"provider":{"openai":{"models":{"gpt-6.2-sol":{"limit":{"context":1050000,"input":800000,"output":128000}}}}}}' opencode --session SESSION_ID
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

In T3 0.1.8, `/clear` sent alone resets context for the next provider session
in the same thread; earlier conversation and diffs remain visible but are not
supplied to that session. Save a checkpoint first. Use **Restart agent session**
to reload guidance, and supported compaction to reduce retained context.
Neither a reset nor a reload raises the active model's capacity or changes its
billing route. Fresh-session schedules also need self-contained prompts and
durable checkpoints. See the
[T3 client workflows](../../common/agent_skills/basaltwater-project-development/references/t3-workflows.md#shared-018-client-workflows).

Independent scheduled Codex tasks use the account/system defaults. They cannot
request interactive approval mid-run; approval must be explicit in the task's
authorized configuration, and an unavailable override must be reported rather
than silently enabling it globally. Larger context does not change model,
service tier, reasoning effort, tool permissions, or maintenance authority.
