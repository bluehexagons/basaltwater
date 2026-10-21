---
name: basaltwater-t3code
description: Operate and troubleshoot the managed T3 Code server, pairing flow, and server-side Git environment on a Basaltwater VM.
metadata:
  managed-by: basaltwater
---

# Managed T3 Code

Start server investigations with a scoped readiness check:

```bash
basaltw agent doctor --capability t3code --capability host --json
```

Treat service readiness and host pressure separately; use
`basaltwater-vm-triage` for host pressure or support snapshots. Inspect only
relevant result fields and failing checks before collecting logs. The server
is the upstream per-user `t3code.service`; inspect it without sudo.

Restarting it interrupts active T3 sessions: arrange the interruption with the
owner first. After setup refreshes skills, plugins, or MCP configuration, use
**Restart agent session** in T3's command palette after the current turn.
This per-thread action reloads provider configuration without a server restart.

Read only the route needed:

- **Service/logs, pairing, or Git:** [operations](references/operations.md).
  Pairing credentials, cookies, and GitHub tokens must stay out of shared
  output. Startup filtering does not sanitize every application/provider log.
- **Update, launcher migration, native-module repair:** [updates](references/updates.md).
  Prefer the connected client's **Update server** action for a deliberate
  update. Setup reruns reconcile stable T3 and selected providers. Before a
  first upgrade from pre-0.1.0, also read [thread migration](references/thread-migration.md).
  Startup migration can take 30 minutes; inspect before interrupting/retrying.
- **Delegation/worktrees:** `basaltwater-subagents`. Use T3 workspace binding
  before starting editors; shell `cd` does not rebind a thread.
- **Scheduled work:** `basaltwater-agent-tasks` chooses T3 recurring work versus
  independent Codex tasks. Use one scheduler per job; independent runs do not
  inherit this thread's tools/browser.
- **Maintenance holds/recorded updates:** `basaltwater-agent-operations`.
  Holds are shared: do not release another task's hold. Unattended inspection
  must not create or release holds.
- **Browser:** the installed `basaltwater-browser-testing` or
  `basaltwater-t3-preview-testing` skill. Explicit
  `net::ERR_CERT_AUTHORITY_INVALID` identifies client trust; enrollment is
  optional. Keep TLS intact and use the browser skill's recovery procedure.

**No project** folders under `~/.t3/scratch` are retained user files, not cache.
Use a real project/worktree for repository changes; do not move T3's data or
relax Codex policy to enable projectless work.

When exposed, register every PR created or worked on with `link_pull_request`
using its full URL, including each stack layer. Reconcile
`list_thread_pull_requests` before finishing; CLI Git alone does not link PRs.
