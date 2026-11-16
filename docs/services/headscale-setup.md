# Automated native network setup

Headscale hosting, a native Tailscale client, and managed Unbound are independent
optional capabilities. Supported Debian profiles compose them before exporting
the Web Portal's installed features. Omitted capabilities add no setup steps.

Follow the [three-VM walkthrough](headscale-vms.md) for the routine setup commands
and DNS hookup. Setup handles one VM at a time; startup order and parallel `fan`
commands do not form a dependency-aware fleet rollout.

The first step checks the final configuration, Debian bookworm/trixie, native
architecture, root, active systemd and effective network administration capability.
Client and resolver hosts also need a usable kernel TUN device. This admission
precedes the profile's package changes. A dry run validates the plan without
reading native identities or modifying services, files or enrollment state.

Hosting installs Nginx and Certbot as prerequisites, then configures the native
control server. `--headscale --headscale-name NAME` also saves the managed server
and its verified SSH management binding. Selecting hosting does not enroll that
host as a client.

`--tailscale NAME --tailscale-tag tag:service` prepares and explicitly enrolls a
managed machine. Add `--unbound` to the same setup command for a DNS node: the
controller prepares the native client, completes private enrollment, then applies
the final profile with Unbound. Intermediate preparation does not save a partial
profile or report setup complete. Existing enrolled clients are verified on rerun.
Without `--tailscale-tag`, client setup retains its preparation/resume behavior
for external or separately administered enrollment.

Change routine configuration by rerunning setup with the desired options.
Disabling both stops the unadvertised resolver before its client.
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
