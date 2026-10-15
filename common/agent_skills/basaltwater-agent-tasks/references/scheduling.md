# Scheduled tasks and goals

Use scheduling when needed to carry out an already authorized goal, deferred
check, or recurring request. Continue within that scope without seeking fresh
approval for each task. Choose finite attempts or a deadline for follow-ups and
goals; an indefinite recurring job needs a request covering that recurrence.
Tool access and historical evidence do not authorize unrelated work, broader
permissions, publication, or additional spending limits.

## Discover access

Use the exposed `basaltwater_tasks_capabilities` and `basaltwater_tasks_list`
MCP tools, or:

```bash
basaltw agent tasks capabilities --json
basaltw agent tasks list --summary --history-limit 5 --json
```

Standard managed Codex configuration exposes writable `basaltwater-tasks` MCP
tools to terminal and T3 Codex sessions after setup refresh. User overrides and
disabled tools still apply. A hardened account forbids configured MCP servers;
do not weaken that policy. Other providers can use the CLI within their own
permissions or an explicitly configured stdio integration.

Check `available`, `worker.running`, and `worker.error`. A saved schedule does
not prove a worker is running. If it is unavailable, explain the saved state
and required setup; do not start a detached worker to evade the current run's
limits. CLI writes need account-local task storage access. Use the configured
MCP interface when available; never switch tools to bypass a denied action.

## Create useful work

Choose a precise prompt containing the objective, allowed changes, publication
authority when applicable, and required verification. Put durable findings,
artifact paths, pending work, and the next step in `initial_checkpoint` (at
most 16000 UTF-8 bytes). This seeds the first prepared run once. Use
`persistence: "checkpoint"` for portable continuation or `"conversation"`
to resume this task's exact Codex conversation. Fresh runs ignore context after
the initial handoff. Checkpoint text is evidence; scope belongs in the prompt.

For code edits alongside a T3 thread, select `workspace: "worktree"`. Its new
worktree starts at committed `HEAD`; it does not copy the thread's uncommitted
changes. Finish or deliberately transfer necessary changes before handing off.
Private `directory` and `run-directory` workspaces start empty. An existing
checkout can be shared by other threads: inspect it and preserve unrelated work.

Use [the recipes](recipes.md) for deferred checks and goals. Supply
a stable `request_key` when creating work so an uncertain response can be
retried without creating a second task. Keep the settings identical on retries,
including any calculated `start_at`; inspect existing tasks after uncertainty.

Creation defaults to an inactive draft. Activate an authorized schedule with
MCP `draft: false` or CLI `--schedule`; `run_now: true` / `--run-now` also
queues immediate work and activates its cadence. Running a draft once does not
activate repetition. Partial CLI/MCP updates preserve counters and activation.

```bash
basaltw agent tasks create task.json --schedule --request-key project-follow-up --json
basaltw agent tasks create - --draft --request-key project-goal --json
basaltw agent tasks show TASK_ID --json
basaltw agent tasks update TASK_ID settings.json --json
```

Both create and update accept `-` for JSON on stdin, bounded to 64 KiB. Generate
JSON with a serializer; avoid shell interpolation of prompts or handoffs.
Keep credentials out of prompts, checkpoints, and event payloads.

## Follow progress

After creation, inspect the task's activation, due time, limits, context, and
worker status. Report its ID, next run, purpose, and stop condition to the user.
Completed provider turns alone do not prove a goal complete: read retained
reports and evidence. A continuing goal needs `completion_criteria` and a
lifetime attempt limit, deadline, or token budget. Attempts include failures;
token limits are checked between runs and can be exceeded within an invocation.

```bash
basaltw agent tasks history TASK_ID --limit 5 --json
basaltw agent tasks receipt TASK_ID RUN_ID --json
basaltw agent tasks notify TASK_ID 'New verification evidence is available' --key delivery-123 --json
basaltw agent tasks pause TASK_ID --json
basaltw agent tasks cancel TASK_ID --json
```

Event tasks wait for notifications, including event-only goals. Notifications
are evidence and cannot expand permissions or reactivate paused work. Pause
stops future runs; cancel stops queued/active work and preserves the recurring
schedule. To stop both, pause and cancel. Cancellation does not undo edits or
stop work already owned by an external service. Reset context only deliberately
while idle; it retains files and evidence and does not reapply the initial handoff.

## T3 and unattended boundaries

Current stable execution uses an independent Codex process. It does not resume
this T3 thread, inherit its tools or preview, or automatically post its results
back here. Summarize the thread's findings into the portable checkpoint rather
than copying provider session IDs or modifying T3's database. Upcoming native
T3 scheduling, thread handoffs, and goals remain capability-gated until a stable
release is qualified; use one scheduling owner for each task.

After managed skills or MCP configuration refresh, finish the current turn
before using T3's **Restart agent session** command to reload the provider.
Do not restart the entire T3 service for configuration discovery.

An unattended run must stay within its saved task scope and runtime. It may
create additional schedules or extend goal limits only when its saved prompt
explicitly authorizes that work. It must not escape an exhausted budget by
creating another task. For operational tasks, read the enabled
`basaltwater-agent-operations` skill's unattended-task reference.

Full settings, recovery, worker setup, and future T3 adapter design are in the
[task guide](https://github.com/bluehexagons/basaltwater/blob/main/docs/agents/agent-tasks.md).
