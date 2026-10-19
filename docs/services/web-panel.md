# Minimal web panel

The optional web panel is a browser dashboard for one managed machine. It
shows current host state, configured services, audit activity, maintenance,
notifications, and agent prompt tasks. Agent tasks use the installed Codex CLI
under the panel account; no T3 Code installation is required.

| Need | Open | Result |
| --- | --- | --- |
| Check host health | Overview | Uptime, load average, memory, swap, resource pressure, kernel, reboot state, and update timers |
| Check storage capacity | Mounted storage | Mounted local filesystems, available space, inode use, device/type, and read-only state |
| Find a managed endpoint | Services | Configured and discovered web, SSH, RDP, Samba, Gogs, HomeBox, and Antistatic access |
| Observe or control an agent desktop | Desktop | Optional browser access, mobile input, zoom and read-only readiness checks for the shared XRDP desktop |
| Open a shell without starting a desktop | Console, or Desktop → Open browser console | Guacamole SSH terminal with the Unix password; Desktop also offers an SSH command for an existing client |
| Inspect a service | Local service status or Service diagnostics | On-demand state, fixed runtime details, and filtered logs |
| Check maintenance | Scheduled jobs | Timer state, last result, and selected job logs |
| Inspect a GitHub runner | GitHub runner | On-demand local service state, repository, labels, read-only allowlist and recent gate outcomes |
| Enable tailored supervision | Agents → Maintenance | Host checks, security audits, opt-in housekeeping, bounded AI reviews, and retained findings |
| Exchange messages across sessions | Agent inbox | Account-local mailboxes with durable unread messages and explicit acknowledgement |
| Run agent work | Agents | Persistent Codex context and workspaces, bounded goals, recurring schedules, run evidence, and optional T3 Code diagnostics |
| Configure commit identity and GitHub access | Git & credentials | Account Git name/email, private GitHub token replacement/removal, and agent sign-in guidance |
| Upload games and review release writing | Publishing | VM-local Butler/SteamCMD sessions, completed build uploads, schedules, translations, promotion and tracked editor handoffs |
| Prepare system and data tasks | Agents → Agent tools | Contextual checkups, log and job reviews, Debian update and network checks, maintenance plans, and local data work |
| Administer the host | Admin controls | Approved package updates, Basaltwater refresh, service maintenance, and host power controls |
| Review audit activity | Audit activity | Sanitized recent events and collection health |
| Receive remote notifications | Notifications | Recent accepted events and an optional sender endpoint |

Navigation and read-only views work without JavaScript. The dashboard caches
host and service snapshots for up to 30 seconds. See the [web panel
reference](web-panel-reference.md) for data limits, diagnostic behavior, and
the notification API contract.

Every page uses the same grouped sidebar, containing only destinations with
their own pages. The dashboard's **On this page** links navigate its visible
sections. On smaller screens, **Panel menu** shows the current section and opens
the full grouped list. The menu works with touch and keyboard without JavaScript;
destinations wrap to fit enlarged text.
Light and dark themes follow
the device preference. Memory and disk meters retain numeric values, and
service states have text labels as well as color.

Agent and Git pages require both a saved setup selection and tools installed
for the panel account. Host monitoring remains available without either:

| Area | Appears when |
| --- | --- |
| Agents and Agent inbox | A selected Codex, Claude Code, OpenCode, or T3 Code installation is present |
| Prompt forms, Agent tools, agent-review shortcuts, and dashboard Agent activity | Codex is selected and installed |
| Git & credentials | Setup configures Git for the account and Git is installed; GitHub token controls additionally require selected and installed `gh` |
| Publishing | Butler or SteamCMD is installed; account controls show installed providers |
| Maintenance agent | Agent features are selected and installed, and its managed service and policy file are installed |
| GitHub runner | Its fixed policy and systemd unit are installed; see [runner setup](github-runner.md) |
| Desktop | Its Guacamole gateway service is installed |
| Console | Its private Guacamole SSH socket unit is installed |
| Admin controls | Always visible; host actions require the privilege approval service |

Discovery checks the panel's non-secret setup manifest, local executable paths,
and unit files without running tools, contacting providers, or testing sign-in.
Leftover executables, user units, and task history cannot enable agent UI on a
monitoring-only host. This also suppresses agent review links, sign-in guidance,
T3 controls and diagnostics, and panel prompt scheduling. Git's presence as a
system dependency does not enable account configuration controls by itself.
Unavailable optional pages and their actions return HTTP 404, including direct
links to T3 diagnostics and its update action.

A selected, installed feature remains visible when authentication or service
health needs attention. Reload after installing or removing tools. Rerun setup
or refresh the saved setup to publish changed selections and apply this policy
to an existing installation. Older manifests without agent/Git selections keep
those controls hidden until refreshed; they do not infer intent from binaries.
Selecting `--web-panel` alone does not install agent tools or a task-worker
service. Without selected and installed Codex or installed publishing tools,
the panel starts no prompt scheduler or publishing poll. Selected, installed
Codex enables prompt scheduling;
Butler/SteamCMD can enable publishing polling independently of Codex.
Machines with other agent runtimes but no Codex show runtime diagnostics without
prompt forms. Repository templates and the Git worktree option require selected,
installed Git; direct form submissions enforce the same worktree requirement.
Dashboard notifications appear only with ingest enabled; certificate help appears
when gateway metadata is available. Empty service and access sections are omitted.

