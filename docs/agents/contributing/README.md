# Contributor guide

Read [AGENTS.md](../../../AGENTS.md) for repository rules. For operating a managed
machine, start with the [agent systems index](../README.md).

| Task | Open |
| --- | --- |
| Locate helpers, validators, and setup composition | [Contributor quick reference](quick-reference.md) |
| Debug a failed setup or check | [Troubleshooting](troubleshooting.md) |
| Change builder admission, delivery, or target activation | [Build implementation map](build-architecture.md) |
| Change persistent state or managed systemd units | [Transaction safety](transaction-safety.md) |
| Add or maintain tests | [Test design and fixtures](test-design.md) |
| Diagnose CI, package checks, or runner compatibility | [CI and packaging](ci.md) |
| Measure development resource use | [Resource benchmarks](benchmarks.md) |
| Delegate or choose a model | [Subagent workflows](../agent-subagents.md) |

## Core architecture

Import helpers from their owning `lib.*` module. The package initializer only
checks the supported Python floor; it does not load configuration or SSH code
for unrelated helpers.

| Boundary | Responsibility |
| --- | --- |
| `basaltw setup` | Parses and validates configuration; stages source for ordinary target builds or coordinates an explicitly selected saved builder through `lib.build_setup` |
| `remote_setup.py` | Runs one target-side step plan, including optional post-profile work composed by `plugins/post_setup.py` |
| `plugins/` | Owns setup composition through builders and capability extensions |
| `bootstrap` / `self-setup` | Configures the local orchestration host through `lib/orchestrator_bootstrap.py` |

When adding a setup feature, implement it in its owning package, wire it into
the relevant plugin builder or extension, add focused tests, and update the
operator documentation.

Routine setup guides should use a few `setup` commands that describe the desired
machines and automate prerequisite ordering. Do not recommend `patch` as part of
routine setup. Keep detailed inspection and recovery procedures in the owning
service documentation, linked from the normal workflow.

## Standard checks

Use an isolated Python environment with the project's development extras.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install '.[dev,completion]'
source .venv/bin/activate
make check-fast
```

Use `make docs-check` for documentation, `make check-fast SUITE=web` for a
focused implementation check, and `make check-ci` for broad shared changes
or local CI-equivalent validation. See [local checks](checks.md) for test
selection, native Windows documentation checks, and browser development.
