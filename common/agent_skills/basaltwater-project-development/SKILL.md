---
name: basaltwater-project-development
description: Plan, implement, review and verify repository changes using the project's own conventions.
metadata:
  managed-by: basaltwater
---

# Project development

Read the project's applicable `AGENTS.md`, contributor guide, and task
documentation. Preserve unrelated worktree changes. Read the relevant
implementation, callers, tests, and configuration before editing. Use the
project's native commands and acceptance checks.

Workstation skills own machine operations. The active session governs tool
access, permissions, and delegation. Keep private infrastructure, account and
business details and raw evidence outside Git. Use fictional examples and
sanitized verification. See the [project standards](https://github.com/bluehexagons/basaltwater/blob/main/docs/agents/project-standards.md).

## Keep evidence focused

Open only the relevant references and satisfy the project's full-file reading
rules. Keep verbose output in private artifacts and return status, actionable
failures, and evidence paths. Budget the whole batch and treat truncated
results as incomplete. See [tool output and evidence](references/tool-output.md).

Before handoff or context exhaustion, save the checkout, tested and dirty
state, decisions, next action, and job handles in a private checkpoint.
Reconcile uncertain mutations before retrying. See the [handoff templates](https://github.com/bluehexagons/basaltwater/blob/main/docs/agents/handoffs.md).

## Work and verify

| Work | Reference |
| --- | --- |
| Basaltwater capabilities | `basaltw agent manifest . --capability NAME` for discovery, `basaltw agent doctor --capability NAME` for readiness, or `basaltw agent context` for inventory |
| New screens or substantial interface changes | `basaltwater-frontend-design` |
| Authorized independent work | `basaltwater-subagents` |
| Disposable rootless Podman fixtures | [Container lifecycle](references/containers.md) |
| Electron testing on Debian, CachyOS, Windows, or WSL | [Platform testing](references/electron-testing.md) |
| T3 connection, account, or stale skill troubleshooting | [Execution environment](references/t3-workflows.md#establish-the-execution-environment) |
| T3 project commands or requested interactive results | [Project actions](references/t3-workflows.md#project-commands-and-worktree-setup), [visual replies](references/t3-workflows.md#visual-replies) |

Verify actual tool exposure in the live session.

## Bound optimization work

Define a measured baseline, quality gates, minimum useful improvement, and
finite experiment budget. See [optimization experiments](references/optimization.md).

## Wait for jobs to finish

Keep the job handle and prefer completion notifications or watches. Continue
independent work and space polls when needed. Check the same job after a
timeout and read its final result before reporting success.

## Review and report

Run focused project checks. Review the diff for scope, artifacts, and secrets.
Report the change, validation, and gaps. Commit or publish when authorized.
