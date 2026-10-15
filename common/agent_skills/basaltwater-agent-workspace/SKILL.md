---
name: basaltwater-agent-workspace
description: Manage owned Git worktrees and shared project notes or todos for long-running agent work on a Basaltwater VM.
metadata:
  managed-by: basaltwater
---

# Agent workspaces

For general repository workflow and local project instructions, read the
installed `basaltwater-project-development` skill.

Use managed worktrees when concurrent editors could touch the same checkout.
Read-only review can use the primary checkout; checks generating files are edits.
Delegate only within the session's authority and when independent scopes justify it.

Read [workspace commands and lifecycle](references/workspaces.md) before creation,
adoption, integration, or removal. Use the installed subagent skill for bounded
assignments and model selection only when delegation is part of the task.

For durable project notes, shared todos, and private secret-file references,
read [project memory](references/project-memory.md) when that context is needed.
Load a bounded index and selected records, not the entire store on every task.

The parent owns file assignments, review, combined validation, integration, and
cleanup. Workers use the returned absolute worktree path and report commits,
changed files, checks, and unresolved concerns. Preserve unrelated changes.

Merge task branches when authorized so cleanup can verify ancestry. Inspect and
preview removal; never discard a dirty, unmerged, or unowned worktree to make
cleanup succeed. Worktree metadata can lie outside a task's writable sandbox.
