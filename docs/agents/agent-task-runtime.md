# MCP, storage, and recovery

[Documentation index](agent-tasks.md)

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
reservations and wake consumption are persisted before execution. The worker
passes its lease descriptor to the Codex process and fixed host storage helper,
so if the scheduler crashes while either is still alive, a new worker cannot
recover or dispatch another run until that child exits. The descriptor is
runtime-only and never enters task state or receipts. It serializes each direct
child process; arbitrary descendants may not inherit it, so the managed
service's cgroup remains necessary to terminate them after supervisor failure.

After the lease is available, a still-running claim without a final receipt is
recorded as interrupted and its task is paused with a review stop reason; queued
wakes are cleared. Inspect its edits before using **Run** or **Resume** to
continue. If token usage is uncertain under a token budget, or evidence storage
needs repair, those stricter stops remain in force. If a final receipt was
saved before an interrupted hot-state write, recovery reconciles its result and
usage without relaunching the provider and advances a recurring task past
intervals elapsed during that run. If saving the recovered interruption also
fails, replaying its receipt still leaves the task paused for review; it cannot
launch at the next due time until **Run** or **Resume** is selected. A claimed
cancellation still consumes its reserved lifetime and daily attempt, while
preserving the consecutive-failure count.
Deadlines also stop idle event tasks and work deferred by a daily limit.
Active invocations recheck their absolute deadline as well as their monotonic
runtime cap; moving the wall clock forward does not leave a late invocation running.
Receipt writing precedes checkpoint updates. An evidence/checkpoint storage
failure pauses the task even when failure pausing is disabled; inspect its
retained receipt and repair storage before explicitly continuing. If a final
receipt was saved but a checkpoint update failed, worker startup and explicit
`run`/`resume` reconcile the exact retained receipt into context before another
provider launch. This restores the saved checkpoint without replaying the
provider or charging usage twice, and keeps the hot run's storage-failure
status for inspection. A durable per-task recovery identity survives eviction
from bounded hot history. Recovery is repeatable after another failed save. A
missing or invalid receipt, or a context write that still fails, keeps that
task paused; worker startup continues for unrelated tasks. Explicit
`run`/`resume` retries recovery and cannot launch until it succeeds. An
explicit `reset-context` after reconciliation remains effective and does not
cause the old checkpoint to be reapplied. If worker startup cannot read a
final receipt or save its recovered context, it finalizes that hot claim as a
storage failure and quarantines only that task. Its already reserved attempt
is retained; usage is charged once when a valid receipt becomes readable.

| Location | Contents |
| --- | --- |
| `~/.local/state/basaltwater/prompt-tasks/tasks.json` | Schema 2 task settings, counters, usage-repair audit, recovery/accounting identity and latest 40 runs; schema 1 usage gaps pause token-limited tasks |
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
