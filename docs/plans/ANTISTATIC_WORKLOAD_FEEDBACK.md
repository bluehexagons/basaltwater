# Antistatic development workload feedback

Status: reference and scoped handoff, updated 2026-10-11. Recommendations feed
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

Source activation is not full setup qualification. The latest approved refresh
dispatched successfully, activated commit `39aa8cf`, then reported setup failure
with exit code 1. The running broker's response still omits the newer diagnostic
fields, so the failing stage is unknown from that response. The approval page
continues to return its HTTPS authentication challenge, and Xvfb can create an
OpenGL 4.5 llvmpipe context. These observations permit continued game development;
they do not establish successful setup, loaded-service revisions, hardware GPU
performance, or shared-desktop reconnect health.

## Recommended order and acceptance

### 1. Qualify refresh and recovery on disposable Debian/systemd

Owner: [privilege execution qualification](PRIVILEGE_EXECUTION_QUALIFICATION.md),
with the existing [transactional execution](TRANSACTIONAL_EXECUTION.md) and
[release matrix](../BASALTWATER_RELEASE.md).

Reproduce both fresh snapshot activation and managed-checkout refresh under
`UMask=0077`, including the managed state symlink and saved desktop setup.
Diagnose the latest failure before another live replay. Do not infer its cause
from the earlier logout-helper permission failure or interpret a collected
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

Carry the implemented fixed maintenance stage, command index and failure category
through the operator surfaces. Correlate a validated setup recovery marker with
the specific maintenance job before reporting its phase or step; stale markers
must not explain a newer failure. Keep exception text, root command output,
configuration and credentials private. Test mixed old/new reader versions and
interrupted jobs, with unknown fields represented honestly.

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
file cache. Show available host memory, anonymous versus file-backed usage,
pressure, swap activity, limits and OOM/restart counters where readable. Treat
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
The next Basaltwater agent should start with the current privilege qualification
status, gather safe diagnostics for the failed replay, and complete the disposable
lifecycle matrix before proposing another live refresh. Antistatic work resumes
on its existing graphics plan; its build and publishing tools remain independent
of Basaltwater.
