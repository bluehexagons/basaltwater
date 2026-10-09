# Transactional execution and reconciliation

Status: the bounded transaction system is implemented. Host qualification stays
in the [release checklist](../BASALTWATER_RELEASE.md); broader recovery automation
and database migration rollback are separate follow-on work.

This plan addresses ARCH-01, ARCH-03, ARCH-05, ARCH-06 and ARCH-08 from the
[architectural risk review](ARCHITECTURAL_RISK_REVIEW_2026-08-07.md).
The implementation checkpoint below is current as of 2026-10-09.

## Contract and boundaries

A transaction protects a named resource while preparing, applying, verifying
and recovering its replacement or removal. Required command failures stop
dependent work. A failed or interrupted recovery preserves evidence and blocks
another operation at the same boundary.

These guarantees apply to managed release trees, generated systemd units and
operation/state files. A full setup is an ordered, fail-fast reconciliation:
its marker identifies partial progress, but it cannot reverse package installs,
user changes, arbitrary scripts or application data writes. Two separately
atomic state files are not a single filesystem transaction.

Nginx owns durable snapshots and verified recovery of its managed configuration
and generated TLS files. It is a separate boundary from application activation.
Application rollback does not restore database writes or migrations; SQLite
backups require an explicit operator
restore decision.

## Implemented execution contracts

- `lib.remote_utils.run()` raises on nonzero status by default, with typed
  command errors and redacted, bounded diagnostics. Explicit `check=False`
  returns a result for probes, verified installers and optional cleanup.
- Commands have a one-hour default timeout, positive per-call overrides and an
  explicit `None` opt-out. Timeouts raise even with `check=False`; shell
  process groups receive TERM/KILL cleanup.
- Argv-native calls avoid shell parsing where practical. SSH/SCP/rsync and
  Proxmox builders require approved keys in the workspace enrollment file.
  Key discovery is not approval; rotation remains an operator action.
- Required commands propagate failures through deployment, storage, app-server,
  build-server, CI/CD, firewall, SSH, locale, permissions and supported-host
  time synchronization. Requested APT/Flatpak installs verify the installed
  result. Password updates and hardened sudo removal cannot report success
  without applying or verifying the requested change.
- Cloudflare direct-access rules, Samba account enablement and fail2ban
  enablement are required. Gogs rollback propagates activation/restart failure
  and verifies the restored service is running before announcing restoration.
- Capability-specific container exceptions, optional diagnostics, stale-rule
  deletion and verification-based installers retain explicit best-effort
  handling.

The former in-memory callback framework, `lib/transaction.py`, was removed.
Sync and scrub own explicit fail-fast control flow; `lib/operation_log.py`
is diagnostic evidence, not a durable rollback engine.

## Caller inventory and maintenance

Run the source inventory without importing or executing target setup code:

```bash
python3 scripts/audit_command_contracts.py
python3 scripts/audit_command_contracts.py --unchecked
python3 scripts/audit_command_contracts.py --json
```

The 2026-10-09 checkpoint contains 749 direct calls: 385 required, 284
caller-managed results, 73 discarded best-effort results and 7 delegated
policies. The inventory covers root modules and owning source packages,
including imported aliases and calls inside the helper itself.

These are structural classifications, not semantic certification. A consumed
result still needs review for correct return-code handling; dynamic policies,
wrappers and shell expressions need manual review. Existing best-effort calls
include optional desktop installers, idempotent firewall cleanup, probes and
component-specific cleanup/rollback paths. Do not change all of them to strict
execution without checking absence/idempotency and recovery behavior.

Use this report when changing a component, then add failure-propagation tests
at its orchestration boundary. New required mutations must use the strict
default; intentional exceptions must inspect or verify their result, or explain
why failure is optional. Further component audits are maintenance work, not a
claim that every package installer provides full rollback.

## Atomic persistent state

`lib.atomic_io` writes same-directory private temporary files, flushes and
syncs data, atomically replaces the target and syncs its directory. Newly created
parent directories are persisted in their containing directories before state
publication; operation and shared-release directory creation uses the same
`ensure_directory_durable()` helper. Text and binary writes share the same
ownership, permission and sync behavior. Release renames also sync both parent
directories. A sync error after a rename may mean
the rename happened; activation and rollback inspect actual paths accordingly.
Release files and directories are flushed before completion and old-backup
cleanup; rename durability alone does not flush copied or built file contents.
The flush does not follow build-created symlinks or open special files as data.

Bounded readers require regular, non-symlink files and refuse FIFOs without
blocking. Duplicate object keys, nonfinite JSON constants, invalid shapes,
unreadable files and unsupported versions fail closed. Only missing state
receives defaults where the owning schema permits them. Writers refuse
nonfinite values and preserve the previous file on serialization failure.

These primitives protect setup/machine state, caches/history, webhook and
deployment configuration, release metadata, host/network inventories,
Cloudflare state, remote arguments and Gogs credentials. Proxmox registry reads
and ordinary saves now preserve malformed/incompatible records and reject
coerced field types, including cached facts. Explicit removal of a named
incompatible Proxmox record remains an operator repair path.

Schemas remain compatible with documented legacy records. Add a new version
when an incompatible representation is required; a version number alone does
not make a corrupt-state fallback safe.

## Durable operations and recovery

`lib.operation_state` provides versioned, bounded atomic markers. Nonblocking
kernel locks protect ownership through completion or store closure. Process
exit releases the lock, but an unfinished marker still blocks a fresh operation.
Recovery must use the recorded operation ID; stale IDs, corrupt records,
unsupported versions and unsafe files are rejected. Never delete stable lock
files to bypass ownership.
Conditional recovery transitions compare the exact previously inspected record
under the acquired lock. A changed marker with the same operation ID cannot
bypass the original eligibility decision.

