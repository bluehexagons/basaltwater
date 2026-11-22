# Test suite maintenance

Status: implemented. Routine assertion review continues under the acceptance
rules below.

## Decisions from the review

Keep `make check-fast` as the short local edit loop. Retain full offline PR
coverage: one uninstrumented baseline is insufficient evidence for a safe
changed-path selector. Shared configuration, generated code, validators and
plugin composition have cross-domain dependencies.

Add nightly and merge-queue coverage without weakening the existing PR gate.
A schedule defined on a feature branch does not establish an active nightly
replacement: GitHub schedules start after the workflow reaches the default
branch. Branch protection remains administrator-owned.

Prune only assertions with named retained behavioral coverage. File size,
regression age, similar assertion text, coverage overlap and suite membership
overlap are not sufficient reasons to delete tests. The five largest modules
were reviewed; their size did not justify splitting related fixtures or dropping
distinct security, interruption and recovery inputs.

## Inventory and measurements

Run these from the repository's isolated development environment:

```bash
python3 scripts/test_inventory.py
python3 scripts/test_inventory.py --json
python3 run_tests.py --list-suites
python3 run_tests.py --durations 30 --timings-json /tmp/test-timings.json
```

The inventory validates every named suite selector, domain pattern and discovered
module's domain ownership without importing test modules. JSON includes all
memberships, line/definition counts and candidate process/sleep sites. Its
attribute-call scan is a heuristic: aliases, bare imported functions, dynamic
calls and polling-loop structure still need manual review. It does not establish
I/O, expense, flakiness or redundancy.

Recompute structural counts instead of enforcing dated snapshots. Definitions
include nested/helper functions; runtime loading can generate cases, so compare
definitions and attempted cases only at the same revision.

The initial default run passed **10,173 attempted cases, including 26 skips**,
with **160.465 seconds inside test cases**. Repeating the accumulated top modules
on the same development runner measured install-script fixtures at 8.421 seconds,
Headscale controller fixtures at 5.934 seconds, and Guacamole at 3.001 seconds.
The first two exercise real CLI/cancellation/composition contracts and remain in
routine offline coverage.

The bounded transport experiment targeted Guacamole's real two-second watcher
wait. Acceptance required unchanged connection/EOF/idle/input-release contracts
and at least one second saved, with one implementation variant and a ten-minute
experiment budget. Replacing fixed sleeps/polling with events and controlling only
the owning module's watcher reduced the module to 1.042 seconds; the lifecycle
case went from 2.057 to 0.068 seconds. Console forwarding also waits for processing
instead of assuming a 30 ms delay. The actual loopback WebSockets remain in use.
These are same-runner observations, not a suite-wide speedup or flakiness claim.

Per-case duration includes `setUp`, `tearDown` and cleanups. Suite elapsed time
also includes class/module fixtures and runner overhead; discovery/import time
is excluded. The JSON records both measures, counts, success and every case
duration, including on test failure, without captured logs, skip reasons or
tracebacks. Keep raw host-specific measurements outside Git. Coverage
instrumentation and the lab machine are separate workloads; compare unchanged
selections on the same runner before setting duration budgets.

## Execution policy

| Tier | Trigger | Checks |
| --- | --- | --- |
| Fast local | Every edit | `make check-fast` defaults to six smoke modules plus compile/docs/package metadata; an explicit suite/test replaces smoke. Run all affected domains while editing. |
| PR / merge queue | Every PR and merge-group request | All static, brand and wheel checks; one full branch-covered suite on Node 24; Node 26 compatibility suite; latest-platform wheel qualification. |
| Full baseline | Nightly at 05:23 UTC, protected-branch pushes and manual dispatch | The same full validation and Node compatibility jobs, with result summaries and timing evidence. |
| Release | Release tags and runtime/platform qualification | Full checks at both supported Python endpoints, wheel/install checks, and separately selected browser/native/live qualification. |

The **Tests** Actions workflow preserves its existing check names. Its Debian
jobs have a 25-minute deadline; the wrapper bounds the container run to 15
minutes and each registry pull to two minutes. Only registry pulls fall back:
failed setup/check commands are not replayed. Event-specific concurrency keeps
pushes from cancelling scheduled or PR runs. Job summaries publish result counts
and suite overhead, logs rank modules/tests, and JSON artifacts are retained
for 14 days.

Maintainers own failures through the **Tests** workflow and normal GitHub
notifications: inspect the failing phase/contract, reproduce with native commands,
fix or revert, and rerun before merging. A nightly success does not override a
failed PR. Verify the first scheduled run after merging to the default branch.

A future reduction of PR coverage needs repeated hosted timing evidence, an
active visible replacement, explicit path-to-domain ownership with full fallback,
and an agreed blocking merge policy before disabling the existing gate.
Unknown paths and shared/generated/runtime boundaries must select the full suite.

