# Game-testing implementation handoffs

**BW-GL02A now has a version-1 offline implementation.** See the
[record contract and CLI](../../workstations/learning-records.md). Native/game
qualification and consuming-project adoption remain separate. The remaining
assignments describe planned capabilities, not running jobs. Read the repository's `AGENTS.md`,
contributor guide, this page and the assigned brief; follow links as needed rather
than loading every work package. The [architecture plan](../game-testing-learning.md)
owns the overall design. The consuming project's
[handoffs](https://github.com/bluehexagons/game-testing/blob/main/common/handoffs/README.md)
use the same GL milestone IDs.

| Assignment | State / prerequisite | Deliverable |
| --- | --- | --- |
| [BW-GL02A: private records](records.md) | Implemented offline; adoption pending | Version-1 packet contract, validators, pinned artifact creation and offline CLI |
| [BW-GL06A: corpus summaries](corpus.md) | Implemented offline; adoption pending | Deduplicated condition-aware outcome summaries and review inputs |
| [BW-GL04A: local perception](perception.md) | Implemented and tested synthetically; real-profile acceptance waits for GT-GL04A data | Read-only recognizer interface, one bounded detector and held-out report |
| [BW-GL05A: conditional execution](execution.md) | After records/perception contracts; live adoption waits for qualification | Effect-verified executor/event wait with partial receipts and fail-stop tests |

No generic model scheduler or game strategy engine is assigned. Coach request/
reply/disposition records belong to BW-GL02A; coach orchestration remains in the
controller workflow. Gamepad remains separately scoped in the
[capability plan](../game-testing.md#bounded-linux-virtual-pad-adapter).

## Dispatch and ownership

Assign one job per worker in an isolated checkout using the active orchestration
route. Record assignment ID, owner, branch/base commit and approved writable paths
privately before edits. A directory named in a brief is a scope boundary, not
permission to change every file in it. Read each target completely before editing.
Preserve unrelated work and the human checkout. Do not launch further workers
unless the assignment explicitly authorizes them.

BW-GL02A owns the first shared schema and CLI registration. Shared
`desktop/qa_cli.py`, `lib/desktop_cli.py`, `run_tests.py`, dependencies and common
documentation have one integration owner; serialize edits or return focused
integration changes. Later workers consume the merged contract and fixtures,
not separate guessed copies. The source paths in briefs are discovery points;
reinspect current callers/tests before choosing exact new module names.

Copy this dispatch text and fill the job ID:

> Work in the assigned Basaltwater checkout. Read AGENTS.md, the contributor guide,
> docs/plans/game-testing-handoffs/README.md and the brief for `<BW-JOB-ID>`.
> Implement only that assignment, including focused tests and documentation.
> Keep live desktop/model calls, helper refresh and other work packages out of
> scope. Return the handoff defined in the index. Do not spawn agents or deploy.

## Shared contract and cross-repository integration

BW-GL02A publishes the canonical versioned contract, machine-readable synthetic
fixtures and actual CLI grammar in this repository. Its design step settles
field names, limits, null/status semantics, artifact layout, mutation rules and
error behavior before consumers integrate. The game project owns adapters,
strategy and placement-to-outcome mapping. Do not introduce game-specific rules
or private game captures into the generic implementation.

Release a compatibility receipt: schema version, producing Basaltwater commit,
fixture digests and supported commands. Consuming agents record those values in
their private manifests. When fields change, version/migrate explicitly; preserve
old evidence. Both repositories retain a pair of tested source commits at adoption.
Do not assume installation or running-helper parity from a Git commit.

## Acceptance and return

Use synthetic fixtures/temporary directories and mocked host operations. Run
focused tests, `git diff --check`, documentation checks and affected fast checks
as directed by the contributor guide. Add new tests to the owning suite. Parser
or packaging changes also need their applicable installed-CLI/artifact checks.
Keep verbose receipts private; report exact commands/results and unavailable checks.

Return job ID, checkout/branch, base/head commits, changed files, contract/API
choices, tests, limitations and companion-project adoption instructions. Include
all owned jobs/resources and confirm no live helper/system changes. Mark source,
offline acceptance and native/game qualification separately. Commit bounded work
on the assigned branch; the integration owner reviews/merges and pushes main.
Do not silently edit the companion repository or expand into the next job.

Missing optional media must not block synthetic implementation. Missing required
upstream contracts blocks only dependent integration; retain a concrete partial
handoff rather than inventing compatible fields or claiming a live pass.
