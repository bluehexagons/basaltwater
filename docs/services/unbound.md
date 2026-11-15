# Managed Unbound

Unbound runs on a separate enrolled Debian node. Bootstrap the node's client
using its existing resolver, then select the same network with
`--tailscale NAME --unbound`. Headscale hosting and Unbound cannot share a host
in that network. This installation does not advertise the resolver to clients;
network DNS adoption requires a separate identity and peer-health check.

Setup observes the client's native node ID, control endpoint and assigned overlay
addresses. Unbound binds those addresses explicitly on UDP/TCP 53. Its access
controls allow overlay clients, and managed UFW rules are restricted to
`tailscale0`. The host needs an active UFW firewall; IPv6 serving also requires
UFW IPv6 support. Existing resolver packages, custom configuration, conflicting
listeners or unmanaged unit overrides require explicit reconciliation.

The distribution package and security updates maintain Unbound and its DNSSEC
root anchor. The managed fragment uses native recursion, caching and validation
without a recursive provider or fallback. Queries to authoritative DNS servers
need outbound UDP/TCP 53. Query/reply logging and remote management are disabled.
The package's resolvconf hook is disabled so the DNS node keeps its bootstrap
resolver. Native Tailscale/platform DNS handling remains responsible for client
DNS settings; setup does not write `/etc/resolv.conf`.

Boot ordering follows networking and `tailscaled`; `ip-freebind` permits the
configured address to appear after Unbound starts. Serving still requires the
overlay interface and its source rules. Qualify reconnect, reboot and resolver
outage on the deployed system before relying on it.

Configuration is validated with `unbound-checkconf` before publication. Activation
checks UDP/TCP identity responses and authenticated root DNSKEY resolution.
Failed activation restores prior managed files and service state. An uncertain
stop leaves the candidate, private recovery snapshot and blocking operation
under `/var/lib/basaltwater/unbound`; follow the
[transaction recovery guide](../operations/transaction-recovery.md) before retrying.
Package installation and root-anchor maintenance are outside file rollback.
Fresh setup refreshes APT metadata before installing with bounded commands.
Any failed fresh package attempt or subsequent activation keeps a blocking marker
and private snapshot even if file rollback succeeds. Follow the exact reviewed
[`unbound-recover-fresh` procedure](headscale-recovery.md#failed-fresh-unbound-package-setup)
to confirm an independently restored absent installation before a new attempt.
Recovery never replays installation, adopts an existing package, or removes data.

`--no-unbound` stops and disables the service while retaining configuration,
local records and node binding. An advertised or reserved resolver cannot be
disabled or leave through the managed client lifecycle. Withdraw or replace it
first. A changed node identity or address requires explicit reconciliation and
new peer checks before publication.

Local records use a bounded version-1 document with `records` entries containing
`zone`, `name`, `type`, `value` and `ttl`. Supported types are A, AAAA and CNAME;
TTL is 30–86400 seconds. Supply the intended MagicDNS suffix with
`--headscale-base-domain` to check collisions. Managed zones must differ from
control/onboarding hostnames, MagicDNS and the reserved resolver-health zone.
Keep this inventory private. LAN-wide MagicDNS synchronization is outside scope.

Back up the private `records.json`, `binding.json`, `managed.json`, managed
Unbound fragment, default environment and systemd drop-in together. Cache data
need not be retained. Maintain the distribution's trust-anchor update mechanism
on restore; do not replace a current root key with an old backup. Native client
identity is separate: preserve its private state or explicitly re-enroll the
replacement, withdraw the old resolver and verify/rebind the new identity before
publishing it. Restoring these files alone does not prove DNS recovery.
