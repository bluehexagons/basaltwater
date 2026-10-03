# Minimal web panel

The optional web panel is a browser dashboard for one managed machine. It
shows current host state, configured services, audit activity, maintenance,
notifications, and agent prompt tasks. Agent tasks use the installed Codex CLI
under the panel account; no T3 Code installation is required.

| Need | Open | Result |
| --- | --- | --- |
| Check host health | Overview | Uptime, load average, memory, swap, root-disk and inode use, kernel, reboot state, and update timers |
| Find a managed endpoint | Services | Configured and discovered web, SSH, RDP, Samba, Gogs, HomeBox, and Antistatic access |
| Inspect a service | Local service status or Service diagnostics | On-demand state, fixed runtime details, and filtered logs |
| Check maintenance | Scheduled jobs | Timer state, last result, and selected job logs |
| Run agent work | Agents | Codex prompts, recurring schedules, run history, and optional T3 Code diagnostics |
| Review audit activity | Audit activity | Sanitized recent events and collection health |
| Receive remote notifications | Notifications | Recent accepted events and an optional sender endpoint |

Navigation and read-only views work without JavaScript. The dashboard caches
host and service snapshots for up to 30 seconds. See the [web panel
reference](WEB_PANEL_REFERENCE.md) for data limits, diagnostic behavior, and
the notification API contract.

Every page uses the same grouped sidebar: workspace activity, administration,
and on-demand inspection. Dashboard links open the corresponding section;
optional areas explain when they are not configured. On smaller screens the
navigation becomes a horizontal scrollable row. Light and dark themes follow
the device preference. Memory and disk meters retain numeric values, and
service states have text labels as well as color.

The compact dashboard header shows the host, setup profile, and account.
Service totals distinguish responding endpoints, endpoints needing attention,
and endpoints without a readiness check. Audit totals include warning/error
events while retaining collection warnings. These summaries reuse the displayed
records; they do not start extra service or log queries.

## Install and sign in

HTTPS is recommended:

```bash
basaltw setup agent_vm 192.168.1.50 agent \
  --web-panel \
  --web-panel-password 'replace-this-value' \
  --ssl
```

The default port is 80 for HTTP or 443 with `--ssl`. Use another port when
needed:

