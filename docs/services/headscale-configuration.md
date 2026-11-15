# Headscale setup configuration

Headscale hosting, a Tailscale client and a managed Unbound resolver have
independent setup selections. Hosting uses `--headscale`; joining uses either
`--tailscale NAME` or `--tailscale-login-server https://vpn.example.com`.
`--unbound` selects a resolver on a separate enrolled Debian node.

For routine setup, add `--headscale-name NAME` to hosting to save the managed
server and its verified SSH management connection. Join managed machines with
`--tailscale NAME --tailscale-tag tag:service`; repeat `--tailscale-tag` when
approving more than one service tag. Add `--unbound` to that same command for
the DNS VM. Setup prepares the client, enrolls it privately, then installs the
resolver. See the [short VM walkthrough](headscale-vms.md).

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

The controller resolves `--tailscale NAME` against the private workspace registry
before applying configuration. Client state stores the registry's stable ID
and resolved HTTPS endpoint. A renamed record continues to select the same
network; an explicitly supplied different record is rejected. Removing the
record or changing its endpoint also fails during ordinary setup. Use the
explicit [client migration and saved-selection reconciliation](headscale-clients.md)
workflow after independently completing the new enrollment. Registering an external endpoint only provides
discovery and does not grant administrative or enrollment authority.

Saved configuration retains the client selection and approved tags. Passing
`--no-tailscale` disables managed client setup while retaining its device identity
and saved network binding; this does not leave the network or delete state.
`--tailscale-login-server` is validated as a plain HTTPS origin and follows the
same migration guard. DNS resolver candidates remain local metadata and are
never published merely because they were supplied on the command line.

The hosting contract reserves local TCP ports 8080, 9090 and 50443, HTTPS TCP
443 and STUN UDP 3478. Headscale requires direct ingress; unrelated Cloudflare
tunnels can coexist. The control and onboarding hostnames cannot be assigned to
another managed application. Native clients preserve the platform DNS manager;
network selection does not authorize subnet routes or an exit node.

Automatic enrollment requires the separate managed SSH binding established by
hosting setup or an explicit management bind. A name or direct URL alone grants
no enrollment authority. Without `--tailscale-tag`, setup prepares/resumes the
client for separately administered enrollment. An uncertain enrollment stops
setup and preserves its private recovery journal.

See [managed setup](headscale-setup.md) for installation and composition,
[recovery](headscale-recovery.md) for maintenance, and
[saved references](headscale-references.md) for endpoint discovery commands.
