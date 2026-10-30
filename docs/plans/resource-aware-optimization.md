# Resource-aware optimization

Status: proposed implementation plan. The initial optimization pass removed
selected repeated parser, package-observation, filesystem-discovery and CI work.
The larger changes below are not implemented or qualified by that pass.

The agreed minimum for basic CLI use, lightweight editing and focused tests is
**1 vCPU and 1 GiB RAM**. Improve that experience while allowing larger machines
to complete independent work faster within explicit resource budgets. Optimize
elapsed time, total compute, peak memory, disk activity and network traffic;
report each separately instead of treating more parallelism as less work.

This plan complements the [shared build-host plan](cicd-manifest-reuse.md).
That plan owns cross-role capacity, draining and durable leases. It remains a
dependency for concurrent heavy jobs across builders, webhooks and runners.
This plan adds measurement, workload budgets and implementation efficiency;
it does not introduce another host or fleet scheduler.

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

The 1 GiB floor is a requirement to qualify, not a completed test result. It
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

Phase 0 must assign numeric budgets for CLI/completion tail latency, editor
interaction latency, smoke/focused-run elapsed time, minimum memory headroom,
scratch/output, and swap/pressure rate and duration. Fix these before candidate
comparisons or defaults, recording the measurement method and unavailable
signals. A failing baseline is a finding to fix, not a reason to relax the floor.
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

## Delivery phases

### 0. Establish workload baselines and acceptance budgets

Owner: benchmark/test tooling, with runtime owners supplying representative cases.

- Map the end-to-end paths: CLI startup and completion; focused/full tests;
  CI environment preparation, checks and coverage; source preparation; runtime
  staging/compression/transfer; build queue/execution/artifact delivery; and
  repeated panel/doctor observations. Include idle services, maintenance timers
  and bursts of activity for each enabled role. Measure parent and descendant costs.
- Build an opt-in, bounded benchmark command that records revision, toolchain,
  effective allocation, storage type, background load, cold/warm cache state,
  elapsed/CPU time, peak workload memory, swap/OOM/pressure, bytes/entries and
  subprocess/scan counts. Include transport round trips, content passes, durable
  state writes and service reload/restart counts where relevant. Report
  unavailable metrics explicitly. Keep raw evidence private and machine-readable;
  publish only sanitized summaries.
  Label RSS versus cgroup-accounted memory, sampled versus kernel-recorded
  peaks, and file-cache inclusion; do not compare unlike memory measurements.
- Reuse existing test selections and timing reports. Use generated small/large
  directory fixtures and trusted representative builds rather than repeatedly
  cloning arbitrary projects or generating huge files. Set a run/time/disk
  budget for the benchmark itself.
- Pin explicit latency, memory, scratch and output budgets for each workload
  on its required fixture after baseline measurement, including the numeric
  basic-development thresholds above. Use one paired run for triage, then
  repeated comparisons for promising changes; report sample count and variance.
  Use enough short-operation samples for tail latency; use medians/ranges for
  a small number of expensive runs. Do not make noisy timing assertions unit tests.

**Exit:** reproducible baselines and accepted per-workload budgets exist for the
basic loop on the 1 GiB fixture and heavier workloads on a larger reference host.
Record baseline failures and prioritize their fixes; phases 1/2 may proceed to
resolve them without claiming the floor is qualified. Separate cold-start/network
variability from steady-state gains. Verify the proposed 4/16/32+ GiB fixtures
during staged qualification rather than running every workload on every tier
at every change.

### 1. Define shared resource policy and preserve a small serial path

Owner: resource observation/policy and existing execution owners.

- Extend observations only for missing signals, including usable memory and
  process/file-descriptor/storage constraints where reliable. Test v1/v2/hybrid
  and missing cgroup data, visible-ancestry limits, affinity/quota disagreement,
  memory ballooning and pressure. Keep unsupported-platform behavior explicit.
- Implement a small deterministic policy that maps observations, configured
  reserves, workload estimates and parent grants to a bounded plan. Serial must
  remain a first-class execution path. Calibrate estimates from phase 0 rather
  than using a fixed bytes-per-core rule for every workload.
- Integrate that policy first with test execution. Reuse build policy and
  systemd/scratch enforcement for build workloads. Aggregate cross-role admission
  belongs to the coordinator in the shared build-host plan, initially one heavy
  slot. Do not enable multiple build claims before its lease, cancellation,
  draining and recovery contracts support them.
- Implement the local test-run ownership boundary before automatic pools.
  Cover separate checkouts, changed temporary roots, shared and isolated
  namespaces, disjoint parent grants, parent death with surviving children,
  stale/reused PIDs, reboot recovery and unavailable/damaged coordination.
  Prove the startup registration and cleanup boundary with a native case;
  two independent observations are not two grants. Verify unsupported-platform
  serial fallback separately from busy/unconfirmed ownership handling.
