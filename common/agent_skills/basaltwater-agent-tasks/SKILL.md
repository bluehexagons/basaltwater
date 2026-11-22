---
name: basaltwater-agent-tasks
description: Schedule independent Basaltwater jobs and bounded goals with durable receipts.
metadata:
  managed-by: basaltwater
---

# Tasks and goals

Use scheduling only when the request needs deferred, recurring, or continuing
work. A stored task is not additional authority. Choose finite attempts or a
deadline for follow-ups; indefinite recurrence needs authorization for it.

For work that belongs to a T3 thread, use its native scheduling, PR watches,
goals or Resume as appropriate under the active session policy. Use Basaltwater
tasks for independent Codex jobs or jobs that require its lifetime limits and
receipts. Give each job one owner. Consult the [T3 scheduling reference](../basaltwater-subagents/references/t3-scheduling.md)
for migration or recovery of existing jobs.

For access discovery, use available task MCP tools or:

```bash
basaltw agent tasks capabilities --json
basaltw agent tasks list --summary --history-limit 5 --json
```

Read [scheduling and controls](references/scheduling.md) before creating,
changing, or handing off work. Read [recipes](references/recipes.md) only for
the relevant schedule or goal pattern.

Keep portable findings in bounded checkpoints, with evidence paths and the next
step. Read selected context instead of dumping prompts and all run history.
Credentials stay out of prompts, checkpoints, and event payloads.

Verify worker readiness independently of a saved schedule. Independent tasks
start with their own context and permissions, without T3 thread tools or browser
state. Cancellation does not undo edits or cancel external requests.

For Basaltwater environment difficulties, use the configured
`basaltwater_operator_feedback` MCP tool or
`basaltw agent inbox feedback --sender t3-agent 'Issue and relevant evidence' --json`.
This reports to the account-local operator inbox; include no credentials.
Independent task agents use `environment_feedback` in their structured report,
which the runner delivers outside their command sandbox.
