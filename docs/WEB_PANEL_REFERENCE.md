# Web panel reference

This page records the API, collection limits, and security boundaries for the
[minimal web panel](WEB_PANEL.md). Use the main guide for installation and
day-to-day operation.

## Host snapshot

The overview caches local host readings for up to 30 seconds. It shows uptime,
memory and swap usage, root-filesystem space and inode usage, the running kernel
and architecture, reboot state, and the package-update timer. Missing data is
shown as unavailable; zero swap capacity is shown as not configured. A filesystem
without a fixed inode count is shown as not reported.

Load average counts runnable and uninterruptible tasks over 1, 5, and 15 minutes;
it is not CPU utilization. The 1-minute value is highlighted when it exceeds the
reported logical CPU count. These host readings do not account for container CPU
quotas or memory limits. Usage meters highlight 80% and above, with a stronger
meter warning at 95%. These thresholds are inspection cues, not a health verdict.

Service summary counts reuse gateway readiness and existing HomeBox probes.
An endpoint without a readiness result remains not checked. Audit warning/error
counts cover the displayed snapshot, which can be incomplete or capped at 100
events; collection health remains visible beside the counts.

## Notification ingest

The receiver is opt-in, HTTPS-only, and available at
`/api/v1/notifications`. It accepts `POST` requests authenticated by a generated
bearer token stored at `/etc/basaltwater/web-panel/notification-ingest.token`.
The panel keeps the latest 100 accepted events.

| Result | Response |
| --- | --- |
| New event stored | HTTP 202, `duplicate: false` |
| Existing event ID | HTTP 200, `duplicate: true`; no second history entry |
| Invalid token or request | Rejected without storing the event |
| Excess requests or body size | Rejected by dedicated limits |

The endpoint is the panel's only Basic Auth exception. It requires HTTPS as
reported by the local Nginx proxy, compares the token in constant time, limits
bodies to 64 KiB, limits requests independently, and validates bounded
schema-v2 data. Unknown fields are discarded; malformed or deeply nested data
are rejected. The reported sender name is descriptive; investigate with the
source address and receipt time too.

Disable ingest with `--no-web-panel-notification-ingest`. To rotate its token,
remove the token file as root, rerun setup with ingest enabled, then update each
sender. Removing the panel deletes its Basic Auth data, notification history,
and audit snapshots.

Webhook senders accept the managed self-signed VM certificate by default. Add
`--notification-strict-https` when a sender must verify the normal CA chain and
hostname, especially across an untrusted network.

## Audit activity

A root-only timer exports a sanitized snapshot every five minutes. The panel
cannot read raw audit logs.

| Property | Value |
| --- | --- |
| Time window | Last 24 hours |
| Maximum entries | 100, including at most 25 routine privileged commands |
| Included | Category, time, paths, actors, operations, and executables when available |
| Excluded | Raw records, command arguments, and `proctitle` |
| Stale state | Older than 15 minutes is degraded |

Setup activity is omitted and counted in the page notice. Missing audit
coverage and failed collection are warnings, not a clean result. A setup rerun
reloads managed audit rules even if their file has not changed. VM and hardware
setup fails when the required auditd service cannot start or its rules cannot
load. Audit collection resolves packaged commands from system directories even
when a caller's PATH omits `/usr/sbin`.

The security monitor also records missing audit tools as a source failure on
profiles that require audit coverage; the dedicated Proxmox profile remains
optional. Required profiles check that auditd is active and managed rules are
loaded before treating an empty event search as clean.

The certificate panel verifies the live HTTPS endpoint against the configured
CA before offering installation. Public trust requires a managed Let's Encrypt
certificate policy and a successful TLS probe. A missing CA, failed TLS probe,
or live endpoint that still chains to the VM-local CA is shown as unverified
trust instead of publicly trusted.

## On-demand views

**Local service status** checks a fixed set of installed units, including
Nginx, SSH, Gogs, HomeBox, Docker, Samba, xrdp, fail2ban, auditd, and the
panel user's T3 Code unit. It does no collection until selected, limits each
service-manager call to two seconds, and caches results for 30 seconds. It
does not prove public connectivity, application readiness, or the state of
services owned by another user.

**Scheduled jobs** reads managed timer state on demand. It shows boot state,
next and previous triggers, last result, and available job details. An inactive
job service is normal between runs. A timer trigger without retained service
details is reported as unavailable rather than successful. Current systemd
state can reset after reboot or service-manager reload.

**Service diagnostics** uses fixed service names and properties without
elevation. Select a time window, severity, and literal case-insensitive text;
the panel shows up to 100 newest matching entries. Each collection is limited
to five seconds and 64 KiB, with at most two concurrent requests. Empty,
unavailable, timed-out, or truncated results are shown explicitly. Logs can
contain sensitive application data; redaction covers common credentials and
keys but cannot identify every secret.

