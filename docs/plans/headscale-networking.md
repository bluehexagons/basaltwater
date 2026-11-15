# Headscale network access, relays and DNS

Status: networking contract for the integrated [Headscale implementation](headscale.md).
All-to-all peer access, owned relays, MagicDNS and managed Unbound for all other
queries are selected requirements. See the [implementation handoff](headscale-implementation.md)
for the dependency order and completion gates, and the
[qualification record](../testing/headscale.md) for the tested subset.

## Access among enrolled systems

Headscale permits traffic between all nodes when no policy is loaded. Basaltwater
makes this intent explicit for new managed networks: enrolled personal
devices and tagged machines may initiate connections to each other's overlay
addresses. Registration still requires native administrator approval or an
authorized machine key under the [enrollment contract](headscale-enrollment.md).
Approval therefore admits a device to the shared network trust boundary.

This policy permits packets; host firewalls, service listening addresses and
application authentication still determine usable access. Reconcile managed host
firewall rules for the overlay interface without opening the same services to
the public internet. Preserve existing server policies on adoption; never widen
them through an ordinary setup rerun. Keep policy generation capable of later
restrictions and test those independently of the initial allow-all policy.

## Owned relays and public-relay context

Direct encrypted peer connections are preferred. DERP helps peers establish
connections and relays encrypted packets when direct connectivity fails, such
as behind restrictive NAT or blocked UDP. It does not normally carry every byte
of network traffic. Relays cannot decrypt the WireGuard payload but can observe
connection metadata; a relayed path also consumes relay bandwidth and adds a hop.

Headscale describes Tailscale's public DERP servers as free to use. The reviewed
upstream documentation publishes no numeric Headscale-specific monthly bandwidth
allowance or guaranteed speed. Tailscale documents throughput limits for fairness
and rate limiting/overload protection. Do not present this as guaranteed unlimited
capacity or use Tailscale's hosted-plan user/device allowances as a relay quota.
Public relays remain a third-party runtime dependency even without a subscription.

Initial Basaltwater hosting enables Headscale's embedded DERP and STUN,
retain `verify_clients: true`, and set `derp.urls: []` so the default public map
is not fetched or distributed. Merely enabling the embedded server still leaves
public relays in the upstream default map. Supply the owned server's public
address information and qualify HTTPS TCP 443 plus STUN UDP 3478 through the
firewall/proxy configuration. Embedded DERP has no throughput optimizations and
does not support Tailscale's HTTP captive-portal check endpoint.

One owned relay is an initial single point of failure for relay-assisted
connectivity. A second relay on independent infrastructure is a later availability
option. Budget hosting and bandwidth; self-hosting removes a required SaaS
subscription, not infrastructure costs. Already-established direct paths may
survive a relay outage; new connections and restrictive-network clients can fail.

Keep Headscale client-log upload disabled and qualify the client-side logging
opt-out from startup on each supported platform. Changing the login server alone
does not establish the absence of third-party requests. Separate installation,
app-store updates, public certificate/DNS infrastructure and optional client
checks from the network's runtime control/relay dependencies; document any
remaining platform-specific requests instead of claiming total network isolation.

## MagicDNS and Unbound

These services have complementary jobs:

- Public DNS resolves the control and onboarding hostnames before enrollment,
  such as `vpn.example.com` and `join.example.com`. They must remain resolvable
  without an overlay-only Unbound server, including after reboot or disconnection.
- Headscale distributes node addresses and DNS settings. MagicDNS lets each
  enrolled client's local resolver answer node names such as
  `laptop.mesh.example.net` using that map. Its `100.100.100.100` address is a
  device-local service, not a public resolver or a remote Headscale DNS listener.
- Unbound provides recursive, caching, validating DNS and local records. It can
  resolve internet names itself without requiring a public recursive DNS vendor;
  recursion still contacts the public DNS hierarchy. Private LAN service records
  can use a distinct zone, such as `lan.example.net`.
- A saved Headscale name such as `office` is a Basaltwater configuration alias,
  separate from the public control hostname, node DNS suffix and Unbound zones.

Use MagicDNS for enrolled device names, with Unbound handling all other queries
on new managed networks. Choose a distinct MagicDNS suffix that differs from
the control hostname and does not collide with LAN records. Replace public
recursive resolver examples from the upstream sample configuration with the
operator's own resolvers. Clients must accept the managed DNS settings.

Installing and adopting this network is explicit; existing external servers keep
their DNS settings. General resolution uses the owned Unbound resolver while
connected. Split DNS is later scope. Let the native client/platform DNS manager
apply and restore settings; do not overwrite unrelated OS resolver configuration.
This configures normal resolver use, not device-wide enforcement against
applications deliberately selecting another DNS service.

