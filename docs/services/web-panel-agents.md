# Agent tools, maintenance, and T3

[Documentation index](web-panel.md)

## Agent tools

Open **Agents → Agent tools** to prepare guided tasks for the existing Codex
runner, including checkups, maintenance planning and disk reviews.
Service diagnostics and local service cards carry the selected
service into a log review; scheduled job cards carry the job into a timer review.
These shortcuts open a setup form and never run a prompt automatically.

| Tool | Result | Suggested repeat / cap |
| --- | --- | --- |
| System checkup | Evidence-backed resource, service, update, backup, and agent readiness findings | Once / 20 minutes |
| Review service logs | Grouped errors, likely causes, and next checks for a fixed service and rolling time window | Once / 15 minutes |
| Review a scheduled job | Timer and process evidence, missed runs, failures, and possible overlap | Once / 15 minutes |
| Check Debian update health | Package consistency, held packages, existing index age, update results, and pending reboot evidence | Once / 15 minutes |
| Review disk cleanup | Space consumers, ownership, retention implications, and cleanup candidates | Once / 15 minutes |
| Check network and clock readiness | Local routes, resolver and clock state, plus bounded probes to explicitly configured endpoints | Once / 15 minutes |
| Plan system maintenance | Prioritized repairs with prerequisites, interruption risks, commands, and verification steps for operator review | Once / 30 minutes |
| Check data quality | Aggregate schema, encoding, missing-value, duplicate, and consistency findings | Once / 20 minutes |
| Import and normalize data | A new CSV, JSON, or JSONL dataset with count/type and round-trip validation | Once / 30 minutes |
| Export a dataset | A local directory bundle with checksums, a manifest, and offline restore instructions | Once / 30 minutes |

Select **Set up task**, fill in its inputs, then **Prepare prompt for review**.
The resulting Agents form shows the full prompt and normal model, effort,
runtime, permission, failure-limit, and repetition controls. Choose **Start task**,
**Create schedule**, or **Save draft** only after reviewing it. Preparation
neither saves nor queues a task. Reports and saved execution settings appear in
**Agents → Run history**. There is no separate scheduler or T3 Code dependency.

System tools inspect and recommend changes. Repairs remain operator actions
through separately approved **Admin controls** or the administrator console.
Log reviews use `basaltw agent diagnostics` to read allowlisted runtime properties
and at most 100 redacted recent entries at run time using the selected service,
time window, and severity; message-text searches are not copied. Each underlying
query has a five-second timeout and a 64 KiB output limit. Job reviews default
to seven days and all priorities so successful weekly runs remain visible.
The service selector prefers installed units and retains explicit historical links.
Repeated reviews use a rolling window and may report the same event again.
They report inaccessible evidence rather than seeking elevated access.

System checkups, maintenance planning, update/network checks, and service/log/job reviews use workspace
permissions with command network access and an empty private directory for each
run. This permits local service sockets and readiness probes while keeping the
source home outside writable roots. Temporary writes remain disabled, and the
prompts prohibit host changes. Disk-only reviews retain inspection mode.
Update checks use existing APT indexes and never refresh or repair packages.
Network checks use explicitly configured endpoints without scanning or inventing
probe targets; missing targets and inaccessible evidence remain coverage limits.

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

The former quarantine starter was removed: moving files on the same filesystem
does not reclaim space. Use the disk review to prepare cleanup recommendations.

These task-specific limits and revalidation requirements guide the agent; the
runner enforces the selected Codex sandbox, runtime, and output limits. The
workbench is not a deterministic migration or cleanup engine. Review the
manifest and results, and retain an independent backup for valuable data.
Editing a prepared prompt changes its instructions; saved tasks retain that
exact text for later runs.

## Maintenance agents and inbox

