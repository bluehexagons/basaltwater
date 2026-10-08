# Test suite audit and coverage plan (2026-09-02)

Status: audit complete and improvement plan finalized; the first two
implementation slices were implemented and verified. At the 2026-09-02 audit
baseline the suite had 176 tracked test modules, 3,294 class-based test
methods, and 3,283 tests in the default runner. These counts are historical,
not current suite totals. One live Proxmox test was intentionally skipped
unless explicitly enabled.

## Review method

Every tracked `tests/test_*.py` module, including `tests/service_tools/`, was
reviewed through unittest discovery, source/import inspection, AST scans for
assertions and duplicate bodies, and a check for stale test-to-production
references. The baseline command was:

```bash
python3 run_tests.py
```

The review looked for tests that had become disconnected from current code,
tests that could pass without proving behavior, exact duplicate test bodies,
and groups whose boundaries had become unclear as the repository grew.

## Findings and decisions

### No obsolete tests identified in the tracked test suite

All discovered test modules import successfully and pass in the default suite.
The legacy deployment tests remain useful: `deploy.deploy_steps` and
`DeploymentOrchestrator` are still active compatibility paths, and the tests
protect the supported static/Node behavior and the explicit Ruby refusal.

The only skipped default test is the destructive/live Proxmox lifecycle test.
It remains valuable as an opt-in integration check and is correctly isolated
behind the `live_proxmox` category.

Four exact duplicate-body groups were found, but none is safe to remove:

| Pair or group | Why both remain |
| --- | --- |
| Modern `auto_restart` setting / legacy `no_restart` mapping | Current configuration and backwards-compatible configuration are separate contracts. |
| Routine privileged audit event / fail2ban unban event | Both verify the no-notification policy for different event sources. |
| Full Godot release update / bundle-only update | The updater receives different update results and both paths must notify. |
| SSH hardening skips when `sshd` is absent, the drop-in directory is unwritable, or the drop-in file is unwritable | These are separate refusal branches and protect different environmental failures, even though each verifies that no reload is attempted. |

An AST scan found 119 class-based test methods without a direct `assert*` call.
Most are intentional acceptance, syntax, idempotency, or side-effect tests.
The progress fallback test was the one weak exception: it claimed to verify
fallback to `print` but only checked that no exception was raised. It now
captures and asserts the actual output.

The audit also removed eleven unused imports from test modules. No test was
removed because no candidate met the project’s removal bar: deleting a test
must remove no distinct branch, compatibility rule, safety boundary, failure
path, or externally visible behavior.

## Grouping cleanup

The test files remain at their current paths to avoid a high-noise physical
move and fragile import/path churn. `run_tests.py` now provides pattern-driven
domain suites:

| Suite | Focus |
| --- | --- |
| `core` | Configuration, validation, state, logging, progress, and shared helpers |
| `agent` | Agent setup, credentials, workspaces, browser automation, and T3 Code |
| `network` | Network setup, DNS/mDNS, Cloudflare, SSH, and APT sources |
| `security` | Firewall, SSH hardening, Samba, shell safety, and access policy |
| `storage` | Storage, swap, sync/scrub, disk, and storage service tools |
| `proxmox` | Proxmox host, guest, VM, placement, and provisioning behavior |
| `web` | Gogs, Nginx, TLS, web panel, internal web, and Antistatic behavior |
| `desktop` | Desktop, XRDP, browser, Godot, firmware, and tool bundles |
| `deployment` | Setup, CI/CD, deployment, bootstrap, release, and packaging paths |
| `services` | Maintenance and service-tool entry points |

These suites intentionally overlap at cross-domain boundaries. The runner now
de-duplicates test cases when multiple suites or selectors overlap, so commands
such as the following are safe and predictable:

```bash
./run_tests.py --suite agent
./run_tests.py --suite storage --suite proxmox
./run_tests.py --suite deployment --suite web
```

