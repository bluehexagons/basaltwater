# Notifications

Basaltwater can send setup, maintenance, security, storage, and CI/CD events to
webhooks or local mail. Targets and the delivery level are saved per managed
system and reused by scheduled jobs.

## Quick start

Configure one or more repeatable targets:

```bash
basaltw setup server_lite fileserver admin \
  --notify webhook https://hooks.example.net/infra \
  --notify mailbox ops@example.com \
  --notification-level normal
```

| Target | Value | Delivery |
| --- | --- | --- |
| `webhook` | `http://` or `https://` URL | Schema-version-2 JSON `POST` |
| `mailbox` | Email address | Target machine's `mail` command and local mail transport |

Use `basaltw info HOST` to check the saved target count and delivery level.
Use `basaltw cmd HOST` to inspect the reconstructed setup command. Displayed
commands replace webhook URLs with `https://REPLACE_WITH_WEBHOOK_URL`; insert
the real target before replaying a command. The saved target remains available
to scheduled notification jobs.

HTTPS webhook delivery verifies the receiver's certificate and hostname by
default, including for saved configurations from earlier versions. For a private
CA, install its verified public certificate in the sender's normal trust store,
or pass `--notification-ca-file /etc/basaltwater/receiver-ca.pem` to setup or
patch. The absolute path points to a readable PEM bundle on the sending machine.
Keep the file and its directory under the control of the account that manages
sender trust. The bundle adds trust for notification webhooks alongside the
system CAs. Use `--notification-ca-file ''` to clear the saved bundle path.

For receivers that still require compatibility, explicitly pass
`--notification-allow-unverified-https` (also accepted as
`--no-notification-strict-https`). This disables certificate and hostname checks
for all HTTPS notification targets on that sender, including bearer-token
targets. Use `--notification-strict-https` to restore verification. The
compatibility option and a private CA bundle cannot be combined. The policy
applies to setup and scheduled notifications. Tokenless HTTP targets remain
available for receivers on trusted networks.

## CachyOS workstation webhooks

The local `agent_cachyos` profile supports webhook notifications for setup
completion and failure, including setup repeated by `refresh`. It rejects
`mailbox` and does not install scheduled maintenance/security notification jobs.
Include targets in your initial selection:

```fish
basaltw setup agent_cachyos localhost --node --python --git-lfs --codex \
  --notify webhook 'https://hooks.example.net/infra' \
  --notification-level normal --notification-strict-https
```

