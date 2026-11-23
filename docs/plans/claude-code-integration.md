# Claude Code integration

## Status

Basaltwater provides opt-in Claude Code installation, updates, context defaults,
skills, and inventory. The account holder signs in through Claude Code on the
target. Native terminal and T3 provider turns still need Basaltwater
qualification. The saved and scheduled task executor supports Codex only.

## Experimental task gate

`--experimental-claude-tasks` records an explicit setup opt-in for restricted
Claude task integrations. It defaults to off, is saved with the setup, and is
forwarded to target setup. `--no-experimental-claude-tasks` revokes the saved
opt-in. It is separate from `--claude`, which selects the terminal installation.
An ordinary patch preserves the saved choice. Every opted-in setup summary,
including dry runs and saved reruns, warns about the support and billing limits.

This gate currently enables no executor. Saved and unattended tasks remain
Codex-only in the CLI, API, MCP, panel, and worker regardless of this setting.
Future restricted Claude task choices must remain hidden and rejected without
the opt-in; the flag alone is insufficient to enable an unimplemented mode.
It neither obtains Anthropic approval nor configures credentials or billing.
Any future executor needs an authorized authentication and billing route under
the terms below. Console usage is metered at API rates and may draw on eligible
Console credits.

Restricted Claude task support is **highly experimental and untested with live
Claude**. Only basic offline code tests are planned for these integrations;
live unattended or API-billed qualification is not planned. Support only models
and modes that fit straightforward native provider features and existing
Basaltwater infrastructure. Reject unsupported settings rather than adding
bespoke model, permission, session, billing, or recovery systems for Claude.
Any future executor remains a separate implementation decision.

## Ready for attended testing

Agent setup now requires an explicit provider selection. `--claude`
selects Claude without installing Codex or starting Codex authentication;
`--t3code-ready` also respects this selection. GitHub CLI remains a profile
utility default. Existing saved provider selections remain explicit on replay.
Windows uses `-AgentTool claude` with the same selection requirement.