- Add concise diagnostics showing requested/granted resources and a bounded
  reason for waiting, serial fallback or refusal. Keep operator controls small;
  automatic selection and an explicit serial override are the initial needs.

**Exit:** deterministic mocked boundary tests prevent over-admission within
the owned allocation, unknown capacity never becomes unlimited, nested budgets
stay within the parent grant, and simultaneous managed runs cannot each obtain
the same grant. Serial work remains usable without optional observations or
coordination, and native concurrent-run/parent-death cases confirm the claimed
domain and ownership behavior. Carry unresolved baseline performance failures
into phase 2; final rollout still requires the complete 1 GiB qualification.

### 2. Reduce startup and memory work before increasing concurrency

Owner: CLI, setup transport and source-preparation owners; deliver independent
changes separately against the common phase-0 measurements.

- **Lazy CLI:** start in [`basaltwater.py`](../../basaltwater.py) and command
  registration modules. Separate command metadata/registration from implementation
  imports, then construct only the parser tree needed for execution. Preserve
  top-level/subcommand help, aliases, errors, saved arguments, completion and
  interactive-shell behavior. Prefer explicit command boundaries over a global
  parser cache with mutable defaults. Measure installed-wheel and checkout paths.
- **Bounded output and transport:** profile whole-suite `StringIO` capture and
  in-memory setup archives in [`run_tests.py`](../../run_tests.py) and
  [`lib.setup_common`](../../lib/setup_common.py). Replace large accumulation
  and copying with bounded spools or streaming where useful, including child
  output and cancellation.
  Spools must use the intended disk filesystem, not silently consume RAM-backed
  `/tmp`; include their bytes, permissions and cleanup in the operation budget.
  Preserve archive framing, integrity, size checks and private payload handling.
- **Coordinated storage scans:** consolidate overlapping source-storage monitor
  work in [`lib.build_setup_sources`](../../lib/build_setup_sources.py) behind
  one owner, with explicit sample freshness, latched failure and
  bounded cancellation. Preserve required pre/post-mutation validation. Document
  sampled enforcement and overshoot; a scan result is not a storage reservation.
- **Repeated observations:** identify panel/doctor and setup probes that can
  share one invocation's result or batch transport. Define stale/unavailable
  behavior for display data and re-observe before sensitive mutations. Preserve
  deadlines, failure distinctions and per-host authorization.
- **Idle and setup work:** measure enabled services and timer overlap before
  changing polling or scheduling. Bound collector pools and feature-specific
  dependencies; avoid starting unused browser/build services for basic CLI work.
  Batch package/service probes or independent package transactions only where
  setup ordering permits, invalidating observations after relevant mutations.
  Preserve required post-install verification and failures at each setup boundary.

**Exit:** affected behavioral tests, interruption/cleanup checks and the 1 GiB
benchmarks pass. Gains include lower startup/peak memory or less measured I/O;
there is no new mandatory daemon, dependency or cache for the small-machine path.

### 3. Add resource-aware test parallelism and reduce CI preparation

Owner: test runner and CI configuration. Depend on phases 0/1; phase 2 can
proceed independently where it does not change the same execution boundary.
Start with [`run_tests.py`](../../run_tests.py), the [Makefile](../../Makefile),
the [Debian wrapper](../../scripts/run_debian_ci.py) and existing
[branch](../../.github/workflows/tests.yml)/[release](../../.github/workflows/release.yml) workflows.

- Start opt-in module-level worker processes on one runner. Preserve class and
  module fixture lifetimes; threads cannot isolate process-global mocks. Review
  ports, paths, environment, timers and child processes; mark genuinely exclusive
  modules explicitly. Bound worker queues, output and parent/worker memory.
- Inventory selected test identities, assign every test exactly once and merge
  worker results and coverage. Discovery/import failures, duplicate/missing IDs,
  worker crashes or cleanup failures must fail the run. Every skip remains
  visible. Cancel owned descendants and confirm cleanup before releasing capacity.
- Use measured module duration/memory for bounded balancing. Prevent nested
  worker explosions and preserve a deterministic serial diagnostic mode. Report
  suite elapsed time separately from summed test time and worker CPU cost.
- Measure CI image pulls, APT/Python setup, wheel checks and coverage separately.
  Trial a prepared Debian tool/dependency image or bounded dependency-download
  caches, keyed by OS/architecture/interpreter and dependency inputs. Install
  and validate the current project; do not reuse mutable virtualenvs across
  incompatible runs or turn a cache hit into a skipped artifact check.
- Define image refresh, storage cap, ownership and cold/disabled fallback before
  enabling cache reuse. Retain bounded registry fallback and the current rule
  that failed installs/checks are not replayed. Amortize image maintenance and
  cache uploads when measuring compute, network and storage savings.
