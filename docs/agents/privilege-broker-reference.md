# Privilege broker reference

This reference describes the administrator-owned policy and implementation
boundaries for [privilege approvals](privilege-approvals.md). It is for people
who administer the VM, not agents requesting an action.

## Policy and allowlists

Root owns `/etc/basaltwater/privilege-broker/policy.json`. A new policy has an
empty `services` mapping and `"reboot": "approve"`. It accepts only these
rules:

```json
{
  "services": {
    "example.service": "approve",
    "reviewed-worker.service": "allow"
  },
  "reboot": "approve",
  "commands": "approve",
  "ttl_seconds": 3600
}
```

This fragment describes the mutable values; retain the installation's existing
machine identity and requester UID. `approve` needs a browser decision for
every request. `allow` executes automatically but is still audited.
Unregistered services are denied. Reboot accepts only `approve` or `deny`.
The schema permits at most 32 exact `.service` names and an expiry of 30–86,400
seconds. New installations default to 3,600 seconds; setup reruns preserve the
administrator's existing value. Commands always require individual approval.
The broker reads policy for every request and before execution, so a
change invalidates an outstanding request.

Keep the non-secret policy root-owned with mode `0644`: the dedicated approval
account also reads it at startup. Setup reconciles `/etc/basaltwater` and its
`privilege-broker` directory to root ownership and mode `0755`, including on
reruns after an installation with a restrictive umask. These directories must
permit the approval account to traverse to the policy. An atomic policy rewrite
must explicitly retain mode `0644`; a writer's private default of `0600` prevents
the HTTPS service from starting. Keep `auth.json`, `server.key`, and broker audit
state private. If the approval page refuses connections after a policy edit,
inspect the service through root SSH or the VM console and restore policy
readability and directory traversal; the page cannot approve its own recovery.

Registering a service grants every effect of restarting it. Audit its unit,
drop-ins, environment, executable, scripts, configuration, dependencies, and
lifecycle hooks first. Never register a root service that consumes
agent-writable code or configuration. The broker does not snapshot transitive
service inputs; avoid changing service definitions while requests are pending.
Never allowlist the broker or approval service themselves.

## Trust boundaries

In standalone approval-panel mode, the coding account has no sudoers grants,
privileged supplementary groups, or polkit authorization. Integrated main-panel
approvals preserve the setup account's existing sudo policy. Root-profile
panels use their dedicated non-root service UID as requester.
The root broker accepts requests only from the configured requester's
kernel-authenticated Unix-socket identity. The locked `basaltwater-approval` service
identity alone can use the separate decision socket. The standalone approval
panel uses its own HTTPS origin and Basic Auth record, has no cookies, and does
not share the web-panel credential.

Main-panel approvals are enabled by default. Nginx authenticates the ordinary
panel login and routes `/approvals`, `/approvals/admin`, fixed-action review paths
under `/approvals/admin/ACTION`, and exact `/requests/ID` paths to an HTTP
Unix socket owned by the isolated approval identity and proxy group. The
approval service checks the kernel peer UID against `www-data`, the single
forwarded login, and the configured panel Host/Origin. The requester account
has no decision-socket access and cannot authenticate a direct connection to
the review socket. Credentials are stripped before proxying. The main-panel
login, proxy and browser origin are trusted to authorize approvals: compromised
content on that origin could act through the signed-in browser. Deploy only
the standalone panel when an independent credential and origin are needed.
Integrated approvals follow panel transport; HTTPS is recommended.

Both sockets accept bounded messages with strict action fields. Execution uses
the reviewed exact argv (fixed `systemctl` argv for structured service/reboot
actions), a clean environment, no shell, and no privileged output
returned to the agent. Policy, code, credentials, and their ancestor paths must
be root-owned, non-symlinked, and not group/world writable.

The broker and HTTPS service retain their filesystem/device/home sandboxes.
An approved `command.run` dispatches a unique transient root unit through the
system manager and waits for it to stop. Its fixed, root-owned
`common/service_tools/privilege_command.py` helper receives only the reviewed
argv as bounded JSON on a private stdin pipe. User arguments never enter
systemd's command-line/environment substitution. The helper revalidates argv
and executes it with a clean environment and closed interactive stdin.

