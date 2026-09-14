# Notifications

infra-tools can send setup, maintenance, security, storage, and CI/CD events to
webhooks or local mail. Targets and the delivery level are saved per managed
system and reused by scheduled jobs.

## Quick start

Configure one or more repeatable targets:

```bash
infra-tools setup server_lite fileserver admin \
  --notify webhook https://hooks.example.net/infra \
  --notify mailbox ops@example.com \
  --notification-level normal
```

| Target | Value | Delivery |
| --- | --- | --- |
| `webhook` | `http://` or `https://` URL | Schema-version-2 JSON `POST` |
| `mailbox` | Email address | Target machine's `mail` command and local mail transport |

Use `infra-tools info HOST` to check the saved target count and delivery level.
Use `infra-tools cmd HOST` to inspect the reconstructed setup command.

## Send notifications to an infra-tools web panel

The web panel can receive and display notifications from other managed
machines. The receiver is disabled by default and requires HTTPS.
Install the panel first if it is not already present; see
[Install the panel](WEB_PANEL.md#install-the-panel).

### 1. Enable the receiver

On the controller, patch the machine that hosts the panel:

```bash
infra-tools patch panel.example agent \
  --web-panel \
  --ssl \
  --web-panel-notification-ingest
```

The endpoint uses a fixed path on the same origin as the panel:

```text
https://panel.example/api/v1/notifications
```

Setup creates a bearer token on the panel host. Read it there with `sudo`:

```bash
sudo cat /etc/infra-tools/web-panel/notification-ingest.token
```

### 2. Configure each sender

Append the token as the webhook URL fragment:

```bash
infra-tools patch sender.example agent \
  --notify webhook \
  'https://panel.example/api/v1/notifications#TOKEN_FROM_PANEL_HOST'
```

infra-tools removes the fragment from the URL and sends it in the
`Authorization: Bearer ...` header. The token does not enter the HTTP request
path or Nginx access log.

### 3. Verify delivery

Open the panel and check **Notifications** after the sender's setup finishes.
The setup result uses the newly saved target. Repeated delivery attempts with
the same event ID appear only once.

For enablement, disablement, rotation, retention, and API limits, see
[Web panel notification ingest](WEB_PANEL.md#notification-ingest-api).

> Treat the full fragment-bearing URL as a credential. Do not paste it into
> tickets, logs, or shared terminal output. It remains in the sender's saved
> setup state because scheduled jobs need it. Scheduled jobs also receive a
> root-owned `/etc/infra-tools/notifications.json` subset containing only the
> notification targets and level, so they do not need access to the full
> root-only setup state.

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
infra-tools patch fileserver admin --notification-level warning

# Include successful maintenance runs
infra-tools patch buildbox agent --notification-level verbose

# Temporarily stop outbound delivery without deleting targets
infra-tools patch labbox agent --notification-level off
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
| SSH failures or account lockout | Failures are grouped by source, user, and method; lockouts are warnings | Check the source and affected account |
| fail2ban ban | A source address was banned by a named jail | Investigate repeated or unexpected sources |
| Monitoring source unavailable | auditd, fail2ban, or the SSH journal could not be read | Restore the named source; recovery is sent once |

Routine sudo audit hits, fail2ban ban expirations, and missing optional security
components do not notify by themselves. Audit events recorded during an
infra-tools setup window are treated as expected maintenance; the setup result
records that work. The monitor holds its cursor while a required source is
unavailable so events are not silently skipped.

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
| `event.id` | Idempotency; retries keep the same value and `X-Infra-Tools-Event-ID` header |
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

Common checks:

- Install and configure a local MTA before relying on mailbox alerts.
- Confirm the effective level with `infra-tools info HOST`.
- Check local job logs when the level suppresses routine success events.
- Re-run setup or patch after correcting an invalid target; malformed schemes,
  addresses, empty values, and unknown target types fail validation early.

Setup summaries redact webhook paths, queries, fragments, and mailbox local
parts. Identical repeated targets are normalized and notified only once.
