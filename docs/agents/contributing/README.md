# Contributor and coding-agent guide

Use this guide when changing the infra-tools repository. For operating a
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
| `infra-tools setup` | Parses and validates user-facing configuration, then stages source for the target |
| `remote_setup.py` | Performs target-side mutations using steps selected by the plugin registry |
| `plugins/` | Owns setup composition through builders and capability extensions |
| `bootstrap` / `self-setup` | Configures the local orchestration host through `lib/orchestrator_bootstrap.py` |

When adding a setup feature, implement it in its owning package, wire it into
the relevant plugin builder or extension, add focused tests, and update the
operator documentation.

## Standard checks

```bash
python3 -m py_compile file.py
infra-tools setup server_web test.com --dry-run
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
