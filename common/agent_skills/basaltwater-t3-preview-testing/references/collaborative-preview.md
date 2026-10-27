# T3 collaborative preview testing

This setup supplies T3 Code preview guidance but no managed Playwright fallback. T3
v0.1.3 uses its server browser for automation, with the server's routes and
certificate trust, and works without a viewing client. A desktop-hosted preview
uses the desktop client's routes and trust instead. Identify the selected runtime
before diagnosing connectivity or requesting client changes.

Check each listed tab's `runtime` before reusing its ID. Only `runtime: "server"`
entries can be passed to this environment's `preview_*` tools. Entries with
desktop or missing runtime belong to the user's local app; report that the
server cannot access that tab and call `preview_open` without its ID to open a
server tab. This is a runtime boundary, not a route, certificate, or capture
failure. Desktop automation and recovery guidance below applies only when the
active tools explicitly support that runtime. See the
[upstream tab-list contract](https://github.com/pingdotgg/t3code/blob/v0.1.3/apps/server/src/mcp/toolkits/previewControls/tools.ts).

This workflow requires preview tools exposed to the active agent session.
Unattended panel prompts run outside the connected T3 thread and cannot borrow
its tools. If preview tools are absent, inspect configured alternatives below;
an installed T3 service does not establish browser access.

## T3 browser host preparation

T3 downloads its own headless Chrome on first server-tab or `html_preview` use;
these features share that download. Managed Playwright's browser cache is
separate. Allow the returned installation/retry interval before declaring the
host unavailable. HTTP service readiness does not verify Chrome launch.

For explicit missing-library, font, or AppArmor sandbox diagnostics, inspect
`t3 browser setup` as the service account without sudo; it reports needed host
changes. Use the server's data directory through `T3CODE_HOME` or `--base-dir`,
especially for CachyOS web's isolated home. The elevated form can install
Debian/Ubuntu packages/fonts or an AppArmor profile. Use the exact setup command printed by the active T3
runtime and the platform's authorized privileged workflow for those changes.
Do not automatically run it as root or disable Chrome's sandbox. On CachyOS,
use its package workflow for missing libraries/fonts; the upstream apt route
is not an Arch installer. Preserve the selected T3 mode and data directory.

Retry the failed operation after repair and verify a screenshot with readable
text. A pre-download setup check cannot inspect libraries for an absent Chrome
executable. For T3 0.1.3, see [browser host setup](https://github.com/pingdotgg/t3code/blob/v0.1.3/docs/user/remote-access.md#browser-host-setup).

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
Discover browser capabilities with `basaltw agent manifest REPOSITORY
--capability browser --json`. If a desktop skill is installed, read it before
launch, capture, or input. CachyOS uses `basaltwater-cachyos-desktop` and
`basaltw desktop --native`; Debian uses `basaltwater-desktop` and the XRDP
commands. Check the matching status/doctor. Honor human pause and
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
visible preview surface. When tools explicitly support desktop automation, a
hidden desktop-hosted tab may lack raster capture; attempt a snapshot and apply
the session's bounded retry rules on failure.
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

With tools that explicitly support desktop automation, if `preview_open`
reports success but the user sees no preview surface, status stays
`visible: false`, and snapshot capture fails, reuse the same tab for at most
one confirmation. A read-only page check may show that a background
renderer works even though T3 did not mount its UI. Treat that combination as
stale preview-presentation state rather than an application, route, or TLS
failure, and do not create replacement tabs.

Restarting only a desktop client may preserve remote server preview state.
When collaborative coverage requires a restart and the user explicitly accepts
the interruption, follow the installed platform T3 skill. Debian's managed unit
is `t3code.service`; CachyOS web mode uses `basaltwater-cachyos-t3.service`, and
CachyOS desktop mode requires reopening the packaged app. Keep those environments
distinct. Reconnect the client and try status/open once. Never restart T3
silently or for ordinary preview absence. Without approval, leave the affected
collaborative operation skipped and continue in a configured alternative when
policy permits; a full VM reboot is not the first recovery step.

## Network origin

With the preferred server browser, `environment-port` resolves directly to
`localhost:PORT` on the T3 server. Navigate with
`target: {kind: "environment-port", port: PORT}` and the retained tab ID; add
`path` or `protocol` as needed. No gateway forward is needed for VM-loopback
automation in this runtime.

When desktop automation is supported, the same target rewrites the requested
port onto the environment connection host; it does not tunnel to VM loopback.
A local environment on the desktop client's machine is reachable directly. Remote
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