Open **Agents → Maintenance** to enable the installed supervisor, set its check
interval, model choices, reasoning effort and daily attempt limits, or request
a check. Panel setup prepares disabled supervision; `--maintenance-agent`
enables it during setup. Checks continue independently of the panel and T3 Code.
Healthy checks make no AI calls. Opt-in housekeeping requests fixed existing
cleanup jobs; AI reviews provide recommendations.

The tab leads with the saved enabled state, check interval, and **Check once now**.
This manual request works with automatic maintenance disabled; it does not
enable schedules or request housekeeping. Host checks, security audits, and
housekeeping have separate controls. The settings disclosure is closed by
default and its summary shows the selected model and daily AI attempt limit;
open it to edit automatic schedules, housekeeping, model choices, and review
limits. The maintenance records section follows the settings. Incomplete checks
retain a warning even when they recorded no findings. Failed submissions
preserve entered settings for correction while the status summary continues to
show the saved policy.

Model dropdown choices come from the panel account's local Codex model cache.
Saved model IDs remain selectable even when absent from that cache, and an
optional custom model ID can override the selected routine or escalation model.
Model availability still depends on the account and Codex installation.

**Automatic restart hold** exposes the same account-local hold as
`basaltw agent maintenance hold`. Expand **Manage restart hold** to choose
1–72 whole hours (default 8), extend
the hold, inspect its UTC expiry, or release it early. Holds defer managed
automatic restarts; checks, package updates, housekeeping and manual restarts
continue under their existing policies. Opening the page never creates or
extends a hold. The controls require a writable home owned by the panel
account; locked service accounts cannot create holds for another user.

Open **Agent inbox** to read a mailbox, send a message, or acknowledge one after
handling it. Unread messages appear first with sender labels and UTC timestamps;
**Mark read** explicitly acknowledges a handled message. **Write message** opens
the composer without JavaScript. If sending fails, the mailbox, recipient,
sender, and draft text remain available for correction. Messages are limited
to 4,000 UTF-8 bytes, so non-ASCII text can reach the limit in fewer characters.
Terminal workers can use `basaltw agent inbox wait` between tasks
for cooperative wake. Messages survive restarts and reading never acknowledges
them. Mailboxes share the panel account's trust boundary; root-profile panels
cannot expose another account's inbox.

See [Maintenance agents and cross-session mail](../operations/maintenance-agents.md) for
installation and sign-in, flags, escalation rules, storage limits, recovery,
and the terminal-worker protocol.

## T3 Code workspace

When T3 is selected and installed, open **Agents → T3 Code** or the
**Coding workspace** card on the Agents dashboard. **Open T3 Code** opens the
HTTPS route registered as `t3code` by the gateway in a separate tab. The public
port is allocated by the gateway; the panel does not guess it from its own URL
or T3's loopback listener. Sign in or pair through T3 as needed. Opening the
link does not issue credentials, start a task or update the server.

Use T3 for repository prompts, goals, worktrees, schedules, pull requests and
browser preview. Manage updates in **Settings → Environments → Update server**.
The panel's separate T3 updater and nine repository starters have been removed:
repository health, CI repair, dependency updates, documentation updates, security
review, release review, GitHub Actions review, browser smoke checks and Godot
export checks. Use the project's canonical checks in a T3 conversation instead.
This removes the unattended starter forms, not the managed browser, Godot export
or host setup capabilities.

A missing or ambiguous route shows recovery guidance. Inspect
`basaltwater-web forward list --json` on the host and reconcile the managed T3
route through setup, or use an existing T3 Connect connection. The panel does
not change routing or authentication automatically.

Existing independent tasks, schedules, workspaces and receipts remain here with
their original settings. To move an ordinary coding schedule, pause it under
**Independent tasks**, review its prompt and cadence, then create the replacement
in T3. Verify one run before removing the old task. Use one scheduler per job.
If you return to the independent runner, stop the T3 schedule before resuming
the saved task. T3 does not import Basaltwater's context, receipts or lifetime
limits automatically; retain the independent runner when those controls matter.
