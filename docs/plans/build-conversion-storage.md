# Web-target conversion and build storage lifecycle

Status: initial conversion evidence, physical admission and payload collectors
are implemented; qualification and protocol follow-ups remain below. Commands and limits are in the
[operations guide](../services/build-operations.md). This plan complements the
[shared build-host plan](cicd-manifest-reuse.md), rather than changing its
execution or transport contracts.

## Implemented boundary

Selected-builder setup now probes physical bytes/inodes across controller,
builder and target filesystems before bootstrap, budgets the complete staged
cohort and builder packaging, observes installed custom role roots and temporary
mounts, and preserves private preflight/failure diagnostics. Known unsubmitted
preparation failures dispose of their own temporary attempts; uncertain source
processes retain evidence.

First bootstrap captures bounded serving configuration, enrolled roots/routes
and existing persistent-directory identities. Final guarded setup rechecks
expected releases, captured route bindings and data-directory identities before
completing that one-time snapshot. Sequential publication still permits downtime;
operators must verify HTTP routes, health and database contents.

Role-aware maintenance now retires proven superseded target bundles while
pinning verified current bundles, schedules builder terminal source/log retention
through existing verified primitives, and supports explicit builder bundle-release
policy. The daily host timer dispatches these roles with a seven-day grace and
configurable CLI budgets. Explicit controller preview/apply collects proven
completed sources, bundles and extraction trees while preserving history proof.
Interrupted payload deletion requires original revisions. Record runway warnings
do not reclaim or erase replay identities.

Remaining work: replay-safe record retirement and long-run qualification beyond
existing record bounds; controller automatic age/budget scheduling; configurable
inode-driven collection policy; full live populated-target conversion and measured
small-disk/tmpfs peaks; richer pre-mutation inspection of arbitrary installed
service/root changes and end-to-end route verification. No automatic runtime
removal or shared writable toolchain design is introduced. The requirements below
remain the full design, not a claim that these qualification boundaries have passed.

## Scope and decisions

Convert an existing source-built target to a separate selected builder while
preserving deployment roots, service identities and persistent data. A one-time
maintenance window is acceptable. Atomic multi-site publication and importing
arbitrary existing Nginx routes are not prerequisites. Partial conversion must
be reported and recoverable; a successful command must verify the complete
intended site set.

Storage policy is host-wide and role-aware, including ordinary hosts, builders,
targets and controllers. It must work without coding-agent tools. Defaults must
be bounded, with explicit per-host overrides and separate budgets for caches,
retained payloads and metadata. A budget is not a disk reservation. Never lower
headroom checks merely to make a small server pass setup.

Interpreter services and runtime secret bindings remain separate deliverables.
Node static builds and supported native Go services are the initial conversion
cases. Installed runtimes are a separate, explicitly requested cleanup class.

## Review findings

| Priority | Current behavior and owning code | Required improvement |
| --- | --- | --- |
| P0 | `lib.build_setup.run_build_setup` checks controller delivery workspace capacity, but its dry run does not establish target physical peak readiness. Target artifact staging in `lib.build_target_artifacts` checks logical reservations; the generic deployment guard runs later. | Observe all three hosts before expensive work and model the aggregate stage-all peak, bytes and inodes by filesystem. Recheck at each allocation boundary. |
| P0 | `TargetReleaseActivator._publish` renders only registered artifact applications. During initial conversion an ordinary sibling on the same domain can lose its route before its activation. | Make the maintenance-window behavior explicit, preserve the complete desired inventory and configuration snapshot, and verify every intended route before declaring success. Failures retain an exact converted/unconverted inventory. |
| P0 | Current bundles are needed by `reconcile_installed`, including refresh with the builder unavailable. There is no general target artifact collector. | Introduce reference-aware target payload retirement that always pins current installed bundles and unresolved operations. |
| P0 | `BuildRetentionStore` already settles terminal charges, prunes source/logs and explicitly releases successful bundles, but has no age/budget scheduler. | Build policy and scheduling around these verified primitives, with explicit authorization for ending artifact retrieval. Do not sweep builder directories. |
| P1 | `build_setup_history` archives successful attempts by rename; sources, bundles and verified extraction trees remain. Preparation can also leave attempts before a setup guard begins. | Add bounded inspection and collection of proven completed payloads; distinguish safely disposable local preparation from uncertain remote attempts. |
| P1 | Payload cleanup preserves input, admission and job identities. Defaults include 128 builder input records, 128 target artifact records and 32 active controller attempts; independent history limits also apply. | Report record runway separately and design replay-safe retirement. Raising limits alone is not a durable storage policy. |
| P1 | `managed_storage` coordinates cache cleanup with setup/deployment. Managed builder execution uses a separate coordinator and worker/payload locks. | Specify and test a compatible lock order for each storage owner; no process-snapshot-only exclusion or cleanup of active scratch. |
| P1 | `run_build_setup` replaces caught failures with a generic recovery-required error. | Retain a bounded, redacted phase and cause/refusal code alongside exact recovery identities and storage observations. |
| P2 | Switching modes retains old toolchains, build homes and uploaded source staging. | Inventory leftovers and remove only proven obsolete managed payloads; runtime removal requires separate reference checks. |

