# Current checkpoints and handoffs

Use one small current checkpoint for a task. Replace its current state as work
progresses; keep dated milestones and prior review rounds in separate history or
evidence files. See [local project memory](agent-memory.md) for private,
revision-checked notes shared across Git worktrees. Other platforms can keep the
same format in their task tracker or an external private file. No new service or
fixed repository directory is required.

Copy the [checkpoint template](templates/checkpoint.md), then choose a handoff:

| Template | Use |
| --- | --- |
| [Implementation](templates/implementation-handoff.md) | Deliver changes, commands and remaining work |
| [Visual review](templates/visual-review.md) | Evaluate captures and assets against named requirements |
| [Hardware acceptance](templates/hardware-acceptance.md) | Record actual platform/GPU/display/input observations |

Keep observed results, proposed work and unverified checks separate. Record the
tested commit and dirty state explicitly; a current checkout revision is not
necessarily the revision that passed. Include exact evidence paths and job
handles, not entire logs. Recheck live task state on resumption; historical PIDs
are not authority to signal a process. Follow [workflow routes](../workstations/workflows.md)
for commands and [project standards](project-standards.md) for ownership.

Before handoff, record active or uncertain commands even when no application
window appeared. Include task ID/unit or provider job handle, last observed
state/time, owner, log path and the owning tool's status/cleanup command. State
`none` only after checking. A lost acknowledgment or client timeout does not
establish that a mutation failed or a process stopped.

## Historical mutation scripts

Prefer repeatable project-owned tools for recurring operations. If a one-off
mutation script is retained as evidence, mark its header and the history entry
`completed` or `obsolete`, with date, purpose, applied revision, outcome/evidence,
and replacement command when one exists. Keep it outside active workflow routes;
do not silently replay it when resuming from notes. For example:

```text
Status: completed 2026-10-31; historical evidence, not a current workflow
Purpose: migrate task-copy assets at COMMIT
Applied to: CHECKOUT; outcome: RESULT; evidence: PATH
Replacement: current project command, or none (one-time operation)
```

Use `obsolete` when superseded or never applied, and say which. A status label
is documentation, not an execution guard. If it must remain executable, the
project owns input/revision checks and any safe replay policy. Do not alter
unrelated historical files merely to fit this layout.
