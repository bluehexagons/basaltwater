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
