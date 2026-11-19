# Panel views and access boundaries

[Documentation index](web-panel.md)

The optional web panel is a browser dashboard for one managed machine. It
shows host health, services, shares, storage integrity, maintenance and agent
tasks. Agent tasks use the installed Codex CLI
under the panel account. When T3 Code is configured, coding work opens its
native workspace; independent host tasks do not require T3.

| Need | Open | Result |
| --- | --- | --- |
| Check host health | Overview | Uptime, load average, memory, swap, resource pressure, kernel, reboot state, and update timers |
| Check storage capacity | Mounted storage | Mounted local filesystems, available space, inode use, device/type, and read-only state |
| Estimate disk usage | Storage | An on-demand diagnostic with visual capacity, ranked host areas, services and projects, file counts and coverage notes |
| Find a managed endpoint | Services | Configured and discovered web, SSH, RDP, Samba, Gogs, HomeBox, and Antistatic access |
| Observe or control an agent desktop | Desktop | Optional browser access, mobile input, zoom and read-only readiness checks for the shared XRDP desktop |
| Open a shell without starting a desktop | Console, or Desktop → Open browser console | Guacamole SSH terminal with the Unix password; Desktop also offers an SSH command for an existing client |
| Inspect a service | Local service status or Service diagnostics | On-demand state, fixed runtime details, and filtered logs |
| Check maintenance | Scheduled jobs | Timer state, last result, and selected job logs |
| Inspect a GitHub runner | GitHub runner | On-demand local service state, repository, labels, read-only allowlist and recent gate outcomes |
| Enable tailored supervision | Agents → Maintenance | Host checks, security audits, opt-in housekeeping, bounded AI reviews, and retained findings |
| Exchange messages across sessions | Agent inbox | Account-local mailboxes with durable unread messages and explicit acknowledgement |
| Code and manage coding sessions | Agents → T3 Code | Native conversations, providers, goals, worktrees, schedules, PRs and preview |
| Run independent host work | Agents → Independent tasks | Bounded Codex tasks, recurring host checks and retained execution evidence |
| Configure commit identity and GitHub access | Git & credentials | Account Git name/email, private GitHub token replacement/removal, and agent sign-in guidance |
| Upload games and review release writing | Publishing | VM-local Butler/SteamCMD sessions, completed build uploads, schedules, translations, promotion and tracked editor handoffs |
| Prepare system and data tasks | Agents → Agent tools | Contextual checkups, log and job reviews, Debian update and network checks, maintenance plans, and local data work |
| Administer the host | Admin controls | Approved package updates, Basaltwater refresh, service maintenance, and host power controls |
| Manage shares and stored data | [Administration → Shares & Data](shares-data.md) | Samba access, backup/sync routines, scrub health and file recovery |
| Review audit activity | Audit activity | Sanitized recent events and collection health |
| Receive remote notifications | Notifications | Recent accepted events and an optional sender endpoint |