The JSON payload, bearer-token URL fragment, retries, and delivery levels below
apply to CachyOS webhooks too. Dry runs send nothing. Setup failures report the
failing phase and exception type, retaining detailed output locally.
Successful setup stores webhook URLs and policy in the private user-owned
local selection and receipt; displayed refresh commands redact URLs. Use
`refresh`, rather than generic remote-host `patch`, `info`, or `cmd`, to retain
or change that selection. See [CachyOS notification maintenance](../workstations/cachyos-maintenance.md#webhook-notifications)
for adding targets to an existing setup, disabling delivery, and replacing targets.

## Send notifications to a Basaltwater web panel

The web panel can receive and display notifications from other managed
machines. The receiver is disabled by default and requires HTTPS.
Install the panel first if it is not already present; see
[Install and sign in](web-panel.md#install-and-sign-in).

### 1. Enable the receiver

On the controller, patch the machine that hosts the panel:

```bash
basaltw patch panel.example agent \
  --web-panel \
  --ssl \
  --web-panel-notification-ingest
```

The endpoint uses a fixed path on the same origin as the panel:

```text
https://panel.example/api/v1/notifications
```

Setup creates a bearer token on the panel host. On the administrator panel,
open **Notifications** and expand **Reveal full sender link** to copy the
complete target. The link includes the token and is ready to use with a sender
setup command. For scripted workflows, read the token on the panel host with
`sudo`:

```bash
sudo cat /etc/basaltwater/web-panel/notification-ingest.token
```

### 2. Configure each sender

Append the token as the webhook URL fragment (or paste the complete link shown
by the panel):

```bash
basaltw patch sender.example agent \
  --notify webhook \
  'https://panel.example/api/v1/notifications#TOKEN_FROM_PANEL_HOST'
```

Basaltwater removes the fragment from the URL and sends it in the
`Authorization: Bearer ...` header. The token does not enter the HTTP request
path or Nginx access log.

For a panel using a VM-local CA, install that CA on the sender or pass its
absolute PEM path using `--notification-ca-file`. Use the hostname or IP
covered by the panel certificate in the webhook URL. [VM CA trust](client-ca-trust.md)

### 3. Verify delivery

Open the panel and check **Notifications** after the sender's setup finishes.
The setup result uses the newly saved target. Repeated delivery attempts with
the same event ID appear only once. Active alerts and history show suggested
actions alongside the report. Expand **Security findings** for the scan window
and structured evidence. The panel displays timestamps with timezone offsets
in your browser's local time. Hover over a time to see the sender's original
value. Older evidence without an offset remains in the sender's local time.

For enablement, disablement, rotation, retention, and API limits, see
[Web panel notification ingest](web-panel-reference.md#notification-ingest).

> Treat the full fragment-bearing URL as a credential. Do not paste it into
> tickets, logs, or shared terminal output. It remains in the sender's saved
> setup state because scheduled jobs need it. Scheduled jobs also receive a
> root-owned `/etc/basaltwater/notifications.json` subset on Debian containing the
> notification targets, level, and HTTPS policy, so they do not need access to
> the full root-only setup state. Setup reconciles the shared `/etc/basaltwater`
> directory to `root:root` mode `0755` for service traversal. The subset itself
> remains `root:<configured user's primary group>` mode `0640`, or root-only
> mode `0600` until that account exists. Re-running setup repairs a directory
> previously created with mode `0700`; symlinked, non-root-owned, or
> group/world-writable configuration directories are rejected.

## Choose a delivery level

`--notification-level` controls outbound webhook and mailbox volume. It does
not remove local service logs, setup history, audit records, or panel data.

| Level | Outbound events |
| --- | --- |
| `verbose` | Every event a job produces, including routine successes |
| `normal` | Setup completion plus actionable warnings, failures, repairs, and recoveries (default) |
| `warning` | Warnings, errors, firing incidents, and their recoveries |
| `error` | Errors and the recoveries required to close firing incidents |
| `off` | None; targets remain saved and local records continue |

Examples:

```bash
# High-signal production alerts
basaltw patch fileserver admin --notification-level warning

# Include successful maintenance runs
basaltw patch buildbox agent --notification-level verbose

# Temporarily stop outbound delivery without deleting targets
basaltw patch labbox agent --notification-level off
```

A later patch that omits the flag preserves the saved level.

## Events and expected volume

Configured targets are used by:

- APT, Node.js, uv, and Gogs updates;
- restart checks, cleanup, and security monitoring;
- sync, parity, and storage operations; and
- CI/CD executors.

At the default `normal` level, routine starts and successful scheduled sync,
scrub, Node update, and CI/CD runs stay local. Failures and recoveries are sent.
Initial sync or parity performed by setup is represented by the setup result,
not a second notification based on pre-setup state.

### Interpret security events

| Event | Meaning | Suggested response |
| --- | --- | --- |
| Protected-file audit finding | Evidence names the affected control, path, actor, operation, and executable when available; it is not proof of compromise | Compare the evidence with approved work |
| Kernel module activity | Auditd recorded module operations, including failed attempts. The report includes names, results, executables, and audit login identities when recorded | Compare the timing with boot, service startup, and maintenance logs. An unset login identity can occur during automatic loading |
| SSH failures or account lockout | Failures are grouped by source, user, and method; lockouts are warnings | Check the source and affected account |
| fail2ban ban | A source address was banned by a named jail | Investigate repeated or unexpected sources |
| Monitoring source unavailable | auditd, fail2ban, or the SSH journal could not be read | Restore the named source; recovery is sent once |
| Source retention gap | The cursor predates the oldest retained entry in the named log. The report shows the affected interval and the oldest entry observed | Check archived logs for the uncovered interval and review retention |

Routine sudo audit hits, fail2ban ban expirations, and missing optional security
components do not notify by themselves. Audit events recorded during a
Basaltwater setup window are treated as expected maintenance; the setup result
records that work. The monitor holds its cursor while a required source is
unavailable so events are not silently skipped.

During catch-up, the monitor reports each newly uncovered source and includes
the interval through its observed retention boundary. It logs every affected
scan window locally. Extensions of the same continuous gap are combined into
at most one update every 24 hours per source. Failed attempts follow the same
interval because a report can reach some targets before another target fails.
Separate gaps and findings from retained logs still notify promptly.

## Webhook API

Webhook payloads use schema version 2:

```json
{
  "schema_version": 2,
  "event": {
    "id": "629ae4a98e8b4e55aec19fd969666a0e",
    "occurred_at": "2026-09-03T10:00:00+00:00",
    "type": "security.source_health",
    "state": "resolved",
    "status": "info",
    "deduplication_key": "security_monitor:source-health"
  },
  "operator": {
    "subject": "Security source recovered",
    "job": "security_monitor",
    "system": "fileserver",
    "what_happened": "Auditd is readable again",
    "suggested_actions": ["No action is required"],
    "details": ""
  },
  "data": {}
}
```

| Field | Receiver use |
| --- | --- |
| `event.id` | Idempotency; retries keep the same value and `X-Basaltwater-Event-ID` header |
| `event.type`, `state`, `status` | Routing, filtering, and incident state |
| `event.deduplication_key` | Group related firing and recovery events |
| `operator` | Stable, directly renderable operator context |
| `data` | Producer-specific structured facts |

Security-monitor `data` can include the collection window, counts, source
health, SSH summaries, and audit evidence. Receivers should use typed fields
instead of parsing prose.

## Delivery and troubleshooting

| Condition | Behavior |
| --- | --- |
| HTTP 2xx | Accepted |
| Connection failure or HTTP 408, 425, 429, 500, 502, 503, 504 | Two bounded retries; numeric `Retry-After` honored up to 30 seconds |
| Redirect | Rejected to avoid forwarding credentials |
| Other HTTP, TLS, or mailbox failure | Not retried |
| Attempt duration | 30-second timeout per webhook attempt |

Delivery is best effort and does not change the underlying job result.
Incomplete delivery is logged. Invalid saved targets are skipped without
disabling other targets.

Setup follows the same best-effort rule. If its completion or failure
notification cannot be delivered, setup prints a warning with the checks to
make; inspect the sender output and service logs before treating the receiver
history as complete.

Common checks:

- Install and configure a local MTA before relying on mailbox alerts.
- Confirm the effective level with `basaltw info HOST`.
- Check local job logs when the level suppresses routine success events.
- Re-run setup or patch after correcting an invalid target; malformed schemes,
  addresses, empty values, and unknown target types fail validation early.

Setup summaries redact webhook paths, queries, fragments, and mailbox local
parts. Identical repeated targets are normalized and notified only once.
