# Resource-aware optimization follow-up

[Topic index](resource-aware-optimization.md)

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

## Larger-allocation follow-up

The follow-up used a shared Debian development allocation with six affinity
CPUs and an inherited 4.34 GiB memory ceiling (2.90 GiB soft high threshold).
This is a reference workload measurement, not qualification of the proposed
8/16/32 GiB fixtures or of full checks on the 1 GiB basic-development fixture.
OS caches and CPU frequency were uncontrolled; raw logs and samples stay private.

| Workload | Before | After | Evidence |
| --- | --- | --- | --- |
| Repository shell-safety scan | 2.61 s median; 307 AST parses | 1.84 s median; 144 parses | Three samples per variant; normalized Unicode keywords still detected, comments/string literals excluded |
| Artifact descriptor validation, 13,481 entries / 640 exports | 0.774 s median | 0.232 s median | Three samples per variant; identical canonical output; indexed membership avoids the entry/export cross-product |
| Full checks, first same-source pair | 186.4 s serial | 180.1 s with two Make jobs | 8,799 tests / six local skips; exactly equal executed lines and branch arcs |
| Full checks, combined protocol changes | 185.8 s serial | 182.8 s with two Make jobs | 8,802 tests / six local skips; full covered suite remains a single execution |

The modest elapsed-time improvement from Make overlap is separate from total
compute. It does not establish a portable speedup or a reason to add more jobs.
Both parallel runs stayed below the inherited soft memory threshold with no
increase in its pressure/OOM counters, sampled every 250 ms. Those observations
include other work in that cgroup and are not exclusive job or whole-guest peaks.
Wheel snapshots exclude coverage databases and SQLite side files while retaining
`.coveragerc`. A native Make graph fixture confirmed actual artifact/coverage
overlap, exactly-once execution and failure propagation from either branch.

Protocol regressions retain Unicode byte-limit enforcement, root and nested
exports, required directory ancestry and rejection of prefix siblings. A seeded
2,000-case comparison against the previous public parser matched 441 accepted
and 1,559 rejected inventories, in addition to focused behavioral tests.
Request hashing removes one duplicate serialization; interleaved CPU timings
varied with host frequency, so no broad runtime speedup is claimed for that change.
Queue claiming selects the minimum persisted sequence without sorting all queued
records, and sequence validation constructs its uniqueness set once. Fresh
ledger validation, cancellation checks and durable ownership remain in place.
