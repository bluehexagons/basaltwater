# Create and control work

[Documentation index](agent-tasks.md)

## Create and control work

The Agents dashboard is the starting page. It summarizes recent runs, tasks
needing review, maintenance status, operator inbox feedback and runtime
readiness. **Tasks**, **Run history**, and **Readiness** are views within
Agents; **Agent tools**, **Inbox**, and **Maintenance** are neighboring tabs.
Dashboard summaries use saved state and do not launch diagnostics or prompts.
Run history is available at `/agents?view=history` and retains the same
receipts and controls as task history opened from a run link.

New tasks and ordinary templates default to **Once**. Guided system and data
tools also prepare one-time work by default. Templates on the Maintenance tab
are optional one-time reviews; opening one prepares a task for review and does
not create a schedule. The explicitly named
**Daily maintenance checks** template supplies a daily routine; other recurrence
requires a deliberate selection. Existing schedules keep their saved cadence.

The Tasks view shows each task's latest summary and pending follow-up badge.
Finished one-time tasks move into **Completed one-time tasks** unless **Keep in
saved tasks** is selected. Their receipts and results remain accessible. When
the saved-task limit is reached, the oldest finished, unpinned one-time control
can be reclaimed; drafts, queued/running work, goals and schedules are retained.
Follow-ups link back to their original task history.

Host service review templates use `mode: "workspace"`, `workspace:
"run-directory"`, and `network: true`. Command network access permits local
service-manager sockets and loopback readiness probes that inspection mode can
block. Each run receives an empty private writable directory rather than write
access to the source home or checkout; temporary writes remain disabled. These
prompts request read-only diagnostics and recommendations, without host repairs.
Filesystem-only storage reviews retain inspection mode and select
`storage_operation: "review"` for bounded host inventory/native preview evidence
before the sandboxed model starts. Cancellation is checked again before model
launch; if it arrives after the helper returns, its evidence is retained and
the provider is skipped. The
[storage cleanup workflow](storage-cleanup.md) gathers bounded metadata,
ranks concrete candidates and proposes a separately approved follow-up.
Saved tasks keep their original settings; create a new task from the refreshed
template to replace an
older inspection task. The panel identifies restricted saved host-check presets
and links to their current template in task settings, copied/reused forms and
run reports. Older guided system checks link to their current preparation form.
Other blocked inspections link to the template catalog. These links
only open a form; review and submit the replacement, then pause the old schedule
to avoid duplicate runs. **Reuse run settings** preserves the old permissions.
An established workspace binding cannot be changed in place.

For custom host reviews, use `agent doctor` with explicit capabilities.
`agent manifest` requires a project checkout and is not a host readiness check.
An inaccessible service manager leaves maintenance state unknown rather than
proving timers are disabled. The development doctor's Node probes need no
temporary writes or package-manager downloads.

The runner explicitly selects a read-only profile or a fresh workspace profile
on current Codex, and supplies matching legacy sandbox settings for older
versions. A generated profile name prevents unrelated configured profile roots
from being merged into the task. Administrator requirements still apply:
an account restricted to an allowlist of named profiles can reject workspace
tasks; the runner does not retry with broader access or weaken that policy.
User-service queries use systemd's ordinary D-Bus connection
(`SYSTEMCTL_FORCE_BUS=1`) because its private manager connection cannot verify
peer PIDs across Codex's PID namespace. Each invocation sets the task account's
`HOME`, `CODEX_HOME`, runtime directory, D-Bus address and forced-bus setting in
both the provider environment and Codex's shell environment overrides. This
prevents a caller's stale bus address or core-only environment inheritance from
breaking live diagnostics. Explicit administrator policies and shell include
filters still apply; the runner does not widen environment inheritance.

Use **Host maintenance review** for a one-time live check, or **Daily
maintenance checks** to prepare a recurring review. Both use a private run
directory and command network access, with prompts prohibiting host repairs.
Restricted saved inspection tasks can still report unavailable service state;
opening their current template prepares the access needed for a new check.
Repair proposals need exact targets, permitted changes, verification, and
rollback. Root actions still use the configured privilege approval workflow;
workspace mode does not grant root access.

