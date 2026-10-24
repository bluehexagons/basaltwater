# Web panel reference

This page records the API, collection limits, and security boundaries for the
[minimal web panel](web-panel.md). Use the main guide for installation and
day-to-day operation.

## Feature discovery and navigation

All panel views use one page-only navigation list filtered by installation
evidence for the panel account. Discovery checks the existing managed tool
search paths, the account's T3 runtime and user unit, and fixed system unit
locations. It runs no subprocesses, credential checks, or network requests.
Service health, enabled state, and successful authentication are separate from
installation; a failed or masked installation remains inspectable. Saved T3
setup flags alone do not expose a missing runtime.

Codex exposes prompt forms, tools, and contextual review links. Other installed
agent runtimes expose diagnostics and inboxes. Git exposes account settings;
GitHub token controls require GitHub CLI too. Butler/SteamCMD expose publishing,
the installed maintenance service and policy expose supervision, and a valid
configured approval URL exposes administration. GET and POST routes for absent
optional features return HTTP 404 before reading or changing their state.
Discovery is repeated on requests, so tool changes need only a page reload.

Dashboard section links stay inside the dashboard and reference only rendered
sections. Unconfigured notifications, unavailable gateway certificate help,
and empty web-service/access lists are omitted. Diagnostic service selectors
list installed fixed units; an explicit saved service link can still inspect
historical logs after removal. The fixed unit allowlist remains the validation
boundary for such links.

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

The dashboard's Agent activity section reads bounded saved task state without
launching diagnostics, tools, or prompts. It counts panel tasks only: active and
queued runs, enabled schedules, drafts, automatically paused tasks, and saved
tasks whose latest retained outcome failed or was interrupted. Removed tasks
do not contribute to the attention count. The latest finished run remains
available while retained, including after its task is removed. Missing storage
is shown as unavailable; a scheduler error is displayed alongside saved counts.
Run links use `GET /agents?run=RUN_ID` and an in-page anchor to open the selected
report; an evicted record produces a notice. IDs use the same strict
32-character format as other task views. Opening a report changes no state.

On a `server_proxmox` host, the dashboard adds a **Proxmox overview** for all
visible cluster nodes, guests, storage and recent tasks. It shows node status,
CPU and RAM capacity/use, guest counts and assigned vCPU/RAM alongside host
capacity, and separate running-guest and all-guest totals. Assigned resources
do not guarantee host headroom: ballooning and LXC limits affect actual use.
Templates are excluded from guest allocations. Offline node metrics may be the
last values Proxmox reported; unknown or truncated allocations and capped
inventory are explicitly partial.
Per-node ZFS pool health is local to the collector node; Ceph health is
cluster-wide when Ceph is locally configured. Missing backends are distinct
from failed or unavailable health checks.

Guest tables support search and filters by name, VMID, node, state, type and
tag, and sorting by name, VMID, CPU or memory. Guest filters affect only the
guest table; node selection also filters tasks, storage and allocation
summaries. Clustered views default to the cluster. Task activity shows the
latest bounded shared task list of up to 50 tasks, including running, succeeded,
warning, failed and unknown states; it does not expose raw errors or logs or
provide a complete task archive. Native Proxmox links open guest, node or task
details for investigation. Storage pools show their
reported state, type, shared flag and capacity, with warnings at 80% and 95%
use. Pool totals can overlap; do not add them together. Guest disk capacity is
allocated space, not guest filesystem free space. **Open Proxmox VE** uses the
configured native address on HTTPS port 8006, including IPv6, and Proxmox's own
login and certificate.

The overview controls use bounded GET fields `pve_q`, `pve_node`, `pve_type`,
`pve_state`, `pve_tag` and `pve_sort`. Guest-specific filters affect only the
guest table; node selection also filters tasks, storage and allocations. A
state-preserving link retains filters, and a reset link clears them.

Guest addresses and application links are best-effort details from a bounded
probe of up to 16 local-node guests per collector run, rotated each minute to
cover larger inventories. Guest configuration, QEMU guest-agent and interface
probes are only made for guests on the collector's own node; remote-node
addresses and Notes links are not fetched automatically. QEMU addresses require
the guest agent to be enabled and running; LXC addresses come from its
interfaces. Loopback, link-local and multicast addresses are omitted. A
copy-address control has a selectable-text fallback. Application links are
validated HTTP(S) Markdown links from saved Proxmox Notes; the panel does not
export raw descriptions/configuration or provide a link editor.

