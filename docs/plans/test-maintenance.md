# Test suite maintenance plan

Status: audit and recommendations; no test cases have been removed or changed.
Inventory reviewed: 2026-11-16 on `main`.

This plan aims to keep regression protection for observable contracts while
making the ordinary edit/PR loop short. Long-running, live-environment and
high-cost qualification should run at deliberate checkpoints rather than on
every small change. Treat every proposed deletion as unapproved until its
assertions, failure inputs, callers and coverage impact have been reviewed.

## Current inventory

Static inventory from `tests/test_*.py` (including nested Python tests):

| Measure | Current observation |
| --- | ---: |
| Python test modules | 501 |
| `test*` methods/functions | 10,133 |
| Test source lines | 193,625 |
| Largest modules | `test_t3code_steps.py` (2,449 lines), `test_web_panel.py` (2,212), `test_agent_tools.py` (2,018), `test_build_retention.py` (1,897), `test_gogs_steps.py` (1,788) |

Counts are structural, not a measure of useful coverage or runtime. They include
different assertion types and may count helper-like `test*` functions. No test
suite was run for this inventory, so this plan makes no runtime or flakiness
claims.

The runner exposes `smoke`, `integration`, `node`, ten domain suites, and
`all`. Seventeen modules appear in multiple domain suites and are deduplicated
by test ID when selections are combined. Review their suite ownership, but
treat this selection overlap as distinct from duplicate test cases.

Current execution boundaries:

- `make check-fast` runs static checks and the six-module smoke selection by
  default; an explicit suite or test replaces smoke.
- Branch CI runs the complete branch-covered suite on pushes and pull requests,
  plus a Node 26 compatibility selection. Release CI runs full checks at both
  supported Python endpoints.
- `make browser-test` is a separate local, intensive Chromium/Firefox check and
  is not a CI dependency.
- `run_tests.py` declares `live_proxmox`, `network`, and `slow` expensive
  categories. The live Proxmox module uses its gate. Static search found no
  current test consumers of the `slow` or `network` decorators/categories, so
  these gates may be stale or the search should be confirmed during the first
  inventory pass.
