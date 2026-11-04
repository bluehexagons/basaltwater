# BW-GL02A: offline private decision and coach records

**Ready; no live desktop or model calls.** Implement the smallest complete offline
record lifecycle from [GL-02](../game-testing-learning.md#gl-02-proposed-version-1-records).
Follow the [handoff index](README.md). This job is the shared contract owner.

## Inputs and scope

Read `desktop/qa_artifacts.py`, `desktop/preflight.py`, `desktop/run_checkpoint.py`,
`desktop/qa_cli.py`, relevant CLI dispatch, atomic I/O/validation helpers and owning
desktop tests.
Reuse private bundles, immutable snapshots, UTC/clock metadata and output-path
guards. Existing run clocks get artifact references, not a replacement ledger.

Own new focused record/validation modules and tests; minimal CLI wiring, suite
registration and operator/contract documentation are included. Do not modify
native input admission, observation expiry, model selection or shared services.

## Required behavior

1. Define version 1 for observation packets, decisions and coach requests/replies/
   dispositions. Specify exact names/types, finite numeric/size/count limits,
   status/null semantics and the relationship between packet/state/frame IDs.
2. Create private immutable snapshots and validate pinned bytes/geometry/source
   regions. New output refuses overwrites and unsafe paths. Keep the original
   request/reply immutable; dispositions are separately linked records.
3. Validate advice against explicit current state revision and deadline without
   contacting a helper. Distinguish invalid/late/stale advice from controller
   acceptance/rejection. A validated packet grants no live input authority.
4. Expose creation/validation through the existing desktop QA/evidence command
   style. Document actual grammar and machine-readable error/partial semantics;
   do not create a root command or pretend the CLI understands game strategy.
5. Publish small synthetic accepted/rejected examples reusable by both projects.
   Include condition fields needed by later corpus work, without implementing
   aggregation, OCR, coaching dispatch or game adapters in this job.

## Acceptance

Exercise valid and explicitly unknown state, unsupported versions, booleans in
numeric fields, NaN/infinity, oversized/deep records, duplicate IDs, unsafe or
changed inputs, mixed frame/geometry, missing files and existing outputs. Verify
state-change/late-reply rejection, separate dispositions and unchanged originals.
Mock all host boundaries. A fixture must fail if it attempts live desktop/model
access. Assert bounds through small fixtures, not large resource allocations.

Run focused owning tests plus affected static/desktop checks, documentation and
offline CLI validation in a disposable install/artifact fixture. Register new tests.
Keep all real captures,
personal paths and operational receipts out of Git.

## Handoff

Return the exact schema/CLI documentation and synthetic fixture paths, test results,
producer commit and consumer migration/adoption notes. Explicitly list deferred
corpus/perception/executor work. GT-GL03A may continue manually until this lands;
consumers must not claim machine validation before adopting the merged version.
