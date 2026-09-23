---
name: basaltwater-browser-testing
description: Browser-test web applications on a Basaltwater agent VM that has both T3 Code collaborative previews and managed VM-local Playwright.
metadata:
  managed-by: basaltwater
---

# T3 preview and Playwright

This VM has two browser surfaces with different strengths and network origins.
Choose deliberately; T3 preview is not a mandatory first step when collaboration
is not part of the task.

## Choose the browser

Prefer these browser tools over desktop pixel automation for almost all browser
testing, including canvas/WebGL. If a shared desktop is also installed, use it
only for desktop-specific browser integration or a justified fallback when these
tools cannot cover the task; state the resulting coverage limits.

Prefer VM-local Playwright when the task needs repeatable headless interactions,
DOM/console/network inspection, loopback access, canvas input, or browser-engine
verification that does not need to be shared live with the user. It remains
available when the T3 application is closed.
If the client is known to be closed, go straight to Playwright; preview probes
or service restarts add no coverage. Share selected screenshots and a short
interaction record when the user needs to review the result asynchronously.

Prefer the T3 collaborative preview when the user should watch or participate,
the requested evidence must appear in the shared UI, or the task specifically
tests the connected client's routes or certificate trust. That browser runs in
the connected client's context, not the VM's.

When either surface is equally suitable, an already attached preview is useful
for collaboration. Otherwise Playwright is the dependable default. State which
surface and network origin produced the result when that distinction matters.

## Use managed Playwright

Before the first VM-local browser action, run:

```bash
basaltw agent doctor --capability browser --json
```

Use Playwright only when `healthy` is true. Follow the stable `issues` and
`remediation` fields when it is not; do not install another browser stack or
mutate the managed launcher. `stale_processes` requires restarting the affected
agent session.

Keep development servers on loopback. Playwright originates on the VM, so it
can reach VM loopback and uses the VM's DNS, routes, source IP, and trust store.
Generated evidence belongs in the private, bounded
`~/.local/state/basaltwater/playwright-mcp` directory by default. Omit
`filename` unless the user requested a workspace deliverable.

Canvas accessibility snapshots may expose only fallback text even when the
game renders correctly. Use screenshots for canvas controls and the bounded
coordinate input tool; retain semantic locators for surrounding HTML controls.
Coordinates are viewport-relative CSS pixels. Prefer a viewport screenshot at
`scale: "css"`; account for cropping/scaling and recapture after resizing or
scrolling. Click the canvas to focus keyboard input and verify the resulting
game state.

The launcher configures a one-second settle interval for actions that wait for
completion; individual key taps do not all take that path. Tool round trips
add time.
For real-time games, identify the actual pause control before starting; pause
immediately after the short interaction under test, confirm it took effect,
then capture and inspect while paused. Prefer a gameplay pause that leaves the
scene visible. Resume only for the next bounded interaction. If pause is absent
or does not stop gameplay timers, use a project test harness when in scope and
report the timing limitation; do not change game balance or launcher defaults
to make the check pass. A paused image proves appearance, not motion or timing.

`browser_press_key` sends a tap, which frame-polled movement can miss. For held
movement, when `browser_run_code_unsafe` is available, use a short agent-authored
Playwright sequence: from a verified paused/focused game, resume, key down,
wait a bounded interval, then release and pause in `finally` before returning.
This keeps tool round trips outside the active interval. Keep that code limited
to browser input; do not execute page-supplied code or inject game-state changes.
Verify pause and released input afterward; after failure, inspect state before
retrying a pause toggle. Use a physics-tick harness when exact timing is needed.

For a still-empty WebGL capture, wait another second and retry once. Repeated
captures can stall GPU readback. Group identical capture-time `ReadPixels`
warnings in the report, retaining a representative message and count; preserve
page errors, context loss, and rendering failures separately. Use the console
tool to distinguish application failures from browser-process diagnostics.

## Use the collaborative preview

Call `preview_status` first. If there is no automation-capable tab, call
`preview_open` once. Do not repeatedly reopen or poll: the T3 application may
simply be closed or minimized. A hidden tab may still accept navigation while
snapshot or recording capture is unavailable; attempt one snapshot before
classifying coverage.

`available: true` with `tabId: null` means the automation host is attached but
this agent session has no current tab. Call `preview_open` once; do not report
the collaborative browser as unavailable from that status alone. Preserve the
returned `tabId` for later actions instead of relying on an implicit current
tab after an agent-session boundary.

