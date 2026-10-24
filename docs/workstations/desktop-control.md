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

Closing the window, selecting observation, losing a browser connection, stopping
the native helper, or restarting a backend never changes this durable permission.
The browser's input ownership can expire independently. After a timed override
ends, browser input stops and agents can continue. Indefinite override and disabled
have no automatic return to enabled. Ordinary physical or RDP input does not
detect human activity automatically; use the control UI before intervening.

Agents should inspect `agent_control` in `desktop status`. A request to use
desktop software authorizes that task when enabled; it does not authorize a
transition out of disabled or human override. In those states, report the block
or wait for an existing timer. Ask to change the state only when it is needed
and the human has not already explicitly approved that transition. Never add an
approval flag simply to work around a blocked task.

For an explicitly approved change, use the configured backend:

```bash
# CachyOS; omit --native on a shared Debian desktop.
basaltw desktop --native control human-override --human-approved
basaltw desktop --native control human-override --seconds 3600 --human-approved
basaltw desktop --native control human-override --indefinite --human-approved
basaltw desktop --native control enabled --human-approved
basaltw desktop --native control disabled --human-approved
```

Timed overrides accept 60–86400 seconds. State controls work while the backend
is stopped; enabling agents does not start a desktop or grant KDE portal access.
Backend health and permission are displayed separately. Native portal consent
still requires the owner's monitor/device approval when KDE requests it.
Native helpers now run without a lifetime limit by default; an explicit
`start --session-seconds 3600` or `renew --generation GEN --seconds 3600` can
bound the backend lifetime. `0` removes that limit. Backend expiration leaves
agent permission unchanged.

State lives in `~/.local/state/basaltwater/desktop-control/state.json`, with a
0700 directory, 0600 file, atomic writes, and a lock for concurrent updates.
Invalid or unsafe records fail closed rather than silently enabling agents.
Each transition changes `control_revision`; managed input and sequences stop
when a human intervenes, even if agents are subsequently enabled again. Existing
session, geometry, observed identity, and bounded-operation checks still apply.

These controls coordinate trusted programs sharing the desktop account. The CLI
approval flag records the caller's explicit human authorization; it cannot prove
who typed a command or isolate hostile programs using the same UID. In-flight
capture/accessibility operations can finish before a human request is processed;
this is not compositor-level emergency isolation. Setup can request normal XRDP
logout and block further input during maintenance without changing permission.

Rerun setup to install the new runtime, UI, and skill guidance. Existing XRDP
supervisors require normal logout; existing native helpers require stop/start
to load new code. Save work first. Setup retains the durable control record.
