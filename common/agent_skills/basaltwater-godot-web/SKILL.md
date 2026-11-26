---
name: basaltwater-godot-web
description: Export Godot browser games, publish them as managed Basaltwater sites, and verify the served build or browser behavior.
metadata:
  managed-by: basaltwater
---

# Godot web workflow

Use the VM's shared Nginx HTTPS origin on TCP 8443 for static Godot web
exports. The game does not need its own listener or firewall rule. Do not start
a public plain-HTTP server or edit Nginx and UFW directly.

Export validation and publication are separate scopes. A local-only or unattended
export check should use the project's documented headless export command and
ignored output directory, without running the publication steps below. Report
missing presets or export templates instead of downloading or installing them
unless that setup work is explicitly requested.

## Publish

1. Run `basaltw agent doctor --capability development --json` and confirm
   the Godot toolchain is healthy and reports `web_templates: true`.
2. Confirm the repository contains `project.godot` and an appropriate Web
   export preset in `export_presets.cfg`.
3. Run `godot --headless --path . --import` when imports need to be completed
   before export; it waits for resource imports before quitting.
4. From the project root, run `basaltwater-web publish site --profile godot --json`.
   For an existing publication, pass its recorded site name explicitly. Check
   `basaltwater-web site list --json` when a migrated game may have received a
   new name. Reuse the configured preset and build mode for a development
   refresh. Godot progress goes to stderr and the JSON result includes the
   site name, URL, artifact sizes, and whether it replaced a snapshot.

   To publish an already exported build containing `index.html`, `index.js`,
   `index.wasm`, and `index.pck`, supply the export directory instead:

   ```bash
   basaltwater-web publish site GAME --profile godot --no-build --output exports/web --json
   ```

5. Replace `GAME` below with the lowercase `site` value returned by `publish`:

   ```bash
   basaltwater-web site url GAME
   basaltwater-web site doctor GAME
   basaltwater-web site verify GAME --project . --json
   ```

   The doctor checks HTTPS, isolation headers, and WASM MIME type. Verification
   compares the snapshot and served bytes and uses the recorded directory when
   checking a prebuilt export. For a separate local export of a site built by
   the publisher, add `--output DIR`. Use the returned URL and keep TLS
   verification enabled.
6. For rendered verification, use the capability-specific browser skill and the
   active session's browser policy. In T3 sessions with `preview_*` tools, use
   status/open and that skill's fallback conditions before selecting Playwright.
   Outside those sessions, VM-local Playwright suits repeatable canvas and console
   checks; T3 preview supplies shared client-origin evidence. Confirm that the
   game canvas initializes without browser console errors.

Published games are independent and remain available under their stable URLs.
Use `basaltwater-web site list` to inspect them. Remove one only when explicitly requested,
using `basaltwater-web site remove GAME --yes`.

Setup enrolls the VM-local CA in the managed user's system and VM-local
Chromium trust stores. A collaborative T3 preview may run on the connected
client and therefore does not necessarily share that trust store. If only the
collaborative preview fails, inspect its snapshot and network error. For an
explicit `ERR_CERT_AUTHORITY_INVALID`, follow the installed T3-capable browser
skill. Client CA enrollment is optional: use VM-local Playwright when
available and the session permits fallback, or skip the collaborative browser
layer and continue VM checks.
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
The [physics testing recipe](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/godot.md#browser-and-physics-testing)
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
