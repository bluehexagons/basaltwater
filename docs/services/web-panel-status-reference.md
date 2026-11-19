# Discovery and status API

[Documentation index](web-panel-reference.md)

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

## Passive status API

`GET /api/panel/v1/RESOURCE` reads a fixed passive resource through the existing
authenticated panel proxy. It requires the proxy-supplied account identity;
unknown resources and absent optional features return 404 before inspecting
state. Query fields are rejected. All responses remain `no-store`.

| Resource | Owner and cache | Poll interval | Stale after |
| --- | --- | --- | --- |
| `host` | Shared host overview; 30 seconds | 30 seconds | 90 seconds |
| `web` | Shared service discovery; 60 seconds, independently cached loopback probes | 60 seconds | 180 seconds |
| `proxmox` | Existing minute export; read at most once per minute | 60 seconds | 180 seconds |
| `audit` | Existing five-minute export; read at most once per five minutes | Five minutes | 15 minutes |
| `agents` | Bounded saved task state; Codex feature required | Five seconds while running, otherwise 30 seconds | 90 seconds |
| `storage` | Fixed in-memory diagnostic metadata; never copies the report or starts a scan | Five seconds while running, otherwise 60 seconds | 180 seconds |
| `services` | Existing fixed-unit cache; unavailable until explicitly loaded | 30 seconds | 90 seconds |

The schema-1 envelope contains `resource`, UTC `observed_at` (or null),
`freshness`, `warnings`, `interval_seconds`, `stale_after_seconds`, bounded
`data`, and `revision`. Source freshness is `fresh`, `stale`, `unknown`,
`incomplete`, or `unavailable`. Missing or future observations are unknown;
missing metrics are never converted to healthy zeroes. Stale partial snapshots
retain their collection warning. Export timestamps belong to their exporter;
reading an old export does not make it new.

Host, web, and loaded-service values travel with their observation metadata in
one cache reading. The initial HTML and its embedded status use that same
reading, so concurrent collection cannot attach a newer timestamp to older
displayed values. If a newer successful snapshot omits a displayed metric, the
browser marks it unavailable and offers a reload to inspect the current list.

An in-memory client may send the returned quoted `ETag` in `If-None-Match`.
An unchanged resource returns 304 with no snapshot body. Authentication and
feature checks still run. Validators cover observation time, values, warnings
and freshness, excluding browser fetch time. No public cache is introduced.
Storage metadata is observed directly on each read so a just-started scan cannot
inherit a cached idle interval. Exported reports retain their separate timestamps.

Each panel admits eight concurrent status requests, two source collectors
across all sources, and one collector per source. Followers immediately receive
the previous reading, or 503 with `Retry-After: 5` on a cold/busy source.
Failed collection is negatively cached and retains the last observation time.
The production listener admits at most 32 request threads and times out idle
request I/O after 15 seconds; excess connections receive a bounded 503 retry
response. These bounds apply independently of browser tab count.

Web discovery commands have ten-second and 256 KiB output limits, with at most
64 displayed services and four HomeBox probes per collection. Each probe has a
one-second socket timeout and a one-second total response deadline after its
request is sent, preventing a slow stream from holding a collector. Probe
results and certificate-trust checks have separate 60-second caches; TLS checks
remain page-load work and are not polled. Responses are capped at 24 KiB and
contain only fixed summary fields. They exclude mounted paths, credentials,
URLs, prompts, reports, logs and raw collector diagnostics. On-demand jobs,
journals, runner checks, notification histories and approval forms have no
polling endpoint.
The web summary uses the oldest contributing probe observation, so a newer
discovery read cannot make an older independently cached probe appear fresh.

The browser uses the CSP hash-pinned `/assets/panel-refresh.js` script and
same-origin fetches. Each named region has one request in flight. Initial
requests jitter by 0.5–3 seconds; ordinary intervals add up to 10% jitter.
Failures use exponential backoff capped at five minutes plus up to 20% jitter,
and honor `Retry-After` seconds or HTTP dates. Requests abort after 30 seconds,
on page hide, or when paused. The pause preference lives in `sessionStorage`.
Authorization failures stop automatic reads and retain the sign-in notice
across pause and visibility changes; a manual refresh retries after sign-in.
Relative ages advance locally; unchanged 304 responses do not announce a value
change. A source-health transition or changed value uses a polite status region
without moving focus. Detailed tables and editable forms remain page-load
snapshots. Mounted storage does not inherit the live host region's timestamp.

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
from failed or unavailable health checks. An incomplete storage inventory cannot
establish that a backend is unconfigured.

Guest tables support search and filters by name, VMID, node, state, type and
tag, and sorting by name, VMID, CPU or memory. Guest filters affect only the
guest table; node selection also filters tasks, storage and allocation
summaries. Clustered views default to the cluster. Task activity shows the
latest bounded shared task list of up to 50 tasks, including running, succeeded,
warning, failed and unknown states; it does not expose raw errors or logs or
provide a complete task archive. Malformed task records leave an incomplete or
unavailable notice while valid records remain visible. Native Proxmox links
open guest, node or task details for investigation. Storage pools show their
reported state, type, shared flag and capacity, with warnings at 80% and 95%
use. Pool totals can overlap; do not add them together. Guest disk capacity is
allocated space, not guest filesystem free space. **Open Proxmox VE** uses the
configured native address on HTTPS port 8006, including IPv6, and Proxmox's own
login and certificate.

The overview controls use bounded GET fields `pve_q`, `pve_node`, `pve_type`,
`pve_state`, `pve_tag` and `pve_sort`. Controls retain the selected values; a
reset link clears them.

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
per guest. An oversized export preserves the last readable snapshot, whose age
warning becomes visible if refreshes keep failing. Resource history and backup
freshness are not provided. Core cached
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
see Proxmox's `/etc/pve` and `/var/lib/pve-cluster`. The collector uses AF_UNIX,
AF_INET and AF_INET6 for local Ceph monitor communications; `ProtectHome=true`
remains in place. Its private device namespace exposes only the optional
`/dev/zfs` control device in addition to standard pseudo devices, so local
`zpool` status reads work without exposing disks. It does not use remote-node
SSH, scan storage backends, accept arbitrary query parameters or paths,
export raw descriptions/configuration, task errors,
credentials or command output, or control guests. ZFS health covers only pools
on the local node; snapshot presence does not mean remote ZFS was collected.

The panel revalidates bounded JSON on read, warns when the snapshot is more than
three minutes old, and distinguishes unavailable or failed collection from a
successfully empty inventory. Unknown metrics remain unavailable. Refreshing
the dashboard reads the cache and launches no commands.
Existing installations must rerun panel setup to install the updated service
and collector. To investigate missing or stale data, inspect
`systemctl status basaltwater-web-panel-proxmox.timer` and
`journalctl -u basaltwater-web-panel-proxmox.service` on the host.

The fixed `pvesh` child commands use root's primary group for
[Proxmox IPC authentication](https://github.com/proxmox/pve-cluster/blob/master/src/pmxcfs/server.c#L106).
The exporter retains the panel group when publishing the
snapshot; the web worker receives no additional group memberships or privileges.

Existing host metrics, services, mounted storage, scheduled jobs and service
diagnostics remain available. The diagnostic allowlist includes `pveproxy`,
`pvedaemon`, `pvestatd`, `pve-cluster`, `corosync`, `pve-firewall` and
`proxmox-firewall`. The panel has no guest-operation broker; management actions
remain in the native Proxmox UI and CLI.
