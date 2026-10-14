# Persistent agent tasks and goals

Use **Agents** in the web panel or `basaltw agent tasks` as the task account to
run saved prompts, continue a bounded goal, or wake scheduled work with an
external event. Terminal Codex and agents inside T3 Code can use the same CLI
or stdio MCP API. All three surfaces share account-local state and controls.
Current execution uses Codex; installing T3 does not change the executor.
The managed `basaltwater-agent-tasks` skill makes scheduling and goal handoffs
discoverable in agent catalogs. Agents can schedule necessary follow-ups within
the user's authorized scope; an indefinite recurring job needs authorization
covering that recurrence.

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
[`agent workspace` workflow](AGENT_SUBAGENTS.md).

The refreshed editing templates stop for unrelated changes in existing/shared
checkouts. A managed persistent task worktree can retain and continue its own
unfinished edits after inspecting them against the checkpoint. Saved prompts
retain their exact instructions; update an older template deliberately before
expecting it to continue a dirty task worktree.

## Create and control work

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
Run now consumes a one-time schedule. Repeating tasks use the first-run time
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

## Hand off current work from T3 or a terminal agent

Set `initial_checkpoint` to a portable summary of findings, artifact paths,
pending work, and the next action (at most 16000 UTF-8 bytes). It seeds a new
context's first prepared run once. Persistent runs then use their own reports;
later fresh runs ignore it. The original handoff remains in settings and
receipts for provenance. The task prompt supplies authority; checkpoint text
is untrusted evidence and does not grant additional permissions.

`show` reports `initial_checkpoint_pending` before preparation. The initial
checkpoint may change before any attempt or context reset; subsequent changes
require a new task. Updates omitting it preserve it, including older panel
forms. Resetting context or changing persistence does not reseed the handoff.
Existing checkpoints and conversations are preserved when upgrading records
that predate the one-time handoff marker.

For concurrent repository edits, choose an owned worktree. It starts at committed
`HEAD`, so necessary uncommitted changes in the T3 thread must be finished or
deliberately transferred before handing off. The scheduled run is a separate
Codex conversation and does not automatically post its results into the creating
T3 thread. Inspect task history and receipts through the shared API.

## Continuation, outcomes and budgets

`continue_until_done` requires completion criteria and a total attempt limit,
deadline, or token budget. The CLI supplies a 20-attempt default when omitted.
A Once goal repeats using `continuation_minutes`; other goals use their saved
cadence. Ordinary recurring tasks remain recurring after a successful check.

The Codex report separates `progress`, `complete`, `waiting`, `blocked`, and
`failed` from the process exit status. Completion requires recorded evidence;
the report remains the agent's claim and should be reviewed against its logs
and artifacts. A goal stops when its report claims verified completion. Blocked
reports count toward failure pausing even when Codex exits successfully.
An invalid/missing structured report or an unfinished final provider turn fails
the invocation. An earlier turn's completion cannot finish a later turn.
Existing historical records are not retroactively classified.

| Setting | Default and meaning |
| --- | --- |
| `timeout_minutes` | 30; 1–10080 minutes for the provider invocation, including waits |
| `max_runs` | 0 for ordinary tasks; 20 for CLI goals; lifetime attempt limit, including failed/interrupted work |
| `deadline_at` | 0; UTC epoch or ISO date with timezone; prevents late launches and bounds the invocation |
| `start_at` | 0; explicit first-run instant, including a scheduled one-time invocation; must precede a nonzero deadline |
| `token_budget` | 0; sum of reported input/output tokens across runs, checked after each run |
| `daily_limit` | 0; maximum reserved attempts per UTC day; preserves queued work, including one-time invocations, until the next day |
| `failure_limit` | 3; pause after 1–10 consecutive failures/blocked/interrupted runs; 0 disables pausing |
| `backoff_minutes` | 0; minimum delay after unsuccessful work, in addition to the saved cadence |
| `history_limit` | 100; retain 1–1000 generated receipt/log pairs per task |

Zero disables an individual usage limit. Token budgets can be exceeded within
an invocation; they are not dollar budgets or provider-side hard caps. Missing
usage with a token budget pauses work for inspection. Cached input is already
included in input usage and is not counted twice. Raising a limit preserves
past consumption. Resume resets the failure streak, not usage counters.
Codex completion events contain cumulative conversation totals. Receipts keep
those totals as `session_usage` and record only the invocation's new tokens in
`usage`; a resumed conversation subtracts its saved private baseline. Repeated
completion events are not added together. Missing/decreasing totals invalidate
the baseline. The baseline must also match the task's last finished run, so a
failed checkpoint write cannot charge already recorded tokens again.
`show` reports `usage_baseline_valid`; compact listing retains `last_run_id` for
receipt lookup. A token-limited conversation with an unknown baseline stops
before launching another provider; inspect its receipts and reset context
deliberately if appropriate. Older conversation records lack this baseline;
past counters are preserved rather than guessed or rewritten.
Missing usage from any provider turn makes the invocation's total unavailable;
the runner never treats a partial total as the complete budget charge.
Workspace preparation has its own bounded managed Git operations before the
provider invocation starts.