The patterns are resolved from the repository at runner startup, which keeps a
new test with an established domain prefix from silently missing its domain
suite. The existing `smoke`, `proxmox`, `security`, and `integration` suites
remain available as targeted workflows; `all` and the default invocation still
use unittest discovery.

## Coverage review

The original audit used a one-off standard-library `trace` run because the
optional `coverage` package was not installed in that environment. That result
is an approximate prioritization signal, not a branch-coverage gate: it
measures the default suite only, counts a statement as covered when its source
line executed, and does not include the opt-in live Proxmox test.

The run covered 29,723 of approximately 43,365 statement lines (68.5%) across
204 tracked non-test Python files. 189 files executed at least one statement;
15 did not. The largest gaps are concentrated in a small number of user-facing
and privileged workflows rather than in the validation code that has the most
test methods:

| Area | Approximate coverage | Evidence and meaningful next test |
| --- | ---: | --- |
| `lib/recall.py` | 125/132 statements (94.7%; 40 branches; 90% branch-aware) | The first slice covers stored-config success, empty/invalid data, SSH timeout/missing command, reconstruction, and extra-feature rendering. Remaining branches are concentrated in reconstruction fallbacks. |
| `lib/mount_utils.py` | 95/112 statements (84.8%; 34 branches; 83% branch-aware) | The first slice covers `/mnt` ancestry, SMB probing and cleanup, findmnt failure, accessibility, callbacks, and multi-path results. Remaining branches are lower-level mount/accessibility exceptions. |
| `web/service_tools/webhook_manager.py` | 177/179 statements (98.9%; 58 branches; 97% branch-aware) | The second slice covers config absence and malformed data, list/add/remove/test commands, duplicate and missing paths, script validation, secret/log/status commands, and CLI privilege/dispatch behavior. |
| `web/service_tools/setup_cloudflare_tunnel.py` | 195/388 statements (50.3%; 140 branches; 50% branch-aware) | The second slice covers state-shape validation, origin validation, atomic config replacement/rollback, existing-config validation, service activation outcomes, UFW installation, no-site refreshes, no-op refreshes, and update error conversion. Interactive authentication and tunnel creation remain lower-priority workflow coverage. |
| `web/service_tools/deploy_admin.py` | 150/171 statements (87.7%; 58 branches; 80% branch-aware) | The first slice covers regular-file and size checks, atomic install/remove, symlink handling, ownership refusal, rollback, and privileged command construction. Remaining branches are owner/mode and privileged entry-point edges. |
| `lib/remote_deploy.py` | 167/181 statements (92.3%; 42 branches; 91% branch-aware) | The first slice covers target loading, rsync/SCP/SSH success, failure, timeout, temporary-file cleanup, and safe-path rejection. Remaining branches are concentrated in lower-level command-construction transitions. |
| `lib/user_rename.py` and `common/service_tools/infra_web.py` | 348/995 statements (35.0%; 436 branches; 32% branch-aware) and 534/1,219 statements (43.8%; 502 branches; 38% branch-aware) | These are large, security-sensitive workflows. Add decision tables for rollback, idempotency, ownership, and failure transitions rather than shallow tests for every helper. |

The eight lazy sysadmin handlers (`sysadmin_fan`, `sysadmin_health`,
`sysadmin_keys`, `sysadmin_mount`, `sysadmin_reachable`, `sysadmin_ssh`,
`sysadmin_transfer`, and `sysadmin_upgrade`) did not execute at all in the
trace run. There are parser tests for the user-rename command, but no general
parser/dispatcher contract test for the sysadmin command family. This is the
highest-value addition: verify each `_sysadmin_cmd` dispatches the parsed
arguments correctly, then test handler success, failure, and credential
fallbacks with `subprocess.run`, `os.execvp`, and concurrency mocked.