`writable_directories` adds up to eight specific, existing account-owned
directories to workspace mode. These grants accompany the execution directory,
including when it is private scratch space. Whole-home grants, symlinks,
shared writable directories and credential/configuration/control stores are
rejected. The runner reapplies these grants when resuming a conversation.
For `mode: "workspace"` with `workspace: "existing"`, the working directory
must also be a specific project directory: the account's home and any path that
overlaps `.ssh`, `.codex`, `.agents`, `.config`, or `.local/state/basaltwater`
are rejected, including protected descendants and their ancestors. Creation,
updates, and prelaunch checks enforce this boundary. Older saved tasks remain in
the inventory, but an unsafe task fails before workspace preparation or provider
launch. A draft without an established context can be edited to use a safe
project directory through CLI or MCP. If an earlier run established its source
binding, create a new task with the safe directory and pause or remove the old
task. `reset-context` retains that source binding; it does not enable changing
the old task's directory. Retained receipts remain readable after removal.
Inspection can still use the home directory. Managed `directory` and
`run-directory` workspaces can keep the home as their source because execution
uses private empty task directories. Fixed native storage operations also
intentionally use the home as their target and do not launch Codex.
`operator_requirements` records unresolved prerequisites as up to eight short
lines. A task with unresolved prerequisites stops before provider launch;
review the evidence and clear resolved entries deliberately. For a follow-up,
**Approve once** confirms the displayed checklist and records it in the approval
decision; the queued task no longer carries those entries as unresolved blockers.
Saving a draft preserves them and does not confirm completion.

Prepare a JSON file such as `task.json`:

```json
{
  "title": "Repair project checks",
  "prompt": "Reproduce and repair the failing project checks. Preserve behavior and unrelated work. Leave changes for review.",
  "directory": "~/repos/project",
  "mode": "workspace",
  "interval": "once",
  "persistence": "checkpoint",
  "workspace": "worktree",
  "continue_until_done": true,
  "completion_criteria": "The repository's documented focused checks pass; record commands, results and remaining coverage gaps.",
  "max_runs": 10,
  "continuation_minutes": 5,
  "timeout_minutes": 30,
  "temporary_files": true,
  "network": false
}
```

```bash
basaltw agent tasks capabilities --json
basaltw agent tasks create task.json --draft --request-key project-checks --json
basaltw agent tasks show TASK_ID --json
basaltw agent tasks run TASK_ID --json
basaltw agent tasks resume TASK_ID --json
basaltw agent tasks list --json
basaltw agent tasks list --summary --history-limit 5 --json
basaltw agent tasks history TASK_ID --limit 40 --json
basaltw agent tasks receipt TASK_ID RUN_ID --json
basaltw agent tasks pause TASK_ID --json
basaltw agent tasks cancel TASK_ID --json
```

Creation defaults to a draft. `--run-now` queues an immediate invocation and
activates repetition; `--schedule` activates it after one interval, or at
`start_at` when supplied. Manually
running a draft does not activate its schedule. `update TASK_ID JSON_FILE`
changes only supplied settings, preserving activation and durable usage.
Persistence changes reset the binding when the next invocation prepares its
context. A failed settings write leaves the previous checkpoint intact;
inspection reports a pending reset after a successful mode change.
An idempotent creation key returns the same saved task for matching settings;
different settings using that key are rejected.

Create and update also accept `-` for strict JSON on stdin, bounded to 64 KiB.
Duplicate keys, non-finite numbers, and excessive nesting are rejected in files,
stdin, and MCP requests. Generate JSON with a serializer when prompts contain
quotes or shell metacharacters; no intermediate settings file is required.

`start_at` accepts a UTC epoch number or an ISO date with timezone. For example,
`"interval": "once", "start_at": "2026-11-01T09:00:00-06:00"` schedules one
invocation for that instant. An overdue first run catches up once. A manual
run consumes a one-time schedule. Repeating tasks use the first-run time
to anchor their elapsed cadence; a Once goal then continues at its configured
goal interval. Rescheduling a finished one-time task requires a future
`start_at` and explicit Resume. Event tasks leave `start_at` unset.

Compact listing omits prompts, wake payloads and run output while preserving
controls, counters and stop reasons. `--history-limit 0` omits recent runs.
`receipt` reads full retained settings, checkpoint and verification evidence.
History and receipts remain accessible after removing their task, using its
saved ID; an absent receipt fails without creating storage or starting work.

`reset-context TASK_ID` clears the checkpoint and session binding while the
task is idle. It retains workspaces and receipts. `delete TASK_ID` removes
the saved task while preserving account-owned context, evidence and files.
Changing an established conversation or managed workspace's source or workspace requires
a new task. Copying task settings never copies its session or context.
