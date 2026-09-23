---
name: basaltwater-godot-web
description: Export, publish, and browser-test Godot web games on a Basaltwater-managed VM.
metadata:
  managed-by: basaltwater
---

# Godot web workflow

Use the VM's shared Nginx HTTPS origin on TCP 8443 for static Godot web
exports. The game does not need its own listener or firewall rule. Do not start
a public plain-HTTP server or edit Nginx and UFW directly.

## Publish

1. Run `basaltw agent doctor --capability development --json` and confirm
   the Godot toolchain is healthy and reports `web_templates: true`.
2. Confirm the repository contains `project.godot` and an appropriate Web
   export preset in `export_presets.cfg`.
3. Run `godot --headless --path . --import` when imports need to be completed
   before export; it waits for resource imports before quitting.
4. From the project root, run `basaltwater-web publish godot --json`. It derives a
   stable game slug from the project and sends Godot progress and diagnostics
   to stderr, leaving stdout as JSON. Pass an explicit slug, `--preset`, or `--debug` only when
   the task requires them. The result reports the URL, elapsed time, artifact
   sizes, and whether it replaced an earlier publication.
5. Replace `GAME` below with the lowercase `game` value returned by `publish`:

   ```bash
   basaltwater-web url GAME
   basaltwater-web doctor GAME
   ```

   Treat the returned URL as authoritative. Do not substitute a local port or
   bypass certificate verification with `curl -k` or an equivalent option.
6. Use the capability-specific browser skill installed on the VM. Prefer
   VM-local Playwright for repeatable canvas and console checks when
   collaboration is unnecessary; use T3 preview for shared, client-origin
   evidence. Confirm that the game canvas initializes without browser console
   errors.

Published games are independent and remain available under their stable URLs.
Use `basaltwater-web list` to inspect them. Remove one only when explicitly requested,
using `basaltwater-web remove GAME --yes`.

Setup enrolls the VM-local CA in the managed user's system and VM-local
Chromium trust stores. A collaborative T3 preview may run on the connected
client and therefore does not necessarily share that trust store. If only the
collaborative preview fails, inspect its snapshot and network error. For an
explicit `ERR_CERT_AUTHORITY_INVALID`, follow the installed T3-capable browser
skill. Client CA enrollment is optional: use VM-local Playwright when
available, or skip the collaborative browser layer and continue VM checks.
Offer the verified public CA URL and fingerprint only when the user wants
preview access restored. If VM-local automation fails trust, report the failed
capability; rerun the saved setup only when the task includes repairing managed
trust. Never suppress HTTPS errors.

## Real-time and physics checks

Read the project's input map and pause implementation before interacting.
Pause immediately after a short gameplay action, verify that simulation and
gameplay timers stop, then capture and inspect while paused. Resume for the next
bounded action. Canvas fallback text is not proof of a blank frame; use the
browser skill's screenshot and coordinate workflow. Paused images establish
appearance, not movement quality or inactivity timing.

When physics feel is in scope, complement screenshots with a project-owned,
opt-in debug trace. Record simulation ticks/time, stable body IDs, positions,
linear/angular velocities, link distance versus rest length, and gameplay
timers. Capture contact impulses in the body's existing `_integrate_forces`
callback; keep them distinct from scripted impulses and enable a bounded
`max_contacts_reported`. Sample positions at a modest rate, but accumulate
collision peaks every physics tick so short impacts are not missed.

Bound the trace by duration, body count, and sample count. Export JSON once
through a prefixed console message or a debug-only `JavaScriptBridge` window
property, then read it through the browser tools without mutating game state.
Start each capture with a unique run ID and cleared previous output. Preserve
numeric precision and explicitly flag non-finite physics values before JSON
serialization. Aggregate and publish on the main thread; use a synchronized
handoff if physics callbacks run on a separate thread.
Do not assume the canvas exposes Godot objects to JavaScript. Compare a fixed
seed/input scenario with tolerances and retain the engine/backend, timestep,
revision, and debug/release mode. Verify the normal release without tracing.
The [physics testing recipe](https://github.com/bluehexagons/basaltwater/blob/main/docs/GODOT.md#browser-and-physics-testing)
has a bounded capture example and measurement guidance.

## Live previews

Prefer static publication for normal Godot exports. If a task genuinely needs
a long-running local preview server, bind it to `127.0.0.1` on an unprivileged
port and use the `basaltwater-web-gateway` skill to expose it through managed
HTTPS with the `godot` profile.

The preview's loopback port remains private. `basaltwater-web` allocates a separate
Nginx HTTPS listener and applies UFW only to that listener; always report the
URL returned by `basaltwater-web`, not the loopback address.

Godot threaded and web GDExtension exports need the managed cross-origin
isolation headers. External assets used by such a game must also satisfy the
browser's cross-origin isolation rules.
