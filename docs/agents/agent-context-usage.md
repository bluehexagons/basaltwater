# Scope agent context

[Documentation index](agent-context.md)

## Reduce context growth

Load one applicable skill entrypoint, then only references needed for the current
operation. Use [scoped discovery](agent-environment.md), such as
`basaltw agent manifest . --application blender --json`, instead of repeatedly
loading the complete environment and skill inventory.

Keep verbose output in private local artifacts. The project-development skill's
`scripts/capture_output.py` runs a command after `--` and returns only status,
duration, byte counts and log paths; use the script from the active skill's
directory. Retrieve bounded failure excerpts rather than complete logs.
[Desktop observations](../workstations/desktop-automation.md#keep-observation-output-small)
support `--save-output` for full JSON artifacts and short previews, preserving
errors and incomplete-scan indicators.

Keep a short current task note with objective, decisions, unresolved issues,
job handles, validation and artifact paths. Handoffs and repeated review rounds
should carry a self-contained brief and relevant findings, responses and
unresolved objections, with detailed evidence in files. Delegate independent
[bounded work](agent-subagents.md) with explicit ownership and acceptance checks;
only one worker controls a shared desktop session.

Also check effective provider settings. Pricing review dates are advisory and
never shrink a context budget. How quickly observations and history fill the
window is a separate concern. A repository default alone does not establish the
settings used by an existing session.

## What setup installs and configures

Agent VM and workstation profiles select GitHub CLI. Choose one or more
providers explicitly with `--codex`, `--claude`, or
`--opencode`. Vendor installers and the managed updater supply the
CLIs; provider configuration is reconciled before a selected T3
Code interface starts. Model selection, reasoning effort, authentication, and
permissions retain their existing configuration.

| Setup | Context configuration |
| --- | --- |
| Debian agent profiles and other profiles selecting these tools | Codex defaults in `/etc/codex/config.toml`; owned Claude/OpenCode defaults refreshed after payload seeding |
| Local `agent_cachyos` | Owned defaults refreshed in the desktop user's provider files, before starting T3; no system-wide policy |
| Ubuntu `server_wsl` | Owned defaults refreshed in the selected Linux user's provider files; Windows-native providers have separate configuration |

Existing user context policies are preserved, including larger budgets,
disabled auto-compaction (including Claude's native `autoCompactEnabled`
setting), and smaller custom model limits. For a local Codex or
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
