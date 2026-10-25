# Persistent agent tasks and goals

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

T3 Code 0.1.0 also provides native scheduling. In an active T3 session, use its
`schedule_task` for authorized recurring work returning to that conversation,
another T3 provider, or a supported webhook. The shipped
[T3 scheduling reference](../../common/agent_skills/basaltwater-subagents/references/t3-scheduling.md)
covers structured schedules, server timezone, current-thread versus fresh-thread
binding, inherited model/options, controls, signing secrets, and Connect URLs.
Use T3's PR watcher for requested PR monitoring instead of creating a timer.

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

## Create and control work

The Agents dashboard is the starting page. It summarizes recent runs, tasks
needing review, maintenance status, operator inbox feedback and runtime
readiness. **Tasks**, **Run history**, and **Readiness** are views within
Agents; **Agent tools**, **Inbox**, and **Maintenance** are neighboring tabs.
Dashboard summaries use saved state and do not launch diagnostics or prompts.
Run history is available at `/agents?view=history` and retains the same
receipts and controls as task history opened from a run link.

New tasks and ordinary templates default to **Once**. Guided system and data
tools also prepare one-time work by default. Templates on the Maintenance tab
are optional one-time reviews; opening one prepares a task for review and does
not create a schedule. The explicitly named
**Daily maintenance checks** template supplies a daily routine; other recurrence
requires a deliberate selection. Existing schedules keep their saved cadence.

The Tasks view shows each task's latest summary and pending follow-up badge.
Finished one-time tasks move into **Completed one-time tasks** unless **Keep in
saved tasks** is selected. Their receipts and results remain accessible. When
the saved-task limit is reached, the oldest finished, unpinned one-time control
can be reclaimed; drafts, queued/running work, goals and schedules are retained.
Follow-ups link back to their original task history.

Host service review templates use `mode: "workspace"`, `workspace:
"run-directory"`, and `network: true`. Command network access permits local
service-manager sockets and loopback readiness probes that inspection mode can
block. Each run receives an empty private writable directory rather than write
access to the source home or checkout; temporary writes remain disabled. These
prompts request read-only diagnostics and recommendations, without host repairs.
Filesystem-only storage reviews retain inspection mode and select
`storage_operation: "review"` for bounded host inventory/native preview evidence
before the sandboxed model starts. The
[storage cleanup workflow](storage-cleanup.md) gathers bounded metadata,
ranks concrete candidates and proposes a separately approved follow-up.
Saved tasks keep their original settings; create a new task from the refreshed
template to replace an
older inspection task. The panel identifies restricted saved host-check presets
and links to their current template in task settings, copied/reused forms and
run reports. Older guided system checks link to their current preparation form.
Other blocked inspections link to the template catalog. These links
only open a form; review and submit the replacement, then pause the old schedule
to avoid duplicate runs. **Reuse run settings** preserves the old permissions.
An established workspace binding cannot be changed in place.

For custom host reviews, use `agent doctor` with explicit capabilities.
`agent manifest` requires a project checkout and is not a host readiness check.
An inaccessible service manager leaves maintenance state unknown rather than
proving timers are disabled. The development doctor's Node probes need no
temporary writes or package-manager downloads.

The runner explicitly selects a read-only profile or a fresh workspace profile
on current Codex, and supplies matching legacy sandbox settings for older
versions. A generated profile name prevents unrelated configured profile roots
from being merged into the task. Administrator requirements still apply:
an account restricted to an allowlist of named profiles can reject workspace
tasks; the runner does not retry with broader access or weaken that policy.
User-service queries use systemd's ordinary D-Bus connection
(`SYSTEMCTL_FORCE_BUS=1`) because its private manager connection cannot verify
peer PIDs across Codex's PID namespace. Each invocation sets the task account's
`HOME`, `CODEX_HOME`, runtime directory, D-Bus address and forced-bus setting in
both the provider environment and Codex's shell environment overrides. This
prevents a caller's stale bus address or core-only environment inheritance from
breaking live diagnostics. Explicit administrator policies and shell include
filters still apply; the runner does not widen environment inheritance.

Use **Host maintenance review** for a one-time live check, or **Daily
maintenance checks** to prepare a recurring review. Both use a private run
directory and command network access, with prompts prohibiting host repairs.
Restricted saved inspection tasks can still report unavailable service state;
opening their current template prepares the access needed for a new check.
Repair proposals need exact targets, permitted changes, verification, and
rollback. Root actions still use the configured privilege approval workflow;
workspace mode does not grant root access.

