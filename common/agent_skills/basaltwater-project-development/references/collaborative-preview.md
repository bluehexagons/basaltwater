# T3 preview on Basaltwater hosts

Use the active session's preview policy and live schemas. Browser access
belongs to that session. A terminal or independent task cannot use a T3
thread's preview merely because it runs on the same machine.

## Browser runtime and network origin

Check which runtime the tools support before reusing a tab. Server-only tools
operate on tabs marked `runtime: "server"`. If only desktop or unmarked tabs
are listed, explain the access gap and open a server tab without passing their
IDs. A supported server browser can run without a viewing client. A reachable
browser without a current tab can still open one.

The server browser uses the T3 host's routes and trust store. Use
`target: {kind: "environment-port", port: PORT}` when the live open or
navigate schema supports it. On the server this reaches loopback directly.
When tools support desktop automation, its routes and trust belong to the
desktop. On a remote connection, the port target points to the connection
host and requires reachability from the desktop.

When a desktop needs a managed VM's loopback development port, resolve it first:

```bash
basaltwater-web preview resolve --port PORT --json
```

Use the returned `navigation.url`. For `forward_missing`, use the exact
returned command when client-visible access is within scope, then resolve
again. Add `--host ::1` for IPv6 or `--profile godot` for threaded Godot.
The resolver is read-only and reports `component`, `issue`, and
`remediation`. Report an unprovisioned gateway if that route needs the CLI
and it is absent. Keep loopback bindings and saved access policy intact.

## Chrome host prerequisites

T3's server browser and `html_preview` share a headless Chrome download.
Managed Playwright uses a separate cache. Follow the runtime's download or
retry interval before deciding that Chrome is unavailable.

For missing libraries, fonts, or AppArmor errors, inspect `t3 browser setup`
as the service account without sudo. Use the server's actual data directory
through `T3CODE_HOME` or `--base-dir`, especially on CachyOS web mode.
On Debian/Ubuntu, follow the command reported by T3 and use the host's
authorized privilege workflow for any package or AppArmor change. On CachyOS,
use its native package workflow. Preserve Chrome's sandbox and the selected
T3 data directory. After repair, verify a rendered page or screenshot.
An HTTP service check alone does not exercise Chrome.

## Certificate trust

`net::ERR_CERT_AUTHORITY_INVALID` refers to the selected browser host's
trust store. Client enrollment only helps a desktop-hosted browser. For a
managed gateway URL, if the user wants client trust restored, run
`basaltwater-web ca` and provide the public certificate URL and SHA-256
fingerprint. Use a trusted transfer path if the untrusted URL cannot deliver
its own certificate. Keep the CA private key on the gateway. Follow the
[client CA guide](https://github.com/bluehexagons/basaltwater/blob/main/docs/services/client-ca-trust.md)
for enrollment. For other origins, follow their trust procedure on the
affected browser host. Keep TLS verification enabled and continue other checks
when the affected route remains unavailable.

## Fallback and evidence

When session policy permits fallback, use an available configured browser or
existing project browser tests. If you need to find alternatives on a
Basaltwater host, use `basaltw agent manifest . --capability browser`.
For desktop control, check its platform skill and readiness. If no browser
remains, complete independent checks and identify the missing coverage. A
server-browser pass does not establish client reachability or trust.

Verify rendered state and inspect the page before repeating an uncertain
submission or toggle. Scope logs to the active tab, URL, and navigation time.
Use the [browser guide](https://github.com/bluehexagons/basaltwater/blob/main/docs/agents/browser-automation.md#collaborative-preview-and-private-networks)
for canvas timing, input acknowledgments, recordings and stalled presentation.
Use the [offline-cache check](https://github.com/bluehexagons/basaltwater/blob/main/docs/agents/browser-automation.md#offline-cache-freshness)
for stale builds. Keep recordings short and verify the transferred artifact
before linking it.

An approved service restart is a last step when collaborative coverage
requires it. Debian uses `t3code.service`, CachyOS web uses
`basaltwater-cachyos-t3.service`, and packaged desktops use their app
lifecycle. Follow the installed platform T3 skill before restarting.
