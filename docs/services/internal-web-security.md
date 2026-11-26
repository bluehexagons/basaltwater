# Internal web trust and security

[Topic index](internal-web.md)

## Certificate trust

When the VM uses its local certificate authority, enroll the public CA only on
LAN clients that should open the private origin. Client enrollment is optional;
VM-local checks and managed Playwright already use the VM trust store. The
command prints the file, download URL, and SHA-256 fingerprint:

```bash
basaltwater-web ca
```

Verify the printed fingerprint before installing the public CA. See
[Client CA trust](client-ca-trust.md) for safe transfer, desktop, ChromeOS, and
mobile enrollment, application-specific trust limitations, and removal.

Keep TLS verification enabled. Use `site doctor` for a published snapshot and
`forward doctor` for a named HTTPS listener, including supervised previews. For
an authenticated forward, a `401` response with a `WWW-Authenticate` challenge
confirms that the protected endpoint is reachable; other error responses still
fail the check.

## Security and state

The configured account can publish and manage its own live routes without
`sudo`. A root-owned local control service authenticates live mutations with
the caller's Unix socket UID, checks the saved gateway users, and retains the
existing per-route ownership checks. If a live command says the control service
is unavailable, rerun saved VM setup and check
`systemctl status basaltwater-web-control.service`. The administrator-only
`forward reconcile` command still requires root.

Forward and preview mutations share a root-owned lock at
`/etc/basaltwater/internal-web/mutation.lock`. A competing command fails before
reading or changing state by default; retry after the active command finishes.
`forward add` and the root-only `forward reconcile` accept
`--lock-timeout SECONDS` (0–300) to wait for the lock before reading state.
VM setup waits up to five minutes for policy reconciliation and T3 endpoint
creation. A timeout reports the wait duration and leaves gateway policy and
forward state untouched by that command.

Setup passes a private staged policy to `forward reconcile --policy FILE`.
Policy validation, activation, forward reconciliation, and rollback hold the
same lock. A reconciliation failure restores the previous policy and uses its
access sources when restoring firewall rules; a failed rollback is included in
the error. An unchanged, active control service stays running during setup.
Changes to the control helper, the gateway utility it imports for policy
checks, or the service unit trigger a restart. Lock ownership lasts
through rollback and state writes and is released on process exit. Do not delete
the stable lock file to bypass a running command.

### Protected upstream ports

T3 setup registers the configured T3 HTTP port and, when enabled, the pairing
HTTP port in the root-managed `/etc/basaltwater/internal-web/policy.json`.
The `upstream_access_sources` map uses decimal port strings as keys. Each value
is an allowlist of IPv4 and IPv6 addresses or CIDRs. T3 setup uses the effective
access sources from the saved setup, including `--access-source` and
`--web-interface-source`. An empty list permits loopback only.

The restriction covers every managed forward targeting the port on any
supported loopback address, including IPv4 aliases in `127.0.0.0/8` and IPv6
`::1`. It also follows chains through ports assigned to managed gateway
listeners. The gateway intersects applicable upstream and named route
allowlists, including the ordinary gateway source policy on hops that have
no named policy. Nginx checks the connecting peer on restricted listeners, and
UFW rules admit only the resulting external sources. Ordinary forwards keep
their existing gateway defaults. A managed forward still sends HTTP to its
upstream. Targeting another HTTPS listener does not make the gateway an
HTTPS-to-HTTPS proxy.

After upgrading from a route-name-only policy, rerun the saved T3 setup to
register the trusted HTTP ports and reconcile all existing forwards. Route
records are mutable by their owners, so reconciliation alone cannot infer
which ports belong to T3 or pairing on an older VM. Policy updates regenerate
the listener and firewall rules for existing aliases. Staged updates replace
entries for the supplied ports and retain omitted entries. Retired ports stay
restricted until root explicitly removes their entries from the live policy
and reconciles it. Before reusing a retired port, verify the old service has
stopped and inspect the remaining forwards. Keep a policy backup if editing
the live file directly, since the staged-policy rollback applies only to
`forward reconcile --policy FILE`.

These controls apply to new connections to Basaltwater-managed forwards. A
policy update can leave established connections, including WebSockets, active
until closed. The gateway cannot identify arbitrary local relays, unmanaged
listeners, or proxies outside its forward records. A process with local
loopback access can still connect to the HTTP service directly. Direct
non-loopback HTTP listeners remain subject to their service's own firewall
policy. T3 and pairing authentication still apply.

- Static builds and live commands run as the configured non-root owner.
- Live upstreams are limited to unprivileged loopback ports.
- Only Nginx's HTTPS listener receives a managed UFW rule.
- One explicitly named project is exposed at a time; ports are never discovered
  and exposed automatically.
- Generated Nginx, firewall, publication, preview, and systemd state is
  validated before mutation and scoped to its recorded owner.
- Preview units use systemd process, memory, privilege, device, kernel, and
  address-family limits.

Use `basaltwater-web forward reconcile` after repairing managed state, and
`basaltwater-web forward doctor NAME` to validate a reachable forwarded endpoint.
