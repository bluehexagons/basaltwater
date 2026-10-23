# T3 collaborative preview testing

This VM has T3 Code preview guidance but no managed Playwright fallback. T3
v0.1.2 prefers its server browser, which uses the server's routes and certificate
trust and can automate without a viewing client. A desktop-hosted preview uses
the desktop client's routes and trust instead. Identify the selected runtime
before diagnosing connectivity or requesting client changes.

This workflow requires preview tools exposed to the active agent session.
Unattended panel prompts run outside the connected T3 thread and cannot borrow
its tools. If preview tools are absent, inspect configured alternatives below;
an installed T3 service does not establish browser access.

## Preview availability

Choose a browser that covers the task under the active session's policy. T3 is
useful for live collaboration and server-side automation; a configured
desktop browser covers native integration and visual interaction, and existing
project Playwright tests cover repeatable automation. This skill does not imply
that either alternative is installed.
Under a preview-first policy, fallback is allowed for absent preview tools, an explicit
user request for another browser, or an explicit unsupported/unavailable host
error from `preview_open`. Follow any additional fallback rules in the active
session, including a limit of two failures on the same step when specified;
quote the raw error when switching. Inspect and correct actionable errors before
classifying the host as unavailable.

Call `preview_status` first. If no automation-capable tab is attached, call
`preview_open` once. Do not repeatedly reopen or poll. A closed or minimized T3
application does not establish that the server browser is unavailable or that
the web application is unhealthy.

`available: true` with `tabId: null` means the automation host is reachable but
this agent session has no current tab. Open once and retain the returned tab ID;
do not classify that status as preview unavailability.

When fallback is permitted, continue automatically with a ready configured
alternative; do not wait for the user to reopen T3 or restart its service.
Discover applications with `basaltw agent manifest REPOSITORY --json`. If a
desktop skill is installed, read it and check desktop status/doctor before using
its managed launch, scoped inspection, and input commands. Honor human pause and
existing browser profiles. Alternatively use a project's existing Playwright
dependencies and documented test command. Do not install Playwright ad hoc,
bind a service to `0.0.0.0`, or widen firewall policy.

If the client disconnects during a test, keep using an available server browser.
If the automation host also becomes unavailable, make one bounded status/open
check, then choose an available alternative when policy permits. Inspect
application state before retrying uncertain submissions. Do not copy cookies between
profiles or poll T3 before every subsequent action. Record browser, origin, and
observed behavior; VM desktop checks do not verify the disconnected client's
trust or routes. If no usable browser remains, complete build, unit, service,
loopback HTTP, and gateway checks and report only the missing browser coverage.

A server-browser tab can navigate, accept input, and capture snapshots without a
visible preview surface. A hidden desktop-hosted tab may lack raster capture;
attempt a snapshot and apply the session's bounded retry rules on failure.
Opening a tab or seeing a URL in status does not prove the page rendered;
confirm visible content or a snapshot.

Retain the returned tab ID on every call. Read a human-controlled tab only with
snapshot/wait tools, or open your own tab; operate it only while unclaimed.
Snapshot before interaction and prefer its semantic role/name locators. A
successful action response confirms the helper was accepted, not that the page
received an event or changed, so verify the expected state with a bounded wait,
another snapshot, or a read-only state check. Programmatic typing can set DOM
focus without pointer-activating page input in the connected preview. If a
keyboard helper then produces no expected
change, semantically click the intended target and retry the key once. If it
still has no effect, use an equivalent semantic click when appropriate or
report the keyboard behavior as unverified; do not mutate page state through
evaluation to fake the check.

If an input call fails or times out, inspect state before retrying it. A changed
dropdown after a failed `Home` acknowledgment is successful input with uncertain
acknowledgment. Keep transport failure, timeout, and unverified input distinct;
do not repeat a toggle or submission when the expected change already occurred.

Attribute diagnostics by tab ID, requested URL, and navigation time/action.
Separate Electron/preload/preview-host errors from gateway/network and application
errors. Errors retained from `about:blank` are historical host diagnostics;
do not count them as current game errors without evidence.

