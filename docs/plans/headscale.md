# Headscale integration

Status: planned; Headscale hosting and Tailscale client enrollment are not
implemented. Provide an optional private network for managed servers,
workstations, agent VMs and builders, including access to SSH and management
services across NAT.

## Scope and boundaries

- Treat Headscale hosting and Tailscale client enrollment as independent
  capabilities. Clients may join an existing Headscale server. Start with
  Debian servers and Linux clients on explicitly supported profiles.
- Install a pinned, integrity-verified official Headscale Debian package with
  its service account and systemd unit. Use SQLite and a dedicated public HTTPS
  hostname, with Nginx terminating TLS and local backend/admin/metrics listeners.
- Require direct ingress for that hostname; reject Cloudflare Proxy/Tunnel
  routing for it without disabling tunnels for unrelated services. Implement
  and qualify the Tailscale control-protocol upgrade through Nginx.
- Keep target mutations behind `remote_setup.py` and plugin-owned step
  composition. Use the appropriate package/service/firewall capability checks;
  client preflight must also verify usable TUN support.
- Preserve SSH and panel authentication and an independent recovery route.
  Enrollment must not implicitly open services, replace DNS, advertise routes,
  enable an exit node or enroll the Headscale host into its own network.

## Milestones

1. **Configuration and setup contracts.** Add focused configuration helpers,
   CLI parsing, validation, saved-state serialization and command reconstruction.
   Wire server steps through the server/web capability builders and client
   steps through optional post-setup composition. Define explicit disable,
   leave and data-removal behavior; ordinary reruns preserve state.
   Proposed flags are `--headscale` and
   `--tailscale-login-server https://vpn.example.com`; finalize their contracts
   before documenting them as available commands.
   **Acceptance:** dry runs and mocked tests cover composition, round trips,
   unsupported profiles, invalid inputs and conflicting ingress/listeners,
   without system mutation or credential disclosure.

2. **Managed Headscale server.** Implement package installation, private state
   permissions, configuration validation, certificate provisioning, Nginx
   routing, explicit firewall rules and health checks. Keep administrative
   access local through the Unix socket. Publish configuration atomically and
   restart only when needed; preserve working configuration on validation failure.
   **Acceptance:** disposable Debian setup serves valid HTTPS and supports real
   client registration through Nginx; reruns and reboot retain identity and data.

3. **Client enrollment and access policy.** Install the Tailscale client and
   support interactive enrollment, then unattended enrollment through a private
   short-lived, single-use key handoff. Keep keys out of argv, logs, saved
   commands and durable setup payloads. Preserve valid enrollment and require an
   explicit operation to change control servers or leave. Define a restrictive
   Headscale policy and reconcile only requested host service access, accounting
   for Tailscale's own firewall behavior.
   **Acceptance:** two disposable clients can reach allowed services while
   denied traffic remains blocked; expired keys, interrupted joins and setup
   reruns do not leak keys, duplicate enrollment or remove recovery access.

4. **Recovery and qualification.** Add consistent backups of configuration,
   SQLite data and private keys, a documented restore procedure, and controlled
   upgrades following upstream's sequential minor-version requirements. Preserve
   the matching pre-upgrade data for recovery from database migrations; replacing
   a binary alone is not rollback. Document manual setup, diagnosis and removal.
   **Acceptance:** restore onto a replacement disposable server, qualify an
   upgrade and recovery, and verify client connectivity after service restart
   and reboot. Record sanitized scope and gaps in the coverage map.

## Delivery and checks

Commit and push this plan, then each coherent, validated milestone to `main`.
Update operator/reference documentation with the behavior it introduces. Keep
focused tests in their owning suites, mock system calls and use temporary
directories. Run documentation checks and `git diff --check` for this plan;
run affected suites while implementing and `make check-ci` for shared
configuration/plugin changes. Offline tests do not establish live connectivity;
use disposable systems for the acceptance above.

## Later scope

OIDC, configurable MagicDNS, embedded DERP, subnet routers, exit nodes,
administrative panel controls and broader platform support follow the initial
deliverable. Embedded DERP requires public UDP 3478 and separate relay
qualification; do not imply that hosting Headscale makes relays self-hosted.

Upstream references: [installation](https://headscale.net/stable/setup/install/official/),
[proxy protocol and Cloudflare limits](https://headscale.net/stable/ref/integration/reverse-proxy/),
[registration](https://headscale.net/stable/usage/getting-started/),
[upgrades and backups](https://headscale.net/stable/setup/upgrade/),
[same-host limitations](https://headscale.net/stable/about/faq/), and
[DERP](https://headscale.net/stable/ref/derp/).
