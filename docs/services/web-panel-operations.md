# Publishing, notifications, and removal

[Documentation index](web-panel.md)

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
basaltw setup agent_vm sender.example agent --codex \
  --notify webhook 'https://PANEL_HOST/api/v1/notifications#TOKEN'
```

To disable receiving, patch with
`--web-panel --ssl --no-web-panel-notification-ingest`. For token rotation and
the API contract, see the [web panel reference](web-panel-events-reference.md#notification-ingest).
See [Notifications](notifications.md) for event meaning, delivery levels, and
retries.

## Remove the panel

```bash
basaltw patch 192.168.1.50 agent --no-web-panel
```

Removal first acquires the shared systemd unit lock and refuses any unfinished,
corrupt or unreadable recovery marker before changing services, routes or data.
Follow [transaction recovery](../operations/transaction-recovery.md) to reconcile
that operation; removal cannot bypass it. The lock remains held through optional
Proxmox, Guacamole and runner registration removal and final cleanup.

Removal validates the managed configuration, then stops the installed
audit timer, audit exporter and panel service before deleting units, credentials,
events or the Nginx route. A failed stop aborts removal and preserves those
files. Units already stopped or disabled remain in that state; inspect the
reported systemd failure, repair it and retry removal, or rerun the enabled
panel setup to restore service. For a partial installation, setup inspects
systemd before skipping a missing unit file: the unit must be explicitly absent,
inactive and not enabled. Inspection errors abort removal.
This exclusion does not make all teardown one rollback transaction: a later
optional component failure can leave earlier services stopped or a route removed.
Inspect the failure and any retained component recovery marker before retrying.

Successful removal stops the approval engine unless a standalone approval panel
is selected. Policy and audit history are retained. Shared packages and independent agent tasks remain. Pause
those tasks separately if they should stop. Managed audit and storage snapshots
are removed; unrecognized files and their containing directories are preserved.

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

An initial browser request without credentials receives the normal HTTP 401
Basic Auth challenge and does not count as a failed login. A 401 after the
browser submits Authorization is recorded in the privacy-preserving Nginx log.
Passwords and Authorization headers are not logged.