Two other untraced modules deserve a code-ownership decision before new tests
are written. `lib/service_manager.py` and `lib/concurrent_sync_scrub.py` have
no repository consumers beyond their own definitions and no focused tests.
Confirm whether they are supported public paths; if not, remove the abandoned
implementations, and if so, wire them into the supported path and add tests.
The `deploy/steps.py` and `sync/steps.py` files are thin compatibility facades
and should instead get a cheap import/registration smoke test if they remain
part of the plugin contract. The untraced `scripts/check_cli_docs.py` and
`scripts/check_package_metadata.py` are directly exercised by `make check`;
`scripts/update_cloud_images.py` remains a lower-priority untested command
path.

Coverage should be improved as a risk-weighted branch matrix, not by chasing a
percentage through import-only tests. For command and system-mutating code,
the useful cases are validation refusal, dry-run/idempotent behavior, success,
external-command failure, timeout, and cleanup/rollback. All external commands
must stay mocked and filesystem cases should use temporary directories.

## Safe simplification opportunities

No additional test deletion is justified. The four duplicate-body groups
recorded above each protect a distinct compatibility, event-source, update
result, or environmental-failure contract. The no-direct-`assert*` signal is
also not, by itself, test slop: most of those tests are valid “must not raise”
validator cases, syntax checks, idempotency checks, or side-effect tests.
Non-validator side-effect tests should continue to assert a command, file, log,
or return value explicitly.

The low-risk cleanup path is to reduce scaffolding while retaining test
identity and behavior coverage:

- Use `subTest` tables for closely related scalar validation cases in
  `test_validators.py` and `test_validation.py`, while keeping separate tests
  where the error message or security rule is materially different.
- Extract repeated mock setup/builders from broad service-tool tests when the
  setup is identical. This makes new failure-path tests cheaper without
  hiding which contract failed.
- Split large files only at existing behavior-class boundaries when a feature
  change provides a natural owner. Current candidates are
  `test_validation.py` (167 methods), `test_proxmox_manage.py` (107),
  `test_storage.py` (104), `test_agent_tools.py` (70),
  `test_antistatic_steps.py` (70), and `test_project_manifest.py` (66).
  The classes already provide useful grouping, so a mechanical move would add
  churn without improving coverage.

## Finalized improvement plan

The plan is deliberately risk-weighted. A coverage percentage is useful for
finding blind spots, but the completion criterion is a tested behavior matrix
for code that validates inputs, invokes remote commands, changes files, or
performs rollback.

| Phase | Scope and owner | Deliverable and exit criterion |
| --- | --- | --- |
| 1. Establish coverage and ownership gates | Test infrastructure and module owners | Add a repeatable CI/local coverage command, preferably with `coverage.py` as a development-only dependency, using branch measurement. Until that is available, retain the documented `trace` plus AST measurement. Publish per-file and branch-oriented results without enforcing a global threshold on the current baseline. At this gate, decide whether `lib/service_manager.py` and `lib/concurrent_sync_scrub.py` are supported before writing tests for them. |
| 2. Close command-line blind spots | Sysadmin/CLI maintainers | Add parser and dispatcher contract tests for every sysadmin command, then focused mocked tests for the eight untraced handlers. Cover credential fallback, success, refusal, subprocess failure, timeout, and `os.execvp`/parallel-output behavior without opening real SSH or rsync connections. |
| 3. Cover remote and filesystem boundaries | Deployment/storage maintainers | Add `tests/test_recall.py` and `tests/test_mount_utils.py`; expand `test_deploy_admin.py` and add direct `remote_deploy` cases. Use temporary directories and mocked subprocesses to cover atomic writes, modes/ownership, symlink and path rejection, mount ancestry, SMB checks, cleanup, timeout, and rollback. |
| 4. Exercise service-tool failure matrices | Web/security maintainers | Implemented in the second slice: webhook-manager and Cloudflare-tunnel tests cover missing state, malformed input, duplicate/remove paths, validation failures, atomic rollback, and command/service failures. Preserve the existing security-monitor and notification cases because their event sources differ. |
| 5. Harden large workflows | Workflow owners | Add decision tables for `user_rename`, internal web forwarding, Proxmox helpers, Godot updates, swap/scrub, and network transitions. Prioritize failure transitions, idempotency, ownership, and rollback; do not add import-only tests just to raise the number. |
| 6. Simplify and regroup during feature work | Test maintainers | Convert only same-contract scalar cases to `subTest` tables, extract repeated mock builders, and split large modules at existing behavior-class boundaries. Keep the pattern-driven domain suites as the primary grouping mechanism and avoid mechanical file moves. |