A separate root oneshot, `basaltwater-web-panel-proxmox.service`, writes a
sanitized read-only snapshot to
`/var/lib/basaltwater/web-panel/proxmox/inventory.json`, mode 0640, readable by
the panel group. The schema-2 snapshot is bounded to 2 MiB per query and in
total; it allows up to 128 nodes, 512 guests, 512 node-pool entries and 32 tags
per guest. Resource history and backup freshness are not provided. Core cached
reads use only fixed allowlisted GET endpoints
`/cluster/resources`, `/cluster/tasks`, `/cluster/status` and `/version`.
The shared resources API supplies visible nodes, guests and storage; the shared
tasks API supplies the latest at most 50 tasks. Status supplies quorum and
members; version is the local installed version. Standalone nodes remain
supported.
Guest detail reads are local-only and separately bounded to three seconds each;
collection rotates candidates to stay within 45 seconds overall. Each cached
core query is limited to eight seconds. The systemd service allows 60 seconds
to start, and initial setup waits up to 70 seconds. The timer refreshes 60
seconds after collection finishes.

Only selected sanitized fields are published. The root collector uses fixed
read-only GET queries; the web worker and Nginx cannot execute root queries or
see Proxmox's `/etc/pve` and `/var/lib/pve-cluster`. Root/web isolation from
pmxcfs is unchanged. The collector uses AF_UNIX, AF_INET and AF_INET6 for
local Ceph monitor communications; `ProtectHome=true` remains in place. It
does not use remote-node SSH, scan storage backends, accept arbitrary query
parameters or paths, export raw descriptions/configuration, task errors,
credentials or command output, or control guests. ZFS health covers only pools
on the local node; snapshot presence does not mean remote ZFS was collected.

The panel revalidates bounded JSON on read and distinguishes unavailable or
failed collection from a successfully empty inventory. Unknown metrics remain
unavailable. Refreshing the dashboard reads the cache and launches no commands.
Existing installations must rerun panel setup to install the updated service
and collector. To investigate missing or stale data, inspect
`systemctl status basaltwater-web-panel-proxmox.timer` and
`journalctl -u basaltwater-web-panel-proxmox.service` on the host.

Existing host metrics, services, mounted storage, scheduled jobs and service
diagnostics remain available. The diagnostic allowlist includes `pveproxy`,
`pvedaemon`, `pvestatd`, `pve-cluster`, `corosync`, `pve-firewall` and
`proxmox-firewall`. The panel has no guest-operation broker; management actions
remain in the native Proxmox UI and CLI.

## Notification ingest

The receiver is opt-in, HTTPS-only, and available at
`/api/v1/notifications`. It accepts `POST` requests authenticated by a generated
bearer token stored at `/etc/basaltwater/web-panel/notification-ingest.token`.
The panel keeps the latest 100 accepted events.

The dashboard summarizes unresolved warning/error reports before the collapsed
notification history. Repeated firing reports with the same source address,
reported system, event type, and deduplication key appear once with an episode
count and first/latest receipt times. A matching recovery or successful report
clears the episode; a later failure starts a new count. The latest receipt wins,
including a change to an informational status, independently of sender clocks.
Unkeyed reports and reports without a known source address remain separate.
The summary covers retained history only: it is not a durable incident tracker,
and a quiet sender is not evidence of recovery. Sender address changes create a
separate group. Every retained receipt remains available in history.

| Result | Response |
| --- | --- |
| New event stored | HTTP 202, `duplicate: false` |
| Existing event ID | HTTP 200, `duplicate: true`; no second history entry |
| Invalid token or request | Rejected without storing the event |
| Excess requests or body size | Rejected by dedicated limits |

The endpoint uses its bearer token instead of Basic Auth. It requires HTTPS as
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

Setup activity is omitted and counted in the page notice. Up to 64 recent setup
attempts retain separate bounded windows, including failed runs; activity between
runs remains visible. Window boundaries account for audit timestamps rounded to
seconds. Audit-rule configuration records are excluded from keyed activity
because their rule keys do not indicate a protected-file or module operation.
Missing audit coverage and failed collection are warnings, not a clean result. A setup rerun
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

**GitHub runner** is exposed by the fixed
`/etc/basaltwater/github-runner.json` policy and
`basaltwater-github-runner.service` unit. Discovery starts no process or network
request. `GET /github-runner` collects nothing; `?load=1` inspects the fixed unit
with a five-second, 64 KiB command limit, bounded 16 KiB policy and 64 KiB outcome
reads, and one concurrent collector. Unknown or duplicate query fields return
HTTP 400. The page shows registration state, repository or organization/group,
labels, pinned version, read-only allowlist, local service state/boot enablement
and the last 100 gate outcomes. Local service health does not prove GitHub
connectivity. Outcomes are runner-writable telemetry,
not an audit trail. No runner credentials, full event payloads or workflow logs
are displayed.

