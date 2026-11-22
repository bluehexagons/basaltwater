# Privilege broker panel and storage operations

This is the [VM administrator's reference](privilege-broker-reference.md) for
brokered panel and storage operations. See [privilege approvals](privilege-approvals.md)
for the request workflow.

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
recovery requests. Older rows are trimmed first to meet the transport limit.
If necessary, the newest row retains its identity, state, execution metadata and
review URL but replaces exact paths with `target_count` and parameters containing
only `action` and `job_id`. Full owned request status and review still contain the
exact parameters, and the request remains cancellable before execution is claimed.
An oversized worker record is omitted with `latest_omitted: true`; unit and
outstanding-request gates remain available. Inspect the retained worker record
through administrator access. The status operation never reads NAS data or
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