When a changed build appears stale, use read-only `preview_evaluate` to inspect
the controlling service worker's script URL/state and the current page's
`getRegistration()` scope and active/installing/waiting workers. Report whether
an update is waiting; avoid `ready`, which can wait indefinitely. Compare a
served artifact with the build, use the app's update flow or one reload, and
recheck. Worker metadata alone does not establish freshness. Do not unregister
workers, delete caches, or force activation merely to diagnose a stale preview.
See the read-only expression in the
[browser guide](https://github.com/bluehexagons/basaltwater/blob/main/docs/agents/browser-automation.md#offline-cache-freshness)
or `docs/agents/browser-automation.md` in a Basaltwater checkout.

Canvas snapshots may expose only fallback text; inspect the screenshot and
use supported coordinate input for canvas controls, keeping semantic locators
for surrounding HTML. Account for screenshot scaling and refocus the canvas
before keyboard input. For real-time games, identify the pause control before
starting, perform a short interaction, then pause immediately and verify that
gameplay timers stop before capturing. Resume for the next bounded interaction;
paused screenshots alone do not verify motion or timing. The same canvas
limitations apply to both collaborative preview and VM-local browsers.

Device presets change the CSS viewport but retain the desktop user agent.
Snapshot again after resize or scroll, and expect scroll position to persist
across some resizes. Restore appearance emulation to `system` after light/dark
checks unless the task requires otherwise. Recording output belongs to the
agent environment after the current `preview_recording_stop` tool transfers it
once (up to 50 MiB). Stop every recording, verify successful transfer and a
readable local file, then link the returned evidence path. For older tools that
return client-only artifacts, use their metadata without assuming the client
path is mounted here. Keep recordings short and free of credentials and private
data. The current snapshot can appear as `running` in its own action timeline
while the response is assembled; that self-entry is not a hung action after the
snapshot has returned.

For a desktop-hosted preview, if `preview_open` reports success but the user sees
no preview surface, status stays `visible: false`, and snapshot capture fails,
reuse the same tab for at
most one confirmation. A read-only page check may show that a background
renderer works even though T3 did not mount its UI. Treat that combination as
stale preview-presentation state rather than an application, route, or TLS
failure, and do not create replacement tabs.

Restarting only the desktop client may preserve VM-side preview state. When
collaborative coverage is required and the user explicitly accepts interruption
of every active T3 session, restart the managed server as the target user:

```bash
systemctl --user restart t3code.service
```

Reconnect the client and try status/open once. Never restart the service
silently or for ordinary preview absence. Without approval, leave the affected
collaborative operation skipped and continue in a configured alternative when
policy permits; a full VM reboot is not the first recovery step.

## Network origin

With the preferred server browser, `environment-port` resolves directly to
`localhost:PORT` on the T3 server. Navigate with
`target: {kind: "environment-port", port: PORT}` and the retained tab ID; add
`path` or `protocol` as needed. No gateway forward is needed for VM-loopback
automation in this runtime.

With a desktop-hosted preview, the same target rewrites the requested port onto
the environment connection host; it does not tunnel to VM loopback. A local
environment on the desktop client's machine is reachable directly. Remote
private hosts require an already reachable service, and public relay hosts
reject this target until T3's authenticated preview gateway exists. When the
desktop browser needs a remote VM's loopback development port, resolve it first:

```bash
basaltwater-web preview resolve --port PORT --json
```

Navigate using the returned `navigation.url` and retained tab ID. For
`forward_missing`, execute the exact returned `command` when client-visible
preview is in the requested scope, then resolve again. Use `--host ::1` for
IPv6 or `--profile godot` for threaded Godot. The resolver is read-only and
reports failures by `component`, `issue`, and `remediation`. If this desktop
route needs the CLI and it is missing, report the unprovisioned gateway. A
server-browser pass does not verify a desktop client's reachability or trust.

Use the managed `basaltwater-web-gateway` only when client-visible access is in
scope. Do not rebind the development server or weaken access policy solely to
make preview automation work.

For WebAssembly or WebGL applications, allow one bounded startup interval after
document load before judging the frame. Capture only the interactions, console
failures, and screenshots needed for the handoff; avoid credentials and private
response data.

## Certificate errors are not blockers

An explicit preview network error of `net::ERR_CERT_AUTHORITY_INVALID` identifies
a trust problem on the selected browser host. Check whether that host is the
server or desktop before choosing a repair. Do not require client CA enrollment
for a server-browser error. Skip the affected preview-only operation,
continue server and HTTPS checks from the VM without bypassing verification,
and report the browser coverage gap. Never use `curl -k`, ignore HTTPS errors,
or disable TLS.

For a desktop-hosted preview of a managed `basaltwater-web` URL, if the user wants
to restore client trust, run:

```bash
basaltwater-web ca
```

Give the user the reported public certificate URL and SHA-256 fingerprint.
If the untrusted URL cannot deliver its own CA, transfer only the public
certificate over an existing trusted path and verify its fingerprint before
installation. Never transfer the CA private key.

For enrollment, read only the matching platform section of the
[client CA trust guide](https://github.com/bluehexagons/basaltwater/blob/main/docs/services/client-ca-trust.md)
(or `docs/services/client-ca-trust.md` in a Basaltwater checkout). The user performs
this client security change. After enrollment, restart the client, recheck
preview status, and retry the existing tab. If enrollment is declined or the
guide is unavailable, retain the coverage gap and continue the other checks.

For a certificate error on any other HTTPS origin, use that origin's documented
trust process on the browser host or leave the affected check skipped;
`basaltwater-web ca` does not repair unrelated certificates.
