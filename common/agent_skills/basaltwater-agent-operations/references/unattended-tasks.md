# Unattended panel prompt tasks

Use this guidance for the Basaltwater web panel's **Agents** screen and prompts
launched by its scheduler. The runner uses the panel account's Codex CLI,
credentials, configuration, and installed skills. T3 Code is optional; Claude
Code and OpenCode are diagnostic inventory entries, not task executors.

Each run fixes `HOME` and `CODEX_HOME` to the panel account, so credentials,
configuration, and the `~/.agents/skills` catalog belong to that account rather
than a caller's custom home. Check the active session's skill list; filesystem
presence does not establish that a skill is enabled or loaded. Use
`basaltw agent manifest /absolute/project --json` to find local entrypoints and
incomplete or differing bundles. Honor disabled skill settings. T3 preview and
other thread-scoped tools are not inherited by this terminal process.

If the prompt authorizes delegation and its tools support it, use the installed
`basaltwater-subagents` session-context reference. Pass worker-specific skill
paths, permissions, unattended status, and the remaining runtime. Join workers
before completing the run; use one agent when worker limits cannot be enforced.
Internal parent/worker messaging is part of authorized delegation when native
tools expose it. The panel has no inbox for injecting follow-up replies into an
active run.
The prompt's model selector accepts supported `none` effort from Codex's cache;
disabled thinking is model-dependent. User-selected settings do not authorize
workers to inherit fast service, `max`, or `ultra` without explicit scope.

## Scope and permissions

Review the prompt and execution settings before saving. A template supplies
starting text, not additional permissions. **Inspect only** uses Codex's
read-only sandbox and should use read-only operations through every integration.
**Workspace changes** permits writes in the selected directory inside the
account's home; command network access and temporary writes are separate options.
Web search is separate from command network access. Configured hooks and MCP
servers have their own permissions; the panel does not sandbox them separately.

There is no interactive operator reply or sandbox escalation. If a command,
socket, credential, cache path, or browser tool is inaccessible, report the
blocked check and evidence still available. Do not start login, bypass the
sandbox, install missing tools, or use a more privileged integration to complete
an inspection. Use documented per-command cache locations within the selected
workspace or permitted temporary directories; do not change global tool settings.
Tool presence and saved setup flags establish availability, not authentication,
service health, or tool access in this session.

Keep source changes, external messages, publication, and privileged requests
within the explicit task prompt. A health check should recommend a repair rather
than perform it. A privileged request may outlive the prompt; stopping the Codex
process does not withdraw it or reverse an approved action.

## Repeated repository work

Prepare a dedicated checkout or managed worktree before scheduling when another
agent could edit the same files. The scheduler runs one prompt at a time, but it
does not coordinate with T3 threads, other terminal agents, or other accounts.
Worktree creation changes Git metadata outside a worktree; do not assume a
workspace sandbox permits creating or removing one during a run.

The editing templates require a clean checkout, including untracked files, and
leave their changes for review. If the previous run left changes, stop the next
editing run and report them; do not stash, reset, clean, commit, or switch branches
to make the schedule proceed. Review and integrate the result separately before
continuing. Read-only reports can inspect a dirty checkout without changing it.
Keep dependency updates within the project's existing manager, lockfiles, and
upgrade policy; report larger migrations as follow-up work.

## Time, outcomes, and evidence

Maximum runtime is configurable from 1 minute to 7 days. Waiting counts toward
that limit, so keep each wait bounded by the time remaining. Do not daemonize
work or hand it to another service to outlive the run. Cancellation and expiry
stop the local process group without undoing edits or remote actions. They do
not guarantee cancellation of work owned by an external MCP server or broker.
Maintenance holds are separate, shared account state and are not automatic.

Schedules run only while the panel service is running. Preset or custom elapsed
intervals are independent of the runtime cap; custom intervals accept 1 minute
to 30 days. Downtime produces at most one catch-up run per overdue schedule.
Intervals crossed during a run are skipped on its existing cadence instead of
immediately repeating the task. Changing the elapsed interval resets its next
deadline; editing other settings preserves it and does not activate a draft or paused
schedule. A restarted active run is marked interrupted. The
default failure limit pauses repetition after three failed or interrupted runs.
Codex exiting successfully means the process completed, not that checks passed:
a report of blocked or unhealthy checks can still have a completed status and
will not trigger automatic failure pausing. Read the output before resuming or
reusing work. Runtime is wall time, not a spending limit or token budget.

Report checks, changes, validation, unresolved findings, and private artifact
paths. Do not put credentials in prompts or evidence; common output redaction
does not remove every secret. Browser and export templates should use isolated
local test data and report missing prerequisites instead of publishing a preview,
downloading export templates, or changing the host. A healthy managed browser
installation does not establish that its MCP tools are exposed to this run.

See the [panel guide](https://github.com/bluehexagons/basaltwater/blob/main/docs/WEB_PANEL.md#agent-prompt-tasks)
for the form controls, template catalog, and retained-history limits.
