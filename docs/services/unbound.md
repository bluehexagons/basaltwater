# Managed Unbound

Unbound runs on a separate enrolled Debian node. Select
`--tailscale NAME --tailscale-tag tag:dns --unbound` in its setup command.
Basaltwater prepares the native client using bootstrap DNS, completes private
enrollment, then installs Unbound in the same operation. Headscale hosting and
Unbound cannot share a host in that network. This installation does not advertise
the resolver to clients; network DNS adoption uses
`headscale-manage dns-publish NAME --resolver HOST --peer HOST`
for independent identity and health checks. The
[three-VM walkthrough](headscale-vms.md) gives the routine setup and hookup commands.

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
The identity TXT answer contains the plain binding digest; configuration quoting
must not add quote or backslash characters to its DNS payload.
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
Keep this inventory private. The generated fragment also contains the inventory:
setup, rollback and restore restrict it to root and the `unbound` group (mode
`0640` or stricter). Snapshot validation refuses world-readable fragments.
LAN-wide MagicDNS synchronization is outside scope.

Use the root-local maintenance commands to replace the entire inventory. Direct
editing of managed `records.json` invalidates the manifest hashes. On the DNS node:

```bash
sudo basaltw headscale-maintenance unbound-records-inspect --binding /private/expected-binding.json
sudo basaltw headscale-maintenance unbound-records-replace --review /private/records-review.json --dry-run
sudo basaltw headscale-maintenance unbound-records-replace --review /private/records-review.json
```

If the VM has no `basaltw` launcher, replace it with
`python3 /opt/basaltwater/basaltwater.py` and run as root.

Inputs must be absolute paths to private owned regular JSON files (mode `0600`),
with safe parent directories. The binding file is the exact reviewed version-1
DNSBinding: `version`, `network_url`, `node_id`, `addresses`, `advertised` and
`publication_id`, including the current publication reservation if present.
Inspect emits only `records_sha256`, `record_count`, `enabled`, `advertised` and
`readiness_claim: false`, plus action/confirmation flags. It reads owned state
without creating locks or files; its result is a snapshot, not a readiness test.
Review the existing private inventory and binding locally before replacement.

The replacement review has exactly these fields (all names below are fictional):

```json
{
  "version": 1,
  "binding": {
    "version": 1, "network_url": "https://vpn.example.com", "node_id": "7",
    "addresses": ["100.64.0.2"], "advertised": false, "publication_id": null
  },
  "records_sha256": "COPY_THE_64_LOWERCASE_HEX_DIGEST_FROM_INSPECT",
  "magic_dns_suffix": "mesh.example.net",
  "onboarding_domain": "join.example.com",
  "inventory": {
    "version": 1,
    "records": [
      {"zone": "lan.example.net", "name": "files.lan.example.net", "type": "A", "value": "192.0.2.8", "ttl": 300}
    ]
  }
}
```

Supply the known onboarding hostname, or `null` only when none exists. This is
an additional reviewed exclusion, not a native observation. The expected
MagicDNS suffix is checked against the running native client's effective
tailnet status; a missing/disabled suffix or mismatch refuses the update.
The retained native identity, enrollment ownership and running daemon privacy
are checked independently of the review. Control and health exclusions remain
mandatory. `inventory.records: []` removes all local records; every replacement
must include all records to retain. The digest is SHA-256 of the exact prior
`records.json` bytes, not a normalized JSON digest.

Replacement requires an existing managed, active, persistently enabled resolver.
It cannot install, adopt, enable a disabled resolver, enroll, rebind, or change
network, node, addresses or publication identity. Exact binding and inventory
comparison occurs under the shared publication/lifecycle lease; stale reviews
fail before transaction writes. The same publication guard may remain in place.
Activation uses the existing candidate validation, health checks, rollback and
retained recovery protocol described above. Dry-run validates private input
syntax only: no native/network calls, service/package activity, locks or state
writes, and no readiness claim. Errors emit a generic reconciliation message;
keep corrupt/unsafe files and unfinished recovery state for investigation.

Back up the private `records.json`, `binding.json`, `managed.json`, managed
Unbound fragment, default environment and systemd drop-in together. Cache data
need not be retained. Maintain the distribution's trust-anchor update mechanism
on restore; do not replace a current root key with an old backup. Native client
identity is separate: preserve its private state or explicitly re-enroll the
replacement, withdraw the old resolver and verify/rebind the new identity before
publishing it. Restoring these files alone does not prove DNS recovery.
