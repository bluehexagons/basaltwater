# Contributor and coding-agent guide

Use this guide when changing the Basaltwater repository. For operating a
managed coding VM or workstation, use the sibling
[agent-systems guide](../README.md) instead.

## Start here

| Need | Guide |
| --- | --- |
| Repository-specific rules | [`AGENTS.md`](../../../AGENTS.md) |
| General project workflow and delegation | [Basaltwater project development skill](../../../common/agent_skills/basaltwater-project-development/SKILL.md) |
| Basaltwater subagent workflows | [Subagent guidance](../agent-subagents.md) |
| Types, validators, capability helpers, setup composition, and command contracts | [Quick reference](quick-reference.md) |
| Common failures and debugging | [Troubleshooting](troubleshooting.md) |
| Shared build request and artifact interfaces | [Build contracts](build-contracts.md) |
| Target deployment reservations and replay safety | [Build admission](build-admission.md) |
| Debian build process and resource supervision | [Build execution](build-execution.md) |
| Pinned build inputs and bounded workspace | [Source snapshots](build-source.md), [scratch storage](build-scratch.md) |
| Durable builder jobs and composition | [Job store](build-jobs.md), [worker](build-worker.md) |
| Explicit terminal builder quota maintenance | [Settlement, source/log pruning and bundle release](build-retention.md), [capacity inspection](build-host-entrypoint.md#read-only-local-capacity-inspection) |
| Authenticated builder transport and admission | [Process entrypoint](build-host-entrypoint.md), [dispatcher](build-host-service.md), [project policy](build-host-policy.md), [source admission](build-inputs.md) |
| Managed builder enrollment and controller transport | [Setup configuration](build-host-config.md), [installer](build-host-setup.md), [SSH client](build-client.md) |
| Public selected-builder setup and recovery | [Controller setup](build-setup.md), [saved selection](build-selection.md), [selection configuration](build-selection-config.md), [pinned sources](build-setup-sources.md), [delivery planning](build-setup-plan.md) |
| Completed controller history maintenance | [Explicit archival](build-setup-history.md) |
| Operator build walkthrough and sample projects | [Build systems](../../services/build-systems.md), [examples and enrollment templates](../../examples/builds/README.md) |
| Prebuilt service compatibility | [Runtime validation](build-runtime.md) |
| Authenticated prebuilt target deployment | [Target policy](build-target-policy.md), [staging](build-target-artifacts.md), [dispatcher](build-target-service.md), [activation](build-target-activation.md), [process entrypoint](build-target-entrypoint.md), [SSH client](build-target-client.md) |

## Core architecture

| Boundary | Responsibility |
| --- | --- |
| `basaltw setup` | Parses and validates configuration; stages source for ordinary target builds or coordinates an explicitly selected saved builder through `lib.build_setup` |
| `remote_setup.py` | Runs one target-side step plan, including optional post-profile work composed by `plugins/post_setup.py` |
| `plugins/` | Owns setup composition through builders and capability extensions |
| `bootstrap` / `self-setup` | Configures the local orchestration host through `lib/orchestrator_bootstrap.py` |

When adding a setup feature, implement it in its owning package, wire it into
the relevant plugin builder or extension, add focused tests, and update the
operator documentation.

For `--build-on`, the public remote-setup hook holds the controller setup-target
lock across preflight, minimal target enrollment, artifact delivery and guarded
final profile application. Minimal enrollment preserves the saved profile; final
setup carries transient expected release identities and omits reenrollment.
Keep builder selection and credentials on the controller. Targets retain artifact
mode and installed releases for local reconciliation without source fetches or
build commands. Preserve guards, retained inputs and exact IDs on uncertain
responses; do not add retries or replacement identities. See
[controller setup](build-setup.md) for the bounded scope and recovery contract.
Selected native public-flow coverage includes a fresh Debian target with Node-built
static sites and Go services, guarded final setup, retained reconciliation and idle reboot
without target compilers. See the [coverage map](../../plans/testing-coverage.md)
for remaining boundaries; mocked checks and dry runs do not establish live coverage.

## Standard checks

Install the development extra in an isolated environment before running the CI
checks; it includes wheel building and coverage:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install '.[dev,completion]'
source .venv/bin/activate  # In fish: source .venv/bin/activate.fish
make check-fast
```

Use `make check-fast` for the edit/test loop: it runs syntax, documentation and
package metadata checks plus the smoke suite. Select relevant tests with
`make check-fast SUITE=web`, `make test SUITE="web network"`, or
`make test TEST=tests.test_gogs_steps.TestGogsNginx`. `TEST_ARGS="--durations 20"`
reports slow tests. Suite and explicit test selections combine without repeats.
Quiet runs discard test stdout/stderr by default. `TEST_ARGS="--show-output"`
retains the last 256 Ki characters and prints that tail on failure, with an
omission notice when truncated. Failure tracebacks are still reported normally;
use `make test-verbose` for live, complete test output.
The fast target omits the generated brand export and wheel build/install checks;
run affected checks before committing and let GitHub CI perform full validation.
`make artifact-check` suppresses successful build/install chatter and reports
the wheel smoke result. Failed build/install commands replay their full output
and remain failures.
Use `python3 run_tests.py --list-suites` to see concise suite names and sizes.
Choose all affected domains; shared configuration, validators, plugin composition,
test infrastructure, or uncertain dependencies warrant a full local run.
Use `make check-ci` for that broad validation or when CI is unavailable.

Use the project's environment rather than installing development dependencies
into CachyOS's or Debian's system Python.
CI installs the same `dev` and `completion` extras from `pyproject.toml`, so new
test dependencies such as Guacamole's aiohttp are included automatically.

Python 3.11 is the minimum supported interpreter; release CI also checks Python
3.15. Keep this range aligned with `pyproject.toml` and the installer checks.
The build backend requires setuptools 84.2.1 or newer, and the development
extra requires aiohttp 3.14.5 or newer, excluding versions affected by the
[setuptools path traversal advisory](https://github.com/pypa/setuptools/security/advisories/GHSA-5rjg-fvgr-3xxf)
and [aiohttp security fixes](https://github.com/aio-libs/aiohttp/releases/tag/v3.14.4).
Target services use distribution-managed packages and their security updates.

Routine CI uses Python 3.13 in Debian Trixie. Branch and release workflows run
[`scripts/run_debian_ci.py`](../../../scripts/run_debian_ci.py) on a hosted
runner. It tries Docker's official Debian image from
[public ECR](https://gallery.ecr.aws/docker/library/debian), Google's
[Docker Hub cache](https://docs.cloud.google.com/artifact-registry/docs/pull-cached-dockerhub-images),
then Docker Hub, with a two-minute bound per pull. This keeps registry fallback
inside a runnable step; job-container initialization would fail before fallback.
The selected `actions/setup-python` interpreter and supported `actions/setup-node`
runtime are mounted read-only from the runner tool cache. Python creates a private
container venv; checks still run in Debian. Branch CI covers Node 24.22.1 and
26.12.1. Only image pulls fall back:
failed package installation, checks and coverage are never replayed. Cleanup
removes only that invocation's named container. An all-registry failure means
checks did not start. Inspect the failed step before changing application code.
`make check-ci` runs all static, brand and wheel checks and executes the full
suite once with branch coverage, matching branch CI. Do not also run
`make check` and `make coverage` for the same unchanged source: that repeats
the full suite. `make check` remains available for full verification without
coverage, and `make coverage` produces a standalone full-suite report. Fast
selections do not narrow `check-ci` or `coverage`.
Branch CI runs the complete covered suite and all static/artifact checks once
on Node 24. The Node 26 job runs only `make test SUITE=node`: generated OpenCode
hooks, Guacamole browser behavior, and the certificate-install UI. Keep this
selection in `run_tests.py` current when adding tests that execute JavaScript.
Both jobs retain the Debian environment and registry fallback. Release CI still
runs full checks at both supported Python version endpoints.

CI replaces obsolete runs when a newer revision is pushed to the same branch
or pull request. The coverage run reports the ten slowest modules and tests;
module totals reveal accumulated fixture cost that individual timings can hide.

```bash
python3 -m py_compile file.py
basaltw setup server_web test.com --dry-run
python3 -m unittest discover -s tests
git diff --check
```

Tests run on Debian and must not modify the local system. Use `--dry-run` for
setup-plan validation; never run a test against a real target.
Debian CI runs as root inside its container. When a fixture mocks a desktop
user's UID, model ownership of its temporary files consistently while retaining
the permission and file-type checks being tested.
Mock user-runtime locks in setup/refresh tests that do not exercise locking;
use a temporary file descriptor when refresh needs an exec handoff.
Set required fixture directory modes explicitly rather than relying on the
runner's umask. An unsafe ancestor can otherwise prevent a negative test from
reaching the file or failure it intends to exercise.
`Path.mkdir(parents=True, mode=...)` applies the requested mode only to the
leaf; set required ancestor modes explicitly too, and use `chmod` when a
restrictive umask would remove required access.
When running those tests from a CachyOS workstation, fixtures must mock the
target distribution, package-manager discovery, and account privileges. A
Debian test must not accidentally enter the local CachyOS setup path or inspect
and remove files from the developer's host.
Register new test modules in the appropriate `TEST_SUITE_PATTERNS` entry in
`run_tests.py`; the default suite checks that every discovered module belongs
to a domain suite.
Generated OpenCode context hooks also have offline behavioral tests using
Node.js. Keep a supported Node.js available when running those tests locally;
the Debian CI container uses the explicitly selected runner runtime so this
coverage is exercised there.

First-party Chromium/Firefox application checks are a separate developer-machine
command: `make browser-test`. They are never dependencies of `check-ci` and do
not install browser payloads. Follow [browser qualification](../browser-testing.md)
for prerequisites, private evidence, the disposable Guacamole boundary, and the
separate native Firefox ESR release check. Keep fast unit/service/render checks
in CI. When creating project instructions, document browser targets and the
local intensive test command in its agent/contributor guidance; projects can
explicitly choose another CI policy when their needs justify it.

For interactive panel edits, run `make panel-dev` in the activated environment.
It serves the production handlers on loopback with sample scenarios, simulated
host actions, private temporary state and automatic source/browser reload.
Use `PANEL_ARGS="--scenario degraded"` for failure states or `PANEL_ARGS="--https"`
for transport-sensitive forms. `PANEL_ARGS="--surface connect"` opens T3 Connect;
`pairing`, `approval`, `desktop`, and `console` select the other specialized
panels with the same reset/reload workflow. Desktop and Console require reviewed
Guacamole assets and enable HTTPS automatically.
See the [local panel workflow](../browser-testing.md#develop-the-web-panel-locally)
for scenario/reset controls, supported simulated actions and certificate trust.
For a focused browser check use
`make browser-test BROWSER_ARGS="--engine chromium --grep 'panel development'"`;
filtered results do not count as full application qualification.

## Maintaining tests

Keep tests that exercise an observable contract: parsed arguments, generated
configuration, state changes, failure propagation, permissions, rollback, or
credential handling. A regression's age alone is not a reason to remove it.

Before adding a regression, check the owning domain suite for an existing case
that can cover it. Remove settled feature-presence checks, import/re-export
identity assertions, and catalog snapshots when behavior tests already cover
the contract. Test ordering with small fixtures rather than copying the entire
built-in catalog. Tests must call Basaltwater code; recomputing the same expected
value with a standard-library function does not verify the application.

When pruning, name the retained coverage in the commit description and preserve
distinct failure inputs, even if their assertion bodies match. Use `subTest`
for a shared behavioral contract when it improves clarity. Sweep independent
options separately and keep representative interaction cases; avoid Cartesian
products unless the options interact. Reuse parsers and immutable fixtures
within the owning test class. Generated HTML and
service configuration assertions remain useful when they verify access,
escaping, activation, or other behavior. Run the affected selection and review
coverage when pruning execution paths; a broad cleanup warrants `make check-ci`.

Keep related variants in the owning module: optional development bundles share
`test_development_tool_bundles.py`, disk-space calculations belong in
`test_disk_utils.py`, and mount boundaries belong in `test_mount_utils.py`.
Prefer one fixture per contract over rerendering identical configuration for
each directive. Mock host observations such as disk usage and assert exact
values and threshold boundaries. Coverage overlap helps locate candidates;
it does not establish that their assertions or failure inputs are redundant.
For storage limits, lower the fixture's limit instead of generating large
volumes of data. For timeout logic, advance fixture timestamps and control the
owning module's timer; retain real transport tests for connection lifecycle.
Synchronize concurrent tests on events instead of fixed sleeps, and keep mocks
local so they do not change the event loop's clock or unrelated tasks.

## Repository maps

| Topic | Location |
| --- | --- |
| Configuration and serialization | `lib/config.py`, `lib/runtime_config.py` |
| Common types | `lib/types.py` |
| Machine capability checks | `lib/machine_state.py` |
| Structured and identity validation | `lib/validation.py`, `lib/validators.py` |
| Remote commands | `lib/remote_utils.py` |
| User-facing command parsing | `lib/arg_parser.py` |
| Setup composition | `plugins/` |
| Operator behavior | [Documentation index](../../README.md) |
