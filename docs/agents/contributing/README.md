# Contributor and coding-agent guide

Use this guide when changing the Basaltwater repository. For operating a
managed coding VM or workstation, use the sibling
[agent-systems guide](../README.md) instead.

## Start here

| Need | Guide |
| --- | --- |
| Essential repository rules and workflow | [Quick start](QUICK_START.md) |
| Types, validators, capability helpers, and setup composition | [Quick reference](QUICK_REFERENCE.md) |
| Common failures and debugging | [Troubleshooting](TROUBLESHOOTING.md) |

## Required workflow

1. Run `git status -sb` and preserve unrelated worktree changes.
2. Read every file you will edit completely.
3. Follow the existing patterns and use `apply_patch` for edits.
4. Review `git diff --check` and the final diff before staging.
5. Run focused tests, then the relevant wider checks.

## Critical rules

- Use `from __future__ import annotations` in Python modules.
- Never commit secrets or credentials.
- Use `lib.validation` for structured paths, packages, network values, and
  setup configuration; use `lib.validators` for hosts, IPs, and usernames.
- Update all callers and tests when changing a function signature.
- Remove unused code rather than retaining it for compatibility.
- Choose the machine capability helper that matches the operation; do not use
  `can_modify_kernel()` as a general package or service check.
- Mock system calls and use temporary directories in tests.

## Core architecture

| Boundary | Responsibility |
| --- | --- |
| `basaltw setup` | Parses and validates user-facing configuration, then stages source for the target |
| `remote_setup.py` | Runs one target-side step plan, including optional post-profile work composed by `plugins/post_setup.py` |
| `plugins/` | Owns setup composition through builders and capability extensions |
| `bootstrap` / `self-setup` | Configures the local orchestration host through `lib/orchestrator_bootstrap.py` |

When adding a setup feature, implement it in its owning package, wire it into
the relevant plugin builder or extension, add focused tests, and update the
operator documentation.

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
The fast target omits the generated brand export and wheel build/install checks;
run `make check-ci` before committing changes exercised by CI.

Use the project's environment rather than installing development dependencies
into CachyOS's or Debian's system Python.
CI installs the same `dev` and `completion` extras from `pyproject.toml`, so new
test dependencies such as Guacamole's aiohttp are included automatically.

Routine CI uses Python 3.13 in Debian Trixie. Branch and release workflows run
[`scripts/run_debian_ci.py`](../../../scripts/run_debian_ci.py) on a hosted
runner. It tries Docker's official Debian image from
[public ECR](https://gallery.ecr.aws/docker/library/debian), Google's
[Docker Hub cache](https://docs.cloud.google.com/artifact-registry/docs/pull-cached-dockerhub-images),
then Docker Hub, with a two-minute bound per pull. This keeps registry fallback
inside a runnable step; job-container initialization would fail before fallback.
The selected `actions/setup-python` interpreter is mounted read-only and creates
a private container venv; checks still run in Debian. Only image pulls fall back:
failed package installation, checks and coverage are never replayed. Cleanup
removes only that invocation's named container. An all-registry failure means
checks did not start. Inspect the failed step before changing application code.
`make check-ci` runs all static, brand and wheel checks and executes the full
suite once with branch coverage, matching branch CI. Do not also run
`make check` and `make coverage` for the same unchanged source: that repeats
the full suite. `make check` remains available for full verification without
coverage, and `make coverage` produces a standalone full-suite report. Fast
selections do not narrow `check-ci` or `coverage`.

```bash
python3 -m py_compile file.py
basaltw setup server_web test.com --dry-run
python3 -m unittest discover -s tests
git diff --check
```

Tests run on Debian and must not modify the local system. Use `--dry-run` for
setup-plan validation; never run a test against a real target.
When running those tests from a CachyOS workstation, fixtures must mock the
target distribution, package-manager discovery, and account privileges. A
Debian test must not accidentally enter the local CachyOS setup path or inspect
and remove files from the developer's host.
Register new test modules in the appropriate `TEST_SUITE_PATTERNS` entry in
`run_tests.py`; the default suite checks that every discovered module belongs
to a domain suite.
Generated OpenCode context hooks also have offline behavioral tests using
Node.js. Keep Node.js available when running those tests locally; the Debian CI
container installs it so this coverage is exercised there.

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
