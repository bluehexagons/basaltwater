# Unattended panel prompt tasks

Use this guidance for the Basaltwater web panel's **Agents** screen and prompts
launched by its scheduler. The runner uses the panel account's Codex CLI,
credentials, configuration, and installed skills. T3 Code is optional; Claude
Code and OpenCode are diagnostic inventory entries, not task executors.

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

Schedules run only while the panel service is running. A missed interval produces
at most one catch-up run; a restarted active run is marked interrupted. The
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