```bash
basaltw patch 192.168.1.50 agent \
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

## Agent prompt tasks

Open **Agents** to run Codex prompts against the host or a repository. Install
and authenticate Codex as the non-root account running the panel. Other
terminal agents are inventoried by diagnostics but do not execute prompt tasks
in this initial version. Root-managed panels use a locked service account
without a home and show diagnostics with prompt execution unavailable.

1. Choose a starting template under **Repository work** or **Host checks**,
   or write a custom prompt. Each template shows its execution mode, suggested
   repeat interval, runtime cap, and additional permissions before selection.
2. Enter the working directory. Repository templates require you to choose
   the specific checkout; host inspection defaults to the account's home.
3. Choose **Inspect only** for Codex's read-only sandbox, or **Workspace
   changes** for a directory inside the account's home. Enable command network
   access when changes require package downloads, and temporary writes when
   tests or package tools need `/tmp` or `TMPDIR`. Provider requests still use
   the network in either mode. Configured hooks and MCP integrations retain
   their own permissions.
4. Select a model and reasoning effort, or keep the configured defaults. The
   model dropdown reads Codex's local cache without contacting the provider;
   **Additional options** accepts a custom model ID when needed. Model access
   and supported effort levels depend on the installed CLI and account.
5. Set **Maximum runtime** in whole minutes, from 1 minute to 7 days (default
   30 minutes). Waiting and sleeping count toward this wall-clock limit. This
   is a duration cap, not an exact cost or token budget.
6. Choose once, hourly, daily, or weekly. **Run now** queues an immediate run
   and enables repetition when selected. **Create schedule** starts after one
   interval.

The starting templates cover these tasks. Their prompts and settings remain
editable before submission; selecting a template does not run or schedule it.

| Template | Default repeat | Runtime cap | Scope |
| --- | --- | --- | --- |
| Update repository dependencies | Weekly | 30 minutes | Workspace changes; command network and temporary writes |
| Repository health check | Weekly | 30 minutes | Inspect only |
| Repair failing checks | Once | 60 minutes | Workspace changes; temporary writes |
| Add regression tests | Once | 60 minutes | Workspace changes; temporary writes |
| Update repository documentation | Weekly | 30 minutes | Workspace changes |
| Review repository security | Weekly | 30 minutes | Inspect only; live web search for advisories |
| Review release readiness | Once | 20 minutes | Inspect only |
| Host maintenance review | Daily | 30 minutes | Inspect only |
| Check backup health | Daily | 15 minutes | Inspect only |
| Review storage cleanup | Weekly | 15 minutes | Inspect only |
| Triage a service incident | Once | 20 minutes | Inspect only |
| Check certificate expiry | Daily | 10 minutes | Inspect only |

Repair and test-writing templates preserve unrelated work and leave changes
for review. Host templates inspect existing evidence and recommend follow-up:
backup checks do not perform restores, storage reviews do not delete files,
and certificate checks do not renew certificates or change trust. Missing
configuration or unavailable evidence is reported explicitly. Security review
uses web search for current advisories and distinguishes confirmed version
matches from uncertain findings.

Additional templates appear when the panel's saved setup configuration or
commands installed for its account indicate a relevant integration. Each shows
the detection reason. These checks do not run commands or prove service health,
browser readiness, repository compatibility, or authentication. Repository
templates still require you to select a suitable checkout.

| Conditional template | Appears when | Default repeat / cap | Scope |
| --- | --- | --- | --- |
| Check T3 Code readiness | T3 Code is configured or `~/.t3/runtime` exists | Daily / 15 minutes | Inspect only |
| Review container health | `docker` or `podman` is installed | Daily / 15 minutes | Inspect only |
| Review web hosting | Web server is configured, or `basaltwater-web` or `nginx` is installed | Daily / 15 minutes | Inspect only |
| Check shared storage | Samba is configured, or `smbd`, `mount.cifs`, or `sshfs` is installed | Daily / 15 minutes | Inspect only |
| Check remote desktop readiness | Remote desktop is configured or `xrdp` is installed | Daily / 10 minutes | Inspect only |
| Review GitHub Actions failures | `gh` is installed | Daily / 20 minutes | Workspace permissions for command network; prompt requests inspection only |
| Run browser smoke checks | `basaltwater-playwright-mcp` is installed | Once / 30 minutes | Workspace changes; command network and temporary writes |
| Validate Godot web export | `godot` or `godot4` is installed | Once / 60 minutes | Workspace changes; temporary writes |
| Check HomeBox data protection | HomeBox is configured | Daily / 15 minutes | Inspect only |
| Check Gogs repository hosting | Gogs is configured or `gogs` is installed | Daily / 15 minutes | Inspect only |
| Check Antistatic services | Antistatic lobby or DB is configured | Daily / 15 minutes | Inspect only |
| Check notification delivery | Panel notification ingest is enabled | Daily / 10 minutes | Inspect only |
| Check privilege approval readiness | Privilege approvals are configured | Weekly / 15 minutes | Inspect only |

GitHub CI inspection needs command network access; the runner offers that in
workspace mode, so its prompt explicitly requests no file changes or remote
actions. Browser checks use isolated local previews and synthetic data. Godot
exports use a documented ignored output directory and report missing presets
or export templates instead of downloading them. Other integration checks
inspect existing state and recommend follow-up without repairs or privileged
actions. Availability filters only the starting catalog: saved tasks retain
their prompts and settings if an integration is later removed. Opening a stale
template link explains why it is unavailable and leaves the custom form usable.

Additional options control Codex's web search independently of command network
access: disabled, cached results, or live search. Runs use ephemeral Codex
sessions by default; enable **Keep Codex session history** to retain them in
Codex too. Panel run history is retained with either choice.

Saved tasks show their next run, last outcome, and working directory. Use
**Pause**, **Resume**, **Edit**, **Run now**, or **Remove** to manage them.
Once tasks are saved, the screen leads with their status; expand **Create a
prompt task** to add another, or select a saved task's **Edit** link.
Pausing stops future work and clears a queued run; cancel an active run
separately. Cancellation stops processes, but does not undo completed edits.
Run history retains the prompt and settings used, timestamps, and bounded
output. Use **Refresh status** to see results without automatic reloads that
would discard a prompt draft.

The scheduler belongs to the panel service, so it runs without T3 Code while
the panel is running. Tasks persist across panel and host restarts. After
downtime, each overdue schedule runs at most once, then advances to its next
interval. Runs interrupted by a restart are labelled for review. The initial
runner executes serially and does not create isolated Git worktrees: choose
a dedicated checkout when work could overlap another agent session.

**Check agent readiness** collects versions and local credential metadata for
Codex, Claude Code, OpenCode, and GitHub CLI, plus maintenance-hold state and
the timestamp of any saved readiness record. T3 Code is checked when configured
or installed; its update action appears for a configured installation.
Credential file presence is not proof of provider authentication. Checks run
in the background and do not launch prompts or automatically repair the host.

Prompt execution uses the account's existing privileges. Host repairs that
need elevation use the configured [privilege approval workflow](PRIVILEGE_APPROVALS.md);
the panel does not grant root access. The templates prepare changes for review
and do not ask the agent to commit, push, or deploy.

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
the API contract, see the [web panel reference](WEB_PANEL_REFERENCE.md#notification-ingest).
See [Notifications](NOTIFICATIONS.md) for event meaning, delivery levels, and
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
| Browser warns about the certificate | Follow [Client CA trust](CLIENT_CA_TRUST.md) |

Failed browser logins are written to a privacy-preserving Nginx log. Passwords
and Authorization headers are not logged.