`writable_directories` adds up to eight specific, existing account-owned
directories to workspace mode. These grants accompany the execution directory,
including when it is private scratch space. Whole-home grants, symlinks,
shared writable directories and credential/configuration/control stores are
rejected. The runner reapplies these grants when resuming a conversation.
For `mode: "workspace"` with `workspace: "existing"`, the working directory
must also be a specific project directory: the account's home and any path that
overlaps `.ssh`, `.codex`, `.agents`, `.config`, or `.local/state/basaltwater`
are rejected, including protected descendants and their ancestors. Creation,
updates, and prelaunch checks enforce this boundary. Older saved tasks remain in
the inventory, but an unsafe task fails before workspace preparation or provider
launch. A draft without an established context can be edited to use a safe
project directory through CLI or MCP. If an earlier run established its source
binding, create a new task with the safe directory and pause or remove the old
task. `reset-context` retains that source binding; it does not enable changing
the old task's directory. Retained receipts remain readable after removal.
Inspection can still use the home directory. Managed `directory` and
`run-directory` workspaces can keep the home as their source because execution
uses private empty task directories. Fixed native storage operations also
intentionally use the home as their target and do not launch Codex.
`operator_requirements` records unresolved prerequisites as up to eight short
lines. A task with unresolved prerequisites stops before provider launch;
review the evidence and clear resolved entries deliberately. For a follow-up,
**Approve once** confirms the displayed checklist and records it in the approval
decision; the queued task no longer carries those entries as unresolved blockers.
Saving a draft preserves them and does not confirm completion.

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
run consumes a one-time schedule. Repeating tasks use the first-run time
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

## Request a follow-up with different access

An unattended run can return `follow_up` in its structured report when a check
or repair needs different permissions or authorization. Use `null` when no
follow-up is needed. A request is retained in the receipt and recent run
history; it never creates a task, changes permissions, or submits a privileged
approval. Reports from older runners without this field remain readable.

For example, a restricted check can request live diagnostics:

```json
{
  "title": "Verify live maintenance state",
  "reason": "The inspection sandbox denied service-manager and loopback checks; file presence alone cannot establish health.",
  "prompt": "Read the update timers' current state and last results, then check the local T3 readiness endpoint. Record results and inaccessible checks. Do not modify files or services.",
  "directory": "/home/agent",
  "mode": "workspace",
  "workspace": "run-directory",
  "network": true,
  "temporary_files": false,
  "writable_directories": [],
  "operator_requirements": [],
  "service_restarts": [],
  "storage_operation": "none"
}
```

New reports supply these fields. Older requests remain readable and offer
**Review follow-up task** instead of a disabled approval button. Review their
write targets and operator prerequisites, then select **Approve once** in the
form. Missing fields default to no additional grants or prerequisites; the
review requirement is not inserted as an unresolved operator action. Complete
any outside preparation before approval; resolved checklist lines can remain in
the form because **Approve once** confirms them.
Its prompt accepts up to 3800 UTF-8 bytes
and should include concrete targets, actions, verification, and rollback for
repairs. `directory` must be absolute; the normal task validator rechecks its
existence and the account's workspace boundary before preparation.

The main list and run history offer **Approve once**, **Review follow-up task**
and **Block**, alongside the proposal's summary, access and expandable actions.
Approve authorizes the proposed task, confirms that its displayed operator
checklist is satisfied, and queues one attempt after validating access, policy
and separate service prerequisites. The decision preserves the confirmed checklist
and the original receipt remains unchanged. Users do not need to delete an
agent-written request for approval or resolved preparation notes to proceed.
Review opens
an editable form with the source receipt, **Once**, and a one-attempt limit;
opening it does not save or queue anything. The panel saves a private intent
with the exact validated child settings before creating the task. If publishing
the final decision fails, leave the proposal pending and select **Finish saved submission**
from its run history to finish the same submission; recovery does not repeat
service actions. Blocking checks for the exact matching child and refuses while
one is saved, so it cannot mark a queued follow-up blocked. If no child was
found, approval does not create another: the child might have completed and its
controls might have been removed. Inspect retained history before blocking the
proposal or preparing replacement work. Blocking can still finish when no child
is saved. Repeated submission of a proposal returns the
same child task. Blocking prevents that proposal from being launched and attempts
to cancel known pending service requests. If submission is
uncertain and no request ID was returned, blocking cannot cancel that broker
request; check Privilege approvals before any retry. Blocking cannot undo an
already executed action. The original task keeps its permissions, schedule and
outcome.

