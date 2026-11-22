# Persistent agent tasks and goals

Use the web panel's **Agents** view, `basaltw agent tasks`, or the installed
stdio MCP tools as the task account. Independent Basaltwater prompts use that
account's Codex CLI and local state. T3 schedules have their own thread,
environment, and permissions.

Start with the scheduling owner and persistence decision. Read the procedure
for the requested operation.

## Choose a topic

<a id="choose-the-scheduling-owner"></a>
<a id="choose-context-and-workspace-persistence"></a>
- [Choose the task owner and persistence](agent-task-ownership.md) T3 versus independent tasks, saved context, and workspace binding.

<a id="create-and-control-work"></a>
- [Create and control work](agent-task-controls.md) Prompts, schedules, controls, permissions, and receipts.

<a id="request-a-follow-up-with-different-access"></a>
<a id="report-environment-difficulties"></a>
<a id="hand-off-current-work-from-t3-or-a-terminal-agent"></a>
- [Follow-ups, access, and handoffs](agent-task-handoffs.md) Authorization, environment difficulties, and carrying current work forward.

<a id="continuation-outcomes-and-budgets"></a>
<a id="independent-worker-and-events"></a>
- [Continuation, budgets, and events](agent-task-execution.md) Bounded goals, independent worker startup, and events.

<a id="mcp-for-codex-and-t3-agents"></a>
<a id="storage-and-recovery"></a>
- [MCP, storage, and recovery](agent-task-runtime.md) Tool registration, account state, and recovery.