`live_proxmox` is the only advertised expensive category and the only production
decorator consumer. Unused `slow`/`network` listings were removed; custom category
decorators/environment flags still work. The `network` domain remains offline
policy/protocol coverage. Ordinary tests can use bounded local processes and
loopback transports. Hosted CI receives no personal infrastructure credentials.
`make browser-test` and real-host/destructive workflows stay separate and gated.

## Review register

All domains' module ownership and selectors were checked. All 17 cross-domain
memberships are legitimate and retained; combined selections still deduplicate
test IDs. This is a selection review, not a claim that every assertion in every
domain has been audited.

| Suite | Review outcome / retained boundary |
| --- | --- |
| `agent` | Reviewed the large tooling/T3 modules. Fixed the symlink preservation fixture to inspect the actual redirected `auth.json`. Retain provider, credential, failure and rollback cases. |
| `core` | Runner tests now detect stale explicit suites and empty globs as well as unowned modules. Structured timing and registry-fallback failure contracts have focused tests. |
| `deployment` | Reviewed all build-retention cases: distinct durable states/permissions/faults use small payloads and controlled clocks/events. Retain them and the real installer lifecycle checks. |
| `security` | Lab qualification exposed ambient `SSH_CONNECTION` leaking into generic firewall fixtures. Set their connection context explicitly; dedicated allowed/excluded/invalid peer tests retain the guard contract. Keep distinct hostile inputs, permission and credential boundaries. |
| `web` | Reviewed the large panel/Gogs modules and Guacamole transport fixtures. Gogs caller tests retain supplied/generated password redaction with explicit placeholder checks; removed a helper-only assertion against a password it never received. |
| `integration` | Removed deleted `tests.test_cicd`; the eight remaining wiring modules stay selected and checked. |
| `node` | Keep all seven modules executing generated JavaScript on both supported Node majors. |
| `smoke` | Keep the six-module default unchanged. |

A follow-up audit covered 18 configuration/CLI, target-setup and native/runtime
modules and their owning production paths. Retain distinct serialization,
credential, registry ordering, refusal, retention, enrollment, transport/cache,
locking, diagnostic, ownership, rollback, token lifecycle and recovery cases.
Gogs variants retain interacting exception types, prior publication states and
interrupted links.

The optional sibling-checkout manifest check was replaced by stronger assertions
in `TestLoadManifest.test_loads_from_disk`: it loads a temporary repository and
checks component names and the templated domain. Placeholder parsing remains in
`TestRejects.test_domain_placeholder_accepted`. The shipped manifests retain
`BuildSetupPlanTests.test_documented_projects_plan_with_their_complete_enrollment_templates`,
which validates all four checked-in examples through delivery planning and
target authorization. This removes an implicit dependency on another workspace
while keeping reproducible application behavior in routine coverage.

## Acceptance and verification

Before deleting or consolidating a test:

1. Name the production behavior and observable contract.
2. Name retained tests that invoke application code for every distinct failure
   input and security boundary; similar assertions or line coverage alone are
   insufficient.
3. Verify selection membership and inspect affected branch paths.
4. Run the affected module/domain. Broad cleanup or runner changes require one
   `make check-ci`, rather than separately repeating full tests and coverage.
5. Review the final diff and record retained coverage in the commit description.

Initial implementation verification through `564ddccb` completed on 2026-11-16:

| Check | Result |
| --- | --- |
| Local `make -j2 check-ci` at `349f1afc` | 10,181 attempted cases, 26 skips; 82% branch-aware coverage. Static, documentation, compilation and wheel checks passed. |
| SSH firewall fixture isolation | All 100 access-filter/security-step cases passed with an injected local SSH context and in a real lab SSH session. |
| Full lab check, `make -j2 check-ci` | 10,181 attempted cases, 23 skips; 82% branch-aware coverage. All changed implementation/test file hashes matched the local source. |
| Lab Node 26 compatibility | All 79 cases passed, without skips. |
| [Hosted manual dispatch](https://github.com/bluehexagons/basaltwater/actions/runs/43917682189) at `349f1afc` | Full Debian validation, Node 26 compatibility and latest-platform packaging passed. Both timing artifacts downloaded and validated. |
| Inventory, workflow lint and final independent review | All selectors/patterns current; actionlint passed; no unresolved material review findings. |

The lab's first full run exposed the SSH environment dependency described above;
the corrected full run passed. Its failed-run timing report also retained counts
and per-case measurements as intended. The disposable fixture was removed, and
pre-existing guest configurations and process identities were unchanged. Private
raw operational evidence remains outside this repository.

The follow-up manifest cleanup passed all 106 cases in `test_project_manifest`
and `test_build_setup_plan` locally and in a fresh disposable lab fixture, without
skips. The lab's test-source hash matched the final local file. Documentation
checks and diff review passed. That fixture was also removed, with pre-existing
guest configurations and process identities verified unchanged.
