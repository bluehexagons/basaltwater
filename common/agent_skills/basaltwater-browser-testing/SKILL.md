---
name: basaltwater-browser-testing
description: Browser-test web applications with T3 Code, managed Playwright, or a configured desktop browser, and continue testing when the collaborative client disconnects.
metadata:
  managed-by: basaltwater
---

# Browser testing and client disconnection

This VM has T3 preview and managed Playwright; a desktop browser may also be installed.
Follow the active session's browser policy when choosing between them.

## Choose the browser

Choose a ready option that covers the task, subject to any session policy:

| Browser | Good fit | Origin |
| --- | --- | --- |
| T3 collaborative preview | Live participation, shared evidence, connected-client routing or trust | Connected client |
| Managed Playwright | Repeatable DOM/console/network checks, loopback access, file fixtures, canvas/WebGL | VM |
| Configured desktop browser | Native dialogs, desktop integration, visual interaction, or an available alternative when automation tools are missing | VM, separate browser profile |

For a session requiring T3 first, call `preview_status`, then `preview_open`
when no automation-capable tab is attached. Use an alternative when preview
tools are absent, the user requests it, or open explicitly reports an
unsupported/unavailable host, including a disconnected or missing client.
A closed pane, navigation error, timeout, or TLS error alone does not establish
host unavailability; inspect actionable errors and retry with corrected arguments.
Outside such a policy, choose the available browser best suited to the task.

Once disconnection or unavailable tools permit fallback, continue automatically
with a ready configured alternative. Do not wait for the user to reopen T3,
restart T3 to restore automation, or ask permission merely to switch browser
within the already authorized test. Playwright and the VM desktop do not depend
on an open T3 application. See the readiness checks below and the installed
desktop skill for its control lifecycle. If one alternative is missing or
unhealthy, inspect the others before declaring browser coverage blocked.

If a connection is lost after an input, inspect application state before
repeating it; a failed acknowledgment may follow a successful submission.
Profiles and logins are separate: use the application's normal login flow with
authorized test credentials instead of copying cookies. Keep using the selected
alternative for the task instead of polling T3 before every action. Recheck T3
when the client returns or client-origin coverage is needed.

Share selected screenshots and observed interactions for asynchronous review.
State the browser and network origin; VM-origin success cannot verify the
disconnected client's routing, trust, or shared presentation. Report that
specific gap while completing the behavior the alternative can exercise.

## Use managed Playwright

