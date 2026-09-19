---
name: infra-tools-cachyos-workspace
description: Isolate concurrent coding work in managed Git worktrees on a CachyOS workstation, preserving the human user's checkout.
metadata:
  managed-by: infra_tools
---

# Local coding workspaces

Use the existing human account. Repositories default to `~/repos`, or the path
selected with `--agent-workspace`; discover the actual project before editing.
Setup clones only missing repositories and never pulls or resets an existing one.

When concurrent work needs isolation, use the shared managed-worktree commands:

```bash
basaltw agent workspace create ~/repos/PROJECT TASK --base HEAD --json
basaltw agent workspace list ~/repos/PROJECT --json
basaltw agent workspace status WORKTREE --json
```

Work in the returned directory. `HEAD` uses the local checkout's commit; fetch
and use `--base origin/main` when the task needs current remote main. Preserve
unrelated work, and integrate or push only within the user's requested scope.

Inspect status before cleanup, then preview removal:

```bash
basaltw agent workspace remove WORKTREE --dry-run --json
basaltw agent workspace remove WORKTREE --json
```

Removal refuses dirty, unmerged, or unmanaged worktrees. Resolve the reported
condition without discarding user work. This workflow needs no SSH host, VM
provisioning, or saved server setup.