Markers record context and recent phases. Completion writes a private
`<marker>.last.json` before removing the marker. It retains the most recent
256 phase transitions, final context, completion time and the `succeeded`,
`rolled_back` or `failed` outcome. This is bounded last-result evidence, not
a permanent audit archive. Persistence/removal failure leaves or restores the
marker so a new invocation cannot silently pass incomplete finalization.
A retained marker takes precedence over a last-result file written before
marker removal. Marker errors require verified recovery and preserve private
field contents instead of suggesting an immediate bypass.

Use [transaction recovery](../TRANSACTION_RECOVERY.md) for marker locations,
phase-specific repair, previous port/unit restoration, first-deployment
recovery and verified marker resolution.

## Setup and systemd reconciliation

Target setup records its current step before mutation and saves remembered
machine/setup state only after the full operation succeeds. It preserves
interrupted or failed progress. Handled failures may retry a matching setup
plan; hard-kill markers require explicit inspection first.
Failure-detail persistence cannot replace the original setup exception. Failed
invocations release ownership while retaining their marker, including when
called repeatedly in one process. Security activity records success only after
marker completion succeeds.

`lib.unit_transaction.replace_units()` serializes replacement, snapshots live
files and activation states, stages candidates privately and validates them
with `systemd-analyze verify` before replacing anything. Write, reload and
activation failures restore old files, ownership, modes, enablement and running
state. Timer/path changes do not restart an unrelated executing oneshot.
Incomplete rollback retains snapshots and its recovery marker.
Snapshots also survive failed marker completion. Restoration gates group
restarts on successful restoration of every file and daemon reload, so a
restored timer/path cannot activate an unrestored service and cached new
definitions cannot be restarted as if they were the old configuration.

Managed application, Antistatic, Gogs, CI/CD, storage operations and maintenance
units use this boundary. `remove_units()` shares its lock and marker, snapshots
files and states, stops the whole group before deletion, and restores the
service before rearming its timer/path on failure. Removal skips absent managed
files and preserves static units' enablement; runtime-enabled units use runtime
disable/enable. Stop timeouts, unlink sync errors, reload failures and incomplete
rollback retain the same evidence as replacement. This protects unit
configuration, not data changed by a service startup.

## Nginx reconciliation

Deployment Nginx reconciliation uses a shared operation lock and private,
durable snapshots of previous site files, link targets, ownership, permissions
and selected self-signed certificate/key files. Missing planned paths are
recorded as null. Configuration reads reject special files without blocking
and bound regular-file snapshots to 1 MiB per file.

The daemon must already be active. Apply persists files and links, requires
`nginx -t`, reloads and verifies activity before completion. A validation error,
reload timeout or finalization failure restores the snapshot, validates it,
reloads the previous configuration and verifies activity. Failed file restoration
prevents reloading a partial configuration. Incomplete recovery or process
death retains the snapshot and a blocking marker. Output after completion does
not trigger rollback. This boundary remains separate from release activation.

## Release activation

Static and manifest deployment share one lock for the deployment base and
inspect both marker types for the requested application. Switching deployment
formats cannot bypass unfinished recovery.
Plain static deployment refuses a release with managed service units. Retire
those services by deploying an all-static manifest before switching to the
plain static path, so a directory swap cannot strand running old services.
Preparation rejects unsafe destinations and symlinked shared state.
Source and release paths must not overlap; source copying refuses special
files without consuming their contents.

Both paths stage beside the active release, record deterministic paths before
activation and retain the previous tree until finalization succeeds. Manifest
builds and output validation happen while the old services continue running.
Activation verifies app-scoped stops, switches trees, activates managed units
and gates success on declared direct-loopback 2xx health checks. A stop timeout
still attempts to restore previously running services.
SQLite backups are integrity-checked, flushed and durably published before
older archives are pruned. Retention always keeps the newly created recovery
point even if older files have future modification times.

Handled failures restore the previous tree and unit snapshots. Manifest
rollback restores the previous port assignments, including removing a newly
created port file when none existed. Interrupted or incomplete recovery retains
staging/backup/failed trees and the marker. Successful activation tolerates
best-effort old-tree/source cleanup failures.
Rollback attempts each independent unit restoration, including after a rename
takes effect but its sync fails. Rejected trees remain until port restoration
and marker completion succeed. Reporting failures after the commit boundary
cannot trigger rollback of a completed activation.
An inner `UnitRecoveryError` keeps the manifest recovery marker even if its
release tree was restored; both transaction layers require reconciliation.

Immutable release directories with a stable `current` symlink were an earlier
proposal, not a requirement for the implemented directory-swap boundary.
Migrating layouts would add compatibility work without removing the need for
durable markers, verification and recovery. Longer release retention belongs
to a separate operator-facing rollback feature.

## Validation and remaining qualification

Mocked system calls and temporary directories cover command failures, marker
ownership, corrupt/unsafe state, atomic write interruption, post-rename sync
failure, service-stop timeout, failed activation, port restoration, interruption
and incomplete rollback. Tests assert retained evidence and refusal to start
another deployment after incomplete recovery.

The repository check also validates syntax, packaging and documentation.
Hosted VM, unprivileged LXC and direct Debian live qualification remains in the
release matrix; mocked tests do not establish that those hosts have passed.

Follow-on work is deliberately separate:

- automatic recovery across every recorded phase;
- multi-release retention and operator-facing rollback commands;
- database migration and restore policy;
- a general desired-versus-observed planning layer; and
- component-specific reconciliation beyond the managed boundaries above.

Do not reintroduce a general callback transaction framework or promise reversal
of arbitrary scripts and database writes.
