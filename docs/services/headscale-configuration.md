# Headscale setup configuration

Headscale hosting, a Tailscale client and a managed Unbound resolver have
independent setup selections. Hosting uses `--headscale`; joining uses either
`--tailscale NAME` or `--tailscale-login-server https://vpn.example.com`.
`--unbound` selects a resolver on a separate enrolled Debian node.

Hosting settings are `--headscale-domain`, `--headscale-base-domain` for the
MagicDNS suffix, `--headscale-onboarding-domain`, and the relay address options
`--headscale-public-ipv4` and `--headscale-public-ipv6`. The relay addresses must
be reachable by the intended clients; an Internet-facing network needs public
addresses. Private addresses can serve an explicitly private deployment. Keep
the control and onboarding names outside the MagicDNS suffix so public bootstrap
resolution remains independent of the overlay.

`--no-headscale`, `--no-tailscale` and `--no-unbound` preserve their explicit
disabled state through saved configuration and remote setup. Disabling client
management preserves its native identity and endpoint binding; leaving a network
and purging data are separate operations. Changing a saved endpoint never
silently moves an enrolled client. Registry renames retain the stable selection.

`--headscale-resolver IP` records an overlay resolver candidate on the controller.
It does not publish DNS settings and is omitted from target transport. Resolver
publication needs observed network/node identity and DNS health checks first.
Never use an overlay-only resolver to bootstrap its own enrollment.

The hosting contract reserves local TCP ports 8080, 9090 and 50443, HTTPS TCP
443 and STUN UDP 3478. Headscale requires direct ingress; unrelated Cloudflare
tunnels can coexist. The control and onboarding hostnames cannot be assigned to
another managed application. Native clients preserve the platform DNS manager;
network selection does not authorize subnet routes or an exit node.

This configuration milestone does not yet install services. See the
[implementation handoff](../plans/headscale-implementation.md) for the remaining
runtime milestones and [saved references](headscale-references.md) for endpoint
discovery commands.