The transient unit deliberately has host filesystem, device and network access;
that authority is described in the reviewed effects. It has no restart, a
60-second command timeout, a 65-second unit runtime cap, a five-second stop
deadline and control-group termination. The broker waits up to 80 seconds;
the unit is limited to 1 GiB of memory and 128 tasks. A signalled systemd client or
a lost wait response stays uncertain and is never replayed. Child output is
discarded. `execution` metadata records only state, exit code and a fixed failure
category, including on the review page. Existing audit databases gain a nullable
execution column without losing requests or events.

The approval page checks the configured Host and Origin, uses per-request CSRF
tokens, escapes agent-supplied text, disables caching and framing, and has no
unauthenticated request feed. Login failures are globally limited to 20 per
minute on the standalone listener; integrated routes use the panel's Nginx
login throttling and fail2ban policy. Keep access source-restricted: flooding can affect availability.
Approval origins support DNS names, IPv4 literals, and bracketed IPv6 literals.
Controller-to-target arguments carry the effective approval hostname, including
an explicit override. IPv4 CIDR setup targets such as `10.0.0.20/16` use the
address `10.0.0.20` for approvals; the prefix remains part of setup metadata.

## Audit, limits, and recovery

The root-only database is
`/var/lib/basaltwater-privilege-broker/requests.sqlite3`. Its `requests` and
`events` tables record decisions and outcomes. It is protected from the coding
account, but not tamper-proof against root. Execution claims are committed
before effects. Pending requests expire on restart; an in-progress action is
marked uncertain and never replayed.

The request socket also permits its authenticated owner to cancel pending or
approved requests. Cancellation shares the execution-claim lock and commits a
`cancelled` state and UID-attributed event before responding. It cannot cancel
another UID's request or an action already claimed by the worker. Cancelled
requests remain terminal across broker restarts and stale browser decisions;
cancellation does not erase audit records or reset the hourly request quota.

There can be eight outstanding requests and 30 requests per UID per hour. The
database capacity is bounded and fails closed. Archive it as root with the
services stopped if storage fills.

The managed units are `basaltwater-privilege-broker.service` (root) and
`basaltwater-privilege-approval.service` (locked `basaltwater-approval` account).
The web service receives standalone credentials through systemd `LoadCredential`.
An integrated-only service has no standalone credential or TCP listener.
Root SSH remains the setup and recovery path. The first version has no
passkeys, multi-user roles, push notifications, or browser password recovery.

Setup and disable query the manager for both unit names even if their on-disk
fragments are missing: loaded services can still hold credentials. Existing
fragments must pass the root ownership/path checks and carry the broker's
management marker. Disable requires both services to be observed inactive and
disabled before removing authentication and reporting success. Unknown states,
query errors, and failed stops fail closed and retain recovery evidence.

A rerun, re-enable, or password rotation deliberately stops and restarts both
services. This reloads systemd credentials and expires pending/approved requests;
executing requests become uncertain on broker startup and are never replayed.
The policy's machine identity and administrator choices, audit database, and
terminal decisions are retained. Rollback never restores a database snapshot or
replays an approval.

Broker setup owns a transaction through activation and the selected review
listeners' readiness checks. Before mutation it privately snapshots policy, authentication,
certificate/key, polkit rule, unit files, their modes and ownership, directory
metadata, and each unit's activation and persistent/runtime enablement. Failure
or a catchable interruption quiesces the replacement, restores those files and
metadata, reloads units, restores the prior activation/enablement, and verifies
the previous endpoint if it was active. A failed first installation returns to
stopped/disabled services and removes its partial broker files.

