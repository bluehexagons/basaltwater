# Prompt runner and access controls

[Documentation index](web-panel-reference.md)

## Agent prompt runner

`/agents` is a saved-state dashboard for recent runs, review-needed tasks,
maintenance, inbox feedback and runtime readiness. It does not launch
diagnostics or prompts. `?view=tasks`, `?view=history`, and
`?view=diagnostics` select task, standalone run history, and readiness views.
`?view=t3` opens the coding workspace handoff without reading task storage.
It reuses bounded, cached gateway discovery and accepts only an unambiguous
HTTPS root identified by the exact `t3code` forward name, preserving the allocated
public port. Arbitrary service labels cannot enable the link. No token is minted
or copied; T3 owns authentication and the destination opens with `noopener
noreferrer`. Missing routes show recovery guidance. Saved `t3_update` selection
flags remain readable; new manifests emit `t3code`, which takes precedence.
Agent tools, inbox, and maintenance are tabs in the same Agents workspace.
State changes use
Basic Auth and the panel's CSRF token. The fixed form routes are
`/actions/agent-task/save`, `/actions/agent-task`, and
`/actions/agent-diagnostics`; duplicate and unknown fields are rejected.
Tasks accept a name, prompt, working directory, optional model and reasoning
effort, maximum runtime, execution mode, command-network and temporary-write
choices, web-search mode, Codex session retention, a preset or custom interval,
and a 0–10 consecutive-failure limit (default 3). No executable or arbitrary CLI
arguments can be supplied through a form.

Task saving accepts at most 40 fields and a 32 KiB encoded body; other ordinary
forms retain the 18-field/16 KiB limits. Tasks also accept context/workspace
persistence, completion criteria, bounded continuation, lifetime attempts,
timezone-qualified deadlines, reported-token and daily limits, failure backoff
and retained evidence limits. A custom schedule uses
`interval=custom` and `repeat_minutes` from 1 to 43,200 whole minutes; preset
intervals normalize the custom value to zero. Older saved presets receive that
default without changing their deadlines. The runtime cap is independent of
the repeat interval.