These findings come from repository code and contracts, not an inspection of a
particular server. Existing selected native checks do not qualify populated
source-built target conversion; see the [coverage map](testing-coverage.md).

## Delivery order

### 1. Conversion inspection and capacity admission

Extend the selected-builder preflight with a bounded read-only conversion
report. Include every merged saved destination, pinned revision, generated grant,
installed/proposed root, component/service/data identity, required tools and
unsupported settings. Refuse unexplained identity or root changes. Preserve
existing setup-user, SSH-source, TLS and other saved profile settings; show
explicit changes. Generated manifests must be committed before conversion.

Report physical free bytes/inodes, logical charges, record counts and protected
storage totals separately. On each filesystem include already-retained content,
remaining allocations and reserve. Avoid counting existing files twice. Account
for all staged bundles, verification extraction, staging copies, live releases,
temporary backups, metadata, source preparation and builder scratch; combine
overlapping lifetimes on the same filesystem. Separate `/tmp` and data mounts
must receive their own checks. Source preparation itself needs bounded admission,
not just a check after clone/snapshot creation.

Estimates before building use declared ceilings; later checks use verified
sizes/entry counts where available. Recheck after cleanup and before receiving,
unpacking or copying payloads. Reports remain observations, not guarantees
against external writers, quotas or later ENOSPC. On refusal include phase,
path/mount, available/required bytes and inodes, reclaimable estimate, protected
bytes and remaining shortfall. Keep enough reserve for durable failure metadata.

**Acceptance:** insufficient bytes, inodes, a small tmpfs or logical/record
capacity refuses before builds where observable. Simulated later exhaustion
preserves data and reports the exact boundary without blind retry.

### 2. Guarded one-time conversion

Persist the complete conversion inventory and protected serving-configuration
snapshot before the first conversion mutation. Retain backup references and
verify data backups according to each service's existing backup contract.
Coordinate competing deployment controllers and target maintenance through
existing ownership boundaries; document any new lock graph before adding locks.
Downtime permission does not authorize overwriting unknown services or data.

Keep reserve-all/build/stage-all ordering. Sequential activation may temporarily
remove shared-domain routes during the window. After final profile application,
verify the complete desired route set, service health and data identities before
saving conversion success. Persist per-application old/new release identities
and completed/uncertain boundaries. Do not label delivery success as complete
conversion if final profile or verification failed.

Provide an operator reconciliation procedure using original IDs and observed
remote state, including failures after activation or final-profile success.
Restoring serving configuration alone is not an application/database rollback.
Do not clear guards, replace IDs, switch back to source mode or rerun uncertain
mutations automatically. Preserve a useful redacted cause in the refusal.

**Acceptance:** a populated Node-static/Go target converts with unchanged data
and service identities. Shared-domain sites may be unavailable during the
window, but all return before success. Interrupted and partially completed
conversions remain inspectable without destructive recovery shortcuts.

### 3. Payload cleanup primitives on every storage owner

Use preview/apply reports with exact managed paths, ownership, logical and
allocated bytes, reason, references, rebuild/retrieval cost and skips. Apply
revalidates a revision under ownership. Record deletion intent before unlink,
sync affected directories, and credit reclaimed charges only after completion.
Unknown, altered, incomplete or active evidence remains protected.

