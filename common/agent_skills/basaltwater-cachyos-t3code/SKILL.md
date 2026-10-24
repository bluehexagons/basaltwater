---
name: basaltwater-cachyos-t3code
description: Operate T3 Code desktop or the managed web service and T3 Connect on a Basaltwater CachyOS coding workstation.
metadata:
  managed-by: basaltwater
---

# Local T3 Code and T3 Connect

Use the existing desktop account and selected T3 mode. Inspect the selected
provider's readiness; missing optional providers do not justify installing them.
Verify an authenticated provider turn and terminal in T3; HTTP reachability,
package presence, or an accepted refresh job does not establish readiness.

## Choose the mode

Read only the relevant reference before installation, updates, or repair:

| Selected environment | Workflow |
| --- | --- |
| Packaged `t3code-bin`, default data at `~/.t3` | [Desktop setup, AUR updates, login startup and graceful reload](references/desktop.md) |
| `basaltwater-cachyos-t3.service`, data at `~/.local/share/basaltwater/cachyos-t3/data` | [Web service, pairing, Connect and activation recovery](references/web-service.md) |

Desktop mode disables the managed web service; an inactive service is expected.
Use desktop Settings → Connections for desktop pairing/Connect. The web CLI
operates on separate data. Keep history, credentials and package ownership intact;
do not start another backend to repair a working desktop.

Finish active work before setup/refresh: desktop setup queues a graceful app
reload; web setup restarts its service. To switch desktop → web, quit the desktop
to free its port, then use `--web-interface t3code`. To switch back, select
`--t3code-desktop`. These flags are mutually exclusive. Omitting them leaves the
mode alone; switching does not migrate data. Successful web activation removes
managed desktop autostart; failed activation preserves it.

## Agent and browser workflows

Follow live session orchestration instructions. Use `basaltwater-subagents` for
provider/model selection, child tasks, worktree binding and native schedules.
A shell directory change does not rebind T3. This profile does not provision
the independent Basaltwater task worker; keep one owner per schedule/worktree.

Use `basaltwater-t3-preview-testing` for browser checks and the active session's
preview policy. This profile installs neither managed Playwright nor the VM
gateway/device-pairing helpers. Use `basaltwater-cachyos-desktop` for task-scoped
portal consent and native control; T3 setup does not start desktop control.
Use the CachyOS references above for service management.
