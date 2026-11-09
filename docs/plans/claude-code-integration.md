# Claude Code integration

## Status

Optional Claude Code installation, configuration, skills, credential handling,
and inventory are implemented. Basaltwater's saved and scheduled task executor
is Codex-only. Claude Code provider execution has not been qualified. This plan
defines the compliance gate and test work before any wider execution support is
considered.

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
Operators must have appropriate Anthropic pre-approval or an authorized API
route for uses that require it, and accept separate API-rate charges when using
that route.

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
| Authentication | Claude credentials are not imported automatically. Setup and `basaltw agent auth set` can explicitly transfer a `.credentials.json` file to a target account. Readiness and the panel report local credential metadata, not a successful provider login. | `lib/setup_common.py`, `lib/agent_auth.py`, `docs/agents/agent-authentication.md`, `tests/test_agent_setup.py`, `tests/test_agent_auth_login.py` |
| Settings and skills | Selected installations receive managed context defaults and the chosen skill catalog under `~/.claude`. Existing user settings are preserved according to the context ownership rules. | `common/agent_context_steps.py`, `lib/agent_skill_bundles.py`, `tests/test_agent_context.py`, `tests/test_agent_skills.py` |
| Agent inventory | The web panel can display Claude Code installation, version, and whether local credential metadata exists. This does not launch Claude or validate provider authentication. | `common/service_tools/web_panel_agents.py`, `tests/test_web_panel_agents.py` |
| Saved and unattended tasks | Task validation, CLI/MCP capabilities, and execution launch Codex only. Claude is not an executor. | `lib/agent_tasks.py`, `lib/agent_task_api.py`, `lib/agent_tasks_mcp.py`, `docs/agents/agent-tasks.md`, `common/agent_skills/basaltwater-agent-operations/references/unattended-tasks.md` |
| Activity observation | The activity reader recognizes `claude -p` / `claude --print` as one-shot work for activity reporting. This observes an existing process; it does not launch one. | `lib/agent_activity.py`, `tests/test_agent_activity.py` |

The automated suite covers installation and update command construction,
explicit credential staging, settings reconciliation, skill placement, and
activity detection. It does not establish a real Claude login, subscription
billing mode, interactive coding session, or provider-backed task execution.

## Terms and usage boundary

Terms checked on 2026-11-07:

- Anthropic's [Consumer Terms of Service](https://www.anthropic.com/legal/consumer-terms), effective 2025-10-08, govern individual Claude plans and incorporate the Acceptable Use Policy. Section 2 says account login information and credentials may not be shared and the account may not be made available to anyone else. Section 3(7) prohibits access through automated or non-human means, except when using an Anthropic API key or where Anthropic explicitly permits it.
- Anthropic's [Commercial Terms of Service](https://www.anthropic.com/legal/commercial-terms), effective 2025-06-17, govern API keys and commercial offerings that reference those terms. Section A.1 permits using the services to power products and services, subject to the contract, incorporated policies, service terms, and applicable region requirements. API use has its own billing and customer responsibilities; it is not included in an individual Claude subscription.
- Anthropic's [Claude Code plan guidance](https://support.anthropic.com/en/articles/11145838-using-claude-code-with-your-max-plan) describes Claude Code as available with eligible individual plans for terminal coding workflows. It also warns that `ANTHROPIC_API_KEY` takes precedence over plan login and can result in API charges. Check the current plan guidance and legal documents again before live qualification because product plans and incorporated terms change.

Project decisions pending that review:

1. Allow manual, user-attended Claude Code use by the account holder as the
   initial integration target, subject to the account's applicable terms.
2. Do not use consumer-plan credentials for Basaltwater scheduled jobs,
   unattended task workers, or other non-human `claude -p` invocation. The
   Consumer Terms' automation restriction is the blocker; keep Claude absent
   from task executor choices unless Anthropic explicitly confirms this mode is
   permitted or the implementation uses a separately authorized API/commercial
   route.
