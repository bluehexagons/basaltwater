# Headscale integration

Status: planned; Headscale hosting and Tailscale client enrollment are not
implemented. Provide an optional private network for managed servers,
workstations, agent VMs and builders, including access to SSH and management
services across NAT.

## Scope and boundaries

- Treat Headscale hosting and Tailscale client enrollment as independent
  capabilities. Clients may join an existing Headscale server. Start with
  Debian servers and managed Linux clients on explicitly supported profiles;
  end-user onboarding uses official clients across desktop and mobile platforms.
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
  Apply the selected peer access and DNS configuration explicitly. Enrollment
  must not open public service ports, advertise routes, enable an exit node or
  enroll the Headscale host into its own network.

## Named servers and end-user onboarding

Resolve a managed Headscale server by its saved friendly name, following the
existing saved-builder selection pattern. Allow an external server to be
registered with a name and validated HTTPS URL, without granting administrative
access. Keep these records in private workspace state, with completion and
list/show commands. Use exact, case-insensitive names; missing or ambiguous
matches fail before mutation. Bind saved client selections to a stable record
identity and resolved URL so renaming a record preserves selection and changing
its endpoint requires an explicit client migration. Enrollment authority is
separate from endpoint discovery.

Proposed commands (not yet implemented):

```bash
basaltw headscale add office --url https://vpn.example.com
basaltw setup agent_vm agent.example.com --tailscale office --codex
basaltw headscale onboarding office
```

Keep `--tailscale-login-server URL` as an explicit alternative to
`--tailscale NAME`, with conflicting selections rejected. The onboarding command
prints a shareable HTTPS join-page URL and optionally its QR code. A new device
needs only the official Tailscale app, not Basaltwater:

1. Open the join page, reachable before joining the network. Choose Windows,
   macOS, Linux, iOS or Android and follow official installation links.
2. Copy the server URL into the app's custom-server setting, or use the supplied
   platform-specific login command. A QR code opens instructions; it does not
   promise automatic app configuration on every platform.
3. Initiate Headscale's native browser registration. An administrator verifies
   the person and approves that exact pending device for the intended user.
4. Confirm connection and test an allowed resource. Provide clear pending,
   expired and denied guidance, plus administrator device revocation.

The join page contains no enrollment keys or administrative credentials.
Ordinary personal devices retain their user identity; service tags and
short-lived preauthenticated keys belong to unattended machine enrollment.
Keep administrative approval authenticated and separate from public instructions;
the existing T3 pairing portal does not grant Headscale enrollment authority.

Follow the [enrollment and onboarding security contract](headscale-enrollment.md)
for public/LAN page visibility, native approval, machine-key handoff and
revocation. Initial enrollment uses native administrator approval for personal
devices and single-use preauthenticated keys for unattended machines. Network
access policy remains separate from registration.

SSO integration, provider hosting and a custom identity provider are deferred.
The [self-hosted SSO assessment](headscale-sso.md) preserves research for a later
decision; it is not an active qualification task.

## Selected defaults and remaining decisions

- **Access policy:** all enrolled systems may communicate with each other over
  their overlay addresses. Headscale already defaults to allow-all without a
  policy; generate an explicit managed allow-all policy for new networks so the
  intent is visible. Preserve existing policies when adopting a server. Keep
  personal ownership and approved machine tags for administration and later
  restrictions. Host firewalls, listening addresses and service authentication
  still apply; reconcile managed host rules only for the overlay interface.
- **Administrative interface:** include a Headscale administration page in the
  Web Portal when both are installed. Support user management, exact pending
  device approval, node inspection and revocation through an authenticated,
  authorized backend using native Headscale operations. Preserve CLI-over-SSH
  recovery. Named external endpoints require separately configured management
  authority. Keep the public join page separate from administration.
- **Relay dependency:** self-host the control plane and DERP/STUN from the first
  delivery. Enable embedded DERP with client verification and remove Tailscale's
  public DERP map; do not require a paid service or public Tailscale relays.
  One relay is the initial availability tradeoff; a second owned relay can follow.
- **DNS design to confirm:** recommend MagicDNS for enrolled device names and
  Unbound for recursive DNS and local records. Decide whether to send all other
  queries to Unbound or only selected private zones before implementing DNS
  defaults. DNS installation and DNS adoption are explicit configuration choices.
