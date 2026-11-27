# T3 schedule migration and recovery

Use the active T3 instructions and tool schemas for native scheduling,
webhooks, goals, and PR watches. Use Basaltwater tasks for independent Codex
jobs with lifetime attempt, deadline, or token limits and execution receipts.
A Basaltwater job has its own context, permissions, and tools. Give each job
one owner. See the [T3 scheduler contract](https://github.com/pingdotgg/t3code/blob/v0.1.10/packages/contracts/src/scheduledTask.ts)
and [project settings](https://github.com/pingdotgg/t3code/blob/v0.1.10/docs/user/project-settings.md)
for native controls.

## Verify time, context, and workspace

Fixed-time schedules use the server's timezone. For one-time jobs, confirm the
intended instant with an explicit UTC offset. A manual run of a one-time job
can leave its scheduled run pending. Check the next run and result before
retrying, pausing, or removing it. After server downtime, inspect active and
missed work before assuming a trigger ran.

A bound schedule follows the thread's provider, model, options, and modes at
dispatch. Review the schedule when those settings change. For unbound jobs,
inspect the saved workspace and provider settings in T3's task settings if
the tool summary omits them. Default worktrees use the repository's default
branch. Verify the workspace before relying on unpublished changes or
isolated writes.

Fresh-session runs need a self-contained prompt and durable checkpoint. On
T3 0.1.10, moving an already-bound task to a new retained fresh-session thread
requires two saves. Pause it, unbind it, and then enable fresh sessions.
Verify each save and the destination workspace before restoring the intended
enabled state. A prompt or cadence edit normally preserves the current
workspace. Inspect binding changes separately.

## Migrate an independent job

Identify the scheduler, job ID, trigger and next due time, active run,
required limits, and retained state. If the replacement supports the job's
requirements, pause the source and resolve any active work before enabling the
destination. Preserve the old run history and carry forward a bounded
checkpoint, so the first trigger can resume without duplicating prior effects.
A scheduling request alone does not authorize an immediate run.

## Webhooks and private secrets

Get signing secrets through the active private secret flow. Give the webhook
only the needed event fields, filters, and replay handling. Treat event
values as input data. Inspect T3's **Deliveries** before retrying downstream
work. A webhook URL can trigger work, so share it privately with the workflow
owner. Public callback reachability and offline delivery storage depend on
the owner's Connect settings. Retiring the schedule and removing the external
hook are separate operations.

For request and binding details, see the [MCP contract](https://github.com/pingdotgg/t3code/blob/v0.1.10/packages/contracts/src/orchestratorMcp.ts)
and [workspace preservation](https://github.com/pingdotgg/t3code/blob/v0.1.10/apps/server/src/mcp/OrchestratorMcpService.ts).

## Durable workflows

T3 0.1.12 adds opt-in Workflows under **Settings → Labs** on the environment
that owns the run. Use a scheduled task for a single prompt on a timer or
webhook. Use a workflow for authorized multi-step T3 work with saved results,
triggers, and explicit human decision points. Use the active instructions
for tool access and authorization. The run and its journal stay on the
starting server, including when an agent step targets a linked environment.

Keep workflow definitions and credentials private. Review the selected
provider, permissions, workspace and trigger before enabling a workflow.
Keep versions used by unfinished runs, reuse the idempotency key when retrying
the same start, and inspect the run and its open question before repeating a
signal. Completed step results survive restarts. Make external writes
retry-safe and verify uncertain effects before resuming an interrupted step.
Basaltwater's independent jobs continue to provide lifetime budgets and
receipts for host-owned work.

On 0.1.13, give a run a subject with `subject(...)` or pass `{ subject }`
when starting it from another workflow. Use the issue, PR, thread ID, or
canonical URL that the run concerns. To recover or review runs for an item,
open its workflow list or use `t3 workflow list --subject URL_OR_THREAD_ID`
on the owning environment. Keep the start key for retry deduplication.

Set `concurrency` to cap whole runs of one workflow across versions. Use
`within("pool-name", { limit: N }, ...)` to cap a section that shares a local
resource with other workflows. Use a consistent limit per pool name on that
server. A queued run is waiting for capacity. Inspect the pool and its
holders before retrying, including any run holding a place during a human
wait. Resume reacquires held places before execution continues. Keep pool
names distinct from capped workflow names, and release a place before
invoking a child that needs the same pool. Avoid nesting `within` calls
for the same pool.
[T3 Workflows](https://github.com/pingdotgg/t3code/blob/v0.1.13/docs/user/workflows.md)
· [Version-matched workflow authoring skill](https://github.com/pingdotgg/t3code/blob/v0.1.13/skills/t3-workflows/SKILL.md)

For planned maintenance, inspect executing steps separately from queued runs
and open human questions. Use a bounded hold for external work that outlives
the step that started it.
[Basaltwater maintenance holds](https://github.com/bluehexagons/basaltwater/blob/main/docs/operations/maintenance-holds.md#agent-maintenance-holds)