The starting catalog combines universal templates with integration templates
filtered by existing manifest feature flags, configured service/access labels,
the account's T3 runtime directory, and a fixed list of executable names in
the managed account/system search paths. Rendering does not execute version,
service, credential, or browser checks for template discovery. Detection
reasons describe configuration or command presence, not health or authenticated
access. Conditional template links are validated against the fixed catalog;
unavailable selections show an explanatory notice without prefilling the task.
Saved tasks remain independent of catalog availability. See the [template
table](web-panel.md#agent-prompt-tasks) for conditions and suggested settings.
Repository starters have been removed in favor of T3. Without Codex, the
Agents page exposes installed runtime diagnostics and the selected T3 handoff; the CLI can still save
inactive task drafts before installation.

The runner invokes the installed Codex executable directly with `codex exec`
or `codex exec resume SESSION_ID`,
passes the prompt through stdin, and explicitly uses the read-only or
workspace-write sandbox and the `never` approval policy. Workspace mode
explicitly configures command network access and temporary-directory writes,
and clears additional configured writable roots. Inspection uses a read-only
sandbox; workspace writes are restricted to a directory inside the account's
home plus `/tmp` and `TMPDIR` when explicitly enabled. These tasks use the
account's Codex configuration, credentials, and repository instructions; the
panel does not sandbox installed hooks or MCP servers independently. Sandboxing
must be supported by the installed CLI and host; failures remain failed runs
rather than falling back to unrestricted execution.

The prompt preamble supplies the saved mode, command-network and temporary-write
settings, web-search mode, runtime cap, and repeat interval. It instructs the
agent to keep inspection read-only across tools, report blocked checks without
escalation or interactive login, and keep waits within the remaining runtime.
Skill examples and tool presence do not grant authority beyond the task prompt.
These instructions guide agent behavior; they do not add an OS sandbox around
external integrations. Existing saved prompts retain their text and settings
when a starter is removed; no catalog change deletes or transfers tasks.

Model choices read only selector metadata from the bounded local
`~/.codex/models_cache.json`; identity and credential fields are never exposed.
Missing, malformed, oversized, or unavailable caches leave the configured
default and custom-ID option usable. Cached effort capabilities validate
explicit model choices when saving; availability is ultimately checked by
Codex when it runs. Effort overrides use `model_reasoning_effort`. Web-search
overrides use `web_search`; command-network restrictions do not restrict
provider requests or independently configured MCP integrations. Runs use
`--ephemeral` unless fresh session retention or conversation persistence is enabled. These settings follow
the [Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).

State lives in `~/.local/state/basaltwater/prompt-tasks/tasks.json` (0600) below
a private directory (0700). Mutations reload state under a process lock so CLI,
MCP and panel controllers cannot overwrite each other's updates; readers use
atomic snapshots. A separate lifetime lease prevents a second worker from
executing the same tasks. New setups install an independent non-root service;
older setups use the panel. The scheduler checks due work every 15 seconds, persists a claim before execution,
and executes serially. Next-run deadlines use elapsed hourly/daily/weekly or
custom minute intervals rather than local calendar times; timestamps display
in UTC. An overdue task runs once, then advances beyond the current time. A
run-now request before the normal deadline does not move that deadline unless the run crosses
it. At completion, failure, or cancellation, deadlines crossed during that run
advance to the next future point on the existing cadence; they do not queue an
immediate catch-up run. Other queued work is retained. Resume schedules a full
interval from the resume time. Pause clears queued work and stops repetition;
cancel stops the active process group. Restarted runs become interrupted and
are not automatically retried as the same run.

Changing the elapsed interval while editing resets the next deadline to a full
interval from saving. Other edits preserve it. Editing never enables a paused
task or draft, and switching to Once disables repetition unless goal continuation is selected.

Draft creation persists settings with repetition and queueing disabled; it
does not require Codex to be installed. One-time drafts can be queued later;
recurring drafts can also be activated with Start schedule. Editing a draft
does not activate it. Task duplication (`?copy=TASK_ID`) and reuse of historical
settings (`?reuse=RUN_ID`) only prefill forms. Both use strict 32-character IDs;
reuse defaults to a one-time run and survives deletion of the original task
while its history is retained. All submissions still use CSRF protection and
normal task validation.

The consecutive-failure count is persisted on each saved task independently
of bounded run history. Failed, blocked and restart-interrupted runs increment
it; progress/completion/waiting reset it; cancelled runs leave it unchanged. At the configured
nonzero limit, enabled schedules are disabled and labelled automatically paused.
Subsequent scheduled invocations remain subject to the saved cadence and limits;
the same interrupted run is not replayed. Resume resets the count and schedules a full
interval; a successful manual run leaves a paused schedule disabled. Older
state receives the default limit and a zero count, without recounting historical
failures. Recovery counts each interrupted run once. Policy state and run
results are saved together.

New run durations measure elapsed monotonic time around execution. Interrupted
run recovery and older records use nonnegative timestamp differences when a
monotonic duration is unavailable. Interrupted duration can include downtime;
active elapsed/remaining values use the wall clock at page load and can be
affected by clock adjustments. Outcome and duration summaries cover the latest
retained finished runs only; CLI completion is not proof of task success, and
duration is not a provider-usage or cost metric.

Limits are 32 saved tasks, 40 retained runs, a 4,000-byte prompt, and a 1 MiB
state file. Private per-task receipts retain up to `history_limit` runs
(default 100), including checkpoints, verification evidence and provider logs.
The recent panel list remains bounded at 40. `GET /agents?context=TASK_ID`
reads context and retained receipt paths without starting work. Each task has a 1–10,080 minute wall-clock limit, defaulting to 30
minutes. Sleeping and waiting count against this limit; cancellation or expiry
stops the process group, including child commands. A runtime limit does not
enforce a monetary or token budget or stop work owned by another service, such
as an external MCP server or an approved privilege-broker request. Stopping a
local run does not withdraw such a request or undo its effects. Optional token
budgets use actual reported usage after an invocation and can be exceeded
within that invocation. Missing usage pauses a budgeted task. Attempt budgets
are reserved before execution, including failures; deadlines bound provider
execution. The runner uses structured JSONL output and a schema-validated final
report, retaining a bounded display summary and stopping after 1 MiB of output.
Completion requires recorded evidence, although that remains the agent's claim
for review. Common
credential patterns are redacted for display;
raw output and exact prompt settings remain in private state. Users must avoid
putting credentials in prompts, and output can contain application data.
Removing a task retains its existing run history, context and workspace files.
Removing the web panel retains the independent worker; pause its tasks first
if they should stop. See [persistent tasks](../agents/agent-tasks.md) for storage,
API, and limits.

Runtime diagnostics use the existing local tool and credential inspectors.
T3 Code is checked only when its runtime directory or user service unit is present,
and checks never use `--fix`. The screen does not expose credential contents or
the T3 pairing password. Missing tools remain optional; only Codex is required
for prompt execution. A root-owned setup's locked panel service account cannot
run prompt tasks.

## Access controls and scope

The panel uses Basic Auth, request throttling, an
`basaltwater-web-panel` fail2ban jail, a Unix-socket-only application listener,
CSRF tokens for state-changing forms, no-store responses, and separate service
group access for the socket, bearer token, audit snapshot, and notification
history. It supports server and workstation profiles, including optional
host and guest monitoring on `server_proxmox`. On a root-managed setup, it runs as
the locked `basaltwater-web-panel` service account. Proxmox's `/etc/pve` and
`/var/lib/pve-cluster` paths are hidden from the panel and its Nginx service.
Proxmox panel setup restarts Nginx to apply these restrictions, including on
retries after an interrupted setup.
The Proxmox collector publishes sanitized read-only inventory without granting
the web worker Proxmox API access. Links open the native Proxmox UI on port
8006 for VM, container and storage management. With
panel TLS enabled, it listens on the selected HTTPS port and exposes the
certificate-only CA enrollment and download listener on port 8442. Port 8442
serves the public CA certificate; it is not an ACME issuance listener on port
80, and the panel does not use the shared web origin on port 8443. The panel
shows the CA fingerprint, which can be checked on the host with
`sudo sha256sum /srv/basaltwater/web/basaltwater-ca.crt`. The Proxmox TLS path
sets up Nginx and OpenSSL and creates or reuses the panel certificate without
installing the `basaltwater-web` publisher/control helper, its web policy, or
NSS trust.

For Proxmox, setup prepends native node-level TCP `ACCEPT` rules for the
selected panel port and, with TLS, port 8442. Rules use the selected access
sources when configured and are otherwise unrestricted. Each carries the
exact ownership comment `Managed by basaltwater web panel`. They do not switch
the firewall engine, enable or disable the global firewall, or change the
cluster-wide `management` IP set. Removing the panel deletes only these owned
rules, its Nginx site and systemd isolation drop-in, backend/configuration,
inventory collector/timer/snapshot and Fail2ban configuration; the shared CA,
Nginx/OpenSSL packages, native Proxmox UI/configuration, and unowned firewall
rules remain.

If the inventory directory contains leftover temporary files from an interrupted
write or other unrecognized files, removal preserves that directory and its
remaining contents while completing the rest of panel teardown.

The panel renders saved configured access and discovers live `basaltwater-web`
forwards and static sites. HomeBox status is a loopback readiness probe only;
it does not prove DNS, TLS, browser reachability, or inventory administration.
