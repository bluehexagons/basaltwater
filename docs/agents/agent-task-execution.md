# Continuation, budgets, and events

[Documentation index](agent-tasks.md)

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
| `max_runs` | 0 for ordinary tasks; 20 for CLI goals; lifetime attempt limit, including failed, interrupted, and cancelled claims |
| `deadline_at` | 0; UTC epoch or ISO date with timezone; prevents late launches and bounds the invocation |
| `start_at` | 0; explicit first-run instant, including a scheduled one-time invocation; must precede a nonzero deadline |
| `token_budget` | 0; sum of reported input/output tokens across runs, checked after each run |
| `daily_limit` | 0; maximum reserved attempts per UTC day; a cancelled claim still consumes its reservation; queued work, including one-time invocations, waits until the next day |
| `failure_limit` | 3; pause after 1–10 consecutive failures/blocked/interrupted runs; 0 disables pausing |
| `backoff_minutes` | 0; minimum delay after unsuccessful work, in addition to the saved cadence |
| `history_limit` | 100; retain 1–1000 generated receipt/log pairs per task |

Zero disables an individual usage limit. Token budgets can be exceeded within
an invocation; they are not dollar budgets or provider-side hard caps. Missing
usage with a token budget sets durable usage uncertainty and pauses work for
inspection. `run`, `resume`, scheduled work and event delivery remain blocked
while that task still has a token budget; partial settings updates and
`reset-context` do not clear the uncertainty. Cached input is already included
in input usage and is not counted twice. Raising a limit preserves past
consumption. Uncertainty persists through later known-usage runs and while the
token budget is temporarily disabled. Upgrade scans retained receipts and hot
history; missing records that prevent a complete lifetime accounting remain
uncertain. Resume resets the failure streak, not usage counters.

After reviewing the retained receipt and independently verifying the exact
lifetime input/output total, repair an uncertain task with an update-only
`usage_repair_tokens_used` setting. It must be at least the already recorded
`tokens_used` total; the task records the prior and repaired values with the
run ID, then clears uncertainty. For example, save
`{"usage_repair_tokens_used": 59710}` in a JSON file and pass it to
`basaltw agent tasks update TASK_ID FILE`, or send it in an MCP task update.
This value is the exact lifetime total, not an estimate or an added delta. If
the missing usage cannot be recovered, explicitly set `token_budget` to `0`
to continue without a token cap; this preserves `tokens_used` and the
uncertainty marker, and re-enabling a token budget blocks again. Creating a
new task after reviewing the old receipts starts a separate lifetime budget
while preserving the original task's accounting and evidence.

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

Lowering `failure_limit` to or below the saved consecutive failure count
immediately pauses an active schedule. The worker checks the saved threshold
again before each claim, including after loading older state. **Resume** clears
the failure streak and restores the schedule; **Run** queues one explicit
attempt without reactivating that schedule. A zero failure limit disables this
pause.

Waiting reports for timed goals may request a bounded retry delay; ordinary
tasks keep their configured cadence. Event-only goals wait for the next
notification even when an agent reports a retry delay. Cancellation stops local
processes without undoing edits or cancelling work owned by another service.
Maintenance holds remain separate and are never created automatically.

## Independent worker and events

Non-root Debian setup with Codex selected installs
`basaltwater-agent-tasks-UID.service`. It runs as the task account, uses its
own credentials, starts at boot, and is independent of the panel and T3.
The service grants `systemd-journal` to the worker and its provider processes
for host diagnostics. This service credential still applies with `--harden-user`,
which removes the group from interactive logins. Treat the task account,
provider execution, and any readable system or user journals as part of the
same host trust boundary. Provider sandbox and task permissions still govern
the filesystem paths and operations available during each run.
Selecting only `--web-panel` does not install the task worker. A panel starts
its legacy fallback scheduler only when Codex is installed for its account.
Shutdown stops new claims and cancels active work. A shutdown during claim
reservation cannot be cleared by the next invocation; a cancelled claim is
recorded before the worker exits.

When an unchanged worker is already active, setup verifies persistent boot
enablement and repairs it without restarting the worker or interrupting a busy
task. Runtime-only enablement is insufficient; an unsuccessful repair fails setup.
Reuse also requires the expected unit-file ownership and permissions, and
systemd must report the managed definition loaded without a pending daemon
reload. Missing or stale observations use the guarded idle
refresh, and retained unit-transaction recovery markers block setup until
[verified recovery](../operations/transaction-recovery.md).

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
The separate [durable inbox](../operations/maintenance-agents.md#durable-inbox-and-cooperative-wake)
remains available for cooperating terminal workers.
