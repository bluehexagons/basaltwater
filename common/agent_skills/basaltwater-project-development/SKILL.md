---
name: basaltwater-project-development
description: Plan, implement, review, verify and iterate on project work using the project's own conventions.
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
| T3 project commands | [Project actions](references/t3-workflows.md#project-commands-and-worktree-setup) |
| Work tracked by a T3 issue or pull request | [Issue-to-PR workflow](references/t3-issue-to-pr.md) |
| Return images or other visual evidence in T3 | [Visual replies](references/t3-visual-replies.md) |

Verify actual tool exposure in the live session.

## Iterate on the outcome

Treat “iterate,” “keep improving,” and similar requests as authorization to
choose and carry out successive passes within the stated scope. If the user
asks for findings or a plan, keep changes within the authority they gave.

For broad reviews, investigations, and playtests, map the main workflows or
plausible causes up front. Pick representative cases and prioritize leads by
likely impact. Check neighboring paths or another plausible cause before
calling the exploration complete.

Use each verified improvement as a checkpoint. Reassess the updated result
against the original request, explore what remains, and implement and verify
the next worthwhile improvement. Carry out feasible, authorized follow-ups
you would otherwise report as next steps. Share progress during the work and
resolve routine decisions yourself.

Finish when the requested outcome is verified and a fresh pass finds no
material in-scope improvement worth pursuing. Stop at a user-set limit or when
a concrete blocker prevents further useful work. In the handoff, state the
coverage, verification, and reason for stopping.

## Bound optimization work

Define a measured baseline, quality gates, minimum useful improvement, and
finite experiment budget. See [optimization experiments](references/optimization.md).

## Wait for jobs to finish

Keep the job handle and prefer completion notifications or watches. Continue
independent work and space polls when needed. Check the same job after a
timeout and read its final result before reporting success.

## Review and report

Run focused project checks. Review the diff for scope, artifacts, and secrets.
Report the change, validation, and gaps.

## Publish authorized work

When asked to commit or push, reconcile the worktree and local commits against
the full request, including in-scope edits and commits from earlier passes.
Review the full staged diff, including changes staged before the task. If the
requested work covers whole paths, `git commit --only -- PATH ...` can keep
unrelated paths staged. For files with mixed edits, isolate the requested
hunks and verify the commit's patch.

Before pushing, review the complete outgoing commit range against the current
remote branch. After the push, verify the remote branch contains the intended
commits. Recheck the worktree and account for any requested changes still local
in the handoff.
