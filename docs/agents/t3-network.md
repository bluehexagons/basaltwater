# Network access and trust

[Documentation index](t3-code.md)

## Network behavior

The safe default is loopback:

```bash
--web-interface t3code
--web-interface-host 127.0.0.1
--web-interface-port 3773
```

Basaltwater publishes a managed HTTPS endpoint through its shared gateway. Use
direct HTTP only over localhost or in a controlled test environment. Direct
HTTP can expose T3 credentials and pairing links without encryption. For a
non-loopback bind, declare private source networks:

```bash
--web-interface-host 0.0.0.0
--web-interface-source 192.168.1.0/24
```

Setup preserves the existing T3 HTTPS forward and its public port when device
pairing is disabled. It removes only the pairing forward before reconciling the
T3 endpoint, so rerunning setup does not tear down and recreate that endpoint.
Each T3 and pairing HTTPS route uses the current T3 source selection. Changing
that selection removes previously managed exceptions from both routes. Without
any sources, those HTTPS routes accept loopback connections only. Use the URL
printed by setup. If it names a public TLS hostname, resolve that name to
loopback on the machine accessing the gateway locally so the certificate
continues to match.

A non-loopback bind is rejected unless UFW is active and a private or
non-global allowlist is present. Basaltwater reconciles only its own labeled
UFW rules and refuses conflicting unmanaged rules on the managed ports.
T3 supports loopback literals, `localhost`, and the wildcard binds `0.0.0.0`
and `::`. Specific non-loopback addresses are rejected because readiness and
administrative pairing require a local loopback connection. Use a wildcard
with the source allowlist for LAN access. Local checks try both loopback
families for `localhost`; IPv6-only listeners use IPv6 loopback for readiness
and their managed HTTPS forwards, including the pairing route.

The protected device-pairing broker uses port 3774 by default. Its Basic Auth
credential is staged for setup and is not written to the saved setup command.
Prefer the HTTPS endpoints printed during setup.
Opening the protected HTTPS endpoint completes browser pairing and opens T3
automatically. Its `/devices` page retains manual enrollment and links to native
T3 Connect controls, with host CLI guidance for headless servers. Use the primary
T3 HTTPS URL after pairing; if a saved browser session stops working, reopen the
protected entry to obtain a fresh native session.
See [device pairing](../services/device-pairing.md) for origin and cookie diagnostics.

### Browser authentication diagnostics

An unpaired browser can report WebSocket authentication failures while showing
the native pairing form. A bare URL has not enrolled that browser; complete
the normal pairing flow before judging authenticated WebSocket access.

On T3 v0.0.45, cloud-account sign-in at a private HTTPS origin can return
HTTP 400 from `clerk.t3.codes` with a production-key domain error requiring
`t3.codes`. T3 loads its browser cloud-account provider from its bundled public
configuration; see the upstream [app entrypoint](https://github.com/pingdotgg/t3code/blob/v0.0.45/apps/web/src/main.tsx)
and [browser auth shell](https://github.com/pingdotgg/t3code/blob/v0.0.45/apps/web/src/components/clerk/BrowserManagedAuthShell.tsx).
This browser cloud-account flow is separate from native pairing and gateway
trust. Keep cloud-account sign-in unverified on that origin; a Clerk domain
rejection does not establish a TLS or native pairing failure. Preserve the
managed authentication policy and upstream assets.
