# Agent desktop control

Basaltwater remembers one human-selected agent control state per desktop account
across helper restarts, logout, reboot, and setup reruns. The same choice applies
to native CachyOS control, development launches, and the shared XRDP desktop.

| State | Agent behavior | How it ends |
| --- | --- | --- |
| Enabled | Perform authorized desktop tasks without asking to unpause or obtaining a control lease | Explicit human state change |
| Human override | Block agent launches, input, and accessibility mutations; allow observation | Approved timer expires, or explicit human state change |
| Disabled | Keep agent desktop mutations off | Explicit human state change |

New accounts default to enabled. A previously remembered native pause migrates
once to indefinite human override, preserving the existing human decision.

The control window offers **Take control** with a 15-minute default, a duration
choice, and an indefinite option. **Enable agents** ends an override or disabled
state explicitly; **Disable agents** keeps agents off until another human choice.
The web desktop provides the same choices on agent systems. A timed override
stores an absolute deadline: rebooting or reconnecting does not restart its timer.
Open **Agent Desktop Control** from KDE's application menu on CachyOS, or
**Shared Desktop Control** on XRDP. These launchers also work while agents are
blocked; they do not change permission until the human selects an action.
Its return to enabled is approved when the human chooses that timer.

## Handoff is not a permission change

From the local KDE terminal, `basaltw desktop --native handoff` opens the same
control window even with a stopped backend or blocked agents. Opening the
window does not start a portal, transfer input ownership or change permission.
The reply acknowledges a spawn request; visibility is unverified until observed.
These are three separate actions:

1. Open the human control window with `handoff`.
2. Relinquish agent ownership by stopping your own action loop and handing over
   the current application/task/evidence state. Opening a window does not stop
   another worker or an already-running sequence.
3. On CachyOS, leave persistent agent permission to the human's choice in the
   KDE control window.

Use `basaltw desktop --native handoff --json` on CachyOS, with `--native` before
`handoff`. Bare `basaltw desktop handoff` selects Debian XRDP even on KDE; “Shared
desktop is not configured” refers to that backend, not a missing KDE capability.
Inspect the invoked installation/source if the explicit native route is missing;
do not provision XRDP, enable agents, start a portal or restart a match to open
the window. On Debian use `basaltw desktop handoff`; its dedicated supervisor
action opens only the fixed control UI, including while agent permission is
blocked. It requires an existing running XRDP desktop and does not start one.
An older supervisor may refuse this action: use the **Shared Desktop Control**
launcher and load the update at a safe logout checkpoint. Never enable agents
just to open the window. Windows uses its distinct
[`desktop control`](windows-desktop.md) UI and pause contract.

Closing the window, selecting observation, losing a browser connection, stopping
the native helper, or restarting a backend never changes this durable permission.
The browser's input ownership can expire independently. After a timed override
ends, browser input stops and agents can continue. Indefinite override and disabled
have no automatic return to enabled. Ordinary physical or RDP input does not
detect human activity automatically; use the control UI before intervening.

Agents should inspect `agent_control` in `desktop status`. A request to use
desktop software authorizes that task when enabled; it does not authorize a
transition out of disabled or human override. In those states, report the block
or wait for an existing timer. On CachyOS, the operator can select a different
permission in the KDE control window if the task requires it.

On native CachyOS, both the CLI and the helper socket refuse permission changes.
Open **Agent Desktop Control** from the KDE application menu or run
`basaltw desktop --native handoff` and have the operator choose the state.
The window works while the backend is stopped.

On Debian XRDP, an explicitly approved change can still use the CLI.

```bash
basaltw desktop control human-override --human-approved
basaltw desktop control human-override --seconds 3600 --human-approved
basaltw desktop control human-override --indefinite --human-approved
basaltw desktop control enabled --human-approved
basaltw desktop control disabled --human-approved
```

Debian CLI timed overrides accept 60–86400 seconds. Backend health and
permission are displayed separately. Enabling agents leaves backend startup
and any required KDE portal approval as separate steps.
Native helpers now run without a lifetime limit by default; an explicit
`start --session-seconds 3600` or `renew --generation GEN --seconds 3600` can
bound the backend lifetime. `0` removes that limit. Backend expiration leaves
agent permission unchanged.

State lives in `~/.local/state/basaltwater/desktop-control/state.json`, with a
0700 directory, 0600 file, atomic writes, and a lock for concurrent updates.
Invalid or unsafe records fail closed rather than silently enabling agents.
Each transition changes `control_revision`; managed input and sequences stop
when a human intervenes, even if agents are subsequently enabled again.
Accessibility workers recheck this revision after lookup and immediately before
invoking an action, replacing text, or requesting focus. Existing
session, geometry, observed identity, and bounded-operation checks still apply.

These controls coordinate trusted programs sharing the desktop account.
The native control window prevents a command-line approval claim from changing
permission through the native API. Programs that share the desktop user's
identity are still in the same trust boundary. In-flight
capture/accessibility operations can finish before a human request is processed;
this is not compositor-level emergency isolation. Setup can request normal XRDP
logout and block further input during maintenance without changing permission.

Rerun setup to install the new runtime, UI, and skill guidance. Existing XRDP
supervisors require normal logout; existing native helpers require stop/start
to load new code. Save work first. Setup retains the durable control record.

Debian setup applies browser defaults, XFCE compatibility and theme settings,
and LXQt theme settings as the desktop owner. Managed settings require real
directories and regular files at their destinations. If an older setup left
an unwritable directory, inspect the path and have an administrator correct
ownership of the affected directory before rerunning. Keep unrelated home
files and their ownership intact.