Agent tools and Admin controls use grouped lists with aligned actions rather
than large option cards. Category links jump directly to system or data tools,
and to updates, services, or power controls. Tool rows show permissions and
suggested repetition/runtime; admin rows retain the full action effects and
explain unavailable controls. Rows wrap on smaller screens, with touch-sized
links. Dashboard metrics and service summaries use compact layouts while
keeping status labels, endpoint URLs, and numeric values visible.

The compact dashboard header shows the host, setup profile, and account.
Service totals distinguish responding endpoints, endpoints needing attention,
and endpoints without a readiness check. Audit totals include warning/error
events while retaining collection warnings. These summaries reuse the displayed
records; they do not start extra service or log queries.

**Mounted storage** includes separate `/home`, `/var`, `/boot`, and data
filesystems rather than assuming all storage belongs to `/`. Each row shows
its mount point, device, filesystem type, space available to ordinary users,
and inode use. Space percentages exclude reserved blocks from the available
capacity, as `df` does. Percentages round up using integer counters so large
filesystems retain accurate values. Usage at or above 80% is highlighted;
95% is critical.
Full inode tables are highlighted even when byte capacity remains available.
Read-only mounts have a text label. Filesystems without a fixed inode count
show **Not reported**, while failed measurements show **Unavailable**.

The snapshot probes common local filesystem types with `findmnt`, including
ext, XFS, Btrfs, ZFS, FAT, NTFS, and a container's root overlay. It does not
probe network filesystems, arbitrary FUSE mounts, virtual filesystems, or
container overlay layers. Unmounted partitions are not included. Bind aliases
of the same filesystem are deduplicated; distinct Btrfs subvolumes remain
visible and can share the same reported capacity. Capacity is not summed
across mounts. The view shows at most 32 filesystems, prioritizes mounts with
high space or inode use, and explains omitted records.

The inventory reads PID 1's mount table, so the panel service's filesystem
isolation does not create extra read-only entries. Capacity measurements still
use the panel account's access; inaccessible mount points show **Unavailable**.
The filesystem query has a two-second timeout and a 256 KiB output-read limit.
Missing tools, invalid data, and timeouts remain visible; root-only metrics
remain available as a fallback. These read-only snapshots use the panel
account's existing access and the same 30-second dashboard cache. They do not
scan directory contents, run SMART checks, or require administrator access.

