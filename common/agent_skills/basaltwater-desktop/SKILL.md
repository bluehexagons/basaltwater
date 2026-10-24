---
name: basaltwater-desktop
description: Operate native applications or a configured browser in the shared XRDP desktop.
metadata:
  managed-by: basaltwater
---

# Shared desktop

One setup-account XRDP/Xorg session is shared with the human and persists through
client disconnection. Check `basaltw desktop status` for its current state.
Discover only the needed application, for example
`basaltw agent manifest . --application blender --json`; presence is not readiness.

Prefer repeatable application scripts/exports for bulk edits, then verify saved
artifacts and the relevant UI result. Use exact PID/name/role/root filters for
controls; add `--save-output` to broad observations to retain full JSON privately.
Keep one worker in control of the shared desktop. Delegate independent script,
file, or export checks with `basaltwater-subagents` when useful.

| Task | Read |
| --- | --- |
| Launch, readiness, handoff, or logout | [Session lifecycle](references/session.md) |
| Accessibility inspection, screenshots, or input | [Observe and interact](references/interaction.md) |
| Window placement or short action sequences | [Window controls](references/windows.md) |
| Application/tool prerequisites or rendering evidence | [Applications](references/applications.md) |
| Blender edit/save/reopen | [Blender](references/blender.md) |
| Vector, raster, audio, or video touch-ups | [Media](references/media.md) |

Use the configured account's managed commands, which supply display and bus
environment. Never guess a display, copy authority cookies, or start another X
server. When `status.agent_control` is `enabled`, perform authorized desktop work
without asking to unpause or obtaining a control lease. `human-override` and
`disabled` block mutations. Only explicit human approval or the human control UI
authorizes a state change; a desktop task alone does not. A timed override's
return to enabled is approved when the human chooses its timer.

Readiness-only work uses status/doctor. Launches, captures, and input require the
task scope, affect the shared session, and must target the intended application.
Inspect current identity/geometry, verify outcomes, and preserve unrelated windows.

For browser work, follow session policy and the installed browser skill.
After confirmed T3/tool unavailability permits fallback, continue here when this
configured browser fits the test. Do not ask the user to reopen T3 or restart a
running desktop. Profiles/logins are separate, and pixel evidence does not prove
DOM/network assertions. Leave the session running unless logout is requested.