Navigation and read-only views work without JavaScript. Passive summaries update
in place when JavaScript is available. See the [web panel
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

Discovery reads saved selections, executable paths and unit files without running
tools or testing sign-in. Leftover binaries/history cannot enable unselected
agent or Git features. Unavailable optional routes return HTTP 404. Selected,
installed features stay visible when authentication or service health fails.
Reload after tool changes; rerun setup/refresh to publish changed selections.
Older manifests keep agent/Git controls hidden until refreshed.

Selecting `--web-panel` alone installs no agents or task worker. Codex enables
prompt scheduling; Butler/SteamCMD enable publishing polling independently.
Other agent runtimes expose diagnostics without prompt forms. T3 exposes its
workspace entry without requiring Codex. Independent task worktrees require
selected, installed Git. Dashboard
notifications require ingest; certificate help requires gateway metadata.
Empty service/access sections are omitted. See [feature discovery](web-panel-reference.md#feature-discovery-and-navigation)
for the full availability contract.

Agent tools and Admin controls group actions by task, with explicit effects
and unavailable reasons. Admin controls separates host actions, requests and
activity into their own views. **Shares & Data** separates Samba,
mirrors and integrity work; see its [operator guide](shares-data.md).
The dashboard header identifies the host, profile and account. Service and audit
totals reuse displayed records, distinguish unchecked endpoints and retain
collection warnings.

Kernel audit activity shows **Not applicable** on containers and WSL, matching
the auditd setup policy for VMs and hardware. The panel detects this capability
without reading root-only setup state. On applicable machines, missing tools,
failed collection and snapshots older than 15 minutes remain coverage warnings.
The five-minute audit exporter timer also refreshes storage inventory and stays
enabled on containers; its storage health is independent of audit applicability.

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

## Automatic status updates

Each eligible region shows its source health, **Updated … ago** and update
controls. The timestamp is when the source was observed or exported; a successful
request does not make an old export fresh. Hover the age for its UTC timestamp.
Missing or future source times show **Unknown**. **Incomplete** means coverage
is limited; **Stale** means the source exceeded its freshness limit.
**Unavailable** means collection could not provide values. Warnings remain
visible alongside any retained values.

| Region | Automatic update |
| --- | --- |
| Host readings | Every 30 seconds from the shared host cache |
| Web service summary and existing readiness labels | Every minute from shared discovery and probe caches |
| Proxmox inventory summary | Every minute, following the exported snapshot |
| Audit snapshot summary | Every five minutes, following the exported snapshot |
| Agent activity summary | Every five seconds while a panel task runs; otherwise 30 seconds |
| Storage diagnostic state | Every five seconds while scanning; otherwise every minute |
| Loaded local service status | Every 30 seconds after **Load local service status** |

**Pause updates** applies to all eligible regions in the current tab and survives
page navigation. **Resume updates** resumes them; other tabs keep their own
preference. **Refresh status** reads one region immediately, including while
paused. Hidden pages suspend polling and read again when visible. Requests have
jitter, a 30-second timeout and bounded retry backoff; server retry hints also
apply to manual requests. A delayed connection retains the last values and
their increasing source age. Authorization errors stop automatic retries until
you sign in and refresh status.

Only named summaries and readings change. Mounted filesystem rows, Proxmox
tables, audit history, agent reports and review decisions remain as loaded;
reload or open their existing links to inspect newer details. Changed agent
activity asks you to inspect runs or review requests again. Focus, disclosures,
filters and unsaved fields stay in place. Approval forms never update or submit
automatically.

Jobs, journals, runner inspection, agent readiness, notifications, certificate
trust and scans retain their explicit controls or page-load behavior. Polling
never starts a scan, prompt or privileged action. With JavaScript disabled,
the same initial readings, timestamps, navigation and forms remain available;
use page refresh links for newer snapshots. See the
[status API contract](web-panel-reference.md#passive-status-api) for budgets and
freshness thresholds.

## Storage usage diagnostic

Open **Storage** in the Inspect menu and choose **Run diagnostic**. Collection
runs in the background; its state and elapsed time update automatically.
**Load latest scan results** loads the finished report, preserving the selected
view. The previous result remains visible during a rerun, labelled with its own
scan time. Opening the page does not scan the filesystem.
Results remain in memory until the panel restarts.

Choose a **Scan depth** before starting: **Quick** allows 15 seconds and 200,000
entry observations; **Extended** allows 60 seconds and 2,000,000. A quick scan
that reaches either limit suggests an extended rerun. Only one scan runs at a
time. Extended scans can cover larger package stores and checkouts, but retain
the same filesystem and permission boundaries.

The capacity chart shows root filesystem usage, available space and reserves at
scan time. **Host areas**, **Services** and **Projects** show ranked allocated
space, logical sizes (file lengths and directory metadata) and file counts.
Services and projects are alternate attributions of files already included in
host areas; their totals must not be
added together. Conventional service paths and project directories are used,
so this does not discover every possible project location.

The diagnostic reads metadata with the panel account's permissions and never
reads file contents, deletes data, runs agent prompts or elevates privileges.
It scans only the root filesystem, skips symlinks and nested mounts, and uses
time and entry limits shown with the results. The time budget is checked between
metadata operations; it cannot interrupt a blocked filesystem call. Separate home/data filesystems
remain visible in **Mounted storage** but are outside this usage scan.
Verified same-path projections of the root filesystem, including the panel
service's read-only `/usr`, `/etc` and `/boot` binds, remain measurable. Bind
aliases, PrivateTmp remappings, other filesystems and ambiguous mount records
are excluded. Permission-denied paths are identified explicitly; increasing
scan depth does not grant access to them.
Known service locations and discovered projects each receive a share of the
remaining time and entry budgets before the rest of the host is scanned. Large
locations can resume afterward with spare budget, so a single large checkout
or package store cannot monopolize the initial scan. Project discovery itself
is bounded; omitted projects and unfinished locations remain explicit in the
coverage notes.
Inaccessible paths and exhausted limits produce explicit partial estimates;
unavailable locations are not reported as empty. Partial numbers are lower
bounds. Sparse files, shared blocks, filesystem metadata and deleted files held
open can make directory estimates differ from filesystem capacity usage.

For UI development, the local panel's diagnostic returns synthetic results;
the degraded scenario demonstrates incomplete coverage without inspecting the host.
