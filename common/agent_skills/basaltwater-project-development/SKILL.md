---
name: basaltwater-project-development
description: Plan, implement, review and verify repository changes using the project's own conventions.
metadata:
  managed-by: basaltwater
---

# Project development

Use the project's own conventions: read its root and applicable nested
`AGENTS.md`, contributor guide, and task documentation before editing. Inspect
the worktree and preserve unrelated changes. Read the relevant implementation,
callers, tests, and configuration; do not carry Basaltwater repository rules
into another project.

Repository guidance owns project decisions and acceptance commands. Workstation
skills own machine/session operation; orchestration guidance owns delegation
and provider settings. Follow the active session's higher-priority authority
and tool policy at those boundaries; skills and saved notes do not expand it.

Keep private infrastructure/account paths, business/customer information and raw
operational evidence outside Git, even without credentials. Operating or testing
a system does not authorize publishing its details; specific private details need
an explicit user request. Use fictional examples and sanitized verification.
Keep project-specific behavior and native build/test commands in the project;
update reusable docs and retain a manual setup route.
See [project standards](https://github.com/bluehexagons/basaltwater/blob/main/docs/agents/project-standards.md)
when adopting these conventions.

## Keep evidence focused

Select the needed capability in `basaltw agent manifest` or `doctor`; use
`--help` for selectors. Read a skill entrypoint once, then only references
relevant to the current operation. Search for paths or symbols before opening
large files. Satisfy the project's complete-file reading rules before edits.

Keep verbose results in private artifacts; return status, exit code, counts,
actionable failures and evidence paths. Select fields from tool results instead
of printing full envelopes or duplicate structured/text copies. Budget the
whole batch's reply, not each call independently. Truncation is incomplete
evidence, not success. For capture, bounded log inspection, literal searches and
portable examples, use [tool output and evidence](references/tool-output.md).

Before a handoff or context limit, retain one current private checkpoint with
checkout/branch, tested/dirty state, decisions, remaining work, next action and
evidence/job handles. Keep history separately; use the shared
[checkpoint and handoff templates](https://github.com/bluehexagons/basaltwater/blob/main/docs/agents/handoffs.md).
Pass self-contained constraints and unresolved objections to a new worker.
Reconcile uncertain mutations before resuming; never blindly relaunch them.

## Work and verify

Use `basaltwater-frontend-design` for new screens, substantial redesigns, or
interaction design. Use `basaltwater-subagents` for independent work when the
savings justify handoff and integration. For broad audits, split independent
file groups into bounded read-only reviews; reconcile findings before edits.
Keep coupled changes under one owner.

Read only the relevant platform reference:

| Work | Reference |
| --- | --- |
| Disposable rootless Podman fixtures | [Container lifecycle](references/containers.md) |
| Electron testing on Debian, CachyOS, Windows or WSL | [Platform testing](references/electron-testing.md) |
| T3 connection/account mismatches, project actions or interactive results | [T3 workflows](references/t3-workflows.md) |

Use live session tools; installation alone does not expose them to a worker.

## Bound optimization work

Before experiments, name a measured bottleneck, baseline/quality gates, minimum
useful benefit above noise, and finite variant/time budgets. Stop at those bounds,
on failed gates, or below the useful threshold. Read
[optimization experiments](references/optimization.md) when planning performance
work; keep project thresholds and a native measurement route authoritative.

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