The original first implementation slice covered phases 1–3. A slice is complete when
the new tests pass through the default runner and the relevant domain suites,
all external commands remain mocked, and each listed failure/success branch has
an assertion on its return value or externally visible effect. Phases 4–6 can
follow the ownership and feature-change boundaries of the affected modules.

### Handoff contract for the first slice

Implement the first slice in these boundaries:

1. Coverage plumbing: add the development-only dependency or documented
   fallback, a local `make coverage`-style command, and CI reporting. Do not
   fail CI on the current global percentage. Resolve the two unowned modules at
   this point; either remove them as abandoned or assign them a supported path
   and tests.
2. Sysadmin command surface: add parser/dispatcher tests in
   `tests/test_sysadmin_cli.py` and focused handler tests grouped under the
   sysadmin domain. Include nested `key`/`ssh-key` commands and the eight
   currently untraced handlers. Use mocks for SSH, rsync, subprocess, exec, and
   concurrency.
3. Remote/filesystem boundaries: add `tests/test_recall.py` and
   `tests/test_mount_utils.py`, then expand `tests/test_deploy_admin.py` and
   add direct remote-deployment tests. Use temporary directories and assert
   return values, commands, permissions, state, cleanup, or rollback effects.

The first slice is ready to merge only when its coverage report is reproducible,
its new tests run through both the default runner and the relevant domain
suites, and no test opens a real network connection or mutates the local
machine. Named maintainers should be assigned to the role owners in the phase
table before implementation begins.

## First-slice implementation update

The first test implementation adds the coverage plumbing and the phase 2–3
tests described above:

- `pyproject.toml` now exposes `coverage.py` through the development-only
  `dev` extra. `make coverage` runs the default suite with branch measurement,
  omits test/build artifacts from the report, and deliberately has no global
  fail-under threshold yet. CI installs the optional reporter and reports its
  output as a normal check after `make check`.
- `tests/test_sysadmin_cli.py` covers registration, nested `key`/`ssh-key`
  parsing, dispatcher argument mapping, missing command errors, and unknown
  dispatches. `tests/test_sysadmin_handlers.py` covers the eight previously
  untraced handler modules with mocked SSH, rsync, subprocess, `execvp`, and
  concurrency boundaries.
- `tests/test_recall.py`, `tests/test_mount_utils.py`, the expanded
  `tests/test_deploy_admin.py`, and `tests/test_remote_deploy.py` cover the
  stored/reconstructed recall paths, mount ancestry and SMB checks, temporary
  file and symlink rollback, and direct deployment command success/failure,
  timeout, cleanup, and path-validation behavior. Filesystem tests use
  temporary directories only.
- The new modules are included in the `core`, `storage`, and `deployment`
  pattern suites in `run_tests.py`, preserving the existing overlap and
  de-duplication behavior.

One parser usability gap remains explicitly recorded: because `fan` currently
uses a greedy `hosts` positional followed by a remainder command, the documented
multi-host form with `-- command` is not reliably separated by `argparse`.
The tests cover registration and dispatch independently without blessing that
ambiguous parse. A future CLI change should add a dedicated delimiter/parser
contract test before changing the syntax.

The two unowned modules, `lib/service_manager.py` and
`lib/concurrent_sync_scrub.py`, remain an ownership decision for their module
maintainers; this slice intentionally adds no tests for them and does not
delete potentially recoverable code.

The first-slice coverage run now provides the repeatable baseline for future
comparisons: `coverage.py` reports 43,455 production statements, 16,856
branches, and 68% total branch-aware coverage for the default suite. The report
is intentionally informational while ownership and risk-weighted priorities
settle; changed-file and domain-suite trends are the next useful gates.