The stable lock and operation marker are under `/etc/systemd/system/`:
`.basaltwater-privilege-operation.json.lock` and
`.basaltwater-privilege-operation.json`. Recovery snapshots are in a root-only
`.basaltwater-privilege-*` directory there; its `previous.json` contains private
credentials and must never be pasted into logs or agent chat. Failed or
unverifiable restoration retains the marker and backup and blocks setup and
disable retries. Process death also leaves the guard in place. Recover through
root SSH or the console using the [transaction recovery guide](../operations/transaction-recovery.md):
inspect actual service/file state and the recorded backup, quiesce the group,
restore broker files and their metadata, reload units, then restore and verify
the recorded activation/enablement and HTTPS endpoint. Resolve the marker only
after recovery is verified; never delete the stable lock to bypass it.

This rollback covers broker-owned files and service state. Account creation or
locking and shared certificate prerequisites (OpenSSL and CA/certificate files)
remain outside this boundary and may
persist after failure. They run before broker quiescence so a failure there
leaves the previous broker endpoint running. The transaction validates directory
safety before prerequisites and refreshes its directory snapshot afterward,
before broker mutations. Rollback preserves a shared `/etc/basaltwater` parent
created by prerequisites and its existing subtrees, while removing directories
created by the broker. A safely rolled-back first installation permits a retry.

## Panel administration operations

