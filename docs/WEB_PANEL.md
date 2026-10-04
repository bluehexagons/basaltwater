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
| Prepare system and data tasks | Agent tools | Contextual checkups, maintenance plans, log and job reviews, cleanup, and local data work |
| Administer the host | Admin controls | Approved package updates, Basaltwater refresh, service maintenance, and host power controls |
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

**Agent activity** shows the panel's running and queued prompts, enabled
schedules, drafts, automatically paused tasks, and latest finished run. It
includes the next scheduled task and active-run timing at page load. Open a
run link to view that report directly in Agents. Counts use private saved panel
state without launching diagnostics or prompts; other terminal-agent sessions
are not included. Missing task storage and scheduler errors remain explicit.

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

## Admin controls

Open **Admin controls** for host updates and power actions. Host actions require
the optional [privilege approval service](PRIVILEGE_APPROVALS.md), currently
supported on non-root agent VMs. Other panels still show the available inspection
tools and account-level T3 Code updater. The panel does not gain root privileges
or access to the approval password.

| Action | Behavior |
| --- | --- |
| Update system packages | Refresh Debian APT indexes, then upgrade installed packages; keep existing configuration files. Package hooks can restart services. No distribution upgrade, autoremove, or automatic reboot. |
| Check Basaltwater refresh | Validate `basaltw refresh --dry-run`; report whether the saved setup and source channel can be refreshed. |
| Refresh Basaltwater | Upgrade the installed managed source channel and replay the last successful local setup with `basaltw refresh`. This can reconcile configuration and restart services. |
| Check web configuration | Run `nginx -t` without reloading. |
| Reload web gateway | Run `nginx -t`, then reload Nginx only if validation passes. |
| Restart web panel | Restart the panel and prompt scheduler; active panel prompts will be interrupted. |
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

The screen shows recent requests, panel prompt activity, and the latest
maintenance job's result, timestamps, and exit code. **Dispatched** means the
maintenance service started, not that its commands finished. A successful power
job confirms scheduling only. Other agent sessions are not included in the
panel prompt count. Review running work before updates or interruptions.

Maintenance runs in a separate root system service with a six-hour limit,
so restarting the panel or approval broker does not cancel it. One maintenance
job runs at a time; jobs and uncertain dispatches are never retried automatically.
Use **Refresh status** to inspect results. The latest job result and request
history survive panel restarts. Privileged command output is not exposed; use
administrator SSH or console access for detailed diagnosis. T3 Code uses its
existing account-level updater and is shown only when installed and configured.

Refresh requires a root-owned managed source channel and a saved successful
local Debian setup. Controller-installed setup snapshots need a setup rerun
from their controller, and unmanaged or writable checkouts cannot be refreshed
through the panel. The refresh check validates local state without fetching;
it does not preview upstream code changes. No credentials are copied and no
interactive login is started by refresh. The screen explains unavailable
actions rather than granting wider privileges.

## Agent tools

Open **Agent tools** from the sidebar or dashboard to prepare guided tasks for
the existing Codex runner. Admin controls offers checkups, maintenance planning,
and disk reviews. Service diagnostics and local service cards carry the selected
service into a log review; scheduled job cards carry the job into a timer review.
These shortcuts open a setup form and never run a prompt automatically.

| Tool | Result | Suggested repeat / cap |
| --- | --- | --- |
| System checkup | Evidence-backed resource, service, update, backup, and agent readiness findings | Daily / 20 minutes |
| Plan system maintenance | Prioritized repairs with prerequisites, interruption risks, commands, and verification steps for operator review | Weekly / 30 minutes |
| Review service logs | Grouped errors, likely causes, and next checks for a fixed service and rolling time window | Daily / 15 minutes |
| Review a scheduled job | Timer and process evidence, missed runs, failures, and possible overlap | Once / 15 minutes |
| Review disk cleanup | Space consumers, ownership, retention implications, and cleanup candidates | Weekly / 15 minutes |
| Check data quality | Aggregate schema, encoding, missing-value, duplicate, and consistency findings | Weekly / 20 minutes |
| Import and normalize data | A new CSV, JSON, or JSONL dataset with count/type and round-trip validation | Once / 30 minutes |
| Export a dataset | A local directory bundle with checksums, a manifest, and offline restore instructions | Once / 30 minutes |
| Quarantine old workspace files | Reversible moves from a dedicated cache/build folder with a manifest and rollback instructions | Once / 15 minutes |

Select **Set up task**, fill in its inputs, then **Prepare prompt for review**.
The resulting Agents form shows the full prompt and normal model, effort,
runtime, permission, failure-limit, and repetition controls. Choose **Run now**,
**Create schedule**, or **Save draft** only after reviewing it. Preparation
neither saves nor queues a task. Reports and saved execution settings appear in
**Agents → Run history**. There is no separate scheduler or T3 Code dependency.

System tools inspect and recommend changes. Repairs remain operator actions
through separately approved **Admin controls** or the administrator console.
Log reviews read at most 100 recent entries at run time using the selected
service, time window, and severity; message-text searches are not copied.
Repeated reviews use a rolling window and may report the same event again.
They report inaccessible evidence rather than seeking elevated access.

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

