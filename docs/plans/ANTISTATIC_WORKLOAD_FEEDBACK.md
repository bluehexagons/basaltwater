# Antistatic development workload feedback

Status: reference and scoped handoff, updated 2026-10-12. Recommendations feed
the existing owners below; this document does not open a parallel implementation
queue. Finish the P0 refresh qualification before scheduling additional host
mutation work.

## What the updated environment improved

Antistatic exercises TypeScript, a C/OpenGL addon, sanitizer fixtures,
deterministic game automation, software-rendered captures, and isolated Linux
packaging. Basaltwater's current host/container diagnostics, resource controls,
managed workspaces, maintenance holds, shared desktop, and support snapshots
provide useful foundations. Preserve those owners rather than introducing
another scheduler, cache collector, or browser manager.

Recent fixes qualify approved commands outside the broker's own filesystem
sandbox while retaining broker confinement, a practical one-hour review window,
readable runtime source under private maintenance umasks, source-activation
receipts, and approval-origin preservation. The VM's home partition now uses
its enlarged 64 GiB disk, retaining its start and UUIDs. Reviewed project
archives moved about 4.94 GiB from home to a private `/srv` destination with
verified contents and links; home currently has about 39 GiB available.

The owner has rerun setup. At post-setup inspection, the developer launcher
observed clean source `7405b82`; the managed runtime independently reported a
setup-snapshot receipt for that same revision. Antistatic's selected Node, npm
and native prerequisites
pass its own doctor, and its TypeScript/native build succeeds. T3 readiness and
Xvfb GLX context checks pass. Real project selection-screen captures also run,
which is stronger rendering evidence than context creation alone.

This restores a usable development baseline. The previous broker refresh
failure remains historical recovery evidence in the qualification owner; it
should not be described as the current host's development blocker. Matching
source and setup receipts still do not identify revisions loaded by long-running
services or qualify the disposable failure/recovery matrix. No broker request
was replayed during this development review.

## Post-setup Antistatic experience

- **Native tooling and runtime selection work together.** The project selects
  Node 26.10.0 and npm 12.1.0 through its existing selector. SDL3 3.4.14,
  SDL3-image 3.4.4, FreeType, OpenAL, GLEW, Vorbis and libusb are discoverable.
  Keep project minimums/pins independent of Basaltwater's general tool updates.
- **Headless graphics is usable.** Xvfb exposes OpenGL 4.5 through llvmpipe;
  Antistatic's own before/after menu captures exercise native loading, shaders,
  fonts, assets and PNG output. Software graphics continues to support
  correctness work without supplying physical-GPU timing, HDR or motion review.
- **Capacity diagnostics are useful, with a remaining interpretation issue.**
  Home and root have ample headroom and no host errors were reported. The first
  sample explicitly marked memory ballooning and brief swap-in activity, with
  zero recent memory-pressure averages. T3's historical service peak exceeded
  that sample's smaller guest RAM but remained below configured capacity.
  Contextualize that warning with ballooning, capacity and service lifetime;
  a peak collected before RAM changed is not evidence of current exhaustion.
- **OOM outcomes need explicit diagnostic evidence.** A development probe was
  killed with exit 137 after its captures completed. Its next operation tried
  to construct a full assertion diff for differing PNG buffers. Replacing that
  probe assertion with bounded byte equality allowed the same rendered flow to
  finish; ordinary interactive motion is not an exact same-frame pixel oracle.
  The user-readable T3 cgroup reports one cumulative `oom_kill`, while host
  doctor remains healthy afterwards. No pre-event counter or readable kernel
  journal was available to attribute that counter precisely. Include readable
  OOM event counters and anonymous/file memory in support diagnostics, scoped to
  the service lifetime; use before/after deltas for job attribution and retain
  unknown freshness honestly. Do not infer a game-renderer leak from this probe.
- **Useful caches survived setup.** Existing dependencies and incremental
  native build products remain usable. Retain SDK and compiler caches while
  storage has headroom; no broad cleanup was needed for these development runs.
- **Capture repeatability belongs to the project.** Initial Versus image
  differences came from its documented random cosmetic appearances, not host
  setup. Antistatic adds an explicit fixed-appearance capture control and
  recorded settings for exact comparisons. Basaltwater should continue to
  expose environment evidence while projects own state and pixel oracles.

Detailed logs, snapshots, captures and CPU paint probes remain in Antistatic's
ignored `local-artifacts/projects/2026-10-12-post-setup-development/` workspace.

### Additional targeted maintenance checks

The latest Antistatic pass found and fixed another group-ownership gap in its
agent-play client: a runner could exit before a descendant holding inherited
pipes. Its benchmark/IPC runner fixes and identity-checked temporary disposal
are project responsibilities, not evidence of failed host maintenance.

Review the remaining plain captured `subprocess.run` calls in Basaltwater's
cache/cleanup commands and maintenance-review wrapper for output-memory bounds,
descendant ownership and interruption. Existing `agent_probe`, streamed-process
helpers and systemd control-group execution already provide relevant owners;
do not create another process manager. Keep mocked unit coverage, and add the
leader-exits-first/TERM-resistant descendant case to disposable qualification.

Generic rebuildable-cache and stale-tool-temporary cleanup should revalidate
directory identity/ownership and active use immediately before deletion. The
vendor release collectors already check device/inode identity; extend that
contract where generic cleanup still acts on a previously inventoried pathname.
Cover replaced directories and recreated disposed paths, and preserve useful
project reports/captures rather than extending host age-based retention to them.

## Recommended order and acceptance

### 1. Qualify refresh and recovery on disposable Debian/systemd

