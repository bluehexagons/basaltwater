# Resource-aware optimization scope and design

[Topic index](resource-aware-optimization.md)

## Scope and hardware expectations

Capacity tiers below are qualification fixtures, not hard-coded runtime modes
or new platform-support promises. Use effective limits, workload needs and
operator policy to choose execution behavior.

| Fixture | Required behavior |
| --- | --- |
| 1 vCPU / 1 GiB RAM | CLI/help/completion, lightweight editing and representative focused tests work without OOM or sustained swap thrashing. Default to serial work and bounded output. A lightweight editor is included; a full desktop, local T3 service and browser/build workloads need separate qualification. |
| 2 vCPU / 4 GiB RAM | Exercise the normal development loop, full serial checks where they fit, and small worker pools only when measured headroom permits. |
| 8 vCPU / 16 GiB RAM | Demonstrate useful test and build-command parallelism without multiplying nested pools or exceeding shared budgets. |
| 16+ vCPU / 32+ GiB RAM | Exercise scheduling overhead, cache contention and the shared coordinator's queue fairness when implemented; throughput should scale only while CPU, memory and storage permit. |
| Large host with a small container/VM allocation | Honor inherited quotas, affinity and actual available memory rather than physical host specifications. Include fractional CPU quotas, ballooned memory, slow storage and unavailable observations. |

The pinned basic-development workload has selected native Debian qualification;
see the [coverage map](testing-coverage.md) for its scope and gaps. This
does not make every repository build or browser workload fit. Keep existing
recommendations for T3 Code with browser/build workloads distinct. A constrained
machine should still offer useful CLI and test selections, explain why heavier
work cannot start, and support the existing selected-builder path where qualified.
Do not require swap, a warm cache, Docker, a GPU, internet access or a remote
builder for the basic offline test loop. Network-dependent operations and their
prerequisites remain explicit. Full serial validation remains available when its
measured resource requirements fit; do not skip tests silently to fit a tier.

Start native capacity qualification on supported Debian x86-64 guests. Cover
CachyOS development paths separately. WSL, native Windows and additional build
architectures keep their existing support/qualification boundaries; a portable
resource-policy fallback does not qualify a new build platform.

### Basic-development qualification workload

Prepare the supported OS, Bash, nano, Git, Python and an isolated environment
with the project's development/completion extras before measuring the offline
loop. Record their versions and the exact revision. First-time OS/dependency
installation and downloads are separate workloads; application caches may be
empty or disabled. Qualify CLI behavior through both a checkout and its installed
wheel; run the focused tests from the checkout and label that evidence separately.

The required small-machine selection is version, root/setup/network help and
installed Bash completion; the runner's existing `smoke` selection; and separate
focused runs of `tests.test_validation`, `tests.test_network_setup` and
`tests.test_atomic_io`. Edit, search and save a roughly 10 KiB text file in nano
while one focused run executes. Keep this selection pinned in the benchmark
manifest; report its test identities and any skips rather than substituting
cheaper tests. It needs no browser, desktop, local T3 service or remote builder.

Use these initial basic-development qualification budgets, fixed before the
candidate comparison. They are native qualification thresholds, not timing
assertions in ordinary CI:

| Signal | Budget and method |
| --- | --- |
| CLI and installed Bash completion | p95 at most 2 seconds, at least 20 samples per operation |
| Nano search/edit/save interactions | Each measured interaction at most 1 second; verify the exact saved text while a focused test runs |
| Smoke / individual focused runs | At most 15 / 5 seconds per run; three samples and explicit test identities/skips |
| Guest memory headroom | At least 256 MiB `MemAvailable`, sampled every 50 ms; distinguish this from child RSS |
| Swap / OOM | No swap-in/out or OOM-kill counter increase; no-swap guest required |
| Memory pressure | Full-stall rate above 10% must not persist for more than 2 seconds, measured from sampled PSI total deltas |
| Qualification output and scratch growth | At most 64 MiB; bounded capture plus sampled filesystem usage, excluding prepared dependencies/checkouts |

Record measurement limits and unavailable signals. A failing baseline is a
finding to fix, not a reason to relax the floor.
Full-suite, build, browser and initial-install budgets belong to their separately
qualified fixtures; a workload that does not fit 1 GiB is recorded as such without
making it part of this basic-development requirement.

## Common design rules

### Observe once, admit explicitly, enforce at the existing owner

