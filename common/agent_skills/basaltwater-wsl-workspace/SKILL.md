---
name: basaltwater-wsl-workspace
description: Locate Basaltwater guidance and isolate coding or build work in an Ubuntu WSL distribution, including T3 Code connections from Windows.
metadata:
  managed-by: basaltwater
---

# Ubuntu WSL coding work

Run Linux tools as the configured non-root Ubuntu user in the selected WSL
distribution. Keep repositories, worktrees, dependencies, and Linux build output
on its native filesystem, usually under `~/repos`, rather than `/mnt/c`.
The installed `basaltw` launcher provides workspace and project model helpers;
this profile does not provision Debian VM maintenance, a panel, a gateway, XRDP,
or managed browser automation.

```bash
basaltw agent workspace create ~/repos/PROJECT TASK --base HEAD --json
basaltw agent workspace status WORKTREE_PATH --json
basaltw agent manifest ~/repos/PROJECT --json
```

For authorized delegation, use the installed `basaltwater-subagents` skill and
its local lifecycle and session-context references. Give editors their returned
worktree paths; the parent owns integration and cleanup. Read-only reviewers can
inspect the primary checkout. User skills live under `~/.agents/skills` for
Codex/OpenCode or `~/.claude/skills` for Claude Code inside Ubuntu.

In T3 Code on Windows, select the intended Ubuntu distribution in Connections.
The provider must run as this Linux account to discover its skills. A native
Windows provider has a separate home and does not discover Ubuntu's catalog.
Use preview tools only when the active T3 session exposes them; another session
or a desktop installation does not supply tools to a terminal agent.

Logout, sleep, reboot, or WSL shutdown can interrupt work despite the optional
login keepalive. Do not stop or refresh the distribution during active work.
Refresh guidance by rerunning Windows setup from an updated pinned Basaltwater
source; a tool-only update does not refresh skills. Do not infer host capability
or health from a manifest's generic non-CachyOS profile label. Native PowerShell
artifact jobs are a separate workflow, not Basaltwater Codex prompt tasks.
