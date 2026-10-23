# Use the collaborative preview

Call `preview_status` first. If there is no automation-capable tab, call
`preview_open` once. Do not repeatedly reopen or poll. T3 v0.1.2 prefers a
server browser that works without a viewing client. Hidden server tabs support
navigation, input, and snapshots; a closed or minimized client does not establish
host unavailability. A hidden desktop-hosted tab may lack raster capture;
attempt a snapshot before classifying coverage.

`available: true` with `tabId: null` means the automation host is attached but
this agent session has no current tab. Call `preview_open` once; do not report
the collaborative browser as unavailable from that status alone. Preserve the
returned `tabId` for later actions instead of relying on an implicit current
tab after an agent-session boundary. Read a human-controlled tab only with
snapshot/wait tools, or open your own tab; operate it only while unclaimed.

If a later preview action reports a disconnected/unavailable host, repeat this
bounded status/open check once, then automatically choose a ready alternative
when the session permits it. Follow the active session's additional fallback
rules, including a limit of two failures on the same step when specified, and
quote the raw error when switching. Keep using an available server browser
after client disconnection. Do not treat preview absence as an application failure.
If the task needs client-origin evidence, retain that coverage gap alongside
the completed VM-origin checks.

Identify the selected runtime before diagnosing routes or certificate trust.
The preferred server browser uses the T3 server's network and trust store. Use
`target: {kind: "environment-port", port: PORT}` with the retained tab ID;
specify `path` or `protocol` when needed. It resolves directly to `localhost:PORT`
on the server, without a gateway forward for VM-loopback automation.

A desktop-hosted preview uses the desktop client's network and trust store.
Its `environment-port` target rewrites the port onto the environment connection
host; it is not a tunnel to VM loopback. A local environment on the desktop
client's machine is reachable directly; a remote private-network environment
requires a service already reachable on that host and port. Public relay hosts
reject this target until T3's authenticated preview gateway exists. When the
desktop browser needs a remote VM's loopback development port, resolve it first:

```bash
basaltwater-web preview resolve --port PORT --json
```

Use `navigation.url` with `preview_navigate` and the retained `tabId`. If
`issue` is `forward_missing`, run the exact returned `command` when the requested
preview includes client-visible access, then resolve again. Add `--host ::1` for
IPv6 or `--profile godot` for threaded Godot. The resolver changes no routes;
failures identify `component`, `issue`, and `remediation`. If this desktop route
needs the CLI and it is absent, report that the gateway was not provisioned.
Keep the loopback binding and saved access policy. A VM-side success does not
prove client routing or trust.

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
The [browser guide](https://github.com/bluehexagons/basaltwater/blob/main/docs/agents/browser-automation.md#offline-cache-freshness)
(`docs/agents/browser-automation.md` in a Basaltwater checkout) has a read-only expression.

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

For a desktop-hosted preview, if `preview_open` reports a tab but no preview
surface appears, status remains `visible: false`, and a snapshot fails, do not
create more tabs. Confirm the T3
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

An explicit `net::ERR_CERT_AUTHORITY_INVALID` preview network entry identifies
a trust problem on the selected browser host. Check whether that host is the
server or desktop before choosing a repair. Client CA enrollment does not repair
server-browser trust. Use a ready alternative when fallback is permitted,
continue server and HTTP checks, and report the affected browser coverage.
Never use `curl -k`, ignore HTTPS errors, or weaken TLS.

For a desktop-hosted preview of a managed `basaltwater-web` URL, if the user wants
client trust restored, run:

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
