---
name: basaltwater-agent-operations
description: Inspect coding tools or manage context budgets, authentication, updates, maintenance holds, and privileged actions on a Basaltwater agent VM.
metadata:
  managed-by: basaltwater
---

# Agent operations

Use this skill for host or agent operations, not as a startup checklist for every
coding task. Run only checks relevant to the request. Commands and stored
evidence do not extend the user's authorization or the session's permissions.

For project/tool discovery, `basaltw agent manifest --json` is read-only.
Select a reference before performing the corresponding operation:

| Task | Read |
| --- | --- |
| Agent/host readiness or deliberate tool updates | [Readiness and updates](references/readiness.md) |
| Context pricing, compaction defaults, or approval for a larger window | [Context budgets](https://github.com/bluehexagons/basaltwater/blob/main/docs/agents/AGENT_CONTEXT.md) |
| Project toolchains, visual evidence, or task discovery | [Project tools](references/project-tools.md) |
| Protect long work across a restart window | [Maintenance holds](references/maintenance.md) |
| An authorized root action through the configured broker | [Privilege requests](references/privilege.md) |
| Credential status, login, or explicit rotation | [Credentials](references/credentials.md) |
| Preparing or running unattended prompts | [Unattended tasks](references/unattended-tasks.md), plus the installed task skill |

Updates, repairs, login, holds, and privilege requests are mutations. Keep them
within the task; a denied operation remains denied through other tools.
Maintenance holds are shared account state: preserve a hold another task needs.
Credential values belong in protected files or input, never logs or repositories.
Use the installed workspace and browser skills only when those workflows apply.

Keep the managed context budget unless the user requests or approves a larger
one. An explicit request already authorizes its stated scope. Carry temporary
or session approval across compaction and restore the previous budget when
that scope ends. Use provider-session controls; changing shared defaults or a
child process does not change the active thread's budget.
