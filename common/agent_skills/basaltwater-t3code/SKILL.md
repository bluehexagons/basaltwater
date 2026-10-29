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
owner first. Managed setup refresh reloads provider configuration and skills:
Debian restarts the service unless the updater already started it fresh;
CachyOS desktop queues a graceful relaunch of a running app and leaves stopped
apps stopped. Interrupted-turn continuation follows T3's existing setting.
For changes outside setup, use **Restart agent session** in T3's command palette
after the current turn to reload one thread without a server restart.

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
- **Linked environments:** [operations](references/operations.md#linked-environments)
  covers the opt-in beta, peer grants, reachability, and revocation. Remote
  delegation has different workspace constraints; use the subagent reference.
- **Scheduled work:** `basaltwater-agent-tasks` chooses T3 recurring work versus
  independent Codex tasks. Use one scheduler per job; independent runs do not
  inherit this thread's tools/browser.
- **Maintenance holds/recorded updates:** `basaltwater-agent-operations`.
  Holds are shared: do not release another task's hold. Unattended inspection
  must not create or release holds.
- **Browser:** the installed `basaltwater-browser-testing` or
  `basaltwater-t3-preview-testing` skill covers T3's own Chrome download and
  host-library/font/sandbox diagnostics. Explicit
  `net::ERR_CERT_AUTHORITY_INVALID` identifies the browser host's trust;
  distinguish server and desktop runtimes before offering optional client CA
  enrollment. Keep TLS intact and use the browser skill's recovery procedure.

**No project** folders under `~/.t3/scratch` are retained user files, not cache.
Use a real project/worktree for repository changes; do not move T3's data or
relax Codex policy to enable projectless work.

When exposed, register every PR created or worked on with `link_pull_request`
using its full URL, including each stack layer. Follow session instructions for
`list_thread_pull_requests`; otherwise list only when needed. CLI Git alone
does not link PRs. The subagent skill covers native watches and review guides;
project-development covers T3 actions and inline visual replies.
