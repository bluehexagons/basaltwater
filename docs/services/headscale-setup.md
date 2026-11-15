# Native network setup order

Headscale hosting, a native Tailscale client, and managed Unbound are independent
optional capabilities. Supported Debian profiles compose them before exporting
the Web Portal's installed features. Omitted capabilities add no setup steps.

Follow the [three-VM walkthrough](headscale-vms.md) for complete Proxmox commands,
private SSH authority, tagged enrollment and verified DNS publication. Setup
handles one VM at a time; startup order and parallel `fan` commands do not form
a dependency-aware fleet rollout.

The first step checks the final configuration, Debian bookworm/trixie, native
architecture, root, active systemd and effective network administration capability.
Client and resolver hosts also need a usable kernel TUN device. This admission
precedes the profile's package changes. A dry run validates the plan without
reading native identities or modifying services, files or enrollment state.

Hosting installs Nginx and Certbot as prerequisites, then configures the native
control server. Selecting hosting does not enroll that host as a client. A client
is prepared before Unbound; a new client must finish explicit enrollment before
Unbound can observe its network identity. Patch the saved client with `--unbound`
after enrollment, or rerun setup with the retained network selection, to install
the resolver. Disabling both stops the unadvertised resolver before its client.
The DNS publication guard refuses teardown while the node remains advertised.
Hosting also installs the distribution's `python3-grpcio` and `python3-protobuf`
runtime, verifies the managed service and exercises its private socket schema
before the portal exports administrative availability.

The control hostname needs direct HTTPS. Cloudflare for other services on the
same host requires `--cloudflare-allow-direct`; a tunnel policy that closes shared
ports conflicts with this requirement and is rejected before mutation. Likewise,
source restrictions on shared web ports cannot protect a public Headscale
endpoint on those ports; use a separate host or a compatible ingress policy.
Onboarding visibility is a separate Nginx site policy.
