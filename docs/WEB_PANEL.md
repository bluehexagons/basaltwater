# Minimal web panel

The optional web panel is a browser dashboard for one managed machine. It
shows current host state, configured services, audit activity, maintenance, and
notifications. It has no terminal, arbitrary command runner, package form, or
general service control.

| Need | Open | Result |
| --- | --- | --- |
| Check host health | Overview | Uptime, memory, root-disk use, reboot state, and update timers |
| Find a managed endpoint | Services | Configured and discovered web, SSH, RDP, Samba, Gogs, HomeBox, and Antistatic access |
| Inspect a service | Local service status or Service diagnostics | On-demand state, fixed runtime details, and filtered logs |
| Check maintenance | Scheduled jobs | Timer state, last result, and selected job logs |
| Review audit activity | Audit activity | Sanitized recent events and collection health |
| Receive remote notifications | Notifications | Recent accepted events and an optional sender endpoint |

Navigation and read-only views work without JavaScript. The dashboard caches
host and service snapshots for up to 30 seconds. See the [web panel
reference](WEB_PANEL_REFERENCE.md) for data limits, diagnostic behavior, and
the notification API contract.

## Install and sign in

HTTPS is recommended:

```bash
infra-tools setup agent_vm 192.168.1.50 agent \
  --web-panel \
  --web-panel-password 'replace-this-value' \
  --ssl
```

The default port is 80 for HTTP or 443 with `--ssl`. Use another port when
needed:

```bash
infra-tools patch 192.168.1.50 agent \
  --web-panel 9443 \
  --web-panel-password 'replace-this-value' \
  --ssl
```

Setup prints the panel URL. The Basic Auth username is the setup username. The
password is hashed before upload and is not saved or reconstructed. Repeat
`--web-panel-password` to rotate it; omit the flag on a later patch to retain
it. If the setup username changes, supply a new password. Use a separate
password for [privilege approvals](PRIVILEGE_APPROVALS.md).

With `--ssl`, the panel uses a suitable existing certificate or the managed VM
CA. Enroll that CA on your client when required; see [Client CA trust](CLIENT_CA_TRUST.md).
Without TLS, Basic Auth crosses the network as plaintext.

## Agent VM approvals

An agent VM configured with `--privilege-broker [PORT]` receives a
**Privilege approvals** service link. It opens a separate HTTPS page with its
own password and service identity. The panel cannot approve actions or read the
approval credential. Use [Privilege approvals](PRIVILEGE_APPROVALS.md) for the
agent and user workflow.

## Receive notifications

Enable the HTTPS receiver on an existing panel:

```bash
infra-tools patch 192.168.1.50 agent \
  --web-panel --ssl --web-panel-notification-ingest
```

Open **Notifications**, expand **Reveal full sender link**, and add that URL to
a sender. It contains a bearer token, so treat it as a credential. The sender
uses the normal webhook setup:

```bash
infra-tools setup agent_vm sender.example agent \
  --notify webhook 'https://PANEL_HOST/api/v1/notifications#TOKEN'
```

To disable receiving, patch with
`--web-panel --ssl --no-web-panel-notification-ingest`. For token rotation and
the API contract, see the [web panel reference](WEB_PANEL_REFERENCE.md#notification-ingest).
See [Notifications](NOTIFICATIONS.md) for event meaning, delivery levels, and
retries.

## Troubleshooting

Run these checks on the panel host:

```bash
sudo systemctl status infra-tools-web-panel.service
sudo journalctl -u infra-tools-web-panel.service -n 100 --no-pager
sudo systemctl status infra-tools-web-panel-audit.timer
sudo journalctl -u infra-tools-web-panel-audit.service -n 100 --no-pager
sudo nginx -t
```

| Symptom | Next check |
| --- | --- |
| Setup rejects notification ingest | Include both `--web-panel` and `--ssl` |
| No sender events | Confirm sender URL, token, and HTTPS reachability |
| Audit activity is stale | Check the audit timer and service journal |
| Login is repeatedly rejected | Check Nginx and the `infra-tools-web-panel` fail2ban jail |
| Browser warns about the certificate | Follow [Client CA trust](CLIENT_CA_TRUST.md) |

Failed browser logins are written to a privacy-preserving Nginx log. Passwords
and Authorization headers are not logged.