- The repo already has strong pruning guidance in
  [the contributor guide](../agents/contributing/README.md#maintaining-tests):
  preserve distinct failure inputs and observable contracts; remove settled
  presence/identity/catalog checks only when behavioral coverage exists; avoid
  Cartesian products; use small fixtures and controlled clocks/events.

## Recommended test cadence

Keep a short, useful gate on every edit and pull request, and reserve the
complete, slower qualification for merge/release checkpoints:

| Tier | When | Suggested contents | Decision |
| --- | --- | --- | --- |
| Fast local | Every edit | `make check-fast` default smoke; run the affected domain suite or exact test while editing | Keep as the normal inner loop. Add a documented duration budget only after measuring representative machines. |
| Pull request | Every PR | Compile/docs/package metadata, smoke, affected domain suites, and small cross-domain selections when shared boundaries change; retain Node compatibility checks for JS-touching changes | Rework CI so every PR does not pay for the full coverage suite. Use explicit path/domain mapping plus a safe fallback for unknown paths; shared config, validators, runner, plugin composition and test infrastructure trigger broad coverage. |
| Full regression | Scheduled (nightly or equivalent) and before merge to protected `main` when a high-risk area changes | One complete branch-coverage run, ranked module/test timings, static checks and artifact checks | Move routine full coverage out of every PR only after the scheduled job is required/visible and failures have an owner. Preserve a blocking full run at critical checkpoints. |
| Release/platform qualification | Release candidates and platform/runtime changes | Full checks at supported Python endpoints, Node matrix as applicable, wheel/install checks, browser qualification and separately approved native/live workflows | Keep hardware, real-host and destructive testing explicit, gated, disposable and owned. Do not make ordinary CI depend on personal infrastructure. |

The exact PR suite policy is a follow-up decision after measurement. In
particular, a changed-file-only test selector is not sufficient: shared code
and generated/runtime boundaries need a broad fallback. Do not drop full CI
coverage until a scheduled replacement is active and its result is visible to
maintainers.

## Audit and change backlog

Work in small batches. Record each decision (retain, consolidate, split,
reclassify, or remove), the contract still covered, the reason, and the checks
run. Add results below as changes land; keep detailed raw timings outside the
repository if they contain host-specific data.

| ID | Recommendation | Scope and review criteria | Priority / state |
| --- | --- | --- | --- |
| TM-01 | Establish a timing baseline | Run the full suite once with `--durations 30`, preserve total/module/test time and skipped counts, and repeat the top accumulated modules on a stable runner. Separate fixture/setup overhead from test-body time. Do not compare timings from different hosts as if they were equivalent. | High / not started |
| TM-02 | Inventory expensive and flaky behavior | Search all test modules for real sleeps, polling loops, repeated subprocess launches, browser/Node startup, external calls and broad integration fixtures. Confirm each is mocked, event/timer controlled, bounded, or intentionally qualified. Convert fixed sleeps to events/fake clocks where behavior permits. Use real transport only where lifecycle is the contract. | High / not started |
| TM-03 | Revisit execution tiers | Inspect `tests/expensive_support.py`, all gate call sites and the CI wrapper. Classify long unit tests separately from live network/host qualification. Apply the existing `slow` gate only to tests whose duration is not useful in the normal edit/PR loop; retain deterministic, fast failure-path tests in domain CI. Remove unused category machinery only after confirming no external command contract depends on it. | High / not started |
| TM-04 | Consolidate redundant assertions | Across every domain suite, compare same-contract tests for settled feature-presence assertions, import/re-export identity, catalog snapshots, duplicated parser/config expectations and repeated setup fixtures. Remove only when an owning behavioral test calls production code and retains each distinct failure input, permission boundary and externally observable result. Prefer `subTest` for truly shared contracts. | High / not started |
| TM-05 | Reduce combinatorial variants | For option sweeps, keep each independent option's boundary cases and a small set of interaction cases. Avoid Cartesian products unless options interact. For order/dependency tests, use minimal catalogs/fixtures rather than the whole built-in catalog. For space/time tests, lower fixture limits and advance controlled timestamps instead of creating data or waiting. | High / not started |
| TM-06 | Review oversized modules by behavioral seam | Start with the five largest modules listed above, then modules above roughly 1,000 lines. Split only when cases have distinct owners/contracts; keep related variants together and reuse immutable fixtures/parsers. A split is organization, not a test reduction, and must not alter selection coverage. | Medium / not started |
| TM-07 | Simplify suite ownership and selection | Review `TEST_SUITE_PATTERNS`, the default discovered-module membership assertion, and 17 cross-domain overlaps. Keep legitimate multi-domain memberships; make the owning domain clear and eliminate only accidental pattern duplication or stale selectors. Verify combining suites still runs each test ID once. | Medium / not started |
| TM-08 | Make the runtime policy real | Confirm whether any current network or generally slow tests need gates. If so, use categorized decorators and prerequisite checks; otherwise delete unused categories from runner docs/tests rather than implying they currently protect long tests. Keep live Proxmox gated and never let routine CI access a real host. | Medium / not started |
| TM-09 | Separate coverage cadence in CI | Update `.github/workflows/tests.yml` and `scripts/run_debian_ci.py` after the timing and tier review: keep fast/high-value checks on PRs, schedule complete coverage, retain a blocking full run for critical merges/releases, and preserve Node-version coverage for generated JavaScript. Document ownership, trigger, timeout and failure visibility. | High / not started |
| TM-10 | Reassess tests with no maintained behavior | Identify tests that assert implementation trivia or settled historical regressions. For each, trace the current production path and replacement behavioral coverage. Remove only proven obsolete checks; regression age by itself is not a deletion reason. | Ongoing / not started |

### Suite-by-suite review register

Review every suite in its owning domain. This is a coverage boundary map, not a
recommendation to delete whole suites. Cross-listed modules should be recorded
once under their primary owner with secondary suite references.

| Suite | Modules | Review emphasis |
| --- | ---: | --- |
| `agent` | 122 | Process/CLI seams, generated hooks, browser automation fixtures, pairing/readiness boundaries, repeated service setup. Keep provider/network behavior offline unless explicitly qualified. |
| `core` | 54 | Parser/config/validation contract duplication, utility boundary tests, runner self-tests, snapshots and catalog-style assertions. Preserve invalid-input and serialization distinctions. |
| `desktop` | 39 | Native desktop lifecycle versus mocked orchestration; event-driven waits; fixture reuse across desktop/session/output cases. Keep native/browser qualification out of routine CI. |
| `deployment` | 81 | Setup composition and rollback/failure propagation; install/upgrade subprocess fixture cost; overlap with integration and web coverage. Keep distinct recovery states. |
| `network` | 45 | Distinguish offline policy/protocol unit tests from real DNS/download/SSH/network qualification. Verify whether the declared network gate is used or needed. |
| `proxmox` | 41 | Keep fast mocked inventory/plan/state contracts in normal checks; retain live guest round-trips behind explicit gate and critical qualification window. |
| `security` | 17 | Preserve denial, boundary, credential, permission and audit failure cases. Consolidate only identical contracts, not distinct hostile inputs. |
| `services` | 32 | Split deterministic policy checks from real service/process integration; reuse controlled service fixtures and avoid repeated installer setup. |
| `storage` | 27 | Keep exact threshold/boundary and failure/rollback cases; lower mocked limits rather than generating large datasets; separate disk/device stress from routine tests. |
| `web` | 60 | Separate generated HTML/config behavior from browser lifecycle; preserve escaping/access/activation assertions; reuse common render fixtures. |
| `integration` | 9 | Check whether cross-boundary tests add unique wiring/failure propagation beyond domain suites; keep representative end-to-end composition cases. |
| `node` | 7 | Preserve actual generated-JavaScript behavior on each supported Node major; avoid duplicate Python harness assertions when the Node behavior check already proves the contract. |
| `smoke` | 6 | Keep small, stable high-value checks; review additions for runtime and breadth so smoke remains a quick default. |

## Acceptance rules for pruning

Before removing or merging a test:

1. Name the production behavior and observable contract it checks.
2. Find the retained test(s) that still invoke the application code for that
   contract, including every distinct failure input and security boundary.
3. Check selection membership and branch coverage for removed execution paths;
   overlap in suite membership or similar assertion text is not proof of
   redundancy.
4. Run the affected module/domain selection. For a broad cleanup or moved
   execution paths, run `make check-ci` once rather than separately repeating
   full test and coverage commands.
5. Review the final diff and record retained coverage in the commit description.

For CI cadence changes, demonstrate the full job remains scheduled, visible and
blocking at the agreed critical checkpoints before removing it from ordinary
PR runs. Keep `make browser-test` and destructive/native qualification separate
unless project owners explicitly choose a different policy.

## Decision log

| Date | Change | Evidence and retained coverage |
| --- | --- | --- |
| 2026-11-16 | Created inventory and recommendations; no test cases or CI behavior changed. | Static inventory: 501 modules, 10,133 `test*` definitions, 193,625 test-source lines. Runtime baseline and assertion-level redundancy review remain open. |
