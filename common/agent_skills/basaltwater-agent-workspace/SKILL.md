---
name: basaltwater-agent-workspace
description: Plan bounded subagent work, isolate editors in managed Git worktrees, and integrate and clean up tasks on a Basaltwater agent VM.
metadata:
  managed-by: basaltwater
---

# Managed agent workspaces

Use the managed workspace command before concurrent tasks could edit the same
checkout. It creates a dedicated `agent/TASK` branch below the user's private
Basaltwater worktree root and never modifies the primary checkout's files.

## Delegation and ownership

Delegate when independent scopes and a clear acceptance check save more work
than context transfer, review, and integration cost. Keep small, tightly coupled
changes with one agent. Agents share the filesystem unless assigned separate
worktrees. A read-only reviewer may inspect the primary checkout; an editor
needs its own worktree. Checks that generate files count as writes.

The parent owns task boundaries, integration, validation of the combined result,
and cleanup. Assign files explicitly; shared configuration, generated files,
and lockfiles belong to one named owner. Workers commit their assigned files and
return worktree path, branch, base and final commit, changed files, validation
results, and unresolved concerns. They do not merge, push, or remove worktrees
unless the parent assigns that responsibility within the user's authorization.

Choose branch merges upfront: they preserve the ancestry required by managed
cleanup. Read [the complete lifecycle](references/lifecycle.md) for assignments,
handoffs, review, integration, and the implications of cherry-picking or squash.
For cost-aware model choice and project learning, read
[model selection](references/model-selection.md) when selecting subagents.

## Create and inspect

From any location, supply the primary repository and a short task name:

```bash
basaltw agent workspace create ~/repos/PROJECT TASK --base HEAD --json
basaltw agent workspace list ~/repos/PROJECT --json
basaltw agent workspace status WORKTREE --json
```

Use the returned absolute worktree path as the task's working directory. Use a
different task name for every concurrent task. Do not create ad hoc sibling
clones or run multiple editing agents in the primary checkout.

The default base is the primary checkout's current `HEAD`. Supply a specific
verified branch or commit with `--base` when the task must start elsewhere.
For work targeting current remote main, fetch that ref and use `--base
origin/main`; creating a workspace does not fetch or update the primary branch.
Uncommitted primary-checkout changes are not included in the new worktree.

For recurring panel prompts, prepare the worktree before scheduling and select
its returned path in **Agents**. Worktree creation and removal also change the
primary repository's Git metadata, which may be outside a task's writable
sandbox. The panel's serial runner does not coordinate other agent sessions.
Review changes left by one run before the next editing run; do not clear a dirty
checkout automatically to keep a schedule running.

## Integration

Commit and validate inside the returned worktree. The parent reviews the handoff
and diff, then merges the task branch when integration is authorized. Before an
authorized push, inspect the destination branch and preserve unrelated work.
Push the intended destination explicitly
and verify the remote result. If the remote advanced, integrate and validate
the new changes before retrying; do not force-push to bypass divergence.

## Cleanup

Inspect first, then preview the removal:

```bash
basaltw agent workspace status WORKTREE --json
basaltw agent workspace remove WORKTREE --dry-run --json
basaltw agent workspace remove WORKTREE --json
```

Removal is intentionally narrow. It refuses the primary checkout, paths
outside the managed workspace root, non-`agent/*` branches, dirty or untracked
files, and branches that are not merged into the primary checkout's current
`HEAD`. Resolve those conditions explicitly; never bypass them with `git
worktree remove --force` or `git branch -D` unless the user separately asks to
discard work.

`status --json` reports `repository`, `primary_branch`, `primary_head`, and
`merged_into_primary`. This is an ancestry observation; `remove --dry-run` also
checks the managed path, branch, registration, and dirty state.
