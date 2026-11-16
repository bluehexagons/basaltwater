# Headscale DNS hookup and recovery

For a managed network, publish from the controller using saved machine names:

```bash
basaltw headscale-manage dns-publish office --resolver dns-vm --peer app-vm
basaltw headscale-manage dns-inspect office
basaltw headscale-manage dns-withdraw office --resolver dns-vm --peer app-vm
```

The [three-VM walkthrough](headscale-vms.md) covers normal setup. The hookup
command observes native identities, prepares a dedicated restricted publication
key and verified host pins, and runs the transaction below. Its private key stays
on the control server. The controller's existing verified root SSH authority is
required to establish that connection on the chosen DNS and peer VMs. Users do
not need to assemble JSON or copy credentials. The independent peer needs `dig`.

Use `--dry-run` for an offline plan without connections, credentials, locks or
state changes. Publication remains an explicit operation; installing Unbound or
saving resolver candidates alone never changes network DNS.

## Advanced root-local interface

Run publication locally as root on the managed control server. It needs no
Tailscale enrollment. Install the same Basaltwater runtime under
`/opt/basaltwater` on the enrolled DNS node and an independent enrolled Linux
peer. Both need `dig`; the DNS node needs the owned Unbound installation.
Keep bootstrap control-host resolution independent of the overlay resolver.
Saved resolver candidates never trigger publication.

The low-level interface below is for separate administration, custom authority
and exact recovery requests. The normal hookup command supplies these inputs.

The registered root command is
`basaltw headscale-dns publish --input /root/dns-request.json`; `withdraw`,
`inspect`, and `reconcile` use the same command family. `inspect` takes no input.
`--dry-run` validates local input and SSH files without locks, network calls,
configuration changes or state writes. It does not claim remote readiness.
On a service VM without a launcher, run the same root-local command with
`python3 /opt/basaltwater/basaltwater.py headscale-dns ...` as root.

Keep request files mode 0600 and root owned, outside Git. Publication requests
have exactly `binding`, `dns_remote`, `peer`, `peer_remote`, `expected`, and
`old_remote`. Initial publication sets the last two to null. Bindings use the
version-1 DNSBinding schema: `version: 1`, HTTPS `network_url`, stable decimal
`node_id`, actual assigned overlay `addresses`, `advertised: false`, and
`publication_id: null`. Obtain identities through native inspection; names are
insufficient. The peer must differ from the DNS node and control server.

Each SSH object has exactly `host`, `user: "root"`, `key`, and `known_hosts`.
Use absolute paths to private root-owned regular key and known-host files.
This transport currently uses SSH port 22; it has no custom-port field.
Populate known-hosts through an independently verified host fingerprint. The
transport disables SSH config, agents, forwarding and interactive authentication,
requires strict host-key verification, runs one fixed helper and bounds input,
output and execution time. Endpoint discovery supplies no SSH authority. Never
include private endpoints, keys, inventory or raw results in tracked documents.

Publication keys are restricted to the root-observed network endpoint, native
node ID and resolver or peer role. A retained key cannot inspect or release
another network's guards after migration. The normal hookup narrows an exact
legacy Basaltwater authorization after root verifies the client again; other
authorization changes require explicit root review. The advanced interface must
use the same scoped forced-command authorization on each target. Unscoped keys
are refused by the helper.

On both DNS node and peer, the helper first verifies private retained client
state and effective systemd/running-process privacy. It then observes network,
node and assigned interface addresses, validates
the owned resolver manifest and file hashes, then queries identity TXT, native
recursion and valid/bogus DNSSEC using both private UDP and TCP. Bogus-domain
verification also queries with checking disabled to distinguish DNSSEC rejection
from an ordinary resolution failure. No caller can submit a health-success file.
The external `dnssec-failed.org` fixture must remain reachable through native
recursion; a failure blocks publication without choosing another resolver.
Any retained resolver operation or recovery snapshot also blocks observation
for publication and new reservations, even when health checks would succeed.
Explicitly reconcile that recovery state first. Releasing an existing guard
remains possible during an outage or recovery; evidence is preserved.
Publication, node checks and peer proof also require exact enrolled client
ownership, serialized against leave, migration and resume. Incomplete client
lifecycle operations must be reconciled before publication.