T3 Code's **Update to latest** action uses the supported user-service updater
and readiness checks. **Admin controls** provides separately approved Debian
package upgrades and a full Basaltwater refresh.

## Administration controls

`GET /admin` reads bounded broker status and recent requests. Its optional
`action` query selects a fixed review form; it performs no host action.
`POST /actions/admin` requires CSRF, one finite action name, a matching single-use
five-minute review ticket, and host-name confirmation for reboot/power-off.
Unknown and duplicate fields are rejected. `POST /actions/admin/cancel` accepts
only CSRF and a request ID and delegates owner checks to the broker. Lost
responses do not cause automatic retries; inspect request history first.

The panel requests `admin.run` with a finite `action` parameter through the
kernel-authenticated requester socket. It cannot choose argv, environment,
service names, paths, or approval decisions. Every host action requires the
independent approval identity, including service restarts that could otherwise
have an administrator `allow` rule. The existing reboot `deny` policy also
denies the screen's reboot and shutdown actions. Cancellation of a scheduled
power action remains separately approved.

The broker's read-only `admin-status` operation returns up to 20 recent owned
administration requests from its latest 100 owned requests, fixed availability
reasons, and the latest job metadata. It returns no root command output or setup
configuration. Unavailable service state blocks new maintenance; a retained
running result with an inactive unit becomes interrupted, never successful.

Long actions use `systemd-run` to start the fixed
`basaltwater-admin-maintenance.service` outside the panel/broker cgroups.
`Type=exec` verifies process launch, and a six-hour runtime limit plus
`KillMode=control-group` bounds the job and its children. The shared unit name
and a lifetime file lock prevent overlapping maintenance. The private latest
result is atomically stored at
`/var/lib/basaltwater-privilege-broker/admin-job.json` before effects and after
completion. It is not a per-request completion log; inspect each request's
dispatch state separately. No job or ambiguous dispatch is replayed.

Scheduled power cancellation uses the fixed `shutdown -c` command directly,
so it can be approved even while a maintenance job is active. Root refresh
validates ownership and permissions throughout the installed source and Git
metadata before running the CLI; it never executes a selected user checkout.
The source's managed state directory is excluded from this code check because
the refresh command validates its saved setup separately. The approved check
reports validation success or failure without exposing privileged plan output.
Root-managed panels and hosts without the supported optional broker have
inspection links and their existing account actions, with host controls
unavailable.

## Agent tool preparation

`GET /agent-tools` displays a fixed catalog or setup form without collecting
logs or running work. Query fields are restricted to one `tool` and, for log
and job reviews, fixed `service`, `window`, and `priority` values. Job review
accepts only managed job services. Duplicate, unknown, and incompatible query
fields are rejected; the query is capped at 512 characters and four fields.
Contextual links never embed log entries, sender tokens, arbitrary commands,
or message-text search values.

`POST /actions/agent-tool/prepare` uses the normal panel CSRF and 16 KiB body
limit. It accepts one tool and only that tool's finite input fields. Preparation
validates paths with the shared filesystem validator and runner directory
validator, then renders the ordinary Agents task form. It does not create
state, call a collector, run Codex, or create output folders. Validation errors
preserve escaped form values for correction. Saving, scheduling, and running
still use `/actions/agent-task/save` with the existing validation and permissions.

Data paths are capped at 512 bytes, canonicalized, and required to stay strictly
below a dedicated account-home workspace. Source paths must exist and be regular
files or directories; output parents may be new or existing directories.
Hidden/credential paths, paths through symlinks, and nested or overlapping
source/output paths are rejected. Imports accept staged CSV/JSON/JSONL files and
one of those output formats. Cleanup requires a directory and a whole-number
minimum age of 1–3650 days. All generated prompts fit the runner's 4,000-byte
limit; paths travel as escaped JSON data, never executable command arguments.

Preparation does not lock source files or guarantee their later identity.
The prompt requires run-time path, ownership, and boundary revalidation.
Checksum/manifest, fresh-output, file-count, data-size, privacy, and quarantine
rules are agent instructions rather than an extra OS sandbox or deterministic
transfer implementation. The existing workspace sandbox and runtime remain the
execution boundary; independently configured integrations keep their permissions.
Only metadata and paths are handled by the workbench; the scheduled agent reads
source contents, which may enter its configured provider context. No download
or arbitrary-file serving endpoint is added; artifacts remain local to the
workspace and are reported in run output.

## Agent prompt runner

`/agents` renders without launching diagnostics or prompts. State changes use
Basic Auth and the panel's CSRF token. The fixed form routes are
`/actions/agent-task/save`, `/actions/agent-task`, and
`/actions/agent-diagnostics`; duplicate and unknown fields are rejected.
Tasks accept a name, prompt, working directory, optional model and reasoning
effort, maximum runtime, execution mode, command-network and temporary-write
choices, web-search mode, Codex session retention, a fixed interval, and a
0–10 consecutive-failure limit (default 3). No executable or arbitrary CLI
arguments can be supplied through a form.

