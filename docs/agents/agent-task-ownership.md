# Choose the task owner and persistence

[Documentation index](agent-tasks.md)

Use **Agents** in the web panel or `basaltw agent tasks` as the task account to
review agent activity, run saved prompts, continue a bounded goal, or wake scheduled work with an
external event. Terminal Codex and agents inside T3 Code can use the same CLI
or stdio MCP API. All three surfaces share account-local state and controls.
Current execution uses Codex; installing T3 does not change the executor.
Fixed host storage operations run without a model, as described in the
[storage cleanup workflow](storage-cleanup.md#host-evidence-and-approved-cache-operations).
The managed `basaltwater-agent-tasks` skill makes scheduling and goal handoffs
discoverable in agent catalogs. Agents can schedule necessary follow-ups within
the user's authorized scope; an indefinite recurring job needs authorization
covering that recurrence.

## Choose the scheduling owner

T3 Code provides native scheduling. In an active T3 session, use its
`schedule_task` for authorized recurring work returning to that conversation,
another T3 provider, or a supported webhook. The shipped
[T3 scheduling reference](../../common/agent_skills/basaltwater-subagents/references/t3-scheduling.md)
covers structured schedules, server timezone, current-thread versus fresh-thread
binding, inherited model/options, controls, signing secrets, and Connect URLs.
`schedule_task` updates by `scheduledTaskId`. In 0.1.8, `freshSession: true`
resets provider context each run while retaining one thread; prompts need
self-contained instructions and durable checkpoints. MCP-created unbound tasks
prefer the primary remote's default branch with remote/local `main`/`master`
fallbacks; they do not continue the caller's feature branch. Inspect custom
workspace/model settings in T3's task settings because
the MCP task summary omits them. Webhook filters and replay/coalescing keys can
discard irrelevant or duplicate events before launching a provider turn; the
reference includes a scoped recipe and delivery troubleshooting.
Use T3's PR watcher for requested PR monitoring instead of creating a timer.
Optional device-local [attention alerts](../../common/agent_skills/basaltwater-t3code/references/operations.md#attention-alerts)
can notify an owner while T3 is open; they do not wake tasks or replace receipts.

Use Basaltwater tasks for independent Codex execution, one-shot follow-ups, and
goals needing its attempt/deadline/token limits, checkpoints, and receipts.
T3's stable schedule types do not supply those lifetime goal limits. Choose one
scheduler per job and inspect its own state when stopping work; the panel and
CLI here do not control T3 automations. A T3 recurring task is active work, so
do not turn a finite request into indefinite recurrence. The settings and
recovery procedures below describe the Basaltwater executor.

## Choose context and workspace persistence

These are independent settings:

| `persistence` | Behavior |
| --- | --- |
| `fresh` | Start a new conversation from the saved prompt on every run; default for ordinary tasks |
| `checkpoint` | Start a new conversation with the latest saved findings and next steps; default for a CLI goal when persistence is omitted |
| `conversation` | Resume the exact recorded Codex session, with the current scope and permissions reapplied |

Keeping fresh Codex session history only retains those independent sessions;
it does not resume them. Conversation persistence always retains its session.
The runner never selects an unrelated session with `--last`, and a missing
session fails rather than silently creating a replacement.

| `workspace` | Behavior |
| --- | --- |
| `existing` | Use the selected source directory; default |
| `directory` | Reuse an initially empty private task directory |
| `worktree` | Create and reuse an owned Git worktree from the source repository's committed `HEAD` |
| `run-directory` | Create an empty private directory for every run |

Managed workspace provisioning requires workspace mode. Empty directories do
not copy source files; the prompt receives both source and execution paths.
Conversation persistence requires a stable workspace and rejects
`run-directory`. Worktrees do not fetch refs, change the primary checkout, or
copy its uncommitted changes. Ownership is rechecked before each invocation.
Established context stays bound to its source. Replacing that source with a
symlink or removing a managed directory stops the invocation for review;
the runner does not silently redirect work or recreate an empty workspace.
Managed Git cleanup remains the separate
[`agent workspace` workflow](agent-subagents.md).

The refreshed editing templates stop for unrelated changes in existing/shared
checkouts. A managed persistent task worktree can retain and continue its own
unfinished edits after inspecting them against the checkpoint. Saved prompts
retain their exact instructions; update an older template deliberately before
expecting it to continue a dirty task worktree.