Install and manage Unbound on a separate enrolled Debian DNS node, using its
overlay address. A LAN resolver instead needs an explicitly configured reachable
route; subnet routing is outside the first deliverable.
Do not enroll the Headscale control host into its own network merely to expose
DNS; upstream does not support that topology. Permit UDP and TCP 53 only from
intended private clients, with matching Unbound access controls and firewall
rules. Do not expose unrestricted public recursion.

Ordinary LAN clients without Tailscale do not acquire MagicDNS records
automatically. Publishing those names into Unbound requires a separately owned
record synchronization design and is later scope. Do not forward Unbound's
default queries back into the same client's MagicDNS resolver and create a loop.

## Managed Unbound contract

- Use distribution-managed Unbound packages and security updates, systemd and a
  Basaltwater-owned configuration fragment. Enable native recursive resolution,
  caching and DNSSEC validation using maintained trust anchors. Do not configure
  a public recursive provider as an upstream or fallback. Recursion needs outbound
  UDP/TCP 53 to authoritative DNS servers; DNSSEC does not encrypt those queries.
- Preflight address/port ownership and existing resolver configuration before
  changes. Bind private serving addresses explicitly, with matching access
  controls and firewall rules. Keep query logging off by default and remote
  management disabled or local. Existing LAN records/settings require explicit
  adoption; do not replace an unmanaged resolver configuration silently.
- First enroll the DNS node using its preexisting bootstrap resolver. Observe
  its stable node identity and overlay addresses from the intended Headscale
  network, install/configure Unbound, and test queries from another enrolled
  client. Only then publish it as the network resolver. Keep the DNS node's own
  recursion and control-host bootstrap independent of a circular forwarding path.
  Until adoption succeeds, leave general client resolution at its bootstrap
  settings; never copy public recursive defaults from the Headscale sample.
- Validate with `unbound-checkconf` before atomic configuration replacement and
  activation. Failed validation or activation preserves/restores working managed
  configuration and reports the failing stage. Preserve unrelated fragments.
  Qualify boot ordering when the overlay address is not yet available.
- Save the network-to-DNS-node binding as private non-secret metadata; reject a
  resolver from another network. Node replacement or overlay-address changes
  require observed identity/address reconciliation and health checks before
  publishing updated DNS settings. A failed DNS rollout keeps the prior working
  resolver settings and never leaves an unverified address advertised.
  Guard DNS-node disable, leave and removal while it is advertised: withdraw or
  replace the resolver through explicit network reconfiguration first.
- One resolver is the initial availability tradeoff. Include configuration,
  local records and trust-anchor maintenance in recovery procedures; DNS cache
  data need not be backed up. A second resolver and existing-resolver adoption
  beyond the owned fragment are later scope.

MagicDNS adds no hosted-service fee or additional resolver server requirement.
Names apply to enrolled clients, do not provision HTTPS certificates, and extra
Headscale records currently support only A/AAAA. Test naming collisions and
applications that bypass system DNS; use Unbound for other private DNS records.

## Qualification

- Test peer connections in both directions, policy restrictions when configured,
  and unchanged public service exposure.
- Block access to Tailscale's public relays and map endpoints. Verify the client
  map contains only owned relays, then test direct and forced-relay paths through
  the owned server. Confirm unknown nodes cannot relay; measure representative
  transfer performance and record the single-relay outage behavior.
- Test MagicDNS node names, Unbound local records and public recursion on each
  advertised client platform. Verify pre-enrollment control-host lookup, resolver
  outage, disconnect/reconnect and reboot without a DNS bootstrap loop. Do not
  add a public recursive fallback silently when an owned resolver fails.

## References

- [Headscale access-policy defaults and grants](https://headscale.net/stable/ref/policy/)
- [Headscale embedded DERP, client verification and limitations](https://headscale.net/stable/ref/derp/)
- [Tailscale DERP behavior and encryption](https://tailscale.com/docs/reference/derp-servers)
- [Tailscale relay throughput limitations](https://tailscale.com/docs/reference/troubleshooting/poor-performance-tailnet)
- [Tailscale fair queuing and rate limiting](https://tailscale.com/blog/free-plan)
- [Headscale DNS configuration](https://headscale.net/stable/ref/dns/)
- [Use the configuration example matching the installed version](https://headscale.net/stable/ref/configuration/)
- [Tailscale device-local DNS resolver](https://tailscale.com/docs/reference/quad100)
- [Unbound resolver and local/forward zones](https://unbound.docs.nlnetlabs.nl/en/latest/manpages/unbound.conf.html)
- [Unbound installation, DNSSEC and private-network serving](https://unbound.docs.nlnetlabs.nl/en/latest/use-cases/home-resolver.html)
- [Headscale same-host and client-log limitations](https://headscale.net/stable/about/faq/)
