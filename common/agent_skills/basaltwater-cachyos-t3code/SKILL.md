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
Use desktop Settings → Sharing & access for desktop pairing/Connect (Connections
in older clients). The web CLI operates on separate data. Keep history,
credentials and package ownership intact; do not start another backend to
repair a working desktop.

Setup/refresh can run in a local or remote T3 terminal. Package review and sudo
prompts remain attached; disruptive T3 changes wait until all steps, saved state
and notifications finish, then run in an independent user worker. Inspect its
printed status/journal before treating queued completion as readiness. Desktop
mode reloads a running app or starts a stopped app when KDE is available;
otherwise startup waits for KDE login. Web mode activates and checks its staged
runtime there. Save active work before the final interruption. To switch desktop → web, quit the desktop
to free its port, then use `--web-interface t3code`. To switch back, select
`--t3code-desktop`. These flags are mutually exclusive. Omitting them leaves the
mode alone; switching does not migrate data. Successful web activation removes
managed desktop autostart; failed activation preserves it.

## Agent and browser workflows

Use `basaltw local cachyos-doctor` when diagnosing this host. For provider or
skill discovery problems, use the
[execution environment checks](../basaltwater-project-development/references/t3-workflows.md#establish-the-execution-environment).

Follow T3's active session instructions for thread work. This profile uses
T3's own scheduler. For browser tasks, use the installed
`basaltwater-t3-preview-testing` skill. Native desktop control follows
`basaltwater-cachyos-desktop` and its portal consent workflow. See
[KDE SnapShots](references/desktop.md#kde-snapshots) for the separate capture helper.
