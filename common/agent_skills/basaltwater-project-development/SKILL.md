---
name: basaltwater-project-development
description: Apply a repository-agnostic workflow for planning, changing, reviewing, and documenting software projects, including discovery of local instructions and useful independent subagent work.
metadata:
  managed-by: basaltwater
---

# Project development

Use the project's own conventions: read its root and applicable nested
`AGENTS.md`, contributor guide, and task documentation before editing. Inspect
the worktree and preserve unrelated changes. Read the relevant implementation,
callers, tests, and configuration; do not carry Basaltwater repository rules
into another project.

Keep changes scoped and follow the project's patterns. Update docs for changed
behavior and durable decisions; put task progress in tracking tools. Keep native
build/test commands usable without Basaltwater. Reuse shared environment tools
while retaining project-specific behavior and a documented manual setup route.
For disposable rootless Podman testing assets, follow the
[container lifecycle reference](references/containers.md).
See [project standards](https://github.com/bluehexagons/basaltwater/blob/main/docs/agents/project-standards.md)
when adopting these conventions.

## Keep evidence focused

Select the needed capability in `basaltw agent manifest` or `doctor`; use
`--help` for selectors. Read a skill entrypoint once, then only references
relevant to the current operation. Search for paths or symbols before opening
large files. Satisfy the project's complete-file reading rules before edits.

Keep verbose command results in private local artifacts where supported; bring
exit status, counts, actionable failures, and the artifact path into context.
When orchestrating tools in code, select fields before emitting results.
Do not print both structured data and its duplicate text envelope. Never
discard failure details or treat truncated evidence as a complete result.
Retrieve a bounded relevant excerpt when investigating a failure.

For a verbose local command, use
`python3 <skill-directory>/scripts/capture_output.py --timeout 300 -- COMMAND [ARGS...]`.
It runs argv without a shell, closes stdin, and keeps stdout/stderr in private
temporary logs; its JSON summary reports log paths, byte counts, status, return
code, and duration. It returns the child status, `124` on timeout, or `130` on
interrupt. Keep the summary and only bounded failure excerpts in context; logs
are retained for follow-up. A timeout does not establish whether a desktop
mutation completed, so inspect its status/state before any further action and
never blindly relaunch it.

Before a handoff or context limit, save a short task note outside source files:
objective, current decisions, unresolved issues, job handles, validation status,
and artifact paths. Return that note plus new findings; leave full logs and
earlier review rounds in artifacts. A new worker still needs a self-contained
brief with the constraints and unresolved objections relevant to its task.

## Work and verify

Use `basaltwater-frontend-design` for new screens, substantial redesigns, or
interaction design. Use `basaltwater-subagents` for independent work when the
savings justify handoff and integration. For broad audits, split independent
file groups into bounded read-only reviews; reconcile findings before edits.
Keep coupled changes under one owner.

In T3 sessions, use [project actions and visual replies](references/t3-workflows.md)
when connecting native project commands to T3 or presenting an interactive result.
Use the live tools; installing T3 does not expose them to independent agents.

## Wait for jobs to finish

Keep the handle for tests, builds, and CI jobs. Wait for completion on that
same handle; a timeout or silence does not establish failure or justify a
second run. Prefer native completion notifications/watchers. If polling is
necessary, space checks and back off. Continue independent work while waiting.
Read final status and relevant output before reporting success.

## Review and report

Run focused project checks for the changed behavior. Review the final diff
and worktree for scope, artifacts, and secrets. Report behavior changed,
validation, and unresolved checks. Commit, push, or publish when authorized
by the user or applicable project workflow.
