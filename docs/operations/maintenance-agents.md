# Maintenance agents and cross-session mail

Enable a supervisor tailored to the host's saved setup:

```bash
basaltw setup server_web HOST USER --maintenance-agent --web-panel
basaltw patch HOST USER --maintenance-agent \
  --maintenance-agent-model YOUR_ECONOMY_MODEL \
  --maintenance-agent-escalation-model YOUR_STRONGER_MODEL
```

The **Agents → Maintenance** tab enables or disables automatic supervision,
saves the same policy options, requests a check, and displays up to 40 recent
records. The Agents dashboard also shows saved maintenance state and can
request a one-time check.
Panel setups install the supervisor disabled unless explicitly enabled.
The supervisor runs independently of the panel and T3 Code. Without a panel,
configure it using setup flags or the local CLI as the control-directory owner:

```bash
basaltw agent maintenance-agent status --json
basaltw agent maintenance-agent configure --maintenance-agent \
  --maintenance-agent-daily-calls 2 --maintenance-agent-timeout 10
basaltw agent maintenance-agent run --json
# Explicitly request installed managed housekeeping once:
basaltw agent maintenance-agent clean --json
```

For manual-only operation, save `--no-maintenance-agent`. This pauses all
automatic maintenance without changing the saved check, cleanup or model
preferences. `run` still requests one bounded check while disabled and never
enables automatic maintenance or dispatches cleanup. It checks host diagnostics
and, when `security` is selected, refreshes security evidence regardless of the
audit interval. Manual reviews share the same daily attempt and timeout limits
as automatic reviews; a request does not guarantee an AI call.

**Run managed cleanup once**, or `clean`, separately authorizes one dispatch of
the installed package, log, temporary-file and user-cache cleanup jobs. This
includes APT autoremove of unused packages under the existing job policy. It works
with automatic maintenance off and preserves all saved schedule preferences.
Missing or unverifiable job definitions are reported as blocked. The request
contains a fixed cleanup flag, never a shell command or model-generated action.
Requests are claimed durably before checking the host, then acknowledged only
after their report and pending jobs are saved. A restart resumes jobs that have
not been attempted, including an explicit cleanup with automatic maintenance
off. An interrupted dispatch has an unknown outcome and is never replayed
automatically; inspect Scheduled jobs and the named service before retrying.
Disabling automatic housekeeping cancels its pending, unattempted jobs.

Automatic host diagnostics and security audits have independent schedules:
`--maintenance-agent-host-checks` uses the minute `interval`, while
`--maintenance-agent-security` uses `audit_days`. For security-only automation:

```bash
basaltw agent maintenance-agent configure --maintenance-agent \
  --no-maintenance-agent-host-checks --maintenance-agent-security \
  --maintenance-agent-audit-days 7 --no-maintenance-agent-routines
```

The fixed timer polls every 15 minutes; enabled schedules run on a later poll
when due. Saved cleanup permission is independent of both diagnostic schedules
and must be disabled separately when unwanted. Disabling automatic maintenance
pauses all three automatic activities while preserving their individual
preferences for later use.

With a non-root setup user, that user owns maintenance controls and supplies
the Codex installation and credentials. Root setups use the locked, non-login
`basaltwater-maintenance` account with home `/var/lib/basaltwater-maintenance`.
When a root setup includes the panel, the `basaltwater-web-panel` account owns
the controls; otherwise the maintenance account owns them. An administrator
can run the CLI as that account with `sudo -u ACCOUNT -H python3
/opt/basaltwater/basaltwater.py agent maintenance-agent ...`.

Root setup reruns can switch controls between these two managed accounts when
adding or removing the panel. Saved policy and reports are retained, including
when using `--no-web-panel --no-maintenance-agent` to remove panel access and
pause supervision together. The control directory, rewritten settings, and
private settings lock change owner. Private supervisor state stays root-owned,
and exported reports remain root-owned with the current controller's readable
group. Unrelated directory owners and symlinked directory components are refused.
Setup acquires the policy lock before changing ownership or retaining saved
settings. An active policy edit stops setup with a retry message and preserves
the previous controller's access.

Explicitly enabling AI reviews installs Codex on Debian targets. Setting
`--maintenance-agent-daily-calls 0` skips that installation. Panel-only
setup prepares controls without installing Codex; select `--agent-tool codex`
or rerun with `--maintenance-agent` before expecting AI reviews. On CachyOS,
use its existing `--agent-tool codex` installation path. Sign in as the AI
identity with its own credentials; credentials are never borrowed from root
or another account. For a root setup, an administrator can invoke the installed
client directly:

```bash
sudo -u basaltwater-maintenance -H \
  /var/lib/basaltwater-maintenance/.local/bin/codex login --device-auth
```

Missing tools, credentials, or model-selector metadata leave an actionable
record. Deterministic checks still work. OCI targets cannot install persistent
system services. Setup reruns preserve panel policy when the corresponding
setup fields were omitted; explicitly saved maintenance flags are reapplied.
Disabling automatic supervision affects subsequent scheduled work, not an
already running review or an explicit manual request.