| Owner | First eligible payloads | Always protected by the initial collector |
| --- | --- | --- |
| Host cache maintenance | Existing npm content, nvm archives and Go build caches | Configuration, `_npx`, installed runtimes, modules/toolchains and application data |
| Builder | Verified terminal source/log payloads through existing settlement/pruning; successful bundles only under an explicit retrieval-retention policy | Queued/running jobs, active readers, uncertain scratch/uploads/publications and replay metadata |
| Target | Superseded verified bundles after reference checks against installed registry, admissions and activation/refresh state | Every current installed bundle, active/live releases, persistent data, pending or uncertain operations and metadata |
| Controller | Bulky source snapshots, bundles and extracted trees from proven successful completed attempts after policy grace | Active/uncertain attempts, credentials, guards, compact receipts and metadata needed by history/recovery inspection |

Define a controller payload-retired marker and update history validation before
deleting files; original metadata paths must become explicitly historical, never
silently replayable. For unguarded failed preparation, prove no remote mutation
was possible and no live owner remains before treating its exact owned tree as
disposable. Do not infer this from age or an absent process alone.

Retain the current bundle for every installed target application regardless of
budget pressure. Any additional previous bundles are retained inputs, not a
promise of implemented rollback. Builder download success alone does not
authorize bundle release; standalone/webhook consumers also need their retrieval
policy. Cleanup must not require reaching another host to protect local current
or uncertain references.

**Acceptance:** preview changes no payloads; stale revisions, active work and
unknown files skip/refuse. Faults before/after unlink and directory sync preserve
replay and accounting. Current target refresh succeeds with the builder off.
Another deployment successfully rebuilds disposable caches after cleanup.

### 4. Automatic policy, record retirement and long-run qualification

Persist role-aware byte budgets, free-space/inode reserves, minimum retention
ages, payload count limits and per-pass time/I/O bounds. Expose overrides through
normal setup configuration and provision maintenance through shared plugin/timer
composition. Reuse host inventory and cache primitives without requiring agent-VM
packages. Maintain a gap between cleanup trigger and target to avoid thrashing;
select only enough eligible old payloads to restore policy. Never repeatedly
evict caches during setup or run deletion inside admission as an implicit retry.

Start automatic deletion only for classes with qualified primitives. Let
per-host schedules dispatch builder/target cleanup under their existing capacity,
maintenance and reader locks, with bounded nonblocking deferral. Jobs starting
after inspection must remain excluded through apply and its durable receipt.
Keep application services available during routine cleanup. Emit per-owner
success/failure/skip and measured filesystem before/after totals. If protected
content prevents headroom, report the shortfall and require more capacity or a
reviewed policy change.

Separately define compact replay tombstones and safe history/record retirement.
Specify request-ID reuse rejection, generation high-water marks, authorization
scope, idempotent responses and ledger size bounds before reclaiming slots.
Reject retired replays without recreating payloads or running jobs. Compact
records still occupy space: surface their hard limits and refusal behavior.
Ship early warnings while this protocol work remains pending; do not claim
indefinite operation from payload cleanup alone.

**Acceptance:** repeat multi-application deployments beyond today's record and
attempt limits with bounded payload and metadata growth under the new policy.
Test bytes and inodes independently, cleanup/build races, stale plans, crash
boundaries, current-bundle protection, repeated cleanup and quota shortfalls.
Qualify on a disposable small Debian target with populated data, a separate
builder and controller, including a separate small tmpfs. Record measured peaks
and native versus mocked boundaries in the coverage map.

## Deferred runtime removal

After a successful conversion, preview obsolete source staging and build-home
caches first. Runtime removal needs a separate explicit operation checking
running executables, services, project version requirements, nvm aliases and
global packages, including external/unmanaged references. Uncertain references
skip removal. Do not recursively delete old build homes or introduce shared
writable toolchains as a storage shortcut.

## First implementation slice

Implement conversion inventory, capacity reporting and failure diagnostics, then
qualify one populated maintenance-window conversion. Next deliver target bundle
retirement and controller completed-payload collection with preservation tests,
reuse builder retention primitives, and only then enable scheduled payload
cleanup. Record retirement is a separate protocol change required before
claiming sustained bounded operation. Keep this order visible in the operator
guide as features become available; planned cleanup commands must not appear as
already supported.