The starting catalog combines universal templates with integration templates
filtered by existing manifest feature flags, configured service/access labels,
the account's T3 runtime directory, and a fixed list of executable names in
the managed account/system search paths. Rendering does not execute version,
service, credential, or browser checks for template discovery. Detection
reasons describe configuration or command presence, not health or authenticated
access. Conditional template links are validated against the fixed catalog;
unavailable selections show an explanatory notice without prefilling the task.
Saved tasks remain independent of catalog availability. See the [template
table](WEB_PANEL.md#agent-prompt-tasks) for conditions and suggested settings.

The runner invokes the installed Codex executable directly with `codex exec`,
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
external integrations. Repository editing templates require a clean checkout
and stop for review when an earlier run left tracked or untracked changes. This
is prompt guidance, not a Git lock or a runner-enforced preflight. Existing saved
prompts retain their text; select the refreshed template to adopt its guidance.

Model choices read only selector metadata from the bounded local
`~/.codex/models_cache.json`; identity and credential fields are never exposed.
Missing, malformed, oversized, or unavailable caches leave the configured
default and custom-ID option usable. Cached effort capabilities validate
explicit model choices when saving; availability is ultimately checked by
Codex when it runs. Effort overrides use `model_reasoning_effort`. Web-search
overrides use `web_search`; command-network restrictions do not restrict
provider requests or independently configured MCP integrations. Runs use
`--ephemeral` unless Codex session retention is enabled. These settings follow
the [Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).

State lives in `~/.local/state/basaltwater/prompt-tasks/tasks.json` (0600) below
a private directory (0700). A lifetime process lock prevents a second panel
scheduler from executing the same tasks. The scheduler runs with the panel
service, checks due work every 15 seconds, persists a claim before execution,
and executes serially. Next-run deadlines use elapsed hourly/daily/weekly
intervals rather than local calendar times; timestamps display in UTC. An
overdue task runs once, then advances beyond the current time. A run-now request
before the normal deadline does not move that deadline. Resume schedules a full
interval from the resume time. Pause clears queued work and stops repetition;
cancel stops the active process group. Restarted runs become interrupted and
are not automatically retried as the same run.

Draft creation persists settings with repetition and queueing disabled; it
does not require Codex to be installed. One-time drafts can be queued later;
recurring drafts can also be activated with Start schedule. Editing a draft
does not activate it. Task duplication (`?copy=TASK_ID`) and reuse of historical
settings (`?reuse=RUN_ID`) only prefill forms. Both use strict 32-character IDs;
reuse defaults to a one-time run and survives deletion of the original task
while its history is retained. All submissions still use CSRF protection and
normal task validation.

The consecutive-failure count is persisted on each saved task independently
of bounded run history. Failed and restart-interrupted runs increment it;
completed runs reset it; cancelled runs leave it unchanged. At the configured
nonzero limit, enabled schedules are disabled and labelled automatically paused.
There are no automatic retries. Resume resets the count and schedules a full
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
state file. Each task has a 1–10,080 minute wall-clock limit, defaulting to 30
minutes. Sleeping and waiting count against this limit; cancellation or expiry
stops the process group, including child commands. A runtime limit does not
enforce a monetary or token budget or stop work owned by another service, such
as an external MCP server or an approved privilege-broker request. Stopping a
local run does not withdraw such a request or undo its effects. The runner
retains the last 8 KiB of output and stops a run after 1 MiB of output. Common
credential patterns are redacted for display;
raw output and exact prompt settings remain in private state. Users must avoid
putting credentials in prompts, and output can contain application data.
Removing a task retains its existing run history. Removing the web panel stops
the scheduler but retains account-owned prompt state for explicit cleanup or
later reinstallation.

Runtime diagnostics use the existing local tool and credential inspectors.
T3 Code is checked only when configured or its runtime directory is present,
and checks never use `--fix`. The screen does not expose credential contents or
the T3 pairing password. Missing tools remain optional; only Codex is required
for prompt execution. A root-owned setup's locked panel service account cannot
run prompt tasks.

## Access controls and scope

The panel uses Basic Auth, request throttling, an
`basaltwater-web-panel` fail2ban jail, a Unix-socket-only application listener,
CSRF tokens for state-changing forms, no-store responses, and separate service
group access for the socket, bearer token, audit snapshot, and notification
history. It supports server and workstation profiles, but not `server_proxmox`.
It normally runs as the setup user. A root-managed setup instead uses the
locked `basaltwater-web-panel` service account.

The panel renders saved configured access and discovers live `basaltwater-web`
forwards and static sites. HomeBox status is a loopback readiness probe only;
it does not prove DNS, TLS, browser reachability, or inventory administration.