Use `service_restarts` for up to four exact service units. Registered targets
use the broker's structured restart operation. For unregistered targets, the
panel reads protected broker policy and requests individual approval of the
exact argv `/usr/bin/systemctl --no-ask-password restart -- UNIT`, through the
broker's existing command-approval contract. It adds no reusable service rule.
The launch check binds the succeeded receipt to the selected operation and
exact arguments. Broker request responses must include a valid 32-character
hexadecimal ID; status, cancellation, and approval responses must echo the
exact requested ID. Missing, malformed, or mismatched IDs leave the result
unconfirmed, so the follow-up is not queued. **Approve once** creates any
missing service requests and opens their review without queuing the task or
reporting a submission error.
Open **Open service approval** in its separate tab, approve with the broker
password, then return and select **Check approval and launch**. The review form
keeps edited settings and offers **Request or refresh service approval** for
explicit retries of failed or expired actions. Repeated approval clicks reuse
existing requests; uncertain submissions are never automatically replayed. A
generic or malformed broker error, including an older broker response, leaves
submission status unknown. Check the privilege portal before retrying; a local
Block cannot cancel a request without its broker ID.
The operator approves in the separate privilege portal; before a follow-up can
launch or be saved as a draft, the panel verifies that each exact restart
succeeded. The task agent then verifies the results without repeating the
restart. These are prelaunch prerequisites: if a file repair must happen before
the restart, propose the repair first and a separate restart-verification
follow-up afterward. Before submitting service requests, the review form allows
editing the units and prompt to split an older proposal. Once submitted, block
the proposal to cancel unclaimed requests before preparing a revised task;
removing a unit from the form cannot withdraw a broker action.
Live service diagnostics also need command network
access. Page navigation reads saved request state; explicit submissions check
the broker without a background polling loop.
Unsupported actions or other manual preparation belong in
`operator_requirements`, not a prompt asking another unattended agent to grant
itself permissions. Use this checklist for concrete outside preparation, not
approval of the proposal itself; selecting **Approve once** supplies that
authorization. Confirmation cannot grant extra write paths, override policy, or
satisfy a service restart that has not succeeded through the broker.
Workspace/network access does not supply root approval.
`storage_operation` defaults to `none` for older tasks/proposals. New reports
include it explicitly. A fixed cleanup proposal selects `npm-content-cache`,
`go-build-cache` or `podman-images` and uses the constrained settings documented
in the storage workflow. Approval queues one deterministic host operation;
its prompt cannot supply shell commands or expand its target. `review` only
adds host evidence before the ordinary Codex invocation. Capabilities and compact
task listings expose these selections; CLI JSON and MCP use the same validation.
Do not repeat unchanged blocked proposals; report environmental difficulties
as feedback instead. The receipt is also readable through CLI/MCP; agents must
not activate a request needing additional authorization themselves.

## Report environment difficulties

Task agents return `environment_feedback` in their structured report (empty
when unnecessary; at most 3000 UTF-8 bytes). The runner delivers it with task/run
references to the account-local **Agent inbox** (the `operator` mailbox), outside the
provider's sandbox. A delivery failure is recorded in the receipt without
discarding the feedback. Describe the failed operation, error and expected
behavior without credentials or raw private logs.

T3 and terminal agents can use the local CLI:

```bash
basaltw agent inbox feedback --sender t3-agent 'Describe the environment issue and relevant evidence' --json
```

The writable task MCP integration exposes `basaltwater_operator_feedback` too.
For an otherwise read-only integration, `basaltw agent tasks mcp --allow-feedback`
exposes only this additional mutation. Feedback is self-reported, account-local
evidence; it neither contacts an external developer nor authorizes repairs.
The operator inbox supports **Archive** and **Restore** and keeps archived
messages browsable within its bounded retention.

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
The separate [durable inbox](../operations/maintenance-agents.md#durable-inbox-and-cooperative-wake)
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
reservations and wake consumption are persisted before execution. The worker
passes its lease descriptor to the Codex process, so if the scheduler crashes
while Codex is still alive, a new worker cannot recover or dispatch another run
until that process exits. The descriptor is runtime-only and never enters task
state or receipts. It serializes the direct provider process; arbitrary
descendants may not inherit it, so the managed service's cgroup remains
necessary to terminate them after supervisor failure.

After the lease is available, a still-running claim without a final receipt is
recorded as interrupted; inspect its edits before another invocation. If a
final receipt was saved before an interrupted hot-state write, recovery
reconciles its result and usage without relaunching the provider and advances a
recurring task past intervals elapsed during that run. A claimed cancellation
still consumes its reserved lifetime and daily attempt, while preserving the
consecutive-failure count.
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