Quarantine requires a dedicated disposable cache/build folder and a minimum
age of 1–3650 days (default 7). Its prompt limits each run to 100 eligible
ordinary files, skips tracked, open, sensitive, and uncertain files, and
requires non-overwriting moves on the same filesystem. It never permanently
deletes or automatically purges quarantine. Moving files into quarantine does
not free disk space; use the disk review to plan actual reclamation.

These task-specific limits and revalidation requirements guide the agent; the
runner enforces the selected Codex sandbox, runtime, and output limits. The
workbench is not a deterministic migration or cleanup engine. Review the
manifest and results, and retain an independent backup for valuable data.
Editing a prepared prompt changes its instructions; saved tasks retain that
exact text for later runs.

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
6. Choose once, hourly, daily, weekly, or **Custom interval**. Custom intervals
   accept 1–43,200 whole minutes (30 days): 360 means every 6 hours; 20,160 means
   every 2 weeks. The custom field applies only when Custom interval is selected.
   **Run now** queues an immediate run
   and enables repetition when selected. **Create schedule** starts after one
   interval. **Save draft** stores an inactive task, including a one-time
   prompt, without running it or enabling repetition. Drafts can be prepared
   before Codex is installed.
7. Set **Pause schedule after failures** from 1 to 10, or 0 to keep repeating.
   The default is 3. Failed or interrupted runs count toward this limit;
   cancellation preserves the count, and a completed run resets it.

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

Repository editing templates ask for a clean Git checkout, including untracked
files, and leave changes for review. Their prompts tell later runs to report a
dirty checkout and stop before editing; review and integrate the previous result
before continuing the schedule. The templates prohibit stashing, resetting,
cleaning, or committing existing work to clear the checkout.
Dependency updates follow the project's package manager, lockfiles, and upgrade
policy, with larger migrations reported for follow-up.
Host templates inspect existing evidence and recommend follow-up:
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
actions. Browser checks use isolated loopback previews and synthetic data, without
creating gateway previews or forwards. A healthy managed browser installation
does not prove that browser tools are exposed to the unattended session; missing
tools are reported as blocked coverage. Godot exports use a documented ignored
output directory and report missing presets
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
Use **Start schedule** to activate a recurring draft. Running a draft manually
does not activate its repeat schedule. **Duplicate** opens a new form with a
saved task's settings so you can adjust its directory, model, or prompt. Run
history's **Reuse run settings** uses the prompt and execution settings recorded
for that specific run, even after the task is edited or removed. Reuse defaults
to Once to avoid enabling another recurring schedule inadvertently. Neither
link saves or executes work until you submit the form.

Schedules that reach their failure limit show **Auto-paused** and the failure
count. Inspect the last run, edit the task if needed, then use **Resume** to
reset the count and schedule its next interval. A successful manual recovery
run clears the count but leaves the schedule paused until resumed. Timeouts
and CLI startup errors count as failed runs. A completed CLI process does not
prove the requested task succeeded; review its output and validation evidence.

Once tasks are saved, the screen leads with their status; expand **Create a
prompt task** to add another, or select a saved task's **Edit** link. Selecting
a template, editing, duplicating, or reusing settings puts the selected form
before the saved-task list.
Pausing stops future work and clears a queued run; cancel an active run
separately. Cancellation stops processes, but does not undo completed edits.
Run history retains the prompt and settings used, timestamps, duration, exit
code when available, and bounded output. Summary cards show the active/queued
work, repeating schedules, drafts, automatically paused tasks, recent outcomes,
and recorded wall time for retained finished runs. These are retained-history
totals, not lifetime usage or spending estimates. Active runs show elapsed time
and remaining runtime at page load. Use **Refresh status** to update results
and timing without automatic reloads that would discard a prompt draft.

The scheduler belongs to the panel service, so it runs without T3 Code while
the panel is running. Tasks persist across panel and host restarts. After
downtime, each overdue schedule runs at most once, then advances to its next
interval. Deadlines crossed while a task is running are skipped, preserving its
cadence without immediately starting another run of that task. An explicit
Run now request remains available. Changing the elapsed interval resets the next
deadline to a full interval from saving; editing other settings keeps the existing
deadline. Paused tasks and drafts remain inactive after edits. These are elapsed
intervals rather than calendar times. Runs interrupted by a restart are labelled
for review. The runner executes serially and does not create isolated Git
worktrees: choose a dedicated checkout when work could overlap another agent session.

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

Runs receive their saved execution settings in the prompt instructions and are
told to report blocked checks instead of bypassing sandbox permissions, starting
login, or waiting for an operator. Package and test tools may need a documented
per-command cache path inside the workspace or permitted temporary directory;
temporary writes do not grant access to caches elsewhere in the account's home.
Prompts must keep waits within the runtime cap and avoid detaching work into
another service. Cancellation does not undo changes or withdraw privileged
broker requests; external integrations can own work outside the local process
group. The [managed agent skills](AGENT_SKILLS.md) include unattended task guidance.

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