- Keep one authoritative full covered branch run and existing release-version
  gates. Node compatibility remains targeted to real JavaScript execution.
  Build/install/smoke the release artifact as required; remove repeated work only
  after tracing the exact artifact and distinct contract it verifies.

**Exit:** serial and parallel selections have equivalent test identities,
outcomes and branch-coverage obligations. Constrained hosts remain serial;
larger hosts improve measured feedback time within their aggregate budgets.
Cold/miss/corrupt cache behavior still passes authoritative checks. Document
both elapsed-time gains and any increase in compute or storage cost.

### 4. Reuse immutable setup/build inputs and qualify host throughput

Owner: setup source/transport, cache and shared build-host owners. Depend on the
identity and budget contracts above; multi-job execution also depends on the
shared coordinator's completed single-slot qualification.

- Trial a controller-side immutable runtime-code bundle cache to avoid repeated
  staging/compression across setups. Identify actual content, modes, schema and
  toolchain inputs, including dirty-tree changes and deletions; a Git commit
  alone is insufficient. Validate atomic publication, concurrent readers,
  corruption, bounded size and cold/disabled operation. Send all per-invocation
  arguments and credentials freshly through their existing private boundary.
- Inventory existing pinned-source/snapshot and toolchain reuse before extending
  it. Keep immutable inputs distinct from job-writable outputs. Reusing build
  results requires a complete input/environment/runtime identity and current
  authority; defer general build-result caching until this can be proved.
- Improve parallel commands within one admitted build only where toolchains
  support bounded jobs. Include compiler/package-manager children, temporary
  storage, logs and artifacts in the parent budget; avoid host-wide CPU defaults.
- After the shared coordinator owns all entry points, consider multiple heavy
  slots as a separate migration. Reserve aggregate resources atomically, define
  fair bounded waiting and prevent starvation, preserve validated nested leases,
  and release only after children and cleanup are confirmed stopped. Qualify
  policy revocation, drain, cancellation, restart and uncertain outcomes first.

**Exit:** repeated ordinary setup and the qualified selected-builder flows use
the correct sources and private payloads after cache hits/misses and reruns.
Demonstrate reduced repeated work without unbounded disk growth or weaker
integrity checks. More host throughput never weakens lease/recovery ownership.

## Additional routes to rank from measurements

These are candidates, not prerequisites for the first batch. Rank them by
measured end-to-end cost, small-machine benefit, implementation/recovery risk
and maintenance burden. Prefer removing passes or writes before introducing
another cache, protocol or state store. Give each selected route its own bounded
prototype and acceptance evidence within the owning phase.

- **Avoid unnecessary setup writes and restarts (phase 2).**
  [`lib.unit_transaction`](../../lib/unit_transaction.py) stages/verifies units,
  rewrites them and reloads/restarts services on ordinary reconciliation.
  Compare a healthy same-configuration rerun with drift and repair cases.
  Evaluate operation-specific no-op detection using all relevant configuration,
  ownership/modes, drop-ins, external inputs and callback effects. Equal unit
  text alone is insufficient. Keep fresh health/authority checks, required
  repair, explicit restart behavior and transaction/recovery evidence. Measure
  writes, reloads/restarts, elapsed time and service interruption.
- **Remove extra artifact content passes (phase 2/4).**
  [`lib.build_artifacts`](../../lib/build_artifacts.py) copies exports into a
  private snapshot, reads that snapshot for per-file hashes, then reads it again
  to emit the bundle. Trial collecting bounded inventory/hashes during the
  validated snapshot copy. Preserve source-change/link defenses, exact file and
  bundle digests, ordering and atomic publication. The descriptor precedes
  payload bytes, so a single streaming pass is not available for free; retain
  the existing format unless measured gains justify a separate migration.
- **Tune compression within bounded transport (phase 2/4).**
  Setup archives in [`lib.setup_common`](../../lib/setup_common.py) use gzip.
  Measure compression levels and chunk/spool sizing across constrained CPU,
  slow/fast links and target decompression, including small and large payloads.
  Choose by total CPU, memory and delivery time, not archive size alone.
  Preserve receiver framing, extraction limits and private-payload separation;
  a new codec or threaded compressor needs separate compatibility and budget
  evidence.
- **Reduce state and observation amplification (phase 2).**
  [`lib.build_jobs`](../../lib/build_jobs.py) loads/validates a bounded JSON
  ledger; mutations can validate/encode it again. Similar serialization costs
  exist in [`lib.agent_memory`](../../lib/agent_memory.py). Measure bytes,
  CPU and lock duration as retained history grows; first remove redundant
  encoding within one validated transaction. In
  [`lib.build_delivery`](../../lib/build_delivery.py), also measure repeated
  status transport and journal writes. Trial read-only batching or a bounded
  status wait only if it pays for its protocol/compatibility cost. Preserve
  current credential/host checks, deadlines and recovery writes; do not blindly
  enable SSH connection sharing, reuse stale authority, or coalesce/retry
  mutations. Indexes or a transactional-store migration require a separate
  measured growth case and recovery plan, without automatic history deletion.