Offline regressions cover Claude-only setup/remote/saved configuration and
non-secret environment-override diagnostics through `agent auth status`.
Follow the [attended test procedure](../agents/agent-authentication.md#claude-code-attended-testing)
in a disposable single-user environment. These changes do not qualify provider
execution or enable Claude in unattended task workers. The terms and native
qualification work below remains open.

## Current implementation

| Area | Current behavior | Evidence |
| --- | --- | --- |
| Installation and updates | Explicitly selected setups install Claude Code with Anthropic's installer; the agent updater uses `claude update`. | `common/agent_steps.py`, `lib/agent_cli.py`, `tests/test_agent_tools.py` |
| Authentication | The recommended path is native sign-in on the target. An explicit `.credentials.json` import and rotation mechanism still exists for authorized files. SSH status reports standard-path metadata and selected override names without invoking Claude. | `lib/setup_common.py`, `lib/agent_auth.py`, `docs/agents/agent-authentication.md`, `tests/test_agent_setup.py`, `tests/test_agent_auth_validation.py` |
| Settings and skills | Selected installations receive managed context defaults and the chosen skill catalog under `~/.claude`. Existing user settings are preserved according to the context ownership rules. | `common/agent_context_steps.py`, `lib/agent_skill_bundles.py`, `tests/test_agent_context.py`, `tests/test_agent_skills.py` |
| Agent inventory | The web panel can display Claude Code installation, version, and whether local credential metadata exists. This does not launch Claude or validate provider authentication. | `common/service_tools/web_panel_agents.py`, `tests/test_web_panel_agents.py` |
| T3 Code | Upstream uses Claude Code's target-side login and configuration. Check the selected provider instance and its `CLAUDE_CONFIG_DIR` on that environment. | [T3 Code 0.1.11 Claude provider](https://github.com/pingdotgg/t3code/blob/v0.1.11/docs/user/providers-claude.md) |
| Saved and unattended tasks | Task validation, CLI/MCP capabilities, and execution launch Codex only. Claude is not an executor. | `lib/agent_tasks.py`, `lib/agent_task_api.py`, `lib/agent_tasks_mcp.py`, `docs/agents/agent-tasks.md`, `common/agent_skills/basaltwater-agent-operations/references/unattended-tasks.md` |
| Activity observation | The activity reader recognizes `claude -p` / `claude --print` as one-shot work for activity reporting. This observes an existing process; it does not launch one. | `lib/agent_activity.py`, `tests/test_agent_activity.py` |

The automated suite covers installation and update command construction,
explicit credential staging, settings reconciliation, skill placement, and
activity detection. It does not establish a real Claude login, subscription
billing mode, interactive coding session, or provider-backed task execution.

## Terms and usage boundary

Reviewed on 2026-11-22.

- **Native login.** Anthropic documents end-user sign-in to the unmodified
  Claude Code binary with the user's own subscription, including on
  qualifying hosted platforms. Offering Claude Code in a hosted product is
  subject to Anthropic's commercial terms and product conditions. Use native
  sign-in on a dedicated target account and keep OAuth tokens and one-time
  codes out of Basaltwater's credential staging and logs. Reserve the existing
  file importer for credentials authorized for transfer.
  [Claude Code legal and compliance](https://code.claude.com/docs/en/legal-and-compliance) · [Consumer Terms of Service](https://www.anthropic.com/legal/consumer-terms) · [Commercial Terms of Service](https://www.anthropic.com/legal/commercial-terms)
- **Programmatic use.** Consumer Terms §3(7) restricts automation except
  through an Anthropic API key or where Anthropic explicitly permits it.
  Anthropic's billing guidance says native plan-authenticated `claude -p`
  and Agent SDK use still draw from plan limits. Keep consumer OAuth out of
  saved and unattended Basaltwater jobs. Any future executor needs an
  authorized route such as a customer-owned Claude Console API key or a
  supported cloud provider, with an explicit credential choice and cost
  controls. [Consumer Terms of Service](https://www.anthropic.com/legal/consumer-terms) · [Claude Code legal and compliance](https://code.claude.com/docs/en/legal-and-compliance) · [Agent SDK plan guidance](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan)
- **Billing.** An approved `ANTHROPIC_API_KEY` can take precedence over the
  subscription. Eligible Max, Ultra, and Team plans include monthly API credits
  that the holder claims into a linked Console organization. An eligible
  key-authenticated `claude -p` or SDK run can use those credits. Subscription
  overage uses a separate usage-credit balance when enabled. Anthropic's linked
  billing beta for third-party apps bills a Console organization and is outside
  this plan. [API key precedence](https://support.claude.com/en/articles/12304248-manage-api-key-environment-variables-in-claude-code) · [Monthly API credits](https://support.claude.com/en/articles/17154008-monthly-api-credits-for-max-ultra-and-team-plans) · [Plan usage credits](https://support.claude.com/en/articles/12429409-manage-usage-credits-for-paid-claude-plans) · [Linked billing](https://support.claude.com/en/articles/18467148-use-your-claude-platform-account-in-a-third-party-app)

## Work plan

1. **Qualify native sign-in first.** Follow the attended procedure on a
   disposable single-user host. Verify the selected account and billing mode
   in Claude Code, exercise a read, edit, approval and cancellation in a
   synthetic repository, and verify a fresh session after setup refreshes
   skills and settings. Record an authenticated T3 turn separately if that
   provider route is in scope. Keep private session evidence outside Git.
2. **Define any additional modes.** Write the accepted matrix for interactive plan
   login, commercial API-key use, and any supported enterprise platform. Name
   the account owner, execution identity, billing source, whether a human is
   present, and whether credentials can be reached by another user. Recheck the
   applicable terms, Usage Policy, service-specific terms and supported regions
   before expanding the integration.
3. **Implement only the approved surface.** Keep interactive install,
   configuration, and inventory independent of task execution. If a commercial
   executor is approved, add an explicit provider/auth mode and fail-closed
   validation across task CLI, API, MCP, panel, and worker. Preserve task
   sandboxing, cancellation, timeouts, usage accounting, and receipts. Set
   spending controls with the billing provider as well as local task limits.
4. **Verify changes offline.** Use mocked executables, synthetic credential files
   and temporary homes. Cover provider selection, environment precedence,
   credential handling, and executor rejection across entrypoints. For any
   approved executor, include cancellation, timeout, rate limits, and restart.
   Live unattended or API-billed qualification requires a separate decision,
   authorized credentials, and an approved budget.
5. **Record qualification.** Update operator guidance and the coverage map with
   the observed authentication and execution modes, evidence type, and open cases.

## Acceptance

- The accepted authentication and execution combinations have an owner and a
  current terms/policy basis; ambiguous consumer-plan automation remains
  disabled.
- No shared or unattended path exposes individual account credentials to other
  people, and no plan-authenticated path silently incurs API charges.
- Offline tests cover positive and negative selection paths without network
  access or real credentials.
- Native attended testing proves the account holder can use the intended
  terminal workflow with the correct billing mode and without exposing private
  project data.
- Any later restricted executor requires the explicit setup gate and offline
  boundary tests, and discloses highly experimental support and absent live
  testing wherever offered. Unsupported modes remain unavailable.
- Documentation and the coverage map state the tested revisions, evidence type,
  and remaining provider/account boundaries.

## Test selection

Existing starting points include `tests/test_agent_tools.py`,
`tests/test_agent_setup.py`, `tests/test_agent_profiles.py`,
`tests/test_agent_auth_validation.py`,
`tests/test_agent_context.py`, `tests/test_agent_skills.py`,
`tests/test_agent_activity.py`, and the task boundary/API modules. New tests
should be added to the appropriate domain suite in `run_tests.py`. Select the
full affected suites after implementation; run an account-backed smoke test only
when an account, dedicated environment, and approved authentication mode are
available. No provider-backed Claude Code result is currently claimed.