The [Admin controls screen](../services/web-panel.md#admin-controls) requests `admin.run`
with only an `action` chosen from the installed catalog. It always requires
individual approval; reboot and shutdown also respect the existing `reboot`
deny rule. Review the fixed helper argv and the described effects. Actions can
update packages, replay saved setup, reload Nginx, restart the panel, or schedule
host power changes, so approving them can interrupt active work.

Administrator-initiated actions use approval-socket-only `admin-catalog` (no
parameters), `admin-preview`
(`admin_action`) and `admin-approve` (`admin_action`, reviewed plan `digest`,
`actor`) messages. The catalog reports fixed availability and policy/busy reasons
without exposing request plans. Preview reads the fixed plan and availability without creating
a request. Approval rechecks availability and the plan digest, creates the ordinary
audited `admin.run` request under the configured requester UID, then approves that
exact request. All quotas, policy checks and execution claims still apply.
A policy change during acceptance can leave a pending or invalidated request;
inspect the queue before creating a replacement. These messages accept no arbitrary
argv or a caller-selected UID, and the requester socket rejects all three messages.
The isolated HTTP service binds the allowed timing variants to a random one-use
review ticket, expires it after five minutes, and consumes it before broker I/O.
Each listener retains at most 32 tickets; restart invalidates all outstanding
forms. Host, Origin, authenticated peer/login and strict form checks apply to
this path too. Lost responses are never replayed automatically.

`reboot` calls logind's `ScheduleShutdown` through `busctl`, using a fixed reboot
type and a deadline computed by the trusted executor five seconds ahead. This
also supports systemd versions before `systemctl --when`; see the
[systemd 252 logind interface](https://www.freedesktop.org/software/systemd/man/252/org.freedesktop.login1.html).
The browser supplies neither the timestamp nor D-Bus arguments. A scheduling
failure is reported without attempting an alternate reboot command.
The explicit `reboot-warning` variant uses `shutdown -r +2` to warn logged-in
users and wait two minutes. Both variants and power-off respect `reboot: deny`.
`shutdown -c` cancels a still-pending logind power schedule, including the short
restart. A successful worker result confirms scheduling, not host return.

Long jobs are dispatched as the detached root
`basaltwater-admin-maintenance.service`, with a six-hour runtime cap and no
automatic restart. The broker records `dispatched`, not command completion.
The helper records its latest result in a private root-owned file under the
broker state directory. Its fixed commands use a clean environment, no shell,
and no interactive stdin. Root output is discarded. The shared transient unit
and file lock prevent concurrent maintenance; cancelling a pending broker
request does not stop an already dispatched job. Use administrator access to
investigate or stop that unit deliberately.

For `update-packages`, `apt-get update` uses
`APT::Update::Error-Mode=any`, so an index error fails the job before the
upgrade command. The update command keeps its 60-second dpkg lock wait.

Sanitized job status includes a fixed stage (`preflight`, `source_verification`,
or `command`), one-based command index and fixed failure category. Each stage
is persisted before its effects. Categories distinguish unavailable actions,
rejected source, timeout, launch/validation errors, child failure/signal and
interrupted jobs. Exception text and output remain private. Older results keep
unknown stage/category values instead of inventing diagnostics; invalid values
make the result unavailable. Confirmed absence of the job-history file is shown
as no latest result; unsafe, corrupt, or inaccessible state is unavailable. The
helper validates the trusted state directory and checks the file without
following its final symlink. It reads the job record before observing the unit,
then re-reads protected history after every inactive or failed observation,
including when the first snapshot is empty or terminal. Two confirmed empty
snapshots mean no job history. A terminal record for the same job wins; an
unchanged running record is reported as interrupted and is never retried
automatically. If history changes to a different job or either read cannot be
confirmed, both the latest result and unit state are reported unavailable so
the panel blocks new controls.

The CLI's `agent privilege admin-status --json` and the panel show the fixed
diagnostics. Each helper invocation records a random `job_id` before effects;
refresh passes it only through its clean child environment. The target setup
marker retains that identity through steps, failure and explicit recovery.
Only a protected, schema-validated marker for that exact job can supply
`setup_context`: phase, pre-step/step stage, numeric step index/count and an
allowlisted error class. Step names, exception text, configuration and root
output are omitted. The helper persists safe context with its final result,
so a later marker cannot replace an older job's explanation. Process-loss
inspection can correlate a still-running record before marking it interrupted.
Legacy records without a job identity and unrelated/corrupt/missing markers
leave context unknown. New readers cannot reconstruct diagnostics omitted by
an older running broker; source files and loaded service code remain separate.

There can be only one pending/approved/executing administration request at a
time. A new `admin-status` requester message returns bounded owned request
metadata and sanitized job state; it cannot execute actions, decide requests, or
read command output. Its `privilege_requests` field lists up to 20 recent
requests owned by the requester, across all supported operations, with status,
approval expiry, a fixed operation/target summary, safe execution metadata and
the independently authenticated review URL. It omits command arguments,
agent explanations and approval digests, and trims the list to the transport
size limit. `restartable_services` names the registered structured restart
targets. Unregistered follow-up targets can use individual approval of the
exact restart argv through the existing `command.run` contract; their receipt
must match that exact operation and argument vector before launch. This does
not add a service rule or relax broker policy. `requests` retains
administration-only metadata for job gating and can also be trimmed to fit.
An older broker omits the new field; the panel links to the configured review
queue and reports the listing as unavailable. Both review surfaces retain the
isolated approval identity and existing request quotas. A lost dispatch response remains uncertain, and a
broker restart never retries it. Root refresh additionally checks all installed
code and Git metadata for root ownership, symlinks, and writable permissions.

## Shares & Data recovery operations

The [Shares & Data page](../services/shares-data.md) requests `data.run` with
exact fields: `action` (`verify`, `repair`, `restore`, `accept`), normalized
absolute `directory`, `database`, `file`, restore-only `backup` (otherwise empty),
the exported `finding` revision and a fresh `job_id`. All paths are bounded to
1,024 characters. Scrub actions use `allow` mode: panel review and confirmation
submit directly, without a second browser approval. This grants the configured
requester authority to request these typed actions, including quarantine,
deletion and acceptance, but no arbitrary command or decision-socket access.
Existing expiry, policy-digest revalidation, claim and quota rules remain. At most one
storage request (`data.run` or `data.routine`) may be pending, approved or
executing at once.

Batch verify, quarantine and deletion use `action`, normalized `directory` and
`database`, a fresh `job_id`, and up to ten `items` containing exact `file` paths
and `finding` revisions. Canonical parameters are limited to 10,000 bytes.
The panel requires `CHANGE FILE` for quarantine and `DELETE N FILES` for deletion.
All selections are checked before effects, then each item is checked again.
Disposition stays inside the source filesystem/subvolume, retains parity and
mirror protection, and cannot change read-only snapshot flags. Batches retain
durable per-job claims and per-file results and are never replayed automatically.

After claiming execution, the broker writes the approved public parameters to
a root-only `data-payload-JOB_ID.json` under its state directory. The fixed
`data_job.py` helper receives only the hexadecimal job identity through systemd;
user paths never enter systemd argument/environment substitution. The helper
validates and durably consumes the protected payload before mutation. A failed
or uncertain dispatch is never retried. If dispatch leaves a payload behind,
preserve it and inspect the request and actual unit state before removing it
through administrator access.

The detached `basaltwater-data-management.service` has a six-hour cap, no
restart and control-group termination. It serializes its own result publication
and acquires `/run/lock/storage-ops.lock` before loading current saved setup.
Only a matching configured scrub job and open finding can proceed. Revision
checks bind classification, check time and recorded file identity. Recovery
rechecks that identity at the staged-remediation boundary. Older findings
without identity require verification before mutation. The existing CLI mount,
confinement, staged-candidate verification and retained-original safeguards also
apply. Acceptance explicitly replaces parity for independently confirmed content.

The protected `data-job.json` contains only the latest job’s public parameters,
fixed status, result category, failure category and timestamps. `data-status`
on the requester socket returns this record, unit state and at most ten owned
recovery requests, trimmed to the transport limit. It never reads NAS data or
returns PAR2 output, private evidence, recovery copies or approval authority.
The worker reports remaining findings separately from successful recovery.
An unchanged running record observed with an inactive/failed unit becomes
interrupted; raced or unsafe history becomes unavailable. A broker `dispatched`
receipt does not establish completion. The worker and its storage lock continue
independently of panel or broker restart; cancelling a request cannot stop an
already dispatched worker. Root SSH remains the inspection and stop path.

## Storage routine operations

`data.routine` always requires individual approval. Its exact public fields are
`action` (`change` or `run`), `before`, `after`, the exported configuration/pause
`revision`, and a fresh `job_id`. A routine is exactly `kind` (`sync`, `backup`,
`scrub`), `spec` (the ordinary three- or four-field storage declaration) and a
boolean `paused`. Create has no `before`; remove has no `after`; run has one
existing `before` and no `after`. Edits retain the kind. Both paths are canonical
absolute directories, limited to 512 characters. The complete manageable
selection is limited to 256 routines and rejects duplicate or ambiguous cadence
keys, shared parity directories and mirror destinations overlapping parity.
Existing relative scrub databases are exported as canonical absolute paths.

The broker stages the claimed public plan in a private
`routine-payload-JOB_ID.json`. The fixed `sync/routine_job.py` helper consumes it
once; only the job ID enters systemd argv. The detached
`basaltwater-storage-routine.service` has a six-hour runtime cap, no restart and
control-group termination. Broker dispatch is separate from worker completion.
`routine-status` returns fixed local unit/result metadata and up to ten owned
requests, trimmed to the transport bound. The latest `storage-routine.json`
records only job identity, fixed result/failure categories and timestamps.
Neither status operation reads NAS paths or returns worker output.

The worker holds the shared target-setup owner and storage operation lock,
rechecks fresh metadata and the exact prior declaration, and preserves unrelated
saved settings. A change writes only the selected declaration and
`/var/lib/storage-ops/controls.json`, then reconciles the fixed hourly units.
It never calls inline setup's initial copy/parity builders or directly starts
the storage service. Newly active/edited/resumed routines require installed
`par2`, or `rsync` and `unshare`, before publication. A paused declaration can
be saved without those tools. Package installation remains ordinary setup work.

A durable `storage_routines` setup marker spans pause/configuration publication
and timer activation. Failure retains it for explicit reconciliation. Scheduled
and selected runs refuse any unfinished setup marker or invalid pause state.
No automatic replay or multi-file rollback is claimed; follow
[transaction recovery](../operations/transaction-recovery.md) before resolving
the guard. Removal retains files, parity, findings, cadence/retry history and
logs. Removing a scrub also removes its mirror integrity safeguards; pausing
keeps its declaration and those protections while skipping full verification
and daily parity maintenance.

Run once selects exactly one currently configured routine, including a paused
one, and ignores its cadence/retry delay for that run only. It keeps the complete
scrub configuration for mirror safeguards, all mount/worker guards, and normal
completion/retry recording. A completed scrub with damage reports `findings`;
an incomplete operation reports failure. A clean completed scan that repaired
files reports `warnings`, preserving the repaired-file signal from the worker.
Mirror precautions also report `warnings`. Pause state remains unchanged.

The management export includes scoped cadence/retry history separately from
configuration availability. Corrupt history does not hide the declarations or
pretend that work succeeded. A scrub cadence timestamp can be its initial
deferral reference; the scrub report's full-scan record establishes actual scan
completion. Only configured routine identities are exported, never retained
unconfigured history or private error output.

## Refresh interrupted during source activation

For a controller-installed source snapshot, refresh publishes a private
operation marker beside the installed source before its first rename:
`<source-parent>/.basaltwater-source-refresh-operation.json` (normally
`/opt/.basaltwater-source-refresh-operation.json`). It records the exact
`repository_path`, `backup_path`, `staged_path`, `temporary_path`, and the
`runtime_data_names` that are being carried across. A completed activation
removes the marker after writing its bounded `.last.json` result. A caught
failure that cannot restore the original source keeps the marker with
`recovery_required`; process loss leaves it `in_progress`. The marker identifies
planned copies but does not prove which rename completed. These renames are not
one filesystem transaction, and this evidence does not claim atomic
power-loss recovery.

An unresolved marker blocks refresh and its dry-run check. First confirm that
the original maintenance process has exited and keep all recorded paths in
place. Inspect the record and each path as root:

```bash
sudo systemctl status basaltwater-admin-maintenance.service
sudo python3 -m json.tool /opt/.basaltwater-source-refresh-operation.json
sudo ls -lai -- /opt/basaltwater /opt/REPLACE_WITH_RECORDED_BACKUP_PATH /opt/REPLACE_WITH_RECORDED_STAGED_PATH
```

Use the exact paths from the marker if they differ from the normal `/opt`
layout. Check which copy has the old installed source, which has the downloaded
source, and where every name in `runtime_data_names` currently resides. Inspect
symlinks with `sudo readlink` and compare file identity, modes and contents
before moving anything. Preserve the marker and all copies while deciding
whether to restore the old source or finish activation. Never rerun refresh or
blindly replay a rename while this marker exists. If a path or runtime entry is
missing or duplicated and its owner is unclear, keep the evidence in place and
continue recovery from administrator SSH or the VM console.

After restoring or activating a complete source tree at `/opt/basaltwater`,
verify its code, retained runtime entries, ownership and modes. Then resolve
only the inspected operation using that verified source:

```bash
sudo /usr/bin/python3 - <<'PY'
import sys
sys.path.insert(0, "/opt/basaltwater")
from lib.operation_state import OperationStateStore

marker = "/opt/.basaltwater-source-refresh-operation.json"
expected_operation_id = "REPLACE_WITH_RECORDED_OPERATION_ID"
store = OperationStateStore(marker)
record = store.load()
if (record is None or record.operation_type != "source_refresh"
        or record.operation_id != expected_operation_id):
    raise SystemExit("Expected source refresh recovery marker is missing or has changed")
outcome = "rolled_back"  # Use "succeeded" only after verifying completed activation.
store.complete(record.operation_id, outcome=outcome)
PY
```

Keep the retained backup until a later successful refresh and setup are
verified. A separate target setup marker at
`/opt/basaltwater/state/setup-operation.json` also blocks refresh when it is
still `in_progress`, preserving its original job correlation for inspection.
Use the [transaction recovery guide](../operations/transaction-recovery.md)
before resolving such a hard-killed setup. A matching
`recovery_required` setup remains eligible for its idempotent setup replay;
refresh refuses a profile, machine type or username mismatch before changing
source.
