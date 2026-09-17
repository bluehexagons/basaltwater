# Web panel reference

This page records the API, collection limits, and security boundaries for the
[minimal web panel](WEB_PANEL.md). Use the main guide for installation and
day-to-day operation.

## Notification ingest

The receiver is opt-in, HTTPS-only, and available at
`/api/v1/notifications`. It accepts `POST` requests authenticated by a generated
bearer token stored at `/etc/infra-tools/web-panel/notification-ingest.token`.
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
reloads managed audit rules even if their file has not changed.

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
and readiness checks. There is no general package update button; use setup with
`--refresh-packages` for deliberate reconciliation.

## Access controls and scope

The panel uses Basic Auth, request throttling, an
`infra-tools-web-panel` fail2ban jail, a Unix-socket-only application listener,
CSRF tokens for state-changing forms, no-store responses, and separate service
group access for the socket, bearer token, audit snapshot, and notification
history. It supports server and workstation profiles, but not `server_proxmox`.
It normally runs as the setup user. A root-managed setup instead uses the
locked `infra-web-panel` service account.

The panel renders saved configured access and discovers live `infra-web`
forwards and static sites. HomeBox status is a loopback readiness probe only;
it does not prove DNS, TLS, browser reachability, or inventory administration.
