---
name: basaltwater-agent-tasks
description: Schedule authorized follow-ups, recurring checks, or bounded goals and hand off durable task context.
metadata:
  managed-by: basaltwater
---

# Tasks and goals

Use scheduling only when the request needs deferred, recurring, or continuing
work. A stored task is not additional authority. Choose finite attempts or a
deadline for follow-ups; indefinite recurrence needs authorization for it.

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

A saved schedule does not prove worker readiness. Inspect availability; do not
start an unconfigured detached worker or switch tools to bypass a denied action.
T3 tools and browser state are thread-specific and are not inherited by an
independent task. Cancellation does not undo edits or cancel external requests.