**Resource pressure** shows Linux CPU, memory, and I/O stall averages over
10, 60, and 300 seconds. These indicate time when some tasks waited for the
resource, rather than CPU utilization or disk throughput. They can help
explain a slow host whose memory or load averages look ordinary. Unsupported
or unreadable kernel metrics show **Unavailable**. See the kernel's
[pressure stall information](https://docs.kernel.org/accounting/psi.html)
reference for the meaning of these averages.

**Agent activity** shows the panel's running and queued prompts, enabled
schedules, drafts, automatically paused tasks, and latest finished run. It
includes the next scheduled task and active-run timing at page load. Open a
run link to view that report directly in Agents. Counts use private saved panel
state without launching diagnostics or prompts; other terminal-agent sessions
are not included. Missing task storage and scheduler errors remain explicit.

## Install and sign in

HTTPS is recommended:

Replace `REPLACE_WITH_A_UNIQUE_PASSWORD` with a unique secret before running
either command; the placeholder is not a usable password. Command-line
passwords can appear in shell history and to local process inspectors.

```bash
basaltw setup agent_vm 192.168.1.50 agent \
  --web-panel \
  --web-panel-password 'REPLACE_WITH_A_UNIQUE_PASSWORD' \
  --ssl
```

The default port is 80 for HTTP or 443 with `--ssl`. Use another port when
needed:

```bash
basaltw patch 192.168.1.50 agent \
  --web-panel 9443 \
  --web-panel-password 'REPLACE_WITH_A_UNIQUE_PASSWORD' \
  --ssl
```

Setup prints the panel URL. The Basic Auth username is the setup username. The
password is hashed before upload and is not saved or reconstructed. Repeat
`--web-panel-password` to rotate it; omit the flag on a later patch to retain
it. If the setup username changes, supply a new password. Use a separate
password for [privilege approvals](../agents/privilege-approvals.md).

With `--ssl`, the panel uses a suitable existing certificate or the managed VM
CA. Enroll that CA on your client when required; see [Client CA trust](client-ca-trust.md).
Without TLS, Basic Auth crosses the network as plaintext.

## Browser desktop

Add `--guacamole` to an HTTPS panel setup to install an embedded browser desktop.
It enables the shared desktop without opening RDP ingress. Open **Desktop
(Guacamole)** under **Services**, enter the setup account's Unix password, and
connect to observe. On agent systems, **Take control** pauses agent desktop
actions and **Resume agent** explicitly resumes them. Other systems show
ordinary desktop controls, including **Release control**. The panel password
and Unix password remain separate.
Use `--no-guacamole` to remove browser access while retaining the desktop.
The same installation provides **Console**, a separate Unix shell with phone
keyboard controls, text sizes and terminal resizing. It uses the Unix password
through a private OpenSSH Unix socket, leaving public SSH authentication policy
unchanged. Connecting to the console does not start or pause the desktop.
It closes on disconnect or expiry; use `tmux` for persistent work.
See [Guacamole browser desktop](../workstations/guacamole.md) for installation, limits,
security boundaries and recovery.

## Git and credential settings

Open **Git & credentials** to set the setup account's commit name and email.
Changes apply immediately to its global Git config; repository overrides still
take precedence. New agent VMs receive missing author fields from the controller
even when GitHub credentials are not copied. Use `--git-name` and `--git-email`
on setup or patch to save an explicit per-VM override on the controller, or
`--git-identity none` to disable automatic identity seeding. Explicit controller
overrides are reapplied on reruns, so update them too after changing a VM's
identity in the panel.

On an HTTPS panel, paste a classic or fine-grained GitHub token into **New GitHub
access token**, then select **Save or replace token**. The panel privately replaces
this account's `github.com` entry and configures the GitHub CLI credential helper
for Git HTTPS access. Other GitHub host entries remain intact. Tokens are never
prefilled or shown in responses; failed submissions require re-entry. Select the
repositories and provider-side permissions the VM needs; saving a token confirms
storage, not provider acceptance. Test the intended repository separately.

**Remove stored GitHub access** removes the `github.com` file entry after its
checkbox is selected. It does not revoke the provider token or remove separate
keyring/environment credentials. Revoke the token at GitHub when appropriate.
HTTP panels keep token controls disabled; use an HTTPS setup or the existing
SSH credential-import command. Git identity editing remains available.

The page edits only the non-root setup account running the panel, with owned
files and safe paths. Root-profile panels show controller guidance because
their dedicated service account has no user home. Agent subscription sign-in
continues through the existing target-owned device login flow, linked from this
page and documented in [Agent authentication](../agents/agent-authentication.md).

## Agent VM approvals

An agent VM configured with `--privilege-broker [PORT]` receives a
**Privilege approvals** service link. It opens a separate HTTPS page with its
own password and service identity. **Admin controls → Privilege approvals**
also lists this account's recent broker requests, including agent follow-ups,
service restarts and commands. Review status, expiry and safe execution results,
cancel unclaimed requests, or open the exact action in the approval portal.
The standalone portal works on minimal installations without the web panel.
The panel cannot approve actions or read the
approval credential. Use [Privilege approvals](../agents/privilege-approvals.md) for the
agent and user workflow.

## Admin controls

Open **Admin controls** for host updates and power actions. Host actions require
the optional [privilege approval service](../agents/privilege-approvals.md), supported
for non-root operators on agent and GitHub runner VMs. Without it, Admin controls
shows setup guidance and inspection links. The panel does not gain root privileges
or access to the approval password. Agent tools, tasks, maintenance policy and
T3 Code updates live under **Agents**.

| Action | Behavior |
| --- | --- |
| Update system packages | Refresh Debian APT indexes, then upgrade installed packages; keep existing configuration files. Package hooks can restart services. No distribution upgrade, autoremove, or automatic reboot. |
| Check Basaltwater refresh | Validate `basaltw refresh --dry-run`; report whether the saved setup and source channel can be refreshed. |
| Refresh Basaltwater | Upgrade the installed managed source channel and replay the last successful local setup with `basaltw refresh`. This can reconcile configuration and restart services. |
| Check web configuration | Run `nginx -t` without reloading. |
| Reload web gateway | Run `nginx -t`, then reload Nginx only if validation passes. |
| Restart web panel | Restart the UI; independent task work continues. Older installations with panel-owned scheduling interrupt active prompts. |
| Restart / shut down host | Schedule reboot or power-off two minutes after approval; interrupt all running sessions. |
| Cancel scheduled power action | Cancel a still-pending scheduled reboot or power-off. |

Choose **Review action**, read its effects, and select **Create approval request**.
Power actions also require the exact displayed host name. Open the request's
**Open approval review** link and approve or deny it with your separate password.
Use **Cancel request** to withdraw an unclaimed request; cancellation cannot undo
an action already started. Approval expiry follows the installed broker policy.
Cancelling a scheduled power action is a new approval request: allow time to
approve it before the two-minute deadline, or use the administrator console.
After power-off, starting the machine requires its VM controller or physical
access.

The screen shows recent broker requests across all operations and the latest
maintenance job's result, timestamps, and exit code. **Dispatched** means the
maintenance service started, not that its commands finished. A successful power
job confirms scheduling only. Review running work before updates or interruptions.

Maintenance runs in a separate root system service with a six-hour limit,
so restarting the panel or approval broker does not cancel it. One maintenance
job runs at a time; jobs and uncertain dispatches are never retried automatically.
Use **Refresh status** to inspect results. The latest job result and request
history survive panel restarts. Privileged command output is not exposed; use
administrator SSH or console access for detailed diagnosis.

Refresh reuses the installed source and saved successful Debian setup, including
controller-installed snapshots. It automatically prepares a managed checkout for
a snapshot and preserves local state and staged deployment data. Snapshot updates
follow the recorded branch, or `main` when no branch was recorded. The previous
source is retained for recovery. Writable or unidentified source trees are
rejected. The refresh check validates local state without fetching;
it does not preview upstream code changes. No credentials are copied and no
interactive login is started by refresh. Saved coding-agent selections are
retained even if profile defaults change, including a selection with no agents.
Legacy records without a saved selection still use profile defaults.
The screen explains unavailable actions rather than granting wider privileges.

## Agent tools

Open **Agents → Agent tools** to prepare guided tasks for the existing Codex
runner, including checkups, maintenance planning and disk reviews.
Service diagnostics and local service cards carry the selected
service into a log review; scheduled job cards carry the job into a timer review.
These shortcuts open a setup form and never run a prompt automatically.

| Tool | Result | Suggested repeat / cap |
| --- | --- | --- |
| System checkup | Evidence-backed resource, service, update, backup, and agent readiness findings | Once / 20 minutes |
| Review service logs | Grouped errors, likely causes, and next checks for a fixed service and rolling time window | Once / 15 minutes |
| Review a scheduled job | Timer and process evidence, missed runs, failures, and possible overlap | Once / 15 minutes |
| Check Debian update health | Package consistency, held packages, existing index age, update results, and pending reboot evidence | Once / 15 minutes |
| Review disk cleanup | Space consumers, ownership, retention implications, and cleanup candidates | Once / 15 minutes |
| Check network and clock readiness | Local routes, resolver and clock state, plus bounded probes to explicitly configured endpoints | Once / 15 minutes |
| Plan system maintenance | Prioritized repairs with prerequisites, interruption risks, commands, and verification steps for operator review | Once / 30 minutes |
| Check data quality | Aggregate schema, encoding, missing-value, duplicate, and consistency findings | Once / 20 minutes |
| Import and normalize data | A new CSV, JSON, or JSONL dataset with count/type and round-trip validation | Once / 30 minutes |
| Export a dataset | A local directory bundle with checksums, a manifest, and offline restore instructions | Once / 30 minutes |

Select **Set up task**, fill in its inputs, then **Prepare prompt for review**.
The resulting Agents form shows the full prompt and normal model, effort,
runtime, permission, failure-limit, and repetition controls. Choose **Start task**,
**Create schedule**, or **Save draft** only after reviewing it. Preparation
neither saves nor queues a task. Reports and saved execution settings appear in
**Agents → Run history**. There is no separate scheduler or T3 Code dependency.

System tools inspect and recommend changes. Repairs remain operator actions
through separately approved **Admin controls** or the administrator console.
Log reviews use `basaltw agent diagnostics` to read allowlisted runtime properties
and at most 100 redacted recent entries at run time using the selected service,
time window, and severity; message-text searches are not copied. Each underlying
query has a five-second timeout and a 64 KiB output limit. Job reviews default
to seven days and all priorities so successful weekly runs remain visible.
The service selector prefers installed units and retains explicit historical links.
Repeated reviews use a rolling window and may report the same event again.
They report inaccessible evidence rather than seeking elevated access.

System checkups, maintenance planning, update/network checks, and service/log/job reviews use workspace
permissions with command network access and an empty private directory for each
run. This permits local service sockets and readiness probes while keeping the
source home outside writable roots. Temporary writes remain disabled, and the
prompts prohibit host changes. Disk-only reviews retain inspection mode.
Update checks use existing APT indexes and never refresh or repair packages.
Network checks use explicitly configured endpoints without scanning or inventing
probe targets; missing targets and inaccessible evidence remain coverage limits.

Data tools require an existing dedicated working directory inside the panel
account's home. Source and output paths must be below it; relative paths are
resolved against that directory. Hidden paths, credential-file paths, symlink
paths, and overlapping source/output paths are rejected during preparation.
Stage an explicit local dataset first. Import requires an existing `.csv`,
`.json`, or `.jsonl` file and converts to the chosen output format; it does not
load a live application or database. Export packages a selected local file or
directory with ordinary attachments; it does not upload to remote storage.
Data read by the agent may be sent to its configured model provider.

Writing data tasks use workspace mode with command network and temporary writes
off. Their prompts require fresh private run directories, preserved sources,
checksums, manifests, and validation before claiming success. Data reads are
bounded in the prompt to 500 files and 100 MiB; credentials, hidden files,
symlinks, mount crossings, imported code, archives, and live databases are
excluded. Imports stop for ambiguous or lossy conversions rather than silently
repairing records. Repeating tasks produce new output without purging old
bundles; monitor output growth separately.

The former quarantine starter was removed: moving files on the same filesystem
does not reclaim space. Use the disk review to prepare cleanup recommendations.

These task-specific limits and revalidation requirements guide the agent; the
runner enforces the selected Codex sandbox, runtime, and output limits. The
workbench is not a deterministic migration or cleanup engine. Review the
manifest and results, and retain an independent backup for valuable data.
Editing a prepared prompt changes its instructions; saved tasks retain that
exact text for later runs.

## Maintenance agents and inbox

Open **Agents → Maintenance** to enable the installed supervisor, set its check
interval, model choices, reasoning effort and daily attempt limits, or request
a check. Panel setup prepares disabled supervision; `--maintenance-agent`
enables it during setup. Checks continue independently of the panel and T3 Code.
Healthy checks make no AI calls. Opt-in housekeeping requests fixed existing
cleanup jobs; AI reviews provide recommendations.

The tab leads with the saved enabled state, check interval, and **Check once now**.
This manual request works with automatic maintenance disabled; it does not
enable schedules or request housekeeping. Host checks, security audits, and
housekeeping have separate controls. The settings disclosure is closed by
default and its summary shows the selected model and daily AI attempt limit;
open it to edit automatic schedules, housekeeping, model choices, and review
limits. The maintenance records section follows the settings. Incomplete checks
retain a warning even when they recorded no findings. Failed submissions
preserve entered settings for correction while the status summary continues to
show the saved policy.

Model dropdown choices come from the panel account's local Codex model cache.
Saved model IDs remain selectable even when absent from that cache, and an
optional custom model ID can override the selected routine or escalation model.
Model availability still depends on the account and Codex installation.

**Automatic restart hold** exposes the same account-local hold as
`basaltw agent maintenance hold`. Expand **Manage restart hold** to choose
1–72 whole hours (default 8), extend
the hold, inspect its UTC expiry, or release it early. Holds defer managed
automatic restarts; checks, package updates, housekeeping and manual restarts
continue under their existing policies. Opening the page never creates or
extends a hold. The controls require a writable home owned by the panel
account; locked service accounts cannot create holds for another user.

Open **Agent inbox** to read a mailbox, send a message, or acknowledge one after
handling it. Unread messages appear first with sender labels and UTC timestamps;
**Mark read** explicitly acknowledges a handled message. **Write message** opens
the composer without JavaScript. If sending fails, the mailbox, recipient,
sender, and draft text remain available for correction. Messages are limited
to 4,000 UTF-8 bytes, so non-ASCII text can reach the limit in fewer characters.
Terminal workers can use `basaltw agent inbox wait` between tasks
for cooperative wake. Messages survive restarts and reading never acknowledges
them. Mailboxes share the panel account's trust boundary; root-profile panels
cannot expose another account's inbox.

See [Maintenance agents and cross-session mail](../operations/maintenance-agents.md) for
installation and sign-in, flags, escalation rules, storage limits, recovery,
and the terminal-worker protocol.

## Agent prompt tasks

Open **Agents** to run Codex prompts against the host or a repository. Install
and authenticate Codex as the non-root account running the panel. Other
terminal agents are inventoried by diagnostics but do not execute prompt tasks
in this initial version. Root-managed panels use a locked service account
without a home and show diagnostics with prompt execution unavailable.

1. Select **New task** to write a custom prompt, **Templates** for a starting
   point under **Repository work** or **Host checks and maintenance**, or
   **Guided tasks** for system and data tools. Each template shows its execution mode, suggested
   repeat interval, runtime cap, and additional permissions before selection.
2. Enter the working directory. Repository templates require you to choose
   the specific checkout; host inspection defaults to the account's home.
3. Choose **Inspect only** for Codex's read-only sandbox, or **Workspace
   changes** for a directory inside the account's home. Enable command network
   access for service sockets, endpoint checks or package downloads, and temporary writes when
   tests or package tools need `/tmp` or `TMPDIR`. Provider requests still use
   the network in either mode. Configured hooks and MCP integrations retain
   their own permissions.
4. Select a model and reasoning effort, or keep the configured defaults. The
   model dropdown reads Codex's local cache without contacting the provider;
   **Model and web search** accepts a custom model ID when needed. Model access
   and supported effort levels depend on the installed CLI and account.
5. Set **Maximum runtime** in whole minutes, from 1 minute to 7 days (default
   30 minutes). Waiting and sleeping count toward this wall-clock limit. This
   is a duration cap, not an exact cost or token budget.
6. Choose once, hourly, daily, weekly, or **Custom interval**. Custom intervals
   accept 1–43,200 whole minutes (30 days): 360 means every 6 hours; 20,160 means
   every 2 weeks. The custom field applies only when Custom interval is selected.
   **Start task** queues an immediate run and enables repetition when selected.
   **Create schedule** uses the first-run time when set; otherwise it starts
   after one interval. **Save draft** stores an inactive task, including a
   one-time prompt, without running it or enabling repetition. Before Codex is
   installed, drafts can be prepared through the task CLI; panel prompt forms
   appear once Codex is installed.
7. Open **Scheduling options** to set a first-run time, custom interval, or
   **Pause schedule after failures** from 1 to 10, or 0 to keep repeating.
   The default is 3. Failed, blocked, or interrupted runs count toward this
   limit; cancellation preserves the count, and progress or completion resets it.

Open **Context and workspace** to choose **Context persistence** independently of **Workspace**. A checkpoint
carries findings and next steps into a new conversation; conversation mode
resumes the exact saved Codex session. Managed directories begin empty; a
persistent worktree starts from the source repository's committed HEAD and
retains unfinished task changes. Managed workspaces require workspace mode.
Open **Goal and usage limits** to define completion criteria, continuation,
attempts, a deadline, reported-token budget, daily limits and evidence retention.
A goal needs at least one total limit. **Context and evidence** reads its saved
checkpoint and receipts; **Reset context** preserves workspace files and history.
See [persistent tasks](../agents/agent-tasks.md) for CLI/MCP controls and detailed limits.

The starting templates cover these tasks. Their prompts and settings remain
editable before submission; selecting a template does not run or schedule it.

| Template | Default repeat | Runtime cap | Scope |
| --- | --- | --- | --- |
| Repository health check | Once | 30 minutes | Inspect only |
| Repair failing checks | Once | 60 minutes | Workspace changes; command network and temporary writes |
| Update repository dependencies | Once | 30 minutes | Workspace changes; command network and temporary writes |
| Update repository documentation | Once | 30 minutes | Workspace changes; temporary writes |
| Review repository security | Once | 30 minutes | Inspect only; live web search for advisories |
| Review release readiness | Once | 20 minutes | Inspect only |
| Host maintenance review | Once | 30 minutes | Workspace permissions; command network; private run directory |
| Check Debian update health | Once | 15 minutes | Workspace permissions; command network; private run directory |
| Check network and clock readiness | Once | 15 minutes | Workspace permissions; command network; private run directory |
| Check backup health | Once | 15 minutes | Workspace permissions; command network; private run directory |
| Review storage cleanup | Once | 15 minutes | Inspect only |
| Triage a service incident | Once | 20 minutes | Workspace permissions; command network; private run directory |
| Check certificate expiry | Once | 10 minutes | Workspace permissions; command network; private run directory |

Repository editing templates ask for a clean existing/shared checkout,
including untracked files, and leave changes for review. They default to Once
so another editing run does not immediately block on the previous review backlog.
A managed persistent
task worktree may continue its own unfinished changes after inspecting them
against the checkpoint; unrelated changes still require review. Existing saved
prompts retain their original instructions. The templates prohibit stashing,
resetting, cleaning, or committing existing work to clear the checkout.
Dependency updates follow the project's package manager, lockfiles, and upgrade
policy, with larger migrations reported for follow-up.
Host check templates inspect existing evidence and recommend follow-up:
backup checks do not perform restores, storage reviews do not delete files,
and certificate checks do not renew certificates or change trust. Missing
configuration or unavailable evidence is reported explicitly. Security review
uses web search for current advisories and distinguishes confirmed version
matches from uncertain findings.

Host service review templates select a private run directory and command network access;
the writable directory is empty scratch space. Their prompts prohibit host file
changes and service mutations. Inspect-only storage reviews need neither grant.
Saved tasks and reused receipts retain their original permissions: create a new
task from the refreshed template to replace a blocked inspection task. Merely
resuming it does not change its sandbox. Host reviews use explicit doctor
capabilities; project manifest discovery requires an actual Git checkout.

The catalog opens host checks first and leads repository work with a health
review. The restricted maintenance duplicate, empty repair-plan starter, and
speculative regression-test starter were removed. Concrete repair requests can
be prepared through follow-up review or the custom form with exact targets,
actions, verification, and rollback. Existing saved tasks and receipts retain
their original prompts and permissions. Privileged actions require the
configured Admin approval workflow.

Runs can record a **Follow-up requested** proposal for checks or repairs that
need different access. **Review follow-up task** opens its actions and proposed
permissions in the normal task form, defaulting to Once and one attempt. Review
the original evidence, adjust scope, then save or submit. Opening it never
creates or runs work, changes the original task, or approves privileged actions.
Requests remain available in retained receipts after recent history is evicted.
For a service prerequisite, **Approve once** creates the missing broker request
and shows **Open service approval** in the form. Approve the exact operation
with the separate portal password, return, then **Check approval and launch**.
The panel preserves reviewed settings and checks status only on submission.
Restarts run before the task starts: a repair that must precede a restart needs
separate preparation and restart-verification proposals.

Additional templates appear when the panel's saved setup configuration or
commands installed for its account indicate a relevant integration. Each shows
the detection reason. These checks do not run commands or prove service health,
browser readiness, repository compatibility, or authentication. Repository
templates still require you to select a suitable checkout.

The catalog and task form explain known conflicts with local Codex administrator
requirements, including forbidden sandbox/search modes and restricted permission
profiles. The runner checks again before provider launch and records a blocked
outcome with zero token usage when these requirements prevent execution. Drafts
remain available; policy is never relaxed or retried with broader access. This
preflight covers known local constraints, while Codex still enforces other
configuration layers and integrations retain their own permissions.

| Conditional template | Appears when | Default repeat / cap | Scope |
| --- | --- | --- | --- |
| Check T3 Code readiness | T3 Code is selected and its runtime or user service unit is installed | Once / 15 minutes | Workspace permissions; command network; private run directory |
| Review container health | `docker` or `podman` is installed | Once / 15 minutes | Workspace permissions; command network; private run directory |
| Review web hosting | Web server is configured, or `basaltwater-web` or `nginx` is installed | Once / 15 minutes | Workspace permissions; command network; private run directory |
| Check shared storage | Samba is configured, or `smbd`, `mount.cifs`, or `sshfs` is installed | Once / 15 minutes | Workspace permissions; command network; private run directory |
| Check remote desktop readiness | Remote desktop is configured or `xrdp` is installed | Once / 10 minutes | Workspace permissions; command network; private run directory |
| Review GitHub Actions failures | Git and `gh` are selected and installed | Once / 20 minutes | Command network; private run directory; source checkout read-only |
| Run browser smoke checks | `basaltwater-playwright-mcp` is installed | Once / 30 minutes | Workspace changes; command network and temporary writes |
| Validate Godot web export | `godot` or `godot4` is installed | Once / 60 minutes | Workspace changes; temporary writes |
| Check HomeBox data protection | HomeBox is configured | Once / 15 minutes | Workspace permissions; command network; private run directory |
| Check Gogs repository hosting | Gogs is configured or `gogs` is installed | Once / 15 minutes | Workspace permissions; command network; private run directory |
| Check Antistatic services | Antistatic lobby or DB is configured | Once / 15 minutes | Workspace permissions; command network; private run directory |
| Check notification delivery | Panel notification ingest is enabled | Once / 10 minutes | Workspace permissions; command network; private run directory |
| Check privilege approval readiness | Privilege approvals are configured | Once / 15 minutes | Workspace permissions; command network; private run directory |

GitHub CI inspection needs command network access; the runner offers that in
workspace mode with a private run directory. Its prompt uses the recorded source
path for local inspection, requests no remote actions, and reports unfinished
checks as pending without watching or repeatedly polling them.
Browser checks use isolated loopback previews and synthetic data, without
creating gateway previews or forwards. A healthy managed browser installation
does not prove that browser tools are exposed to the unattended session; missing
tools are reported as blocked coverage. Godot exports use a documented ignored
output directory and report missing presets
or export templates instead of downloading them. Other integration checks
inspect existing state and recommend follow-up without repairs or privileged
actions. Availability filters only the starting catalog: saved tasks retain
their prompts and settings if an integration is later removed. Opening a stale
template link explains why it is unavailable and leaves the custom form usable.

**Model and web search** controls Codex's web search independently of command network
access: disabled, cached results, or live search. Runs use ephemeral Codex
sessions by default; enable **Keep fresh Codex session history** to retain them
in Codex too. This does not resume them. Conversation persistence always retains
its session. Run evidence is retained with every choice.

Saved tasks use responsive cards with status, repetition, next run, latest
outcome, and working directory. Active and queued work appears first, followed
by tasks needing review. **View latest result** opens the retained report
directly; **View live run** opens a running task's current record. Use
**Pause**, **Resume**, **Edit**, **Run once**, or **Remove** to manage them.
Use **Start schedule** to activate a recurring draft. Running a draft manually
does not activate its repeat schedule. **Duplicate** opens a new form with a
saved task's settings so you can adjust its directory, model, or prompt. Run
history's **Reuse run settings** uses the prompt and execution settings recorded
for that specific run, even after the task is edited or removed. Reuse defaults
to Once to avoid enabling another recurring schedule inadvertently. Neither
link saves or executes work until you submit the form. **Task settings and more
actions** contains duplicate, context, removal, and reset controls, plus full
execution settings. Removing a task keeps its retained run history.

Schedules that reach their failure limit show **Auto-paused** and the failure
count. Inspect the last run, edit the task if needed, then use **Resume** to
reset the count and schedule its next interval. A successful manual recovery
run clears the count but leaves the schedule paused until resumed. Timeouts
and CLI startup errors count as failed runs. A completed CLI process does not
prove the requested task succeeded. Structured reports distinguish progress,
completion, waiting, blocked and failed work; blocked reports count toward
failure pausing even with a successful process exit. Review evidence before
resuming work.

The screen leads with task status and offers **New task** prominently. Workflow
links lead to templates, guided tasks, run history, and setup/diagnostics.
The task form keeps the prompt, directory, permissions, runtime, and repetition
visible; disclosures group model/search, context/workspace, scheduling, and
goal/usage settings. Relevant groups open for non-default settings and after
validation errors. Selecting a template, editing, duplicating, or reusing
settings puts the selected form before the saved-task list. All task controls
and disclosures work without JavaScript.
Pausing stops future work and clears a queued run; cancel an active run
separately. Cancellation stops processes, but does not undo completed edits.
Run history retains the prompt and settings used, timestamps, duration, exit
code when available, and bounded output. Summary cards show the active/queued
work, repeating schedules, drafts, automatically paused tasks, recent outcomes,
and tasks needing review. Run history shows recorded wall time for retained
finished runs and puts execution settings inside each report's disclosure.
Outcome badges distinguish blocked or failed work from a completed CLI process.
These are retained-history
totals, not lifetime usage or spending estimates. Active runs show elapsed time
and remaining runtime at page load. Use **Refresh status** to update results
and timing without automatic reloads that would discard a prompt draft.
Refreshing a selected run, task context, or receipt keeps that evidence open.
Unreadable task storage shows unavailable counts and a retry link on the task
list; it does not claim that the account has no tasks or run history. A submitted
prompt stays visible with instructions to copy its settings before reloading.
Execution controls remain disabled until storage recovers.

New non-root Debian setups with Codex selected install an independent task
worker. It runs without the panel or T3 Code, and the screen reports its live
lease separately from installed unit presence. Its system service receives
`systemd-journal` supplementary access for bounded log reviews, matching the
panel's journal access without adding that group to the login account. Rerun
Basaltwater setup to update an existing worker's unit; source updates alone do
not change a running service's groups. Older installations retain panel
scheduling until setup installs the worker. Tasks persist across panel and host restarts. After
downtime, each overdue schedule runs at most once, then advances to its next
interval. Deadlines crossed while a task is running are skipped, preserving its
cadence without immediately starting another run of that task. An explicit
Run once request remains available. Changing the elapsed interval resets the next
deadline to a full interval from saving; editing other settings keeps the existing
deadline. Paused tasks and drafts remain inactive after edits. These are elapsed
intervals rather than calendar times. Runs interrupted by a restart are labelled
for review. The runner executes serially across its shared CLI/panel controls;
select a managed task worktree when editing could overlap another agent session.
Removing the panel leaves independent tasks running; pause them explicitly.

Open **Agents → Readiness** for installation and sign-in checks.
**Check agent readiness** collects versions and local credential metadata for
selected, installed Codex, Claude Code, OpenCode, and GitHub CLI, plus
maintenance-hold state and the timestamp of any saved readiness record.
T3 Code is checked when selected and installed. Its account-level updater lives
here too, with elapsed time and command output. Updates continue in the background;
use **Refresh status** to check progress. Active T3 sessions may reconnect.
Leftover unselected agent tools and T3 installations are not probed or offered
as starting templates. Each readiness request resolves current feature selections.
Credential file presence is not proof of provider authentication. Checks run
in the background and do not launch prompts or automatically repair the host.

Prompt execution uses the account's existing privileges. Host repairs that
need elevation use the configured [privilege approval workflow](../agents/privilege-approvals.md);
the panel does not grant root access. The templates prepare changes for review
and do not ask the agent to commit, push, or deploy.

Runs receive their saved execution settings in the prompt instructions and are
told to report blocked checks instead of bypassing sandbox permissions, starting
login, or waiting for an operator. Package and test tools may need a documented
per-command cache path inside the workspace or permitted temporary directory;
temporary writes do not grant access to caches elsewhere in the account's home.
Prompts must keep waits within the runtime cap and avoid detaching work into
another service. Cancellation does not undo changes or withdraw privileged
broker requests; external integrations can own work outside the local process
group. The [managed agent skills](../agents/agent-skills.md) include unattended task guidance.

## Game publishing controls

Open **Publishing** for Butler/SteamCMD installation information, documentation
links, native login challenges, destinations, retained builds and upload history.
The same page manages unattended upload jobs, agent writing-task preparation,
draft imports, side-by-side translations, exact human review and post receipts.
Changes and login forms require HTTPS and a non-root panel account. Page loads
read local observations without running providers or uploading anything.

Steam default/public release and rollback remain human actions on Steamworks.
Optional beta promotion uses a separate VM-local publisher key and fresh branch
observations. Steam announcements and itch.io posts export reviewed plain text
for human editor handoffs; a scheduled handoff is not automatic publication.
Languages come from `basaltwater.json`, default to English, and initially have
English/Spanish structural tests. Each public language revision requires human
review. Provider sessions remain on the VM.

Use the [publishing operator guide](../workstations/game-publishing.md) for setup, CLI commands,
review and release gates, limits, recovery, and outstanding live qualification.

## Receive notifications

Enable the HTTPS receiver on an existing panel:

```bash
basaltw patch 192.168.1.50 agent \
  --web-panel --ssl --web-panel-notification-ingest
```

Open **Notifications**, expand **Reveal full sender link**, and add that URL to
a sender. It contains a bearer token, so treat it as a credential. The sender
uses the normal webhook setup:

```bash
basaltw setup agent_vm sender.example agent \
  --notify webhook 'https://PANEL_HOST/api/v1/notifications#TOKEN'
```

To disable receiving, patch with
`--web-panel --ssl --no-web-panel-notification-ingest`. For token rotation and
the API contract, see the [web panel reference](web-panel-reference.md#notification-ingest).
See [Notifications](notifications.md) for event meaning, delivery levels, and
retries.

## Troubleshooting

Run these checks on the panel host:

```bash
sudo systemctl status basaltwater-web-panel.service
sudo journalctl -u basaltwater-web-panel.service -n 100 --no-pager
sudo systemctl status basaltwater-web-panel-audit.timer
sudo journalctl -u basaltwater-web-panel-audit.service -n 100 --no-pager
sudo nginx -t
```

| Symptom | Next check |
| --- | --- |
| Setup rejects notification ingest | Include both `--web-panel` and `--ssl` |
| No sender events | Confirm sender URL, token, and HTTPS reachability |
| Audit activity is stale | Check the audit timer and service journal |
| Login is repeatedly rejected | Check Nginx and the `basaltwater-web-panel` fail2ban jail |
| Browser warns about the certificate | Follow [Client CA trust](client-ca-trust.md) |

Failed browser logins are written to a privacy-preserving Nginx log. Passwords
and Authorization headers are not logged.