Waiting reports for timed goals may request a bounded retry delay; ordinary
tasks keep their configured cadence. Event-only goals wait for the next
notification even when an agent reports a retry delay. Cancellation stops local
processes without undoing edits or cancelling work owned by another service.
Maintenance holds remain separate and are never created automatically.

## Independent worker and events

Non-root Debian setup with Codex selected installs
`basaltwater-agent-tasks-UID.service`. It runs as the task account, uses its
own credentials, starts at boot, and is independent of the panel and T3.
Selecting only `--web-panel` does not install the task worker. A panel starts
its legacy fallback scheduler only when Codex is installed for its account.
Shutdown stops new claims and cancels active work. A shutdown during claim
reservation cannot be cleared by the next invocation; a cancelled claim is
recorded before the worker exits.
Refreshing a busy worker is refused until its active invocation ends. Setup
holds the task-state lock through the idle check and service replacement so a
new claim cannot race that check. For a live legacy schema-1 worker, setup
automatically stops the managed panel belonging to the task account before
rechecking its worker lease: that older scheduler cannot honor the shared
state lock. This may terminate active panel jobs or prompt invocations.
Saved tasks, queued work, credentials and workspace edits are retained; the
replacement worker records unfinished runs as interrupted through normal
recovery. Inspect those edits before another invocation. Setup restores the
panel after activation or a failed handoff. A foreground or unmanaged legacy
scheduler must be stopped by its owner; setup does not terminate it.
The panel hands scheduling to that worker while continuing its separate publishing poll.
Older installations retain panel scheduling until setup installs the worker.
OCI cannot install a persistent service; root-account tasks are unsupported.
Other platforms can run the foreground worker explicitly:

```bash
basaltw agent tasks worker
basaltw agent tasks worker --once
basaltw agent tasks notify TASK_ID 'CI completed; inspect its new result' --key delivery-123 --json
```