- **Reduce source-preparation I/O (phase 4).**
  Instrument Git commands, tree/blob/history bytes and bundle verification in
  [`lib.build_source`](../../lib/build_source.py). Compare large histories with
  large worktrees before choosing snapshot or pinned-source reuse. Tie any reuse
  to exact content/revision, tags and relevant conversion state. Preserve
  pre/post-mutation cleanliness, bundle identity and checksum checks; repeated
  validation around a mutable boundary is not redundant merely because it
  invokes the same command.

## Integrated qualification and rollout

Use the disposable test Proxmox server for actual 1 GiB and larger guest
allocations, with CPU/RAM/disk limits recorded privately. A limit imposed only
on the benchmark process does not establish whole-guest usability. Exercise
both no-swap and pressure cases; swap availability must not hide a failing
basic-development floor. Do not put machine access details in repository docs.

| Boundary | Required evidence |
| --- | --- |
| Constrained development | Prepared offline checkout and installed wheel: the pinned basic-development workload on a real 1 vCPU / 1 GiB guest, including empty/disabled application caches and missing optional telemetry/coordination; meet numeric latency, memory and pressure budgets. |
| Scaled execution | Serial/automatic/explicit limits on larger fixtures; uneven modules, nested workers/toolchains, simultaneous admissions across checkouts/domains, concurrent CLI use, slow I/O and dynamic pressure; no over-admission within the owned allocation. Local waiting ends with bounded admission or busy; fairness/starvation qualification belongs to the shared coordinator. |
| Cache/stream correctness | Cold/warm/disabled/corrupt caches; changed/deleted/mode-changed sources; concurrent publication; full disk, interrupted transfer and bounded spools; fresh arguments/credentials and exact source identities. |
| Resource enforcement | Missing/partial/stale observations, quota changes, exhausted process/descriptor limits, cancellations, OOM/worker/parent death, surviving children, PID reuse and reboot; preserve conservative serial fallback where supported, and block conflicting managed admissions with retained recovery evidence after uncertain cleanup. |
| CI and supported paths | Full tests/coverage and artifact checks, supported Python/Node gates, affected Debian/CachyOS paths and selected native setup/build qualification. Keep mocked and native evidence distinct in the [coverage map](testing-coverage.md). |

Ship focused, reversible batches. Start with opt-in behavior, compare it with
the retained serial/cold path, then choose conservative defaults from measured
results. A disabled optimization must restore the ordinary bounded path; it
must not automatically rerun a failed or uncertain mutating operation. Use
focused checks during editing and one broad validation at integration points,
avoiding repeated full-suite runs on unchanged source.

Accept a structural optimization only when representative gains exceed observed
noise and justify its operational complexity. Meet every applicable phase-0 budget,
preserve behavioral coverage, and investigate any constrained-path latency or
memory regression beyond measurement variation before enabling it by default.
Choose the smallest worker count near the measured throughput plateau. If a
larger pool only shifts cost into memory, I/O, cache storage or queue latency,
keep the smaller pool. Preserve exact operation IDs and recovery evidence when
stopping or reverting deployment-related changes.

## Deferred policy and scope changes

- Affected-test selection may run in shadow/supplemental mode after dependency
  mapping, but replacing authoritative PR/branch checks needs a separate policy
  decision backed by measured missed-failure/fallback evidence. Ambiguous shared
  configuration, plugins, test tooling and generated assets select the full suite.
- Coverage sampling, automatic deletion of retained build evidence, untrusted
  shared build caches, distributed/fleet scheduling and broader build-platform
  support are separate proposals. They are not implied by higher-end hardware.
- Agent model, context and delegation costs remain part of engineering cost:
  use bounded read-only audits and integrate each finding once. Record failed
  handoffs and review/retry costs instead of assuming a smaller model always
  reduces total work.

## First implementation batch

Deliver phase 0's bounded benchmark harness and hardware fixtures, the phase-1
budget decision contract with mocked boundaries, and a measured serial baseline
on the 1 GiB guest, recording any initial failures. Then prototype lazy CLI
imports and a two-process test runner independently. The worker prototype needs
the ownership/recovery boundary before shared use; startup/I/O fixes can proceed
without waiting for all scheduling infrastructure. Enable neither by default
until its acceptance gates pass.
Use those results to choose the next cache/stream/monitor package, with one
owner per coupled boundary. This keeps the large pass evidence-driven while
establishing the resource contract needed by all later work.
