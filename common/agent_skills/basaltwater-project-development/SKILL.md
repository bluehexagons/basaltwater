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

Repository guidance owns project decisions and acceptance commands. Workstation
skills own machine/session operation; orchestration guidance owns delegation
and provider settings. Follow the active session's higher-priority authority
and tool policy at those boundaries; skills and saved notes do not expand it.

Keep changes scoped and follow the project's patterns. Update docs for changed
behavior and reusable project decisions; put task progress in tracking tools.
Keep personal infrastructure details, deployment inventories, account paths and
raw operational evidence in private state outside Git, even without credentials.
Treat private business/customer information and nonpublic project inventories
likewise. Use synthetic test data. Keep product-specific workflows in opt-in
integration docs; do not generalize one user's layout or release process.
Operating or testing a user's systems does not authorize publishing them. Use
fictional documentation examples and sanitized verification summaries; retain
intentionally public project/upstream references. Specific personal details need
an explicit user request before inclusion in tracked files. Keep native
build/test commands usable without Basaltwater. Reuse shared environment tools
while retaining project-specific behavior and a documented manual setup route.
For disposable rootless Podman testing assets, follow the
[container lifecycle reference](references/containers.md).
For Electron tests on Debian, CachyOS, Windows or WSL, use the
[platform testing guide](references/electron-testing.md) to select the display,
retain logs and distinguish VM from target performance.
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
code, and duration. The same summary is saved as `summary.json` before terminal
output. Use `--output-directory NEW_PATH` before `--` to choose a new directory
under an existing private parent outside Git and retain its path before launch.
Existing paths are refused. The saved initial `in_progress` status has no return
code; it is not proof that the command remains alive or completed. Inspect the
owned run before retrying after a disconnect. The helper captures a foreground
command; it does not detach or supervise a durable job.
It returns the child status, `124` on timeout, or `130` on
interrupt. Windows cleanup targets the owned process tree before its parent;
a `cleanup_error` field means cleanup could not be confirmed, so inspect owned
processes before retrying. Keep the summary and only bounded failure excerpts in
context; logs are retained for follow-up. A timeout does not establish whether a
desktop mutation completed, so inspect its status/state before any further action and
never blindly relaunch it.

Before a handoff or context limit, update one current checkpoint in private
tracking or local state outside Git: checkout/branch, tested commit and dirty
state, current decisions, unfinished work, next action, evidence paths and
running/uncertain job handles.
Keep history and earlier review rounds separately. Use the shared
[checkpoint and handoff templates](https://github.com/bluehexagons/basaltwater/blob/main/docs/agents/handoffs.md).
A new worker still needs a self-contained brief with constraints and unresolved
objections. Recheck live state before resuming a timed-out mutation.

## Work and verify

Use `basaltwater-frontend-design` for new screens, substantial redesigns, or
interaction design. Use `basaltwater-subagents` for independent work when the
savings justify handoff and integration. For broad audits, split independent
file groups into bounded read-only reviews; reconcile findings before edits.
Keep coupled changes under one owner.

In T3 sessions, use [environment checks, project actions and visual replies](references/t3-workflows.md)
when diagnosing connection/account mismatches, connecting native project commands
to T3 or presenting an interactive result.
Use the live tools; installing T3 does not expose them to independent agents.

## Bound optimization work

Before changing code for performance, record a named, measured bottleneck in
one representative project workload, its baseline and measurement method, and
the correctness/quality gates that must continue to pass. State a falsifiable
hypothesis for the proposed change. Tool availability or an expensive-looking
function alone is not a bottleneck measurement.

Set a minimum useful benefit before the first experiment: a concrete absolute
or relative improvement in the user-relevant metric, larger than observed
measurement noise and worth the added complexity. The project owns this
threshold and its rendering/quality acceptance. Record CPU power preferences,
renderer/device, temperatures, competing work and build/revision identity when
they can change results. Use shared resource reservations and an optional
[performance session](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/performance-sessions.md)
where available; retain a native measurement route.

Choose a finite experiment count and elapsed-time budget in advance, counting
variants and repeated attempts against those bounds. Stop when either budget
is exhausted, the bottleneck is removed, correctness/quality fails, or measured
benefits fall below the useful threshold. Keep the simplest verified useful
result and discard unsuccessful variants. Do not continue marginal tuning by
silently expanding the budget. A further bounded batch needs new bottleneck
evidence or a materially different hypothesis with its own stated threshold
and budget. Report the measured benefit, uncertainty and why work stopped.

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