For a local report, install the optional tooling with
`python3 -m pip install -e '.[dev]'` and run `make coverage`.

## Second-slice implementation update

The second test implementation addresses phase 4 of the plan without changing
production behavior:

- Added `tests/service_tools/test_webhook_manager.py` with focused coverage for
  configuration storage, repository CRUD, script-state reporting, secret
  handling, journal commands, health reporting, and command dispatch. Related
  scalar outcomes use `subTest` cases; each externally visible result remains
  asserted.
- Expanded `tests/test_cloudflare_tunnel.py` around malformed state shapes,
  unsupported origins, atomic config validation and preservation, existing
  config validation, service start/activation transitions, UFW installation,
  no-site refreshes, no-op refreshes, and non-interactive error handling.
- The second slice adds 33 default-runner tests. All subprocess, service,
  health-check, and privilege boundaries are mocked; filesystem assertions use
  temporary directories. No tests open a network connection or mutate the
  host.
- The service-tool grouping remains pattern-driven: the new webhook module is
  automatically included by the existing `services` suite, while the expanded
  Cloudflare module remains in the intentional `network`/`web` overlap.

The webhook manager now has 97% statement coverage in the branch-aware report.
The Cloudflare setup helper is at 50% statement coverage because its
interactive authentication, tunnel creation, and full install workflow remain
large user-facing paths; these are the next meaningful cases only when an
owner is prepared to maintain their mocked workflow contract.

### Simplification rules

- Do not remove a test solely because its normalized body matches another test.
  First map both tests to the same production branch and contract; otherwise
  retain them or consolidate only their shared setup.
- Prefer `subTest` for multiple inputs with one expected result, such as valid
  and invalid scalar validators. Keep separate tests when error text, security
  policy, compatibility behavior, or failure source is different.
- Keep explicit test names for rollback, authorization, and compatibility
  boundaries. They make failures actionable and prevent a broad table from
  hiding which safety rule regressed.
- Split `test_validation.py` (167 methods), `test_proxmox_manage.py` (107),
  `test_storage.py` (104), `test_agent_tools.py` (70),
  `test_antistatic_steps.py` (70), and `test_project_manifest.py` (66) only
  along their existing behavior classes. The current classes already group
  behavior, so a mechanical move is not part of this plan.

### Ongoing review rules

- New system-mutating code must include success, refusal, external-command
  failure, timeout, and cleanup/rollback coverage where those paths exist.
- Keep compatibility and security tests even when their names contain
  “legacy”; age or terminology alone is not evidence of test slop.
- Revisit the older watch list when implementation changes: the Proxmox helper
  cluster, `test_validators.py`, `test_disk_utils.py`, `test_progress_utils.py`,
  and `tests/service_tools/test_scrub_par2.py`.
- Review coverage trends by domain suite and changed files. Do not block a
  release on a global percentage until the newly measured baseline is stable.
- Do not add tests for an unowned module until its supported/abandoned status
  is recorded; this prevents dead code from becoming a permanent test surface.

## Verification

- `python3 run_tests.py` — 3,283 passed, 1 skipped.
- `python3 run_tests.py --suite services --suite network --suite web` — 649 passed.
- `python3 run_tests.py test_progress_utils test_run_tests` — 48 passing.
- `python3 run_tests.py --suite core --suite storage --suite deployment` —
  1,561 passing.
- Branch-aware coverage in an isolated `coverage.py` environment — 43,455
  statements, 16,856 branches, 68% total coverage; no global threshold
  enforced.
- `make check` — compilation, documentation, packaging, artifact, and test checks passed.
- One-off `trace` plus AST review — approximately 68.5% of tracked production statement lines; 15 files untraced.
- `python3 -m py_compile run_tests.py tests/test_progress_utils.py tests/test_run_tests.py` — passing.
- `git diff --check` — passing after the final diff review.