3. Do not deploy consumer credentials to shared task accounts, multi-user
   hosts, or workflows that make the account available to another person. The
   existing credential-file transfer is explicit and permission-restricted, but
   that does not itself establish that a proposed recipient or execution mode is
   allowed by Anthropic's account terms.
4. Treat API-key/commercial execution as a distinct opt-in design with separate
   credentials, billing, policy checks, and user-visible cost controls. Never
   let a plan-authenticated test fall through to an environment API key.
5. Recheck the currently incorporated Usage Policy and Service Specific Terms,
   supported-region rules, data rights, and account type before enabling any
   provider-backed feature. Escalate unclear use of consumer-plan credentials in
   a dedicated single-user remote VM to Anthropic or qualified counsel. This is
   an engineering gate, not a legal opinion.

Until these decisions are resolved, Claude Code remains an optional installed
terminal tool and diagnostics may inventory it; unattended Basaltwater task
execution remains Codex-only.

## Work plan

1. **Close the terms gate.** Recheck the linked Consumer and Commercial Terms,
   current Usage Policy, Service Specific Terms, supported regions, and Claude
   Code authentication guidance. Record whether attended remote use, credential
   transfer to a dedicated account, consumer-plan `-p` calls, and API-backed
   task execution are in scope. If Anthropic's wording leaves an execution mode
   ambiguous, keep it excluded pending written confirmation.
2. **Define supported modes.** Write the accepted matrix for interactive plan
   login, commercial API-key use, and any supported enterprise platform. Name
   the account owner, execution identity, billing source, whether a human is
   present, and whether credentials can be reached by another user. Avoid a
   generic `claude` provider switch that hides those distinctions.
3. **Harden credential and billing boundaries.** Confirm explicit opt-in for
   every credential transfer, single-user recipient ownership, file permissions,
   cleanup, redaction, and revocation guidance. Ensure environment API keys are
   detected and never silently override the selected subscription mode. Do not
   copy credentials as part of a generic active-config operation.
4. **Implement only the approved surface.** Keep interactive install,
   configuration, and inventory independent of task execution. If a commercial
   executor is approved, add an explicit provider/auth mode and fail-closed
   validation across task CLI, API, MCP, panel, and worker; do not accept
   consumer-plan credentials for unattended execution without explicit
   permission. Preserve task sandbox, cancellation, timeout, usage accounting,
   receipts, and existing executor behavior.
5. **Add offline regression coverage.** Use mocked executables, synthetic
   credential files, and temporary homes. Cover install/update and setup
   selection; credential allow/deny and permissions; plan-versus-API selection;
   API-key environment precedence; executor rejection in CLI/API/MCP/panel;
   cancellation, timeout, rate-limit and restart behavior for any approved
   executor; and activity detection for interactive versus one-shot processes.
   No test may call Anthropic or contain real credentials.
6. **Run attended native qualification.** When an eligible plan is available,
   use a disposable single-user test environment and a benign synthetic
   repository. Have the account holder authenticate interactively, confirm the
   selected account and `/status` billing mode, verify no API key is present,
   exercise read, edit, approval, cancellation, and context settings, then log
   out and remove test state. Keep private account and raw session evidence out
   of Git.
7. **Keep restricted execution experimental.** No live unattended or API-billed
   testing is planned. Use basic offline tests and disclose the missing live
   coverage during setup and in operator guidance. Any later live qualification
   requires a separate decision and authorized credentials/budget. Do not treat
   Basaltwater's local token budget as a provider-side dollar cap.
8. **Update operator guidance and qualification records.** Document supported
   authentication and execution modes, exclusions, billing boundaries, and
   which tests are mocked versus provider-backed. Update the coverage map only
   after evidence exists.

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
`tests/test_agent_setup.py`, `tests/test_agent_auth_login.py`,
`tests/test_agent_context.py`, `tests/test_agent_skills.py`,
`tests/test_agent_activity.py`, and the task boundary/API modules. New tests
should be added to the appropriate domain suite in `run_tests.py`. Select the
full affected suites after implementation; run an account-backed smoke test only
when an account, dedicated environment, and approved authentication mode are
available. No provider-backed Claude Code result is currently claimed.