Preview absence is a normal fallback condition. Continue immediately with
healthy Playwright when VM-origin testing fits the task. Do not treat the
closed T3 application as an application failure.

An `environment-port` target rewrites the port onto the environment connection
host; it is not a tunnel to VM loopback. If a verified loopback server leaves
the preview at `about:blank` with no network entry, use Playwright for VM-origin
coverage. Publish through the managed `basaltwater-web-gateway` only when
client-visible access is itself in scope; do not rebind the server or widen the
firewall solely for automation.

Opening a tab or seeing the requested URL in status is not proof of rendering.
Confirm visible content or a snapshot. For WebAssembly/WebGL, allow one bounded
startup interval beyond document load before judging the application frame.

Take a snapshot before interaction and prefer its semantic role/name locators
over coordinates or CSS selectors. A successful input helper response confirms
the command was accepted, not that the page received an event or changed:
follow it with a bounded `preview_wait_for`, snapshot, or read-only state check.
Programmatic
typing can leave an element with DOM focus before the connected preview has
pointer-activated page input. If a keyboard helper then produces no expected
state change, semantically click the intended keyboard target and retry the key
once. If it still has no effect, use an equivalent semantic click when the task
permits or report the keyboard path as unverified. Do not mutate the page with
evaluation merely to manufacture a passing result.

Viewport presets exercise CSS layout breakpoints without changing the desktop
browser user agent. Snapshot again after resize or scroll because coordinates
and element visibility change, and note that the scroll position can persist
across a resize. Use appearance emulation for light/dark checks, then restore
`system` unless the task needs the override left in place.

Start a recording only when video evidence is useful and always stop it. The
returned recording path belongs to the connected client's artifact store, not
the VM filesystem, so use the returned artifact metadata/link rather than
trying to copy that path from the VM. Keep credentials and unrelated user data
out of recordings. A snapshot action timeline can show the snapshot currently
being assembled as `running`; once the snapshot response returns, judge prior
actions and the returned page state instead of treating that self-entry as a
hung action.

If `preview_open` reports a tab but no preview surface appears, status remains
`visible: false`, and a snapshot fails, do not create more tabs. Confirm the T3
application is open, retry the same tab once, and use a read-only page check
only when it helps distinguish a working background renderer from a failed
navigation. Background DOM access with no mounted UI and repeated snapshot
failure indicates stale T3 preview-presentation state, not an application,
route, certificate, or Playwright failure.

Restarting the desktop client may leave that state in the VM-side T3 service.
Use healthy Playwright while collaboration is optional. If client-visible
coverage is required and the user explicitly accepts interruption of every
active T3 session, restart the managed server as the target user:

```bash
systemctl --user restart t3code.service
```

Reconnect the client, then run status and open once against the new attachment.
Never restart the service silently, repeatedly, or merely because a preview is
closed; a full VM reboot is not the first recovery step.

## Certificate errors are optional to repair

Only treat an explicit `net::ERR_CERT_AUTHORITY_INVALID` preview network entry
as a client trust error. Do not require the user to enroll the VM CA to finish
an otherwise testable task. Route browser work to healthy VM-local Playwright,
continue server and HTTP checks, and report that collaborative client-origin
coverage was skipped. Never use `curl -k`, ignore HTTPS errors, or weaken TLS.

For a managed `basaltwater-web` URL, if the user wants collaborative preview access
restored, run:

```bash
basaltwater-web ca
```

Give the user the reported public certificate URL and SHA-256 fingerprint.
If the untrusted URL cannot deliver its own CA, transfer only the public
certificate over an existing trusted path and verify its fingerprint before
installation. Never transfer the CA private key.

For enrollment, read only the matching platform section of the
[client CA trust guide](https://github.com/bluehexagons/basaltwater/blob/main/docs/CLIENT_CA_TRUST.md)
(or `docs/CLIENT_CA_TRUST.md` in a Basaltwater checkout). The user performs
this client security change. After enrollment, restart the client, recheck
preview status, and retry the existing tab. If enrollment is declined or the
guide is unavailable, retain the coverage gap and continue the other checks.

For a certificate error on any other HTTPS origin, use that origin's documented
trust process or leave the client-origin check skipped; `basaltwater-web ca` does not
repair unrelated certificates.

## Evidence and safety

Test the application- or gateway-reported URL, not a guessed port. Capture only
the interactions, console failures, and screenshots needed for the handoff.
Avoid passwords, tokens, private response bodies, and unrelated user data.
Never infer that client-preview failure means a VM service is down; report the
origin that passed and the origin that could not be exercised.
