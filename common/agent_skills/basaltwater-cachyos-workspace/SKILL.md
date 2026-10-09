---
name: basaltwater-cachyos-workspace
description: Isolate concurrent coding work in managed Git worktrees on a CachyOS workstation, preserving the human user's checkout.
metadata:
  managed-by: basaltwater
---

# Local coding workspaces

Use the existing human account. Repositories default to `~/repos`, or the path
selected with `--agent-workspace`; discover the actual project before editing.
Setup clones only missing repositories and never pulls or resets an existing one.

Agents otherwise share the filesystem. A read-only reviewer can inspect the
primary checkout; each editing agent needs a separate worktree and explicit
file ownership. Assign shared configuration and lockfiles to one owner. The
parent owns integration, combined validation, and cleanup. Each editor returns
its worktree, branch, base and final commit, changed files, validation results,
and unresolved concerns.

Choose branch merges before delegating; cherry-pick and squash leave the source
commits outside the ancestry required by cleanup. Small, tightly coupled tasks
often cost more to delegate than they save. See the
[subagent workflow guide](https://github.com/bluehexagons/basaltwater/blob/main/docs/AGENT_SUBAGENTS.md)
for the complete lifecycle and project model-selection commands. Use OpenAI
Luna for simple classification, Sol for coding, and Astra only with a concrete
cost justification. Use actual session capabilities and reviewed project
outcomes to adapt to new model releases. Do not autonomously choose fast mode,
`max`, or `ultra`; explicit user selections govern exceptions.

When concurrent work needs isolation, use the shared managed-worktree commands:

```fish
basaltw agent workspace create ~/repos/PROJECT TASK --base HEAD --json
basaltw agent workspace list ~/repos/PROJECT --json
basaltw agent workspace status WORKTREE --json
```

Work in the returned directory. `HEAD` uses the local checkout's commit; fetch
and use `--base origin/main` when the task needs current remote main. Preserve
unrelated work, and integrate or push only within the user's requested scope.

Inspect status before cleanup, then preview removal:

```fish
basaltw agent workspace remove WORKTREE --dry-run --json
basaltw agent workspace remove WORKTREE --json
```

Removal refuses dirty, unmerged, or unmanaged worktrees. Resolve the reported
condition without discarding user work. This workflow needs no SSH host, VM
provisioning, or saved server setup.