Use `interval: "event"` with `--schedule` for a task that waits for notifications
without periodic runs. Notify queues work only for an enabled task, coalesces wake requests and retains
up to 32 payloads. Keys deduplicate the latest 100 deliveries. Events do not
reactivate paused/draft/budget-stopped tasks or expand permissions. A delivery
during an active run is retained for its next invocation. There is no public
webhook listener in this version; trusted local automation can use CLI or MCP.
The separate [durable inbox](MAINTENANCE_AGENTS.md#durable-inbox-and-cooperative-wake)
remains available for cooperating terminal workers.

## MCP for Codex and T3 agents

`basaltw agent tasks mcp` serves initialization-based MCP revisions through
2025-11-25 on stdio, with read-only tools by default. It exposes capabilities,
compact task listing, task/context inspection, retained history and exact run
receipts. MCP listing defaults to `summary: true`; set `summary: false` for
full prompts and output, and `history_limit` from 0 to 40 to bound recent runs. Explicit
`--allow-write` adds creation, partial update, lifecycle actions and notify.
Mutations queue work; the MCP process does not execute providers or start a
scheduler. A scheduler must be running for queued work to proceed.
Worker lease inspection errors are returned in `worker.error` so task reads
and successfully saved controls remain inspectable. Partial or corrupt context
records return an inspection error while preserving their contents.

Standard managed Codex setup includes a writable `basaltwater-tasks` stdio MCP
server in its low-precedence `/etc/codex/config.toml` defaults. It launches the
managed Python entrypoint directly, so it does not depend on the provider's
shell PATH or project write access. It runs as that provider account and queues
work for that account's worker. User/server overrides and tool restrictions
retain precedence; setup preserves administrator-owned defaults. Hardened
setup does not add this server and continues to forbid configured MCP servers.
This follows [Codex's configuration precedence](https://learn.chatgpt.com/docs/config-file/config-basic)
and [stdio MCP configuration](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).

Rerun saved setup from the updated control plane to receive the skill, defaults,
and worker. Finish the current turn before using T3's **Restart agent session**
command to reload configuration. No whole-service restart is needed. Other
providers retain their own configuration and can use the CLI within their
active permissions.

For an installation without managed defaults, register the integration in a
Codex account explicitly:

```bash
codex mcp add basaltwater-tasks -- basaltw agent tasks mcp
```

Append `--allow-write` only when that integration should manage authorized
tasks. T3 agents can also use the installed CLI directly. Current stable T3
tools and browser previews are not inherited by a scheduled Codex session.
Permissions of configured hooks/MCP servers remain independent of the Codex
shell sandbox; persistence grants no extra authority.

To disable the managed server, set `enabled = false` under
`[mcp_servers.basaltwater-tasks]` in user configuration. `enabled_tools` and
`disabled_tools` can restrict its tools. Do not re-enable an explicit opt-out
or use another interface to evade a denied action. Unattended runs may create
additional schedules or extend goal limits only when their saved prompt
explicitly authorizes that work; inherited tool access cannot bypass a limit.

## Storage and recovery

Private state uses atomic writes and a process lock for mutations; readers
reload atomic snapshots. A separate lifetime worker lease prevents overlapping
execution across the panel and foreground/system workers. Claims, attempt
reservations and wake consumption are persisted before execution. A restarted
active run becomes interrupted; inspect its edits before another invocation.
If its final receipt was saved before an interrupted hot-state write, recovery
reconciles that result and usage without relaunching the provider.
Deadlines also stop idle event tasks and work deferred by a daily limit.
Active invocations recheck their absolute deadline as well as their monotonic
runtime cap; moving the wall clock forward does not leave a late invocation running.
Receipt writing precedes checkpoint updates. An evidence/checkpoint storage
failure pauses the task even when failure pausing is disabled; inspect its
retained receipt and repair storage before explicitly continuing.

| Location | Contents |
| --- | --- |
| `~/.local/state/basaltwater/prompt-tasks/tasks.json` | Schema 2 task settings, counters and latest 40 runs; valid schema 1 state migrates without changing activation |
| `~/.local/state/basaltwater/task-contexts/TASK_ID/context.json` | Checkpoint, exact session binding, cumulative usage baseline and owned workspace metadata |
| `~/.local/state/basaltwater/task-contexts/TASK_ID/runs/` | Per-run JSON receipts and bounded raw JSONL/diagnostic logs |
| `~/.local/share/basaltwater/task-workspaces/TASK_ID/` | Persistent or per-run empty task directories |
| `~/.local/share/basaltwater/worktrees/` | Managed Git worktrees with separate ownership records |

Recent history keeps up to 40 records within the 1 MiB shared-state limit.
Older finished summaries are evicted when needed; the newest record and active
claims are retained. Active claims reserve 128 KiB for completion metadata so
incoming events cannot consume that space. Requests that cannot fit after
history eviction fail without changing saved state; reduce task settings or
pending wake payloads before retrying. This limit does not remove full receipts.

Receipts survive eviction from the panel's recent-history list. Evidence
retention removes only generated receipt/log pairs, preserving workspace files
and other artifacts. Files are private; display redaction does not remove every
possible secret from raw evidence. Avoid credentials in prompts and logs.
Corrupt, unsafe or unsupported state is preserved and reported rather than
reset. Removing the panel leaves the independent worker and tasks in place;
pause tasks explicitly when removing their UI.
Setup errors for unsafe task directories identify the exact component, owner
UID and permissions. Inspect that path before changing ownership or modes;
setup does not recursively take over shared or unrelated account directories.

## T3 integration after the next stable release

Current qualification targets T3 v0.0.45. `capabilities` deliberately reports
native T3 task execution and thread binding as unavailable. Presence of a
newer runtime does not silently change existing task ownership or execution.

Upcoming upstream work supplies
[scheduling and thread tools](https://github.com/pingdotgg/t3code/blob/main/apps/server/src/mcp/toolkits/orchestrator/tools.ts),
[outside-agent MCP access](https://github.com/pingdotgg/t3code/blob/main/docs/user/outside-agents.md)
and [native Codex goals](https://github.com/pingdotgg/t3code/blob/main/apps/server/src/orchestration-v2/Adapters/CodexAdapterV2.ts).
These are design inputs from upstream `main`, not released capabilities promised
by this integration.

The intended adapter will let a task select a T3-owned thread as its executor.
Basaltwater will retain objective, policy, workspace, checkpoint and receipts;
T3 will own its native conversation and provider history. Successive runs will
message the recorded thread rather than importing transcripts or editing T3's
database. Switching executors will carry a portable checkpoint into a new
conversation unless supported session transfer is separately qualified.

Each task must have one scheduling owner. Initially Basaltwater should retain
the clock and send work to T3. Optional adoption of a native T3 schedule must
disable the Basaltwater trigger before activating the replacement and record
its schedule/thread/run identifiers, so two clocks cannot dispatch the task.

Before enabling that adapter, qualify the published stable API and feature
flags, account authorization, project/workspace ownership, current permission
limits on resumed turns, turn completion versus goal completion, usage
accounting, cancellation, UI disconnects, service restarts and uncertain
dispatch reconciliation. Native webhooks should reuse T3's supported delivery,
signature and deduplication controls after that qualification. Unsupported
features should remain explicit rather than falling back to a fresh thread or
a second scheduler.