## Checks, routines and notifications

The fixed host service checks root filesystem space and inodes, failed units,
configured persistent services and timers, and pending reboot state. Its
non-secret context includes the profile, machine type, selected agent tools,
and whether storage, sync, scrub and backup workloads are declared. Declared
backup support does not establish backup freshness or restore readiness.

Space warnings start at 85% and errors at 95% of used plus available space,
matching `df`'s treatment of reserved filesystem blocks. Reserved space cannot
hide pressure from the non-root account. When findings remain unchanged between
model reviews, the record says that review is deferred; it does not report the
host as healthy.

When the HTTPS gateway is installed, bounded host checks also inspect ordinary
forwards and managed previews. This catches legacy preview sites registered as
ordinary forwards. Up to ten entries from each inventory are checked; omitted,
unavailable or malformed inventory is reported explicitly. Inactive previews and
unreachable TCP upstreams produce owner-scoped findings with exact repair or
retirement guidance. TCP readiness does not establish HTTP or public health.
Retirement still requires a current-use and ownership recheck and approval;
one failed probe, a name or age does not authorize automatic removal. Managed
previews use their lifecycle command rather than removing their forward alone.
Retiring a route does not remove retained project files or prove storage savings.

Every completed check includes a deterministic summary of measured findings and
next actions, even when AI is unavailable, deferred or out of budget. Storage
findings link to a cleanup task whose approved follow-up applies and verifies
the selected cleanup. Each gateway finding opens a web-hosting task with its
recorded route, owner and bounded evidence. Inventory is scoped to the executing
account; an absent route does not clear a finding owned by another account.
When owner access is unavailable, the task gives the named owner or administrator
exact inspection, repair or retirement steps and verification. Changed or expired
records must be reopened before preparation. AI reviews receive bounded valid
JSON evidence with explicit omitted counts;
they add prioritization and concrete apply/verify steps rather than another
generic review recommendation.

Periodic security checks inspect effective SSH root/empty-password policy and
permissions on `/etc/shadow`, `/etc/gshadow` and the main SSH configuration.
These bounded checks are not a full vulnerability scan or dependency-advisory
audit. Unavailable evidence becomes a finding. Between audits, the last security
findings remain visible until the next audit; disabling auditing drops them from
subsequent check reports. Between host checks, saved host findings remain visible
on security audit records when the host schedule is enabled. Each record includes
`source` (`manual` or `automatic`), `host_check`, `security_audit` and the selected
`policy`, so carried findings can be distinguished from checks performed in that
run. The same non-secret policy is included in AI review context, including
schedules, cleanup permission, model selection and attempt/runtime limits.

Routine maintenance is opt-in. The fixed root supervisor may request the
existing verified `cleanup-maintenance.service` and
`user-cache-maintenance.service` once daily, independently of diagnostic
intervals. Manual checks never request these jobs; the separate `clean` action
requests them once. Those jobs retain their own
account, lock, retention and capability rules. A request is reported separately
from job completion; inspect **Scheduled jobs** for the outcome. No AI output
is executed, and no general root command, package upgrade, restart, deployment
or privileged repair is authorized through this feature.

Findings are recorded locally before notification delivery. New findings,
severity changes, daily reminders and recovery use the host's existing
[`--notify` targets and notification threshold](../services/notifications.md). Failed
delivery is retried at a later check. A successfully processed notification may
be filtered by the host's threshold; the record does not claim receipt. Local
records appear without enabling the
panel's optional remote-notification receiver. AI output is kept in the record;
outbound messages contain the deterministic finding summaries.

## Cost policy and flags

Healthy checks make no AI calls. Changed findings may receive one review;
unchanged findings are reconsidered at most daily, with an additional escalation
opportunity on their third consecutive observation. Model attempts are reserved
durably before execution, so failed or interrupted calls still count. A restart
marks an unfinished recorded check interrupted, and the panel shows that status.

| Flag | Default | Range or behavior |
| --- | --- | --- |
| `--maintenance-agent` / `--no-maintenance-agent` | Disabled | Enable automatic work or select manual-only operation; retain saved preferences |
| `--maintenance-agent-host-checks` / `--no-maintenance-agent-host-checks` | On | Schedule host diagnostics independently of security audits |
| `--maintenance-agent-interval` | 60 minutes | 15–10080 minutes |
| `--maintenance-agent-routines` / `--no-maintenance-agent-routines` | Off | Request existing managed cleanup jobs daily |
| `--maintenance-agent-security` / `--no-maintenance-agent-security` | On | Schedule bounded security audits independently; include fresh evidence in manual checks |
| `--maintenance-agent-audit-days` | 7 | 1–30 days |
| `--maintenance-agent-model` | `auto` | Explicit model ID or a cached economy-role model |
| `--maintenance-agent-effort` | `high` | `minimal`, `low`, `medium`, `high`, `xhigh` |
| `--maintenance-agent-escalation-model` | Empty | Explicit stronger model; empty disables escalation |
| `--maintenance-agent-escalation-effort` | `high` | Same effort choices |
| `--maintenance-agent-daily-calls` | 2 | 0–24 total attempts per UTC day; 0 disables AI |
| `--maintenance-agent-daily-escalations` | 1 | 0–4 stronger-model attempts; also counts toward the total |
| `--maintenance-agent-timeout` | 10 minutes | 1–60 per review |