For a small engine/context inventory, use `basaltw agent manifest REPOSITORY
--capability browser --json`. The managed capability doctor below qualifies
Chromium and its MCP integration. Project Playwright Firefox and interactive
desktop Firefox have separate readiness; discovery leaves rendering unverified.
When the task explicitly needs Firefox, use `basaltw agent browser doctor
--engine firefox --context project --project REPOSITORY --json` with the project's
existing dependencies. An authorized `browser install` with the same arguments
downloads only the matching engine, preserving packages and lockfiles.
Use `--context managed` without `--project` for the managed pinned package.
Neither command changes MCP's Chromium selection or the session browser policy.
See the [engine/context guide](https://github.com/bluehexagons/basaltwater/blob/main/docs/BROWSER_AUTOMATION.md#engine-and-context-coverage)
for dependency and coverage limits.

Before the first VM-local browser action, run:

```bash
basaltw agent doctor --capability browser --json
```

Use Playwright only when `healthy` is true. Follow the stable `issues` and
`remediation` fields when it is not; do not install another browser stack or
mutate the managed launcher. `stale_processes` requires restarting the affected
agent session.

A healthy installation does not establish that its MCP tools are exposed in this
agent session. Unattended panel prompts cannot use a connected T3 thread's tools.
Use only tools actually available to the run. If MCP tools are missing, inspect
configured project Playwright tests or the installed desktop workflow before
declaring browser coverage blocked. Project tests use their own existing
dependencies and documented command; do not bypass the managed launcher, load
its private packages directly, install another stack, or restart services to
make tools appear. If no usable alternative remains, continue non-browser checks
and report the exact missing coverage.

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

If a later preview action reports a disconnected/unavailable host, repeat this
bounded status/open check once, then automatically choose a ready alternative
when the session permits it. Do not treat preview absence as an application failure.
If the task needs client-origin evidence, retain that coverage gap alongside
the completed VM-origin checks.

An `environment-port` target rewrites the port onto the environment connection
host; it is not a tunnel to VM loopback. For a local environment on the same
machine as the preview client, use `target: {kind: "environment-port", port: PORT}`
with the retained tab ID; specify `path` or `protocol` when needed. A remote
private-network environment requires a service already reachable on that host
and port. Public relay hosts reject this target until T3's authenticated preview
gateway exists. For a remote VM's loopback development port, resolve it before
navigating:

```bash
basaltwater-web preview resolve --port PORT --json
```

Use `navigation.url` with `preview_navigate` and the retained `tabId`. If
`issue` is `forward_missing`, run the exact returned `command` when the requested
preview includes client-visible access, then resolve again. Add `--host ::1` for
IPv6 or `--profile godot` for threaded Godot. The resolver changes no routes;
failures identify `component`, `issue`, and `remediation`. If the CLI is absent,
report that the gateway was not provisioned. Keep the loopback binding and
saved access policy. A VM-side success does not prove client routing or trust.

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

A failed or timed-out input acknowledgment can follow successful page input.
Inspect state before retrying, especially toggles and submissions. If the
expected change occurred, report successful input with uncertain acknowledgment;
otherwise report transport failure or timeout as observed, and leave the input
outcome unverified when state cannot be inspected.

Scope diagnostics to the retained tab, requested URL, and navigation time/action.
Label Electron/preload/preview-host errors separately from gateway/network and
application errors. Historical `about:blank` errors do not establish a current
game failure; retain uncertain attribution instead of assigning it to the app.

When updates appear missing, use read-only `preview_evaluate` to report
`navigator.serviceWorker.controller` script URL/state and the current page's
`getRegistration()` scope, active/installing/waiting worker URLs/states, and
whether an update is waiting. Avoid `ready`, which can wait indefinitely.
Compare the hosted artifact with the build, then use the app's normal update
flow or one reload and recheck. Worker metadata alone does not prove freshness;
do not clear caches, unregister workers, or force activation as a diagnostic.
The [browser guide](https://github.com/bluehexagons/basaltwater/blob/main/docs/BROWSER_AUTOMATION.md#offline-cache-freshness)
(`docs/BROWSER_AUTOMATION.md` in a Basaltwater checkout) has a read-only expression.

Viewport presets exercise CSS layout breakpoints without changing the desktop
browser user agent. Snapshot again after resize or scroll because coordinates
and element visibility change, and note that the scroll position can persist
across a resize. Use appearance emulation for light/dark checks, then restore
`system` unless the task needs the override left in place.

Start a recording only when video evidence is useful and always stop it. The
current `preview_recording_stop` tool transfers the compressed recording once
(up to 50 MiB) into this agent environment and returns a local evidence path.
Verify the transfer succeeded and the file is readable before linking it for
the user. Older tools may return client-only artifact metadata; follow that
tool's contract rather than assuming its path is mounted here. Keep recordings
short and exclude credentials and unrelated user data. A snapshot action timeline
can show the snapshot currently being assembled as `running`; once the snapshot
response returns, judge prior
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
Use a ready VM-local browser when the session permits fallback. If client-visible
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
an otherwise testable task. Use a ready VM-local browser when fallback is permitted,
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

## Use a configured desktop browser

Discover installed applications with `basaltw agent manifest REPOSITORY --json`
and read the installed desktop skill before launch, capture, or input. On an
XRDP VM, check `basaltw desktop status` and `basaltw desktop doctor`; use
`desktop start` and `desktop exec -- BROWSER URL` within the browser-testing
task. These commands reuse the managed session and supply its environment.
Use PID/window-scoped inspection and captures, verify the resulting page, and
honor human pause. Never guess a display, copy authority cookies, or take over
an unrelated browser window. Native KDE uses its separate installed desktop
skill and existing portal grant instead of XRDP commands.

Use accessible controls when available and fresh screenshots for visual input.
Pixel-only checks do not establish DOM/network assertions; report the checks
actually performed. Preserve existing desktop applications and leave the
session running. Do not change browser installation or access policy merely
because a collaborative client disconnected.

## Evidence and safety

Test the application- or gateway-reported URL, not a guessed port. Capture only
the interactions, console failures, and screenshots needed for the handoff.
Avoid passwords, tokens, private response bodies, and unrelated user data.
Never infer that client-preview failure means a VM service is down; report the
origin that passed and the origin that could not be exercised.