Owner: [privilege execution qualification](PRIVILEGE_EXECUTION_QUALIFICATION.md),
with the existing [transactional execution](TRANSACTIONAL_EXECUTION.md) and
[release matrix](../BASALTWATER_RELEASE.md).

Reproduce both fresh snapshot activation and managed-checkout refresh under
`UMask=0077`, including the managed state symlink and saved desktop setup.
Diagnose the retained failed broker job before another broker-driven live
replay. Do not infer its cause from the earlier logout-helper permission failure
or interpret a collected
transient unit as successful maintenance.

Acceptance must cover partial source activation, a failed pre-step and later
step, correct code/metadata permissions, preserved public approval origin,
readable policy versus private authentication/state, service restart, process
loss, and explicit recovery. Reconnect T3 and the shared desktop and run a small
native GL/capture workload afterwards. Retain the original recovery snapshot
until that matrix passes. A dispatched, running, or uncertain request must not
be automatically replayed.

### 2. Make failure and source context useful without privileged output

Owners: the privilege qualification plan and
[CLI host audit](AGENT_CLI_MAINTENANCE_AUDIT_2026-08-09.md).

The correlated diagnostic slice is implemented: operator surfaces now carry
fixed maintenance stage/index/category and validated job-specific setup context.
Keep exception text, root command output, configuration and credentials private.
Qualify mixed old/new reader versions and interrupted jobs on the disposable
target, with unknown fields represented honestly. Stale markers must not explain
a newer failure.

Separately report the invoked CLI source, managed filesystem source, recorded
activation, last successful setup, and running-service context where observed.
Preserve intentional developer launchers during reconciliation. Compare known
revisions only, and distinguish an unavailable service revision from a match.
Receipt-backed observation is delivered; the remaining slice is a compact
comparison and explicit override contract, not another update manager.

### 3. Give durable artifacts an explicit storage contract

Owners: [T3 VM storage lifecycle](T3_CODE_AGENT_VM_IMPROVEMENTS.md) and the
[VM data-disk/mount project](VM_MANAGEMENT_AND_LIGHTWEIGHT_GIT_HOSTING.md).

Add an optional private artifact root to setup and bounded doctor discovery.
Validate ownership, path components, mount identity, available bytes and inodes,
and collisions with public web, credential and temporary roots. Provision only
through the reviewed host mutation path. Report source and destination capacity
separately and verify actual filesystem headroom; moving files between paths on
the same filesystem does not free that filesystem.

Basaltwater should own provisioning and discovery. Each project should own
completion selection, active-work exclusion, integrity verification, publication
of compatibility links, and explicit recovery. Antistatic's verified archive
routine already implements that project boundary. Never delete project evidence
because it is old, and never place private captures in public preview roots.
Test reruns, ownership conflicts, absent mounts and full destinations without
mutating a real host.

### 4. Improve resource interpretation before adding cleanup

Owners: the T3 VM diagnostic baseline and CLI host audit above.

During archival and tests, T3's cgroup memory warning could coexist with healthy
host capacity and idle swap. That cgroup includes agent/build descendants and
file cache. Show available host memory, configured versus ballooned guest
capacity, anonymous versus file-backed usage, pressure, swap activity, limits
and OOM/restart counters where readable. Treat
unavailable fields as unknown. Do not label a high aggregate percentage a leak
without pressure or growth evidence. Validate quiet, file-cache-heavy and
constrained workloads using bounded observations.

For packaging storage, retain the newer container graph-root diagnostics and
resource controls. Report peak build/copy headroom on each involved filesystem;
removing heavily shared SDK image tags is not a reliable way to reclaim bytes.
Do not introduce automatic image pruning from this workload feedback.

### 5. Keep cleanup ownership and maintenance interruption explicit

Owner: [T3 VM lifecycle and maintenance](T3_CODE_AGENT_VM_IMPROVEMENTS.md).

The inspected host maintenance timers were healthy. Antistatic fixed its own
settings-test temporary directory leak and parent cleanup after forced worker
termination. This was project-owned scratch, not evidence of a failed host timer.
Keep vendor cache collection with the vendor and each test's scratch cleanup
with the parent test process. Add narrowly owned cleanup only after observing a
specific leak; retain dry-run and active-work protections.

Qualify the existing maintenance hold and readiness audit with a long native
build, capture session, unpushed commit and disconnected client. Deliberate
refresh should show its disruptive effects clearly and persist the final job
result across broker/T3 restart. The human review expiry and command/job runtime
are different limits; increasing one must not silently increase the other.

## Lower-priority checks

- Confirm whether literal IPv6 approval origins are supported end to end. The
  host validator currently rejects them despite bracket-aware URL formatting;
  define and test the supported contract before broadening validation.
- Investigate test-only SQLite connection ResourceWarnings if they recur in
  combined broker suites. Distinguish delayed fixture cleanup from a demonstrated
  runtime leak before changing production ownership.
- Keep software GL evidence clearly marked. A healthy llvmpipe context and
  deterministic captures validate correctness; physical-GPU timing, HDR output,
  human reconnect and high-refresh motion remain separate qualifications.

## Handoff boundary

No additional Basaltwater feature implementation is scheduled by this record.
The next Basaltwater agent should retain the verified post-setup development
baseline, follow the current privilege qualification owner, and complete the
disposable lifecycle matrix before proposing another live refresh. Resolve
historical recovery evidence through correlated safe status or administrator
evidence; do not replay an uncertain request to obtain it. Antistatic continues
its existing graphics/UI work, and its build and publishing tools remain
independent of Basaltwater.