New policies use high reasoning. Medium is the usual minimum for bounded work;
lower settings remain available for explicit trivial exceptions. Setup preserves
saved effort choices. To update an existing policy, select **High** in the panel
or configure `--maintenance-agent-effort high`.

`auto` recognizes `luna`, `mini` and `nano` roles in locally cached model IDs
and requires cached support for the selected effort. These names are role hints,
not verified price comparisons. Pin a reviewed model for predictable selection.
Unknown models never silently fall back to the account's configured model.
Critical findings or repeated unresolved findings can select the explicit
stronger model within both daily limits. Reviews use the read-only sandbox,
ephemeral sessions, disabled web search, and ignore user configuration to avoid
inheriting hooks, MCP integrations, provider overrides or paid service tiers.

Attempt and runtime limits reduce usage; they are not dollar budgets or token
limits. Provider billing and subscription limits still apply. This version
does not estimate prices or track actual token charges.

## Durable inbox and cooperative wake

Use **Agent inbox** in the panel or the CLI for messages shared by sessions
on one Unix account. No panel, Codex or T3 installation is required for the CLI.

```bash
basaltw agent inbox send parent --sender worker-build 'Build passed; artifact ready'
basaltw agent inbox list parent --json
basaltw agent inbox wait parent --timeout 60 --json
basaltw agent inbox ack parent MESSAGE_ID --json
```

Mailboxes accept 1–64 letters, digits, dots, underscores and hyphens. Messages
contain up to 4000 UTF-8 bytes. `list` reads without creating storage and returns
unread messages; `--all` includes acknowledged history. `wait` returns existing
or newly arriving unread mail, with a 1–3600 second timeout. Exit status 2 means
the wait timed out; status 1 means validation or storage failed.

Delivery is at least once: reading or waiting never acknowledges a message.
Acknowledge only after handling it and saving the result. Unread messages survive
session restarts. The inbox retains at most 256 messages within a 2 MiB serialized
storage limit and reclaims only acknowledged records when either limit is reached;
a full inbox refuses new mail without dropping pending
messages. JSON writes are atomic and independent writers share a process lock.
Corrupt state is preserved and reported rather than reset.

For a lightweight terminal-worker protocol, assign each parent/worker a unique
mailbox, include a task ID and result path in messages, and let an idle worker
call `wait` between bounded tasks. A parent can notify that worker with `send`;
the wait returns and the worker decides what to do under its existing scope.
Choose wait deadlines within the enclosing task's remaining runtime, join
workers before the parent exits, and keep editors in separate
[`agent workspace` worktrees](../agents/agentic-vms.md).

This mechanism does not launch workers, signal arbitrary PIDs, inject replies
into an active model turn, or resume T3 threads. Workers must cooperatively
read or wait. Sender labels are self-reported; every process on the same Unix
account shares this trust boundary. Messages are untrusted data, not new
permissions. Avoid putting credentials in them. The supervisor does not consume
inbox messages as host commands or authorization.

The inbox is private under `~/.local/state/basaltwater/inbox/`; the home and its
directory chain must be owned by the account without group/public write access.
A panel installed
with a non-root setup user shares that user's inbox. The dedicated panel account
used for root setups has no home and cannot expose another account's inbox.

## State and troubleshooting

| Path or unit | Purpose |
| --- | --- |
| `/etc/basaltwater/maintenance-agent.json` | Root-owned identity and non-secret setup context |
| `/var/lib/basaltwater/maintenance-agent/control/settings.json` | Private operator policy |
| `/var/lib/basaltwater/maintenance-agent/control/wake.json` | Coalesced check request; carries no command |
| `/var/lib/basaltwater/maintenance-agent/active-request.json` | Durable private claim until request acceptance is recorded |
| `/var/lib/basaltwater/maintenance-agent/state.json` | Root-only counters, audit findings and retained checks |
| `/var/lib/basaltwater/maintenance-agent/reports.json` | Root-exported records readable by the panel group |
| `basaltwater-maintenance-agent.timer` | Poll policy every 15 minutes |
| `basaltwater-maintenance-agent.path` | Wake on settings changes and check requests |
| `basaltwater-maintenance-agent.service` | Serialize deterministic checks and bounded reviews |

Inspect the service journal when records stop updating. A disabled policy does
not disable the timer/path units; they remain available for later enabling.
Check requests coalesce while a run is active. A later check preserves pending
cleanup authorization; requests arriving during consumption remain queued for
the next run. Writers and the consumer share the policy/controller lock.
Inspect interrupted reviews before
retrying. Preserve malformed state and correct its underlying permissions or
schema; deleting budget state can permit more calls than intended.

See the [Web Panel guide](../services/web-panel.md) for panel access and
[recurring maintenance](maintenance.md) for the existing deterministic jobs.
