# Basaltwater agent instructions

These instructions apply throughout the repository. Start with the task route
below and open the relevant reference. Active session instructions govern tools,
permissions, and delegation.

## Instruction ownership

This file and the contributor guide own Basaltwater repository decisions.
Shared project-development skills describe workflow; workstation skills own
machine/session operation; orchestration guidance owns delegation and provider
settings. Project scripts and recipes own project-specific build, capture and
acceptance behavior. Follow the active session's authority and browser/tool
policy where these meet; installed skills and saved notes do not override it.
See [project standards](docs/agents/project-standards.md) and
[handoffs](docs/agents/handoffs.md) for shared conventions.

| Task | Start here |
| --- | --- |
| Change repository code or run checks | [Contributor guide](docs/agents/contributing/README.md) |
| Find operator commands or setup procedures | [Documentation index](docs/README.md) |
| Operate a managed coding VM or workstation | [Agent systems](docs/agents/README.md) |
| Delegate work or select a model | [Subagent workflows](docs/agents/agent-subagents.md) and the live session catalog |

## Before Editing

1. Run `git status -sb` and preserve unrelated worktree changes.
2. Read every target file completely before changing it.
3. Use `apply_patch` for edits and stage only the files in the requested scope.
4. Review `git diff --check` and the final diff before committing.

## Repository rules

- Use `from __future__ import annotations` in Python.
- Keep secrets, private infrastructure and business details, and raw operational
  evidence outside Git. Use fictional examples. Publishing specific private
  details requires an explicit user request. See [documentation privacy](docs/agents/project-standards.md#documentation-and-private-operational-state).
- Validate paths, packages, and network/configuration values with
  `lib/validation.py`. Validate hosts, IPs, and usernames with `lib/validators.py`.
- Use the machine capability helper for the operation. Reserve
  `can_modify_kernel()` for kernel changes.
- Update every caller when signatures change. Remove unused code and update the
  documentation when patterns change.
- Mock system calls and use temporary directories in tests.

## Setup Boundaries

- `basaltw setup` parses and validates a user-facing configuration, then
  uploads or stages the source for `remote_setup.py`.
- `remote_setup.py` executes target-side setup steps selected by the plugin
  registry; it is the boundary for remote system mutations.
- `bootstrap`/`self-setup` configures the local orchestration host through
  `lib/orchestrator_bootstrap.py`. The shell installer forwards options placed
  before `--setup` or `--local-setup` to that bootstrap command.

Implement setup features in their owning package, wire them through the relevant
plugin builder or extension, add focused tests, and update the command reference.