Reuse [`lib.agent_resources`](../../lib/agent_resources.py) for affinity,
visible inherited cgroup limits, memory accounting and pressure observations.
Its CPU upper bound is explicitly not a concurrency recommendation. Keep
observation, policy calculation, reservations and process enforcement separate.
Do not import the large CLI implementation into low-level resource helpers.

Define a small workload-budget contract before adding worker flags. It should
carry the granted CPU share, memory/headroom, scratch and retained-output bounds,
process/file-descriptor allowances, concurrency cap and deadlines, with their
scope, freshness and provenance. Reuse existing build request limits instead of
creating a second wire format for them. Add only the missing local-development
policy and translation needed at each boundary.

Derive admission from the tightest applicable operator/job ceiling, effective
CPU limits, usable memory after reserves and existing commitments, and storage
and process headroom. Avoid counting the same memory usage and reservation
twice. Capacity can change between observation and launch; hard limits and
reservation ownership still matter. Host-visible RAM, configured guest capacity,
current balloon allocation and workload limits are different facts.

Unknown, stale or incomplete observations must remain visible. Use conservative
serial execution for basic local work when feasible; defer or reject a heavy
job when its safety cannot be established. If one worker cannot fit, report that
condition rather than forcing a minimum worker count. A fractional CPU share
may run one throttled worker when the other budgets permit it.

Initially select concurrency at admission and adjust only between tasks.
Pressure should stop new admissions or lower future concurrency, with bounded
backoff; it should not trigger uncontrolled oscillation, automatic process kills
or replay of work with an uncertain outcome. Explicit cancellation and existing
hard-limit enforcement retain their current behavior.

An outer runner/build allocation must bound inner test workers, package-manager
tasks and compiler/toolchain jobs together. Pass validated budget information
through the owning process boundary. Environment variables may communicate
performance hints; they must never confer authorization or bypass a host lease.
Expose the chosen limits and reasons, plus validated overrides that can request
less concurrency. Requests for more cannot exceed administrator or parent grants.

For cooperating local test invocations, begin with one admitted run per shared
coordination domain across checkouts. Define that domain by the user, validated
allocation/namespace scope and a common owner-controlled state root independent
of repository path and temporary-directory settings. An accessible lock path
alone does not prove visibility across containers or mount namespaces. Explicitly
disjoint, enforced parent allocations may use independent domains and run at
the same time. Scope aggregate guarantees to cooperating processes within the
visible domain; unrelated users/processes still need headroom and administrator
enforcement. Local waiting is bounded, then returns an explicit busy/retry result;
an advisory lock provides no FIFO or starvation guarantee.

Reuse the safe file/locking patterns in [`lib.concurrency`](../../lib/concurrency.py),
but a lock released on parent death is insufficient ownership for a worker pool.
Serialize admission against a private durable ownership record, registered
before workers can execute. Record the grant and verified process-containment
identity, including boot and process-start identity rather than a bare PID.
After a runner dies, inspect or stop only verified owned descendants and reclaim
only after confirmed cleanup, or a verified reboot in the same kernel scope.
An absent parent PID is not evidence that children stopped. Preserve uncertain
records with an inspectable recovery action and block conflicting admissions.
If a platform cannot support verified containment/recovery, leave managed pools
unavailable there rather than claiming equivalent ownership from a file lock.

Basic serial tests must remain usable through a built-in conservative policy
without Linux telemetry, a daemon or a manually supplied budget. When available,
they cooperate with admission and obey parent limits. Absence of optional
coordination permits this serial path with an explicit lack of aggregate
guarantees; it must not bypass a known live or unresolved claim in an established
domain. Distinguish unsupported coordination from a busy or damaged domain.
Unknown capacity never enables automatic pools or unbounded output/storage.

### Give cached work a defined lifetime

Inventory existing Git, package/toolchain, source-snapshot, runtime-bundle and
panel-observation caches before adding another. Each cache needs an owner,
complete identity, trust scope, byte/entry bound, invalidation and cleanup policy,
and a usable disabled/cold path. Reuse a cache contract where appropriate without
merging stores with different authorities or retention guarantees.

Pure derived outputs may be reused by content identity. Observations needed for
authorization, target identity, resource admission or activation must be checked
again at their existing mutation boundary. Keep mutable namespaces, credentials,
per-run arguments and operation receipts out of reusable runtime bundles.
Preserve explicit retention of recovery evidence; cache pressure must not turn
into deletion of retained inputs, releases or replay history.
