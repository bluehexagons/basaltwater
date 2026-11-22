# Resource-aware optimization delivery plan

[Topic index](resource-aware-optimization.md)

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
  [basic-development thresholds](resource-aware-optimization-design.md#basic-development-qualification-workload).
  Use one paired run for triage, then
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

An implemented smaller step uses native `make -j2 check-ci`: independent
static/artifact checks overlap with one serial covered suite. The wrapper
defaults to one job and allows an explicit two-job choice; hosted full/release
jobs select two. This does not implement module workers, automatic resource
selection or a shared coordination domain. The outer runner/operator owns the
allocation and concurrent invocations. See the contributor guide and the
[measured follow-up](resource-aware-optimization-follow-up.md#larger-allocation-follow-up).
Retain the phase-1 requirements for managed pools.

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

## Next bounded deliverables

Use the existing harness and serial measurements before choosing another
structural change. Further CLI savings require separating command registration
from runtime modules; the remaining import costs alone do not justify duplicating
registration or adding lazy-module machinery. Artifact hashing already occurs
during the validated copy; retain the [bundle contract](../agents/contributing/build-contracts.md)
unless another measured content pass remains.

Collect CI phase timings across representative runs before preparing an image
or cache. The first hosted overlap run spent 8 seconds on APT installation in
the full job but 65 seconds in the Node-only job, while Python dependencies took
5–6 seconds. Keep registry/APT variability distinct from suite cost before
introducing a maintained image or dependency cache. A two-process test prototype
is a separate opt-in experiment: first
establish the phase-1 budget and descendant-ownership boundary, then compare
identical tests/outcomes and total compute against serial execution. Do not
enable pools merely because a machine reports spare CPUs. Runtime compression,
no-op service reconciliation and source reuse need their own representative
measurements and recovery acceptance before adding state or changing mutations.
