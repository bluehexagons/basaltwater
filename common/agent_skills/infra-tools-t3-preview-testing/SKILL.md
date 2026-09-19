---
name: infra-tools-t3-preview-testing
description: Browser-test web applications with T3 Code's collaborative preview on a Basaltwater agent VM where managed Playwright is not provisioned.
metadata:
  managed-by: infra_tools
---

# T3 collaborative preview testing

This VM has T3 Code preview guidance but no managed Playwright fallback. The
preview depends on the connected T3 application remaining open and uses that
client's routes and certificate trust.

## Preview availability

Prefer this browser workflow over an installed shared desktop for ordinary web
testing, including canvas/WebGL. Desktop browser input is a fallback only when
preview is unavailable or the task requires desktop-specific integration; explain
its reduced coverage. Use the desktop skill if that capability is installed.

Call `preview_status` first. If no automation-capable tab is attached, call
`preview_open` once. Do not repeatedly reopen or poll. A closed or minimized T3
application is a normal coverage limitation, not evidence that the web
application is unhealthy.

`available: true` with `tabId: null` means the automation host is reachable but
this agent session has no current tab. Open once and retain the returned tab ID;
do not classify that status as preview unavailability.

When the preview is unavailable, continue with build, unit, service, loopback
HTTP, and managed gateway checks. Report which collaborative browser behavior
was not exercised. Do not install Playwright ad hoc, bind a service to
`0.0.0.0`, or widen firewall policy to manufacture browser coverage.

A hidden tab may still navigate while raster capture is unavailable. Attempt
one snapshot after navigation. If it fails while the tab remains hidden, stop
retrying and continue with non-browser checks. Opening a tab or seeing a URL in
status does not prove the page rendered; confirm visible content or a snapshot.

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
connected client's artifact store rather than the VM; stop every recording and
use its returned artifact metadata without trying to read the client path from
the VM. The current snapshot can appear as `running` in its own action timeline
while the response is assembled; that self-entry is not a hung action after the
snapshot has returned.

If `preview_open` reports success but the user sees no preview surface, status
stays `visible: false`, and snapshot capture fails, reuse the same tab for at
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
silently or for ordinary preview absence. Without approval, leave the browser
operation skipped; a full VM reboot is not the first recovery step.

## Network origin

The preview runs in the connected client's context, not on the VM. An
`environment-port` target rewrites the requested port onto the environment
connection host; it does not tunnel to VM loopback. If the VM endpoint is
healthy but preview navigation remains at `about:blank` with no network entry,
report the client/VM routing boundary.

Use the managed `infra-tools-web-gateway` only when client-visible access is in
scope. Do not rebind the development server or weaken access policy solely to
make preview automation work.

For WebAssembly or WebGL applications, allow one bounded startup interval after
document load before judging the frame. Capture only the interactions, console
failures, and screenshots needed for the handoff; avoid credentials and private
response data.

## Certificate errors are not blockers

Only an explicit preview network error of
`net::ERR_CERT_AUTHORITY_INVALID` establishes a client trust problem. Do not
require the user to trust the VM CA. Skip the affected preview-only operation,
continue server and HTTPS checks from the VM without bypassing verification,
and report the browser coverage gap. Never use `curl -k`, ignore HTTPS errors,
or disable TLS.

For a managed `infra-web` URL, if the user wants to restore collaborative
preview access, run:

```bash
infra-web ca
```

Give the user the reported public certificate URL and SHA-256 fingerprint.
If the untrusted URL cannot deliver its own CA, transfer only the public
certificate over an existing trusted path and verify its fingerprint before
installation. Never transfer the CA private key.

For enrollment, read only the matching platform section of the
[client CA trust guide](https://github.com/bluehexagons/infra_tools/blob/main/docs/CLIENT_CA_TRUST.md)
(or `docs/CLIENT_CA_TRUST.md` in a Basaltwater checkout). The user performs
this client security change. After enrollment, restart the client, recheck
preview status, and retry the existing tab. If enrollment is declined or the
guide is unavailable, retain the coverage gap and continue the other checks.

For a certificate error on any other HTTPS origin, use that origin's documented
trust process or leave the client-origin check skipped; `infra-web ca` does not
repair unrelated certificates.