Pending runners accept a one-time registration token through authenticated HTTPS
`POST /actions/github-runner/register`, with CSRF and an 8 KiB form limit. A fixed
privileged helper receives the token over a volatile Unix socket; it is never
saved in panel state or responses. Replacement, queue, lifecycle and allowlist
editing remain unavailable. See [GitHub runner](github-runner.md).

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
Interval deadlines include systemd's month and year spans on hosts with long
uptimes; calendar and interval deadlines remain visible together when both apply.

**Service diagnostics** uses fixed service names and properties without
elevation. Select a time window, severity, and literal case-insensitive text;
the panel shows up to 100 newest matching entries. Each collection is limited
to five seconds and 64 KiB, with at most two concurrent requests. Empty,
unavailable, timed-out, or truncated results are shown explicitly. Broaden
the query with links to all priorities or the last 24 hours while retaining
the selected service and literal message search. Notices distinguish missing
units, journal permission failures, unavailable user service managers, and
missing diagnostic commands. A successful empty query is distinct from an
unavailable collector; reaching 100 entries shows an explicit limit notice.
The journal can omit an individual binary or oversized message while retaining
its timestamp and severity. The panel labels that entry
`[Binary or oversized message omitted by the journal]`; use the SSH inspection
command for further investigation. This is separate from the panel's total
collection limit.
Logs can contain sensitive application data; redaction covers common credentials
and keys, including quoted JSON credential fields and escaped quoted values,
but cannot identify every secret. The panel service receives the
`systemd-journal` supplementary group so system logs are readable without sudo.
This is scoped to the service unit rather than the setup user's login groups;
processes launched by the panel also inherit it. Rerun setup to apply the unit
change to an existing installation.

T3 Code's **Update to latest** action uses the supported user-service updater
and readiness checks. **Admin controls** provides separately approved Debian
package upgrades and a full Basaltwater refresh.

## Git and credentials

`GET /credentials` reads only public author fields from the account's standard
global Git files and the presence of a stored `github.com` token. It does not
contact providers, read repository-local settings, follow Git includes, run
credential helpers, or return credential contents. A `saved` query accepts only
the finite `identity`, `github`, or `removed` result labels; all other fields and
duplicates are rejected.

The fixed CSRF-protected POST routes are `/actions/credentials/identity`
(`name`, `email`), `/actions/credentials/github` (`token`), and
`/actions/credentials/github-remove` (`confirmation=remove`). They use the normal
16 KiB body limit, reject unknown/duplicate fields, validate author fields and
single-line tokens, and cap tokens at 8,192 characters. Token replacement and
removal require both an HTTPS manifest URL and the HTTPS scheme supplied by
the local Nginx proxy. Responses and redirects contain no submitted token,
vendor output, or arbitrary exception details. No credential history is stored.

Editing requires the current UID to match the configured non-root account and
its owned home. Paths must have no symlink components, must be regular bounded
files (1 MiB maximum), and must not be writable by other users. Existing GitHub
credential files must also be private. Writes use private temporary files and
atomic replacement with mode `0600`; locks and concurrent-content checks avoid
overwriting another update. The panel modifies fixed identity/helper keys in a
staged Git config and preserves unrelated settings. GitHub replacement requires
Git and GitHub CLI; it directly stores the token without a classic-scope login
probe. Only the selected host entry is replaced or removed. Token removal does
not revoke provider credentials or clear a keyring or environment.

These settings belong to this VM account, not the controller's credential store.
Ordinary controller setup preserves seeded target values; explicit saved author
overrides are reapplied on reruns. The page provides the existing device-login
command for agent subscription sessions rather than copying renewable sessions.

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
Proxmox panels omit the administration page and its navigation entry because
the optional broker is unsupported on that profile. Other profiles retain the
page's setup instructions when the broker is unavailable; administration actions
require a configured broker.

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

`/agents` is a saved-state dashboard for recent runs, review-needed tasks,
maintenance, inbox feedback and runtime readiness. It does not launch
diagnostics or prompts. `?view=tasks`, `?view=history`, and
`?view=diagnostics` select task, standalone run history, and readiness views.
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
Repository templates require an installed Git executable. Without Codex, the
Agents page exposes only installed runtime diagnostics; the CLI can still save
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
external integrations. Repository editing templates require a clean existing
checkout, while a managed task worktree may continue its own changes against
the checkpoint. They stop for review when changes are unrelated. This
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

The panel renders saved configured access and discovers live `basaltwater-web`
forwards and static sites. HomeBox status is a loopback readiness probe only;
it does not prove DNS, TLS, browser reachability, or inventory administration.
