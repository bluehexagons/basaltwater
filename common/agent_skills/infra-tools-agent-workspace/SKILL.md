---
name: infra-tools-agent-workspace
description: Isolate concurrent coding tasks in managed Git branches and worktrees on a Basaltwater agent VM.
metadata:
  managed-by: infra_tools
---

# Managed agent workspaces

Use the managed workspace command before concurrent tasks could edit the same
checkout. It creates a dedicated `agent/TASK` branch below the user's private
Basaltwater worktree root and never modifies the primary checkout's files.

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

## Integration

Commit and validate inside the returned worktree. When merging or pushing is
requested, inspect the destination branch and integrate the task's commits
without overwriting unrelated work. Push the intended destination explicitly
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
