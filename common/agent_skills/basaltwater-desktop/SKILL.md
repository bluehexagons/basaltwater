---
name: basaltwater-desktop
description: Operate native applications or a configured browser in the shared XRDP desktop.
metadata:
  managed-by: basaltwater
---

# Shared desktop

One setup-account XRDP/Xorg session is shared with the human and persists through
client disconnection. Check `basaltw desktop status` for its current state.
Discover installed applications with the project manifest; presence is not readiness.

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
server. Honor human pause; never resume it automatically or bypass denied access.

Readiness-only work uses status/doctor. Launches, captures, and input require the
task scope, affect the shared session, and must target the intended application.
Inspect current identity/geometry, verify outcomes, and preserve unrelated windows.

For browser work, follow session policy and the installed browser skill.
After confirmed T3/tool unavailability permits fallback, continue here when this
configured browser fits the test. Do not ask the user to reopen T3 or restart a
running desktop. Profiles/logins are separate, and pixel evidence does not prove
DOM/network assertions. Leave the session running unless logout is requested.