- **Lifetime:** make enrollment-key lifetime and device reauthentication
  separate explicit settings; settle defaults against pinned client behavior.
- **Deployment inputs:** choose the server profile/architecture, public control
  and onboarding hostnames, page visibility, administrative SSH identity and
  backup destination before live qualification. Keep real endpoints and account
  details in private configuration. These inputs need not block initial code.

See [network access, relays and DNS](headscale-networking.md) for the rationale,
public-relay limitations, Unbound topology and connectivity acceptance.

## Milestones

1. **Configuration and setup contracts.** Add focused configuration helpers,
   CLI parsing, validation, saved-state serialization and command reconstruction.
   Wire server steps through the server/web capability builders and client
   steps through optional post-setup composition. Define explicit disable,
   leave and data-removal behavior; ordinary reruns preserve state.
   Include named managed/external server records, endpoint-change guards and
   CLI completion. Proposed flags include `--headscale`, `--tailscale NAME` and
   `--tailscale-login-server URL`; finalize their contracts before documenting
   them as available commands.
   **Acceptance:** dry runs and mocked tests cover composition, round trips,
   unsupported profiles, invalid inputs, duplicate/missing names, record renames,
   endpoint changes and conflicting ingress/listeners, without system mutation
   or credential disclosure.

2. **Managed Headscale server.** Implement package installation, private state
   permissions, configuration validation, certificate provisioning, Nginx
   routing, explicit firewall rules and health checks. Keep administrative
   access local through the Unix socket. Publish configuration atomically and
   restart only when needed; preserve working configuration on validation failure.
   Include verified-client embedded DERP, public STUN ingress and a relay map
   containing only owned relays. Keep client log upload disabled and qualify
   platform-specific startup settings under the networking contract.
   **Acceptance:** disposable Debian setup serves valid HTTPS and supports real
   client registration through Nginx; reruns and reboot retain identity and data.
   Both direct and forced-relay connections work without Tailscale's public
   relay/map services; unknown clients cannot use the relay.

3. **Client enrollment and access policy.** Install the Tailscale client and
   support interactive enrollment, then unattended enrollment through a private
   short-lived, single-use key handoff. Keep keys out of argv, logs, saved
   commands and durable setup payloads. Preserve valid enrollment and require an
   explicit operation to change control servers or leave. Apply the explicit
   all-to-all default to new networks and preserve adopted policies, accounting
   for Tailscale's own firewall behavior. Deliver the join page, conditional Web
   Portal administration and native CLI recovery. Configure the chosen MagicDNS
   and Unbound integration with reachable private resolvers and bootstrap-safe
   control-host resolution. SSO and provider management remain outside this scope.
   **Acceptance:** two disposable clients can initiate connections in both
   directions under the default; a restrictive policy fixture still blocks denied
   traffic and public ingress stays unchanged. Expired keys, interrupted joins
   and setup reruns do not leak keys, duplicate enrollment or remove recovery access.
   Qualify guided enrollment on Windows, macOS, Linux, iOS and Android separately,
   including pre-join page access, denied/expired approval and device revocation.
   Verify onboarding-origin isolation, restricted enrollment identities, secret
   handling and revocation under the enrollment contract before declaring it ready.

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

Additional relay locations, LAN-wide publication of overlay DNS records, subnet
routers, exit nodes, advanced policy editing, OIDC integration, identity-provider
setup and broader managed-client platform support follow the initial deliverable.

Upstream references: [installation](https://headscale.net/stable/setup/install/official/),
[proxy protocol and Cloudflare limits](https://headscale.net/stable/ref/integration/reverse-proxy/),
[registration](https://headscale.net/stable/usage/getting-started/),
[OIDC](https://headscale.net/stable/ref/oidc/),
[Windows](https://headscale.net/stable/usage/connect/windows/),
[Apple devices](https://headscale.net/stable/usage/connect/apple/),
[Android](https://headscale.net/stable/usage/connect/android/),
[upgrades and backups](https://headscale.net/stable/setup/upgrade/),
[same-host limitations](https://headscale.net/stable/about/faq/), and
[DERP](https://headscale.net/stable/ref/derp/).
