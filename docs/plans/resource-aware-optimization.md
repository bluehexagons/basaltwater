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
| 16+ vCPU / 32+ GiB RAM | Exercise scheduling overhead, queue fairness and cache contention; throughput should scale only while CPU, memory and storage permit. |
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

For cooperating local test invocations, begin with one admitted run per user
and machine across checkouts, using a bounded-wait ownership lock and existing
process-cleanup patterns rather than a queue service. Reuse the safe locking
patterns in [`lib.concurrency`](../../lib/concurrency.py); define a common lock
scope that does not change with repository path or temporary-directory settings.
Retain ownership until worker/child cleanup is confirmed, with an inspectable
blocking outcome after uncertain cleanup. A CI/build parent may instead grant
explicit disjoint allocations. If shared ownership cannot be established,
disable automatic parallel pools; allow basic serial work only within an explicit
budget or report the missing admission capability. Scope aggregate guarantees to
cooperating work within the granted allocation; unrelated processes and other
users still require current headroom checks and administrator enforcement.

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
  subprocess/scan counts. Report unavailable metrics explicitly. Keep raw
  evidence private and machine-readable; publish only sanitized summaries.
  Label RSS versus cgroup-accounted memory, sampled versus kernel-recorded
  peaks, and file-cache inclusion; do not compare unlike memory measurements.
- Reuse existing test selections and timing reports. Use generated small/large
  directory fixtures and trusted representative builds rather than repeatedly
  cloning arbitrary projects or generating huge files. Set a run/time/disk
  budget for the benchmark itself.
- Pin explicit latency, memory, scratch and output budgets for each required
  workload after baseline measurement. Use one paired run for triage, then
  repeated comparisons for promising changes; report sample count and variance.
  Use enough short-operation samples for tail latency; use medians/ranges for
  a small number of expensive runs. Do not make noisy timing assertions unit tests.

**Exit:** reproducible baseline and accepted per-workload budgets exist on the
1 GiB fixture and a larger reference host. Separate cold-start/network variability
from steady-state gains. Verify the proposed 4/16/32+ GiB fixtures during staged
qualification rather than running every workload on every tier at every change.

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
  Cover simultaneous admissions from separate checkouts, parent death,
  surviving children and unavailable coordination. Prove the explicit scope of
  reservations instead of treating two independent observations as two grants.
- Add concise diagnostics showing requested/granted resources and a bounded
  reason for waiting, serial fallback or refusal. Keep operator controls small;
  automatic selection and an explicit serial override are the initial needs.

**Exit:** deterministic mocked boundary tests prevent over-admission within
the owned allocation, unknown capacity never becomes unlimited, nested budgets
stay within the parent grant, and simultaneous local runs cannot each obtain
the same grant. The basic 1 GiB loop works with automatic and explicit serial
execution; a native concurrent-run case confirms the ownership behavior.

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
  [`lib.setup_common`](../../lib/setup_common.py). Replace large accumulation/copying with bounded
  spools or streaming where useful, including child output and cancellation.
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

## Integrated qualification and rollout

Use the disposable test Proxmox server for actual 1 GiB and larger guest
allocations, with CPU/RAM/disk limits recorded privately. A limit imposed only
on the benchmark process does not establish whole-guest usability. Exercise
both no-swap and pressure cases; swap availability must not hide a failing
basic-development floor. Do not put machine access details in repository docs.

| Boundary | Required evidence |
| --- | --- |
| Constrained development | Clean checkout and installed wheel: help/completion, lightweight editing, smoke and representative focused suites on a real 1 vCPU / 1 GiB guest; record aggregate peaks, available headroom and responsiveness. |
| Scaled execution | Serial/automatic/explicit limits on larger fixtures; uneven modules, nested workers/toolchains, simultaneous local admissions across checkouts, concurrent CLI use, slow I/O and dynamic pressure; no over-admission within the owned allocation or starvation. |
| Cache/stream correctness | Cold/warm/disabled/corrupt caches; changed/deleted/mode-changed sources; concurrent publication; full disk, interrupted transfer and bounded spools; fresh arguments/credentials and exact source identities. |
| Resource enforcement | Missing/partial/stale observations, quota changes, exhausted process/descriptor limits, cancellations, OOM/worker death and surviving children; fail or defer with retained recovery evidence. |
| CI and supported paths | Full tests/coverage and artifact checks, supported Python/Node gates, affected Debian/CachyOS paths and selected native setup/build qualification. Keep mocked and native evidence distinct in the [coverage map](testing-coverage.md). |

Ship focused, reversible batches. Start with opt-in behavior, compare it with
the retained serial/cold path, then choose conservative defaults from measured
results. A disabled optimization must restore the ordinary bounded path; it
must not automatically rerun a failed or uncertain mutating operation. Use
focused checks during editing and one broad validation at integration points,
avoiding repeated full-suite runs on unchanged source.

Accept a structural optimization only when representative gains exceed observed
noise and justify its operational complexity. Meet every phase-0 absolute budget,
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
on the 1 GiB guest. Then prototype lazy CLI imports and a two-process test runner
independently. Enable neither by default until its acceptance gates pass.
Use those results to choose the next cache/stream/monitor package, with one
owner per coupled boundary. This keeps the large pass evidence-driven while
establishing the resource contract needed by all later work.
