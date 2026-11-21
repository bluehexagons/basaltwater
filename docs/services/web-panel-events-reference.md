# Notifications, audit, and on-demand views

[Documentation index](web-panel-reference.md)

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

Use `--notification-strict-https` on webhook senders to verify the certificate
chain and hostname. For a panel using a managed VM CA, add the CA to the
sender's trust store or enable verification with a sender-local PEM bundle
using `--notification-ca-file`. [Notification delivery](notifications.md)

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

T3 updates use its native environment settings. The retired panel
`POST /actions/t3-update` endpoint returns 404. **Admin controls** provides separately approved Debian
package upgrades and a full Basaltwater refresh.