The control server independently checks native node IDs and addresses. It holds
the management DNS binding lease through every mutation, journals exact prior
and intended configurations, reserves the management guard, then reserves the
DNS-node guard before changing native DNS. Candidate validation precedes atomic
publication and native restart/health checks. Existing policy, keys and other DNS
settings are preserved; foreign nameservers, split DNS or incompatible override
settings require separate adoption. The result enables MagicDNS, sets global
nameservers to the verified node addresses and enables override-local-DNS.

After activation the peer must observe the exact native resolver list, enabled
managed DNS and MagicDNS, no fallback or foreign split resolver, and answer
identity queries through its device-local forwarder over UDP and TCP. The
helper waits briefly for this read-only native resolver-map observation to
converge after activation. It does not repeat activation, reservations or DNSSEC
probes; a timeout or invalid response retains the transaction and guards. The
reviewed JSON schema is from [Tailscale 1.104.7](https://github.com/tailscale/tailscale/blob/v1.104.7/cmd/tailscale/tsdnsjsonv0/status.go).
This Linux peer check does not establish every desktop/mobile OS DNS path.

For replacement, publish the new unpublished `binding` with `expected` equal to
the complete old advertised binding and `old_remote` its explicit SSH binding.
The new resolver is checked and reserved first. The old node stays guarded until
the new native configuration and independent peer are verified. There is one
resolver node at a time; dual-stack addresses belong to that same node.

Withdrawal input has exactly `expected`, `dns_remote`, `peer`, `peer_remote`.
It removes the managed global resolver and override, retaining MagicDNS and
unrelated settings. It verifies control health and peer withdrawal before
unreserving the node and clearing management state. Withdrawal restores the
client's native bootstrap DNS behavior; it never installs a public fallback.

## Interrupted operations

Do not delete guards, journal or stable lock files to retry. Any unknown result
remains blocked, including lost reserve/release acknowledgements. There are no
automatic mutation retries or replacement publication IDs.

For controller hookup commands, `headscale-manage dns-inspect NAME` also shows
the retained controller `attempt`. Save that exact object in a private mode-0600
JSON file. If the native `journal` is non-null, complete the native reconciliation
below first. Then finish the controller attempt:

```bash
basaltw headscale-manage dns-reconcile office --expected-attempt /private/reviewed-attempt.json
```

This command verifies the exact retained request, unchanged SSH pins, actual node
and publication identities, native DNS policy, resolver health and the independent
peer. It completes only the controller record; it never repeats activation or
reservation. Stale reviews, pending native operations and unknown results retain
the blocker. Use `--dry-run` to validate the local review without connections or
state changes. Inspect again after completion before a fresh operation.

1. Run `inspect` locally over authorized administration and retain its output
   privately. Preserve `dns-transaction.json`, `dns-publication.json` and
   `operation.json` in the managed control private directory, plus the node's
   private Unbound binding. Inspect actual native config and node guards.
2. Restore SSH reachability and resolver health if needed. Choose the journal's
   `before` or `after` side only when the current full native configuration
   equals that side. A partial/different config requires restoring the matching
   reviewed config through native validation/activation first; never bypass the
   guards. Reconciliation does not retry activation.
3. Reconcile using exactly `expected_journal` from inspection, `keep` set to
   `before` or `after`, `peer`, `peer_remote`, `new_remote`, and `old_remote`
   (null for absent nodes). The helper checks exact CAS state, verifies retained
   resolver/control/peer state and releases only the unused guard. Already
   acknowledged releases are observed without replay. Stale inspection fails.
   If guard deletion was interrupted, the exact journal-derived unpublished
   reservation can be reconciled after repeating those checks; unrelated
   reservations remain blocked.
4. After successful reconciliation, inspect again before requesting a fresh
   publication. Node disable/leave and native expiry/removal remain blocked
   while either a reservation or effective native nameserver references them.

Offline tests cover sequencing, invalid identities, peer failures, DNSSEC,
uncertainty, recovery, withdrawal, replacement and dry run. The
[qualification record](../testing/headscale.md) distinguishes verified Debian
publication, reconciliation, DNSSEC, OS resolution and outage behavior from
remaining platform and recovery acceptance work.
